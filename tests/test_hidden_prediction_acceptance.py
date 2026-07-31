from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from research_forge.benchmarks.hidden_prediction import (
    run_openml_hidden_prediction_acceptance,
)
from research_forge.container_execution import ContainerExecutionResult
from research_forge.storage import sha256_file


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _source(root: Path) -> Path:
    source = root / "source"
    dataset = source / "dataset.csv"
    rows = [
        {"sample_id": "a", "x": 0.0, "target": "Mine", "partition": "train"},
        {"sample_id": "b", "x": 0.1, "target": "Mine", "partition": "train"},
        {"sample_id": "c", "x": 0.9, "target": "Rock", "partition": "train"},
        {"sample_id": "d", "x": 1.0, "target": "Rock", "partition": "train"},
        {"sample_id": "e", "x": 0.05, "target": "Mine", "partition": "test"},
        {"sample_id": "f", "x": 0.95, "target": "Rock", "partition": "test"},
    ]
    _write_csv(dataset, ["sample_id", "x", "target", "partition"], rows)
    report = {
        "benchmark": "openml_tabular_ml_v1",
        "task_id": 999,
        "dataset": {
            "dataset_id": 999,
            "name": "fixture",
            "version": 1,
            "materialized_sha256": sha256_file(dataset),
        },
        "protocol": {
            "estimation_procedure": {"type": "fixed"},
            "profile_parameters": {
                "task_type": "classification",
                "id_field": "sample_id",
                "target_field": "target",
                "split_field": "partition",
                "feature_fields": ["x"],
                "train_values": ["train"],
                "test_values": ["test"],
                "positive_label": "Rock",
            },
        },
    }
    (source / "benchmark-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    return source


def _local_runner(command, *, input_dir, output_dir, policy):
    rewritten = [
        str(Path(output_dir) / "submission.csv")
        if item == "/workspace/output/submission.csv"
        else str(Path(output_dir) / "result.json")
        if item == "/workspace/output/result.json"
        else item
        for item in command
    ]
    text = (Path(input_dir) / rewritten[1]).read_text(encoding="utf-8")
    text = text.replace("/workspace/input", str(Path(input_dir)).replace("\\", "/"))
    script = Path(output_dir) / rewritten[1]
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    script.write_text(text, encoding="utf-8")
    rewritten[0] = sys.executable
    rewritten[1] = str(script)
    completed = subprocess.run(rewritten, capture_output=True, text=True, check=False)
    return ContainerExecutionResult(
        command=rewritten,
        image=policy.image,
        image_id="sha256:test",
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        isolation_attestation={"network": "none", "read_only_rootfs": True},
    )


def test_hidden_target_acceptance_separates_candidate_and_evaluator(tmp_path: Path) -> None:
    report = run_openml_hidden_prediction_acceptance(
        _source(tmp_path),
        output_root=tmp_path / "acceptance",
        isolated_runner=_local_runner,
    )

    assert report["profile_id"] == "benchmark_prediction_v1"
    assert report["hidden_target_boundary"]["target_present_in_candidate_bundle"] is False
    assert not (tmp_path / "acceptance" / "candidate-input" / "targets.csv").exists()
    assert report["arms"]["baseline"]["denominator"] == 2
    assert report["arms"]["treatment"]["independent_recalculation_match"] is True
    assert report["independent_validation"]["external_independent_operator"] is False


def test_hidden_target_acceptance_rejects_changed_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with (source / "dataset.csv").open("a", encoding="utf-8") as handle:
        handle.write("changed")
    try:
        run_openml_hidden_prediction_acceptance(
            source,
            output_root=tmp_path / "acceptance",
            isolated_runner=_local_runner,
        )
    except ValueError as exc:
        assert "hash" in str(exc)
    else:  # pragma: no cover - explicit negative assertion
        raise AssertionError("changed source was accepted")
