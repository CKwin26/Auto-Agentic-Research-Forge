"""Bounded, metadata-only inspection for large HDF5 research datasets."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field

from .models import StrictModel


_MAX_HDF5_FILES = 500
_MAX_DATASET_SPECS = 200


class HDF5DatasetSpec(StrictModel):
    name: str
    shape: list[int]
    dtype: str


class HDF5MetadataResource(StrictModel):
    """Structural metadata binding; deliberately not a full-file hash."""

    schema_version: int = 1
    path: str
    size_bytes: int = Field(ge=0)
    modified_at: str
    metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding_level: Literal["metadata_only"] = "metadata_only"
    task: str
    representation_family: str
    root_keys: list[str] = Field(default_factory=list)
    demo_count: int = Field(default=0, ge=0)
    sample_count: int = Field(default=0, ge=0)
    datasets: list[HDF5DatasetSpec] = Field(default_factory=list)
    mask_keys: list[str] = Field(default_factory=list)
    readable: bool = True
    error: str | None = None


def _task(path: Path) -> str:
    lower = path.name.casefold()
    return next(
        (item for item in ("square", "tool_hang", "transport") if item in lower),
        path.parent.name.casefold(),
    )


def _family(path: Path, observation_names: list[str]) -> str:
    lower = path.name.casefold()
    if "pointcloud" in lower or any(
        "point_cloud" in item or "pointcloud" in item for item in observation_names
    ):
        return "pointcloud"
    if "with_dino" in lower or any("dino" in item for item in observation_names):
        return "stereo_dino_features"
    if any("depth" in item for item in observation_names):
        return "rgb_depth"
    if any("left_image" in item or "right_image" in item for item in observation_names):
        return "stereo_rgb"
    return "hdf5_observations"


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_hdf5_metadata(path: Path, root: Path) -> HDF5MetadataResource:
    """Read group names, shapes, dtypes and counters without loading arrays."""

    stat = path.stat()
    relative = path.relative_to(root).as_posix()
    modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    try:
        import h5py

        with h5py.File(path, "r") as handle:
            root_keys = sorted(str(item) for item in handle.keys())
            data = handle.get("data")
            demo_names = sorted(str(item) for item in data.keys()) if data else []
            sample_count = 0
            specs: list[HDF5DatasetSpec] = []
            observation_names: list[str] = []
            for index, demo_name in enumerate(demo_names):
                demo = data[demo_name]
                fallback = (
                    int(demo["actions"].shape[0]) if "actions" in demo else 0
                )
                sample_count += int(demo.attrs.get("num_samples", fallback))
                if index:
                    continue

                def collect(name: str, item: object) -> None:
                    if not isinstance(item, h5py.Dataset):
                        return
                    if len(specs) >= _MAX_DATASET_SPECS:
                        return
                    specs.append(
                        HDF5DatasetSpec(
                            name=name,
                            shape=[int(value) for value in item.shape],
                            dtype=str(item.dtype),
                        )
                    )

                demo.visititems(collect)
                if "obs" in demo:
                    observation_names = sorted(str(item) for item in demo["obs"].keys())
            mask_keys = (
                sorted(str(item) for item in handle["mask"].keys())
                if "mask" in handle
                else []
            )
        digest_payload: dict[str, object] = {
            "path": relative,
            "size_bytes": stat.st_size,
            "root_keys": root_keys,
            "demo_count": len(demo_names),
            "sample_count": sample_count,
            "datasets": [item.model_dump(mode="json") for item in specs],
            "mask_keys": mask_keys,
        }
        return HDF5MetadataResource(
            path=relative,
            size_bytes=stat.st_size,
            modified_at=modified_at,
            metadata_sha256=_digest(digest_payload),
            task=_task(path),
            representation_family=_family(path, observation_names),
            root_keys=root_keys,
            demo_count=len(demo_names),
            sample_count=sample_count,
            datasets=specs,
            mask_keys=mask_keys,
        )
    except Exception as exc:
        payload = {
            "path": relative,
            "size_bytes": stat.st_size,
            "error_type": type(exc).__name__,
        }
        return HDF5MetadataResource(
            path=relative,
            size_bytes=stat.st_size,
            modified_at=modified_at,
            metadata_sha256=_digest(payload),
            task=_task(path),
            representation_family="unreadable_hdf5",
            readable=False,
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )


def inventory_hdf5_metadata(
    source_root: str | Path,
    *,
    excluded_directories: set[str] | None = None,
) -> list[HDF5MetadataResource]:
    root = Path(source_root).resolve()
    excluded = {item.casefold() for item in (excluded_directories or set())}
    paths: list[Path] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name.casefold() not in excluded
            and not (Path(current) / name).is_symlink()
        )
        for name in sorted(file_names):
            if Path(name).suffix.casefold() not in {".h5", ".hdf5"}:
                continue
            path = Path(current) / name
            if path.is_symlink():
                continue
            paths.append(path)
            if len(paths) > _MAX_HDF5_FILES:
                raise ValueError(
                    f"project bundle exceeds the {_MAX_HDF5_FILES}-file HDF5 metadata limit"
                )
    return [inspect_hdf5_metadata(path, root) for path in paths]


__all__ = [
    "HDF5DatasetSpec",
    "HDF5MetadataResource",
    "inspect_hdf5_metadata",
    "inventory_hdf5_metadata",
]
