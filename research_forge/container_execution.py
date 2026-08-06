"""Minimal OCI isolation boundary for generated and third-party Stage 3 code."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .storage import sha256_file


def _filesystem_path(path: Path) -> Path:
    """Return a local filesystem-safe view without changing audit paths."""

    resolved = path.resolve()
    if os.name != "nt":
        return resolved
    rendered = str(resolved)
    if rendered.startswith("\\\\?\\"):
        return resolved
    if rendered.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + rendered.lstrip("\\"))
    return Path("\\\\?\\" + rendered)


@dataclass(frozen=True)
class ContainerSupplyChainEvidence:
    image_digest: str
    signature_verified: bool
    signature_policy: str
    sbom_path: str
    sbom_sha256: str
    vulnerability_report_path: str
    vulnerability_report_sha256: str
    critical_vulnerability_count: int
    high_vulnerability_count: int
    scanner_version: str

    def validate(self) -> None:
        if not self.image_digest.startswith("sha256:"):
            raise ValueError("container image must be content-addressed")
        if not self.signature_verified:
            raise ValueError("container image signature is not verified")
        for label, path_value, expected in (
            ("SBOM", self.sbom_path, self.sbom_sha256),
            (
                "vulnerability report",
                self.vulnerability_report_path,
                self.vulnerability_report_sha256,
            ),
        ):
            path = Path(path_value).resolve()
            if not path.is_file() or sha256_file(path) != expected:
                raise ValueError(f"{label} is missing or changed")
        if self.critical_vulnerability_count:
            raise ValueError("container image has critical vulnerabilities")
        if self.high_vulnerability_count < 0:
            raise ValueError("vulnerability count cannot be negative")


@dataclass(frozen=True)
class ContainerExecutionPolicy:
    image: str = "python:3.12-slim"
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 128
    timeout_seconds: int = 300
    max_output_bytes: int = 64 * 1024 * 1024
    max_input_files: int = 100_000
    max_input_bytes: int = 2 * 1024 * 1024 * 1024
    max_log_bytes: int = 16 * 1024 * 1024
    docker_executable: str = "docker"
    require_supply_chain_evidence: bool = False
    supply_chain_evidence: ContainerSupplyChainEvidence | None = None

    def __post_init__(self) -> None:
        if not self.image or any(char.isspace() for char in self.image):
            raise ValueError("container image reference is invalid")
        if not 0 < self.cpus <= 64:
            raise ValueError("container cpus must be between 0 and 64")
        if not 128 <= self.memory_mb <= 1_048_576:
            raise ValueError("container memory_mb is outside the safe range")
        if not 16 <= self.pids_limit <= 65_536:
            raise ValueError("container pids_limit is outside the safe range")
        if self.timeout_seconds < 1:
            raise ValueError("container timeout must be positive")
        if min(
            self.max_output_bytes,
            self.max_input_files,
            self.max_input_bytes,
            self.max_log_bytes,
        ) < 1:
            raise ValueError("container file and byte limits must be positive")
        if self.require_supply_chain_evidence:
            if self.supply_chain_evidence is None:
                raise ValueError(
                    "container supply-chain evidence is required"
                )
            self.supply_chain_evidence.validate()


@dataclass(frozen=True)
class ContainerExecutionResult:
    command: list[str]
    image: str
    image_id: str
    exit_code: int
    stdout: str
    stderr: str
    isolation_attestation: dict[str, object]


def inspect_local_container_image(docker: str, image: str) -> str | None:
    """Resolve a local image to its immutable ID without pulling it."""

    inspected = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    image_id = inspected.stdout.strip()
    if inspected.returncode == 0 and image_id.startswith("sha256:"):
        return image_id
    listed = subprocess.run(
        [
            docker,
            "image",
            "ls",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"reference={image}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    candidates = sorted(
        {
            line.strip()
            for line in listed.stdout.splitlines()
            if line.strip().startswith("sha256:")
        }
    )
    if listed.returncode != 0 or len(candidates) != 1:
        return None
    return candidates[0]


def _directory_size(root: Path) -> int:
    return sum(
        os.stat(_filesystem_path(item)).st_size
        for item in root.rglob("*")
        if stat.S_ISREG(os.lstat(_filesystem_path(item)).st_mode)
    )


def _validate_mount_tree(
    root: Path, *, max_files: int, max_bytes: int
) -> dict[str, int]:
    files = 0
    total = 0
    forbidden_names = {
        ".env",
        "credentials.json",
        "secrets.json",
        "docker.sock",
        "podman.sock",
    }
    for item in root.rglob("*"):
        try:
            metadata = os.lstat(_filesystem_path(item))
        except OSError as exc:
            raise ValueError(f"cannot inspect container input: {item}") from exc
        mode = metadata.st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("container input contains a symbolic link")
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(
                "container input contains a device, socket, pipe, or "
                "other special file"
            )
        if item.name.casefold() in forbidden_names:
            raise ValueError(
                f"container input contains forbidden host material: {item.name}"
            )
        if stat.S_ISREG(mode):
            files += 1
            total += metadata.st_size
            if files > max_files or total > max_bytes:
                raise ValueError(
                    "container input exceeds the frozen archive-bomb limits"
                )
    return {"file_count": files, "byte_size": total}


def run_isolated_command(
    command: list[str],
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    policy: ContainerExecutionPolicy | None = None,
) -> ContainerExecutionResult:
    """Run code with read-only inputs, isolated outputs, and no network."""

    selected = policy or ContainerExecutionPolicy()
    if not command or any(not item or "\x00" in item for item in command):
        raise ValueError("isolated command contains an invalid argument")
    source = Path(input_dir).resolve()
    output = Path(output_dir).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"container input directory is missing: {source}")
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("container input and output directories must be disjoint")
    input_inventory = _validate_mount_tree(
        source,
        max_files=selected.max_input_files,
        max_bytes=selected.max_input_bytes,
    )
    output.mkdir(parents=True, exist_ok=True)
    # The container deliberately runs as an unprivileged fixed UID.  A fresh
    # POSIX host directory is normally 0755 and therefore not writable through
    # the bind mount by UID 65534.  Grant write/search only for the duration of
    # this isolated run, then restore the host directory to a read-only-for-
    # others mode.  Windows bind mounts do not use POSIX mode bits.
    restore_output_mode = os.name != "nt"
    if restore_output_mode:
        output.chmod(0o733)
    docker = shutil.which(selected.docker_executable)
    if docker is None:
        candidate = Path(selected.docker_executable)
        docker = str(candidate.resolve()) if candidate.is_file() else None
    if docker is None:
        raise FileNotFoundError("Docker CLI is unavailable")
    image_id = inspect_local_container_image(docker, selected.image)
    if image_id is None:
        raise RuntimeError(
            f"container image is unavailable: {selected.image}"
        )
    if selected.supply_chain_evidence is not None:
        selected.supply_chain_evidence.validate()
        if image_id != selected.supply_chain_evidence.image_digest:
            raise RuntimeError(
                "runtime image digest does not match signed supply-chain "
                "evidence"
            )
    docker_command = [
        docker,
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--ipc",
        "none",
        "--ulimit",
        "nofile=1024:1024",
        "--pids-limit",
        str(selected.pids_limit),
        "--memory",
        f"{selected.memory_mb}m",
        "--cpus",
        str(selected.cpus),
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--user",
        "65534:65534",
        "--mount",
        f"type=bind,src={source},dst=/workspace/input,readonly",
        "--mount",
        f"type=bind,src={output},dst=/workspace/output",
        "--workdir",
        "/workspace/input",
        selected.image,
        *command,
    ]
    try:
        completed = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=selected.timeout_seconds,
            shell=False,
            check=False,
        )
    finally:
        if restore_output_mode:
            output.chmod(0o755)
    output_size = _directory_size(output)
    if output_size > selected.max_output_bytes:
        raise RuntimeError(
            "container output exceeded the frozen output-size limit"
        )
    if (
        len(completed.stdout.encode("utf-8"))
        + len(completed.stderr.encode("utf-8"))
        > selected.max_log_bytes
    ):
        raise RuntimeError(
            "container logs exceeded the frozen log-size limit"
        )
    output_inventory = _validate_mount_tree(
        output,
        max_files=selected.max_input_files,
        max_bytes=selected.max_output_bytes,
    )
    return ContainerExecutionResult(
        command=docker_command,
        image=selected.image,
        image_id=image_id,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        isolation_attestation={
            "runtime": "docker",
            "image": selected.image,
            "image_id": image_id,
            "network": "none",
            "read_only_rootfs": True,
            "read_only_input": True,
            "separate_writable_output": True,
            "capabilities_dropped": True,
            "no_new_privileges": True,
            "host_secrets_mounted": False,
            "host_repository_writable": False,
            "run_as_non_root": True,
            "user_namespace_or_non_root_uid": "65534:65534",
            "ipc_namespace": "none",
            "seccomp": "runtime_default",
            "apparmor_or_selinux": "runtime_managed",
            "docker_socket_mounted": False,
            "devices_added": False,
            "supply_chain": (
                {
                    "image_signature_verified": (
                        selected.supply_chain_evidence.signature_verified
                    ),
                    "signature_policy": (
                        selected.supply_chain_evidence.signature_policy
                    ),
                    "sbom_sha256": (
                        selected.supply_chain_evidence.sbom_sha256
                    ),
                    "vulnerability_report_sha256": (
                        selected.supply_chain_evidence
                        .vulnerability_report_sha256
                    ),
                    "critical_vulnerability_count": (
                        selected.supply_chain_evidence
                        .critical_vulnerability_count
                    ),
                    "high_vulnerability_count": (
                        selected.supply_chain_evidence
                        .high_vulnerability_count
                    ),
                    "scanner_version": (
                        selected.supply_chain_evidence.scanner_version
                    ),
                }
                if selected.supply_chain_evidence is not None
                else {
                    "image_signature_verified": False,
                    "sbom_sha256": None,
                    "vulnerability_report_sha256": None,
                    "status": "not_supplied",
                }
            ),
            "input_inventory": input_inventory,
            "output_inventory": output_inventory,
            "limits": {
                "cpus": selected.cpus,
                "memory_mb": selected.memory_mb,
                "pids_limit": selected.pids_limit,
                "timeout_seconds": selected.timeout_seconds,
                "max_output_bytes": selected.max_output_bytes,
                "max_log_bytes": selected.max_log_bytes,
                "max_input_files": selected.max_input_files,
                "max_input_bytes": selected.max_input_bytes,
            },
        },
    )


__all__ = [
    "ContainerExecutionPolicy",
    "ContainerExecutionResult",
    "ContainerSupplyChainEvidence",
    "run_isolated_command",
]
