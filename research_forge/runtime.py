from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .models import ExecutionContract


RuntimeKind = Literal["local", "docker"]
DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
DEFAULT_CONTROLLED_CPU_IMAGE = "rf-airs-cpu:v1"
CONTROLLED_ENVIRONMENT_LABEL = "org.research-forge.controlled"
CONTROLLED_ENVIRONMENT_NAME_LABEL = "org.research-forge.environment"
CONTROLLED_ENVIRONMENT_VERSION_LABEL = "org.research-forge.environment-version"
CAPABILITY_PROBE_LABEL = "org.research-forge.capability-probe"
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")


@dataclass(frozen=True)
class RuntimeOptions:
    kind: RuntimeKind = "local"
    image: str = DEFAULT_DOCKER_IMAGE
    cpus: float = 1.0
    memory_mb: int = 2048
    pids_limit: int = 256
    tmpfs_mb: int = 512
    max_output_mb: int = 256
    docker_executable: str = "docker"

    def __post_init__(self) -> None:
        if self.kind not in {"local", "docker"}:
            raise ValueError("runtime must be local or docker")
        if not _IMAGE_RE.fullmatch(self.image):
            raise ValueError(f"invalid Docker image reference: {self.image}")
        if self.cpus <= 0 or self.cpus > 64:
            raise ValueError("cpus must be greater than 0 and at most 64")
        if self.memory_mb < 128 or self.memory_mb > 1_048_576:
            raise ValueError("memory_mb must be between 128 and 1048576")
        if self.pids_limit < 16 or self.pids_limit > 65_536:
            raise ValueError("pids_limit must be between 16 and 65536")
        if self.tmpfs_mb < 16 or self.tmpfs_mb > self.memory_mb:
            raise ValueError("tmpfs_mb must be between 16 and memory_mb")
        if self.max_output_mb < 1 or self.max_output_mb > self.memory_mb:
            raise ValueError("max_output_mb must be between 1 and memory_mb")

    def public_config(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "image": self.image if self.kind == "docker" else None,
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "pids_limit": self.pids_limit,
            "tmpfs_mb": self.tmpfs_mb,
            "max_output_mb": self.max_output_mb,
        }


@dataclass
class TrialRuntimeResult:
    experiment_argv: list[str]
    evaluator_argv: list[str] | None
    exit_code: int | None = None
    evaluator_exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    evaluator_stdout: str = ""
    evaluator_stderr: str = ""
    error: str | None = None
    phase: str = "experiment"


def _interpolate(
    tokens: list[str],
    values: dict[str, str],
    *,
    python_executable: str,
) -> list[str]:
    rendered_tokens: list[str] = []
    for token in tokens:
        rendered = token
        for placeholder, replacement in values.items():
            rendered = rendered.replace(placeholder, replacement)
        rendered_tokens.append(rendered)
    if rendered_tokens[0] != python_executable:
        raise ValueError("execution command must use the configured Python interpreter")
    return rendered_tokens


def _host_values(
    *,
    project: Path,
    experiment_dir: Path,
    evaluator_dir: Path,
    trial_dir: Path,
    params_file: Path,
    submission_file: Path,
    metrics_file: Path,
) -> dict[str, str]:
    return {
        "{python}": sys.executable,
        "{experiment_dir}": str(experiment_dir),
        "{evaluator_dir}": str(evaluator_dir),
        "{run_dir}": str(trial_dir),
        "{params_file}": str(params_file),
        "{submission_file}": str(submission_file),
        "{metrics_file}": str(metrics_file),
        "{project_dir}": str(project),
    }


class ExecutionRuntime:
    name: RuntimeKind

    def __init__(self, options: RuntimeOptions) -> None:
        self.options = options

    def attestation(self, *, evaluator_separated: bool) -> dict[str, object]:
        raise NotImplementedError

    def capabilities(self) -> dict[str, object]:
        raise NotImplementedError

    def run_trial(
        self,
        *,
        contract: ExecutionContract,
        project: Path,
        experiment_dir: Path,
        evaluator_dir: Path,
        trial_dir: Path,
        params_file: Path,
        environment: dict[str, str],
    ) -> TrialRuntimeResult:
        raise NotImplementedError


