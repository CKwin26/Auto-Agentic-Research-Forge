"""Official AIRS RAD task adapter.

This module intentionally does not reimplement AIRS task code.  It freezes a
verbatim RAD task bundle and invokes its ``prepare.py``, ``evaluate_prepare.py``
and ``evaluate.py`` entrypoints with the directories expected by aira-dojo.
"""

from __future__ import annotations

import hashlib
import json
import csv
import re
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


_REQUIRED = ("metadata.yaml", "project_description.md", "prepare.py", "evaluate_prepare.py", "evaluate.py")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _docker_run(
    *,
    image: str,
    workdir: str,
    mounts: list[tuple[Path, str, bool]],
    command: list[str],
) -> dict[str, Any]:
    """Run an official entrypoint in an isolated Linux container.

    The candidate process is never placed in this container.  This runner is
    only for the unmodified official prepare/evaluation scripts and has no
    network access once the image has been built.
    """
    docker_command = ["docker", "run", "--rm", "--network", "none", "--workdir", workdir]
    for host, container, read_only in mounts:
        host_path = str(host.resolve())
        suffix = ":ro" if read_only else ""
        docker_command.extend(["--volume", f"{host_path}:{container}{suffix}"])
    docker_command.extend([image, *command])
    result = subprocess.run(docker_command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return {"command": docker_command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _runtime_details(execution: str, docker_image: str | None) -> dict[str, str]:
    if execution == "local":
        return {"execution": "local"}
    if execution == "docker":
        if not docker_image:
            raise ValueError("docker execution requires docker_image")
        return {"execution": "docker", "docker_image": docker_image}
    raise ValueError("execution must be 'local' or 'docker'")


def _run_manifest_path(task_root: Path, agent_log_dir: Path, explicit: str | Path | None) -> Path:
    """Keep every seed/candidate run bound to its own manifest.

    The legacy task-root path is retained only for callers that do not provide a
    run-local path.  Official multi-seed execution must pass an explicit path.
    """
    if explicit is not None:
        return Path(explicit).resolve()
    return task_root / "aira_dojo_run_manifest.json"


def _validate_submission_structure(submission: Path, task_root: Path) -> dict[str, Any]:
    """Validate public CSV structure without loading evaluator-only labels."""
    if not submission.is_file():
        raise FileNotFoundError("aira-dojo agent log has no submission.csv")
    with submission.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2 or not rows[0] or any(not cell.strip() for cell in rows[0]):
        raise ValueError("submission.csv must contain a non-empty header and at least one data row")
    width = len(rows[0])
    if any(len(row) != width for row in rows[1:]):
        raise ValueError("submission.csv has inconsistent row widths")
    metadata = yaml.safe_load((task_root / "metadata.yaml").read_text(encoding="utf-8")) or {}
    shape = str(metadata.get("logging_info", {}).get("shape", ""))
    match = re.fullmatch(r"\((\d+),\)", shape)
    expected_rows = int(match.group(1)) if match else None
    if expected_rows is not None and len(rows) - 1 != expected_rows:
        raise ValueError(f"submission.csv row count ({len(rows) - 1}) does not match public task shape ({expected_rows})")
    return {"path": str(submission), "header": rows[0], "row_count": len(rows) - 1, "column_count": width}


def import_official_airs_task(source: str | Path, output_root: str | Path) -> Path:
    """Copy an official RAD bundle verbatim and bind every source file by hash."""
    source_dir = Path(source).resolve()
    missing = [name for name in _REQUIRED if not (source_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("official AIRS RAD task is missing: " + ", ".join(missing))
    metadata = yaml.safe_load((source_dir / "metadata.yaml").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("logging_info"), dict):
        raise ValueError("official AIRS metadata must contain logging_info")
    name = str(metadata["logging_info"].get("name") or source_dir.name)
    destination = Path(output_root).resolve() / name
    if destination.exists():
        raise FileExistsError(f"official AIRS task already exists: {destination}")
    shutil.copytree(source_dir, destination)
    hashes = {path.relative_to(destination).as_posix(): _digest(path) for path in sorted(destination.rglob("*")) if path.is_file()}
    manifest = {
        "schema_version": 1,
        "adapter": "official-airs-rad-aira-dojo",
        "task_name": name,
        "source_bundle": str(source_dir),
        "source_files_sha256": hashes,
        "contract": {
            "prepare": "prepare.py is executed unchanged before the agent starts",
            "evaluate_prepare": "evaluate_prepare.py is executed unchanged after a submission",
            "evaluate": "evaluate.py is executed unchanged against evaluator-only data",
        },
        "leaderboard_status": "not_submitted",
    }
    (destination / "official_adapter_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def prepare_official_airs_task(
    task_dir: str | Path,
    *,
    global_shared_data_dir: str | Path,
    agent_data_mount_dir: str | Path,
    agent_log_dir: str | Path,
    evaluator_data_mount_dir: str | Path,
    python: str | Path,
    run_manifest_path: str | Path | None = None,
    execution: str = "local",
    docker_image: str | None = None,
) -> dict[str, Any]:
    """Execute the official AIRS data preparation scripts without modification.

    ``agent_log_dir`` is intentionally required even before a candidate run: it
    is the aira-dojo run directory into which the candidate will later write
    ``submission.csv``.  The evaluation preparation is deferred until that file
    exists, so hidden labels never enter the agent mount.
    """
    root = Path(task_dir).resolve()
    manifest_path = root / "official_adapter_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("task is not an imported official AIRS RAD bundle")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest["source_files_sha256"].items():
        path = root / relative
        if not path.is_file() or _digest(path) != expected:
            raise ValueError(f"official AIRS source drift: {relative}")
    data_root = Path(global_shared_data_dir).resolve()
    agent_data = Path(agent_data_mount_dir).resolve()
    agent_log = Path(agent_log_dir).resolve()
    evaluator_data = Path(evaluator_data_mount_dir).resolve()
    for directory in (data_root, agent_data, agent_log, evaluator_data):
        directory.mkdir(parents=True, exist_ok=True)
    runtime = _runtime_details(execution, docker_image)
    if execution == "local":
        executable = Path(python).resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"Python interpreter not found: {executable}")
        prepared = _run([str(executable), "prepare.py", "--global-shared-data-dir", str(data_root), "--agent-data-mount-dir", str(agent_data), "--agent-log-dir", str(agent_log)], cwd=root)
    else:
        prepared = _docker_run(
            image=runtime["docker_image"], workdir="/task",
            mounts=[(root, "/task", True), (data_root, "/raw", True), (agent_data, "/agent-data", False), (agent_log, "/agent-log", False)],
            command=["python", "prepare.py", "--global-shared-data-dir", "/raw", "--agent-data-mount-dir", "/agent-data", "--agent-log-dir", "/agent-log"],
        )
    if prepared["returncode"] != 0:
        raise RuntimeError("official AIRS prepare.py failed: " + prepared["stderr"][-1000:])
    run_manifest = {
        "schema_version": 1,
        "adapter": manifest["adapter"],
        "official_adapter_manifest_sha256": _digest(manifest_path),
        "global_shared_data_dir": str(data_root),
        "agent_data_mount": str(agent_data),
        "agent_log_dir": str(agent_log),
        "evaluator_data_mount": str(evaluator_data),
        **runtime,
        "prepare": prepared,
        "evaluation_prepared": False,
        "leaderboard_status": "not_submitted",
    }
    manifest_output = _run_manifest_path(root, agent_log, run_manifest_path)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(json.dumps(run_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return run_manifest


def evaluate_official_airs_submission(
    task_dir: str | Path,
    *,
    python: str | Path,
    run_manifest_path: str | Path | None = None,
    execution: str | None = None,
    docker_image: str | None = None,
) -> dict[str, Any]:
    """Invoke official hidden-label preparation and scorer after aira-dojo writes a submission."""
    root = Path(task_dir).resolve()
    run_path = _run_manifest_path(root, root, run_manifest_path)
    if not run_path.is_file():
        raise FileNotFoundError("run official prepare first")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    agent_log = Path(run["agent_log_dir"])
    submission_validation = _validate_submission_structure(agent_log / "submission.csv", root)
    evaluator_root = Path(run["evaluator_data_mount"])
    evaluator_data = evaluator_root / "data"
    evaluator_data.mkdir(parents=True, exist_ok=True)
    selected_execution = execution or str(run.get("execution", "local"))
    selected_image = docker_image or run.get("docker_image")
    runtime = _runtime_details(selected_execution, selected_image)
    if selected_execution == "local":
        executable = Path(python).resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"Python interpreter not found: {executable}")
        prepared = _run([str(executable), "evaluate_prepare.py", "--global-shared-data-dir", str(run["global_shared_data_dir"]), "--agent-data-mount-dir", str(evaluator_data), "--agent-log-dir", str(agent_log)], cwd=root)
    else:
        prepared = _docker_run(
            image=runtime["docker_image"], workdir="/task",
            mounts=[(root, "/task", True), (Path(run["global_shared_data_dir"]), "/raw", True), (agent_log, "/agent-log", False), (evaluator_root, "/evaluator-data", False)],
            command=["python", "evaluate_prepare.py", "--global-shared-data-dir", "/raw", "--agent-data-mount-dir", "/evaluator-data/data", "--agent-log-dir", "/agent-log"],
        )
    if prepared["returncode"] != 0:
        raise RuntimeError("official AIRS evaluate_prepare.py failed: " + prepared["stderr"][-1000:])
    if selected_execution == "local":
        scored = _run([str(executable), str(root / "evaluate.py"), "--submission-file", str(evaluator_data / "submission.csv")], cwd=evaluator_root)
    else:
        scored = _docker_run(
            image=runtime["docker_image"], workdir="/evaluator-data",
            mounts=[(root, "/task", True), (evaluator_root, "/evaluator-data", False)],
            command=["python", "/task/evaluate.py", "--submission-file", "/evaluator-data/data/submission.csv"],
        )
    if scored["returncode"] != 0:
        raise RuntimeError("official AIRS evaluate.py failed: " + scored["stderr"][-1000:])
    metric_values = re.findall(r'"[^"\\]+"\s*:\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|NaN|Infinity|-Infinity)', scored["stdout"])
    if not metric_values or any(not math.isfinite(float(value)) for value in metric_values):
        raise RuntimeError("official AIRS evaluate.py produced no finite metric")
    run.update({"evaluation_prepared": True, "submission_validation": submission_validation, **runtime, "evaluate_prepare": prepared, "evaluate": scored})
    run_path.write_text(json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return run
