from __future__ import annotations

"""Compatibility migration from four-stage bundle runs to the Study model."""

from pathlib import Path
from typing import Any

from . import __version__
from .storage import read_json, sha256_file, write_json_atomic
from .workflow_domain import (
    AIReviewStatus,
    ArtifactRole,
    ArtifactStatus,
    AuthorApprovalStatus,
    CompletionRecord,
    EntryMode,
    EvidenceChain,
    EvidenceChainLevel,
    ExecutionStatus,
    ExecutorType,
    GateStatus,
    GateType,
    Hypothesis,
    HypothesisRole,
    HypothesisVerdict,
    HypothesisVerdictStatus,
    Phase,
    ReadinessAssessment,
    ResearchContractVersion,
    ScopeContractVersion,
    StepDefinition,
    StudyLifecycle,
    StudyVerdictStatus,
    SystemReadiness,
    WorkflowRepository,
    aggregate_study_verdict,
    stable_id,
)


def _optional_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.is_file() else {}


def _legacy_status(value: str) -> HypothesisVerdictStatus:
    return {
        "supported": HypothesisVerdictStatus.SUPPORTED,
        "refuted": HypothesisVerdictStatus.REFUTED,
        "not_supported": HypothesisVerdictStatus.REFUTED,
        "inconclusive": HypothesisVerdictStatus.INCONCLUSIVE,
        "mixed": HypothesisVerdictStatus.INCONCLUSIVE,
        "unverifiable": HypothesisVerdictStatus.UNVERIFIABLE,
    }.get(value, HypothesisVerdictStatus.UNVERIFIABLE)


def _artifact_role(relative: str) -> ArtifactRole:
    if "scope_contract" in relative or "protocol_lock" in relative:
        return ArtifactRole.PROTOCOL
    if "idea_verdict" in relative or relative.endswith("claims.json"):
        return ArtifactRole.CLAIM
    if relative.endswith("manuscript.md"):
        return ArtifactRole.MANUSCRIPT
    if relative.endswith("audit.json"):
        return ArtifactRole.AUDIT
    if "output" in relative:
        return ArtifactRole.OUTPUT
    return ArtifactRole.OTHER