class LocalRuntime(ExecutionRuntime):
    name: RuntimeKind = "local"

    def capabilities(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "environment": "host-local",
            "version": None,
            "controlled": False,
            "verified": False,
            "python": sys.version.split()[0],
            "packages": {},
            "capabilities": [],
        }

    def attestation(self, *, evaluator_separated: bool) -> dict[str, object]:
        return {
            "runtime": "local",
            "isolation_verified": False,
            "candidate_evaluator_separated": evaluator_separated,
            "network": "host",
            "read_only_rootfs": False,
            "capabilities_dropped": False,
            "no_new_privileges": False,
            "image": None,
            "image_id": None,
            "controlled_environment": False,
            "capability_verified": False,
            "capability_manifest_sha256": None,
            "capabilities": self.capabilities(),
            "limits": self.options.public_config(),
        }

    def run_trial(
        self,
        *,
        contract: ExecutionContract,
        project: Path,
        experiment_dir: Path,
        evaluator_dir: Path,
        trial_dir: Path,
        params_file: Path,
        environment: dict[str, str],
    ) -> TrialRuntimeResult:
        submission_file = trial_dir / "submission"
        metrics_file = trial_dir / "metrics.json"
        values = _host_values(
            project=project,
            experiment_dir=experiment_dir,
            evaluator_dir=evaluator_dir,
            trial_dir=trial_dir,
            params_file=params_file,
            submission_file=submission_file,
            metrics_file=metrics_file,
        )
        command = _interpolate(contract.command, values, python_executable=sys.executable)
        evaluator_command = (
            _interpolate(contract.evaluator_command, values, python_executable=sys.executable)
            if contract.evaluator_command is not None
            else None
        )
        result = TrialRuntimeResult(
            experiment_argv=command,
            evaluator_argv=evaluator_command,
        )
        try:
            completed = subprocess.run(
                command,
                cwd=experiment_dir,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=contract.timeout_seconds,
                shell=False,
                check=False,
            )
            result.exit_code = completed.returncode
            result.stdout = completed.stdout
            result.stderr = completed.stderr
            if completed.returncode != 0:
                result.error = f"experiment exited with code {completed.returncode}"
            elif evaluator_command is not None and not submission_file.is_file():
                result.error = "experiment did not create the required submission file"
            elif evaluator_command is not None:
                result.phase = "evaluator"
                evaluated = subprocess.run(
                    evaluator_command,
                    cwd=evaluator_dir,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=contract.timeout_seconds,
                    shell=False,
                    check=False,
                )
                result.evaluator_exit_code = evaluated.returncode
                result.evaluator_stdout = evaluated.stdout
                result.evaluator_stderr = evaluated.stderr
                if evaluated.returncode != 0:
                    result.error = f"evaluator exited with code {evaluated.returncode}"
                elif not metrics_file.is_file():
                    result.error = "evaluator did not create the required metrics JSON file"
            elif not metrics_file.is_file():
                result.error = "experiment did not create the required metrics JSON file"
        except subprocess.TimeoutExpired as exc:
            result.stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            result.stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            result.error = f"{result.phase} timed out after {contract.timeout_seconds} seconds"
        except Exception as exc:
            result.error = f"runner error: {type(exc).__name__}: {exc}"
        return result


