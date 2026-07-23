from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_forge.storage import sha256_file, write_json_atomic
from research_forge.workflow_domain import (
    AIReviewStatus,
    ArtifactRole,
    ArtifactStatus,
    AuthorApprovalStatus,
    BaselineVerificationContract,
    CompletionRecord,
    EntryMode,
    EvidenceChain,
    EvidenceChainLevel,
    ExecutionStatus,
    ExecutorType,
    GateType,
    Hypothesis,
    HypothesisRole,
    HypothesisVerdict,
    HypothesisVerdictStatus,
    NetworkAuditEvent,
    NetworkPolicy,
    Phase,
    ReadinessAssessment,
    ScopeContractVersion,
    StudyVerdictStatus,
    SystemReadiness,
    WorkflowRepository,
    aggregate_study_verdict,
    is_secret_path,
    seal_completion_record,
    stable_id,
    verify_completion_record,
)
from research_forge.workflow_migration import migrate_bundle_run


def _repository(tmp_path: Path) -> tuple[WorkflowRepository, str]:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Shared project", source_root="C:/research/shared")
    return repository, project.project_id


def test_project_can_own_independent_studies(tmp_path: Path) -> None:
    repository, project_id = _repository(tmp_path)
    first = repository.create_study(
        project_id, "Study A", entry_mode=EntryMode.PROJECT_TO_PAPER
    )
    second = repository.create_study(
        project_id, "Study B", entry_mode=EntryMode.PROJECT_TO_PAPER
    )

    assert first.study_id != second.study_id
    assert {item.study_id for item in repository.list_studies(project_id)} == {
        first.study_id,
        second.study_id,
    }


def test_frozen_scope_requires_gate_and_then_becomes_immutable(tmp_path: Path) -> None:
    repository, project_id = _repository(tmp_path)
    study = repository.create_study(
        project_id, "Versioned scope", entry_mode=EntryMode.PROJECT_TO_PAPER
    )
    draft = ScopeContractVersion(
        study_id=study.study_id,
        version=1,
        direction="Evidence binding",
        research_question="Does explicit binding improve auditability?",
        scope_in=["machine-readable runs"],
        scope_out=["wet lab execution"],
        candidate_contribution="A bounded evidence model",
    )
    repository.save_scope_contract(draft)
    frozen = draft.model_copy(update={"status": ArtifactStatus.FROZEN})
    with pytest.raises(ValueError, match="owner approval"):
        repository.save_scope_contract(frozen)
    gate = repository.create_gate(
        study.study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study.study_id}:scope-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id, gate.gate_id, approve=True, decided_by="owner"
    )
    repository.save_scope_contract(frozen)
    with pytest.raises(ValueError, match="new version"):
        repository.save_scope_contract(
            frozen.model_copy(update={"research_question": "Changed after freeze"})
        )


