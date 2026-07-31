from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from research_forge.stage_three import (
    STAGE3_LOCK_NAMES,
    Stage3AdmissionError,
    apply_stage3_scientific_successor_contract,
    admit_stage_three,
    compile_run_plan,
    ensure_stage_three_dag,
    finalize_stage3_unverifiable_boundary,
    initialize_stage3_successor,
    propose_stage3_repair,
    stage3_read_model,
    stage4_claim_authority,
    _evaluate_results_handler,
)
from research_forge.stage_three_trust import (
    export_stage3_completion_package,
    run_stage3_backup_restore_drill,
    verify_stage3_completion_package,
)
from research_forge.stage_four import ensure_stage_four_dag
from research_forge.stage_three_build import (
    Stage3BuildAdmissionError,
    _approved_local_resource_manifest,
    create_experiment_build_plan,
    formal_execution_admission,
    freeze_generated_profile_v1_execution_package,
    freeze_ready_made_execution_package,
    generate_and_materialize_profile_v1,
    materialize_generated_profile_v1_package,
    record_stage3_build_failure,
    resolve_stage3_resource_routes,
    smoke_generated_profile_v1_package,
    stage3_build_admission,
    stage3_contract_readiness_violations,
)
from research_forge.retrieval.domain.models import (
    ExternalResource,
    MetadataVerificationStatus,
    NetworkMode,
    ResourceSet,
    ResourceSetStatus,
    ResourceSnapshot,
    ResourceType,
    ResourceUseBinding,
    RetrievalPhase,
    retrieval_id,
)
from research_forge.retrieval.interfaces.service import RetrievalGateway
from research_forge.retrieval.policy.engine import RetrievalNetworkPolicy
from research_forge.retrieval.providers.github import GitHubResearchAdapter
from research_forge.retrieval.providers.registry import ProviderRegistry
from research_forge.storage import read_json, sha256_file
from research_forge.workflow_scheduler import (
    PersistentDAGScheduler,
    workflow_handlers,
)
from research_forge.web_app import create_server
from research_forge.workflow_domain import (
    ArtifactRole,
    ArtifactStatus,
    BuildAssetStrategy,
    EntryMode,
    DiagnosticReport,
    GateType,
    EstimandSpecification,
    ExperimentBlueprint,
    ExecutionTrustLevel,
    Hypothesis,
    HypothesisRole,
    Phase,
    ProtocolStatus,
    MVPFeasibilityReceipt,
    ProfileCapabilityStatus,
    RunCellStatus,
    Stage3FailureClass,
    Stage3BuildMode,
    ResearchContractVersion,
    ResultEnvelope,
    ScopeContractVersion,
    Stage3Profile,
    WorkflowRepository,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _profile_repository(
    tmp_path: Path,
    *,
    profile: Stage3Profile | None = (
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1
    ),
    tasks: list[str] | None = None,
    metric_name: str = "accuracy",
    metric_direction: str = "higher_is_better",
    baseline_value: float = 0.65,
    treatment_delta: float = 0.10,
    approved_resource_ids: list[str] | None = None,
    variance_unit: str = "registered pair",
    profile_parameters: dict[str, object] | None = None,
    include_diagnostic_metric: bool = False,
    contract_schema_version: int = 1,
) -> tuple[WorkflowRepository, str, Path]:
    multi_arm = profile is Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1
    registered_arms = (
        ["backbone", "neutral_lora", "marxist_lora"]
        if multi_arm
        else ["baseline", "treatment"]
    )
    task_ids = tasks or ["task-b", "task-a"]
    source = tmp_path / "project"
    source.mkdir(parents=True)
    _write_json(source / "input.json", {"rows": [1, 2, 3]})
    runner = """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--arm", required=True)
parser.add_argument("--task", required=True)
parser.add_argument("--seed", type=int, required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
base = __BASELINE__
value = base + (
    __DELTA__
    if args.arm in {"treatment", "marxist_lora"}
    else 0.0
)
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "__METRIC__": value,
    "factual_accuracy": 0.8,
    "general_accuracy": 0.8,
    "patient_action_rate": (
        0.3 if args.arm in {"treatment", "marxist_lora"} else 0.9
    ),
    "denominator": 3,
    "sample_ids": ["row-1", "row-2", "row-3"],
    "analysis_rows": [
        {
            "sample_id": "row-1",
            "value": int(value >= 0.7) if "__BINARY__" == "yes" else value,
            "task_id": args.task
        },
        {
            "sample_id": "row-2",
            "value": int(value >= 0.7) if "__BINARY__" == "yes" else value,
            "task_id": args.task
        },
        {
            "sample_id": "row-3",
            "value": int(value >= 0.7) if "__BINARY__" == "yes" else value,
            "task_id": args.task
        }
    ]
}), encoding="utf-8")
""".strip()
    runner = (
        runner.replace("__BASELINE__", repr(baseline_value))
        .replace("__DELTA__", repr(treatment_delta))
        .replace("__METRIC__", metric_name)
        .replace(
            "__BINARY__",
            "yes"
            if profile
            in {
                Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
                Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
            }
            else "no",
        )
    )
    (source / "runner.py").write_text(
        runner + "\n",
        encoding="utf-8",
    )
    _write_json(
        source / "research-forge.experiments.json",
        {
            "schema_version": 1,
            "experiments": [
                {
                    "experiment_id": f"{arm_id}-v1",
                    "action_ids": [f"action-{arm_id}"],
                    "title": arm_id,
                    "command": [
                        "{python}",
                        "runner.py",
                        "--arm",
                        arm_id,
                        "--task",
                        "{task_id}",
                        "--seed",
                        "{seed}",
                        "--output",
                        "{metrics_file}",
                    ],
                    "required_inputs": ["input.json"],
                    "artifacts": [
                        {
                            "path": "metrics.json",
                            "format": "json",
                            "required_keys": [
                                metric_name,
                                "denominator",
                                "sample_ids",
                            ],
                        }
                    ],
                }
                for arm_id in registered_arms
            ],
        },
    )
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Profile v1",
        source_root=str(source),
    )
    study = repository.create_study(
        project.project_id,
        "Paired comparison",
        entry_mode=EntryMode.PROJECT_TO_PAPER,
    )
    scope = ScopeContractVersion(
        study_id=study.study_id,
        version=1,
        direction="Evaluate a declared treatment",
        research_question="Does treatment improve accuracy?",
        scope_in=["local paired comparison"],
        scope_out=["unregistered analyses"],
        candidate_contribution="A reproducible paired comparison.",
    )
    repository.save_scope_contract(scope)
    gate = repository.create_gate(
        study.study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study.study_id}:scope-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    repository.save_scope_contract(
        scope.model_copy(
            update={"status": ArtifactStatus.FROZEN, "frozen_at": "frozen"}
        )
    )
    contract = ResearchContractVersion(
        schema_version=contract_schema_version,
        protocol_status=(
            ProtocolStatus.FROZEN_EXECUTABLE
            if contract_schema_version >= 2
            else ProtocolStatus.DRAFT
        ),
        study_id=study.study_id,
        version=1,
        scope_version=1,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary-1",
                statement=f"Treatment changes {metric_name} by at least 0.05.",
                role=HypothesisRole.PRIMARY,
                decision_rule={"effect_threshold": 0.05},
            )
        ],
        data_boundary={"denominator": "all frozen rows"},
        metrics=[
            {
                "name": metric_name,
                "direction": metric_direction,
                "denominator": "all frozen rows",
            },
            *(
                [
                    {
                        "name": "factual_accuracy",
                        "direction": "maximize",
                        "denominator": "all frozen rows",
                        "noninferiority_tolerance": -0.05,
                    },
                    {
                        "name": "general_accuracy",
                        "direction": "maximize",
                        "denominator": "all frozen rows",
                        "noninferiority_tolerance": -0.05,
                    },
                    *(
                        [
                            {
                                "name": "patient_action_rate",
                                "direction": "descriptive",
                                "denominator": "all frozen rows",
                                "role": "diagnostic",
                            }
                        ]
                        if include_diagnostic_metric
                        else []
                    ),
                ]
                if multi_arm
                else []
            ),
        ],
        baseline={
            "name": "baseline",
            "experiment_id": "baseline-v1",
            "action_id": "action-baseline",
        },
        treatment={
            "name": "treatment",
            "experiment_id": (
                "marxist_lora-v1" if multi_arm else "treatment-v1"
            ),
            "action_id": (
                "action-marxist_lora"
                if multi_arm
                else "action-treatment"
            ),
        },
        tasks=task_ids,
        splits=["test"],
        seeds=[2, 1],
        replicates=2,
        concurrency=3,
        runtime_binding={
            "python": "test",
            "approved_resource_ids": approved_resource_ids or [],
        },
        evaluator_policy={"authority_order": ["deterministic"]},
        experiment_profile=profile,
        profile_parameters=profile_parameters or {},
        output_schema={
            "format": "json",
            "metric_field": metric_name,
            "denominator_field": "denominator",
            "sample_id_field": "sample_ids",
            **(
                {
                    "record_layout": "summary_with_analysis_rows",
                    "analysis_rows_field": "analysis_rows",
                }
                if profile
                in {
                    Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
                    Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
                    Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
                    Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1,
                }
                else {}
            ),
            **(
                {
                    "aggregate_fields": [
                        metric_name,
                        "factual_accuracy",
                        "general_accuracy",
                        *(
                            ["patient_action_rate"]
                            if include_diagnostic_metric
                            else []
                        ),
                        "denominator",
                    ]
                }
                if multi_arm
                else {}
            ),
        },
        statistical_rules={
            "method": {
                Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2: (
                    "cluster_bootstrap_paired_mean"
                ),
                Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1: (
                    "exact_mcnemar"
                ),
                Stage3Profile.PAIRED_BINARY_CLUSTERED_V1: (
                    "cluster_bootstrap_paired_binary"
                ),
                Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1: (
                    "cluster_bootstrap_multi_arm_conjunction"
                ),
            }.get(profile, "paired_mean_difference"),
            "effect_threshold": 0.05,
            "missing_cell_policy": "inconclusive",
        },
        estimand={
            "population": "frozen task and split matrix",
            "experimental_unit": "task-split-seed-replicate pair",
            "pairing_key": ["task", "split", "seed", "replicate"],
            "outcome": "accuracy",
            "contrast": "treatment minus baseline",
            "aggregation_hierarchy": ["pair", "task", "study"],
            "weighting_policy": "equal weight per registered pair",
            "variance_unit": variance_unit,
        },
        data_requirements={"partition": "formal evaluation"},
        implementation_requirements={
            "allowed_arm_delta": ["registered treatment intervention"],
            **(
                {
                    "arms": {
                        arm_id: {
                            "experiment_id": f"{arm_id}-v1",
                            "action_id": f"action-{arm_id}",
                        }
                        for arm_id in registered_arms
                    }
                }
                if multi_arm
                else {}
            ),
        },
        environment_requirements={"network": "offline"},
        resource_policy={"routes_must_be_explicit": True},
        budget_security={"max_download_bytes": 50_000_000},
    )
    repository.save_research_contract(contract)
    gate = repository.create_gate(
        study.study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study.study_id}:research-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    repository.save_research_contract(
        contract.model_copy(
            update={"status": ArtifactStatus.FROZEN, "frozen_at": "frozen"}
        )
    )
    stage2 = repository.root / "studies" / study.study_id / "stage2"
    for name in STAGE3_LOCK_NAMES:
        path = stage2 / name
        _write_json(path, {"name": name, "immutable": True})
        repository.register_artifact(
            study.study_id,
            str(path),
            sha256_file(path),
            kind=name.removesuffix(".json").replace(".", "_"),
            role=ArtifactRole.PROTOCOL,
        )
    repository.save_study(
        repository.load_study(study.study_id).model_copy(
            update={"phase": Phase.EXPERIMENT}
        ),
        "test_stage3_handoff",
    )
    return repository, study.study_id, source