class DockerRuntime(ExecutionRuntime):
    name: RuntimeKind = "docker"

    def __init__(self, options: RuntimeOptions, *, preflight: bool = True) -> None:
        super().__init__(options)
        executable = shutil.which(options.docker_executable)
        if executable is None and Path(options.docker_executable).is_file():
            executable = str(Path(options.docker_executable).resolve())
        self.executable = executable or options.docker_executable
        self.server_version: str | None = None
        self.image_id: str | None = None
        self.image_platform: str | None = None
        self.image_labels: dict[str, str] = {}
        self.capability_manifest: dict[str, object] = {
            "schema_version": 1,
            "environment": "uncontrolled-docker-image",
            "version": None,
            "controlled": False,
            "verified": False,
            "python": None,
            "packages": {},
            "capabilities": [],
        }
        self.capability_manifest_sha256: str | None = None
        if preflight:
            self._preflight()

    def _preflight(self) -> None:
        if shutil.which(self.executable) is None and not Path(self.executable).is_file():
            raise FileNotFoundError(
                "Docker CLI is unavailable. Install/start Docker, then rerun benchmark doctor."
            )
        info = subprocess.run(
            [self.executable, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        if info.returncode != 0:
            raise RuntimeError(f"Docker daemon is unavailable: {info.stderr.strip() or info.stdout.strip()}")
        self.server_version = info.stdout.strip() or "unknown"
        image = subprocess.run(
            [self.executable, "image", "inspect", "--format", "{{.Id}}", self.options.image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        if image.returncode != 0:
            raise RuntimeError(
                f"Docker image {self.options.image!r} is unavailable; pull it explicitly before running"
            )
        self.image_id = image.stdout.strip() or "unknown"

        platform_result = subprocess.run(
            [
                self.executable,
                "image",
                "inspect",
                "--format",
                "{{.Os}}/{{.Architecture}}",
                self.options.image,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        if platform_result.returncode != 0:
            raise RuntimeError(f"could not inspect Docker image platform: {platform_result.stderr.strip()}")
        self.image_platform = platform_result.stdout.strip() or "unknown"

        labels = subprocess.run(
            [self.executable, "image", "inspect", "--format", "{{json .Config.Labels}}", self.options.image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        if labels.returncode != 0:
            raise RuntimeError(f"could not inspect Docker image labels: {labels.stderr.strip()}")
        try:
            parsed_labels = json.loads(labels.stdout.strip() or "null") or {}
        except json.JSONDecodeError as exc:
            raise RuntimeError("Docker returned invalid image-label JSON") from exc
        if not isinstance(parsed_labels, dict):
            raise RuntimeError("Docker image labels must be a JSON object")
        self.image_labels = {str(key): str(value) for key, value in parsed_labels.items()}
        if self.image_labels.get(CONTROLLED_ENVIRONMENT_LABEL, "").lower() == "true":
            self._verify_controlled_environment()

    def _verify_controlled_environment(self) -> None:
        probe = self.image_labels.get(CAPABILITY_PROBE_LABEL)
        if not probe or not probe.startswith("/"):
            raise RuntimeError("controlled Docker image has no absolute capability probe path")
        command = [
            self.executable,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "1024m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--user",
            "65534:65534",
            self.options.image,
            "python3",
            probe,
            "--json",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"controlled-environment capability probe failed: {detail}")
        try:
            manifest = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("capability probe returned invalid JSON") from exc
        if not isinstance(manifest, dict):
            raise RuntimeError("capability probe result must be a JSON object")
        expected_name = self.image_labels.get(CONTROLLED_ENVIRONMENT_NAME_LABEL)
        expected_version = self.image_labels.get(CONTROLLED_ENVIRONMENT_VERSION_LABEL)
        if (
            manifest.get("controlled") is not True
            or manifest.get("verified") is not True
            or manifest.get("status") != "ok"
            or manifest.get("environment") != expected_name
            or manifest.get("version") != expected_version
            or manifest.get("platform") != self.image_platform
        ):
            raise RuntimeError("capability probe does not match the controlled-image labels")
        self.capability_manifest = manifest
        canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.capability_manifest_sha256 = hashlib.sha256(canonical).hexdigest()

    def capabilities(self) -> dict[str, object]:
        return json.loads(json.dumps(self.capability_manifest, ensure_ascii=False))

    def attestation(self, *, evaluator_separated: bool) -> dict[str, object]:
        return {
            "runtime": "docker",
            "isolation_verified": evaluator_separated,
            "candidate_evaluator_separated": evaluator_separated,
            "network": "none",
            "read_only_rootfs": True,
            "capabilities_dropped": True,
            "no_new_privileges": True,
            "image": self.options.image,
            "image_id": self.image_id,
            "image_platform": self.image_platform,
            "docker_server_version": self.server_version,
            "controlled_environment": self.capability_manifest.get("controlled") is True,
            "capability_verified": self.capability_manifest.get("verified") is True,
            "capability_manifest_sha256": self.capability_manifest_sha256,
            "capabilities": self.capabilities(),
            "limits": self.options.public_config(),
        }

    @staticmethod
    def _mount(source: Path, destination: str, *, read_only: bool) -> str:
        mode = ",readonly" if read_only else ""
        return f"type=bind,src={source.resolve()},dst={destination}{mode}"

    def _base_command(
        self,
        *,
        name: str,
        workdir: str,
        mounts: list[tuple[Path, str, bool]],
    ) -> list[str]:
        command = [
            self.executable,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.options.pids_limit),
            "--memory",
            f"{self.options.memory_mb}m",
            "--cpus",
            str(self.options.cpus),
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={self.options.tmpfs_mb}m",
            "--user",
            "65534:65534",
            "--workdir",
            workdir,
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "AUTORESEARCH_PROJECT_DIR=/workspace/project",
            "--env",
            "AUTORESEARCH_RUN_DIR=/workspace/output",
        ]
        for source, destination, read_only in mounts:
            command.extend(
                ["--mount", self._mount(source, destination, read_only=read_only)]
            )
        command.append(self.options.image)
        return command

    def _invoke(
        self,
        command: list[str],
        *,
        name: str,
        environment: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(
                [self.executable, "rm", "-f", name],
                env=environment,
                capture_output=True,
                timeout=30,
                shell=False,
                check=False,
            )
            raise

    def _copy_regular_output(self, source: Path, destination: Path, *, label: str) -> None:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"{label} was not produced as a regular file")
        if source.stat().st_size > self.options.max_output_mb * 1024 * 1024:
            raise ValueError(f"{label} exceeds the {self.options.max_output_mb} MiB output limit")
        shutil.copy2(source, destination)

    def run_trial(
        self,
        *,
        contract: ExecutionContract,
        project: Path,
        experiment_dir: Path,
        evaluator_dir: Path,
        trial_dir: Path,
        params_file: Path,
        environment: dict[str, str],
    ) -> TrialRuntimeResult:
        if any("{evaluator_dir}" in token for token in contract.command):
            raise ValueError("candidate command cannot reference evaluator_dir in Docker runtime")
        if contract.evaluator_command is not None and any(
            "{experiment_dir}" in token for token in contract.evaluator_command
        ):
            raise ValueError("evaluator command cannot reference experiment_dir in Docker runtime")

        candidate_output = trial_dir / "candidate-output"
        evaluator_output = trial_dir / "evaluator-output"
        candidate_output.mkdir()
        evaluator_output.mkdir()
        try:
            candidate_output.chmod(0o777)
            evaluator_output.chmod(0o777)
        except OSError:
            pass

        submission_file = trial_dir / "submission"
        metrics_file = trial_dir / "metrics.json"
        candidate_values = {
            "{python}": "python3",
            "{experiment_dir}": "/workspace/experiment",
            "{evaluator_dir}": "/workspace/forbidden-evaluator",
            "{run_dir}": "/workspace/output",
            "{params_file}": "/workspace/input/params.json",
            "{submission_file}": "/workspace/output/submission",
            "{metrics_file}": "/workspace/output/metrics.json",
            "{project_dir}": "/workspace/project",
        }
        candidate_inner = _interpolate(
            contract.command,
            candidate_values,
            python_executable="python3",
        )
        candidate_mounts = [
            (experiment_dir, "/workspace/experiment", True),
            (params_file, "/workspace/input/params.json", True),
            (candidate_output, "/workspace/output", False),
        ]
        if (project / "data").is_dir():
            candidate_mounts.append((project / "data", "/workspace/project/data", True))
        candidate_name = f"rf-candidate-{uuid.uuid4().hex[:12]}"
        candidate_command = self._base_command(
            name=candidate_name,
            workdir="/workspace/experiment",
            mounts=candidate_mounts,
        ) + candidate_inner

        evaluator_command: list[str] | None = None
        evaluator_name: str | None = None
        evaluator_inner: list[str] | None = None
        result = TrialRuntimeResult(
            experiment_argv=candidate_command,
            evaluator_argv=None,
        )
        try:
            completed = self._invoke(
                candidate_command,
                name=candidate_name,
                environment=environment,
                timeout=contract.timeout_seconds,
            )
            result.exit_code = completed.returncode
            result.stdout = completed.stdout
            result.stderr = completed.stderr
            if completed.returncode != 0:
                result.error = f"experiment exited with code {completed.returncode}"
                return result

            if contract.evaluator_command is None:
                self._copy_regular_output(
                    candidate_output / "metrics.json",
                    metrics_file,
                    label="metrics",
                )
                return result

            self._copy_regular_output(
                candidate_output / "submission",
                submission_file,
                label="submission",
            )
            evaluator_values = {
                "{python}": "python3",
                "{experiment_dir}": "/workspace/forbidden-experiment",
                "{evaluator_dir}": "/workspace/evaluator",
                "{run_dir}": "/workspace/output",
                "{params_file}": "/workspace/input/params.json",
                "{submission_file}": "/workspace/input/submission",
                "{metrics_file}": "/workspace/output/metrics.json",
                "{project_dir}": "/workspace/project",
            }
            evaluator_inner = _interpolate(
                contract.evaluator_command,
                evaluator_values,
                python_executable="python3",
            )
            evaluator_mounts = [
                (evaluator_dir, "/workspace/evaluator", True),
                (params_file, "/workspace/input/params.json", True),
                (submission_file, "/workspace/input/submission", True),
                (evaluator_output, "/workspace/output", False),
            ]
            if (project / "data").is_dir():
                evaluator_mounts.append((project / "data", "/workspace/project/data", True))
            evaluator_name = f"rf-evaluator-{uuid.uuid4().hex[:12]}"
            evaluator_command = self._base_command(
                name=evaluator_name,
                workdir="/workspace/evaluator",
                mounts=evaluator_mounts,
            ) + evaluator_inner
            result.evaluator_argv = evaluator_command
            result.phase = "evaluator"
            evaluated = self._invoke(
                evaluator_command,
                name=evaluator_name,
                environment=environment,
                timeout=contract.timeout_seconds,
            )
            result.evaluator_exit_code = evaluated.returncode
            result.evaluator_stdout = evaluated.stdout
            result.evaluator_stderr = evaluated.stderr
            if evaluated.returncode != 0:
                result.error = f"evaluator exited with code {evaluated.returncode}"
                return result
            self._copy_regular_output(
                evaluator_output / "metrics.json",
                metrics_file,
                label="metrics",
            )
        except subprocess.TimeoutExpired as exc:
            result.stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else result.stdout
            result.stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else result.stderr
            result.error = f"{result.phase} timed out after {contract.timeout_seconds} seconds"
        except Exception as exc:
            result.error = f"runner error: {type(exc).__name__}: {exc}"
        return result


def build_runtime(
    runtime: RuntimeKind | RuntimeOptions | ExecutionRuntime = "local",
) -> ExecutionRuntime:
    if isinstance(runtime, ExecutionRuntime):
        return runtime
    options = runtime if isinstance(runtime, RuntimeOptions) else RuntimeOptions(kind=runtime)
    if options.kind == "docker":
        return DockerRuntime(options)
    return LocalRuntime(options)
