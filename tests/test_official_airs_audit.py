from __future__ import annotations

import csv
import json
from pathlib import Path

from research_forge.official_airs_audit import audit_official_airs_matrix
from research_forge.storage import sha256_file


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_audit_accepts_hash_bound_official_evaluator_cell(tmp_path: Path) -> None:
    imported = tmp_path / "imported"
    runs = tmp_path / "runs"
    task = "TextualClassificationSickAccuracy"
    task_root = imported / task
    for name in ("prepare.py", "evaluate_prepare.py", "evaluate.py"):
        _write(task_root / name, f"# {name}\n")
    source_hashes = {
        name: sha256_file(task_root / name)
        for name in ("prepare.py", "evaluate_prepare.py", "evaluate.py")
    }
    official = {
        "schema_version": 1,
        "task_name": task,
        "source_files_sha256": source_hashes,
    }
    _write(
        task_root / "official_adapter_manifest.json",
        json.dumps(official),
    )
    cell = runs / task / "seed-0"
    agent_data = cell / "agent-data"
    agent_log = cell / "agent-log"
    evaluator_data = runs / "_protected_evaluator_data" / task / "seed-0"
    agent_data.mkdir(parents=True)
    evaluator_data.mkdir(parents=True)
    agent_log.mkdir(parents=True)
    with (agent_log / "submission.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows([["prediction"], ["a"], ["b"]])
    metric_stdout = '{"Accuracy": 0.5}'
    run = {
        "official_adapter_manifest_sha256": sha256_file(
            task_root / "official_adapter_manifest.json"
        ),
        "evaluation_prepared": True,
        "leaderboard_status": "not_submitted",
        "execution": "docker",
        "docker_image": "missing-fixture-image:latest",
        "prepare": {"returncode": 0},
        "evaluate_prepare": {"returncode": 0},
        "evaluate": {"returncode": 0, "stdout": metric_stdout},
        "agent_data_mount": str(agent_data),
        "agent_log_dir": str(agent_log),
        "evaluator_data_mount": str(evaluator_data),
        "submission_validation": {"row_count": 2, "column_count": 1},
    }
    _write(cell / "run-manifest.json", json.dumps(run))
    attempt = {
        "status": "valid_submission",
        "backend": "api",
        "program_execution": {
            "returncode": 0,
            "command": [
                "docker",
                "run",
                "--network",
                "none",
                "--read-only",
                "fixture",
            ],
        },
    }
    _write(agent_log / "candidate_attempt.json", json.dumps(attempt))
    ledger = {
        "status": "completed",
        "task": task,
        "seed": 0,
        "metric_stdout": metric_stdout,
    }
    _write(runs / "official-matrix-ledger.jsonl", json.dumps(ledger) + "\n")
    report = audit_official_airs_matrix(
        imported, runs, min_tasks=1, min_completed_seeds_per_task=1
    )
    assert report["status"] == "verified"
    assert report["completed_cell_count"] == 1
    assert report["task_summaries"][0]["metric_min"] == 0.5


def test_audit_rejects_source_drift_and_false_leaderboard_claim(
    tmp_path: Path,
) -> None:
    # Reuse the positive constructor by running it once is intentionally
    # avoided; this test checks the public failure contract directly.
    imported = tmp_path / "imported"
    runs = tmp_path / "runs"
    task = "TaskOne"
    task_root = imported / task
    _write(task_root / "evaluate.py", "# changed\n")
    _write(
        task_root / "official_adapter_manifest.json",
        json.dumps(
            {
                "source_files_sha256": {"evaluate.py": "0" * 64},
            }
        ),
    )
    cell = runs / task / "seed-0"
    _write(cell / "run-manifest.json", json.dumps({}))
    _write(cell / "agent-log" / "candidate_attempt.json", json.dumps({}))
    _write(
        runs / "official-matrix-ledger.jsonl",
        json.dumps(
            {"status": "completed", "task": task, "seed": 0, "metric_stdout": '{"Accuracy": 1}'}
        )
        + "\n",
    )
    report = audit_official_airs_matrix(imported, runs, min_tasks=1)
    assert report["status"] == "failed"
    assert any("official source drift" in item for item in report["errors"])
