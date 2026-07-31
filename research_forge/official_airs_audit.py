"""Deterministic acceptance audit for locally executed official AIRS RAD runs."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from .storage import sha256_file


_TASK_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,199}$")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path.name}")
    return value


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"ledger line {number} is not an object")
            rows.append(value)
    return rows


def _metric_from_stdout(value: str) -> tuple[str, float]:
    matches = re.findall(
        r'"([^"\\]+)"\s*:\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)',
        value,
    )
    if len(matches) != 1:
        raise ValueError("official evaluator output must contain exactly one metric")
    name, raw = matches[0]
    metric = float(raw)
    if not math.isfinite(metric):
        raise ValueError("official evaluator metric must be finite")
    return name, metric


def _submission_shape(path: Path) -> tuple[int, int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2 or not rows[0]:
        raise ValueError("submission has no header or data")
    width = len(rows[0])
    if any(len(row) != width for row in rows[1:]):
        raise ValueError("submission has inconsistent row widths")
    return len(rows) - 1, width


def _docker_image_id(image: str) -> str | None:
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _audit_candidate_boundary(
    attempt: dict[str, Any], evaluator_root: Path
) -> list[str]:
    errors: list[str] = []
    if attempt.get("status") != "valid_submission":
        errors.append("candidate status is not valid_submission")
    execution = attempt.get("program_execution")
    if isinstance(execution, dict):
        command = [str(item) for item in execution.get("command", [])]
        joined = " ".join(command)
        if "--network none" not in joined:
            errors.append("candidate program was not network disabled")
        if "--read-only" not in command:
            errors.append("candidate program root filesystem was not read-only")
        if str(evaluator_root) in joined:
            errors.append("candidate command contains protected evaluator path")
        if int(execution.get("returncode", -1)) != 0:
            errors.append("candidate program execution failed")
    elif not isinstance(attempt.get("fallback"), dict):
        errors.append("candidate attempt lacks isolated execution or bounded fallback evidence")
    return errors


def audit_official_airs_matrix(
    imported_task_root: str | Path,
    run_root: str | Path,
    *,
    ledger_path: str | Path | None = None,
    min_tasks: int = 3,
    min_completed_seeds_per_task: int = 1,
) -> dict[str, Any]:
    """Verify imported sources, candidate separation and official scores."""

    imported = Path(imported_task_root).resolve()
    runs = Path(run_root).resolve()
    ledger = (
        Path(ledger_path).resolve()
        if ledger_path is not None
        else runs / "official-matrix-ledger.jsonl"
    )
    rows = _read_ledger(ledger)
    completed = [item for item in rows if item.get("status") == "completed"]
    errors: list[str] = []
    seen: set[tuple[str, int]] = set()
    cells: list[dict[str, Any]] = []
    image_ids: dict[str, str | None] = {}
    for row in completed:
        task = str(row.get("task") or "")
        seed = int(row.get("seed", -1))
        key = (task, seed)
        prefix = f"{task}/seed-{seed}"
        cell_errors: list[str] = []
        if not _TASK_PATTERN.fullmatch(task):
            cell_errors.append("invalid task name")
        if seed < 0:
            cell_errors.append("invalid seed")
        if key in seen:
            cell_errors.append("duplicate completed task/seed cell")
        seen.add(key)
        task_root = imported / task
        official_manifest_path = task_root / "official_adapter_manifest.json"
        run_manifest_path = runs / task / f"seed-{seed}" / "run-manifest.json"
        attempt_path = runs / task / f"seed-{seed}" / "agent-log" / "candidate_attempt.json"
        try:
            official = _read_json(official_manifest_path)
            run = _read_json(run_manifest_path)
            attempt = _read_json(attempt_path)
            for relative, expected in official.get("source_files_sha256", {}).items():
                path = task_root / relative
                if not path.is_file() or sha256_file(path) != expected:
                    cell_errors.append(f"official source drift: {relative}")
            if run.get("official_adapter_manifest_sha256") != sha256_file(
                official_manifest_path
            ):
                cell_errors.append("official adapter manifest binding differs")
            if run.get("evaluation_prepared") is not True:
                cell_errors.append("official evaluation was not prepared")
            if run.get("leaderboard_status") != "not_submitted":
                cell_errors.append("local run falsely claims leaderboard submission")
            if run.get("execution") != "docker":
                cell_errors.append("official evaluator did not run in Docker")
            image = str(run.get("docker_image") or "")
            if not image:
                cell_errors.append("official evaluator image is missing")
            elif image not in image_ids:
                image_ids[image] = _docker_image_id(image)
            for stage in ("prepare", "evaluate_prepare", "evaluate"):
                if int((run.get(stage) or {}).get("returncode", -1)) != 0:
                    cell_errors.append(f"official {stage} failed")
            agent_data = Path(str(run["agent_data_mount"])).resolve()
            evaluator_data = Path(str(run["evaluator_data_mount"])).resolve()
            if (
                agent_data == evaluator_data
                or agent_data in evaluator_data.parents
                or evaluator_data in agent_data.parents
            ):
                cell_errors.append("candidate and evaluator data boundaries overlap")
            cell_errors.extend(_audit_candidate_boundary(attempt, evaluator_data))
            submission = Path(str(run["agent_log_dir"])) / "submission.csv"
            observed_rows, observed_columns = _submission_shape(submission)
            registered_shape = run.get("submission_validation") or {}
            if observed_rows != int(registered_shape.get("row_count", -1)):
                cell_errors.append("submission row count differs from run manifest")
            if observed_columns != int(registered_shape.get("column_count", -1)):
                cell_errors.append("submission column count differs from run manifest")
            metric_name, metric_value = _metric_from_stdout(
                str((run.get("evaluate") or {}).get("stdout") or "")
            )
            ledger_metric_name, ledger_metric_value = _metric_from_stdout(
                str(row.get("metric_stdout") or "")
            )
            if (metric_name, metric_value) != (
                ledger_metric_name,
                ledger_metric_value,
            ):
                cell_errors.append("ledger metric differs from official run manifest")
            cells.append(
                {
                    "task": task,
                    "seed": seed,
                    "metric_name": metric_name,
                    "metric_value": metric_value,
                    "submission_rows": observed_rows,
                    "submission_sha256": sha256_file(submission),
                    "candidate_backend": attempt.get("backend"),
                    "official_source_manifest_sha256": sha256_file(
                        official_manifest_path
                    ),
                    "errors": cell_errors,
                }
            )
        except Exception as exc:
            cell_errors.append(f"{type(exc).__name__}: {exc}")
            cells.append({"task": task, "seed": seed, "errors": cell_errors})
        errors.extend(f"{prefix}: {item}" for item in cell_errors)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        by_task[cell["task"]].append(cell)
    task_summaries = []
    for task, task_cells in sorted(by_task.items()):
        values = [float(item["metric_value"]) for item in task_cells if "metric_value" in item]
        names = {str(item["metric_name"]) for item in task_cells if "metric_name" in item}
        task_summaries.append(
            {
                "task": task,
                "completed_cells": len(task_cells),
                "metric_name": next(iter(names)) if len(names) == 1 else None,
                "metric_min": min(values) if values else None,
                "metric_max": max(values) if values else None,
                "all_cells_valid": not any(item["errors"] for item in task_cells),
            }
        )
        if len(task_cells) < min_completed_seeds_per_task:
            errors.append(f"{task}: insufficient completed seeds")
    if len(by_task) < min_tasks:
        errors.append(f"only {len(by_task)} tasks completed; {min_tasks} required")
    if len(completed) != len(rows):
        errors.append("ledger includes non-completed cells")
    return {
        "schema_version": 1,
        "status": "verified" if not errors else "failed",
        "official_protocol": "AIRS RAD prepare/evaluate_prepare/evaluate",
        "leaderboard_submission_claimed": False,
        "task_count": len(by_task),
        "completed_cell_count": len(completed),
        "min_completed_seeds_per_task": min_completed_seeds_per_task,
        "task_summaries": task_summaries,
        "docker_images": [
            {"image": image, "image_id": image_id}
            for image, image_id in sorted(image_ids.items())
        ],
        "cells": cells,
        "errors": errors,
        "audit_sha256": _audit_hash(cells, task_summaries, image_ids),
    }


def _audit_hash(
    cells: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    image_ids: dict[str, str | None],
) -> str:
    payload = json.dumps(
        {"cells": cells, "task_summaries": summaries, "image_ids": image_ids},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["audit_official_airs_matrix"]
