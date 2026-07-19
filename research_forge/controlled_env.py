from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .runtime import DEFAULT_CONTROLLED_CPU_IMAGE, DockerRuntime, RuntimeOptions


CONTROLLED_ENVIRONMENT_PROFILES = {
    "airs-cpu": {
        "default_image": DEFAULT_CONTROLLED_CPU_IMAGE,
        "context_directory": "airs-cpu",
    }
}


def build_controlled_environment(
    profile: str = "airs-cpu",
    *,
    image: str | None = None,
    docker_executable: str = "docker",
) -> dict[str, object]:
    if profile not in CONTROLLED_ENVIRONMENT_PROFILES:
        raise ValueError(f"unknown controlled-environment profile: {profile}")
    definition = CONTROLLED_ENVIRONMENT_PROFILES[profile]
    selected_image = image or str(definition["default_image"])
    options = RuntimeOptions(
        kind="docker",
        image=selected_image,
        docker_executable=docker_executable,
    )
    executable = shutil.which(docker_executable)
    if executable is None and Path(docker_executable).is_file():
        executable = str(Path(docker_executable).resolve())
    if executable is None:
        raise FileNotFoundError("Docker CLI is unavailable")

    context = (
        Path(__file__).resolve().parents[1]
        / "docker"
        / str(definition["context_directory"])
    )
    if not (context / "Dockerfile").is_file():
        raise FileNotFoundError(f"controlled-environment build context is missing: {context}")
    completed = subprocess.run(
        [
            executable,
            "build",
            "--pull",
            "--provenance=false",
            "--platform",
            "linux/amd64",
            "--tag",
            selected_image,
            str(context),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"controlled environment build failed with code {completed.returncode}")

    runtime = DockerRuntime(options)
    return {
        "profile": profile,
        "image": selected_image,
        "context": str(context),
        "built": True,
        "runtime_attestation": runtime.attestation(evaluator_separated=True),
    }
