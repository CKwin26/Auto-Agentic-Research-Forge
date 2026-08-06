"""Deterministic Research Contract compilation and non-formal dry runs.

Stage 2 owns scientific semantics.  A contract may be approved and frozen only
when those semantics compile into an explicit run matrix, evaluator, and
analysis plan.  Concrete executables and formal data are still built in Stage
3; the dry run here is deliberately synthetic and never evidence eligible.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel, utc_now
from .workflow_domain import (
    ProtocolStatus,
    ResearchContractVersion,
    stable_id,
)


_PLACEHOLDER_VALUES = {
    "",
    "unknown",
    "unverified",
    "tbd",
    "todo",
    "not specified",
    "not_specified",
    "not_applicable_or_unknown",
    "formal-primary-task",
    "primary-task",
}


class RunSpecification(StrictModel):
    schema_version: int = 1
    run_specification_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    task_id: str
    split_id: str
    seed: int
    replicate: int = Field(ge=1)
    arm_id: Literal["baseline", "treatment"]
    dataset_specification_id: str
    algorithm_specification_id: str
    operation: str
    preprocessing: str
    target_rule: str
    evidence_eligible: Literal[False] = False


class DatasetSpecification(StrictModel):
    schema_version: int = 1
    dataset_specification_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    approved_resource_ids: list[str]
    materialization_mode: Literal[
        "approved_resources", "authorized_builder"
    ]
    target_population: str
    sampling_frame: str
    unit_of_analysis: str
    denominator: str
    target_rule: str
    preprocessing: str
    required_lifecycle_status: Literal["semantically_validated"] = (
        "semantically_validated"
    )


class AlgorithmSpecification(StrictModel):
    schema_version: int = 1
    algorithm_specification_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    arm_id: Literal["baseline", "treatment"]
    experiment_id: str
    action_id: str
    operation: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    concrete_entrypoint_required_in_stage3: Literal[True] = True


class ExecutableRunDAG(StrictModel):
    schema_version: int = 1
    run_dag_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    run_node_ids: list[str]
    evaluation_node_id: str
    analysis_node_id: str
    dependencies: dict[str, list[str]]


class EvaluationSpecification(StrictModel):
    schema_version: int = 1
    evaluation_specification_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    metric_name: str
    metric_formula: str
    direction: Literal["higher_is_better", "lower_is_better"]
    denominator: str
    unit_of_analysis: str
    missing_data_policy: str
    success_rule: str
    primary_evaluator_family: str
    secondary_evaluator_families: list[str] = Field(default_factory=list)
    disagreement_policy: str
    agreement_metrics: list[str] = Field(default_factory=list)


class AnalysisSpecification(StrictModel):
    schema_version: int = 1
    analysis_specification_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    estimand: dict[str, Any]
    pairing_key: list[str]
    method: str
    effect_threshold: float
    effect_scale: str
    resampling: dict[str, Any] = Field(default_factory=dict)


class ContractCompileReport(StrictModel):
    schema_version: int = 1
    compiler_version: Literal["2.0"] = "2.0"
    compile_report_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    protocol_status: ProtocolStatus
    lint_passed: bool
    compile_passed: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    executable_algorithm_count: int = Field(default=0, ge=0)
    required_algorithm_count: int = Field(default=2, ge=1)
    scientific_contribution_checks: dict[str, bool] = Field(
        default_factory=dict
    )
    scientific_contribution_report: dict[str, Any] | None = None
    dataset_specification: DatasetSpecification | None = None
    algorithm_specifications: list[AlgorithmSpecification] = Field(
        default_factory=list
    )
    run_specifications: list[RunSpecification] = Field(default_factory=list)
    evaluation_specification: EvaluationSpecification | None = None
    analysis_specification: AnalysisSpecification | None = None
    executable_run_dag: ExecutableRunDAG | None = None
    generated_at: str = Field(default_factory=utc_now)


class ContractDryRunReport(StrictModel):
    schema_version: int = 1
    dry_run_report_id: str
    compile_report_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    passed: bool
    evidence_eligible: Literal[False] = False
    formal_run_eligible: Literal[False] = False
    checks: dict[str, bool]
    fixture: dict[str, Any]
    blocking_issues: list[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now)


class BlockingIssueReport(StrictModel):
    schema_version: int = 1
    issue_report_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    source_phase: Literal["protocol", "experiment"]
    issues: list[str] = Field(min_length=1)
    auto_fixable: list[str] = Field(default_factory=list)
    owner_decisions_required: list[str] = Field(default_factory=list)
    recommended_contract_changes: dict[str, Any] = Field(
        default_factory=dict
    )
    created_at: str = Field(default_factory=utc_now)


class AmendmentFieldProposal(StrictModel):
    issue_code: str
    field_path: str
    proposed_action: str
    candidate_value: Any | None = None
    owner_decision_required: bool = True


class ContractAmendmentProposal(StrictModel):
    schema_version: int = 1
    proposal_id: str
    study_id: str
    predecessor_contract_version: int = Field(ge=1)
    proposed_contract_version: int = Field(ge=2)
    source_phase: Literal["protocol", "experiment"]
    issues: list[str] = Field(min_length=1)
    field_proposals: list[AmendmentFieldProposal]
    preserves_historical_contract: Literal[True] = True
    compile_and_dry_run_required: Literal[True] = True
    owner_approval_required: Literal[True] = True
    created_at: str = Field(default_factory=utc_now)


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _placeholder_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_placeholder_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_placeholder_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        normalized = value.strip().casefold()
        if (
            normalized in _PLACEHOLDER_VALUES
            or "must be frozen before stage 3" in normalized
            or normalized.startswith("placeholder")
        ):
            found.append(path)
    return found


def _arm_operation(arm: dict[str, Any]) -> str:
    for key in ("operation", "implementation_spec", "behavior"):
        value = arm.get(key)
        if isinstance(value, dict):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            text = _normalized_text(value)
        if len(text) >= 12:
            return text
    return ""


def _metric_formula(metric: dict[str, Any]) -> str:
    """Return only an explicitly frozen formula or evaluator binding.

    Inferring ``accuracy`` or ``latency`` semantics from a display name made a
    contract look executable while silently inventing its denominator or
    measurement boundary.  Stage 2 owns those choices, so the compiler must
    abstain instead of guessing.
    """

    explicit = _normalized_text(
        metric.get("formula")
        or metric.get("implementation")
        or metric.get("aggregation")
    )
    return explicit


def _target_leakage_violations(data: dict[str, Any]) -> list[str]:
    target = _normalized_text(
        data.get("target_column") or data.get("label_column")
    ).casefold()
    raw_features = data.get("feature_columns") or []
    if isinstance(raw_features, str):
        features = {
            item.strip().casefold()
            for item in raw_features.split(",")
            if item.strip()
        }
    else:
        features = {
            _normalized_text(item).casefold()
            for item in raw_features
            if _normalized_text(item)
        }
    violations: list[str] = []
    if target and target in features:
        violations.append(
            "TARGET_LEAKAGE: the authoritative target column is also listed "
            "as an input feature"
        )
    leakage_policy = _normalized_text(data.get("target_leakage_policy"))
    if features and target and not leakage_policy:
        violations.append(
            "TARGET_LEAKAGE_POLICY_MISSING: freeze how target-derived, "
            "post-outcome, and future fields are excluded"
        )
    return violations


def compile_research_contract(
    contract: ResearchContractVersion,
) -> ContractCompileReport:
    """Compile scientific intent into deterministic Stage 3 specifications."""

    from .stage_three_build import stage3_contract_readiness_violations

    issues = list(stage3_contract_readiness_violations(contract))
    placeholder_paths = _placeholder_paths(
        {
            "tasks": contract.tasks,
            "baseline": contract.baseline,
            "treatment": contract.treatment,
            "metrics": contract.metrics,
            "statistical_rules": contract.statistical_rules,
        }
    )
    if placeholder_paths:
        issues.append(
            "UNRESOLVED_PLACEHOLDERS: " + ", ".join(placeholder_paths)
        )

    baseline_operation = _arm_operation(contract.baseline)
    treatment_operation = _arm_operation(contract.treatment)
    executable_algorithm_count = sum(
        bool(item) for item in (baseline_operation, treatment_operation)
    )
    if not baseline_operation:
        issues.append(
            "BASELINE_NOT_COMPILABLE: define its computational operation"
        )
    if not treatment_operation:
        issues.append(
            "TREATMENT_NOT_COMPILABLE: define its computational operation"
        )
    generic_operation_fragments = (
        "the primary implementation",
        "registered treatment intervention",
        "frozen replication and sensitivity conditions",
        "the declared method or intervention",
        "the declared baseline",
    )
    if baseline_operation and any(
        fragment in baseline_operation.casefold()
        for fragment in generic_operation_fragments
    ):
        issues.append(
            "BASELINE_OPERATION_GENERIC: name the algorithm, transformation, "
            "or deterministic operation applied to each formal unit"
        )
    if treatment_operation and any(
        fragment in treatment_operation.casefold()
        for fragment in generic_operation_fragments
    ):
        issues.append(
            "TREATMENT_OPERATION_GENERIC: define the single executable arm "
            "delta instead of naming an unspecified intervention"
        )
    normalized_baseline = " ".join(baseline_operation.casefold().split())
    normalized_treatment = " ".join(treatment_operation.casefold().split())
    contribution_checks = {
        "baseline_operation_resolved": bool(baseline_operation),
        "treatment_operation_resolved": bool(treatment_operation),
        "registered_arm_delta_present": bool(
            normalized_baseline
            and normalized_treatment
            and normalized_baseline != normalized_treatment
        ),
    }
    if (
        contribution_checks["baseline_operation_resolved"]
        and contribution_checks["treatment_operation_resolved"]
        and not contribution_checks["registered_arm_delta_present"]
    ):
        issues.append(
            "ARM_DELTA_MISSING: baseline and treatment compile to the same "
            "operation; freeze the single allowed intervention difference"
        )

    from .scientific_contribution_gate import assess_scientific_contribution

    contribution_report = assess_scientific_contribution(contract)
    issues.extend(contribution_report.blocking_issues)

    data = dict(contract.data_requirements or contract.data_boundary)
    from .domain_adapters import (
        finance_backtest_violations,
        is_finance_backtest_contract,
    )

    if is_finance_backtest_contract(contract.model_dump(mode="json")):
        issues.extend(finance_backtest_violations(data))
    issues.extend(_target_leakage_violations(data))
    approved_resource_ids = contract.runtime_binding.get(
        "approved_resource_ids", []
    )
    resource_mode = _normalized_text(
        contract.resource_policy.get("mode")
    ).casefold()
    resource_routes = contract.resource_policy.get("routes") or []
    authorized_builder = resource_mode in {
        "build_or_acquire_experiment",
        "build_new_bounded_experiment",
        "synthetic_resource_builder",
    } and bool(resource_routes)
    if not approved_resource_ids and not authorized_builder:
        issues.append(
            "APPROVED_RESOURCES_MISSING: authorize at least one resource or "
            "an explicit synthetic-resource builder before contract freeze"
        )
    target_rule = _normalized_text(
        data.get("target_rule") or data.get("label_rule")
    )
    if not target_rule:
        issues.append(
            "TARGET_RULE_MISSING: define authoritative targets or labels"
        )
    population = _normalized_text(
        data.get("target_population")
        or data.get("population")
        or contract.estimand.get("population")
    )
    if (
        not population
        or population.casefold() in {"unknown", "unverified"}
        or population.casefold().startswith("no formal dataset is bound")
    ):
        issues.append(
            "TARGET_POPULATION_MISSING: freeze the population and sampling "
            "frame that Stage 3 must materialize"
        )
    sampling_frame = _normalized_text(
        data.get("sampling_frame") or data.get("sampling_rule")
    )
    if not sampling_frame or sampling_frame.casefold() in {
        "unknown",
        "unverified",
        "local project resources",
    }:
        issues.append(
            "SAMPLING_FRAME_MISSING: define a deterministic census or "
            "sampling rule for formal cases"
        )
    preprocessing = _normalized_text(
        data.get("preprocessing")
        or contract.profile_parameters.get("preprocessing")
        or "identity preprocessing; reject schema-incompatible rows"
    )

    metric = dict(contract.metrics[0]) if contract.metrics else {}
    metric_name = _normalized_text(metric.get("name"))
    metric_formula = _metric_formula(metric)
    if not metric_name:
        issues.append("PRIMARY_METRIC_MISSING: name the primary metric")
    if not metric_formula:
        issues.append(
            "METRIC_FORMULA_MISSING: define an executable metric formula"
        )
    direction = _normalized_text(
        metric.get("direction")
        or contract.statistical_rules.get("direction")
    )
    direction_aliases = {
        "maximize": "higher_is_better",
        "higher": "higher_is_better",
        "higher_is_better": "higher_is_better",
        "minimize": "lower_is_better",
        "lower": "lower_is_better",
        "lower_is_better": "lower_is_better",
    }
    normalized_direction = direction_aliases.get(direction.casefold())
    if normalized_direction is None:
        issues.append(
            "METRIC_DIRECTION_MISSING: use higher_is_better or "
            "lower_is_better"
        )

    threshold = contract.statistical_rules.get("effect_threshold")
    threshold_value = (
        float(threshold)
        if isinstance(threshold, (int, float))
        and not isinstance(threshold, bool)
        else 0.0
    )
    denominator = _normalized_text(data.get("denominator"))
    unit = _normalized_text(data.get("unit_of_analysis"))
    missing_policy = _normalized_text(
        contract.statistical_rules.get("missing_data_policy")
    )
    success_rule = _normalized_text(
        contract.statistical_rules.get("success_threshold")
        or contract.hypotheses[0].decision_rule
    )
    if not success_rule:
        issues.append(
            "SUCCESS_RULE_MISSING: define the frozen scientific decision rule"
        )

    evaluator_policy = dict(contract.evaluator_policy or {})
    primary_evaluator = evaluator_policy.get("primary_evaluator")
    if isinstance(primary_evaluator, dict):
        primary_family = _normalized_text(
            primary_evaluator.get("family_id")
            or primary_evaluator.get("family")
        )
    else:
        primary_family = _normalized_text(
            evaluator_policy.get("primary_family_id")
            or evaluator_policy.get("authority")
            or "deterministic_reference"
        )
    raw_secondary = evaluator_policy.get("secondary_evaluators") or []
    secondary_families = [
        _normalized_text(
            item.get("family_id") or item.get("family")
            if isinstance(item, dict)
            else item
        )
        for item in raw_secondary
    ]
    secondary_families = [item for item in secondary_families if item]
    secondary_required = bool(
        evaluator_policy.get("secondary_required")
        or evaluator_policy.get("uses_learned_evaluator")
    )
    if secondary_required and not secondary_families:
        issues.append(
            "SECONDARY_EVALUATOR_MISSING: learned or protected evaluators "
            "require a frozen second evaluator family"
        )
    if primary_family in secondary_families:
        issues.append(
            "SECONDARY_EVALUATOR_FAMILY_NOT_DISTINCT: a renamed instance of "
            "the primary family is not an independent evaluator family"
        )
    disagreement_policy = _normalized_text(
        evaluator_policy.get("disagreement_policy")
        or (
            "record row-level and directional disagreements; require "
            "adjudication before a stable verdict"
            if secondary_families
            else "not_applicable_single_deterministic_evaluator"
        )
    )
    if secondary_required and not disagreement_policy:
        issues.append(
            "EVALUATOR_DISAGREEMENT_POLICY_MISSING: freeze how evaluator "
            "disagreements affect the scientific verdict"
        )

    if contract.study_design:
        from .study_design import validate_composable_contract

        issues.extend(
            f"STUDY_DESIGN_INVALID: {item}"
            for item in validate_composable_contract(contract)
        )

    issues = list(dict.fromkeys(issues))
    compile_report_id = stable_id(
        "contract-compile",
        contract.study_id,
        contract.version,
        json.dumps(
            contract.model_dump(
                mode="json",
                exclude={
                    "protocol_status",
                    "compile_report_id",
                    "unresolved_placeholders",
                    "run_specification_ids",
                    "evaluation_specification_id",
                    "analysis_specification_id",
                    "dry_run_report_id",
                },
            ),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    if issues:
        return ContractCompileReport(
            compile_report_id=compile_report_id,
            study_id=contract.study_id,
            contract_version=contract.version,
            protocol_status=ProtocolStatus.BLOCKED,
            lint_passed=not placeholder_paths,
            compile_passed=False,
            blocking_issues=issues,
            executable_algorithm_count=executable_algorithm_count,
            scientific_contribution_checks=contribution_checks,
            scientific_contribution_report=contribution_report.model_dump(
                mode="json"
            ),
            warnings=contribution_report.warnings,
        )

    run_specs: list[RunSpecification] = []
    dataset = DatasetSpecification(
        dataset_specification_id=stable_id(
            "dataset-spec", compile_report_id
        ),
        study_id=contract.study_id,
        contract_version=contract.version,
        approved_resource_ids=sorted(str(item) for item in approved_resource_ids),
        materialization_mode=(
            "approved_resources" if approved_resource_ids else "authorized_builder"
        ),
        target_population=population,
        sampling_frame=sampling_frame,
        unit_of_analysis=unit,
        denominator=denominator,
        target_rule=target_rule,
        preprocessing=preprocessing,
    )
    algorithms = [
        AlgorithmSpecification(
            algorithm_specification_id=stable_id(
                "algorithm-spec",
                compile_report_id,
                arm_id,
            ),
            study_id=contract.study_id,
            contract_version=contract.version,
            arm_id=arm_id,  # type: ignore[arg-type]
            experiment_id=_normalized_text(arm.get("experiment_id")),
            action_id=_normalized_text(arm.get("action_id")),
            operation=operation,
            parameters=dict(arm.get("parameters") or {}),
        )
        for arm_id, arm, operation in (
            ("baseline", contract.baseline, baseline_operation),
            ("treatment", contract.treatment, treatment_operation),
        )
    ]
    algorithm_by_arm = {item.arm_id: item for item in algorithms}
    for task in contract.tasks:
        for split in contract.splits:
            for seed in contract.seeds:
                for replicate in range(1, contract.replicates + 1):
                    for arm_id, operation in (
                        ("baseline", baseline_operation),
                        ("treatment", treatment_operation),
                    ):
                        run_specs.append(
                            RunSpecification(
                                run_specification_id=stable_id(
                                    "run-spec",
                                    contract.study_id,
                                    contract.version,
                                    task,
                                    split,
                                    seed,
                                    replicate,
                                    arm_id,
                                ),
                                study_id=contract.study_id,
                                contract_version=contract.version,
                                task_id=task,
                                split_id=split,
                                seed=seed,
                                replicate=replicate,
                                arm_id=arm_id,
                                dataset_specification_id=(
                                    dataset.dataset_specification_id
                                ),
                                algorithm_specification_id=(
                                    algorithm_by_arm[arm_id].algorithm_specification_id
                                ),
                                operation=operation,
                                preprocessing=preprocessing,
                                target_rule=target_rule,
                            )
                        )
    evaluation = EvaluationSpecification(
        evaluation_specification_id=stable_id(
            "evaluation-spec", compile_report_id
        ),
        study_id=contract.study_id,
        contract_version=contract.version,
        metric_name=metric_name,
        metric_formula=metric_formula,
        direction=normalized_direction,  # type: ignore[arg-type]
        denominator=denominator,
        unit_of_analysis=unit,
        missing_data_policy=missing_policy,
        success_rule=success_rule,
        primary_evaluator_family=primary_family,
        secondary_evaluator_families=secondary_families,
        disagreement_policy=disagreement_policy,
        agreement_metrics=[
            "aggregate_metric_agreement",
            "row_level_disagreement",
            "directional_conclusion_agreement",
            "verdict_stability",
        ],
    )
    analysis = AnalysisSpecification(
        analysis_specification_id=stable_id(
            "analysis-spec", compile_report_id
        ),
        study_id=contract.study_id,
        contract_version=contract.version,
        estimand=dict(contract.estimand),
        pairing_key=["task", "split", "seed", "replicate"],
        method=_normalized_text(
            contract.statistical_rules.get("analysis")
            or contract.statistical_rules.get("method")
            or "paired effect estimate"
        ),
        effect_threshold=threshold_value,
        effect_scale=_normalized_text(
            contract.statistical_rules.get("effect_scale")
        ),
        resampling={
            key: contract.statistical_rules[key]
            for key in (
                "bootstrap_resamples",
                "bootstrap_seed",
                "resampling_unit",
            )
            if key in contract.statistical_rules
        },
    )
    evaluation_node_id = stable_id("evaluation-node", compile_report_id)
    analysis_node_id = stable_id("analysis-node", compile_report_id)
    run_node_ids = [item.run_specification_id for item in run_specs]
    run_dag = ExecutableRunDAG(
        run_dag_id=stable_id("run-dag", compile_report_id),
        study_id=contract.study_id,
        contract_version=contract.version,
        run_node_ids=run_node_ids,
        evaluation_node_id=evaluation_node_id,
        analysis_node_id=analysis_node_id,
        dependencies={
            **{item: [] for item in run_node_ids},
            evaluation_node_id: run_node_ids,
            analysis_node_id: [evaluation_node_id],
        },
    )
    return ContractCompileReport(
        compile_report_id=compile_report_id,
        study_id=contract.study_id,
        contract_version=contract.version,
        protocol_status=ProtocolStatus.EXECUTABLE,
        lint_passed=True,
        compile_passed=True,
        executable_algorithm_count=2,
        scientific_contribution_checks=contribution_checks,
        scientific_contribution_report=contribution_report.model_dump(
            mode="json"
        ),
        warnings=contribution_report.warnings,
        dataset_specification=dataset,
        algorithm_specifications=algorithms,
        run_specifications=run_specs,
        evaluation_specification=evaluation,
        analysis_specification=analysis,
        executable_run_dag=run_dag,
    )


def dry_run_compiled_contract(
    report: ContractCompileReport,
) -> ContractDryRunReport:
    """Run a deterministic synthetic specification probe.

    The fixture only exercises bindings and metric direction.  It is never a
    baseline result and cannot enter a verdict or manuscript result table.
    """

    evaluation = report.evaluation_specification
    checks = {
        "contract_compiled": report.compile_passed,
        "two_algorithms_resolved": report.executable_algorithm_count == 2,
        "run_matrix_nonempty": bool(report.run_specifications),
        "both_arms_present": {
            item.arm_id for item in report.run_specifications
        }
        == {"baseline", "treatment"},
        "evaluation_resolved": evaluation is not None,
        "analysis_resolved": report.analysis_specification is not None,
        "dataset_resolved": report.dataset_specification is not None,
        "algorithms_resolved": len(report.algorithm_specifications) == 2,
        "run_dag_resolved": report.executable_run_dag is not None,
    }
    passed = all(checks.values())
    blockers = [] if passed else [
        name for name, value in checks.items() if not value
    ]
    return ContractDryRunReport(
        dry_run_report_id=stable_id(
            "contract-dry-run", report.compile_report_id
        ),
        compile_report_id=report.compile_report_id,
        study_id=report.study_id,
        contract_version=report.contract_version,
        passed=passed,
        checks=checks,
        fixture={
            "kind": "synthetic_binding_fixture",
            "baseline_value": 0.4,
            "treatment_value": 0.6,
            "metric_direction": (
                evaluation.direction if evaluation is not None else None
            ),
            "scientific_interpretation_forbidden": True,
        },
        blocking_issues=blockers,
    )


def blocking_issue_report(
    contract: ResearchContractVersion,
    issues: list[str],
    *,
    source_phase: Literal["protocol", "experiment"],
) -> BlockingIssueReport:
    normalized = list(dict.fromkeys(item.strip() for item in issues if item.strip()))
    auto_fixable_prefixes = {
        "METRIC_FORMULA_MISSING",
        "METRIC_DIRECTION_MISSING",
    }
    auto_fixable = [
        item
        for item in normalized
        if item.split(":", 1)[0] in auto_fixable_prefixes
    ]
    owner = [item for item in normalized if item not in auto_fixable]
    return BlockingIssueReport(
        issue_report_id=stable_id(
            "blocking-issues",
            contract.study_id,
            contract.version,
            source_phase,
            *normalized,
        ),
        study_id=contract.study_id,
        contract_version=contract.version,
        source_phase=source_phase,
        issues=normalized,
        auto_fixable=auto_fixable,
        owner_decisions_required=owner,
        recommended_contract_changes={
            "create_version": contract.version + 1,
            "return_to_phase": "protocol",
            "preserve_historical_contract": True,
            "rerun_required": source_phase == "experiment",
        },
    )


def contract_amendment_proposal(
    contract: ResearchContractVersion,
    issues: list[str],
    *,
    source_phase: Literal["protocol", "experiment"],
) -> ContractAmendmentProposal:
    normalized = list(dict.fromkeys(item.strip() for item in issues if item.strip()))
    mappings = {
        "TARGET_RULE_MISSING": (
            "data_requirements.target_rule",
            "generate and validate a candidate label/target builder",
        ),
        "TARGET_POPULATION_MISSING": (
            "data_requirements.target_population",
            "freeze the estimand population and eligibility boundary",
        ),
        "SAMPLING_FRAME_MISSING": (
            "data_requirements.sampling_frame",
            "freeze a census or deterministic sampling procedure",
        ),
        "METRIC_FORMULA_MISSING": (
            "metrics[0].formula",
            "select an executable formula with an explicit denominator",
        ),
        "TARGET_LEAKAGE": (
            "data_requirements.feature_columns",
            "remove target-derived or future fields and rerun leakage tests",
        ),
        "TARGET_LEAKAGE_POLICY_MISSING": (
            "data_requirements.target_leakage_policy",
            "freeze target-derived, post-outcome, and future-field exclusions",
        ),
        "ARM_DELTA_MISSING": (
            "implementation_requirements.allowed_arm_delta",
            "define the single mechanism-changing intervention",
        ),
        "BASELINE_NOT_COMPILABLE": (
            "baseline.implementation_spec",
            "define baseline algorithm semantics and its Stage 3 entrypoint contract",
        ),
        "BASELINE_OPERATION_GENERIC": (
            "baseline.implementation_spec",
            "replace the generic arm name with an executable baseline algorithm",
        ),
        "TREATMENT_NOT_COMPILABLE": (
            "treatment.implementation_spec",
            "define treatment algorithm semantics and its Stage 3 entrypoint contract",
        ),
        "TREATMENT_OPERATION_GENERIC": (
            "treatment.implementation_spec",
            "replace the generic intervention name with its executable arm delta",
        ),
        "APPROVED_RESOURCES_MISSING": (
            "resource_policy",
            "select validated resources or approve a bounded resource builder",
        ),
        "SECONDARY_EVALUATOR_MISSING": (
            "evaluator_policy.secondary_evaluators",
            "freeze a second implementation from a distinct evaluator family",
        ),
        "SECONDARY_EVALUATOR_FAMILY_NOT_DISTINCT": (
            "evaluator_policy.secondary_evaluators",
            "replace the duplicate family with an independently implemented evaluator",
        ),
        "EVALUATOR_DISAGREEMENT_POLICY_MISSING": (
            "evaluator_policy.disagreement_policy",
            "freeze row-level, directional, and verdict-stability handling",
        ),
        "CONTRIBUTION_EXPECTED_INFORMATION_VALUE": (
            "scientific_validity_contract.expected_information_value",
            "state which scientific or operational decision the experiment resolves",
        ),
        "CONTRIBUTION_SAMPLE_ADEQUACY": (
            "scientific_validity_contract.sample_adequacy_basis",
            "freeze the sample-size or power justification",
        ),
        "CONTRIBUTION_PSEUDO_REPLICATION": (
            "scientific_validity_contract.independence_justification",
            "freeze the independent scientific and variance units without counting reruns as n",
        ),
    }
    proposals: list[AmendmentFieldProposal] = []
    for issue in normalized:
        code = issue.split(":", 1)[0].strip()
        if code in mappings:
            path, action = mappings[code]
        elif code.startswith("FINANCE_") and code.endswith("_MISSING"):
            policy_name = code[len("FINANCE_") : -len("_MISSING")].casefold()
            path, action = (
                f"data_requirements.{policy_name}",
                "freeze the point-in-time finance policy before Stage 3",
            )
        elif "label" in issue.casefold() or "target" in issue.casefold():
            code = "UNSTRUCTURED_TARGET_REQUIREMENT"
            path, action = (
                "data_requirements.target_rule",
                "formalize the missing target/label construction rule",
            )
        elif "treatment" in issue.casefold() or "baseline" in issue.casefold():
            code = "UNSTRUCTURED_ARM_REQUIREMENT"
            path, action = (
                "implementation_requirements",
                "formalize both algorithms and their single allowed difference",
            )
        else:
            code = code if code and len(code) < 80 else "UNCLASSIFIED_BLOCKER"
            path, action = (
                "contract_review.required_fields",
                "resolve this blocker without changing the historical contract",
            )
        proposals.append(
            AmendmentFieldProposal(
                issue_code=code,
                field_path=path,
                proposed_action=action,
            )
        )
    return ContractAmendmentProposal(
        proposal_id=stable_id(
            "contract-amendment-proposal",
            contract.study_id,
            contract.version,
            source_phase,
            *normalized,
        ),
        study_id=contract.study_id,
        predecessor_contract_version=contract.version,
        proposed_contract_version=contract.version + 1,
        source_phase=source_phase,
        issues=normalized,
        field_proposals=proposals,
    )


__all__ = [
    "AmendmentFieldProposal",
    "AlgorithmSpecification",
    "AnalysisSpecification",
    "BlockingIssueReport",
    "ContractAmendmentProposal",
    "ContractCompileReport",
    "ContractDryRunReport",
    "DatasetSpecification",
    "EvaluationSpecification",
    "ExecutableRunDAG",
    "RunSpecification",
    "blocking_issue_report",
    "compile_research_contract",
    "contract_amendment_proposal",
    "dry_run_compiled_contract",
]
