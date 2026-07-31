"""Immutable, isolated replay of an explicitly declared Python project slice.

This module is deliberately narrower than a general build system.  It copies
only owner-authorized files into a sealed package, executes an explicit
command twice in fresh offline containers, and derives metrics with a frozen
deterministic parser.  It never searches the source project for commands and
never treats a successful process exit as a scientific result by itself.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import Field, model_validator

from .container_execution import ContainerExecutionPolicy, run_isolated_command
from .models import StrictModel, utc_now
from .storage import read_json, sha256_file, write_json_atomic


_FORBIDDEN_NAMES = {
    ".env",
    ".env.local",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}


class ExistingProjectReplaySpec(StrictModel):
    """Owner-approved, deterministic replay declaration."""

    schema_version: int = 1
    case_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,119}$")
    source_files: list[str] = Field(min_length=1, max_length=10_000)
    container_image: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    parser: Literal["unittest_v1", "json_metrics_v1"]
    result_path: str | None = None
    expected_metrics: dict[str, float] = Field(min_length=1)
    metric_tolerances: dict[str, float] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    clean_replays: int = Field(default=2, ge=2, le=10)

    @model_validator(mode="after")
    def validate_replay_boundary(self) -> "ExistingProjectReplaySpec":
        if len(self.source_files) != len(set(self.source_files)):
            raise ValueError("source_files must be unique")
        for relative in self.source_files:
            _safe_relative(relative)
        if any(not token or "\x00" in token for token in self.command):
            raise ValueError("command contains an empty or invalid token")
        if self.parser == "json_metrics_v1":
            if not self.result_path:
                raise ValueError("json_metrics_v1 requires result_path")
            _safe_relative(self.result_path)
        elif self.result_path is not None:
            raise ValueError("unittest_v1 does not accept result_path")
        unknown_tolerances = set(self.metric_tolerances).difference(
            self.expected_metrics
        )
        if unknown_tolerances:
            raise ValueError(
                "metric_tolerances contains unknown metrics: "
                + ", ".join(sorted(unknown_tolerances))
            )
        for name, value in self.expected_metrics.items():
            if not name or not math.isfinite(float(value)):
                raise ValueError("expected metrics must be finite")
        for value in self.metric_tolerances.values():
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError("metric tolerances must be finite and non-negative")
        return self


def _safe_relative(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"path must stay inside the project snapshot: {value}")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError(f"path is not canonical: {value}")
    if any(part.casefold() in _FORBIDDEN_NAMES for part in path.parts):
        raise ValueError(f"secret-bearing path cannot enter replay package: {value}")
    return path


def _canonical_sha256(payload: dict[str, Any]) -> str:
    import hashlib

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_existing_project_replay_package(
    source_root: str | Path,
    spec: ExistingProjectReplaySpec,
    package_dir: str | Path,
) -> dict[str, Any]:
    """Freeze an allow-listed project slice without leaking the host path."""

    source = Path(source_root).resolve()
    package = Path(package_dir).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source project is unavailable: {source}")
    if package == source or source in package.parents or package in source.parents:
        raise ValueError("replay package and source project must be disjoint")
    package.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, Any]] = []
    for declared in sorted(spec.source_files):
        relative = _safe_relative(declared)
        source_path = (source / Path(*relative.parts)).resolve()
        source_path.relative_to(source)
        if source_path.is_symlink():
            raise ValueError(f"symbolic links are not allowed: {declared}")
        if not source_path.is_file():
            raise FileNotFoundError(f"declared project file is missing: {declared}")
        destination = package / Path(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        files.append(
            {
                "path": relative.as_posix(),
                "sha256": sha256_file(destination),
                "size": destination.stat().st_size,
            }
        )
    identity: dict[str, Any] = {
        "schema_version": 1,
        "case_id": spec.case_id,
        "spec": spec.model_dump(mode="json"),
        "files": files,
        "source_root_recorded": False,
        "network_access": False,
        "execution_authority": "explicit_owner_approved_spec",
    }
    payload = {
        **identity,
        "created_at": utc_now(),
        "package_sha256": _canonical_sha256(identity),
    }
    sealed = {**payload, "manifest_sha256": _canonical_sha256(payload)}
    write_json_atomic(package / "replay_manifest.json", sealed)
    return sealed


def _verify_package(package: Path, manifest: dict[str, Any]) -> None:
    unsigned_manifest = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    if manifest.get("manifest_sha256") != _canonical_sha256(unsigned_manifest):
        raise ValueError("replay manifest hash is invalid")
    identity = {
        key: value
        for key, value in unsigned_manifest.items()
        if key not in {"created_at", "package_sha256"}
    }
    if manifest.get("package_sha256") != _canonical_sha256(identity):
        raise ValueError("replay package identity hash is invalid")
    declared = {item["path"]: item for item in manifest.get("files", [])}
    actual = {
        item.relative_to(package).as_posix()
        for item in package.rglob("*")
        if item.is_file() and item.name != "replay_manifest.json"
    }
    if actual != set(declared):
        raise ValueError("replay package file set differs from the sealed manifest")
    for relative, record in declared.items():
        path = package / Path(*PurePosixPath(relative).parts)
        if sha256_file(path) != record["sha256"]:
            raise ValueError(f"replay package file changed: {relative}")


def _parse_unittest(stdout: str, stderr: str, exit_code: int) -> dict[str, float]:
    combined = "\n".join([stdout, stderr])
    match = re.search(r"Ran\s+(\d+)\s+tests?", combined)
    if match is None:
        raise ValueError("unittest output does not contain a frozen test denominator")
    tests_run = int(match.group(1))
    if tests_run < 1:
        raise ValueError("unittest replay executed no tests")
    failures_match = re.search(r"failures=(\d+)", combined)
    errors_match = re.search(r"errors=(\d+)", combined)
    skipped_match = re.search(r"skipped=(\d+)", combined)
    failures = int(failures_match.group(1)) if failures_match else 0
    errors = int(errors_match.group(1)) if errors_match else 0
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    if exit_code != 0 and failures == 0 and errors == 0:
        errors = 1
    passed = max(0, tests_run - failures - errors - skipped)
    return {
        "tests_run": float(tests_run),
        "failures": float(failures),
        "errors": float(errors),
        "skipped": float(skipped),
        "pass_rate": passed / tests_run,
    }


def _parse_json_metrics(path: Path) -> dict[str, float]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("JSON metric artifact must be an object")
    metrics: dict[str, float] = {}
    for name, raw in payload.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"metric {name} must be finite")
        metrics[str(name)] = value
    if not metrics:
        raise ValueError("JSON metric artifact has no numeric metrics")
    return metrics


def replay_existing_project_package(
    package_dir: str | Path,
    *,
    work_root: str | Path | None = None,
) -> dict[str, Any]:
    """Execute the same sealed package in fresh, offline containers."""

    package = Path(package_dir).resolve()
    manifest = read_json(package / "replay_manifest.json")
    spec = ExistingProjectReplaySpec.model_validate(manifest["spec"])
    _verify_package(package, manifest)
    owned_temp: tempfile.TemporaryDirectory[str] | None = None
    if work_root is None:
        owned_temp = tempfile.TemporaryDirectory(prefix="rf-project-replay-")
        root = Path(owned_temp.name).resolve()
    else:
        root = Path(work_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    try:
        for index in range(1, spec.clean_replays + 1):
            _verify_package(package, manifest)
            output = root / f"replay-{index:02d}"
            result = run_isolated_command(
                spec.command,
                input_dir=package,
                output_dir=output,
                policy=ContainerExecutionPolicy(
                    image=spec.container_image,
                    timeout_seconds=spec.timeout_seconds,
                ),
            )
            if spec.parser == "unittest_v1":
                metrics = _parse_unittest(result.stdout, result.stderr, result.exit_code)
            else:
                assert spec.result_path is not None
                metrics = _parse_json_metrics(
                    output / Path(*PurePosixPath(spec.result_path).parts)
                )
            missing = sorted(set(spec.expected_metrics).difference(metrics))
            if missing:
                raise ValueError("replay metrics are missing: " + ", ".join(missing))
            comparisons = {}
            for name, expected in spec.expected_metrics.items():
                observed = metrics[name]
                tolerance = float(spec.metric_tolerances.get(name, 0.0))
                comparisons[name] = {
                    "expected": float(expected),
                    "observed": observed,
                    "tolerance": tolerance,
                    "matches": abs(observed - float(expected)) <= tolerance,
                }
            runs.append(
                {
                    "replay_index": index,
                    "exit_code": result.exit_code,
                    "metrics": metrics,
                    "historical_comparison": comparisons,
                    "image": result.image,
                    "image_id": result.image_id,
                    "isolation_attestation": result.isolation_attestation,
                    "stdout_sha256": _text_sha256(result.stdout),
                    "stderr_sha256": _text_sha256(result.stderr),
                }
            )
        reference = runs[0]["metrics"]
        cross_replay_equal = all(item["metrics"] == reference for item in runs[1:])
        historical_match = all(
            comparison["matches"]
            for item in runs
            for comparison in item["historical_comparison"].values()
        )
        image_ids = {item["image_id"] for item in runs}
        return {
            "schema_version": 1,
            "case_id": spec.case_id,
            "package_sha256": manifest["package_sha256"],
            "status": (
                "verified" if cross_replay_equal and historical_match else "mismatch"
            ),
            "historical_match": historical_match,
            "cross_replay_equal": cross_replay_equal,
            "clean_workspace_count": len(runs),
            "single_image_digest": len(image_ids) == 1,
            "source_root_recorded": False,
            "runs": runs,
            "verified_at": utc_now(),
        }
    finally:
        if owned_temp is not None:
            owned_temp.cleanup()


def _text_sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ExistingProjectReplaySpec",
    "build_existing_project_replay_package",
    "replay_existing_project_package",
]
