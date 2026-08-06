"""Shared controlled end-to-end acceptance harness for two-arm Profiles.

This module is intentionally narrower than the production orchestrator.  It
constructs a fully frozen, deterministic acceptance Study for a registered
two-arm Profile, then hands the resulting evidence to the ordinary Stage 4
DAG.  Profile-specific modules supply scientific semantics; persistence,
authority, evidence binding, paper generation, and owner gates remain shared.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..models import utc_now
from ..profiles.stage_four_evidence import (
    build_stage_four_evidence_from_repository,
    queue_stage_four_from_profile_handoff,
)
from ..storage import sha256_file, write_json_atomic
from ..web_app import initialize_idea_research
from ..workflow_domain import (
    AnalysisEligibilityStatus,
    ArtifactRole,
    ArtifactStatus,
    ConfirmatoryStatus,
    EntryMode,
    EvaluationRecord,
    EvidenceChain,
    EvidenceChainLevel,
    EvidenceEdge,
    EvidenceRelation,
    EvidenceReproductionLevel,
    ExecutionAttempt,
    ExecutionStatus,
    ExecutorType,
    GateType,
    Hypothesis,
    HypothesisRole,
    HypothesisVerdict,
    HypothesisVerdictStatus,
    Phase,
    ProtocolStatus,
    PublicationMode,
    QualificationStatus,
    ResearchContractVersion,
    ResearchRun,
    ResultEnvelope,
    RunCell,
    RunCellStatus,
    RunKind,
    RunPlan,
    ScientificClaimEnvelope,
    ScopeContractVersion,
    Stage3CompletionPackage,
    Stage3HandoffPackage,
    Stage3Profile,
    StudyVerdict,
    StudyVerdictStatus,
    WorkflowRepository,
    stable_id,
)
from .schemas import AnalysisPlan, ClaimEnvelope, StudyDesignEvaluation
from ..profile_figure_data import persist_profile_figure_data_bundle
from .workflow_acceptance import (
    _complete_step,
    _digest,
    _register_verified_methodology_literature,
)


@dataclass(frozen=True)
class ControlledProfileAcceptanceConfig:
    title: str
    profile_id: str
    profile_factory: Callable[[], Any]
    plan_factory: Callable[[], AnalysisPlan]
    rows_factory: Callable[[], list[dict[str, Any]]]
    recalculator: Callable[[AnalysisPlan, list[dict[str, Any]]], dict[str, Any]]
    dataset_filename: str
    dataset_fields: tuple[str, ...]
    direction: str
    research_question: str
    candidate_contribution: str
    unit_of_analysis: str
    population_or_corpus: str
    primary_outcome: str
    comparison: str
    scope_in: tuple[str, ...]
    scope_out: tuple[str, ...]
    hypothesis_id: str
    hypothesis_statement: str
    hypothesis_decision_rule: dict[str, Any]
    fixture_role: str
    fixture_task: str
    task_specification: dict[str, Any]
    allocation_description: str
    allocation_fields: tuple[str, ...]
    run_task_id: str
    experiment_id: str
    run_seed: int
    result_kind: str
    maximum_claim_tier: str
    allowed_claim_supported: str
    allowed_claim_other: str
    known_limitations: tuple[str, ...]
    runtime_entrypoint: str
    evaluator_name: str
    independent_unit_label: str
    paired: bool = False


def _write_rows(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _arm_estimate(stats: dict[str, float | int | None]) -> float | None:
    for key in (
        "proportion",
        "mean_item_rating",
        "mean",
        "event_rate",
        "rmst",
    ):
        value = stats.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _reference_agrees(
    evaluation: StudyDesignEvaluation,
    reference: dict[str, Any],
) -> bool:
    if str(reference.get("primary_decision")) != evaluation.primary_decision:
        return False
    outcomes = dict(reference.get("outcomes") or {})
    if set(outcomes) != {item.outcome_id for item in evaluation.outcomes}:
        return False
    for item in evaluation.outcomes:
        expected = outcomes[item.outcome_id]
        if expected.get("decision") != item.decision:
            return False
        if int(expected.get("denominator", -1)) != item.denominator:
            return False
        for left, right in (
            (expected.get("effect"), item.effect),
            (expected.get("missing_count"), item.missing_count),
        ):
            if left is None or right is None:
                if left != right:
                    return False
            elif abs(float(left) - float(right)) > 1e-12:
                return False
        expected_interval = list(expected.get("confidence_interval") or [])
        observed_interval = list(item.confidence_interval or [])
        if len(expected_interval) != len(observed_interval) or any(
            abs(float(left) - float(right)) > 1e-12
            for left, right in zip(expected_interval, observed_interval)
        ):
            return False
    return True


def prepare_controlled_profile_workflow_acceptance(
    output_root: str | Path,
    config: ControlledProfileAcceptanceConfig,
) -> dict[str, Any]:
    """Build frozen Stage 1--3 evidence and queue the canonical Stage 4 DAG."""

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    intake = initialize_idea_research(
        config.title,
        title=config.title,
        idea_root=root / "idea_runs",
    )
    repository = WorkflowRepository(intake["workflow_repository"])
    study_id = str(intake["study_id"])
    study = repository.load_study(study_id)
    if study.entry_mode is not EntryMode.IDEA_TO_PAPER:
        raise ValueError("Profile acceptance must start from Idea-to-paper")
    repository.save_study(
        study.model_copy(
            update={"settings": {**study.settings, "manuscript_language": "en"}}
        ),
        f"{config.profile_id}_acceptance_language_selected",
    )
    study_root = repository.root / "studies" / study_id

    scope = ScopeContractVersion(
        study_id=study_id,
        version=1,
        direction=config.direction,
        research_question=config.research_question,
        scope_in=list(config.scope_in),
        scope_out=list(config.scope_out),
        candidate_contribution=config.candidate_contribution,
        unit_of_analysis=config.unit_of_analysis,
        study_design=config.direction,
        population_or_corpus=config.population_or_corpus,
        primary_outcome=config.primary_outcome,
        comparison=config.comparison,
        feasibility_basis=["versioned local fixture", "independent recalculator"],
        created_by=f"{config.profile_id}_acceptance_designer",
    )
    repository.save_scope_contract(scope)
    literature = _register_verified_methodology_literature(repository, study_id)
    for gate in repository.list_gates(study_id):
        if gate.gate_type is GateType.SCOPE_APPROVAL and gate.status.value == "awaiting_user":
            repository.decide_gate(
                study_id,
                gate.gate_id,
                approve=True,
                decided_by="profile_acceptance_owner",
                reason="Approve the controlled Idea-to-paper acceptance scope.",
            )
    scope_gate = repository.create_gate(
        study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study_id}:scope:v1",
        subject_version=1,
    )
    repository.decide_gate(
        study_id,
        scope_gate.gate_id,
        approve=True,
        decided_by="profile_acceptance_owner",
        reason=f"Approve the frozen {config.profile_id} acceptance scope.",
    )
    repository.save_scope_contract(
        scope.model_copy(update={"status": ArtifactStatus.FROZEN, "frozen_at": utc_now()})
    )
    scope_step = _complete_step(
        repository,
        study_id,
        "scope_drafting",
        Phase.DISCOVERY,
        ExecutorType.CODEX,
        {"scope_version": 1, "status": "frozen", "owner_gate_id": scope_gate.gate_id},
    )
    freeze_scope_step = _complete_step(
        repository,
        study_id,
        "freeze_scope_contract",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        {
            "scope_version": 1,
            "sha256": sha256_file(study_root / "contracts" / "scope-v1.json"),
        },
        depends_on=[scope_step],
    )
    _complete_step(
        repository,
        study_id,
        "freeze_literature_set",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        {
            "literature_set_id": literature.literature_set_id,
            "version": literature.version,
            "verified_source_count": len(literature.background_source_ids),
            "evidence_role": "background_only",
        },
        depends_on=[freeze_scope_step],
    )

    plan = config.plan_factory()
    rows = config.rows_factory()
    design = config.profile_factory()
    dataset_path = study_root / "stage3" / "resources" / config.dataset_filename
    _write_rows(dataset_path, config.dataset_fields, rows)
    dataset_sha = sha256_file(dataset_path)

    task_spec_path = study_root / "stage3" / "resources" / "task_and_scoring_specification.json"
    task_spec = {
        "schema_version": 1,
        "fixture_role": config.fixture_role,
        "scientific_boundary": (
            "This frozen fixture validates the governed research workflow and "
            "registered estimator. It does not establish transport beyond the "
            "declared synthetic acceptance population."
        ),
        "task": config.fixture_task,
        "registered_plan": plan.model_dump(mode="json"),
        **config.task_specification,
    }
    write_json_atomic(task_spec_path, task_spec)
    task_spec_sha = sha256_file(task_spec_path)
    allocation_path = study_root / "stage3" / "resources" / "allocation_ledger.json"
    allocation_rows = [
        {key: row[key] for key in config.allocation_fields}
        for row in rows
    ]
    write_json_atomic(
        allocation_path,
        {
            "schema_version": 1,
            "mechanism": config.allocation_description,
            "seed": config.run_seed,
            "rows": allocation_rows,
        },
    )
    allocation_sha = sha256_file(allocation_path)

    qualification_step = _complete_step(
        repository,
        study_id,
        "study_design_qualification",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {
            "qualified": True,
            "study_design": config.profile_id,
            "known_limits": list(design.descriptor.known_limits),
        },
        depends_on=[freeze_scope_step],
    )
    primary_outcome = next(item for item in plan.outcomes if item.role == "primary")
    primary_rule = plan.decision_rules[primary_outcome.outcome_id]
    contract = ResearchContractVersion(
        study_id=study_id,
        version=1,
        scope_version=1,
        protocol_status=ProtocolStatus.FROZEN_EXECUTABLE,
        hypotheses=[
            Hypothesis(
                hypothesis_id=config.hypothesis_id,
                statement=config.hypothesis_statement,
                role=HypothesisRole.PRIMARY,
                decision_rule=config.hypothesis_decision_rule,
            )
        ],
        data_boundary={
            "population": config.population_or_corpus,
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "unit": config.unit_of_analysis,
            "inclusion": plan.missingness.denominator_rule,
            "exclusion": "Only frozen eligibility and integrity rules may exclude observations.",
            "time_boundary": task_specification_time_boundary(task_spec),
            "synthetic_fixture_disclosure": True,
        },
        metrics=[
            {
                "name": item.label,
                "outcome_id": item.outcome_id,
                "direction": item.beneficial_direction,
                "denominator": plan.missingness.denominator_rule,
                "role": item.role,
                "kind": item.kind,
            }
            for item in plan.outcomes
        ],
        baseline=plan.arms[0].model_dump(mode="json"),
        treatment=plan.arms[1].model_dump(mode="json"),
        tasks=[config.fixture_task],
        seeds=[config.run_seed],
        splits=["formal"],
        replicates=1,
        runtime_binding={"entrypoint": config.runtime_entrypoint, "network": "disabled"},
        evaluator_policy={
            "authority": config.evaluator_name,
            "independent_recalculation_required": True,
            "estimator_plan": {
                key: value.model_dump(mode="json")
                for key, value in plan.estimator_plan.items()
            },
            "multiplicity": plan.multiplicity.model_dump(mode="json"),
        },
        eligibility_rules=[
            {"rule": "the dataset hash equals the frozen resource hash"},
            {"rule": plan.missingness.denominator_rule},
            {"rule": "all Profile qualification checks must pass"},
        ],
        experiment_profile=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
        domain_execution_profile={"id": config.profile_id, "version": "1"},
        study_design={"id": config.profile_id, "version": "1"},
        inference_modules=(
            [{"id": "multiplicity_control_v1", "version": "1"}]
            if plan.multiplicity.method != "no_correction"
            else []
        ),
        study_design_spec=plan.model_dump(mode="json"),
        output_schema={"type": "object", "required": ["outcomes", "primary_decision"]},
        statistical_rules={
            "primary_outcome_id": primary_outcome.outcome_id,
            "primary_decision_rule": primary_rule.model_dump(mode="json"),
            "inference_plan": {
                key: value.model_dump(mode="json")
                for key, value in plan.inference_plan.items()
            },
            "multiplicity": plan.multiplicity.model_dump(mode="json"),
        },
        estimand=next(
            item.model_dump(mode="json")
            for item in plan.estimands
            if item.outcome_id == primary_outcome.outcome_id
        ),
        data_requirements={
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "required_rows": len(rows),
        },
        implementation_requirements={
            "profile_id": config.profile_id,
            "registered_plan_hash": _digest(plan.model_dump(mode="json")),
            "preanalysis_rounding": "none",
        },
        environment_requirements={
            "python": "current locked Research Forge environment",
            "network": "disabled",
            "external_state": "none",
        },
        resource_policy={
            "approved_dataset_sha256": dataset_sha,
            "approved_task_specification_sha256": task_spec_sha,
            "approved_allocation_ledger_sha256": allocation_sha,
        },
        created_by=f"deterministic_{config.profile_id}_contract_completion",
    )
    repository.save_research_contract(contract)
    contract_step = _complete_step(
        repository,
        study_id,
        "research_contract_completion",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        {"contract_version": 1, "completion_issues": [], "complete": True},
        depends_on=[qualification_step],
    )
    contract_gate = repository.create_gate(
        study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study_id}:research-contract:v1",
        subject_version=1,
    )
    repository.decide_gate(
        study_id,
        contract_gate.gate_id,
        approve=True,
        decided_by="profile_acceptance_owner",
        reason=f"Approve the complete prespecified {config.profile_id} contract.",
    )
    frozen_contract = contract.model_copy(
        update={"status": ArtifactStatus.FROZEN, "frozen_at": utc_now()}
    )
    repository.save_research_contract(frozen_contract)
    freeze_contract_step = _complete_step(
        repository,
        study_id,
        "freeze_research_contract",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        {"contract_version": 1, "owner_gate_id": contract_gate.gate_id, "status": "frozen"},
        depends_on=[contract_step],
    )

    dataset_artifact = repository.register_artifact(
        study_id, str(dataset_path), dataset_sha, kind="study_design_dataset", role=ArtifactRole.OUTPUT
    )
    task_spec_artifact = repository.register_artifact(
        study_id, str(task_spec_path), task_spec_sha,
        kind="task_and_scoring_specification", role=ArtifactRole.PROTOCOL,
    )
    allocation_artifact = repository.register_artifact(
        study_id, str(allocation_path), allocation_sha,
        kind="allocation_ledger", role=ArtifactRole.PROTOCOL,
    )
    resource_step = _complete_step(
        repository,
        study_id,
        "study_design_resource_binding",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
        {
            "dataset_artifact_id": dataset_artifact.artifact_id,
            "task_specification_artifact_id": task_spec_artifact.artifact_id,
            "allocation_ledger_artifact_id": allocation_artifact.artifact_id,
            "sha256": dataset_sha,
        },
        depends_on=[freeze_contract_step],
    )
    dry_rows = rows[: min(8, len(rows))]
    dry_step = _complete_step(
        repository,
        study_id,
        "study_design_dry_run",
        Phase.EXPERIMENT,
        ExecutorType.SANDBOX_RUNNER,
        {
            "evidence_eligible": False,
            "rows": len(dry_rows),
            "schema_valid": all(set(config.dataset_fields) == set(row) for row in dry_rows),
            "purpose": "resource and schema feasibility only",
        },
        depends_on=[resource_step],
    )

    lock_ids: dict[str, str] = {}
    locks_root = study_root / "stage3" / "locks"
    for name, value in {
        "protocol": frozen_contract.model_dump(mode="json"),
        "dataset": {"path": str(dataset_path), "sha256": dataset_sha},
        "task_specification": {"path": str(task_spec_path), "sha256": task_spec_sha},
        "allocation": {"path": str(allocation_path), "sha256": allocation_sha},
        "evaluator": {"implementation": config.evaluator_name},
        "environment": {"python": "locked current environment", "network": "disabled"},
        "analysis": plan.model_dump(mode="json"),
    }.items():
        path = locks_root / f"{name}.json"
        write_json_atomic(path, value)
        artifact = repository.register_artifact(
            study_id, str(path), sha256_file(path),
            kind=f"study_design_{name}_lock", role=ArtifactRole.PROTOCOL,
        )
        lock_ids[name] = artifact.artifact_id
    manifest_path = study_root / "stage3" / "experiment_manifest.json"
    write_json_atomic(
        manifest_path,
        {
            "profile_id": config.profile_id,
            "dataset_sha256": dataset_sha,
            "task_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "locks": lock_ids,
        },
    )
    manifest_sha = sha256_file(manifest_path)
    handoff_id = stable_id("stage3-handoff", study_id, dataset_sha, config.profile_id)
    repository.save_stage3_handoff(
        Stage3HandoffPackage(
            study_id=study_id,
            handoff_id=handoff_id,
            scope_version=1,
            contract_version=1,
            profile=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
            lock_artifact_ids=lock_ids,
            experiment_manifest_path=str(manifest_path),
            experiment_manifest_sha256=manifest_sha,
            execution_root=str(study_root / "stage3"),
            resource_artifact_ids=[
                dataset_artifact.artifact_id,
                task_spec_artifact.artifact_id,
                allocation_artifact.artifact_id,
            ],
        )
    )
    plan_id = stable_id("run-plan", study_id, handoff_id, manifest_sha)
    arm_ids = [item.arm_id for item in plan.arms]
    cells = [
        RunCell(
            run_cell_id=stable_id("run-cell", plan_id, arm_id),
            study_id=study_id,
            plan_id=plan_id,
            contract_version=1,
            task_id=config.run_task_id,
            split_id="formal",
            arm_id=arm_id,
            seed=config.run_seed,
            replicate=1,
            action_id=f"action-{arm_id}",
            experiment_id=config.experiment_id,
            expected_output_schema={"type": "object", "required": ["arm_id", "rows"]},
            execution_manifest_hash=manifest_sha,
            status=RunCellStatus.SUCCEEDED,
            pair_block_id=("registered-paired-arm-block" if config.paired else None),
            pair_position=position,
        )
        for position, arm_id in enumerate(arm_ids, start=1)
    ]
    run_plan_payload = {
        "schema_version": 1,
        "plan_id": plan_id,
        "study_id": study_id,
        "handoff_id": handoff_id,
        "contract_version": 1,
        "profile": Stage3Profile.EXISTING_PYTHON_PROJECT_V1.value,
        "compiler_version": "study-design-kernel-v1",
        "concurrency": 1,
        "cells": [item.model_dump(mode="json") for item in cells],
    }
    run_plan = repository.save_run_plan(
        RunPlan(**run_plan_payload, plan_hash=_digest(run_plan_payload))
    )
    run = repository.save_research_run(
        ResearchRun(
            run_id=f"run-{config.profile_id.replace('_v1', '').replace('_', '-')}-formal-v1",
            study_id=study_id,
            contract_version=1,
            kind=RunKind.EXPERIMENTAL,
            status=ExecutionStatus.SUCCEEDED,
            output_artifact_ids=[dataset_artifact.artifact_id],
            completed_at=utc_now(),
        )
    )
    code_hash = _digest({"profile": config.profile_id, "plan": plan.model_dump(mode="json")})
    environment_hash = _digest({"python": "locked", "network": "disabled"})
    result_ids: list[str] = []
    for cell in cells:
        cell_rows = [row for row in rows if str(row["arm"]) == cell.arm_id]
        sample_key = "item_id" if config.paired else "subject_id"
        sample_ids = sorted({str(row[sample_key]) for row in cell_rows})
        output_path = study_root / "stage3" / "results" / f"{cell.arm_id}-result.json"
        write_json_atomic(
            output_path,
            {
                "arm_id": cell.arm_id,
                "rows": len(cell_rows),
                "independent_units": len(sample_ids),
                "sample_ids": sample_ids,
            },
        )
        artifact = repository.register_artifact(
            study_id, str(output_path), sha256_file(output_path),
            kind=config.result_kind, role=ArtifactRole.OUTPUT,
        )
        attempt = repository.save_execution_attempt(
            ExecutionAttempt(
                attempt_id=stable_id("attempt", study_id, cell.run_cell_id, "1"),
                study_id=study_id,
                run_cell_id=cell.run_cell_id,
                attempt_number=1,
                status=RunCellStatus.SUCCEEDED,
                started_at=run.created_at,
                finished_at=run.completed_at,
                exit_status=0,
                input_hashes={
                    "dataset": dataset_sha,
                    "task_specification": task_spec_sha,
                    "allocation_ledger": allocation_sha,
                },
                environment_hash=environment_hash,
                code_hash=code_hash,
                output_artifact_ids=[artifact.artifact_id],
                isolation_attestations={"network_disabled": True},
                canonical=True,
            )
        )
        result = repository.save_result_envelope(
            ResultEnvelope(
                result_id=stable_id("result", study_id, cell.run_cell_id, "1"),
                study_id=study_id,
                run_cell_id=cell.run_cell_id,
                attempt_id=attempt.attempt_id,
                output_artifact_ids=[artifact.artifact_id],
                metrics={"row_count": float(len(cell_rows))},
                denominator=len(sample_ids),
                sample_ids=sample_ids,
                analysis_rows=cell_rows,
            )
        )
        result_ids.append(result.result_id)

    evaluation = design.evaluate(plan, rows)
    reference = config.recalculator(plan, rows)
    if not evaluation.eligible or not _reference_agrees(evaluation, reference):
        raise ValueError(f"{config.profile_id} production and independent reference disagree")
    evaluation_path = study_root / "stage3" / "study_design_evaluation.json"
    reference_path = study_root / "stage3" / "independent_recalculation.json"
    write_json_atomic(evaluation_path, evaluation)
    write_json_atomic(reference_path, reference)
    evaluation_artifact = repository.register_artifact(
        study_id, str(evaluation_path), sha256_file(evaluation_path),
        kind="study_design_evaluation", role=ArtifactRole.EVALUATION,
    )
    reference_artifact = repository.register_artifact(
        study_id, str(reference_path), sha256_file(reference_path),
        kind="independent_recalculation", role=ArtifactRole.EVALUATION,
    )
    outcome_specs = {item.outcome_id: item for item in plan.outcomes}
    evaluation_records: list[EvaluationRecord] = []
    evidence_edges: list[EvidenceEdge] = []
    for outcome in evaluation.outcomes:
        spec = outcome_specs[outcome.outcome_id]
        arm_estimates = {
            arm_id: estimate
            for arm_id, stats in outcome.arm_statistics.items()
            if (estimate := _arm_estimate(stats)) is not None
        }
        record = repository.save_evaluation_record(
            EvaluationRecord(
                evaluation_id=stable_id(
                    "evaluation", study_id, plan_id, outcome.outcome_id,
                    sha256_file(evaluation_path),
                ),
                study_id=study_id,
                plan_id=plan_id,
                contract_version=1,
                qualification_status=QualificationStatus.QUALIFIED,
                qualification_checks=evaluation.qualification_checks,
                metric_name=spec.label,
                baseline_estimate=arm_estimates.get("control"),
                treatment_estimate=arm_estimates.get("treatment"),
                paired_effect=outcome.effect,
                pair_count=(outcome.denominator if config.paired else 0),
                independent_unit_count=outcome.denominator,
                variance_unit=config.independent_unit_label,
                aggregation_hierarchy=[config.independent_unit_label, "study arm"],
                confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
                confidence_interval=outcome.confidence_interval,
                arm_estimates=arm_estimates,
                contrast_estimates={
                    outcome.outcome_id: {
                        "effect_measure": outcome.effect_measure,
                        "effect": outcome.effect,
                    }
                },
                statistical_rule={
                    "estimator": plan.estimator_plan[outcome.outcome_id].model_dump(mode="json"),
                    "inference": plan.inference_plan[outcome.outcome_id].model_dump(mode="json"),
                    "multiplicity": plan.multiplicity.model_dump(mode="json"),
                    "raw_p_value": outcome.raw_p_value,
                    "adjusted_p_value": outcome.adjusted_p_value,
                },
                decision=HypothesisVerdictStatus(outcome.decision),
                rationale="The frozen Profile estimator and decision rule were applied to verified data.",
                result_ids=result_ids,
            )
        )
        evaluation_records.append(record)
        evidence_edges.append(
            repository.save_evidence_edge(
                EvidenceEdge(
                    edge_id=stable_id(
                        "evidence-edge", dataset_artifact.artifact_id, record.evaluation_id
                    ),
                    study_id=study_id,
                    source_id=dataset_artifact.artifact_id,
                    target_id=record.evaluation_id,
                    relation_type=EvidenceRelation.EVALUATED_BY,
                    source_hash=dataset_sha,
                    target_hash=_digest(record.model_dump(mode="json")),
                    created_by=config.profile_id,
                    qualification_status=QualificationStatus.QUALIFIED,
                )
            )
        )
    figure_bundle, figure_artifact = persist_profile_figure_data_bundle(
        repository,
        study_id,
        profile_id=config.profile_id,
        evaluation=evaluation,
        research_contract_spec=plan,
        formal_rows=rows,
        source_artifacts=[dataset_artifact, evaluation_artifact, reference_artifact],
        evaluation_records=evaluation_records,
    )
    if not figure_bundle.complete:
        raise ValueError(f"{config.profile_id} FigureData bundle is incomplete")
    primary = next(item for item in evaluation.outcomes if outcome_specs[item.outcome_id].role == "primary")
    claim_boundary: ClaimEnvelope = design.produce_claim_envelope(plan, evaluation)
    allowed_claim = (
        config.allowed_claim_supported
        if evaluation.primary_decision == "supported"
        else config.allowed_claim_other
    )
    claim = repository.save_claim_envelope(
        ScientificClaimEnvelope(
            claim_envelope_id=stable_id(
                "claim-envelope", study_id, plan_id, evaluation.primary_decision
            ),
            study_id=study_id,
            plan_id=plan_id,
            allowed_claim=allowed_claim,
            population=config.population_or_corpus,
            tasks=[config.fixture_task],
            intervention=plan.arms[1].definition,
            comparator=plan.arms[0].definition,
            outcome=primary_outcome.label,
            effect_estimate=primary.effect,
            interval=primary.confidence_interval,
            evidence_level=EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED,
            confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
            known_limitations=list(config.known_limitations) + list(evaluation.limitations),
            maximum_claim_tier=config.maximum_claim_tier,
            publication_mode=PublicationMode.RESULTS_MANUSCRIPT,
            prohibited_generalizations=claim_boundary.prohibited_claims,
        )
    )
    claim_path = study_root / "stage3" / "claim_envelopes" / f"{claim.claim_envelope_id}.json"
    claim_artifact = repository.register_artifact(
        study_id, str(claim_path), sha256_file(claim_path),
        kind="scientific_claim_envelope", role=ArtifactRole.CLAIM,
    )
    contract_path = study_root / "contracts" / "research-v1.json"
    contract_artifact = repository.register_artifact(
        study_id, str(contract_path), sha256_file(contract_path),
        kind="research_contract", role=ArtifactRole.PROTOCOL,
    )
    chain = repository.save_evidence_chain(
        EvidenceChain(
            chain_id=stable_id("chain", study_id, plan_id, "verified"),
            study_id=study_id,
            level=EvidenceChainLevel.VERIFIED,
            protocol_artifact_id=contract_artifact.artifact_id,
            run_artifact_ids=[
                dataset_artifact.artifact_id,
                task_spec_artifact.artifact_id,
                allocation_artifact.artifact_id,
            ],
            output_artifact_ids=[evaluation_artifact.artifact_id, reference_artifact.artifact_id],
            evaluation_artifact_ids=[evaluation_artifact.artifact_id],
            claim_artifact_ids=[claim_artifact.artifact_id],
            implementation_version=config.profile_id,
            verified_checks={
                "dataset_hash": True,
                "profile_qualification": all(evaluation.qualification_checks.values()),
                "reference_agreement": True,
                "claim_boundary": True,
            },
        )
    )
    verdict_status = HypothesisVerdictStatus(evaluation.primary_decision)
    hypothesis_verdict = repository.save_hypothesis_verdict(
        HypothesisVerdict(
            verdict_id=stable_id("hverdict", study_id, config.hypothesis_id, plan_id),
            study_id=study_id,
            hypothesis_id=config.hypothesis_id,
            status=verdict_status,
            evidence_chain_ids=[chain.chain_id],
            eligible_evidence=True,
            rationale="The frozen primary rule was applied to verified data and independently recalculated.",
        )
    )
    study_verdict_status = StudyVerdictStatus(evaluation.primary_decision)
    study_verdict = repository.save_study_verdict(
        StudyVerdict(
            verdict_id=stable_id("sverdict", study_id, plan_id, evaluation.primary_decision),
            study_id=study_id,
            status=study_verdict_status,
            hypothesis_verdict_ids=[hypothesis_verdict.verdict_id],
            rationale="The Study verdict equals the registered primary hypothesis verdict.",
        )
    )
    completion = repository.save_stage3_completion(
        Stage3CompletionPackage(
            completion_id=stable_id(
                "stage3-completion", study_id, run_plan.plan_hash, evaluation.primary_decision
            ),
            study_id=study_id,
            plan_id=plan_id,
            handoff_id=handoff_id,
            evaluation_ids=[item.evaluation_id for item in evaluation_records],
            evidence_edge_ids=[item.edge_id for item in evidence_edges],
            hypothesis_verdict_ids=[hypothesis_verdict.verdict_id],
            study_verdict_id=study_verdict.verdict_id,
            qualification_status=QualificationStatus.QUALIFIED,
            artifact_hashes={
                str(evaluation_path.relative_to(study_root)).replace("\\", "/"): sha256_file(evaluation_path),
                str(reference_path.relative_to(study_root)).replace("\\", "/"): sha256_file(reference_path),
                str(task_spec_path.relative_to(study_root)).replace("\\", "/"): task_spec_sha,
                str(allocation_path.relative_to(study_root)).replace("\\", "/"): allocation_sha,
            },
            claim_envelope_id=claim.claim_envelope_id,
            evidence_level=EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED,
            confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
            analysis_eligibility=AnalysisEligibilityStatus.QUALIFIED,
            scientific_verdict_status=study_verdict_status,
            publication_mode=PublicationMode.RESULTS_MANUSCRIPT,
        )
    )
    formal_step = _complete_step(
        repository,
        study_id,
        "study_design_formal_evaluation",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"evaluation_ids": [item.evaluation_id for item in evaluation_records], "run_id": run.run_id},
        depends_on=[dry_step],
    )
    reference_step = _complete_step(
        repository,
        study_id,
        "study_design_independent_recalculation",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"artifact_id": reference_artifact.artifact_id, "agreement": True},
        depends_on=[formal_step],
    )
    _complete_step(
        repository,
        study_id,
        "study_design_scientific_verdict",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"verdict_id": study_verdict.verdict_id, "status": study_verdict.status.value},
        depends_on=[reference_step],
    )
    repository.save_study(
        repository.load_study(study_id).model_copy(
            update={"phase": Phase.PAPER, "execution_status": ExecutionStatus.QUEUED}
        ),
        f"{config.profile_id}_stage3_completed",
    )
    stage_four_handoff = build_stage_four_evidence_from_repository(repository, study_id)
    stage_four_steps = queue_stage_four_from_profile_handoff(repository, stage_four_handoff)
    return {
        "study_id": study_id,
        "project_id": study.project_id,
        "profile_id": config.profile_id,
        "workflow_repository": str(repository.root),
        "stage3_completion_id": completion.completion_id,
        "scientific_verdict": study_verdict.status.value,
        "stage_four_steps": len(stage_four_steps),
        "stage_four_queued": True,
    }


def task_specification_time_boundary(task_spec: dict[str, Any]) -> str:
    value = task_spec.get("time_boundary")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "the single frozen formal execution window declared in the task specification"


def run_controlled_profile_workflow_acceptance(
    root: Path,
    config: ControlledProfileAcceptanceConfig,
    *,
    approve_owner_gates: bool = True,
    decided_by: str = "automated Profile acceptance owner",
    max_cycles: int = 12,
) -> dict[str, Any]:
    """Run the ordinary Stage 4 DAG after the controlled Profile handoff."""

    from ..workflow_domain import GateStatus
    from ..workflow_scheduler import PersistentDAGScheduler, workflow_handlers

    prepared = prepare_controlled_profile_workflow_acceptance(root, config)
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = str(prepared["study_id"])
    publication_gate_types = {
        GateType.PUBLICATION_NARRATIVE,
        GateType.VISUAL_ARGUMENT,
        GateType.AUTHOR_VOICE,
        GateType.FINAL_SUBMISSION,
    }
    scheduler = PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=1, recover_interrupted=True
    )
    approvals: list[str] = []
    snapshot: dict[str, Any] = {}
    for _ in range(max_cycles):
        snapshot = scheduler.run(study_id)
        waiting = [
            gate
            for gate in repository.list_gates(study_id)
            if gate.status is GateStatus.AWAITING_USER
            and gate.gate_type in publication_gate_types
        ]
        if approve_owner_gates and waiting:
            for gate in waiting:
                repository.decide_gate(
                    study_id,
                    gate.gate_id,
                    approve=True,
                    decided_by=decided_by,
                    reason=(
                        "Approved for the controlled black-box Profile acceptance; "
                        "scientific and evidence blockers remain non-overridable."
                    ),
                )
                approvals.append(gate.gate_id)
            continue
        break
    steps = repository.list_steps(study_id)
    return {
        **prepared,
        "owner_gate_approvals": approvals,
        "snapshot": snapshot,
        "step_status_counts": {
            status.value: sum(step.status is status for step in steps)
            for status in ExecutionStatus
        },
        "blocking_steps": [
            {"step_type": step.step_type, "status": step.status.value, "blocker": step.blocker}
            for step in steps
            if step.status in {ExecutionStatus.BLOCKED, ExecutionStatus.FAILED}
        ],
    }


__all__ = [
    "ControlledProfileAcceptanceConfig",
    "prepare_controlled_profile_workflow_acceptance",
    "run_controlled_profile_workflow_acceptance",
]
