from __future__ import annotations

"""Declared, auditable experiment execution for remediation actions.

Only commands explicitly declared by the project are executed.  Commands are
started without a shell, are bounded to a project working directory, and are
not considered evidence until every declared artifact passes validation.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .storage import ensure_within, read_json, safe_relative, sha256_file, write_json_atomic


EXPERIMENT_MANIFEST_NAMES = (
    "research-forge.experiments.json",
    ".research-forge/experiments.json",
)


class ExperimentArtifactSpec(StrictModel):
    path: str
    format: Literal["file", "json", "jsonl", "csv"] = "file"
    min_bytes: int = Field(default=1, ge=0)
    required_keys: list[str] = Field(default_factory=list)


class ExperimentSpec(StrictModel):
    experiment_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,119}$")
    action_ids: list[str] = Field(min_length=1)
    title: str
    command: list[str] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = Field(default=3600, ge=1, le=604_800)
    required_env: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    smoke_command: list[str] | None = None
    smoke_required_inputs: list[str] = Field(default_factory=list)
    execution_backend: Literal[
        "controlled_local",
        "isolated_candidate_evaluator",
    ] = "controlled_local"
    container_image: str | None = None
    candidate_code_paths: list[str] = Field(default_factory=list)
    evaluator_command: list[str] | None = None
    evaluator_code_paths: list[str] = Field(default_factory=list)
    evaluator_required_inputs: list[str] = Field(default_factory=list)
    prediction_artifact_path: str | None = None
    network_access: bool = False
    artifacts: list[ExperimentArtifactSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_declaration(self) -> "ExperimentSpec":
        if any(not item.startswith("action-") for item in self.action_ids):
            raise ValueError("experiment action_ids must be remediation action IDs")
        if any(not item or "\x00" in item for item in self.command):
            raise ValueError("experiment command contains an empty or invalid argument")
        if self.smoke_command is not None and any(
            not item or "\x00" in item for item in self.smoke_command
        ):
            raise ValueError("experiment smoke_command contains an invalid argument")
        if self.evaluator_command is not None and any(
            not item or "\x00" in item for item in self.evaluator_command
        ):
            raise ValueError(
                "experiment evaluator_command contains an invalid argument"
            )
        if self.execution_backend == "isolated_candidate_evaluator":
            missing = [
                name
                for name, value in (
                    ("container_image", self.container_image),
                    ("candidate_code_paths", self.candidate_code_paths),
                    ("evaluator_command", self.evaluator_command),
                    ("evaluator_code_paths", self.evaluator_code_paths),
                    (
                        "evaluator_required_inputs",
                        self.evaluator_required_inputs,
                    ),
                    (
                        "prediction_artifact_path",
                        self.prediction_artifact_path,
                    ),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "isolated candidate/evaluator experiment is missing: "
                    + ", ".join(missing)
                )
            if self.network_access:
                raise ValueError(
                    "generated isolated experiments cannot request network"
                )
        for name in self.required_env:
            if not name.replace("_", "a").isalnum() or not name[0].isalpha():
                raise ValueError(f"invalid environment variable name: {name}")
        return self


class ExperimentManifest(StrictModel):
    schema_version: int = 1
    experiments: list[ExperimentSpec]


class ExperimentPreflightError(RuntimeError):
    def __init__(self, kind: Literal["configuration", "environment", "dataset", "permission"], missing: list[str]) -> None:
        self.kind = kind
        self.missing = missing
        super().__init__("、".join(missing))


class ExperimentPaused(RuntimeError):
    """The process was terminated at the user's requested pause checkpoint."""


class ExperimentProcessError(RuntimeError):
    """A declared experiment process exited before producing valid artifacts."""

    def __init__(self, returncode: int, stderr_path: Path) -> None:
        self.returncode = returncode
        self.stderr_path = stderr_path
        super().__init__(
            f"experiment process exited with {returncode}; "
            f"inspect {stderr_path}"
        )


def find_experiment_manifest(source_root: str | Path, explicit_path: str | None = None) -> Path | None:
    root = Path(source_root).resolve()
    if explicit_path:
        candidate = Path(explicit_path)
        candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        ensure_within(root, candidate)
        return candidate if candidate.is_file() else None
    for relative in EXPERIMENT_MANIFEST_NAMES:
        candidate = safe_relative(root, relative)
        if candidate.is_file():
            return candidate
    return None


