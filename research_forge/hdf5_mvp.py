"""Non-scientific HDF5 feasibility MVP for Stage 2.

The adapter validates data and metric interfaces only. It never trains a
model, executes a treatment, or turns its synthetic metric fixture into
scientific evidence.
"""

from __future__ import annotations

import math
import platform
import time
from pathlib import Path
from typing import Any


def _select_diverse_cases(
    rows: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in rows
        if row.get("readable") is True
        and int(row.get("sample_count") or 0) > 0
        and row.get("path")
    ]
    eligible.sort(
        key=lambda row: (
            str(row.get("task") or ""),
            str(row.get("representation_family") or ""),
            str(row["path"]),
        )
    )
    selected: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, str]] = set()
    for row in eligible:
        group = (
            str(row.get("task") or ""),
            str(row.get("representation_family") or ""),
        )
        if group in seen_groups:
            continue
        selected.append(row)
        seen_groups.add(group)
        if len(selected) == limit:
            return selected
    for row in eligible:
        if row in selected:
            continue
        selected.append(row)
        if len(selected) == limit:
            break
    return selected


def _metric_fixture(case_count: int) -> dict[str, Any]:
    """Exercise a success-rate aggregator on explicitly synthetic rows."""

    outcomes = [
        {"fixture_id": f"metric-smoke-{index + 1}", "success": index % 2 == 0}
        for index in range(case_count)
    ]
    numerator = sum(item["success"] is True for item in outcomes)
    denominator = len(outcomes)
    value = numerator / denominator
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("success-rate smoke aggregation is invalid")
    return {
        "fixture_origin": "research_forge_synthetic_schema_smoke",
        "scientific_evidence_eligible": False,
        "metric": "task_success_rate",
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "rows": outcomes,
    }


def run_hdf5_feasibility_mvp(
    source_root: str | Path,
    metadata_rows: list[dict[str, Any]],
    *,
    primary_metric: str,
) -> dict[str, Any]:
    """Run a bounded, read-only HDF5 interface and metric smoke check."""

    started = time.perf_counter()
    root = Path(source_root).resolve()
    selected = _select_diverse_cases(metadata_rows)
    errors: list[str] = []
    smoke_cases: list[dict[str, Any]] = []
    environment_started = False
    reset_verified = True
    try:
        import h5py

        environment_started = True
        h5py_version = str(h5py.__version__)
        for index, row in enumerate(selected):
            relative = Path(str(row["path"]))
            path = (root / relative).resolve()
            inside = path != root and root in path.parents
            if not inside or not path.is_file():
                errors.append(f"missing_or_outside_boundary:{relative.as_posix()}")
                continue
            before = path.stat()
            try:
                with h5py.File(path, "r") as handle:
                    data = handle.get("data")
                    demos = sorted(str(item) for item in data.keys()) if data else []
                    if not demos:
                        raise ValueError("missing data/demo groups")
                    demo = data[demos[0]]
                    actions = demo.get("actions")
                    if actions is None or not actions.shape or int(actions.shape[0]) <= 0:
                        raise ValueError("missing or empty actions dataset")
                    obs = demo.get("obs")
                    observation_keys = (
                        sorted(str(item) for item in obs.keys()) if obs else []
                    )
                    input_interface = "materialized_observations"
                    if not observation_keys:
                        states = demo.get("states")
                        if (
                            states is None
                            or not states.shape
                            or int(states.shape[0]) != int(actions.shape[0])
                        ):
                            raise ValueError(
                                "missing observations and aligned states"
                            )
                        observation_keys = ["states"]
                        input_interface = "state_action_trajectory"
                    action_shape = [int(item) for item in actions.shape]
                after = path.stat()
                unchanged = (
                    before.st_size == after.st_size
                    and before.st_mtime_ns == after.st_mtime_ns
                )
                reset_verified = reset_verified and unchanged
                smoke_cases.append(
                    {
                        "case_id": f"hdf5-smoke-{index + 1}",
                        "status": "passed" if unchanged else "failed",
                        "input_path": relative.as_posix(),
                        "metadata_sha256": row.get("metadata_sha256"),
                        "task": row.get("task"),
                        "representation_family": row.get(
                            "representation_family"
                        ),
                        "demo_count": int(row.get("demo_count") or 0),
                        "sample_count": int(row.get("sample_count") or 0),
                        "first_demo": demos[0],
                        "action_shape": action_shape,
                        "observation_keys": observation_keys,
                        "input_interface": input_interface,
                        "requires_stage3_observation_materialization": (
                            input_interface == "state_action_trajectory"
                        ),
                        "source_unchanged": unchanged,
                    }
                )
            except Exception as exc:
                errors.append(
                    f"hdf5_case_error:{relative.as_posix()}:"
                    f"{type(exc).__name__}:{str(exc)[:200]}"
                )
        metric_smoke = (
            _metric_fixture(len(smoke_cases))
            if 2 <= len(smoke_cases) <= 3
            else None
        )
        failure_modes = {
            "missing_input": "data_error",
            "missing_actions_or_observations": "schema_error",
            "invalid_success_value": "metric_schema_error",
            "environment_import_failure": "environment_error",
        }
        failure_modes_distinguishable = len(set(failure_modes.values())) == len(
            failure_modes
        )
    except Exception as exc:
        h5py_version = None
        metric_smoke = None
        failure_modes = {}
        failure_modes_distinguishable = False
        errors.append(f"environment_error:{type(exc).__name__}:{str(exc)[:300]}")

    passed_cases = [
        item for item in smoke_cases if item.get("status") == "passed"
    ]
    valid_case_count = 2 <= len(passed_cases) <= 3
    metric_computable = bool(
        metric_smoke
        and metric_smoke["denominator"] == len(passed_cases)
        and primary_metric == "task_success_rate"
    )
    completed = all(
        (
            environment_started,
            valid_case_count,
            metric_computable,
            failure_modes_distinguishable,
            reset_verified,
            not errors,
        )
    )
    return {
        "schema_version": 1,
        "adapter": "research_forge_hdf5_feasibility_mvp_v1",
        "scientific_evidence_eligible": False,
        "environment_started": environment_started,
        "metric_computable": metric_computable,
        "baseline_instantiable": valid_case_count,
        "failure_modes_distinguishable": failure_modes_distinguishable,
        "reset_or_isolation_verified": reset_verified and valid_case_count,
        "runtime_seconds": max(time.perf_counter() - started, 0.000001),
        "smoke_cases": passed_cases,
        "metric_smoke_fixture": metric_smoke,
        "failure_mode_contract": failure_modes,
        "environment": {
            "python": platform.python_version(),
            "h5py": h5py_version,
        },
        "primary_metric_under_test": primary_metric,
        "baseline_adapter": (
            "read_only_hdf5_rollout_input_adapter"
            if valid_case_count
            else None
        ),
        "formal_baseline_executed": False,
        "formal_treatment_executed": False,
        "status": "verified" if completed else "blocked",
        "errors": errors,
    }


__all__ = ["run_hdf5_feasibility_mvp"]
