"""Deterministic contract validators owned by Experiment Profiles.

Keeping these checks beside the Profile SDK prevents the Stage 3 orchestrator
from becoming the hidden definition of every scientific experiment family.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from ..experiment_execution import ExperimentManifest, experiment_for_action
from ..workflow_domain import ResearchContractVersion, Stage3Profile
from .contracts import (
    PairedBinaryClusteredParameters,
    PairedBinaryIndependentParameters,
    PairedContinuousV2Parameters,
    PairedMultiArmParameters,
    TabularMLParameters,
)


def action_binding(
    contract: ResearchContractVersion, arm_id: str
) -> tuple[str | None, str | None]:
    if arm_id == "baseline":
        binding = contract.baseline
    elif arm_id == "treatment":
        binding = contract.treatment
    else:
        arm_bindings = contract.implementation_requirements.get("arms", [])
        if isinstance(arm_bindings, dict):
            candidate = arm_bindings.get(arm_id, {})
            binding = candidate if isinstance(candidate, dict) else {}
        else:
            binding = next(
                (
                    item
                    for item in arm_bindings
                    if isinstance(item, dict)
                    and str(item.get("arm_id") or "") == arm_id
                ),
                {},
            )
    experiment_id = str(binding.get("experiment_id") or "").strip() or None
    action_id = str(binding.get("action_id") or "").strip() or None
    return experiment_id, action_id


def _parameter_violations(
    model: type[BaseModel], parameters: dict[str, Any]
) -> list[str]:
    try:
        model.model_validate(parameters)
    except ValidationError as exc:
        return [
            "profile_parameters."
            + ".".join(map(str, item["loc"]))
            + ": "
            + item["msg"]
            for item in exc.errors()
        ]
    return []


def _validate_action_bindings(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
    arms: list[str],
) -> list[str]:
    violations: list[str] = []
    for arm_id in arms:
        experiment_id, action_id = action_binding(contract, arm_id)
        if not experiment_id:
            violations.append(f"{arm_id}.experiment_id is required")
        if not action_id:
            violations.append(f"{arm_id}.action_id is required")
            continue
        try:
            spec = experiment_for_action(manifest, action_id)
        except ValueError as exc:
            violations.append(str(exc))
            continue
        if spec is None:
            violations.append(
                f"manifest has no experiment for {arm_id} action {action_id}"
            )
        elif experiment_id and spec.experiment_id != experiment_id:
            violations.append(
                f"{arm_id}.experiment_id does not match the manifest"
            )
    return violations


def _validate_common_result_schema(
    contract: ResearchContractVersion,
    *,
    record_layout: str,
) -> list[str]:
    violations: list[str] = []
    schema = contract.output_schema
    if schema.get("format") != "json":
        violations.append("Profile requires JSON result output")
    if schema.get("record_layout") != record_layout:
        violations.append(f"record_layout must be {record_layout}")
    required = ["metric_field", "denominator_field", "sample_id_field"]
    if record_layout == "summary_with_analysis_rows":
        required.append("analysis_rows_field")
    for field in required:
        if not str(schema.get(field) or "").strip():
            violations.append(f"output_schema.{field} is required")
    return violations


def _validate_common_matrix(
    contract: ResearchContractVersion,
    *,
    require_splits: bool,
) -> list[str]:
    violations: list[str] = []
    if not contract.tasks:
        violations.append("Profile requires at least one task")
    if require_splits and not contract.splits:
        violations.append("Profile requires at least one split")
    if not contract.seeds:
        violations.append("Profile requires at least one seed")
    if contract.replicates < 1:
        violations.append("Profile requires at least one replicate")
    if len(contract.hypotheses) != 1:
        violations.append("Profile requires exactly one primary hypothesis")
    return violations


def _validate_statistical_rules(
    contract: ResearchContractVersion, expected_method: str
) -> list[str]:
    violations: list[str] = []
    rules = contract.statistical_rules
    if rules.get("method") != expected_method:
        violations.append(
            "statistical_rules.method must match the frozen Profile: "
            + expected_method
        )
    threshold = rules.get("effect_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        violations.append("statistical_rules.effect_threshold must be numeric")
    if rules.get("missing_cell_policy") not in {
        "inconclusive",
        "disqualify",
    }:
        violations.append(
            "missing_cell_policy must be inconclusive or disqualify"
        )
    return violations


def validate_legacy_paired_contract(
    contract: ResearchContractVersion, manifest: ExperimentManifest
) -> list[str]:
    violations = _validate_common_matrix(contract, require_splits=True)
    if len(contract.metrics) != 1:
        violations.append("Profile requires exactly one primary metric")
    else:
        metric = contract.metrics[0]
        if metric.get("direction") not in {
            "higher_is_better",
            "lower_is_better",
            "maximize",
            "minimize",
        }:
            violations.append("primary metric direction is unsupported")
        if not str(metric.get("denominator") or "").strip():
            violations.append("primary metric denominator is missing")
    schema = contract.output_schema
    if schema.get("format") not in {"json", "csv"}:
        violations.append("output_schema.format must be json or csv")
    if schema.get("record_layout", "summary") != "summary":
        violations.append("legacy paired Profile supports summary layout only")
    for field in ("metric_field", "denominator_field", "sample_id_field"):
        if not str(schema.get(field) or "").strip():
            violations.append(f"output_schema.{field} is required")
    violations.extend(
        _validate_statistical_rules(contract, "paired_mean_difference")
    )
    if contract.statistical_rules.get("confidence_level", 0.95) != 0.95:
        violations.append("legacy paired Profile requires 0.95 confidence")
    violations.extend(
        _validate_action_bindings(contract, manifest, ["baseline", "treatment"])
    )
    return violations


def validate_modern_paired_contract(
    contract: ResearchContractVersion, manifest: ExperimentManifest
) -> list[str]:
    parameter_models: dict[Stage3Profile, type[BaseModel]] = {
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2: (
            PairedContinuousV2Parameters
        ),
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1: (
            PairedBinaryIndependentParameters
        ),
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1: (
            PairedBinaryClusteredParameters
        ),
    }
    expected_methods = {
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2: (
            "cluster_bootstrap_paired_mean"
        ),
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1: "exact_mcnemar",
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1: (
            "cluster_bootstrap_paired_binary"
        ),
    }
    violations = _validate_common_matrix(contract, require_splits=True)
    if len(contract.metrics) != 1:
        violations.append("paired Profile requires exactly one primary metric")
    violations.extend(
        _validate_common_result_schema(
            contract, record_layout="summary_with_analysis_rows"
        )
    )
    model = parameter_models.get(contract.experiment_profile)
    method = expected_methods.get(contract.experiment_profile)
    if model is None or method is None:
        violations.append("no modern paired Profile schema is registered")
    else:
        violations.extend(
            _parameter_violations(model, contract.profile_parameters)
        )
        violations.extend(_validate_statistical_rules(contract, method))
    violations.extend(
        _validate_action_bindings(contract, manifest, ["baseline", "treatment"])
    )
    return violations


def validate_multi_arm_contract(
    contract: ResearchContractVersion, manifest: ExperimentManifest
) -> list[str]:
    violations = _validate_common_matrix(contract, require_splits=True)
    if not contract.metrics:
        violations.append("multi-arm Profile requires a primary metric")
    violations.extend(
        _validate_common_result_schema(
            contract, record_layout="summary_with_analysis_rows"
        )
    )
    violations.extend(
        _validate_statistical_rules(
            contract, "cluster_bootstrap_multi_arm_conjunction"
        )
    )
    parameter_errors = _parameter_violations(
        PairedMultiArmParameters, contract.profile_parameters
    )
    violations.extend(parameter_errors)
    if not parameter_errors:
        parameters = PairedMultiArmParameters.model_validate(
            contract.profile_parameters
        )
        violations.extend(
            _validate_action_bindings(contract, manifest, parameters.arms)
        )
    return violations


def validate_tabular_ml_contract(
    contract: ResearchContractVersion, manifest: ExperimentManifest
) -> list[str]:
    violations = _parameter_violations(
        TabularMLParameters, contract.profile_parameters
    )
    if violations:
        return violations
    parameters = TabularMLParameters.model_validate(contract.profile_parameters)
    violations.extend(_validate_common_matrix(contract, require_splits=False))
    metric_names = [str(item.get("name") or "") for item in contract.metrics]
    if metric_names != parameters.metrics:
        violations.append(
            "contract metrics must exactly match profile_parameters.metrics"
        )
    for metric in contract.metrics:
        name = str(metric.get("name") or "")
        expected = (
            "lower_is_better" if name in {"rmse", "mae"}
            else "higher_is_better"
        )
        if metric.get("direction") != expected:
            violations.append(f"metric {name} direction must be {expected}")
        if not str(metric.get("formula") or "").strip():
            violations.append(f"metric {name} requires an executable formula")
        if not str(metric.get("denominator") or "").strip():
            violations.append(f"metric {name} requires a denominator")
    violations.extend(
        _validate_common_result_schema(
            contract, record_layout="summary_with_analysis_rows"
        )
    )
    schema = contract.output_schema
    expected_schema = {
        "metric_field": parameters.primary_metric,
        "denominator_field": "denominator",
        "sample_id_field": "sample_ids",
        "analysis_rows_field": "analysis_rows",
    }
    for field, expected in expected_schema.items():
        if schema.get(field) != expected:
            violations.append(f"output_schema.{field} must be {expected}")
    violations.extend(
        _validate_statistical_rules(contract, "paired_run_difference")
    )
    for arm_id in ("baseline", "treatment"):
        experiment_id, action_id = action_binding(contract, arm_id)
        if not experiment_id or not action_id:
            violations.append(f"{arm_id} action binding is incomplete")
            continue
        try:
            spec = experiment_for_action(manifest, action_id)
        except ValueError as exc:
            violations.append(str(exc))
            continue
        if spec is None or spec.experiment_id != experiment_id:
            violations.append(
                f"{arm_id} binding does not match Experiment Manifest"
            )
            continue
        if spec.execution_backend == "isolated_candidate_evaluator":
            if parameters.dataset_path in spec.required_inputs:
                violations.append(
                    f"{arm_id} candidate input exposes formal targets"
                )
            if len(spec.required_inputs) != 1:
                violations.append(
                    f"{arm_id} requires one target-free candidate data file"
                )
            if len(spec.evaluator_required_inputs) != 1:
                violations.append(
                    f"{arm_id} requires one evaluator-only target file"
                )
            if set(spec.required_inputs).intersection(
                spec.evaluator_required_inputs
            ):
                violations.append(
                    f"{arm_id} candidate and evaluator inputs overlap"
                )
        elif parameters.dataset_path not in spec.required_inputs:
            violations.append(
                f"{arm_id} experiment does not freeze the dataset input"
            )
    return violations


__all__ = [
    "action_binding",
    "validate_legacy_paired_contract",
    "validate_modern_paired_contract",
    "validate_multi_arm_contract",
    "validate_tabular_ml_contract",
]
