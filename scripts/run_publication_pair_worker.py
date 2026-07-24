from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import subprocess
import time
from pathlib import Path

from research_forge.study_runner import run_stage2_publication_pairs


def _pair_sequences(parity: str) -> list[int]:
    if parity == "odd":
        return list(range(1, 41, 2))
    if parity == "even":
        return list(range(2, 41, 2))
    raise ValueError(f"unsupported parity: {parity}")


def _docker_is_ready() -> bool:
    result = subprocess.run(
        ["docker", "info", "--format", "{{json .ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _ensure_docker(desktop: Path, timeout_seconds: int = 240) -> None:
    if _docker_is_ready():
        return
    if not desktop.is_file():
        raise FileNotFoundError(f"Docker Desktop executable not found: {desktop}")
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    subprocess.Popen(
        [str(desktop)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=startupinfo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _docker_is_ready():
            return
        time.sleep(5)
    raise TimeoutError("Docker daemon did not become ready before worker timeout")


def _ensure_frozen_image_tag(project: Path) -> None:
    backbone = json.loads((project / "stage2" / "backbone_manifest.json").read_text(encoding="utf-8"))
    runtime = backbone["runtime"]
    image = str(runtime["image"])
    image_id = str(runtime["image_id"])
    tagged = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if tagged.returncode == 0:
        return
    frozen = subprocess.run(
        ["docker", "image", "inspect", image_id],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if frozen.returncode != 0:
        raise RuntimeError("the frozen Docker image ID is unavailable")
    retagged = subprocess.run(
        ["docker", "image", "tag", image_id, image],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if retagged.returncode != 0:
        raise RuntimeError("the frozen Docker image tag could not be restored")


def _pid_is_running(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return False
    rows = list(csv.reader(io.StringIO(result.stdout)))
    return any(len(row) >= 2 and row[1].strip() == str(pid) for row in rows)


def _wait_for_predecessor(pid: int | None) -> None:
    if pid is None:
        return
    if pid <= 0 or pid == os.getpid():
        raise ValueError("wait-for-pid must identify a different positive process")
    while _pid_is_running(pid):
        time.sleep(10)


def _is_transient_docker_daemon_failure(error: BaseException) -> bool:
    """Return true only for the pre-cell Docker daemon disconnect we can resume."""
    message = str(error).lower()
    return "docker daemon is unavailable" in message or "dockerdesktoplinuxengine" in message


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resume one disjoint half of a frozen publication pair matrix."
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--parity", choices=("odd", "even"), required=True)
    parser.add_argument(
        "--docker-desktop",
        type=Path,
        default=Path(r"D:\Docker\Desktop\Docker Desktop.exe"),
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".private" / "synapai-codex",
    )
    parser.add_argument("--codex-model", default="gpt-5.3-spark")
    parser.add_argument("--wait-for-pid", type=int)
    parser.add_argument(
        "--runtime-retries",
        type=int,
        default=3,
        help="Maximum supervisor retries for a Docker daemon loss before any cell starts.",
    )
    args = parser.parse_args()
    _wait_for_predecessor(args.wait_for_pid)
    codex_home = args.codex_home.resolve()
    if not (codex_home / "config.toml").is_file() or not (
        codex_home / "auth.json"
    ).is_file():
        raise FileNotFoundError(
            "isolated SynapAI Codex configuration is incomplete; expected config.toml and auth.json"
        )
    os.environ["RESEARCH_FORGE_CODEX_HOME"] = str(codex_home)
    os.environ["RESEARCH_FORGE_CODEX_MODEL"] = args.codex_model.strip()
    billing_contract = (
        args.project.resolve()
        / "design_revisions"
        / "synapai_gpt56_billing_contract.json"
    )
    if not billing_contract.is_file():
        raise FileNotFoundError(
            f"frozen SynapAI billing contract is missing: {billing_contract}"
        )
    os.environ["RESEARCH_FORGE_PROVIDER_BILLING_CONTRACT"] = str(billing_contract)
    if args.runtime_retries < 0:
        raise ValueError("runtime-retries must be non-negative")
    project = args.project.resolve()
    for attempt in range(args.runtime_retries + 1):
        _ensure_docker(args.docker_desktop.resolve())
        _ensure_frozen_image_tag(project)
        try:
            result = asyncio.run(
                run_stage2_publication_pairs(
                    project, pair_sequences=_pair_sequences(args.parity)
                )
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            return
        except RuntimeError as error:
            if not _is_transient_docker_daemon_failure(error) or attempt >= args.runtime_retries:
                raise
            time.sleep(5)


if __name__ == "__main__":
    main()
