"""Deterministic point-in-time backtest kernel for Profile development.

This is intentionally narrower than a trading platform.  It evaluates frozen
cross-sectional signals against evaluator-only realized returns.  Raw-price
return construction, exchange calendars and corporate-action ingestion remain
outside the certified boundary until dedicated adapters are implemented.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..experiment_execution import (
    ExperimentArtifactSpec,
    ExperimentManifest,
    ExperimentSpec,
    experiment_for_action,
)
from ..models import StrictModel
from ..storage import safe_relative, write_json_atomic
from ..workflow_domain import ResearchContractVersion
from .contracts import TimeSeriesBacktestParameters


class BacktestPeriodRow(StrictModel):
    timestamp: str
    selected_asset_ids: list[str] = Field(min_length=1)
    gross_return: float
    net_return: float


class TimeSeriesBacktestResult(StrictModel):
    schema_version: int = 1
    profile_id: Literal["time_series_backtest_v1"] = (
        "time_series_backtest_v1"
    )
    arm: Literal["baseline", "treatment"]
    rows: list[BacktestPeriodRow] = Field(min_length=1)
    denominator: int = Field(ge=1)
    mean_net_portfolio_return: float

    @model_validator(mode="after")
    def result_is_self_consistent(self) -> "TimeSeriesBacktestResult":
        if len(self.rows) != self.denominator:
            raise ValueError("period count must equal denominator")
        if len({row.timestamp for row in self.rows}) != len(self.rows):
            raise ValueError("period timestamps must be unique")
        recomputed = sum(row.net_return for row in self.rows) / len(self.rows)
        if not math.isclose(
            recomputed,
            self.mean_net_portfolio_return,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("mean net return does not match period rows")
        return self


class TimeSeriesBacktestEvaluation(StrictModel):
    metric: Literal["mean_net_portfolio_return"] = (
        "mean_net_portfolio_return"
    )
    baseline_value: float
    treatment_value: float
    effect: float
    threshold: float
    denominator: int = Field(ge=1)
    verdict: Literal["supported", "refuted", "inconclusive"]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"backtest input is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"backtest input is empty: {path}")
    return rows


def _require_columns(
    rows: list[dict[str, str]], required: set[str], label: str
) -> None:
    missing = sorted(required.difference(rows[0]))
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def _parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"eligibility value is not boolean: {value}")


def _validate_timestamp(value: str) -> str:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"timestamp is not ISO-8601: {value}") from exc
    return value


def run_time_series_backtest(
    parameters: TimeSeriesBacktestParameters,
    *,
    arm: Literal["baseline", "treatment"],
    root: str | Path,
) -> TimeSeriesBacktestResult:
    root_path = Path(root).resolve()
    candidate_rows = _read_csv(
        safe_relative(root_path, parameters.candidate_input_path)
    )
    target_rows = _read_csv(
        safe_relative(root_path, parameters.evaluator_target_path)
    )
    _require_columns(
        candidate_rows,
        {
            parameters.timestamp_field,
            parameters.feature_as_of_field,
            parameters.asset_id_field,
            parameters.eligibility_field,
            parameters.baseline_signal_field,
            parameters.treatment_signal_field,
        },
        "candidate input",
    )
    if parameters.target_return_field in candidate_rows[0]:
        raise ValueError("candidate input must not contain the target-return column")
    _require_columns(
        target_rows,
        {
            parameters.timestamp_field,
            parameters.asset_id_field,
            parameters.target_return_field,
        },
        "evaluator targets",
    )
    targets: dict[tuple[str, str], float] = {}
    for row in target_rows:
        timestamp = _validate_timestamp(row[parameters.timestamp_field])
        asset_id = row[parameters.asset_id_field].strip()
        key = (timestamp, asset_id)
        if not asset_id or key in targets:
            raise ValueError("evaluator target keys must be non-empty and unique")
        targets[key] = float(row[parameters.target_return_field])

    by_period: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    signal_field = (
        parameters.baseline_signal_field
        if arm == "baseline"
        else parameters.treatment_signal_field
    )
    for row in candidate_rows:
        timestamp = _validate_timestamp(row[parameters.timestamp_field])
        feature_as_of = _validate_timestamp(row[parameters.feature_as_of_field])
        if feature_as_of > timestamp:
            raise ValueError(
                "candidate feature timestamp follows the decision timestamp"
            )
        if not (parameters.evaluation_start <= timestamp <= parameters.evaluation_end):
            continue
        asset_id = row[parameters.asset_id_field].strip()
        key = (timestamp, asset_id)
        if not asset_id or key in seen:
            raise ValueError("candidate signal keys must be non-empty and unique")
        seen.add(key)
        if not _parse_bool(row[parameters.eligibility_field]):
            continue
        if key not in targets:
            raise ValueError(f"eligible candidate has no frozen target: {key}")
        by_period[timestamp].append(
            (asset_id, float(row[signal_field]), targets[key])
        )
    if not by_period:
        raise ValueError("evaluation window contains no eligible periods")

    round_trip_cost = (
        parameters.fee_bps_per_round_trip
        + parameters.slippage_bps_per_round_trip
    ) / 10_000.0
    period_rows: list[BacktestPeriodRow] = []
    for timestamp in sorted(by_period):
        candidates = by_period[timestamp]
        if len(candidates) < parameters.minimum_eligible_assets:
            raise ValueError(
                f"period {timestamp} has {len(candidates)} eligible assets; "
                f"requires {parameters.minimum_eligible_assets}"
            )
        ranked = sorted(candidates, key=lambda item: (-item[1], item[0]))
        selected = ranked[: parameters.top_k]
        gross = sum(item[2] for item in selected) / len(selected)
        period_rows.append(
            BacktestPeriodRow(
                timestamp=timestamp,
                selected_asset_ids=[item[0] for item in selected],
                gross_return=gross,
                net_return=gross - round_trip_cost,
            )
        )
    return TimeSeriesBacktestResult(
        arm=arm,
        rows=period_rows,
        denominator=len(period_rows),
        mean_net_portfolio_return=(
            sum(row.net_return for row in period_rows) / len(period_rows)
        ),
    )


def evaluate_time_series_backtest(
    baseline: TimeSeriesBacktestResult,
    treatment: TimeSeriesBacktestResult,
    parameters: TimeSeriesBacktestParameters,
) -> TimeSeriesBacktestEvaluation:
    baseline_periods = [row.timestamp for row in baseline.rows]
    treatment_periods = [row.timestamp for row in treatment.rows]
    if baseline_periods != treatment_periods:
        raise ValueError("baseline and treatment periods must pair exactly")
    effect = (
        treatment.mean_net_portfolio_return
        - baseline.mean_net_portfolio_return
    )
    tolerance = 1e-12
    verdict = (
        "supported"
        if effect > parameters.effect_threshold + tolerance
        else "refuted"
        if effect < parameters.effect_threshold - tolerance
        else "inconclusive"
    )
    return TimeSeriesBacktestEvaluation(
        baseline_value=baseline.mean_net_portfolio_return,
        treatment_value=treatment.mean_net_portfolio_return,
        effect=effect,
        threshold=parameters.effect_threshold,
        denominator=baseline.denominator,
        verdict=verdict,
    )


def validate_time_series_backtest_contract(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
) -> list[str]:
    """Validate executable two-arm semantics for the formal narrow Profile."""

    violations: list[str] = []
    try:
        parameters = TimeSeriesBacktestParameters.model_validate(
            contract.profile_parameters
        )
    except Exception as exc:
        return [f"profile_parameters: {exc}"]
    if not contract.tasks or not contract.splits or not contract.seeds:
        violations.append("time_series_backtest_v1 requires tasks, splits and seeds")
    if len(contract.hypotheses) != 1:
        violations.append(
            "time_series_backtest_v1 requires exactly one primary hypothesis"
        )
    metric_names = [str(item.get("name") or "") for item in contract.metrics]
    if metric_names != [parameters.primary_metric]:
        violations.append(
            "contract metric must exactly match mean_net_portfolio_return"
        )
    metric = contract.metrics[0] if contract.metrics else {}
    if metric.get("direction") not in {"higher_is_better", "maximize", "higher"}:
        violations.append("backtest primary metric must be higher_is_better")
    if contract.statistical_rules.get("method") != "paired_run_difference":
        violations.append("backtest method must be paired_run_difference")
    threshold = contract.statistical_rules.get("effect_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        violations.append("effect_threshold must be numeric")
    elif not math.isclose(
        float(threshold), parameters.effect_threshold, rel_tol=0.0, abs_tol=1e-12
    ):
        violations.append(
            "contract and Profile effect_threshold values must match exactly"
        )
    for arm, expected_action in (
        ("baseline", "action-time-series-backtest-baseline"),
        ("treatment", "action-time-series-backtest-treatment"),
    ):
        binding = contract.baseline if arm == "baseline" else contract.treatment
        if binding.get("action_id") != expected_action:
            violations.append(f"{arm}.action_id must be {expected_action}")
            continue
        experiment = experiment_for_action(manifest, expected_action)
        if experiment is None:
            violations.append(f"manifest has no experiment for {expected_action}")
        elif experiment.execution_backend != "isolated_candidate_evaluator":
            violations.append(f"{arm} must use isolated_candidate_evaluator")
    return violations


_CANDIDATE_SCRIPT = r'''import argparse, csv, json

def truthy(value):
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}: return True
    if normalized in {"0", "false", "no", "n"}: return False
    raise ValueError("eligibility is not boolean")

parser = argparse.ArgumentParser()
parser.add_argument("--signals", required=True)
parser.add_argument("--config", required=True)
parser.add_argument("--arm", choices=["baseline", "treatment"], required=True)
parser.add_argument("--prediction", required=True)
args = parser.parse_args()
config = json.load(open(args.config, encoding="utf-8"))
rows = list(csv.DictReader(open(args.signals, encoding="utf-8-sig", newline="")))
signal = config[args.arm + "_signal_field"]
by_period = {}
seen = set()
for row in rows:
    timestamp = row[config["timestamp_field"]]
    feature_as_of = row[config["feature_as_of_field"]]
    if feature_as_of > timestamp: raise ValueError("feature timestamp follows decision timestamp")
    if not (config["evaluation_start"] <= timestamp <= config["evaluation_end"]): continue
    asset = row[config["asset_id_field"]].strip()
    key = (timestamp, asset)
    if not asset or key in seen: raise ValueError("candidate keys must be unique")
    seen.add(key)
    if truthy(row[config["eligibility_field"]]):
        by_period.setdefault(timestamp, []).append((asset, float(row[signal])))
periods = []
for timestamp in sorted(by_period):
    candidates = by_period[timestamp]
    if len(candidates) < config["minimum_eligible_assets"]: raise ValueError("undersized eligible universe")
    ranked = sorted(candidates, key=lambda item: (-item[1], item[0]))
    periods.append({"timestamp": timestamp, "asset_ids": [item[0] for item in ranked[:config["top_k"]]]})
if not periods: raise ValueError("evaluation window has no eligible periods")
json.dump({"profile_id": "time_series_backtest_v1", "arm": args.arm, "periods": periods}, open(args.prediction, "w", encoding="utf-8"), sort_keys=True)
'''


_EVALUATOR_SCRIPT = r'''import argparse, csv, json
parser = argparse.ArgumentParser()
parser.add_argument("--prediction", required=True)
parser.add_argument("--targets", required=True)
parser.add_argument("--config", required=True)
parser.add_argument("--metrics", required=True)
args = parser.parse_args()
prediction = json.load(open(args.prediction, encoding="utf-8"))
config = json.load(open(args.config, encoding="utf-8"))
rows = list(csv.DictReader(open(args.targets, encoding="utf-8-sig", newline="")))
targets = {}
for row in rows:
    key = (row[config["timestamp_field"]], row[config["asset_id_field"]].strip())
    if not key[1] or key in targets: raise ValueError("target keys must be unique")
    targets[key] = float(row[config["target_return_field"]])
cost = (config["fee_bps_per_round_trip"] + config["slippage_bps_per_round_trip"]) / 10000.0
analysis = []
result_rows = []
seen_periods = set()
for period in prediction["periods"]:
    timestamp, assets = period["timestamp"], period["asset_ids"]
    if timestamp in seen_periods or len(assets) != config["top_k"]: raise ValueError("invalid portfolio submission")
    seen_periods.add(timestamp)
    missing = [asset for asset in assets if (timestamp, asset) not in targets]
    if missing: raise ValueError("selected assets have no evaluator targets")
    gross = sum(targets[(timestamp, asset)] for asset in assets) / len(assets)
    net = gross - cost
    analysis.append({"pair_id": timestamp, "value": net})
    result_rows.append({"timestamp": timestamp, "selected_asset_ids": assets, "gross_return": gross, "net_return": net})
mean = sum(row["value"] for row in analysis) / len(analysis)
output = {"profile_id": "time_series_backtest_v1", "arm": prediction["arm"], "primary_metric": "mean_net_portfolio_return", "primary_metric_value": mean, "mean_net_portfolio_return": mean, "denominator": len(analysis), "sample_ids": [row["pair_id"] for row in analysis], "analysis_rows": analysis, "rows": result_rows}
json.dump(output, open(args.metrics, "w", encoding="utf-8"), sort_keys=True)
'''


_SMOKE_SCRIPT = r'''import argparse, subprocess, sys
parser = argparse.ArgumentParser()
parser.add_argument("--candidate", required=True); parser.add_argument("--evaluator", required=True)
parser.add_argument("--signals", required=True); parser.add_argument("--targets", required=True)
parser.add_argument("--candidate-config", required=True); parser.add_argument("--evaluator-config", required=True)
parser.add_argument("--arm", required=True); parser.add_argument("--output", required=True)
args = parser.parse_args(); prediction = args.output + ".prediction"
subprocess.run([sys.executable, args.candidate, "--signals", args.signals, "--config", args.candidate_config, "--arm", args.arm, "--prediction", prediction], check=True)
subprocess.run([sys.executable, args.evaluator, "--prediction", prediction, "--targets", args.targets, "--config", args.evaluator_config, "--metrics", args.output], check=True)
'''


def materialize_isolated_time_series_backtest_package(
    parameters: TimeSeriesBacktestParameters,
    root: str | Path,
    *,
    container_image: str = "python:3.12-slim",
    manifest_path: str = ".research-forge/experiments.json",
) -> tuple[Path, Path]:
    """Create a target-isolated formal package with two paired arms."""

    root_path = Path(root).resolve()
    signals = safe_relative(root_path, parameters.candidate_input_path)
    targets = safe_relative(root_path, parameters.evaluator_target_path)
    if not signals.is_file() or not targets.is_file():
        raise ValueError("frozen signal and target CSV files are required")
    package = root_path / ".research-forge" / "time_series_backtest_v1"
    candidate_script = package / "candidate.py"
    evaluator_script = package / "evaluator.py"
    smoke_script = package / "smoke.py"
    candidate_config = package / "candidate-config.json"
    evaluator_config = package / "evaluator-config.json"
    package.mkdir(parents=True, exist_ok=True)
    candidate_script.write_text(_CANDIDATE_SCRIPT, encoding="utf-8")
    evaluator_script.write_text(_EVALUATOR_SCRIPT, encoding="utf-8")
    smoke_script.write_text(_SMOKE_SCRIPT, encoding="utf-8")
    candidate_fields = {
        key: value
        for key, value in parameters.model_dump(mode="json").items()
        if key not in {"evaluator_target_path", "target_return_field", "effect_threshold"}
    }
    evaluator_fields = {
        key: value
        for key, value in parameters.model_dump(mode="json").items()
        if key not in {"candidate_input_path", "baseline_signal_field", "treatment_signal_field", "eligibility_field"}
    }
    write_json_atomic(candidate_config, candidate_fields)
    write_json_atomic(evaluator_config, evaluator_fields)
    relative = lambda path: path.relative_to(root_path).as_posix()
    experiments: list[ExperimentSpec] = []
    for arm in ("baseline", "treatment"):
        experiments.append(
            ExperimentSpec(
                experiment_id=f"time-series-backtest-{arm}",
                action_ids=[f"action-time-series-backtest-{arm}"],
                title=f"target-isolated time-series backtest {arm}",
                command=[
                    "{python}", "/workspace/input/" + relative(candidate_script),
                    "--signals", "{data_file}", "--config", "/workspace/input/" + relative(candidate_config),
                    "--arm", arm, "--prediction", "{prediction_file}",
                ],
                smoke_command=[
                    "{python}", "/workspace/input/" + relative(smoke_script),
                    "--candidate", "/workspace/input/" + relative(candidate_script),
                    "--evaluator", "/workspace/input/" + relative(evaluator_script),
                    "--signals", "/workspace/input/" + relative(signals),
                    "--targets", "/workspace/input/" + relative(targets),
                    "--candidate-config", "/workspace/input/" + relative(candidate_config),
                    "--evaluator-config", "/workspace/input/" + relative(evaluator_config),
                    "--arm", arm, "--output", "{evidence_dir}/result.json",
                ],
                smoke_required_inputs=[relative(path) for path in (smoke_script, candidate_script, evaluator_script, signals, targets, candidate_config, evaluator_config)],
                execution_backend="isolated_candidate_evaluator",
                container_image=container_image,
                required_inputs=[relative(signals), relative(candidate_config)],
                candidate_code_paths=[relative(candidate_script)],
                evaluator_command=[
                    "{python}", "/workspace/input/" + relative(evaluator_script),
                    "--prediction", "{prediction_file}", "--targets", "{target_file}",
                    "--config", "/workspace/input/" + relative(evaluator_config),
                    "--metrics", "{metrics_file}",
                ],
                evaluator_code_paths=[relative(evaluator_script)],
                evaluator_required_inputs=[relative(targets), relative(evaluator_config)],
                candidate_data_path=relative(signals),
                evaluator_target_path=relative(targets),
                prediction_artifact_path="predictions.json",
                network_access=False,
                artifacts=[ExperimentArtifactSpec(path="result.json", format="json", required_keys=["mean_net_portfolio_return", "denominator", "sample_ids", "analysis_rows"])],
            )
        )
    manifest = root_path / manifest_path
    write_json_atomic(manifest, ExperimentManifest(experiments=experiments).model_dump(mode="json"))
    return package, manifest


__all__ = [
    "BacktestPeriodRow",
    "TimeSeriesBacktestEvaluation",
    "TimeSeriesBacktestResult",
    "evaluate_time_series_backtest",
    "materialize_isolated_time_series_backtest_package",
    "run_time_series_backtest",
    "validate_time_series_backtest_contract",
]