def load_experiment_manifest(path: str | Path) -> ExperimentManifest:
    return ExperimentManifest.model_validate(read_json(Path(path).resolve()))


def load_project_experiment_manifest(
    source_root: str | Path, explicit_path: str | None = None
) -> tuple[ExperimentManifest | None, Path | None]:
    """Load an explicit declaration or adapt a directly runnable frozen contract."""

    root = Path(source_root).resolve()
    path = find_experiment_manifest(root, explicit_path)
    if path is not None:
        return load_experiment_manifest(path), path
    contract_path = root / "execution_contract.json"
    if explicit_path or not contract_path.is_file():
        return None, None
    contract = read_json(contract_path)
    command = contract.get("command")
    # A two-process protected evaluator needs an explicit remediation manifest
    # so its candidate/submission boundary cannot be guessed incorrectly.
    if not isinstance(command, list) or not command or contract.get("evaluator_command"):
        return None, None
    required_metrics = [str(item) for item in contract.get("required_metrics", [])]
    primary_metric = str(contract.get("primary_metric", "")).strip()
    if primary_metric:
        required_metrics.append(primary_metric)
    spec = ExperimentSpec(
        experiment_id="frozen-execution-contract",
        action_ids=["action-independent-evaluation"],
        title="执行冻结协议中的独立评价",
        command=[str(item) for item in command],
        cwd=".",
        timeout_seconds=int(contract.get("timeout_seconds", 3600)),
        required_inputs=["current_parameters.json"],
        artifacts=[ExperimentArtifactSpec(
            path="metrics.json",
            format="json",
            required_keys=list(dict.fromkeys(required_metrics)),
        )],
    )
    return ExperimentManifest(experiments=[spec]), contract_path


def experiment_for_action(manifest: ExperimentManifest, action_id: str) -> ExperimentSpec | None:
    matches = [item for item in manifest.experiments if action_id in item.action_ids]
    if len(matches) > 1:
        raise ValueError(f"multiple experiments are declared for {action_id}")
    return matches[0] if matches else None


def declared_action_ids(source_root: str | Path) -> set[str]:
    manifest, _ = load_project_experiment_manifest(source_root)
    if manifest is None:
        return set()
    return {action_id for item in manifest.experiments for action_id in item.action_ids}


def _render(value: str, variables: dict[str, str]) -> str:
    try:
        return value.format_map(variables)
    except KeyError as exc:
        raise ValueError(f"unknown experiment command placeholder: {exc.args[0]}") from exc


def _resolve_working_directory(source_root: Path, cwd: str, variables: dict[str, str]) -> Path:
    rendered = _render(cwd, variables)
    candidate = Path(rendered)
    candidate = candidate.resolve() if candidate.is_absolute() else (source_root / candidate).resolve()
    ensure_within(source_root, candidate)
    if not candidate.is_dir():
        raise ExperimentPreflightError("environment", [f"工作目录不存在：{candidate}"])
    return candidate


def _artifact_path(source_root: Path, artifact: ExperimentArtifactSpec, variables: dict[str, str]) -> Path:
    rendered = _render(artifact.path, variables)
    candidate = Path(rendered)
    evidence_dir = Path(variables["evidence_dir"]).resolve()
    candidate = candidate.resolve() if candidate.is_absolute() else (evidence_dir / candidate).resolve()
    ensure_within(evidence_dir, candidate)
    return candidate


