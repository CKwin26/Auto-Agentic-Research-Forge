from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from research_forge.hdf5_metadata import inventory_hdf5_metadata
from research_forge.project_bundle import (
    discover_hdf5_metadata_candidate,
    inspect_project_bundle,
    inventory_project_bundle,
    inventory_project_hdf5_metadata,
)
from research_forge.workflow_scheduler import run_project_discovery


def _hdf5_fixture(path: Path, *, representation: str = "pointcloud") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as handle:
        data = handle.create_group("data")
        demo = data.create_group("demo_0")
        demo.attrs["num_samples"] = 3
        demo.create_dataset("actions", data=np.zeros((3, 7), dtype=np.float32))
        observations = demo.create_group("obs")
        if representation == "pointcloud":
            observations.create_dataset(
                "point_cloud", data=np.zeros((3, 16, 3), dtype=np.float32)
            )
        else:
            observations.create_dataset(
                "agentview_left_dino",
                data=np.zeros((3, 2, 2, 8), dtype=np.float16),
            )
            observations.create_dataset(
                "agentview_right_dino",
                data=np.zeros((3, 2, 2, 8), dtype=np.float16),
            )
        mask = handle.create_group("mask")
        mask.create_dataset("train", data=np.array([b"demo_0"]))


def test_cache_directories_are_excluded_from_project_and_hdf5_scan(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "README.md").write_text("# Real project\n", encoding="utf-8")
    cache = source / "torch_cache"
    cache.mkdir()
    (cache / "MODEL_CARD.md").write_text("# Third-party model\n", encoding="utf-8")
    _hdf5_fixture(cache / "cached_square_100_pointcloud.hdf5")

    resources, excluded = inventory_project_bundle(source)
    hdf5 = inventory_project_hdf5_metadata(source)

    assert [item.path for item in resources] == ["README.md"]
    assert excluded >= 1
    assert hdf5 == []


def test_hdf5_adapter_reads_structure_without_array_materialization(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stereopolicy"
    _hdf5_fixture(
        source / "pointcloud" / "square_100_pointcloud.hdf5",
        representation="pointcloud",
    )
    _hdf5_fixture(
        source / "dino" / "square_100_stereo_with_dino.hdf5",
        representation="dino",
    )

    metadata = inventory_hdf5_metadata(source)
    candidate = discover_hdf5_metadata_candidate(source, metadata)

    assert len(metadata) == 2
    assert {item.representation_family for item in metadata} == {
        "pointcloud",
        "stereo_dino_features",
    }
    assert all(item.binding_level == "metadata_only" for item in metadata)
    assert all(item.demo_count == 1 and item.sample_count == 3 for item in metadata)
    pointcloud = next(
        item for item in metadata if item.representation_family == "pointcloud"
    )
    assert next(
        item for item in pointcloud.datasets if item.name == "obs/point_cloud"
    ).shape == [3, 16, 3]
    assert candidate is not None
    assert candidate.closure_input_ready is True
    assert candidate.artifact_chain_complete is False
    assert "paired" in candidate.display_title.casefold()


def test_hdf5_bundle_produces_robotics_direction_not_cached_model_card(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stereopolicy"
    _hdf5_fixture(source / "square_100_pointcloud.hdf5")
    _hdf5_fixture(
        source / "square_100_stereo_with_dino.hdf5", representation="dino"
    )
    cache = source / "torch_cache"
    cache.mkdir()
    (cache / "MODEL_CARD.md").write_text(
        "# Model Card for an unrelated cached model\n", encoding="utf-8"
    )

    inspection = inspect_project_bundle(source)
    result = run_project_discovery(
        source,
        repository_root=tmp_path / "workflow",
        include_external=False,
        identity="hdf5-workflow",
    )

    direction = result["discovery_portfolio"]["directions"][0]
    assert inspection.hdf5_metadata
    assert inspection.candidates[0].track_id.startswith("hdf5-metadata-")
    assert direction["academic_normalization_status"] == "normalized"
    assert "robotic imitation learning" in direction["academic_concepts"]
    assert "Model Card" not in direction["title"]