def test_stage3_reports_stage2_contract_gaps_before_handoff(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    contract = repository.latest_research_contract(study_id)
    assert contract is not None
    incomplete = contract.model_copy(
        update={
            "experiment_profile": None,
            "tasks": [],
            "treatment": {
                "name": "treatment",
                "experiment_id": None,
                "action_id": None,
            },
            "statistical_rules": {
                "method": "paired_mean_difference",
            },
        }
    )
    issues = stage3_contract_readiness_violations(incomplete)
    assert any(item.startswith("CONTRACT_PROFILE_MISSING:") for item in issues)
    assert any(item.startswith("FORMAL_TASKS_MISSING:") for item in issues)
    assert any(item.startswith("TREATMENT_ACTION_MISSING:") for item in issues)
    assert any(
        item.startswith("NUMERIC_EFFECT_THRESHOLD_MISSING:")
        for item in issues
    )


def test_stage3_contract_blocker_creates_protocol_vnext(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    original = repository.latest_research_contract(study_id)
    assert original is not None

    record_stage3_build_failure(
        repository,
        study_id,
        "resolve_or_build_assets",
        Stage3BuildAdmissionError(
            [
                "generated package abstained because requirements are "
                "missing: TARGET_RULE_MISSING"
            ]
        ),
    )

    successor = repository.latest_research_contract(study_id)
    assert successor is not None
    assert successor.version == original.version + 1
    assert successor.predecessor_version == original.version
    assert successor.status is ArtifactStatus.DRAFT
    assert successor.protocol_status.value == "blocked"
    assert successor.unresolved_placeholders
    study = repository.load_study(study_id)
    assert study.phase is Phase.PROTOCOL
    assert original.status is ArtifactStatus.FROZEN


def test_profile_v1_compiles_deterministic_matrix(tmp_path: Path) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    handoff = admit_stage_three(repository, study_id)
    first = compile_run_plan(repository, study_id, handoff=handoff)
    second = compile_run_plan(repository, study_id, handoff=handoff)

    assert first.plan_id == second.plan_id
    assert first.plan_hash == second.plan_hash
    assert len(first.cells) == 16
    assert [item.task_id for item in first.cells[:8]] == ["task-a"] * 8
    assert {
        (item.arm_id, item.seed, item.replicate)
        for item in first.cells
        if item.task_id == "task-a"
    } == {
        ("baseline", 1, 1),
        ("baseline", 1, 2),
        ("baseline", 2, 1),
        ("baseline", 2, 2),
        ("treatment", 1, 1),
        ("treatment", 1, 2),
        ("treatment", 2, 1),
        ("treatment", 2, 2),
    }
    assert len({item.run_cell_id for item in first.cells}) == 16
    pair_blocks: dict[str, list[object]] = {}
    for cell in first.cells:
        pair_blocks.setdefault(str(cell.pair_block_id), []).append(cell)
    assert len(pair_blocks) == 8
    assert all(
        sorted(item.pair_position for item in block) == [1, 2]
        for block in pair_blocks.values()
    )
    assert all(
        next(item for item in block if item.pair_position == 2)
        .scheduled_after_run_cell_id
        == next(item for item in block if item.pair_position == 1).run_cell_id
        for block in pair_blocks.values()
    )


@pytest.mark.parametrize(
    ("profile", "parameters"),
    [
        (
            Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
            {
                "pairing_key": "sample_id",
                "cluster_id_field": "task_id",
                "variance_unit": "cluster",
                "inference_spec": {
                    "method": "cluster_bootstrap",
                    "resample_unit": "task_id",
                    "resamples": 1_000,
                    "seed": 20260726,
                },
            },
        ),
        (
            Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
            {
                "pairing_key": "sample_id",
                "success_definition": "registered correctness",
                "variance_unit": "pair",
            },
        ),
        (
            Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
            {
                "pairing_key": "sample_id",
                "cluster_id_field": "task_id",
                "success_definition": "registered correctness",
                "variance_unit": "cluster",
                "inference_spec": {
                    "method": "cluster_bootstrap",
                    "resample_unit": "task_id",
                    "resamples": 1_000,
                    "seed": 20260726,
                },
            },
        ),
    ],
)
def test_certified_modern_profiles_compile_and_evaluate_sample_rows(
    tmp_path: Path,
    profile: Stage3Profile,
    parameters: dict[str, object],
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path,
        profile=profile,
        profile_parameters=parameters,
    )
    handoff = admit_stage_three(repository, study_id)
    plan = compile_run_plan(repository, study_id, handoff=handoff)
    binary = profile in {
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
    }
    for index, cell in enumerate(plan.cells):
        value = (
            (1 if cell.arm_id == "treatment" else 0)
            if binary
            else (0.75 if cell.arm_id == "treatment" else 0.65)
        )
        repository.save_result_envelope(
            ResultEnvelope(
                result_id=f"result-{index:016x}",
                study_id=study_id,
                run_cell_id=cell.run_cell_id,
                attempt_id=f"attempt-{index:016x}",
                output_artifact_ids=[f"result-output-{index}"],
                metrics={"accuracy": float(value)},
                denominator=3,
                sample_ids=["row-1", "row-2", "row-3"],
                analysis_rows=[
                    {
                        "sample_id": sample_id,
                        "value": value,
                        "task_id": cell.task_id,
                    }
                    for sample_id in ("row-1", "row-2", "row-3")
                ],
            )
        )
    result = _evaluate_results_handler(
        SimpleNamespace(
            repository=repository,
            study_id=study_id,
            step=SimpleNamespace(task_group=f"stage3:{plan.plan_id}"),
        )
    )
    evaluation = next(
        item
        for item in repository.list_evaluation_records(study_id)
        if item.evaluation_id == result["evaluation"]["evaluation_id"]
    )

    assert evaluation.qualification_status.value == "qualified"
    assert evaluation.decision.value == "supported"
    assert evaluation.statistical_rule["profile_id"] == profile.value
    assert evaluation.pair_count == len(plan.cells) // 2 * 3


def test_multi_arm_profile_preserves_all_arms_and_requires_both_contrasts(
    tmp_path: Path,
) -> None:
    parameters = {
        "pairing_key": "sample_id",
        "cluster_id_field": "task_id",
        "variance_unit": "cluster",
        "inference_spec": {
            "method": "cluster_bootstrap",
            "resample_unit": "task_id",
            "resamples": 1_000,
            "seed": 20260727,
        },
        "arms": ["backbone", "neutral_lora", "marxist_lora"],
        "treatment_arm": "marxist_lora",
        "control_arms": ["backbone", "neutral_lora"],
        "decision_rule": "all_primary_contrasts",
    }
    repository, study_id, _ = _profile_repository(
        tmp_path,
        profile=Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1,
        tasks=["task-a", "task-b"],
        profile_parameters=parameters,
    )
    handoff = admit_stage_three(repository, study_id)
    plan = compile_run_plan(repository, study_id, handoff=handoff)

    assert len(plan.cells) == 24
    assert {cell.arm_id for cell in plan.cells} == {
        "backbone",
        "neutral_lora",
        "marxist_lora",
    }
    blocks: dict[str, list[object]] = {}
    for cell in plan.cells:
        blocks.setdefault(str(cell.pair_block_id), []).append(cell)
    assert len(blocks) == 8
    assert all(
        sorted(item.pair_position for item in block) == [1, 2, 3]
        for block in blocks.values()
    )

    arm_values = {
        "backbone": 0.60,
        "neutral_lora": 0.62,
        "marxist_lora": 0.75,
    }
    for index, cell in enumerate(plan.cells):
        value = arm_values[cell.arm_id]
        repository.save_result_envelope(
            ResultEnvelope(
                result_id=f"result-{index:016x}",
                study_id=study_id,
                run_cell_id=cell.run_cell_id,
                attempt_id=f"attempt-{index:016x}",
                output_artifact_ids=[f"result-output-{index}"],
                    metrics={
                        "accuracy": value,
                        "factual_accuracy": 0.8,
                        "general_accuracy": 0.8,
                    },
                denominator=3,
                sample_ids=["row-1", "row-2", "row-3"],
                analysis_rows=[
                    {
                        "sample_id": sample_id,
                        "value": value,
                        "task_id": cell.task_id,
                    }
                    for sample_id in ("row-1", "row-2", "row-3")
                ],
            )
        )
    result = _evaluate_results_handler(
        SimpleNamespace(
            repository=repository,
            study_id=study_id,
            step=SimpleNamespace(task_group=f"stage3:{plan.plan_id}"),
        )
    )
    evaluation = next(
        item
        for item in repository.list_evaluation_records(study_id)
        if item.evaluation_id == result["evaluation"]["evaluation_id"]
    )

    assert evaluation.qualification_status.value == "qualified"
    assert evaluation.decision.value == "supported"
    assert evaluation.arm_estimates == arm_values
    assert set(evaluation.contrast_estimates) == {
        "marxist_lora_minus_backbone",
        "marxist_lora_minus_neutral_lora",
    }
    assert evaluation.pair_count == 24


def _blueprint_and_receipt(
    study_id: str,
    *,
    resource_strategy: str = "reuse",
) -> tuple[ExperimentBlueprint, MVPFeasibilityReceipt]:
    blueprint = ExperimentBlueprint(
        blueprint_id="blueprint-0123456789abcdef",
        study_id=study_id,
        contract_version=1,
        profile=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        estimand=EstimandSpecification(
            population="frozen task and split matrix",
            experimental_unit="task-split-seed-replicate pair",
            pairing_key=["task", "split", "seed", "replicate"],
            outcome="accuracy",
            contrast="treatment minus baseline",
            aggregation_hierarchy=["pair", "task", "study"],
            weighting_policy="equal weight per registered pair",
            variance_unit="registered pair",
        ),
        data_requirements={"partition": "formal evaluation"},
        baseline_requirements={"definition": "declared baseline"},
        treatment_requirements={"definition": "declared treatment"},
        allowed_arm_delta=["registered treatment intervention"],
        runner_requirements={"arms": ["baseline", "treatment"]},
        raw_output_fields=[
            "sample_id",
            "prediction",
            "target_reference",
        ],
        environment_requirements={"network": "offline"},
        resource_routes=[
            {
                "strategy": resource_strategy,
                "source": "owner-approved resource route",
            }
        ],
        smoke_test_requirements=[
            "both arms emit schema-valid development output"
        ],
        completion_criteria=["formal resources can be content-addressed"],
    )
    receipt = MVPFeasibilityReceipt(
        receipt_id="mvp-receipt-0123456789abcdef",
        study_id=study_id,
        contract_version=1,
        prototype_resource_ids=["prototype-fixture"],
        smoke_case_ids=["smoke-1", "smoke-2"],
        metric_computable=True,
        schema_feasible=True,
        resource_feasible=True,
        reproducible_seed_probe=True,
    )
    return blueprint, receipt


def test_ready_made_build_handoff_is_separate_from_formal_execution(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    blueprint, receipt = _blueprint_and_receipt(study_id)

    handoff, seal, capability = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    assert handoff.handoff_stage == "build"
    assert handoff.build_mode is Stage3BuildMode.READY_MADE_EXPERIMENT
    assert handoff.lock_artifact_ids == {}
    assert handoff.execution_package_seal_id is None
    assert capability is ProfileCapabilityStatus.SUPPORTED
    assert seal.contract_version == 1
    assert plan.build_mode is Stage3BuildMode.READY_MADE_EXPERIMENT
    assert {
        item.strategy for item in plan.items
    }.issubset({BuildAssetStrategy.REUSE, BuildAssetStrategy.ADAPT})
    frozen_receipt = repository.load_mvp_feasibility_receipt(
        study_id, receipt.receipt_id
    )
    assert frozen_receipt.evidence_eligible is False
    assert frozen_receipt.formal_run_eligible is False


def test_blueprint_only_handoff_produces_profile_build_plan(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="retrieve"
    )

    handoff, _, capability = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    assert capability is ProfileCapabilityStatus.SUPPORTED_WITH_BUILD
    assert handoff.build_mode is Stage3BuildMode.BUILD_FROM_BLUEPRINT
    assert plan.capability_status is ProfileCapabilityStatus.SUPPORTED_WITH_BUILD
    strategies = {item.asset_type: item.strategy for item in plan.items}
    assert strategies["dataset"] is BuildAssetStrategy.RETRIEVE
    assert strategies["baseline"] is BuildAssetStrategy.IMPLEMENT
    assert strategies["treatment"] is BuildAssetStrategy.IMPLEMENT


def test_schema_v2_legacy_manifest_falls_back_to_blueprint_build(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path,
        contract_schema_version=2,
    )
    blueprint, receipt = _blueprint_and_receipt(study_id)

    handoff, _, capability = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    assert capability is ProfileCapabilityStatus.SUPPORTED_WITH_BUILD
    assert handoff.build_mode is Stage3BuildMode.BUILD_FROM_BLUEPRINT
    assert plan.build_mode is Stage3BuildMode.BUILD_FROM_BLUEPRINT


def _seed_frozen_profile_partitions(
    repository: WorkflowRepository,
    study_id: str,
    partitions: dict[str, str],
) -> dict[str, str]:
    gateway = RetrievalGateway(str(repository.root))
    study = repository.load_study(study_id)
    binding_ids: dict[str, str] = {}
    for index, (role, content) in enumerate(sorted(partitions.items()), 1):
        resource_id = f"resource-{role}"
        artifact = gateway.repository.write_binary_artifact(
            project_id=study.project_id,
            study_id=study_id,
            step_instance_id="step-" + f"{index:016x}",
            kind=f"{role}_partition",
            content=content.encode("utf-8"),
            producer="test_frozen_protocol_resource",
            extension="bin",
        )
        gateway.repository.save_resource(
            ExternalResource(
                resource_id=resource_id,
                resource_type=ResourceType.DATASET,
                canonical_identifier=f"dataset:{resource_id}:v1",
                title=f"Frozen {role} partition",
                dataset_identifier=f"{resource_id}@v1",
                license="CC-BY-4.0",
                providers=["test_protocol_import"],
                metadata_verification_status=(
                    MetadataVerificationStatus.VERIFIED
                ),
                canonical_metadata_hash=artifact.content_hash,
                metadata={"version": "v1"},
            )
        )
        snapshot = ResourceSnapshot(
            snapshot_id=f"snapshot-{role}",
            resource_id=resource_id,
            content_level="dataset_file",
            raw_response_artifact_id=artifact.artifact_id,
            normalized_content_artifact_id=artifact.artifact_id,
            provider="test_protocol_import",
            content_hash=artifact.content_hash,
            mime_type="application/x-ndjson",
            byte_size=len(content.encode("utf-8")),
            license_status="CC-BY-4.0",
            access_status="retrieved",
            access_mode="user_upload",
            model_processing_allowed=False,
        )
        gateway.repository.save_snapshot(snapshot)
        binding = ResourceUseBinding(
            binding_id=f"binding-{role}",
            resource_id=resource_id,
            snapshot_id=snapshot.snapshot_id,
            project_id=study.project_id,
            study_id=study_id,
            phase=RetrievalPhase.PROTOCOL,
            step_instance_id="step-" + f"{index:016x}",
            purpose="dataset_discovery",
            usage_role="contract_field_justification",
            target_type="research_contract",
            target_id=f"{study_id}:research-v1",
            target_field="runtime_binding",
            relation="supplies",
            verification_status=MetadataVerificationStatus.VERIFIED,
        )
        gateway.repository.save_binding(binding)
        binding_ids[role] = binding.binding_id
    gateway.repository.save_resource_set(
        ResourceSet(
            resource_set_id="resource-set-stage3-partitions",
            study_id=study_id,
            phase=RetrievalPhase.PROTOCOL,
            version=1,
            status=ResourceSetStatus.FROZEN,
            binding_ids=list(binding_ids.values()),
            query_plan_id="query-plan-stage3-partitions",
            coverage_report_id="coverage-stage3-partitions",
            content_hash=__import__("hashlib").sha256(
                "\n".join(sorted(binding_ids.values())).encode("utf-8")
            ).hexdigest(),
            frozen_at="2026-07-26T00:00:00+00:00",
        )
    )
    return binding_ids


def test_retrieved_profile_partitions_require_gateway_bindings_and_are_injected(
    tmp_path: Path,
) -> None:
    partitions = {
        "formal_candidate": (
            '{"sample_id":"f1","features":{"x":1},'
            '"target_reference":"formal-target-f1"}\n'
        ),
        "formal_target": (
            '{"target_reference":"formal-target-f1","target":1}\n'
        ),
        "smoke_candidate": (
            '{"sample_id":"s1","features":{"x":0},'
            '"target_reference":"smoke-target-s1"}\n'
        ),
        "smoke_target": (
            '{"target_reference":"smoke-target-s1","target":0}\n'
        ),
    }
    approved = [f"resource-{role}" for role in partitions]
    repository, study_id, source = _profile_repository(
        tmp_path, approved_resource_ids=approved
    )
    (source / "research-forge.experiments.json").unlink()
    binding_ids = _seed_frozen_profile_partitions(
        repository, study_id, partitions
    )
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="retrieve"
    )
    blueprint = blueprint.model_copy(
        update={
            "resource_routes": [
                {
                    "strategy": "retrieve",
                    "asset_role": role,
                    "binding_id": binding_ids[role],
                    "license": "CC-BY-4.0",
                }
                for role in sorted(partitions)
            ]
        }
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    resolution = resolve_stage3_resource_routes(
        repository, study_id, plan.build_plan_id
    )

    assert resolution["status"] == "resolved"
    assert resolution["open_ended_search_performed"] is False
    assert {
        item["asset_role"] for item in resolution["resource_bindings"]
    } == set(partitions)
    assert all(
        item["experimentation_binding_id"] != item["source_binding_id"]
        for item in resolution["resource_bindings"]
    )

    generated = {
        "files": [
            {
                "path": "data/formal.jsonl",
                "role": "dataset",
                "content": (
                    '{"sample_id":"placeholder-f","target_reference":"pf"}\n'
                ),
                "source_basis": ["retrieval placeholder"],
                "generated": True,
            },
            {
                "path": "data/formal-targets.jsonl",
                "role": "target_dataset",
                "content": '{"target_reference":"pf","target":0}\n',
                "source_basis": ["retrieval placeholder"],
                "generated": True,
            },
            {
                "path": "data/smoke.jsonl",
                "role": "smoke_dataset",
                "content": (
                    '{"sample_id":"placeholder-s","target_reference":"ps"}\n'
                ),
                "source_basis": ["retrieval placeholder"],
                "generated": True,
            },
            {
                "path": "data/smoke-targets.jsonl",
                "role": "smoke_target_dataset",
                "content": '{"target_reference":"ps","target":0}\n',
                "source_basis": ["retrieval placeholder"],
                "generated": True,
            },
            *[
                {
                    "path": f"{role}.py",
                    "role": role,
                    "content": "print('generated')\n",
                    "source_basis": [f"frozen {role} requirement"],
                    "generated": True,
                }
                for role in ("baseline", "treatment", "evaluator")
            ],
        ],
        "formal_data_path": "data/formal.jsonl",
        "formal_target_path": "data/formal-targets.jsonl",
        "smoke_data_path": "data/smoke.jsonl",
        "smoke_target_path": "data/smoke-targets.jsonl",
        "baseline_command": [
            "{python}", "baseline.py", "{data_file}", "{prediction_file}"
        ],
        "treatment_command": [
            "{python}", "treatment.py", "{data_file}", "{prediction_file}"
        ],
        "evaluator_command": ["{python}", "evaluator.py"],
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
        "conformance_claims": ["Only the registered arm delta differs."],
        "declared_allowed_arm_delta": [
            "registered treatment intervention"
        ],
    }
    materialized = materialize_generated_profile_v1_package(
        repository, study_id, plan.build_plan_id, generated
    )
    root = Path(materialized["materialization_root"])
    assert (root / "data" / "formal.jsonl").read_text(
        encoding="utf-8"
    ) == partitions["formal_candidate"]
    assert (root / "data" / "formal-targets.jsonl").read_text(
        encoding="utf-8"
    ) == partitions["formal_target"]
    assert materialized[
        "retrieved_partitions_injected_after_model_generation"
    ] is True
    assert len(materialized["retrieval_binding_ids"]) == 4


def test_retrieve_route_without_frozen_resources_is_resource_blocked(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="retrieve"
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    with pytest.raises(Stage3BuildAdmissionError, match="RESOURCE_BLOCKED"):
        resolve_stage3_resource_routes(
            repository, study_id, plan.build_plan_id
        )

    blocked = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "resolve_external_resources"
    )
    assert blocked.status.value == "blocked"
    assert blocked.blocker["kind"] == "resource_blocked"
    assert blocked.blocker["scientific_verdict_changed"] is False
    view = stage3_read_model(repository, study_id)
    assert view["status_spaces"]["operational_state"] == "resource_blocked"
    assert view["status_spaces"]["scientific_verdict"] is None


def _seed_frozen_code_source(
    repository: WorkflowRepository,
    study_id: str,
) -> str:
    gateway = RetrievalGateway(str(repository.root))
    study = repository.load_study(study_id)
    content = b"def baseline(row):\n    return 0\n"
    artifact = gateway.repository.write_binary_artifact(
        project_id=study.project_id,
        study_id=study_id,
        step_instance_id="step-" + "c" * 16,
        kind="baseline_source_archive",
        content=content,
        producer="test_frozen_protocol_resource",
        extension="bin",
    )
    resource_id = "resource-baseline-source"
    gateway.repository.save_resource(
        ExternalResource(
            resource_id=resource_id,
            resource_type=ResourceType.CODE_RELEASE,
            canonical_identifier="code:baseline:v1",
            title="Pinned baseline implementation",
            repository="https://example.invalid/baseline",
            commit="1" * 40,
            license="Apache-2.0",
            providers=["test_protocol_import"],
            metadata_verification_status=MetadataVerificationStatus.VERIFIED,
            canonical_metadata_hash=artifact.content_hash,
            metadata={"version": "v1"},
        )
    )
    snapshot = ResourceSnapshot(
        snapshot_id="snapshot-baseline-source",
        resource_id=resource_id,
        content_level="source_archive",
        raw_response_artifact_id=artifact.artifact_id,
        normalized_content_artifact_id=artifact.artifact_id,
        provider="test_protocol_import",
        content_hash=artifact.content_hash,
        mime_type="text/x-python",
        byte_size=len(content),
        license_status="Apache-2.0",
        access_status="retrieved",
        access_mode="user_upload",
        model_processing_allowed=True,
    )
    gateway.repository.save_snapshot(snapshot)
    binding = ResourceUseBinding(
        binding_id="binding-baseline-source",
        resource_id=resource_id,
        snapshot_id=snapshot.snapshot_id,
        project_id=study.project_id,
        study_id=study_id,
        phase=RetrievalPhase.PROTOCOL,
        step_instance_id="step-" + "c" * 16,
        purpose="baseline_discovery",
        usage_role="contract_field_justification",
        target_type="research_contract",
        target_id=f"{study_id}:research-v1",
        target_field="runtime_binding",
        relation="implements",
        verification_status=MetadataVerificationStatus.VERIFIED,
    )
    gateway.repository.save_binding(binding)
    gateway.repository.save_resource_set(
        ResourceSet(
            resource_set_id="resource-set-stage3-code",
            study_id=study_id,
            phase=RetrievalPhase.PROTOCOL,
            version=1,
            status=ResourceSetStatus.FROZEN,
            binding_ids=[binding.binding_id],
            query_plan_id="query-plan-stage3-code",
            coverage_report_id="coverage-stage3-code",
            content_hash=__import__("hashlib").sha256(
                binding.binding_id.encode("utf-8")
            ).hexdigest(),
            frozen_at="2026-07-26T00:00:00+00:00",
        )
    )
    return binding.binding_id


def test_stage3_online_code_acquisition_uses_retrieval_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, study_id, source = _profile_repository(
        tmp_path, approved_resource_ids=["resource-online-code"]
    )
    (source / "research-forge.experiments.json").unlink()
    study = repository.load_study(study_id)
    sha = "e" * 40
    archive = b"PK\x03\x04online-pinned-source"

    def content_transport(request, *, max_bytes: int, url_validator):
        url_validator(request.full_url)
        assert max_bytes == 200_000
        final_url = f"https://codeload.github.com/org/online/zip/{sha}"
        url_validator(final_url)
        return archive, final_url, "application/zip", {}

    gateway = RetrievalGateway(
        str(repository.root),
        providers=ProviderRegistry(
            [
                GitHubResearchAdapter(
                    transport=lambda _request: {},
                    content_transport=content_transport,
                )
            ]
        ),
    )
    gateway.set_policy(
        study.project_id,
        RetrievalNetworkPolicy(
            policy_id=retrieval_id(
                "network-policy", study.project_id, "stage3-online-code"
            ),
            mode=NetworkMode.PUBLIC_RESEARCH,
            allowed_providers={"github"},
            allowed_domains={"api.github.com"},
            allowed_http_methods={"GET"},
            allowed_resource_types={
                ResourceType.CODE_REPOSITORY,
                ResourceType.CODE_RELEASE,
            },
            allow_repository_download=True,
            max_queries=1,
            max_results=1,
            max_bytes=200_000,
            approved_by="project_owner",
            approved_at="2026-07-26T00:00:00+00:00",
        ),
    )
    metadata_artifact = gateway.repository.write_artifact(
        project_id=study.project_id,
        study_id=study_id,
        step_instance_id="step-" + "e" * 16,
        kind="repository_metadata",
        value={"repository": "org/online", "commit": sha},
        producer="protocol_retrieval",
    )
    resource = ExternalResource(
        resource_id="resource-online-code",
        resource_type=ResourceType.CODE_RELEASE,
        canonical_identifier=f"github:org/online@{sha}",
        title="Online pinned code",
        repository="org/online",
        commit=sha,
        license="MIT",
        providers=["github"],
        metadata_verification_status=MetadataVerificationStatus.VERIFIED,
        canonical_metadata_hash=metadata_artifact.content_hash,
    )
    gateway.repository.save_resource(resource)
    metadata_snapshot = ResourceSnapshot(
        snapshot_id="snapshot-online-code-metadata",
        resource_id=resource.resource_id,
        content_level="repository_metadata",
        raw_response_artifact_id=metadata_artifact.artifact_id,
        normalized_content_artifact_id=metadata_artifact.artifact_id,
        provider="github",
        content_hash=metadata_artifact.content_hash,
        mime_type="application/json",
        byte_size=Path(metadata_artifact.path).stat().st_size,
        license_status="MIT",
        access_status="metadata_only",
        access_mode="metadata_only",
        model_processing_allowed=False,
    )
    gateway.repository.save_snapshot(metadata_snapshot)
    binding = ResourceUseBinding(
        binding_id="binding-online-code",
        resource_id=resource.resource_id,
        snapshot_id=metadata_snapshot.snapshot_id,
        project_id=study.project_id,
        study_id=study_id,
        phase=RetrievalPhase.PROTOCOL,
        step_instance_id="step-" + "e" * 16,
        purpose="implementation_reference",
        usage_role="contract_field_justification",
        target_type="research_contract",
        target_id=f"{study_id}:research-v1",
        target_field="runtime_binding",
        relation="implements",
        verification_status=MetadataVerificationStatus.VERIFIED,
    )
    gateway.repository.save_binding(binding)
    gateway.repository.save_resource_set(
        ResourceSet(
            resource_set_id="resource-set-online-code",
            study_id=study_id,
            phase=RetrievalPhase.PROTOCOL,
            version=1,
            status=ResourceSetStatus.FROZEN,
            binding_ids=[binding.binding_id],
            query_plan_id="query-plan-online-code",
            coverage_report_id="coverage-online-code",
            content_hash=__import__("hashlib").sha256(
                binding.binding_id.encode("utf-8")
            ).hexdigest(),
            frozen_at="2026-07-26T00:00:00+00:00",
        )
    )
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )
    blueprint = blueprint.model_copy(
        update={
            "resource_routes": [
                {"strategy": "generate", "asset_type": "dataset"},
                {
                    "strategy": "retrieve",
                    "asset_type": "baseline",
                    "asset_role": "baseline_source",
                    "binding_id": binding.binding_id,
                    "license": "MIT",
                    "source_format": "zip",
                    "acquire_online": True,
                    "max_download_bytes": 200_000,
                },
            ]
        }
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )
    monkeypatch.setattr(
        "research_forge.retrieval.interfaces.service.RetrievalGateway",
        lambda *_args, **_kwargs: gateway,
    )

    resolution = resolve_stage3_resource_routes(
        repository, study_id, plan.build_plan_id
    )

    resolved = resolution["resource_bindings"][0]
    assert resolved["source_binding_id"] == binding.binding_id
    assert resolved["snapshot_id"] != binding.snapshot_id
    assert gateway.repository.load_snapshot(
        resolved["snapshot_id"]
    ).content_level == "source_archive"
    assert gateway.repository.load_artifact(
        resolved["retrieval_artifact_id"]
    ).producer == "provider:github"


def test_pinned_third_party_code_is_read_only_input_and_output_is_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, study_id, source = _profile_repository(
        tmp_path, approved_resource_ids=["resource-baseline-source"]
    )
    (source / "research-forge.experiments.json").unlink()
    binding_id = _seed_frozen_code_source(repository, study_id)
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )
    blueprint = blueprint.model_copy(
        update={
            "resource_routes": [
                {"strategy": "generate", "asset_type": "dataset"},
                {
                    "strategy": "retrieve",
                    "asset_type": "baseline",
                    "asset_role": "baseline_source",
                    "binding_id": binding_id,
                    "license": "Apache-2.0",
                    "source_format": "python",
                },
            ]
        }
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )
    assert {
        item.asset_type: item.strategy for item in plan.items
    }["baseline"] is BuildAssetStrategy.RETRIEVE
    observed: dict[str, object] = {}

    async def fake_builder(
        prompt: str, *, cwd: str | Path | None = None
    ) -> dict[str, object]:
        payload = json.loads(prompt)
        record = next(
            item
            for item in payload["resolved_experiment_resources"][
                "resource_bindings"
            ]
            if item["asset_role"] == "baseline_source"
        )
        root = Path(str(cwd))
        observed["cwd"] = root
        observed["source"] = (
            root / "baseline_source" / "source.py"
        ).read_text(encoding="utf-8")
        basis = [
            f"retrieval_binding:{record['experimentation_binding_id']}",
            f"snapshot:{record['snapshot_id']}",
        ]
        return {
            "files": [
                {
                    "path": "data/formal.jsonl",
                    "role": "dataset",
                    "content": (
                        '{"sample_id":"f1","target_reference":"tf1"}\n'
                    ),
                    "source_basis": ["frozen generation rule"],
                    "generated": True,
                },
                {
                    "path": "data/formal-targets.jsonl",
                    "role": "target_dataset",
                    "content": '{"target_reference":"tf1","target":1}\n',
                    "source_basis": ["frozen target rule"],
                    "generated": True,
                },
                {
                    "path": "data/smoke.jsonl",
                    "role": "smoke_dataset",
                    "content": (
                        '{"sample_id":"s1","target_reference":"ts1"}\n'
                    ),
                    "source_basis": ["smoke generation rule"],
                    "generated": True,
                },
                {
                    "path": "data/smoke-targets.jsonl",
                    "role": "smoke_target_dataset",
                    "content": '{"target_reference":"ts1","target":0}\n',
                    "source_basis": ["smoke target rule"],
                    "generated": True,
                },
                {
                    "path": "baseline.py",
                    "role": "baseline",
                    "content": "print('adapted')\n",
                    "source_basis": basis,
                    "generated": True,
                },
                {
                    "path": "treatment.py",
                    "role": "treatment",
                    "content": "print('treatment')\n",
                    "source_basis": ["frozen treatment requirement"],
                    "generated": True,
                },
                {
                    "path": "evaluator.py",
                    "role": "evaluator",
                    "content": "print('proposal only')\n",
                    "source_basis": ["frozen evaluator requirement"],
                    "generated": True,
                },
            ],
            "formal_data_path": "data/formal.jsonl",
            "formal_target_path": "data/formal-targets.jsonl",
            "smoke_data_path": "data/smoke.jsonl",
            "smoke_target_path": "data/smoke-targets.jsonl",
            "baseline_command": [
                "{python}", "baseline.py", "{data_file}", "{prediction_file}"
            ],
            "treatment_command": [
                "{python}", "treatment.py", "{data_file}", "{prediction_file}"
            ],
            "evaluator_command": ["{python}", "evaluator.py"],
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
            "conformance_claims": ["Only the registered arm delta differs."],
            "declared_allowed_arm_delta": [
                "registered treatment intervention"
            ],
        }

    monkeypatch.setattr(
        "research_forge.agent_runtime.generate_stage3_profile_v1_package",
        fake_builder,
    )

    materialized = asyncio.run(
        generate_and_materialize_profile_v1(
            repository,
            study_id,
            plan.build_plan_id,
            package_generator=fake_builder,
        )
    )

    assert "def baseline" in str(observed["source"])
    assert materialized["retrieved_code_adapted_without_raw_execution"] is True
    assert materialized["isolated_execution_required"] is True
    builder_manifest = read_json(
        Path(str(observed["cwd"])) / "builder_input_manifest.json"
    )
    assert builder_manifest["raw_source_executed"] is False
    assert builder_manifest["network_access_authorized"] is False


def test_blueprint_build_does_not_require_a_local_project_bundle(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    study = repository.load_study(study_id)
    project = repository.load_project(study.project_id)
    repository.save_project(project.model_copy(update={"source_root": None}))
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )

    handoff, _, capability = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    assert capability is ProfileCapabilityStatus.SUPPORTED_WITH_BUILD
    assert handoff.build_mode is Stage3BuildMode.BUILD_FROM_BLUEPRINT
    assert plan.build_mode is Stage3BuildMode.BUILD_FROM_BLUEPRINT


def test_stage3_resolves_approved_local_resource_ids_to_verified_paths(
    tmp_path: Path,
) -> None:
    resource_id = "resource-local-input"
    repository, study_id, source = _profile_repository(
        tmp_path,
        approved_resource_ids=[resource_id],
    )
    selection_path = (
        repository.root / "studies" / study_id / "stage2"
        / "resource_selection.json"
    )
    _write_json(
        selection_path,
        {
            "candidates": [
                {
                    "resource_id": resource_id,
                    "need_type": "dataset",
                    "source_kind": "local_project",
                    "canonical_identifier": "input.json",
                    "content_hash": sha256_file(source / "input.json"),
                }
            ]
        },
    )
    contract = repository.latest_research_contract(study_id)
    assert contract is not None

    manifest = _approved_local_resource_manifest(
        repository,
        study_id,
        contract,
    )

    assert manifest == [
        {
            "resource_id": resource_id,
            "need_type": "dataset",
            "relative_path": "input.json",
            "sha256": sha256_file(source / "input.json"),
            "size_bytes": (source / "input.json").stat().st_size,
            "read_only": True,
        }
    ]


def test_blueprint_generation_has_a_bounded_model_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(
        "research_forge.agent_runtime.generate_stage3_profile_v1_package",
        never_finishes,
    )
    monkeypatch.setattr(
        "research_forge.stage_three_build._PROFILE_V1_GENERATION_TIMEOUT_SECONDS",
        0.01,
    )

    with pytest.raises(Stage3BuildAdmissionError, match="attempt limit"):
        asyncio.run(
            generate_and_materialize_profile_v1(
                repository,
                study_id,
                plan.build_plan_id,
                package_generator=never_finishes,
            )
        )


def test_stage3_model_timeout_is_a_distinct_actionable_blocker(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    record_stage3_build_failure(
        repository,
        study_id,
        "resolve_or_build_assets",
        Stage3BuildAdmissionError(
            [
                "Codex experiment-asset generation exceeded the bounded "
                "15-minute attempt limit."
            ]
        ),
    )

    step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "resolve_or_build_assets"
    )
    assert step.status.value == "blocked"
    assert step.blocker["kind"] == "model_timeout"


def test_stage3_builder_abstention_routes_to_contract_revision(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    record_stage3_build_failure(
        repository,
        study_id,
        "resolve_or_build_assets",
        Stage3BuildAdmissionError(
            [
                "generated package abstained because requirements are "
                "missing: metric unit is not frozen"
            ]
        ),
    )

    view = stage3_read_model(repository, study_id)
    assert view["overview"]["status"] == "contract_revision_required"
    assert view["repair_route"] == "stage2_contract_vnext"
    assert view["handoff_issues"]


def test_stage3_build_admission_retry_reuses_immutable_specification(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )

    first_handoff, first_seal, first_capability = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    second_handoff, second_seal, second_capability = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )

    assert second_handoff == first_handoff
    assert second_seal == first_seal
    assert second_capability == first_capability


def test_stage3_contract_vnext_does_not_reuse_stale_build_state(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )
    record_stage3_build_failure(
        repository,
        study_id,
        "resolve_or_build_assets",
        Stage3BuildAdmissionError(
            [
                "generated package abstained because requirements are "
                "missing: metric unit is not frozen"
            ]
        ),
    )

    previous = repository.load_research_contract(study_id, 1)
    draft = previous.model_copy(
        update={
            "version": 2,
            "predecessor_version": 1,
            "status": ArtifactStatus.DRAFT,
            "frozen_at": None,
        }
    )
    repository.save_research_contract(draft)
    gate = repository.create_gate(
        study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study_id}:research-v2",
        subject_version=2,
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    repository.save_research_contract(
        draft.model_copy(
            update={"status": ArtifactStatus.FROZEN, "frozen_at": "frozen"}
        )
    )

    view = stage3_read_model(repository, study_id)

    assert view["overview"]["status"] == "contract_revision_required"
    assert view["build_handoff"] is None
    assert view["build_plan"] is None
    assert view["build_steps"] == []


def test_ready_made_package_passes_second_admission_into_same_kernel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    source = Path(
        repository.load_project(
            repository.load_study(study_id).project_id
        ).source_root
    )
    _write_json(source / "smoke-input.json", {"rows": ["development-only"]})
    manifest_path = source / "research-forge.experiments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for experiment in manifest["experiments"]:
        experiment["smoke_command"] = list(experiment["command"])
        experiment["smoke_required_inputs"] = ["smoke-input.json"]
        experiment["container_image"] = "python:3.12-slim"
    _write_json(manifest_path, manifest)

    def fake_isolated(command, *, input_dir, output_dir, policy):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        _write_json(
            output / "metrics.json",
            {
                "accuracy": 1.0,
                "denominator": 1,
                "sample_ids": ["smoke-1"],
            },
        )
        return SimpleNamespace(
            exit_code=0,
            stdout="",
            stderr="",
            isolation_attestation={
                "runtime": "docker",
                "network": "none",
                "image": policy.image,
                "image_id": "sha256:ready-made-test-image",
                "read_only_input": True,
                "separate_writable_output": True,
            },
        )

    monkeypatch.setattr(
        "research_forge.container_execution.run_isolated_command",
        fake_isolated,
    )
    blueprint, receipt = _blueprint_and_receipt(study_id)
    build_handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    build_plan = create_experiment_build_plan(
        repository, study_id, build_handoff.handoff_id
    )

    execution_seal = freeze_ready_made_execution_package(
        repository, study_id, build_plan.build_plan_id
    )
    formal_handoff = formal_execution_admission(
        repository, study_id, execution_seal.seal_id
    )
    admitted, run_plan, _ = ensure_stage_three_dag(
        repository, study_id
    )

    assert formal_handoff.handoff_stage == "formal_execution"
    assert formal_handoff.predecessor_handoff_id == build_handoff.handoff_id
    assert formal_handoff.execution_package_seal_id == execution_seal.seal_id
    assert len(execution_seal.lock_artifact_ids) == 11
    environment_lock = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage3"
            / "execution_packages"
            / build_plan.build_plan_id
            / "environment.lock.json"
        ).read_text(encoding="utf-8")
    )
    assert environment_lock["container_image"] == "python:3.12-slim"
    assert (
        environment_lock["container_image_id"]
        == "sha256:ready-made-test-image"
    )
    assert admitted.handoff_id == formal_handoff.handoff_id
    assert run_plan.handoff_id == formal_handoff.handoff_id
    feasibility = [
        item
        for item in repository.list_artifacts(study_id)
        if item.role is ArtifactRole.FEASIBILITY
    ]
    assert feasibility
    assert all(
        item.artifact_id
        not in {
            artifact_id
            for chain in repository.list_evidence_chains(study_id)
            for artifact_id in (
                chain.output_artifact_ids
                + chain.evaluation_artifact_ids
            )
        }
        for item in feasibility
    )


