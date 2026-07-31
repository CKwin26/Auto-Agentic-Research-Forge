"""Contract checks for replaying an existing Python experiment project."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..experiment_execution import ExperimentManifest, experiment_for_action
from ..storage import read_json, sha256_file
from ..workflow_domain import ResearchContractVersion
from .contracts import ExistingPythonProjectParameters


def validate_existing_python_project_contract(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
) -> list[str]:
    violations: list[str] = []
    try:
        parameters = ExistingPythonProjectParameters.model_validate(
            contract.profile_parameters
        )
    except ValidationError as exc:
        return [
            "profile_parameters."
            + ".".join(map(str, item["loc"]))
            + ": "
            + item["msg"]
            for item in exc.errors()
        ]
    if not contract.tasks or not contract.seeds:
        violations.append(
            "existing_python_project_v1 requires tasks and seeds"
        )
    if len(contract.hypotheses) != 1:
        violations.append(
            "existing_python_project_v1 requires one primary hypothesis"
        )
    metric_names = [str(item.get("name") or "") for item in contract.metrics]
    if metric_names != parameters.metric_names:
        violations.append(
            "contract metrics must exactly match the frozen parser fields"
        )
    for metric in contract.metrics:
        if not str(metric.get("formula") or "").strip():
            violations.append(
                f"metric {metric.get('name')} requires a formula"
            )
        if not str(metric.get("direction") or "").strip():
            violations.append(
                f"metric {metric.get('name')} requires a direction"
            )
        if not str(metric.get("denominator") or "").strip():
            violations.append(
                f"metric {metric.get('name')} requires a denominator"
            )
    schema = contract.output_schema
    expected_schema = {
        "format": "json",
        "metric_field": parameters.primary_metric,
        "denominator_field": "denominator",
        "sample_id_field": "sample_ids",
        "analysis_rows_field": "analysis_rows",
        "record_layout": "summary_with_analysis_rows",
    }
    for field, expected in expected_schema.items():
        if schema.get(field) != expected:
            violations.append(f"output_schema.{field} must be {expected}")
    if contract.statistical_rules.get("method") != "paired_run_difference":
        violations.append(
            "existing_python_project_v1 method must be paired_run_difference"
        )
    if not isinstance(
        contract.statistical_rules.get("effect_threshold"), (int, float)
    ) or isinstance(
        contract.statistical_rules.get("effect_threshold"), bool
    ):
        violations.append("effect_threshold must be numeric")
    expected_commands = {
        "baseline": parameters.baseline_command,
        "treatment": parameters.treatment_command,
    }
    for arm_id, binding in (
        ("baseline", contract.baseline),
        ("treatment", contract.treatment),
    ):
        action_id = str(binding.get("action_id") or "")
        experiment_id = str(binding.get("experiment_id") or "")
        spec = experiment_for_action(manifest, action_id) if action_id else None
        if spec is None or spec.experiment_id != experiment_id:
            violations.append(
                f"{arm_id} binding does not match Experiment Manifest"
            )
            continue
        if spec.execution_backend != "isolated_candidate_evaluator":
            violations.append(f"{arm_id} must run in an isolated container")
        if spec.container_image != parameters.container_image:
            violations.append(f"{arm_id} container image differs from contract")
        if spec.command != expected_commands[arm_id]:
            violations.append(f"{arm_id} command differs from contract")
        if spec.network_access:
            violations.append(f"{arm_id} formal replay must be offline")
        if spec.required_inputs != [parameters.candidate_data_path]:
            violations.append(f"{arm_id} candidate data binding differs")
        if spec.evaluator_required_inputs != [
            parameters.evaluator_target_path
        ]:
            violations.append(f"{arm_id} evaluator target binding differs")
        if set(spec.required_inputs).intersection(
            spec.evaluator_required_inputs
        ):
            violations.append(f"{arm_id} candidate can access formal targets")
        if not set(parameters.project_code_paths).issubset(
            set(spec.candidate_code_paths)
        ):
            violations.append(f"{arm_id} does not freeze all project code")
        if spec.evaluator_command != parameters.evaluator_command:
            violations.append(f"{arm_id} evaluator command differs")
        if not spec.evaluator_code_paths:
            violations.append(f"{arm_id} evaluator code is not frozen")
        if spec.prediction_artifact_path != (
            parameters.prediction_artifact_path
        ):
            violations.append(f"{arm_id} prediction artifact path differs")
        artifact_paths = {item.path for item in spec.artifacts}
        if parameters.result_artifact_path not in artifact_paths:
            violations.append(f"{arm_id} result artifact is not declared")
        if not spec.smoke_command or not spec.smoke_required_inputs:
            violations.append(f"{arm_id} engineering smoke is not declared")
    return violations


def parse_existing_project_result(
    path: str | Path,
    parameters: ExistingPythonProjectParameters,
) -> dict[str, Any]:
    payload = read_json(Path(path))
    if not isinstance(payload, dict):
        raise ValueError("existing project result must be a JSON object")
    missing = [
        field
        for field in [
            *parameters.metric_names,
            "denominator",
            "sample_ids",
            "analysis_rows",
        ]
        if field not in payload
    ]
    if missing:
        raise ValueError("result is missing fields: " + ", ".join(missing))
    metrics: dict[str, float] = {}
    for field in parameters.metric_names:
        value = float(payload[field])
        if not math.isfinite(value):
            raise ValueError(f"metric {field} must be finite")
        metrics[field] = value
    denominator = int(payload["denominator"])
    sample_ids = [str(item) for item in payload["sample_ids"]]
    if denominator < 1 or denominator != len(sample_ids):
        raise ValueError("denominator must equal sample_ids length")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_ids must be unique")
    analysis_rows = payload["analysis_rows"]
    if not isinstance(analysis_rows, list) or any(
        not isinstance(item, dict) for item in analysis_rows
    ):
        raise ValueError("analysis_rows must be a list of objects")
    return {
        "valid": True,
        "metrics": metrics,
        "primary_metric": parameters.primary_metric,
        "primary_metric_value": metrics[parameters.primary_metric],
        "denominator": denominator,
        "sample_ids": sample_ids,
        "analysis_rows": analysis_rows,
        "result_sha256": sha256_file(Path(path)),
    }


def collect_frozen_project_files(
    root: str | Path,
    relative_paths: list[str],
) -> list[dict[str, Any]]:
    root_path = Path(root).resolve()
    records: list[dict[str, Any]] = []
    for relative in relative_paths:
        path = (root_path / relative).resolve()
        path.relative_to(root_path)
        if not path.is_file():
            raise ValueError(f"declared project file is unavailable: {relative}")
        records.append(
            {
                "path": Path(relative).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return records


__all__ = [
    "collect_frozen_project_files",
    "parse_existing_project_result",
    "validate_existing_python_project_contract",
]
