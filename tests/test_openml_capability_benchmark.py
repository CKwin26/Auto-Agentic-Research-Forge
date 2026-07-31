from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research_forge.benchmarks.openml_tasks import (
    LoadedOpenMLTask,
    run_openml_task_benchmark,
)


def test_openml_fixture_freezes_split_results_and_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    task = LoadedOpenMLTask(
        task_id=999,
        dataset_id=888,
        dataset_name="fixture",
        dataset_version=1,
        dataset_md5="a" * 32,
        dataset_license="CC0",
        task_type="classification",
        target_name="class",
        feature_names=["x1", "x2"],
        sample_ids=[f"s{i}" for i in range(10)],
        x=np.asarray(
            [
                [-3, -2], [-2, -3], [-2, -1], [2, 1], [3, 2], [1, 3],
                [-4, -2], [-1, -2], [2, 2], [4, 3],
            ],
            dtype=float,
        ),
        y=np.asarray(["0", "0", "0", "1", "1", "1", "0", "0", "1", "1"]),
        train_indices=np.arange(6),
        test_indices=np.arange(6, 10),
        estimation_procedure={"type": "fixture"},
    )
    monkeypatch.setattr(
        "research_forge.benchmarks.openml_tasks.load_official_openml_task",
        lambda task_id, cache_dir: task,
    )

    report = run_openml_task_benchmark(
        999, output_root=tmp_path / "out", cache_dir=tmp_path / "cache"
    )

    assert report["scientific_decision"]["verdict"] == "supported"
    assert report["results"]["baseline"]["denominator"] == 4
    assert report["results"]["treatment"]["accuracy"] == 1.0
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert persisted["protocol"]["split_sha256"]
    assert persisted["dataset"]["materialized_sha256"]
    assert persisted["results"]["baseline_result_sha256"]