def test_execution_freeze_blocks_smoke_on_formal_inputs(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    blueprint, receipt = _blueprint_and_receipt(study_id)
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    with pytest.raises(
        Stage3BuildAdmissionError,
        match="isolated smoke_command",
    ):
        freeze_ready_made_execution_package(
            repository, study_id, plan.build_plan_id
        )


def test_untrusted_code_cannot_fall_back_to_local_smoke_runner(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    blueprint, receipt = _blueprint_and_receipt(study_id)
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )

    with pytest.raises(
        Stage3BuildAdmissionError,
        match="isolated container execution is required",
    ):
        freeze_ready_made_execution_package(
            repository,
            study_id,
            plan.build_plan_id,
            trust_level=ExecutionTrustLevel.AI_GENERATED_CODE,
        )


def test_blueprint_generated_source_is_materialized_without_model_write_access(
    tmp_path: Path, monkeypatch
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    (source / "research-forge.experiments.json").unlink()
    blueprint, receipt = _blueprint_and_receipt(
        study_id, resource_strategy="generate"
    )
    handoff, _, _ = stage3_build_admission(
        repository, study_id, blueprint, receipt
    )
    plan = create_experiment_build_plan(
        repository, study_id, handoff.handoff_id
    )
    generated = {
        "files": [
            {
                "path": "data/formal.jsonl",
                "role": "dataset",
                "content": (
                    '{"sample_id":"f1","features":{"x":1},'
                    '"target_reference":"formal-target-f1"}\n'
                ),
                "source_basis": ["frozen generation rule"],
                "generated": True,
            },
            {
                "path": "data/formal-targets.jsonl",
                "role": "target_dataset",
                "content": (
                    '{"target_reference":"formal-target-f1","target":1}\n'
                ),
                "source_basis": ["frozen formal target rule"],
                "generated": True,
            },
            {
                "path": "data/smoke.jsonl",
                "role": "smoke_dataset",
                "content": (
                    '{"sample_id":"s1","features":{"x":0},'
                    '"target_reference":"smoke-target-s1"}\n'
                ),
                "source_basis": ["development-only fixture rule"],
                "generated": True,
            },
            {
                "path": "data/smoke-targets.jsonl",
                "role": "smoke_target_dataset",
                "content": (
                    '{"target_reference":"smoke-target-s1","target":0}\n'
                ),
                "source_basis": ["development-only target rule"],
                "generated": True,
            },
            *[
                {
                    "path": f"{role}.py",
                    "role": role,
                    "content": "print('generated')\n",
                    "source_basis": [f"frozen {role} requirement"],
                    "generated": True,
                }
                for role in ("baseline", "treatment", "evaluator")
            ],
        ],
        "formal_data_path": "data/formal.jsonl",
        "formal_target_path": "data/formal-targets.jsonl",
        "smoke_data_path": "data/smoke.jsonl",
        "smoke_target_path": "data/smoke-targets.jsonl",
        "baseline_command": [
            "{python}", "baseline.py", "{data_file}", "{prediction_file}"
        ],
        "treatment_command": [
            "{python}", "treatment.py", "{data_file}", "{prediction_file}"
        ],
        "evaluator_command": ["{python}", "evaluator.py"],
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
        "conformance_claims": ["Only the registered arm delta differs."],
        "declared_allowed_arm_delta": [
            "registered treatment intervention"
        ],
    }

    with pytest.raises(
        Stage3BuildAdmissionError,
        match="arm delta does not exactly match",
    ):
        materialize_generated_profile_v1_package(
            repository,
            study_id,
            plan.build_plan_id,
            {
                **generated,
                "declared_allowed_arm_delta": [
                    "post-result unregistered change"
                ],
            },
        )
    materialized = materialize_generated_profile_v1_package(
        repository, study_id, plan.build_plan_id, generated
    )

    assert materialized["model_had_write_access"] is False
    assert materialized["formal_execution_performed"] is False
    assert materialized["evidence_eligible"] is False
    assert materialized["isolated_execution_required"] is True
    assert materialized["generated_evaluator_has_formal_authority"] is False
    assert materialized["commands"]["formal_evaluator"][1] == (
        "platform_evaluator.py"
    )
    assert Path(materialized["materialization_root"]).is_dir()
    smoke = next(
        item
        for item in repository.list_artifacts(study_id)
        if item.kind == "generated_smoke_dataset"
    )
    assert smoke.role is ArtifactRole.FEASIBILITY

    mounted_inputs: list[Path] = []

    def fake_isolated(
        command: list[str],
        *,
        input_dir: str | Path,
        output_dir: str | Path,
        policy: object,
    ) -> SimpleNamespace:
        del policy
        mounted = Path(input_dir)
        mounted_inputs.append(mounted)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        if "platform_evaluator.py" in command:
            if (mounted / "golden_predictions.jsonl").is_file():
                metrics = {
                    "accuracy": 0.5,
                    "denominator": 2,
                    "sample_ids": ["a", "b"],
                    "abstentions": 0,
                }
            else:
                predictions = [
                    json.loads(line)
                    for line in (mounted / "predictions.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line
                ]
                targets = {
                    row["target_reference"]: row["target"]
                    for row in (
                        json.loads(line)
                        for line in (
                            mounted / "data" / "formal-targets.jsonl"
                        )
                        .read_text(encoding="utf-8")
                        .splitlines()
                        if line
                    )
                }
                metrics = {
                    "accuracy": sum(
                        row["prediction"]
                        == targets[row["target_reference"]]
                        for row in predictions
                    )
                    / len(predictions),
                    "denominator": len(predictions),
                    "sample_ids": [
                        row["sample_id"] for row in predictions
                    ],
                    "abstentions": 0,
                }
            _write_json(output / "metrics.json", metrics)
        else:
            data_path = next(
                path
                for path in (
                    mounted / "data" / "smoke.jsonl",
                    mounted / "data" / "formal.jsonl",
                )
                if path.is_file()
            )
            row = json.loads(
                data_path.read_text(encoding="utf-8").splitlines()[0]
            )
            (output / "predictions.jsonl").write_text(
                json.dumps(
                    {
                        "sample_id": row["sample_id"],
                        "prediction": (
                            1 if "treatment.py" in command else 0
                        ),
                        "target_reference": row["target_reference"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return SimpleNamespace(
            exit_code=0,
            stdout="",
            stderr="",
            isolation_attestation={
                "network": "none",
                "image": "python:3.12-slim",
                "image_id": "sha256:test-image",
            },
        )

    monkeypatch.setattr(
        "research_forge.container_execution.run_isolated_command",
        fake_isolated,
    )
    smoke_receipt = smoke_generated_profile_v1_package(
        repository, study_id, plan.build_plan_id
    )

    assert smoke_receipt["formal_data_mounted"] is False
    assert smoke_receipt["formal_targets_mounted"] is False
    assert smoke_receipt["candidate_saw_any_targets"] is False
    assert smoke_receipt["arm_performance_compared"] is False
    assert smoke_receipt["evaluator_golden_vector_passed"] is True
    assert all(
        not (mounted / "data" / "formal.jsonl").exists()
        for mounted in mounted_inputs
    )
    assert all(
        not (mounted / "data" / "formal-targets.jsonl").exists()
        for mounted in mounted_inputs
    )
    assert all(
        not (mounted / "data" / "smoke-targets.jsonl").exists()
        for mounted in mounted_inputs[:2]
    )
    execution_seal = freeze_generated_profile_v1_execution_package(
        repository, study_id, plan.build_plan_id
    )
    formal_handoff = formal_execution_admission(
        repository, study_id, execution_seal.seal_id
    )
    run_plan = compile_run_plan(
        repository, study_id, handoff=formal_handoff
    )
    assert execution_seal.trust_level is ExecutionTrustLevel.AI_GENERATED_CODE
    assert execution_seal.isolated_execution_required is True
    assert Path(str(formal_handoff.execution_root)) == Path(
        materialized["materialization_root"]
    )
    assert {cell.arm_id for cell in run_plan.cells} == {
        "baseline",
        "treatment",
    }
    build_steps = [
        item
        for item in repository.list_steps(study_id)
        if item.task_group
        and item.task_group.startswith("stage3-build:")
    ]
    assert {item.step_type for item in build_steps} == {
        "receive_stage2_handoff",
        "validate_scientific_handoff",
        "stage3_build_admission",
        "resolve_experiment_profile",
        "create_experiment_build_plan",
        "approve_experiment_build_plan",
        "resolve_or_build_assets",
        "run_engineering_smoke_tests",
        "verify_spec_conformance",
        "freeze_execution_package",
        "formal_execution_admission",
    }
    assert all(
        item.status.value == "succeeded" for item in build_steps
    )
    _, persisted_plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=4
    )
    scheduler.run(study_id)
    gate = next(
        item
        for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == persisted_plan.plan_id
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    scheduler.run(study_id)
    evaluation = repository.list_evaluation_records(study_id)[0]
    assert evaluation.qualification_status.value == "qualified"
    assert evaluation.paired_effect == pytest.approx(1.0)
    assert all(
        attempt.isolation_attestations["candidate_targets_mounted"] is False
        for attempt in repository.list_execution_attempts(study_id)
    )
    artifacts = repository.list_artifacts(study_id)
    scientific_artifact = next(
        item
        for item in artifacts
        if item.kind == "scientific_specification_seal"
    )
    execution_artifact = next(
        item for item in artifacts if item.kind == "execution_package_seal"
    )
    edges = repository.list_evidence_edges(study_id)
    assert any(
        edge.source_id == scientific_artifact.artifact_id
        and edge.target_id == execution_artifact.artifact_id
        for edge in edges
    )
    assert any(
        edge.source_id == execution_artifact.artifact_id
        and edge.target_id == persisted_plan.plan_id
        for edge in edges
    )


def test_input_change_produces_new_plan_identity(tmp_path: Path) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    handoff = admit_stage_three(repository, study_id)
    first = compile_run_plan(repository, study_id, handoff=handoff)
    _write_json(source / "input.json", {"rows": [1, 2, 3, 4]})
    second = compile_run_plan(repository, study_id, handoff=handoff)

    assert first.plan_id != second.plan_id
    assert first.plan_hash != second.plan_hash


def test_unprofiled_contract_is_explicitly_blocked(tmp_path: Path) -> None:
    repository, study_id, _ = _profile_repository(tmp_path, profile=None)
    with pytest.raises(
        Stage3AdmissionError,
        match="computational_paired_comparison_v1",
    ):
        admit_stage_three(repository, study_id)


def test_stage3_can_close_as_truthful_unverifiable_boundary(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    admit_stage_three(repository, study_id)
    frozen_contract = repository.latest_research_contract(study_id)
    assert frozen_contract is not None
    repository.save_research_contract(
        frozen_contract.model_copy(
            update={
                "version": frozen_contract.version + 1,
                "status": ArtifactStatus.DRAFT,
                "protocol_status": ProtocolStatus.BLOCKED,
                "predecessor_version": frozen_contract.version,
                "frozen_at": None,
            }
        )
    )

    package = finalize_stage3_unverifiable_boundary(
        repository,
        study_id,
        reasons=["The formal target labels are not available."],
    )
    repeated = finalize_stage3_unverifiable_boundary(
        repository,
        study_id,
        reasons=["A later duplicate click must remain idempotent."],
    )

    assert repeated.completion_id == package.completion_id
    assert package.qualification_status.value == "incomplete"
    assert package.evidence_level.value == "L0_boundary_only"
    assert repository.load_study(study_id).phase is Phase.PAPER
    assert repository.list_execution_attempts(study_id) == []
    assert repository.list_result_envelopes(study_id) == []
    evaluation = repository.list_evaluation_records(study_id)[-1]
    assert evaluation.decision.value == "unverifiable"
    assert evaluation.result_ids == []
    envelope = repository.list_claim_envelopes(study_id)[-1]
    assert envelope.maximum_claim_tier == "evidence_boundary_report"
    assert envelope.effect_estimate is None
    authority = stage4_claim_authority(repository, study_id)
    assert authority["claims"][0]["evidence_level"] == "L0_boundary_only"

    ensure_stage_four_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository,
        workflow_handlers(),
        max_concurrency=2,
    )
    snapshot = scheduler.run(study_id)
    by_type = {item["step_type"]: item for item in snapshot["steps"]}
    assert by_type["publication_prerequisite_gate"]["status"] == "succeeded"
    assert by_type["stage4_evidence_sufficiency_gate"]["status"] == "succeeded"
    sufficiency_artifact = next(
        item
        for item in repository.list_artifacts(study_id)
        if item.kind == "stage4_evidence_backfill_request"
    )
    sufficiency = read_json(Path(sufficiency_artifact.path))
    assert sufficiency["status"] == "disclosure_only"
    narrative_gate = next(
        item
        for item in repository.list_gates(study_id)
        if item.subject_type == "publication_narrative_contract"
    )
    repository.decide_gate(
        study_id,
        narrative_gate.gate_id,
        approve=True,
        decided_by="owner",
        reason="Approve the evidence-boundary narrative.",
    )
    scheduler.run(study_id)
    visual_artifact = next(
        item
        for item in repository.list_artifacts(study_id)
        if item.kind == "visual_argument_plan"
    )
    visual_plan = read_json(Path(visual_artifact.path))
    assert visual_plan["figures"][0]["evidence_status"] == "explanatory_only"
    assert visual_plan["figures"][0]["visual_type"] == (
        "editable_process_diagram"
    )
    assert "experimental effect" in visual_plan["visual_thesis"]


def test_stage3_evidence_boundary_bounds_narrative_task_identifier(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path,
        tasks=["A detailed formal scientific task. " * 20],
    )
    admit_stage_three(repository, study_id)

    package = finalize_stage3_unverifiable_boundary(
        repository,
        study_id,
        reasons=["Formal resources are unavailable."],
    )

    plan = repository.list_run_plans(study_id)[-1]
    assert package.qualification_status.value == "incomplete"
    assert all(len(cell.task_id) <= 300 for cell in plan.cells)
    assert all(
        cell.task_id.startswith("formal-primary-task-")
        for cell in plan.cells
    )


def test_stage3_dag_persists_run_cell_parameters(tmp_path: Path) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    handoff, plan, created = ensure_stage_three_dag(repository, study_id)
    repeated_handoff, repeated_plan, repeated = ensure_stage_three_dag(
        repository, study_id
    )

    run_steps = [
        item for item in created
        if item.step_type == "execute_stage3_run_cell"
    ]
    assert handoff.handoff_id == repeated_handoff.handoff_id
    assert plan.plan_id == repeated_plan.plan_id
    assert {item.step_instance_id for item in repeated} == {
        item.step_instance_id
        for item in repository.list_steps(study_id)
        if item.phase is Phase.EXPERIMENT
        and item.task_group
        and item.task_group.startswith("stage3:")
    }
    assert len(run_steps) == len(plan.cells)
    assert {
        item.parameters["run_cell_id"] for item in run_steps
    } == {item.run_cell_id for item in plan.cells}
    assert all(
        item.parameters["plan_id"] == plan.plan_id for item in run_steps
    )
    step_by_cell = {
        item.parameters["run_cell_id"]: item for item in run_steps
    }
    for cell in plan.cells:
        if cell.scheduled_after_run_cell_id:
            assert step_by_cell[
                cell.scheduled_after_run_cell_id
            ].step_instance_id in step_by_cell[
                cell.run_cell_id
            ].depends_on


def test_profile_v1_runs_to_verified_stage3_completion(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository,
        workflow_handlers(),
        max_concurrency=4,
    )
    snapshot = scheduler.run(study_id)
    gate_step = next(
        item for item in snapshot["steps"]
        if item["step_type"] == "stage3_execution_gate"
    )
    assert gate_step["status"] == "waiting_for_user"
    gate = next(
        item for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == plan.plan_id
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
        reason="Run the bounded local fixture.",
    )
    completed = scheduler.run(study_id)

    assert repository.load_study(study_id).phase is Phase.PAPER
    assert all(
        item["status"] == "succeeded"
        for item in completed["steps"]
        if item["task_group"]
        and item["task_group"].startswith("stage3:")
    )
    attempts = repository.list_execution_attempts(study_id)
    assert {
        item.run_cell_id
        for item in attempts
        if item.status is RunCellStatus.SUCCEEDED
    } == {item.run_cell_id for item in plan.cells}
    assert {
        item.status.value for item in attempts
    }.issubset({"succeeded", "failed_transient"})
    evaluations = repository.list_evaluation_records(study_id)
    assert len(evaluations) == 1
    assert evaluations[0].qualification_status.value == "qualified"
    assert evaluations[0].decision.value == "supported"
    assert evaluations[0].paired_effect == pytest.approx(0.1)
    assert evaluations[0].independent_unit_count == 8
    assert evaluations[0].variance_unit == "registered pair"
    assert evaluations[0].secondary_implementation_matches is True
    assert evaluations[0].confirmatory_status.value == "confirmatory_used"
    assert all(item.canonical for item in attempts)
    assert len(repository.list_formal_exposures(study_id)) == 1
    assert (
        repository.list_statistical_assurance_reports(study_id)[0]
        .status.value
        == "conditional"
    )
    assert repository.list_leakage_audit_reports(study_id)[0].blocking is False
    claim_envelopes = repository.list_claim_envelopes(study_id)
    assert len(claim_envelopes) == 1
    assert claim_envelopes[0].confirmatory_status.value == "confirmatory_used"
    verdict = repository.load_study(study_id)
    assert verdict.latest_study_verdict_id
    completion_paths = list(
        (
            repository.root
            / "studies"
            / study_id
            / "stage3"
            / "completion"
        ).glob("stage3-completion-*.json")
    )
    assert len(completion_paths) == 1
    view = stage3_read_model(repository, study_id)
    assert view["overview"] == {
        "status": "completed",
        "completed": len(plan.cells),
        "total": len(plan.cells),
        "attempts": len(plan.cells),
    }
    assert view["evidence"]["verified"] is True
    assert view["verdict"]["status"] == "supported"
    assert view["completion"]["plan_id"] == plan.plan_id
    assert view["completion"]["claim_envelope_id"] == (
        claim_envelopes[0].claim_envelope_id
    )
    stage4_authority = stage4_claim_authority(repository, study_id)
    assert stage4_authority["claim_registry_source"] == (
        "scientific_claim_envelope_only"
    )
    assert stage4_authority["raw_logs_are_claim_authority"] is False
    assert stage4_authority["may_broaden_claims"] is False
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    private_key = Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "control-plane-ed25519.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    archive = tmp_path / "stage3-completion-package.zip"
    exported = export_stage3_completion_package(
        repository_root=repository.root,
        study_id=study_id,
        output_path=archive,
        private_key_path=private_key_path,
        identity="test-control-plane",
        source_commit="test-commit",
    )
    assert exported["signed"] is True
    assert exported["third_party_certified"] is False
    verification = verify_stage3_completion_package(archive)
    assert verification["passed"] is True
    assert verification["stage4_eligible"] is True
    drill = run_stage3_backup_restore_drill(archive)
    assert drill.status == "passed"
    assert drill.append_only_source_preserved is True


def test_task_variance_unit_aggregates_seeds_before_inference(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path,
        tasks=["task-a", "task-b"],
        variance_unit="task",
    )
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=4
    )
    scheduler.run(study_id)
    gate = next(
        item for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == plan.plan_id
    )
    repository.decide_gate(
        study_id, gate.gate_id, approve=True, decided_by="owner"
    )
    scheduler.run(study_id)

    evaluation = repository.list_evaluation_records(study_id)[0]
    assert evaluation.pair_count == 8
    assert evaluation.independent_unit_count == 2
    assert evaluation.variance_unit == "task"
    assert evaluation.paired_effect == pytest.approx(0.1)


def test_second_different_profile_v1_project_needs_no_platform_code(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path / "latency-study",
        tasks=["graph-search", "image-segmentation"],
        metric_name="latency_ms",
        metric_direction="lower_is_better",
        baseline_value=0.40,
        treatment_delta=-0.10,
    )
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=4
    )
    scheduler.run(study_id)
    gate = next(
        item for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == plan.plan_id
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    scheduler.run(study_id)

    evaluation = repository.list_evaluation_records(study_id)[0]
    assert evaluation.metric_name == "latency_ms"
    assert evaluation.paired_effect == pytest.approx(-0.1)
    assert evaluation.decision.value == "supported"
    assert repository.load_study(study_id).phase is Phase.PAPER


def test_multi_arm_profile_runs_through_the_persistent_stage3_dag(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path,
        profile=Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1,
        profile_parameters={
            "pairing_key": "sample_id",
            "cluster_id_field": "task_id",
            "variance_unit": "cluster",
            "inference_spec": {
                "method": "cluster_bootstrap",
                "resample_unit": "task_id",
                "resamples": 1_000,
                "seed": 20260727,
            },
            "arms": ["backbone", "neutral_lora", "marxist_lora"],
            "treatment_arm": "marxist_lora",
            "control_arms": ["backbone", "neutral_lora"],
            "decision_rule": "all_primary_contrasts",
        },
    )
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository,
        workflow_handlers(),
        max_concurrency=4,
    )
    snapshot = scheduler.run(study_id)
    gate = next(
        item
        for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == plan.plan_id
    )
    assert any(
        item["step_type"] == "stage3_execution_gate"
        and item["status"] == "waiting_for_user"
        for item in snapshot["steps"]
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
        reason="Run the bounded three-arm fixture.",
    )
    completed = scheduler.run(study_id)

    assert repository.load_study(study_id).phase is Phase.PAPER
    assert all(
        item["status"] == "succeeded"
        for item in completed["steps"]
        if item["task_group"]
        and item["task_group"].startswith("stage3:")
    )
    evaluation = repository.list_evaluation_records(study_id)[0]
    assert evaluation.decision.value == "supported"
    assert set(evaluation.arm_estimates) == {
        "backbone",
        "neutral_lora",
        "marxist_lora",
    }
    assert evaluation.statistical_rule["safeguard_results"][
        "factual_accuracy"
    ]["passed"] is True
    assert all(
        item["holm_reject"]
        for item in evaluation.contrast_estimates.values()
    )


def test_multi_arm_diagnostic_metric_does_not_gate_the_verdict(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path,
        profile=Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1,
        include_diagnostic_metric=True,
        profile_parameters={
            "pairing_key": "sample_id",
            "cluster_id_field": "task_id",
            "variance_unit": "cluster",
            "inference_spec": {
                "method": "cluster_bootstrap",
                "resample_unit": "task_id",
                "resamples": 1_000,
                "seed": 20260727,
            },
            "arms": ["backbone", "neutral_lora", "marxist_lora"],
            "treatment_arm": "marxist_lora",
            "control_arms": ["backbone", "neutral_lora"],
            "decision_rule": "all_primary_contrasts",
        },
    )
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository,
        workflow_handlers(),
        max_concurrency=4,
    )
    scheduler.run(study_id)
    gate = next(
        item
        for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == plan.plan_id
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    scheduler.run(study_id)

    evaluation = repository.list_evaluation_records(study_id)[0]
    diagnostic = evaluation.statistical_rule["safeguard_results"][
        "patient_action_rate"
    ]
    assert evaluation.decision.value == "supported"
    assert diagnostic["role"] == "diagnostic"
    assert diagnostic["gates_verdict"] is False
    assert diagnostic["passed"] is None


def test_refuted_hypothesis_is_completed_science_not_repair_failure(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(
        tmp_path,
        baseline_value=0.65,
        treatment_delta=-0.10,
    )
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=3
    )
    scheduler.run(study_id)
    gate = next(
        item
        for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == plan.plan_id
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    scheduler.run(study_id)

    view = stage3_read_model(repository, study_id)
    assert view["status_spaces"]["operational_state"] == "completed"
    assert view["status_spaces"]["scientific_verdict"] == "refuted"
    assert view["diagnostics"] == []
    assert view["repair_lineage"] == []


def test_stage3_initialize_and_read_model_api(tmp_path: Path) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    server = create_server(
        "127.0.0.1",
        0,
        runs_root=tmp_path / "runs",
        static_root=static,
        workflow_root=repository.root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}"
            "/api/studies/stage3/initialize",
            data=json.dumps({"study_id": study_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            initialized = json.loads(response.read().decode("utf-8"))
        assert initialized["stage3"]["initialized"] is True
        assert initialized["stage3"]["gate"]["status"] == "awaiting_user"
        assert initialized["stage3"]["overview"]["total"] == 16
        with urlopen(
            f"http://127.0.0.1:{server.server_port}"
            f"/api/studies/stage3?study_id={study_id}"
        ) as response:
            view = json.loads(response.read().decode("utf-8"))
        assert view["plan"]["plan_id"] == initialized["plan_id"]
        assert len(view["run_matrix"]) == 16
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stage3_repair_creates_append_only_successor_plan(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    _, predecessor, _ = ensure_stage_three_dag(repository, study_id)
    diagnostic = repository.save_stage3_diagnostic(
        DiagnosticReport(
            diagnostic_id="stage3-diagnostic-0123456789abcdef",
            study_id=study_id,
            plan_id=predecessor.plan_id,
            earliest_preventable_step_type="execute_stage3_run_cell",
            failure_class=Stage3FailureClass.INTEGRITY,
            system_findings=["A frozen input requires an authorized update."],
        )
    )
    repair = propose_stage3_repair(
        repository,
        study_id,
        diagnostic_id=diagnostic.diagnostic_id,
        scientific_change=False,
        regression_checks=[{"check": "rerun full paired matrix"}],
    )
    repair_gate = next(
        item for item in repository.list_gates(study_id)
        if item.subject_type == "repair_contract"
        and item.subject_id == repair.repair_id
    )
    repository.decide_gate(
        study_id,
        repair_gate.gate_id,
        approve=True,
        decided_by="owner",
        reason="Approve the bounded input repair.",
    )
    _write_json(source / "input.json", {"rows": [1, 2, 3, 4]})
    successor, handoff, next_plan, steps = initialize_stage3_successor(
        repository,
        study_id,
        repair.repair_id,
    )

    assert next_plan.plan_id != predecessor.plan_id
    assert len(repository.list_run_plans(study_id)) == 2
    assert successor.predecessor_plan_id == predecessor.plan_id
    assert successor.successor_plan_id == next_plan.plan_id
    assert successor.repair_contract_id == repair.repair_id
    assert handoff.handoff_id == repository.load_stage3_handoff(
        study_id
    ).handoff_id
    assert len(
        [
            item for item in steps
            if item.step_type == "execute_stage3_run_cell"
        ]
    ) == len(next_plan.cells)
    assert repository.load_repair_contract(
        study_id, repair.repair_id
    ).successor_run_id == next_plan.plan_id


def test_operational_failure_retries_instead_of_creating_repair(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    diagnostic = repository.save_stage3_diagnostic(
        DiagnosticReport(
            diagnostic_id="stage3-diagnostic-1111111111111111",
            study_id=study_id,
            plan_id=plan.plan_id,
            earliest_preventable_step_type="execute_stage3_run_cell",
            failure_class=Stage3FailureClass.TRANSIENT,
            system_findings=["temporary process interruption"],
        )
    )

    with pytest.raises(ValueError, match="retry the same RunCell"):
        propose_stage3_repair(
            repository,
            study_id,
            diagnostic_id=diagnostic.diagnostic_id,
        )


def test_metric_or_threshold_change_requires_scientific_successor(
    tmp_path: Path,
) -> None:
    repository, study_id, _ = _profile_repository(tmp_path)
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    diagnostic = repository.save_stage3_diagnostic(
        DiagnosticReport(
            diagnostic_id="stage3-diagnostic-2222222222222222",
            study_id=study_id,
            plan_id=plan.plan_id,
            earliest_preventable_step_type="evaluate_stage3_results",
            failure_class=Stage3FailureClass.SCIENTIFIC_DESIGN,
            system_findings=["owner proposes a different primary threshold"],
        )
    )

    successor = propose_stage3_repair(
        repository,
        study_id,
        diagnostic_id=diagnostic.diagnostic_id,
        changed_contract_fields=["metrics", "statistical_rules"],
    )

    assert successor.status == "proposed"
    assert successor.changed_contract_fields == [
        "metrics",
        "statistical_rules",
    ]
    assert successor.predecessor_plan_id == plan.plan_id
    assert "create Research Contract vNext" in successor.required_actions
    assert repository.list_repair_contracts(study_id) == []
    assert repository.list_scientific_successor_requests(study_id) == [
        successor
    ]

    predecessor = repository.latest_research_contract(study_id)
    assert predecessor is not None
    frozen = apply_stage3_scientific_successor_contract(
        repository,
        study_id,
        request_id=successor.request_id,
        changes={
            "statistical_rules": {
                **predecessor.statistical_rules,
                "effect_threshold": 0.25,
            }
        },
        decided_by="owner",
        reason="Use the preregistered corrected threshold.",
    )

    assert frozen.version == predecessor.version + 1
    assert frozen.predecessor_version == predecessor.version
    assert frozen.status is ArtifactStatus.FROZEN
    assert frozen.statistical_rules["effect_threshold"] == 0.25
    assert predecessor.statistical_rules["effect_threshold"] != 0.25


def test_stage3_successor_reuses_only_compatible_cells(
    tmp_path: Path,
) -> None:
    repository, study_id, source = _profile_repository(tmp_path)
    _, predecessor, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=3
    )
    scheduler.run(study_id)
    plan_gate = next(
        item for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == predecessor.plan_id
    )
    repository.decide_gate(
        study_id,
        plan_gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    scheduler.run(study_id)

    predecessor_results = {
        item.run_cell_id: item
        for item in repository.list_result_envelopes(study_id)
    }
    treatment_outputs = sorted(
        {
            artifact_id
            for cell in predecessor.cells
            if cell.arm_id == "treatment"
            for artifact_id in predecessor_results[
                cell.run_cell_id
            ].output_artifact_ids
        }
    )
    diagnostic = repository.save_stage3_diagnostic(
        DiagnosticReport(
            diagnostic_id="stage3-diagnostic-fedcba9876543210",
            study_id=study_id,
            plan_id=predecessor.plan_id,
            earliest_preventable_step_type="execute_stage3_run_cell",
            failure_class=Stage3FailureClass.INTEGRITY,
            system_invalidation_artifact_ids=treatment_outputs,
            system_findings=["Treatment declaration requires repair."],
        )
    )
    repair = propose_stage3_repair(
        repository,
        study_id,
        diagnostic_id=diagnostic.diagnostic_id,
        scientific_change=False,
    )
    repair_gate = next(
        item for item in repository.list_gates(study_id)
        if item.subject_type == "repair_contract"
        and item.subject_id == repair.repair_id
    )
    repository.decide_gate(
        study_id,
        repair_gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    manifest_path = source / "research-forge.experiments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["experiments"][1]["title"] = "Treatment repaired"
    _write_json(manifest_path, manifest)

    _, _, successor_plan, successor_steps = initialize_stage3_successor(
        repository, study_id, repair.repair_id
    )
    run_steps = {
        item.parameters["run_cell_id"]: item
        for item in successor_steps
        if item.step_type == "execute_stage3_run_cell"
    }
    for cell in successor_plan.cells:
        has_reuse = "reuse_from_result_id" in run_steps[
            cell.run_cell_id
        ].parameters
        assert has_reuse is (cell.arm_id == "baseline")

    successor_gate = next(
        item for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == successor_plan.plan_id
    )
    repository.decide_gate(
        study_id,
        successor_gate.gate_id,
        approve=True,
        decided_by="owner",
    )
    PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=3
    ).run(study_id)
    successor_cell_ids = {
        item.run_cell_id for item in successor_plan.cells
    }
    attempts = [
        item for item in repository.list_execution_attempts(study_id)
        if item.run_cell_id in successor_cell_ids
    ]
    assert {
        item.run_cell_id for item in attempts
    } == {
        item.run_cell_id
        for item in successor_plan.cells
        if item.arm_id == "treatment"
    }
    successor_results = {
        item.run_cell_id: item
        for item in repository.list_result_envelopes(study_id)
        if item.run_cell_id in successor_cell_ids
    }
    assert all(
        successor_results[item.run_cell_id].reused_from_result_id
        for item in successor_plan.cells
        if item.arm_id == "baseline"
    )
    assert repository.load_repair_contract(
        study_id, repair.repair_id
    ).status.value == "completed"
    exposures = repository.list_formal_exposures(study_id)
    assert [item.confirmatory_status.value for item in exposures] == [
        "confirmatory_used",
        "adaptive_reuse",
    ]
    assert exposures[-1].result_influenced_successor is True