def preflight_experiment(
    spec: ExperimentSpec,
    *,
    source_root: str | Path,
    evidence_dir: str | Path,
    action_id: str,
    plan_id: str,
    network_authorized: bool,
    run_variables: dict[str, str] | None = None,
    use_smoke: bool = False,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    evidence = Path(evidence_dir).resolve()
    variables = {
        "source_root": str(root),
        "evidence_dir": str(evidence),
        "action_id": action_id,
        "plan_id": plan_id,
        "python": sys.executable,
        "experiment_dir": str(root / "experiment"),
        "evaluator_dir": str(root / "evaluator"),
        "run_dir": str(evidence),
        "params_file": str(root / "current_parameters.json"),
        "submission_file": str(evidence / "submission"),
        "metrics_file": str(evidence / "metrics.json"),
        "project_dir": str(root),
    }
    allowed_run_variables = {
        "task_id",
        "split_id",
        "arm_id",
        "seed",
        "replicate",
        "run_cell_id",
    }
    supplied = dict(run_variables or {})
    unknown = sorted(set(supplied).difference(allowed_run_variables))
    if unknown:
        raise ValueError(
            "unsupported experiment run variables: " + ", ".join(unknown)
        )
    variables.update({key: str(value) for key, value in supplied.items()})
    cwd = _resolve_working_directory(root, spec.cwd, variables)
    declared_command = (
        spec.smoke_command
        if use_smoke and spec.smoke_command is not None
        else spec.command
    )
    command = [_render(item, variables) for item in declared_command]
    executable = command[0]
    executable_path = Path(executable)
    if executable_path.is_absolute():
        executable_ok = executable_path.is_file()
    elif any(sep in executable for sep in ("/", "\\")):
        executable_ok = (cwd / executable_path).is_file()
    else:
        executable_ok = shutil.which(executable) is not None
    if not executable_ok:
        raise ExperimentPreflightError("environment", [f"找不到实验程序：{executable}"])
    missing_env = [name for name in spec.required_env if not os.getenv(name)]
    if missing_env:
        raise ExperimentPreflightError("environment", [f"环境变量 {name}" for name in missing_env])
    missing_inputs: list[str] = []
    required_inputs: list[Path] = []
    declared_inputs = (
        spec.smoke_required_inputs
        if use_smoke and spec.smoke_required_inputs
        else spec.required_inputs
    )
    for item in declared_inputs:
        rendered = _render(item, variables)
        candidate = Path(rendered)
        candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        ensure_within(root, candidate)
        if not candidate.exists():
            missing_inputs.append(rendered)
        else:
            required_inputs.append(candidate)
    if missing_inputs:
        raise ExperimentPreflightError("dataset", missing_inputs)
    if spec.network_access and not network_authorized:
        raise ExperimentPreflightError("permission", ["该实验声明需要访问网络"])
    artifacts = [_artifact_path(root, item, variables) for item in spec.artifacts]
    return {
        "cwd": cwd,
        "command": command,
        "artifacts": artifacts,
        "variables": variables,
        "required_inputs": required_inputs,
    }


def _snapshot_required_input(path: Path, source_root: Path) -> dict[str, Any]:
    relative_path = path.resolve().relative_to(source_root.resolve()).as_posix()
    if path.is_file():
        return {
            "path": str(path),
            "relative_path": relative_path,
            "kind": "file",
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return {
        "path": str(path),
        "relative_path": relative_path,
        "kind": "directory",
        "sha256": None,
        "size_bytes": None,
        "verification": (
            "unverifiable_directory_input; declare content-addressed files "
            "or a manifest for formal binding"
        ),
    }


def _verify_artifact(path: Path, spec: ExperimentArtifactSpec) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"实验未生成声明的证据产物：{path}")
    size = path.stat().st_size
    if size < spec.min_bytes:
        raise ValueError(f"证据产物小于最低大小 {spec.min_bytes} 字节：{path}")
    if spec.format == "json":
        payload = read_json(path)
        missing = [key for key in spec.required_keys if key not in payload]
        if missing:
            raise ValueError(f"证据 JSON 缺少字段 {missing}：{path}")
    elif spec.format == "jsonl":
        with path.open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if not rows:
            raise ValueError(f"证据 JSONL 没有记录：{path}")
    elif spec.format == "csv":
        with path.open("r", encoding="utf-8-sig") as handle:
            header = handle.readline().strip()
            body = handle.readline()
        if not header or not body:
            raise ValueError(f"证据 CSV 必须包含表头和至少一行数据：{path}")
    return {
        "path": str(path),
        "bytes": size,
        "sha256": sha256_file(path),
        "format": spec.format,
        "verified_at": utc_now(),
    }


def run_declared_experiment(
    spec: ExperimentSpec,
    *,
    source_root: str | Path,
    evidence_dir: str | Path,
    action_id: str,
    plan_id: str,
    network_authorized: bool,
    report_progress: Callable[..., None],
    control_status: Callable[[], str] | None = None,
    run_variables: dict[str, str] | None = None,
    use_smoke: bool = False,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    evidence = Path(evidence_dir).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    prepared = preflight_experiment(
        spec,
        source_root=root,
        evidence_dir=evidence,
        action_id=action_id,
        plan_id=plan_id,
        network_authorized=network_authorized,
        run_variables=run_variables,
        use_smoke=use_smoke,
    )
    required_inputs_before = [
        _snapshot_required_input(path, root)
        for path in prepared["required_inputs"]
    ]
    execution_path = evidence / "execution.json"
    stdout_path = evidence / "stdout.log"
    stderr_path = evidence / "stderr.log"
    started_at = utc_now()
    write_json_atomic(execution_path, {
        "schema_version": 1,
        "experiment_id": spec.experiment_id,
        "action_id": action_id,
        "plan_id": plan_id,
        "status": "running",
        "started_at": started_at,
        "command": prepared["command"],
        "cwd": str(prepared["cwd"]),
        "required_env_names": spec.required_env,
        "network_access": spec.network_access,
    })
    process = subprocess.Popen(
        prepared["command"],
        cwd=prepared["cwd"],
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    messages: queue.Queue[tuple[str, str]] = queue.Queue()

    def drain(stream: Any, label: str, destination: Path) -> None:
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
            for line in iter(stream.readline, ""):
                handle.write(line)
                handle.flush()
                messages.put((label, line.rstrip()))

    threads = [
        threading.Thread(target=drain, args=(process.stdout, "stdout", stdout_path), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr", stderr_path), daemon=True),
    ]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    try:
        while process.poll() is None:
            if time.monotonic() - started > spec.timeout_seconds:
                process.kill()
                raise TimeoutError(f"实验超过 {spec.timeout_seconds} 秒超时限制")
            if control_status and str(control_status()) == "pause_requested":
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                write_json_atomic(execution_path, {
                    **read_json(execution_path),
                    "status": "paused",
                    "paused_at": utc_now(),
                })
                raise ExperimentPaused("实验已在安全检查点停止；恢复后将重新执行本动作")
            try:
                channel, line = messages.get(timeout=0.2)
            except queue.Empty:
                continue
            if line:
                report_progress(
                    stage="experimentation",
                    title=f"正在执行：{spec.title}",
                    detail=line[-500:],
                    reason=f"{channel} · 实验进程 {process.pid}",
                    output=str(evidence),
                    next="等待实验产物并进行完整性验证",
                )
        for thread in threads:
            thread.join(timeout=2)
        if process.returncode != 0:
            write_json_atomic(
                execution_path,
                {
                    **read_json(execution_path),
                    "status": "failed",
                    "finished_at": utc_now(),
                    "returncode": process.returncode,
                    "stderr_log": str(stderr_path),
                },
            )
            raise ExperimentProcessError(
                process.returncode,
                stderr_path,
            )
        artifacts = [
            _verify_artifact(path, artifact)
            for path, artifact in zip(prepared["artifacts"], spec.artifacts, strict=True)
        ]
        required_inputs_after = [
            _snapshot_required_input(path, root)
            for path in prepared["required_inputs"]
        ]
        input_binding_valid = bool(
            len(required_inputs_before) == len(required_inputs_after)
            and all(
                before["kind"] == "file"
                and before["sha256"]
                and before["sha256"] == after["sha256"]
                for before, after in zip(
                    required_inputs_before,
                    required_inputs_after,
                    strict=True,
                )
            )
        )
        report = {
            **read_json(execution_path),
            "status": "completed",
            "completed_at": utc_now(),
            "exit_code": process.returncode,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "artifacts": artifacts,
            "required_inputs_before": required_inputs_before,
            "required_inputs_after": required_inputs_after,
            "input_binding_valid": input_binding_valid,
        }
        write_json_atomic(execution_path, report)
        write_json_atomic(evidence / "artifact_manifest.json", {
            "schema_version": 1,
            "experiment_id": spec.experiment_id,
            "action_id": action_id,
            "artifacts": artifacts,
        })
        return report
    except BaseException as exc:
        if process.poll() is None:
            process.kill()
        for thread in threads:
            thread.join(timeout=2)
        if not isinstance(exc, ExperimentPaused):
            write_json_atomic(execution_path, {
                **read_json(execution_path),
                "status": "failed",
                "failed_at": utc_now(),
                "error": str(exc),
                "exit_code": process.poll(),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            })
        raise


__all__ = [
    "EXPERIMENT_MANIFEST_NAMES",
    "ExperimentArtifactSpec",
    "ExperimentManifest",
    "ExperimentPaused",
    "ExperimentProcessError",
    "ExperimentPreflightError",
    "ExperimentSpec",
    "declared_action_ids",
    "experiment_for_action",
    "find_experiment_manifest",
    "load_experiment_manifest",
    "load_project_experiment_manifest",
    "preflight_experiment",
    "run_declared_experiment",
]