def test_persisted_dag_allows_parallel_steps_and_study_pause(tmp_path: Path) -> None:
    repository, project_id = _repository(tmp_path)
    study = repository.create_study(
        project_id, "Parallel study", entry_mode=EntryMode.IDEA_TO_PAPER
    )
    parent = repository.add_step(
        study.study_id,
        "literature_query_plan",
        Phase.DISCOVERY,
        ExecutorType.CODEX,
    )
    repository.update_step(
        study.study_id, parent.step_instance_id, ExecutionStatus.RUNNING
    )
    repository.update_step(
        study.study_id, parent.step_instance_id, ExecutionStatus.SUCCEEDED
    )
    crossref = repository.add_step(
        study.study_id,
        "crossref_retrieval",
        Phase.DISCOVERY,
        ExecutorType.RETRIEVAL_SERVICE,
        depends_on=[parent.step_instance_id],
        task_group="literature_providers",
    )
    semantic = repository.add_step(
        study.study_id,
        "semantic_scholar_retrieval",
        Phase.DISCOVERY,
        ExecutorType.RETRIEVAL_SERVICE,
        depends_on=[parent.step_instance_id],
        task_group="literature_providers",
    )
    repository.update_step(
        study.study_id, crossref.step_instance_id, ExecutionStatus.RUNNING
    )
    repository.update_step(
        study.study_id, semantic.step_instance_id, ExecutionStatus.RUNNING
    )

    paused = repository.pause_study(study.study_id)
    snapshot = repository.snapshot(study.study_id)

    assert paused.execution_status is ExecutionStatus.PAUSED
    assert len(snapshot["study"]["current_step_ids"]) == 2
    assert snapshot["task_groups"]["literature_providers"] == {
        "completed": 0,
        "total": 2,
    }
    queued = repository.add_step(
        study.study_id,
        "metadata_validation",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    with pytest.raises(ValueError, match="paused study"):
        repository.update_step(
            study.study_id, queued.step_instance_id, ExecutionStatus.RUNNING
        )


def test_inferred_chain_cannot_support_formal_verdict() -> None:
    chain = EvidenceChain(
        chain_id="chain-" + "a" * 16,
        study_id="study-demo",
        level=EvidenceChainLevel.INFERRED,
        protocol_artifact_id="artifact-" + "1" * 16,
        output_artifact_ids=["artifact-" + "2" * 16],
    )
    assert chain.verdict_eligible() is False
    with pytest.raises(ValueError, match="verified evidence"):
        HypothesisVerdict(
            verdict_id="hverdict-" + "a" * 16,
            study_id="study-demo",
            hypothesis_id="hypothesis-primary",
            status=HypothesisVerdictStatus.SUPPORTED,
            evidence_chain_ids=[chain.chain_id],
            eligible_evidence=False,
            rationale="Content is semantically related but not bound.",
        )


def test_primary_hypothesis_verdicts_aggregate_without_secondary_vote() -> None:
    hypotheses = [
        Hypothesis(
            hypothesis_id="hypothesis-primary-a",
            statement="Primary A is better than its frozen baseline.",
            role=HypothesisRole.PRIMARY,
            decision_rule={"metric": "accuracy"},
        ),
        Hypothesis(
            hypothesis_id="hypothesis-primary-b",
            statement="Primary B preserves the task-native score.",
            role=HypothesisRole.PRIMARY,
            decision_rule={"metric": "native_score"},
        ),
        Hypothesis(
            hypothesis_id="hypothesis-secondary",
            statement="The secondary cost measure decreases.",
            role=HypothesisRole.SECONDARY,
            decision_rule={"metric": "cost"},
        ),
    ]
    verdicts = [
        HypothesisVerdict(
            verdict_id="hverdict-" + "1" * 16,
            study_id="study-demo",
            hypothesis_id="hypothesis-primary-a",
            status=HypothesisVerdictStatus.SUPPORTED,
            evidence_chain_ids=["chain-" + "1" * 16],
            eligible_evidence=True,
            rationale="Passed the frozen rule.",
        ),
        HypothesisVerdict(
            verdict_id="hverdict-" + "2" * 16,
            study_id="study-demo",
            hypothesis_id="hypothesis-primary-b",
            status=HypothesisVerdictStatus.REFUTED,
            evidence_chain_ids=["chain-" + "1" * 16],
            eligible_evidence=True,
            rationale="Failed the frozen rule.",
        ),
        HypothesisVerdict(
            verdict_id="hverdict-" + "3" * 16,
            study_id="study-demo",
            hypothesis_id="hypothesis-secondary",
            status=HypothesisVerdictStatus.SUPPORTED,
            evidence_chain_ids=["chain-" + "1" * 16],
            eligible_evidence=True,
            rationale="Descriptive secondary result.",
        ),
    ]
    aggregate = aggregate_study_verdict("study-demo", hypotheses, verdicts)
    assert aggregate.status is StudyVerdictStatus.MIXED
    assert verdicts[2].verdict_id not in aggregate.hypothesis_verdict_ids


def test_dependency_impact_invalidates_only_descendants(tmp_path: Path) -> None:
    repository, project_id = _repository(tmp_path)
    study = repository.create_study(
        project_id, "Impact study", entry_mode=EntryMode.PROJECT_TO_PAPER
    )
    protocol = repository.register_artifact(
        study.study_id,
        "protocol.json",
        "1" * 64,
        kind="protocol",
        role=ArtifactRole.PROTOCOL,
    )
    output = repository.register_artifact(
        study.study_id,
        "output.json",
        "2" * 64,
        kind="output",
        role=ArtifactRole.OUTPUT,
    )
    manuscript = repository.register_artifact(
        study.study_id,
        "manuscript.md",
        "3" * 64,
        kind="manuscript",
        role=ArtifactRole.MANUSCRIPT,
    )
    independent = repository.register_artifact(
        study.study_id,
        "background.json",
        "4" * 64,
        kind="literature",
        role=ArtifactRole.LITERATURE_BACKGROUND,
    )
    repository.add_dependency(study.study_id, protocol.artifact_id, output.artifact_id)
    repository.add_dependency(study.study_id, output.artifact_id, manuscript.artifact_id)

    impact = repository.impact(study.study_id, [output.artifact_id])

    assert impact["invalidated_artifact_ids"] == sorted(
        [output.artifact_id, manuscript.artifact_id]
    )
    assert independent.artifact_id in impact["reusable_artifact_ids"]
    assert protocol.artifact_id in impact["reusable_artifact_ids"]


def test_publication_ready_requires_system_ai_and_author(tmp_path: Path) -> None:
    repository, project_id = _repository(tmp_path)
    study = repository.create_study(
        project_id, "Publication study", entry_mode=EntryMode.IDEA_TO_PAPER
    )
    assessment = repository.save_readiness(
        ReadinessAssessment(
            study_id=study.study_id,
            system_publication_readiness=SystemReadiness.CONDITIONS_MET,
        )
    )
    assert assessment.publication_ready is False
    assert repository.submit_ai_review(
        study.study_id, AIReviewStatus.PASSED
    ).publication_ready is False
    _, approved = repository.submit_author_approval(
        study.study_id,
        AuthorApprovalStatus.APPROVED,
        decided_by="owner",
    )
    assert approved.publication_ready is True


def test_secret_guard_is_not_optional() -> None:
    assert is_secret_path(".env.local")
    assert is_secret_path("config/private-key.pem")
    assert is_secret_path("credentials.json")
    assert not is_secret_path("literature/query.json")


def test_baseline_verified_requires_every_frozen_condition() -> None:
    base = {
        "study_id": "study-demo",
        "run_id": "run-baseline",
        "baseline_run_succeeded": True,
        "preregistered_units_accounted_for": True,
        "output_schema_valid": True,
        "frozen_denominator_computable": True,
        "sample_integrity_valid": True,
        "hashes_match_contract": True,
        "artifact_binding_valid": True,
    }
    assert BaselineVerificationContract(**base).baseline_verified is True
    assert (
        BaselineVerificationContract(
            **{**base, "hashes_match_contract": False}
        ).baseline_verified
        is False
    )


def test_network_ledger_blocks_secrets_writes_and_budget_overrun(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Network project",
        network_policy=NetworkPolicy(budget_limit=1.0),
    )
    accepted = NetworkAuditEvent(
        event_id=stable_id("network", project.project_id, "first"),
        project_id=project.project_id,
        domain="api.crossref.org",
        method="GET",
        query_summary="metadata lookup",
        cost=0.5,
    )
    repository.record_network_request(accepted)
    with pytest.raises(ValueError, match="secret"):
        NetworkAuditEvent(
            event_id=stable_id("network", project.project_id, "secret"),
            project_id=project.project_id,
            domain="example.org",
            method="GET",
            request_file_paths=[".env"],
        )
    with pytest.raises(ValueError, match="explicit approval"):
        NetworkAuditEvent(
            event_id=stable_id("network", project.project_id, "write"),
            project_id=project.project_id,
            domain="example.org",
            method="POST",
        )
    with pytest.raises(ValueError, match="budget"):
        repository.record_network_request(
            NetworkAuditEvent(
                event_id=stable_id("network", project.project_id, "over"),
                project_id=project.project_id,
                domain="api.openalex.org",
                method="GET",
                cost=0.6,
            )
        )


def test_completion_record_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"value": 1}', encoding="utf-8")
    record = {
        "schema_version": 1,
        "completion_record_id": "completion-" + "a" * 16,
        "project_id": "project-demo",
        "study_id": "study-demo",
        "run_ids": ["run-1"],
        "completed_at": "2026-07-23T00:00:00+00:00",
        "issuer": "research-forge-local",
        "issuer_version": "0.1.0",
        "readiness": {
            "schema_version": 1,
            "study_id": "study-demo",
            "system_publication_readiness": "conditions_met",
            "ai_scientific_review": "passed",
            "author_publication_approval": "approved",
            "system_checks": {},
            "blockers": [],
            "assessed_at": "2026-07-23T00:00:00+00:00",
        },
        "artifact_hashes": {"artifact.json": sha256_file(artifact)},
        "verification_command": "research-forge workflow verify-completion completion_record.json",
    }
    write_json_atomic(
        tmp_path / "completion_record.json",
        seal_completion_record(CompletionRecord.model_validate(record)),
    )
    assert verify_completion_record(tmp_path / "completion_record.json")["passed"] is True

    artifact.write_text('{"value": 2}', encoding="utf-8")
    assert verify_completion_record(tmp_path / "completion_record.json")["passed"] is False

    artifact.write_text('{"value": 1}', encoding="utf-8")
    tampered = json.loads((tmp_path / "completion_record.json").read_text(encoding="utf-8"))
    tampered["issuer_version"] = "tampered"
    write_json_atomic(tmp_path / "completion_record.json", tampered)
    assert verify_completion_record(tmp_path / "completion_record.json")["passed"] is False


