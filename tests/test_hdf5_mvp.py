from __future__ import annotations

from pathlib import Path

import pytest

from research_forge.hdf5_metadata import inventory_hdf5_metadata
from research_forge.hdf5_mvp import run_hdf5_feasibility_mvp


def _write_hdf5(path: Path, *, materialized_obs: bool = True) -> None:
    h5py = pytest.importorskip("h5py")
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as handle:
        data = handle.create_group("data")
        demo = data.create_group("demo_0")
        demo.attrs["num_samples"] = 2
        demo.create_dataset("actions", data=[[0.0], [1.0]])
        if materialized_obs:
            obs = demo.create_group("obs")
            obs.create_dataset("left_image", data=[[[[0]]], [[[1]]]])
            obs.create_dataset("right_image", data=[[[[0]]], [[[1]]]])
        else:
            demo.create_dataset("states", data=[[0.0, 1.0], [1.0, 2.0]])


def test_hdf5_mvp_is_read_only_and_non_scientific(tmp_path: Path) -> None:
    for name in (
        "square_stereo.hdf5",
        "tool_hang_stereo.hdf5",
        "transport_stereo.hdf5",
    ):
        _write_hdf5(tmp_path / "data" / name)
    rows = [
        item.model_dump(mode="json")
        for item in inventory_hdf5_metadata(tmp_path)
    ]
    before = {
        item["path"]: (
            (tmp_path / item["path"]).stat().st_size,
            (tmp_path / item["path"]).stat().st_mtime_ns,
        )
        for item in rows
    }

    report = run_hdf5_feasibility_mvp(
        tmp_path,
        rows,
        primary_metric="task_success_rate",
    )

    assert report["status"] == "verified"
    assert report["scientific_evidence_eligible"] is False
    assert len(report["smoke_cases"]) == 3
    assert report["metric_smoke_fixture"][
        "scientific_evidence_eligible"
    ] is False
    assert report["formal_baseline_executed"] is False
    assert report["formal_treatment_executed"] is False
    assert all(
        before[item["path"]]
        == (
            (tmp_path / item["path"]).stat().st_size,
            (tmp_path / item["path"]).stat().st_mtime_ns,
        )
        for item in rows
    )


def test_hdf5_mvp_blocks_without_two_valid_cases(tmp_path: Path) -> None:
    _write_hdf5(tmp_path / "data" / "square_stereo.hdf5")
    rows = [
        item.model_dump(mode="json")
        for item in inventory_hdf5_metadata(tmp_path)
    ]

    report = run_hdf5_feasibility_mvp(
        tmp_path,
        rows,
        primary_metric="task_success_rate",
    )

    assert report["status"] == "blocked"
    assert report["metric_computable"] is False
    assert report["baseline_instantiable"] is False


def test_hdf5_mvp_accepts_aligned_raw_state_action_trajectories(
    tmp_path: Path,
) -> None:
    for name in (
        "square_raw.hdf5",
        "tool_hang_raw.hdf5",
        "transport_raw.hdf5",
    ):
        _write_hdf5(
            tmp_path / "data" / name,
            materialized_obs=False,
        )
    rows = [
        item.model_dump(mode="json")
        for item in inventory_hdf5_metadata(tmp_path)
    ]

    report = run_hdf5_feasibility_mvp(
        tmp_path,
        rows,
        primary_metric="task_success_rate",
    )

    assert report["status"] == "verified"
    assert all(
        item["input_interface"] == "state_action_trajectory"
        and item["requires_stage3_observation_materialization"] is True
        for item in report["smoke_cases"]
    )
