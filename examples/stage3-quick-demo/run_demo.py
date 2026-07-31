"""Run a small, real Stage 3 paired experiment in a few minutes.

The demo deliberately avoids network and model calls. It exercises the
build-from-blueprint path with platform materialization, real Docker smoke
tests, execution-package freeze, formal owner Gate, candidate/evaluator
container separation, deterministic evaluation, verdict, and evidence chain.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from research_forge.stage_three import (
    ensure_stage_three_dag,
    stage3_read_model,
)
from research_forge.stage_three_build import (
    create_experiment_build_plan,
    formal_execution_admission,
    freeze_generated_profile_v1_execution_package,
    materialize_generated_profile_v1_package,
    smoke_generated_profile_v1_package,
    stage3_build_admission,
)
from research_forge.storage import write_json_atomic, write_text_atomic
from research_forge.workflow_domain import (
    ArtifactStatus,
    EntryMode,
    EstimandSpecification,
    ExperimentBlueprint,
    GateType,
    Hypothesis,
    HypothesisRole,
    MVPFeasibilityReceipt,
    Phase,
    ResearchContractVersion,
    ScopeContractVersion,
    Stage3Profile,
    WorkflowRepository,
)
from research_forge.workflow_scheduler import (
    PersistentDAGScheduler,
    workflow_handlers,
)


BASELINE_SOURCE = """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
rows = [
    json.loads(line)
    for line in source.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    "".join(
        json.dumps(
            {
                "sample_id": row["sample_id"],
                "prediction": 0,
                "target_reference": row["target_reference"],
            },
            ensure_ascii=False,
        )
        + "\\n"
        for row in rows
    ),
    encoding="utf-8",
)
"""


TREATMENT_SOURCE = """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
rows = [
    json.loads(line)
    for line in source.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    "".join(
        json.dumps(
            {
                "sample_id": row["sample_id"],
                "prediction": int(row["features"]["signal"]),
                "target_reference": row["target_reference"],
            },
            ensure_ascii=False,
        )
        + "\\n"
        for row in rows
    ),
    encoding="utf-8",
)
"""


EVALUATOR_PROPOSAL_SOURCE = """\
# This proposal has no formal authority. Research Forge compiles and freezes
# the independent platform evaluator from the Research Contract.
raise SystemExit("use the frozen platform evaluator")
"""


def _freeze_scope_and_contract(
    repository: WorkflowRepository,
    study_id: str,
) -> None:
    scope = ScopeContractVersion(
        study_id=study_id,
        version=1,
        direction="Evaluate a deterministic signal-reading intervention",
        research_question=(
            "Does reading the registered signal feature improve accuracy "
            "over the constant-zero baseline?"
        ),
        scope_in=[
            "frozen synthetic binary classification rows",
            "paired baseline/treatment comparison",
        ],
        scope_out=[
            "claims about real-world generalization",
            "post-result metric selection",
        ],
        candidate_contribution=(
            "A compact demonstration of the complete Stage 3 evidence loop."
        ),
    )
    repository.save_scope_contract(scope)
    scope_gate = repository.create_gate(
        study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study_id}:scope-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study_id,
        scope_gate.gate_id,
        approve=True,
        decided_by="demo-owner",
        reason="Run the bounded local Stage 3 demonstration.",
    )
    repository.save_scope_contract(
        scope.model_copy(
            update={
                "status": ArtifactStatus.FROZEN,
                "frozen_at": datetime.now(UTC).isoformat(),
            }
        )
    )

    estimand = {
        "population": "the frozen six-row synthetic formal partition",
        "experimental_unit": "task-seed paired run",
        "pairing_key": ["task", "split", "seed", "replicate"],
        "outcome": "accuracy",
        "contrast": "treatment minus baseline",
        "aggregation_hierarchy": ["pair", "task", "study"],
        "weighting_policy": "equal weight per registered pair",
        "variance_unit": "registered pair",
    }
    data_requirements = {
        "formal_partition": "six frozen candidate rows",
        "target_partition": "separate frozen evaluator-only rows",
    }
    allowed_arm_delta = [
        "treatment reads the registered binary signal; baseline predicts zero"
    ]
    environment_requirements = {
        "runtime": "python-3.12",
        "network": "offline",
        "candidate_target_separation": True,
    }
    contract = ResearchContractVersion(
        study_id=study_id,
        version=1,
        scope_version=1,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-stage3-quick-demo-primary",
                statement=(
                    "The registered signal-reading treatment improves "
                    "accuracy by at least 0.40 over the constant-zero baseline."
                ),
                role=HypothesisRole.PRIMARY,
                decision_rule={"effect_threshold": 0.40},
            )
        ],
        data_boundary={
            "candidate_rows": 6,
            "formal_targets_hidden_from_candidate": True,
        },
        metrics=[
            {
                "name": "accuracy",
                "direction": "higher_is_better",
                "denominator": "all six frozen formal rows",
            }
        ],
        baseline={
            "name": "constant-zero baseline",
            "experiment_id": "baseline-v1",
            "action_id": "action-baseline",
        },
        treatment={
            "name": "registered signal reader",
            "experiment_id": "treatment-v1",
            "action_id": "action-treatment",
        },
        tasks=["slice-a", "slice-b"],
        splits=["formal"],
        seeds=[1, 2],
        replicates=1,
        concurrency=2,
        runtime_binding={"python": "container", "approved_resource_ids": []},
        evaluator_policy={
            "authority_order": ["deterministic_platform_evaluator"],
            "candidate_may_not_see_targets": True,
        },
        experiment_profile=(
            Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1
        ),
        output_schema={
            "format": "json",
            "raw_fields": [
                "sample_id",
                "prediction",
                "target_reference",
            ],
            "metric_field": "accuracy",
            "denominator_field": "denominator",
            "sample_id_field": "sample_ids",
        },
        statistical_rules={
            "method": "paired_mean_difference",
            "effect_threshold": 0.40,
            "missing_cell_policy": "inconclusive",
            "confidence_level": 0.95,
        },
        estimand=estimand,
        data_requirements=data_requirements,
        implementation_requirements={
            "baseline": "constant-zero predictor",
            "treatment": "registered signal reader",
            "allowed_arm_delta": allowed_arm_delta,
        },
        environment_requirements=environment_requirements,
        resource_policy={
            "routes_must_be_explicit": True,
            "routes": [
                {
                    "strategy": "generate",
                    "source": "frozen local demo specification",
                }
            ],
        },
        budget_security={
            "network": "disabled",
            "max_runtime_seconds": 300,
            "max_download_bytes": 0,
        },
    )
    repository.save_research_contract(contract)
    contract_gate = repository.create_gate(
        study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study_id}:research-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study_id,
        contract_gate.gate_id,
        approve=True,
        decided_by="demo-owner",
        reason="Freeze the quick-demo scientific specification.",
    )
    repository.save_research_contract(
        contract.model_copy(
            update={
                "status": ArtifactStatus.FROZEN,
                "frozen_at": datetime.now(UTC).isoformat(),
            }
        )
    )


def _blueprint_and_receipt(
    study_id: str,
) -> tuple[ExperimentBlueprint, MVPFeasibilityReceipt]:
    estimand = EstimandSpecification(
        population="the frozen six-row synthetic formal partition",
        experimental_unit="task-seed paired run",
        pairing_key=["task", "split", "seed", "replicate"],
        outcome="accuracy",
        contrast="treatment minus baseline",
        aggregation_hierarchy=["pair", "task", "study"],
        weighting_policy="equal weight per registered pair",
        variance_unit="registered pair",
    )
    blueprint = ExperimentBlueprint(
        blueprint_id="blueprint-a11ce00000000001",
        study_id=study_id,
        contract_version=1,
        profile=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        estimand=estimand,
        data_requirements={
            "formal_partition": "six frozen candidate rows",
            "target_partition": "separate frozen evaluator-only rows",
        },
        baseline_requirements={"definition": "constant-zero predictor"},
        treatment_requirements={"definition": "registered signal reader"},
        allowed_arm_delta=[
            "treatment reads the registered binary signal; baseline predicts zero"
        ],
        runner_requirements={
            "arms": ["baseline", "treatment"],
            "sample_level_predictions": True,
        },
        raw_output_fields=[
            "sample_id",
            "prediction",
            "target_reference",
        ],
        environment_requirements={
            "runtime": "python-3.12",
            "network": "offline",
            "candidate_target_separation": True,
        },
        resource_routes=[
            {
                "asset_type": "dataset",
                "strategy": "generate",
                "source": "frozen local demo specification",
                "license": "CC0-1.0",
            }
        ],
        smoke_test_requirements=[
            "both arms emit sample-level predictions",
            "candidate cannot see smoke or formal targets",
            "platform evaluator passes its golden vector",
        ],
        completion_criteria=[
            "all package files are content-addressed",
            "real Docker smoke passes",
            "formal candidate and target remain separated",
        ],
    )
    receipt = MVPFeasibilityReceipt(
        receipt_id="mvp-receipt-a11ce00000000001",
        study_id=study_id,
        contract_version=1,
        prototype_resource_ids=["synthetic-schema-probe"],
        smoke_case_ids=["smoke-negative", "smoke-positive"],
        metric_computable=True,
        schema_feasible=True,
        resource_feasible=True,
        reproducible_seed_probe=True,
        temporary_implementation_notes=[
            "MVP checks feasibility only and is excluded from formal evidence."
        ],
    )
    return blueprint, receipt


def _generated_package() -> dict[str, object]:
    formal_rows = [
        {
            "sample_id": f"formal-{index}",
            "features": {"signal": label},
            "target_reference": f"formal-target-{index}",
        }
        for index, label in enumerate([0, 1, 0, 1, 0, 1], 1)
    ]
    formal_targets = [
        {
            "target_reference": f"formal-target-{index}",
            "target": label,
        }
        for index, label in enumerate([0, 1, 0, 1, 0, 1], 1)
    ]
    smoke_rows = [
        {
            "sample_id": "smoke-1",
            "features": {"signal": 0},
            "target_reference": "smoke-target-1",
        },
        {
            "sample_id": "smoke-2",
            "features": {"signal": 1},
            "target_reference": "smoke-target-2",
        },
    ]
    smoke_targets = [
        {"target_reference": "smoke-target-1", "target": 0},
        {"target_reference": "smoke-target-2", "target": 1},
    ]

    def jsonl(rows: list[dict[str, object]]) -> str:
        return "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        )

    return {
        "schema_version": 1,
        "builder_plugin": "computational_paired_comparison_codex_v1",
        "files": [
            {
                "path": "data/formal-candidate.jsonl",
                "role": "dataset",
                "content": jsonl(formal_rows),
                "source_basis": ["frozen quick-demo generation rule"],
                "generated": True,
            },
            {
                "path": "data/formal-targets.jsonl",
                "role": "target_dataset",
                "content": jsonl(formal_targets),
                "source_basis": ["frozen quick-demo target rule"],
                "generated": True,
            },
            {
                "path": "data/smoke-candidate.jsonl",
                "role": "smoke_dataset",
                "content": jsonl(smoke_rows),
                "source_basis": ["development-only smoke rule"],
                "generated": True,
            },
            {
                "path": "data/smoke-targets.jsonl",
                "role": "smoke_target_dataset",
                "content": jsonl(smoke_targets),
                "source_basis": ["development-only target rule"],
                "generated": True,
            },
            {
                "path": "baseline.py",
                "role": "baseline",
                "content": BASELINE_SOURCE,
                "source_basis": ["frozen constant-zero baseline requirement"],
                "generated": True,
            },
            {
                "path": "treatment.py",
                "role": "treatment",
                "content": TREATMENT_SOURCE,
                "source_basis": ["frozen signal-reader treatment requirement"],
                "generated": True,
            },
            {
                "path": "evaluator_proposal.py",
                "role": "evaluator",
                "content": EVALUATOR_PROPOSAL_SOURCE,
                "source_basis": [
                    "proposal only; platform evaluator has formal authority"
                ],
                "generated": True,
            },
        ],
        "formal_data_path": "data/formal-candidate.jsonl",
        "formal_target_path": "data/formal-targets.jsonl",
        "smoke_data_path": "data/smoke-candidate.jsonl",
        "smoke_target_path": "data/smoke-targets.jsonl",
        "baseline_command": [
            "{python}",
            "baseline.py",
            "{data_file}",
            "{prediction_file}",
        ],
        "treatment_command": [
            "{python}",
            "treatment.py",
            "{data_file}",
            "{prediction_file}",
        ],
        "evaluator_command": [
            "{python}",
            "evaluator_proposal.py",
        ],
        "raw_prediction_path": "predictions.jsonl",
        "metric_output_path": "metrics.json",
        "expected_raw_fields": [
            "sample_id",
            "prediction",
            "target_reference",
        ],
        "expected_metric_fields": [
            "accuracy",
            "denominator",
            "sample_ids",
        ],
        "conformance_claims": [
            "Both arms use the same data, runtime, arguments, and evaluator.",
            "Only the frozen signal-reading intervention differs.",
        ],
        "declared_allowed_arm_delta": [
            "treatment reads the registered binary signal; baseline predicts zero"
        ],
        "unresolved_requirements": [],
    }


def run_demo(output_root: Path) -> dict[str, object]:
    started = time.perf_counter()
    output_root.mkdir(parents=True, exist_ok=False)
    project_root = output_root / "project_bundle"
    project_root.mkdir()
    repository = WorkflowRepository(output_root / "workflow")
    project = repository.create_project(
        "Stage 3 quick paired experiment",
        source_root=str(project_root),
    )
    study = repository.create_study(
        project.project_id,
        "Signal-reading paired comparison",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    _freeze_scope_and_contract(repository, study.study_id)
    repository.save_study(
        repository.load_study(study.study_id).model_copy(
            update={"phase": Phase.EXPERIMENT}
        ),
        "stage3_quick_demo_handoff",
    )

    blueprint, receipt = _blueprint_and_receipt(study.study_id)
    build_handoff, scientific_seal, capability = stage3_build_admission(
        repository,
        study.study_id,
        blueprint,
        receipt,
    )
    build_plan = create_experiment_build_plan(
        repository,
        study.study_id,
        build_handoff.handoff_id,
    )
    materialization = materialize_generated_profile_v1_package(
        repository,
        study.study_id,
        build_plan.build_plan_id,
        _generated_package(),
    )
    smoke = smoke_generated_profile_v1_package(
        repository,
        study.study_id,
        build_plan.build_plan_id,
    )
    execution_seal = freeze_generated_profile_v1_execution_package(
        repository,
        study.study_id,
        build_plan.build_plan_id,
    )
    formal_handoff = formal_execution_admission(
        repository,
        study.study_id,
        execution_seal.seal_id,
    )
    _, run_plan, _ = ensure_stage_three_dag(repository, study.study_id)
    scheduler = PersistentDAGScheduler(
        repository,
        workflow_handlers(),
        max_concurrency=run_plan.concurrency,
        recover_interrupted=True,
    )
    scheduler.run(study.study_id)
    run_gate = next(
        gate
        for gate in repository.list_gates(study.study_id)
        if gate.subject_type == "stage3_run_plan"
        and gate.subject_id == run_plan.plan_id
    )
    repository.decide_gate(
        study.study_id,
        run_gate.gate_id,
        approve=True,
        decided_by="demo-owner",
        reason="Execute the bounded eight-cell formal matrix.",
    )
    scheduler.run(study.study_id)

    view = stage3_read_model(repository, study.study_id)
    elapsed = time.perf_counter() - started
    evaluation = repository.list_evaluation_records(study.study_id)[-1]
    attempts = repository.list_execution_attempts(study.study_id)
    summary = {
        "schema_version": 1,
        "demo": "stage3_quick_paired_signal_experiment",
        "elapsed_seconds": round(elapsed, 3),
        "output_root": str(output_root.resolve()),
        "project_id": project.project_id,
        "study_id": study.study_id,
        "profile_capability": capability.value,
        "build_mode": build_handoff.build_mode.value,
        "scientific_specification_seal_id": scientific_seal.seal_id,
        "build_plan_id": build_plan.build_plan_id,
        "materialization_root": materialization["materialization_root"],
        "smoke": {
            "evidence_eligible": smoke["evidence_eligible"],
            "formal_data_mounted": smoke["formal_data_mounted"],
            "formal_targets_mounted": smoke["formal_targets_mounted"],
            "candidate_saw_any_targets": smoke[
                "candidate_saw_any_targets"
            ],
            "evaluator_golden_vector_passed": smoke[
                "evaluator_golden_vector_passed"
            ],
        },
        "execution_package_seal_id": execution_seal.seal_id,
        "formal_handoff_id": formal_handoff.handoff_id,
        "run_plan": {
            "plan_id": run_plan.plan_id,
            "cells": len(run_plan.cells),
            "concurrency": run_plan.concurrency,
        },
        "execution": {
            "attempts": len(attempts),
            "all_network_disabled": all(
                attempt.isolation_attestations.get("candidate", {}).get(
                    "network"
                )
                == "none"
                and attempt.isolation_attestations.get("evaluator", {}).get(
                    "network"
                )
                == "none"
                for attempt in attempts
            ),
            "candidate_targets_never_mounted": all(
                attempt.isolation_attestations.get(
                    "candidate_targets_mounted"
                )
                is False
                for attempt in attempts
            ),
        },
        "evaluation": evaluation.model_dump(mode="json"),
        "operational_state": view["status_spaces"]["operational_state"],
        "scientific_verdict": view["status_spaces"][
            "scientific_verdict"
        ],
        "evidence_verified": view["evidence"]["verified"],
        "phase_after_completion": repository.load_study(
            study.study_id
        ).phase.value,
        "completion": view["completion"],
    }
    write_json_atomic(output_root / "quick-demo-summary.json", summary)
    write_text_atomic(
        output_root / "quick-demo-report.txt",
        "\n".join(
            [
                "Research Forge Stage 3 quick experiment",
                "",
                f"Elapsed seconds: {summary['elapsed_seconds']}",
                f"Study: {study.study_id}",
                f"Build mode: {summary['build_mode']}",
                f"Run cells: {summary['run_plan']['cells']}",
                f"Execution attempts: {summary['execution']['attempts']}",
                (
                    "All candidate/evaluator networks disabled: "
                    f"{summary['execution']['all_network_disabled']}"
                ),
                (
                    "Candidate targets never mounted: "
                    f"{summary['execution']['candidate_targets_never_mounted']}"
                ),
                (
                    "Smoke evidence eligible: "
                    f"{summary['smoke']['evidence_eligible']}"
                ),
                (
                    "Baseline accuracy: "
                    f"{evaluation.baseline_estimate:.3f}"
                ),
                (
                    "Treatment accuracy: "
                    f"{evaluation.treatment_estimate:.3f}"
                ),
                (
                    "Paired effect: "
                    + (
                        f"{evaluation.paired_effect:+.3f}"
                        if evaluation.paired_effect is not None
                        else "unavailable"
                    )
                ),
                (
                    "Frozen effect threshold: "
                    f"{evaluation.statistical_rule['effect_threshold']:.3f}"
                ),
                f"Qualification: {evaluation.qualification_status.value}",
                f"Scientific verdict: {summary['scientific_verdict']}",
                f"Evidence verified: {summary['evidence_verified']}",
                (
                    "Phase after completion: "
                    f"{summary['phase_after_completion']}"
                ),
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(".tmp")
            / (
                "stage3-quick-demo-"
                + datetime.now().strftime("%Y%m%d-%H%M%S")
            )
        ),
    )
    args = parser.parse_args()
    summary = run_demo(args.output.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
