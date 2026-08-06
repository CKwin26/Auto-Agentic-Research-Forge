"""Canonical four-phase acceptance harness for a randomized 2x2 factorial Study.

Only the scientific fixture adapter is Profile-specific.  Persistence, gates,
Stage 4 evidence projection, manuscript writing, audits, and completion records
all use the same production Workflow v2 path as every other Study.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

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
from .designs.factorial import FactorialExperiment
from .factorial_case import (
    FACTORIAL_TITLE,
    factorial_acceptance_plan,
    factorial_acceptance_rows,
)
from .factorial_reference import recalculate_factorial
from .schemas import AnalysisPlan
from ..profile_figure_data import persist_profile_figure_data_bundle
from .workflow_acceptance import (
    _complete_step,
    _digest,
    _register_verified_methodology_literature,
)


def _write_factorial_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["subject_id", "arm", "assistance", "feedback", "quality"],
        )
        writer.writeheader()
        writer.writerows(rows)


def prepare_factorial_workflow_acceptance(output_root: str | Path) -> dict[str, Any]:
    """Create frozen Stage 1--3 objects and queue the canonical Stage 4 DAG."""

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    intake = initialize_idea_research(
        FACTORIAL_TITLE,
        title=FACTORIAL_TITLE,
        idea_root=root / "idea_runs",
    )
    repository = WorkflowRepository(intake["workflow_repository"])
    study_id = str(intake["study_id"])
    study = repository.load_study(study_id)
    if study.entry_mode is not EntryMode.IDEA_TO_PAPER:
        raise ValueError("factorial acceptance must start from Idea-to-paper")
    repository.save_study(
        study.model_copy(
            update={"settings": {**study.settings, "manuscript_language": "en"}}
        ),
        "factorial_acceptance_language_selected",
    )
    study_root = repository.root / "studies" / study_id

    scope = ScopeContractVersion(
        study_id=study_id,
        version=1,
        direction="Randomized factorial evaluation of two design features",
        research_question=FACTORIAL_TITLE.removesuffix(
            " A Prespecified Randomized Factorial Study"
        ),
        scope_in=[
            "two independently randomized binary factors",
            "all four factorial cells",
            "one continuous task-quality outcome",
            "two main effects and their interaction",
        ],
        scope_out=[
            "repeated measures",
            "clustered allocation",
            "higher-order or incomplete factorial designs",
            "generalization beyond the deterministic acceptance fixture",
        ],
        candidate_contribution=(
            "A black-box demonstration that Research Forge can preserve and "
            "report main effects and interaction evidence through one governed "
            "idea-to-paper workflow."
        ),
        unit_of_analysis="independently allocated deterministic software task",
        study_design="randomized two-by-two factorial experiment",
        population_or_corpus="80 frozen deterministic software tasks",
        primary_outcome="continuous task-quality score",
        comparison=(
            "average assistance effect, average feedback effect, and the "
            "assistance-by-feedback interaction"
        ),
        feasibility_basis=["versioned local fixture", "independent recalculator"],
        created_by="factorial_acceptance_designer",
    )
    repository.save_scope_contract(scope)
    literature = _register_verified_methodology_literature(repository, study_id)
    for intake_gate in repository.list_gates(study_id):
        if (
            intake_gate.gate_type is GateType.SCOPE_APPROVAL
            and intake_gate.status.value == "awaiting_user"
        ):
            repository.decide_gate(
                study_id,
                intake_gate.gate_id,
                approve=True,
                decided_by="profile_acceptance_owner",
                reason="Approve the factorial Idea-to-paper intake scope.",
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
        reason="Approve the prespecified randomized factorial scope.",
    )
    frozen_scope = scope.model_copy(
        update={"status": ArtifactStatus.FROZEN, "frozen_at": utc_now()}
    )
    repository.save_scope_contract(frozen_scope)
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

    rows = factorial_acceptance_rows()
    plan = AnalysisPlan.model_validate(factorial_acceptance_plan())
    dataset_path = study_root / "stage3" / "resources" / "factorial_observations.csv"
    _write_factorial_rows(dataset_path, rows)
    dataset_sha = sha256_file(dataset_path)
    task_spec_path = study_root / "stage3" / "resources" / "task_and_scoring_specification.json"
    task_spec = {
        "schema_version": 1,
        "fixture_role": "deterministic software-task factorial acceptance study",
        "scientific_boundary": (
            "The fixture validates the governed factorial workflow and estimator; "
            "it is not a human, clinical, field, or production intervention."
        ),
        "task": (
            "Each independently allocated task is processed once under one of four "
            "registered combinations of structured assistance and explanatory feedback."
        ),
        "quality_rule": (
            "50 + 2*A + 3*B + 4*A*B + balanced deterministic residual, where "
            "each low level is coded -0.5 and each high level is coded +0.5"
        ),
        "measurement": {
            "outcome": "continuous software-fixture task-quality points",
            "direction": "higher is better",
            "timing": "immediately after the single task execution",
            "range": "unbounded mathematical fixture; observed values are near 50",
        },
        "allocation": {
            "seed": 20260804,
            "cell_size": 20,
            "without_replacement": True,
        },
        "execution": {
            "network": "disabled",
            "invocations_per_task": 1,
            "preanalysis_rounding": "none",
        },
    }
    write_json_atomic(task_spec_path, task_spec)
    task_spec_sha = sha256_file(task_spec_path)
    allocation_path = study_root / "stage3" / "resources" / "allocation_ledger.json"
    write_json_atomic(
        allocation_path,
        {
            "schema_version": 1,
            "mechanism": "fixed-seed balanced random allocation without replacement",
            "seed": 20260804,
            "cell_counts": {
                cell.arm_id: sum(row["arm"] == cell.arm_id for row in rows)
                for cell in plan.factorial.cells
            },
            "assignments": [
                {
                    "subject_id": row["subject_id"],
                    "arm": row["arm"],
                    "assistance": row["assistance"],
                    "feedback": row["feedback"],
                }
                for row in rows
            ],
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
            "study_design": "factorial_experiment_v1",
            "known_limits": list(FactorialExperiment.descriptor.known_limits),
        },
        depends_on=[freeze_scope_step],
    )
    contract = ResearchContractVersion(
        study_id=study_id,
        version=1,
        scope_version=1,
        protocol_status=ProtocolStatus.FROZEN_EXECUTABLE,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary-interaction",
                statement=(
                    "The benefit of structured assistance on task quality is "
                    "larger under detailed than brief explanatory feedback in "
                    "the frozen randomized factorial population."
                ),
                role=HypothesisRole.PRIMARY,
                decision_rule={
                    "mode": "superiority",
                    "effect_measure": "factorial interaction",
                    "supported_if": (
                        "the two-sided interval is positive and the Holm-adjusted "
                        "p-value is at most 0.05"
                    ),
                },
            )
        ],
        data_boundary={
            "population": "80 frozen deterministic software tasks",
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "unit": "one independently allocated software task",
            "inclusion": "all eighty registered task identifiers",
            "exclusion": "none",
            "time_boundary": "one immediate post-execution observation per task",
            "synthetic_fixture_disclosure": True,
        },
        metrics=[
            {
                "name": "Task quality",
                "direction": "higher",
                "denominator": "all eighty registered tasks",
                "role": "primary",
                "scale": "continuous software-fixture points",
                "measurement_timing": "immediately after the single task execution",
            }
        ],
        baseline={
            "factor": "structured assistance",
            "low_level": "absent",
            "definition": "no structured assistance",
        },
        treatment={
            "factor": "structured assistance",
            "high_level": "present",
            "definition": "structured assistance is supplied",
        },
        tasks=["one deterministic execution for each independently allocated task"],
        seeds=[20260804],
        splits=["formal"],
        replicates=1,
        runtime_binding={
            "entrypoint": "research_forge.study_design.factorial_case",
            "network": "disabled",
        },
        evaluator_policy={
            "authority": "deterministic factorial evaluator",
            "independent_recalculation_required": True,
            "estimator": "effect-coded ordinary least squares",
            "covariance": "HC2 robust",
            "multiplicity_method": "Holm",
        },
        eligibility_rules=[
            {"rule": "each task belongs to exactly one factorial cell"},
            {"rule": "all four cells contain twenty independent tasks"},
            {"rule": "factor fields agree with the frozen cell mapping"},
        ],
        experiment_profile=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
        domain_execution_profile={"id": "controlled_factorial_fixture_v1", "version": "1"},
        study_design={"id": "factorial_experiment_v1", "version": "1"},
        inference_modules=[{"id": "multiplicity_control_v1", "version": "1"}],
        study_design_spec=plan.model_dump(mode="json"),
        output_schema={"type": "object", "required": ["outcomes", "primary_decision"]},
        statistical_rules={
            "confidence_level": 0.95,
            "primary_estimator": "effect-coded factorial interaction coefficient",
            "secondary_estimators": "effect-coded assistance and feedback main effects",
            "covariance": "HC2 robust covariance",
            "multiplicity": "Holm adjustment across all three registered contrasts",
            "success_rule": (
                "the interaction is supported only when its interval is positive "
                "and its Holm-adjusted p-value is at most 0.05"
            ),
        },
        estimand={
            "population": "all eighty randomized deterministic software tasks",
            "outcome": "continuous task-quality points",
            "contrast": (
                "difference in the assistance effect between detailed and brief "
                "feedback, with main effects averaged over the other factor"
            ),
            "unit": "quality points",
        },
        data_requirements={
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "minimum_independent_units_per_cell": 20,
        },
        implementation_requirements={
            "factor_coding": "low=-0.5 and high=+0.5",
            "quality_rule": task_spec["quality_rule"],
            "invocations_per_task": 1,
            "measurement_timing": "immediate post-execution",
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
        created_by="deterministic_factorial_contract_completion",
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
        reason="Approve the complete prespecified factorial contract.",
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
        study_id, str(dataset_path), dataset_sha,
        kind="study_design_dataset", role=ArtifactRole.OUTPUT,
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
    dry_rows = [
        row
        for cell in plan.factorial.cells
        for row in [item for item in rows if item["arm"] == cell.arm_id][:2]
    ]
    dry_plan = plan.model_copy(
        update={"factorial": plan.factorial.model_copy(update={"minimum_observed_per_cell": 2})}
    )
    dry_issues = FactorialExperiment().validate_realized_data(dry_plan, dry_rows)
    if dry_issues:
        raise ValueError("controlled factorial dry run failed: " + "; ".join(dry_issues))
    dry_step = _complete_step(
        repository,
        study_id,
        "study_design_dry_run",
        Phase.EXPERIMENT,
        ExecutorType.SANDBOX_RUNNER,
        {"evidence_eligible": False, "rows": len(dry_rows), "schema_valid": True},
        depends_on=[resource_step],
    )

    lock_ids: dict[str, str] = {}
    locks_root = study_root / "stage3" / "locks"
    for name, value in {
        "protocol": frozen_contract.model_dump(mode="json"),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset_sha,
            "task_and_scoring_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
        },
        "evaluator": {"implementation": "factorial_experiment_v1"},
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
    write_json_atomic(manifest_path, {"dataset_sha256": dataset_sha, "locks": lock_ids})
    manifest_sha = sha256_file(manifest_path)
    handoff_id = stable_id("stage3-handoff", study_id, dataset_sha, "factorial")
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
    cells = [
        RunCell(
            run_cell_id=stable_id("run-cell", plan_id, cell.arm_id),
            study_id=study_id,
            plan_id=plan_id,
            contract_version=1,
            task_id="registered_factorial_fixture",
            split_id="formal",
            arm_id=cell.arm_id,
            seed=20260804,
            replicate=1,
            action_id=f"action-{cell.arm_id}",
            experiment_id="factorial_acceptance_v1",
            expected_output_schema={"type": "object", "required": ["mean_quality"]},
            execution_manifest_hash=manifest_sha,
            status=RunCellStatus.SUCCEEDED,
            pair_position=position,
        )
        for position, cell in enumerate(plan.factorial.cells, start=1)
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
            run_id="run-factorial-formal-v1",
            study_id=study_id,
            contract_version=1,
            kind=RunKind.EXPERIMENTAL,
            status=ExecutionStatus.SUCCEEDED,
            output_artifact_ids=[dataset_artifact.artifact_id],
            completed_at=utc_now(),
        )
    )
    result_ids: list[str] = []
    code_hash = _digest(
        {"study_design": "factorial_experiment_v1", "analysis_plan": plan.model_dump(mode="json")}
    )
    environment_hash = _digest(
        {"python": "current locked Research Forge environment", "network": "disabled"}
    )
    for cell in cells:
        cell_rows = [row for row in rows if row["arm"] == cell.arm_id]
        mean_quality = sum(float(row["quality"]) for row in cell_rows) / len(cell_rows)
        output_path = study_root / "stage3" / "results" / f"{cell.arm_id}-result.json"
        write_json_atomic(
            output_path,
            {
                "arm_id": cell.arm_id,
                "registered_subject_count": len(cell_rows),
                "mean_quality": mean_quality,
                "subject_ids": [row["subject_id"] for row in cell_rows],
            },
        )
        artifact = repository.register_artifact(
            study_id, str(output_path), sha256_file(output_path),
            kind="factorial_cell_result", role=ArtifactRole.OUTPUT,
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
                metrics={"mean_quality": mean_quality},
                denominator=len(cell_rows),
                sample_ids=[row["subject_id"] for row in cell_rows],
                analysis_rows=cell_rows,
            )
        )
        result_ids.append(result.result_id)

    design = FactorialExperiment()
    evaluation = design.evaluate(plan, rows)
    reference = recalculate_factorial(plan, rows)
    if not evaluation.eligible or evaluation.primary_decision != reference["primary_decision"]:
        raise ValueError("factorial production and independent reference verdicts disagree")
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
    contrast_specs = {item.contrast_id: item for item in plan.factorial.contrasts}
    evaluation_records: list[EvaluationRecord] = []
    evidence_edges: list[EvidenceEdge] = []
    for outcome_result in evaluation.outcomes:
        contrast = contrast_specs[outcome_result.outcome_id]
        baseline_mean = float(outcome_result.arm_statistics["absent_brief"]["mean"])
        treatment_mean = float(outcome_result.arm_statistics["present_detailed"]["mean"])
        record = repository.save_evaluation_record(
            EvaluationRecord(
                evaluation_id=stable_id(
                    "evaluation", study_id, plan_id, outcome_result.outcome_id,
                    sha256_file(evaluation_path),
                ),
                study_id=study_id,
                plan_id=plan_id,
                contract_version=1,
                qualification_status=QualificationStatus.QUALIFIED,
                qualification_checks=evaluation.qualification_checks,
                metric_name=contrast.label,
                baseline_estimate=baseline_mean,
                treatment_estimate=treatment_mean,
                paired_effect=float(outcome_result.effect),
                pair_count=0,
                independent_unit_count=outcome_result.denominator,
                variance_unit="software task",
                aggregation_hierarchy=["software task", "factorial cell"],
                confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
                confidence_interval=outcome_result.confidence_interval,
                arm_estimates={
                    arm_id: float(stats["mean"])
                    for arm_id, stats in outcome_result.arm_statistics.items()
                },
                contrast_estimates={
                    outcome_result.outcome_id: {
                        "label": contrast.label,
                        "kind": contrast.kind,
                        "factor_ids": contrast.factor_ids,
                        "effect": outcome_result.effect,
                    }
                },
                statistical_rule={
                    "estimator": "effect-coded factorial OLS with HC2 covariance",
                    "multiplicity": "Holm",
                    "raw_p_value": outcome_result.raw_p_value,
                    "adjusted_p_value": outcome_result.adjusted_p_value,
                },
                decision=HypothesisVerdictStatus(outcome_result.decision),
                rationale=(
                    "The registered factorial contrast was evaluated under the "
                    "frozen robust estimator and multiplicity family."
                ),
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
                    created_by="factorial_experiment_v1",
                    qualification_status=QualificationStatus.QUALIFIED,
                )
            )
        )
    figure_bundle, figure_artifact = persist_profile_figure_data_bundle(
        repository,
        study_id,
        profile_id="factorial_experiment_v1",
        evaluation=evaluation,
        research_contract_spec=plan,
        formal_rows=rows,
        source_artifacts=[dataset_artifact, evaluation_artifact, reference_artifact],
        evaluation_records=evaluation_records,
    )
    if not figure_bundle.complete:
        raise ValueError("factorial FigureData bundle is incomplete")
    primary = next(
        item for item in evaluation.outcomes
        if contrast_specs[item.outcome_id].role == "primary"
    )
    claim_boundary = design.produce_claim_envelope(plan, evaluation)
    allowed_claim = (
        "In the frozen randomized software-task fixture, structured assistance "
        "and explanatory feedback interacted: the assistance-associated gain in "
        "task quality was larger with detailed than with brief feedback."
        if evaluation.primary_decision == "supported"
        else (
            "The registered randomized factorial comparison did not establish "
            "the prespecified assistance-by-feedback interaction."
        )
    )
    claim = repository.save_claim_envelope(
        ScientificClaimEnvelope(
            claim_envelope_id=stable_id(
                "claim-envelope", study_id, plan_id, evaluation.primary_decision
            ),
            study_id=study_id,
            plan_id=plan_id,
            allowed_claim=allowed_claim,
            population="80 frozen independently randomized deterministic software tasks",
            tasks=["one deterministic execution followed by immediate quality recording"],
            intervention="structured assistance present versus absent",
            comparator="brief versus detailed explanatory feedback in a complete factorial crossing",
            outcome="continuous software-fixture task-quality points",
            effect_estimate=float(primary.effect),
            interval=primary.confidence_interval,
            evidence_level=EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED,
            confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
            known_limitations=[
                "This deterministic software fixture is not a human, clinical, field, or production study.",
                "The task-quality scale has no validated external construct interpretation.",
                "Generalization is limited to the frozen factors, levels, allocation, and scoring rule.",
                "Only a balanced complete two-by-two design with independent units was evaluated.",
            ],
            maximum_claim_tier="randomized_factorial_interaction",
            publication_mode=PublicationMode.RESULTS_MANUSCRIPT,
            prohibited_generalizations=claim_boundary.prohibited_claims + [
                "effects on people or real-world performance",
                "higher-order interactions",
                "effects under repeated or clustered observations",
            ],
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
            implementation_version="factorial_experiment_v1",
            verified_checks={
                "dataset_hash": True,
                "complete_factorial_cells": True,
                "reference_agreement": True,
                "claim_boundary": True,
            },
        )
    )
    verdict_status = HypothesisVerdictStatus(evaluation.primary_decision)
    hypothesis_verdict = repository.save_hypothesis_verdict(
        HypothesisVerdict(
            verdict_id=stable_id(
                "hverdict", study_id, "hypothesis-primary-interaction", plan_id
            ),
            study_id=study_id,
            hypothesis_id="hypothesis-primary-interaction",
            status=verdict_status,
            evidence_chain_ids=[chain.chain_id],
            eligible_evidence=True,
            rationale=(
                "The frozen factorial interaction rule was applied to verified "
                "data and independently recalculated."
            ),
        )
    )
    study_verdict_status = StudyVerdictStatus(evaluation.primary_decision)
    study_verdict = repository.save_study_verdict(
        StudyVerdict(
            verdict_id=stable_id(
                "sverdict", study_id, plan_id, evaluation.primary_decision
            ),
            study_id=study_id,
            status=study_verdict_status,
            hypothesis_verdict_ids=[hypothesis_verdict.verdict_id],
            rationale="The Study verdict equals the registered primary interaction verdict.",
        )
    )
    completion = repository.save_stage3_completion(
        Stage3CompletionPackage(
            completion_id=stable_id(
                "stage3-completion", study_id, run_plan.plan_hash,
                evaluation.primary_decision,
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
        repository, study_id, "study_design_formal_evaluation", Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"evaluation_ids": [item.evaluation_id for item in evaluation_records], "run_id": run.run_id},
        depends_on=[dry_step],
    )
    reference_step = _complete_step(
        repository, study_id, "study_design_independent_recalculation", Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"artifact_id": reference_artifact.artifact_id, "agreement": True},
        depends_on=[formal_step],
    )
    _complete_step(
        repository, study_id, "study_design_scientific_verdict", Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"verdict_id": study_verdict.verdict_id, "status": study_verdict.status.value},
        depends_on=[reference_step],
    )
    repository.save_study(
        repository.load_study(study_id).model_copy(
            update={"phase": Phase.PAPER, "execution_status": ExecutionStatus.QUEUED}
        ),
        "factorial_stage3_completed",
    )
    handoff = build_stage_four_evidence_from_repository(repository, study_id)
    stage_four_steps = queue_stage_four_from_profile_handoff(repository, handoff)
    return {
        "study_id": study_id,
        "project_id": study.project_id,
        "workflow_repository": str(repository.root),
        "stage3_completion_id": completion.completion_id,
        "scientific_verdict": study_verdict.status.value,
        "stage_four_steps": len(stage_four_steps),
        "stage_four_queued": True,
    }


def run_factorial_workflow_acceptance(
    root: Path,
    *,
    approve_owner_gates: bool = True,
    decided_by: str = "automated factorial acceptance owner",
    max_cycles: int = 12,
) -> dict[str, Any]:
    """Run the canonical Stage 4 DAG after the formal factorial handoff."""

    from ..workflow_domain import GateStatus
    from ..workflow_scheduler import PersistentDAGScheduler, workflow_handlers

    prepared = prepare_factorial_workflow_acceptance(root)
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
                        "Approved for the canonical black-box factorial acceptance; "
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
    "prepare_factorial_workflow_acceptance",
    "run_factorial_workflow_acceptance",
]
