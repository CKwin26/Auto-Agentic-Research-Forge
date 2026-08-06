"""Canonical four-phase acceptance harness for a randomized survival Study.

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
from .designs.survival import SurvivalAnalysis
from .survival_case import (
    SURVIVAL_TITLE,
    survival_acceptance_plan,
    survival_acceptance_rows,
)
from .survival_reference import recalculate_survival
from .schemas import AnalysisPlan
from ..profile_figure_data import persist_profile_figure_data_bundle
from .workflow_acceptance import (
    _complete_step,
    _digest,
    _register_verified_methodology_literature,
)


def _write_survival_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["subject_id", "arm", "duration_hours", "failure_observed"],
        )
        writer.writeheader()
        writer.writerows(rows)


def prepare_survival_workflow_acceptance(output_root: str | Path) -> dict[str, Any]:
    """Create frozen Stage 1--3 objects and queue the canonical Stage 4 DAG."""

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    intake = initialize_idea_research(
        SURVIVAL_TITLE,
        title=SURVIVAL_TITLE,
        idea_root=root / "idea_runs",
    )
    repository = WorkflowRepository(intake["workflow_repository"])
    study_id = str(intake["study_id"])
    study = repository.load_study(study_id)
    if study.entry_mode is not EntryMode.IDEA_TO_PAPER:
        raise ValueError("survival acceptance must start from Idea-to-paper")
    repository.save_study(
        study.model_copy(
            update={"settings": {**study.settings, "manuscript_language": "en"}}
        ),
        "survival_acceptance_language_selected",
    )
    study_root = repository.root / "studies" / study_id

    scope = ScopeContractVersion(
        study_id=study_id,
        version=1,
        direction="Randomized survival evaluation of time to first failure",
        research_question=SURVIVAL_TITLE.removesuffix(
            " A Prespecified Randomized Survival Study"
        ),
        scope_in=[
            "two subject-level randomized arms",
            "one prespecified time-to-first-failure record per subject",
            "administrative right censoring with explicit status codes",
            "a treatment-minus-control difference in restricted mean event-free time through 12 hours",
        ],
        scope_out=[
            "left or interval censoring and recurrent events",
            "clustered allocation",
            "competing risks, proportional-hazards claims, covariate adjustment, and observational identification",
            "generalization beyond the deterministic acceptance fixture",
        ],
        candidate_contribution=(
            "A black-box demonstration that Research Forge can preserve the "
            "subject as the independent risk-set unit and report right-censored evidence through one governed "
            "idea-to-paper workflow."
        ),
        unit_of_analysis="independently randomized subject",
        study_design="two-arm randomized time-to-event survival experiment",
        population_or_corpus="48 frozen deterministic subject follow-up records",
        primary_outcome="time to first failure with administrative right censoring",
        comparison="resilience-intervention minus standard-configuration 12-hour RMST",
        feasibility_basis=["versioned local fixture", "independent recalculator"],
        created_by="survival_acceptance_designer",
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
                reason="Approve the survival Idea-to-paper intake scope.",
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
        reason="Approve the prespecified randomized survival scope.",
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

    rows = survival_acceptance_rows()
    plan = AnalysisPlan.model_validate(survival_acceptance_plan())
    dataset_path = study_root / "stage3" / "resources" / "survival_observations.csv"
    _write_survival_rows(dataset_path, rows)
    dataset_sha = sha256_file(dataset_path)
    task_spec_path = study_root / "stage3" / "resources" / "task_and_scoring_specification.json"
    task_spec = {
        "schema_version": 1,
        "fixture_role": "deterministic time-to-event survival acceptance study",
        "scientific_boundary": (
            "The fixture validates the governed survival workflow and estimator; "
            "it is not a human, clinical, field, or production intervention."
        ),
        "task": "Each subject is randomized once and followed from workload start until first failure or administrative censoring.",
        "event_rule": "failure_observed=1 denotes first failure; failure_observed=0 denotes verified administrative censoring.",
        "measurement": {
            "outcome": "hours from frozen workload start to first failure or censoring",
            "direction": "longer event-free time is better",
            "timing": "continuous follow-up truncated at the frozen 12-hour restriction time",
            "range": "positive durations no greater than 12 hours in the acceptance fixture",
        },
        "allocation": {
            "seed": 20260804,
            "subjects_per_arm": 24,
            "without_replacement": True,
        },
        "execution": {
            "network": "disabled",
            "records_per_subject": 1,
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
            "subject_counts": {
                arm.arm_id: len({row["subject_id"] for row in rows if row["arm"] == arm.arm_id})
                for arm in plan.arms
            },
            "assignments": [
                {
                    "subject_id": subject_id,
                    "arm": next(row["arm"] for row in rows if row["subject_id"] == subject_id),
                }
                for subject_id in sorted({row["subject_id"] for row in rows})
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
            "study_design": "survival_analysis_v1",
            "known_limits": list(SurvivalAnalysis.descriptor.known_limits),
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
                hypothesis_id="hypothesis-primary-rmst",
                statement=(
                    "The resilience intervention increases restricted mean event-free "
                    "time through 12 hours relative to the standard configuration."
                ),
                role=HypothesisRole.PRIMARY,
                decision_rule={
                    "mode": "superiority",
                    "effect_measure": "treatment-minus-control 12-hour RMST difference",
                    "supported_if": "the two-sided confidence interval lower bound exceeds zero",
                },
            )
        ],
        data_boundary={
            "population": "48 frozen deterministic randomized subject follow-up records",
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "unit": "one independently randomized subject",
            "inclusion": "all 48 registered subjects with positive duration and explicit event or censor status",
            "exclusion": "missing or invalid duration, event status, arm, or subject identifier",
            "time_boundary": "from frozen workload start through the 12-hour RMST restriction time",
            "synthetic_fixture_disclosure": True,
        },
        metrics=[
            {
                "name": "Restricted mean event-free time through 12 hours",
                "direction": "higher",
                "denominator": "eligible independently randomized subjects",
                "role": "primary",
                "scale": "hours",
                "measurement_timing": "Kaplan–Meier area from time zero through 12 hours",
            }
        ],
        baseline={
            "arm": "control",
            "definition": "standard resilience configuration",
        },
        treatment={
            "arm": "treatment",
            "definition": "resilience intervention intended to delay first failure",
        },
        tasks=["one frozen time-to-first-failure or censoring record for each randomized subject"],
        seeds=[20260804],
        splits=["formal"],
        replicates=1,
        runtime_binding={
            "entrypoint": "research_forge.study_design.survival_case",
            "network": "disabled",
        },
        evaluator_policy={
            "authority": "deterministic time-to-event evaluator",
            "independent_recalculation_required": True,
            "estimator": "Kaplan–Meier RMST by arm through 12 hours",
            "covariance": "arm-stratified subject-level nonparametric bootstrap",
            "multiplicity_method": "none; one confirmatory estimand",
        },
        eligibility_rules=[
            {"rule": "each subject belongs to exactly one randomized arm"},
            {"rule": "each eligible subject has one positive duration and one explicit event or censor code"},
            {"rule": "right-censored subjects remain in the risk set and are never recoded as failures"},
        ],
        experiment_profile=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
        domain_execution_profile={"id": "controlled_survival_fixture_v1", "version": "1"},
        study_design={"id": "survival_analysis_v1", "version": "1"},
        inference_modules=[],
        study_design_spec=plan.model_dump(mode="json"),
        output_schema={"type": "object", "required": ["outcomes", "primary_decision"]},
        statistical_rules={
            "confidence_level": 0.95,
            "primary_estimator": "resilience-intervention minus standard-configuration 12-hour RMST",
            "covariance": "arm-stratified subject-level bootstrap interval",
            "multiplicity": "none; one confirmatory RMST estimand",
            "success_rule": "the RMST difference is supported only when its two-sided interval is positive",
        },
        estimand={
            "population": "all eligible subjects in the frozen randomized fixture",
            "outcome": "time to first failure with administrative right censoring",
            "contrast": "resilience-intervention minus standard-configuration 12-hour RMST",
            "unit": "hours",
        },
        data_requirements={
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": task_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "minimum_independent_subjects_per_arm": 10,
            "minimum_observed_events_per_arm": 4,
        },
        implementation_requirements={
            "survival_estimator": "Kaplan–Meier restricted mean survival time",
            "event_rule": task_spec["event_rule"],
            "records_per_subject": 1,
            "measurement_timing": "time zero through the frozen 12-hour restriction time",
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
        created_by="deterministic_survival_contract_completion",
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
        reason="Approve the complete prespecified survival contract.",
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
    dry_subjects = {
        f"{arm.arm_id}-{index:02d}"
        for arm in plan.arms
        for index in range(3)
    }
    dry_rows = [row for row in rows if row["subject_id"] in dry_subjects]
    dry_plan = plan.model_copy(
        update={
            "survival": plan.survival.model_copy(
                update={"minimum_subjects_per_arm": 3}
            )
        }
    )
    dry_issues = SurvivalAnalysis().validate_realized_data(
        dry_plan, dry_rows
    )
    if dry_issues:
        raise ValueError("controlled survival dry run failed: " + "; ".join(dry_issues))
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
        "evaluator": {"implementation": "survival_analysis_v1"},
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
    handoff_id = stable_id("stage3-handoff", study_id, dataset_sha, "survival")
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
            run_cell_id=stable_id("run-cell", plan_id, arm.arm_id),
            study_id=study_id,
            plan_id=plan_id,
            contract_version=1,
            task_id="registered_survival_time_to_first_failure_fixture",
            split_id="formal",
            arm_id=arm.arm_id,
            seed=20260804,
            replicate=1,
            action_id=f"action-{arm.arm_id}",
            experiment_id="survival_acceptance_v1",
            expected_output_schema={"type": "object", "required": ["rmst"]},
            execution_manifest_hash=manifest_sha,
            status=RunCellStatus.SUCCEEDED,
            pair_position=position,
        )
        for position, arm in enumerate(plan.arms, start=1)
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
            run_id="run-survival-formal-v1",
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
        {"study_design": "survival_analysis_v1", "analysis_plan": plan.model_dump(mode="json")}
    )
    environment_hash = _digest(
        {"python": "current locked Research Forge environment", "network": "disabled"}
    )
    preliminary_reference = recalculate_survival(plan, rows)
    for cell in cells:
        cell_rows = [row for row in rows if row["arm"] == cell.arm_id]
        subject_ids = sorted({str(row["subject_id"]) for row in cell_rows})
        rmst = float(preliminary_reference["arm_rmst"][cell.arm_id])
        event_count = sum(int(row["failure_observed"] == 1) for row in cell_rows)
        output_path = study_root / "stage3" / "results" / f"{cell.arm_id}-result.json"
        write_json_atomic(
            output_path,
            {
                "arm_id": cell.arm_id,
                "registered_subject_count": len(subject_ids),
                "registered_event_count": event_count,
                "registered_censor_count": len(cell_rows) - event_count,
                "rmst": rmst,
                "subject_ids": subject_ids,
            },
        )
        artifact = repository.register_artifact(
            study_id, str(output_path), sha256_file(output_path),
            kind="survival_arm_result", role=ArtifactRole.OUTPUT,
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
                metrics={"rmst": rmst},
                denominator=len(subject_ids),
                sample_ids=subject_ids,
                analysis_rows=cell_rows,
            )
        )
        result_ids.append(result.result_id)

    design = SurvivalAnalysis()
    evaluation = design.evaluate(plan, rows)
    reference = recalculate_survival(plan, rows)
    if not evaluation.eligible or evaluation.primary_decision != reference["primary_decision"]:
        raise ValueError("survival production and independent reference verdicts disagree")
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
    evaluation_records: list[EvaluationRecord] = []
    evidence_edges: list[EvidenceEdge] = []
    for outcome_result in evaluation.outcomes:
        baseline_mean = float(outcome_result.arm_statistics["control"]["rmst"])
        treatment_mean = float(outcome_result.arm_statistics["treatment"]["rmst"])
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
                metric_name="Difference in restricted mean event-free time through 12 hours",
                baseline_estimate=baseline_mean,
                treatment_estimate=treatment_mean,
                paired_effect=float(outcome_result.effect),
                pair_count=0,
                independent_unit_count=outcome_result.denominator,
                variance_unit="subject",
                aggregation_hierarchy=["randomized subject", "risk set", "randomized arm"],
                confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
                confidence_interval=outcome_result.confidence_interval,
                arm_estimates={
                    arm_id: float(stats["rmst"])
                    for arm_id, stats in outcome_result.arm_statistics.items()
                },
                contrast_estimates={
                    outcome_result.outcome_id: {
                        "label": "Resilience-intervention minus standard-configuration 12-hour RMST",
                        "kind": "rmst_difference",
                        "effect": outcome_result.effect,
                    }
                },
                statistical_rule={
                    "estimator": "Kaplan–Meier RMST with arm-stratified subject bootstrap",
                    "restriction_time_hours": plan.survival.restriction_time,
                    "bootstrap_repetitions": plan.survival.bootstrap_repetitions,
                    "bootstrap_seed": plan.survival.bootstrap_seed,
                    "interval_algorithm": "two-sided percentile bootstrap interval",
                    "sampling_unit": "independently randomized subject within arm",
                    "control_subjects": int(outcome_result.arm_statistics["control"]["n_subjects"]),
                    "control_events": int(outcome_result.arm_statistics["control"]["events"]),
                    "control_censored": int(outcome_result.arm_statistics["control"]["censored"]),
                    "treatment_subjects": int(outcome_result.arm_statistics["treatment"]["n_subjects"]),
                    "treatment_events": int(outcome_result.arm_statistics["treatment"]["events"]),
                    "treatment_censored": int(outcome_result.arm_statistics["treatment"]["censored"]),
                    "multiplicity": "none; one confirmatory estimand",
                    "raw_p_value": outcome_result.raw_p_value,
                    "adjusted_p_value": outcome_result.adjusted_p_value,
                },
                decision=HypothesisVerdictStatus(outcome_result.decision),
                rationale=(
                    "The registered between-arm RMST difference was evaluated over "
                    "independent subjects under the frozen event and censoring rules."
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
                    created_by="survival_analysis_v1",
                    qualification_status=QualificationStatus.QUALIFIED,
                )
            )
        )
    figure_bundle, figure_artifact = persist_profile_figure_data_bundle(
        repository,
        study_id,
        profile_id="survival_analysis_v1",
        evaluation=evaluation,
        research_contract_spec=plan,
        formal_rows=rows,
        source_artifacts=[dataset_artifact, evaluation_artifact, reference_artifact],
        evaluation_records=evaluation_records,
    )
    if not figure_bundle.complete:
        raise ValueError("survival FigureData bundle is incomplete")
    primary = evaluation.outcomes[0]
    claim_boundary = design.produce_claim_envelope(plan, evaluation)
    allowed_claim = (
        "In the frozen randomized survival fixture, the resilience intervention "
        "increased restricted mean event-free time through 12 hours relative to the standard configuration."
        if evaluation.primary_decision == "supported"
        else (
            "The registered randomized survival comparison did not establish "
            "a positive difference in restricted mean event-free time through 12 hours."
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
            population="48 frozen independently randomized deterministic subject follow-up records",
            tasks=["one frozen time-to-first-failure or censoring record per subject"],
            intervention="resilience intervention",
            comparator="standard resilience configuration",
            outcome="restricted mean event-free time through 12 hours",
            effect_estimate=float(primary.effect),
            interval=primary.confidence_interval,
            evidence_level=EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED,
            confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
            known_limitations=[
                "This deterministic software fixture is not a human, clinical, field, or production study.",
                "Generalization is limited to the frozen arms, first-failure definition, time origin, and censoring rule.",
                "The estimator does not model competing risks, recurrent events, covariates, or informative censoring.",
            ],
            maximum_claim_tier="randomized_survival_rmst_difference",
            publication_mode=PublicationMode.RESULTS_MANUSCRIPT,
            prohibited_generalizations=claim_boundary.prohibited_claims + [
                "effects on people or real-world performance",
                "constant hazard ratios or effects beyond 12 hours",
                "effects under clustered allocation or informative censoring",
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
            implementation_version="survival_analysis_v1",
            verified_checks={
                "dataset_hash": True,
                "complete_survival_arms_and_risk_sets": True,
                "reference_agreement": True,
                "claim_boundary": True,
            },
        )
    )
    verdict_status = HypothesisVerdictStatus(evaluation.primary_decision)
    hypothesis_verdict = repository.save_hypothesis_verdict(
        HypothesisVerdict(
            verdict_id=stable_id(
                "hverdict", study_id, "hypothesis-primary-rmst", plan_id
            ),
            study_id=study_id,
            hypothesis_id="hypothesis-primary-rmst",
            status=verdict_status,
            evidence_chain_ids=[chain.chain_id],
            eligible_evidence=True,
            rationale=(
                "The frozen survival RMST-difference rule was applied to verified "
                "subject risk-set records and independently recalculated."
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
            rationale="The Study verdict equals the registered primary RMST-difference verdict.",
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
        "survival_stage3_completed",
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


def run_survival_workflow_acceptance(
    root: Path,
    *,
    approve_owner_gates: bool = True,
    decided_by: str = "automated survival acceptance owner",
    max_cycles: int = 12,
) -> dict[str, Any]:
    """Run the canonical Stage 4 DAG after the formal survival handoff."""

    from ..workflow_domain import GateStatus
    from ..workflow_scheduler import PersistentDAGScheduler, workflow_handlers

    prepared = prepare_survival_workflow_acceptance(root)
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
                        "Approved for the canonical black-box survival acceptance; "
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
    "prepare_survival_workflow_acceptance",
    "run_survival_workflow_acceptance",
]