def migrate_bundle_run(
    run_dir: str | Path,
    *,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Register one immutable legacy/new bundle run as a v2 Project and Study.

    The migration is idempotent.  It never edits the source-project directory
    and never removes or rewrites the legacy completion certificate.
    """

    run = Path(run_dir).resolve()
    manifest = read_json(run / "bundle_manifest.json")
    scope = _optional_json(run / "stage_1_discovery" / "scope_contract.json")
    protocol = _optional_json(run / "stage_2_protocol" / "protocol_lock.json")
    old_verdict = _optional_json(
        run / "stage_3_experimentation" / "idea_verdict.json"
    )
    audit = _optional_json(run / "stage_4_synthesis" / "audit.json")
    old_certificate = _optional_json(run / "completion_certificate.json")

    source_root = str(manifest.get("source_root", ""))
    project_id = stable_id("project", source_root or run.name)
    study_id = stable_id(
        "study",
        project_id,
        str(manifest.get("selected_track_id", run.name)),
        run.name,
    )
    repository = WorkflowRepository(repository_root)
    project = repository.create_project(
        Path(source_root).name if source_root else run.name,
        source_root=source_root or None,
        project_id=project_id,
    )
    study_path = repository.root / "studies" / study_id / "study.json"
    if study_path.is_file():
        return repository.snapshot(study_id)
    support_type = (
        "computational"
        if bool(protocol.get("protocol_bound_to_output"))
        else "imported_materials"
    )
    study = repository.create_study(
        project.project_id,
        str(scope.get("title") or manifest.get("selected_track_id") or run.name),
        entry_mode=EntryMode.PROJECT_TO_PAPER,
        research_type=support_type,
        study_id=study_id,
    )
    study = repository.save_study(
        study.model_copy(
            update={
                "settings": {
                    **study.settings,
                    "workflow_origin": "legacy_bundle_import",
                    "live_stage_execution": False,
                    "source_run": run.name,
                }
            }
        ),
        "legacy_bundle_import_registered",
    )
    gate_scope = repository.create_gate(
        study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study_id}:scope",
        subject_version=1,
        status=GateStatus.AWAITING_USER,
    )
    gate_scope = repository.decide_gate(
        study_id,
        gate_scope.gate_id,
        approve=True,
        decided_by="legacy_bundle_import",
        reason=(
            "Compatibility import preserves the historical frozen Scope; "
            "this is not a new owner approval or live Stage-1 execution."
        ),
    )
    gate_research = repository.create_gate(
        study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study_id}:research",
        subject_version=1,
        status=GateStatus.AWAITING_USER,
    )
    repository.decide_gate(
        study_id,
        gate_research.gate_id,
        approve=True,
        decided_by="legacy_bundle_import",
        reason=(
            "Compatibility import preserves the historical protocol lock; "
            "this is not a new owner approval or live Stage-2 execution."
        ),
    )

    scope_contract = ScopeContractVersion(
        study_id=study_id,
        version=1,
        status=ArtifactStatus.FROZEN,
        direction=str(scope.get("title") or manifest.get("selected_track_id", "")),
        research_question=str(
            scope.get("research_question")
            or "What claim can be supported by the selected project evidence?"
        ),
        scope_in=[str(item) for item in scope.get("scope_in", [])] or ["selected project evidence"],
        scope_out=[str(item) for item in scope.get("scope_out", [])] or ["unbound external claims"],
        candidate_contribution=str(scope.get("novelty_candidate") or "Project-derived candidate contribution"),
        project_resource_ids=[
            str(item.get("path"))
            for item in manifest.get("resources", [])
            if isinstance(item, dict) and item.get("path")
        ],
        frozen_at=str(old_certificate.get("completed_at") or study.created_at),
    )
    repository.save_scope_contract(scope_contract)

    hypothesis = Hypothesis(
        hypothesis_id="hypothesis-primary",
        statement=str(
            scope.get("hypothesis_under_test")
            or old_verdict.get("conclusion")
            or "The selected project claim is supported by its bound evidence."
        ),
        role=HypothesisRole.PRIMARY,
        decision_rule={"source": "legacy_import", "verdict": old_verdict.get("status")},
    )
    research_contract = ResearchContractVersion(
        study_id=study_id,
        version=1,
        scope_version=1,
        status=ArtifactStatus.FROZEN,
        hypotheses=[hypothesis],
        data_boundary={"source_mode": manifest.get("source_mode", "declared_chain")},
        metrics=[{"name": "legacy_project_evidence", "direction": "contract_defined"}],
        baseline={"mode": "imported"},
        treatment={"mode": "imported"},
        tasks=[],
        seeds=[],
        runtime_binding={
            "source_run": run.name,
            "protocol_sha256": protocol.get("protocol_sha256"),
            "output_sha256": protocol.get("output_sha256"),
        },
        evaluator_policy={
            "deterministic_rules_precede_nli": True,
            "nli_authority": "risk_alert_only",
        },
        frozen_at=str(old_certificate.get("completed_at") or study.created_at),
    )
    repository.save_research_contract(research_contract)

    repository.create_gate(
        study_id,
        GateType.FINAL_SUBMISSION,
        "manuscript",
        f"{study_id}:manuscript",
        status=GateStatus.AWAITING_USER,
    )

    step_specs = [
        ("project_scan", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_SERVICE),
        ("evidence_chain_detection", Phase.DISCOVERY, ExecutorType.MODEL),
        ("scope_review", Phase.DISCOVERY, ExecutorType.PROJECT_OWNER),
        ("scope_freeze", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_SERVICE),
        ("protocol_drafting", Phase.PROTOCOL, ExecutorType.CODEX),
        ("contract_review", Phase.PROTOCOL, ExecutorType.PROJECT_OWNER),
        ("contract_freeze", Phase.PROTOCOL, ExecutorType.DETERMINISTIC_SERVICE),
        ("evidence_import", Phase.EXPERIMENT, ExecutorType.SANDBOX_RUNNER),
        ("deterministic_validation", Phase.EXPERIMENT, ExecutorType.DETERMINISTIC_EVALUATOR),
        ("result_review", Phase.EXPERIMENT, ExecutorType.AI_SCIENTIFIC_REVIEW_PANEL),
        ("claim_mapping", Phase.PAPER, ExecutorType.DETERMINISTIC_SERVICE),
        ("manuscript_writing", Phase.PAPER, ExecutorType.CODEX),
        ("manuscript_audit", Phase.PAPER, ExecutorType.DETERMINISTIC_EVALUATOR),
        ("completion_record", Phase.PAPER, ExecutorType.DETERMINISTIC_SERVICE),
    ]
    previous: str | None = None
    for step_type, phase, executor in step_specs:
        repository.save_step_definition(
            StepDefinition(
                step_type=step_type,
                phase=phase,
                executor_type=executor,
                scientific_failure_enters_diagnosis=phase is Phase.EXPERIMENT,
            )
        )
        step = repository.add_step(
            study_id,
            step_type,
            phase,
            executor,
            depends_on=[previous] if previous else [],
        )
        step = repository.update_step_parameters(
            study_id,
            step.step_instance_id,
            {
                "execution_provenance": "legacy_bundle_import",
                "live_execution": False,
                "source_run": run.name,
            },
        )
        repository.update_step(study_id, step.step_instance_id, ExecutionStatus.RUNNING)
        repository.update_step(study_id, step.step_instance_id, ExecutionStatus.SUCCEEDED)
        previous = step.step_instance_id

    artifact_paths = [
        "bundle_manifest.json",
        "stage_1_discovery/scope_contract.json",
        "stage_2_protocol/protocol_lock.json",
        "stage_3_experimentation/idea_verdict.json",
        "stage_4_synthesis/claims.json",
        "stage_4_synthesis/manuscript.md",
        "stage_4_synthesis/audit.json",
    ]
    artifacts = []
    for relative in artifact_paths:
        path = run / relative
        if not path.is_file():
            continue
        artifacts.append(
            repository.register_artifact(
                study_id,
                relative,
                sha256_file(path),
                kind=Path(relative).stem.replace("-", "_"),
                role=_artifact_role(relative),
            )
        )
    for left, right in zip(artifacts, artifacts[1:]):
        repository.add_dependency(study_id, left.artifact_id, right.artifact_id)

    by_path = {item.path: item for item in artifacts}
    protocol_artifact = by_path.get("stage_2_protocol/protocol_lock.json")
    output_artifact = by_path.get("stage_3_experimentation/idea_verdict.json")
    claim_artifact = by_path.get("stage_4_synthesis/claims.json")
    level = (
        EvidenceChainLevel.VERIFIED
        if bool(protocol.get("protocol_bound_to_output"))
        else EvidenceChainLevel.INFERRED
    )
    chain = EvidenceChain(
        chain_id=stable_id("chain", study_id, level.value),
        study_id=study_id,
        level=level,
        protocol_artifact_id=protocol_artifact.artifact_id if protocol_artifact else artifacts[0].artifact_id,
        output_artifact_ids=[output_artifact.artifact_id] if output_artifact else [],
        claim_artifact_ids=[claim_artifact.artifact_id] if claim_artifact else [],
        implementation_version=str(protocol.get("protocol_sha256") or "") or None,
        inference_rationale=(
            None
            if level is EvidenceChainLevel.VERIFIED
            else "Legacy project files are semantically related but lack exact protocol-output binding."
        ),
        verified_checks={
            "protocol_output_binding": bool(protocol.get("protocol_bound_to_output"))
        },
    )
    repository.save_evidence_chain(chain)
    status = _legacy_status(str(old_verdict.get("status", "unverifiable")))
    hypothesis_verdict = HypothesisVerdict(
        verdict_id=stable_id("hverdict", study_id, hypothesis.hypothesis_id, run.name),
        study_id=study_id,
        hypothesis_id=hypothesis.hypothesis_id,
        status=(
            status
            if chain.verdict_eligible()
            else HypothesisVerdictStatus.UNVERIFIABLE
        ),
        evidence_chain_ids=[chain.chain_id],
        eligible_evidence=chain.verdict_eligible(),
        rationale=str(
            old_verdict.get("conclusion")
            or "Imported legacy verdict was bounded by its evidence-chain eligibility."
        ),
    )
    repository.save_hypothesis_verdict(hypothesis_verdict)
    study_verdict = aggregate_study_verdict(
        study_id, [hypothesis], [hypothesis_verdict]
    )
    repository.save_study_verdict(study_verdict)

    system_status = (
        SystemReadiness.CONDITIONS_MET
        if bool(audit.get("publication_ready"))
        else SystemReadiness.NOT_READY
    )
    readiness = repository.save_readiness(
        ReadinessAssessment(
            study_id=study_id,
            system_publication_readiness=system_status,
            ai_scientific_review=AIReviewStatus.PENDING,
            author_publication_approval=AuthorApprovalStatus.PENDING,
            system_checks={
                str(key): bool(value)
                for key, value in audit.get("checks", {}).items()
            },
            blockers=[str(item) for item in audit.get("publication_blockers", [])],
        )
    )
    hashes = {
        relative: sha256_file(run / relative)
        for relative in artifact_paths
        if (run / relative).is_file()
    }
    completion = CompletionRecord(
        completion_record_id=stable_id("completion", study_id, run.name),
        project_id=project_id,
        study_id=study_id,
        scope_version=1,
        research_contract_version=1,
        run_ids=[run.name],
        issuer_version=__version__,
        readiness=readiness,
        artifact_hashes=hashes,
        verification_command=f"research-forge workflow verify-completion \"{run / 'completion_record.json'}\"",
        legacy_certificate_path=(
            "completion_certificate.json"
            if (run / "completion_certificate.json").is_file()
            else None
        ),
    )
    completion = repository.save_completion_record(completion)
    write_json_atomic(run / "completion_record.json", completion)
    write_json_atomic(
        run / "workflow_v2.json",
        {
            "schema_version": 2,
            "repository_root": str(repository.root),
            "project_id": project_id,
            "study_id": study_id,
            "legacy_gate_id": gate_scope.gate_id,
        },
    )
    latest = repository.load_study(study_id)
    repository.save_study(
        latest.model_copy(
            update={
                "phase": Phase.PAPER,
                "lifecycle": StudyLifecycle.ACTIVE,
                "execution_status": (
                    ExecutionStatus.WAITING_FOR_USER
                    if bool(audit.get("publication_ready"))
                    else ExecutionStatus.BLOCKED
                ),
                "current_step_ids": [],
            }
        ),
        "legacy_bundle_import_stopped_before_live_execution",
    )
    return repository.snapshot(study_id)


__all__ = ["migrate_bundle_run"]