def test_legacy_bundle_migration_preserves_certificate_and_creates_v2_record(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    write_json_atomic(
        run / "bundle_manifest.json",
        {
            "source_root": "C:/research/project",
            "selected_track_id": "track-v1",
            "source_mode": "declared_chain",
            "resources": [{"path": "protocols/track-v1.json"}],
        },
    )
    write_json_atomic(
        run / "stage_1_discovery" / "scope_contract.json",
        {
            "title": "Track",
            "research_question": "Does the track improve the frozen metric?",
            "hypothesis_under_test": "The track improves the frozen metric.",
            "scope_in": ["frozen metric"],
            "scope_out": ["external claims"],
            "novelty_candidate": "A bounded comparison.",
        },
    )
    write_json_atomic(
        run / "stage_2_protocol" / "protocol_lock.json",
        {
            "protocol_bound_to_output": True,
            "protocol_sha256": "1" * 64,
            "output_sha256": "2" * 64,
        },
    )
    write_json_atomic(
        run / "stage_3_experimentation" / "idea_verdict.json",
        {"status": "supported", "conclusion": "The frozen rule passed."},
    )
    write_json_atomic(run / "stage_4_synthesis" / "claims.json", {"claims": []})
    (run / "stage_4_synthesis" / "manuscript.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (run / "stage_4_synthesis" / "manuscript.md").write_text(
        "# Paper", encoding="utf-8"
    )
    write_json_atomic(
        run / "stage_4_synthesis" / "audit.json",
        {
            "publication_ready": False,
            "checks": {"claims_bound": True},
            "publication_blockers": ["author approval pending"],
        },
    )
    write_json_atomic(
        run / "completion_certificate.json",
        {"completed_at": "2026-07-23T00:00:00+00:00"},
    )

    snapshot = migrate_bundle_run(run, repository_root=tmp_path / "repository")

    assert (run / "completion_certificate.json").is_file()
    assert (run / "completion_record.json").is_file()
    assert snapshot["study"]["legacy_stage"] == "completed"
    assert snapshot["study"]["support_level"] == "formal"
    assert len(snapshot["steps"]) == 14
    assert snapshot["evidence_chains"][0]["level"] == "verified_chain"
