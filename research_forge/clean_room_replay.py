"""Execute a sealed reproduction package in an ephemeral Docker worker.

The worker receives exactly one scientific input: the signed reproduction
package.  The host supplies a generic Python runtime and an empty evidence
directory, but it does not mount the repository, Workflow database, caches,
credentials, Docker socket, or signing material.

This module produces an unsigned :class:`WorkerExecutionReport`.  A local
Docker replay is useful controlled evidence, but it is not external
verification and this module never signs a receipt or awards RF-E2 by itself.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .reproduction_checker import verify_reproduction_package
from .reproduction_domain import (
    IsolationReport,
    ReproductionJobStatus,
    ReproductionPackageManifest,
    ReproductionPolicy,
    WorkerIdentity,
)
from .reproduction_launcher import create_reproduction_job
from .reproduction_worker_protocol import build_unsigned_worker_report
from .stage_three_trust import _safe_extract
from .storage import read_json, sha256_file, write_json_atomic, write_text_atomic


_WORKER_BOOTSTRAP = r"""
import hashlib, json, pathlib, runpy, shutil, sys, zipfile
package = pathlib.Path('/sealed/reproduction-package.zip')
root = pathlib.Path('/tmp/reproduction-package')
with zipfile.ZipFile(package) as archive:
    archive.extractall(root)
manifest = json.loads((root / 'package-manifest.json').read_text('utf-8'))
logical = sys.argv[1]
asset = next((item for item in manifest.get('assets', [])
              if item.get('logical_path') == logical), None)
if not asset or asset.get('mode') != 'embedded':
    raise SystemExit('requested replay entrypoint is not an embedded asset')
entrypoint = root / asset['object_path']
digest = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
if digest != asset['sha256']:
    raise SystemExit('embedded replay entrypoint digest mismatch')
sys.argv = [str(entrypoint), str(root), '/evidence/reproduced-summary.json']
runpy.run_path(str(entrypoint), run_name='__main__')
"""


DockerRunner = Callable[..., subprocess.CompletedProcess[str]]


def build_clean_room_docker_command(
    *,
    package_path: str | Path,
    evidence_directory: str | Path,
    entrypoint_logical_path: str,
    image: str,
    container_name: str,
) -> list[str]:
    """Return the hardened, auditable Docker invocation."""

    package = Path(package_path).resolve()
    evidence = Path(evidence_directory).resolve()
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "1.0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=128m",
        "--mount",
        f"type=bind,src={package},dst=/sealed/reproduction-package.zip,readonly",
        "--mount",
        f"type=bind,src={evidence},dst=/evidence",
        image,
        "python",
        "-c",
        _WORKER_BOOTSTRAP,
        entrypoint_logical_path,
    ]


def run_clean_room_docker_reproduction(
    *,
    package_path: str | Path,
    entrypoint_logical_path: str,
    trusted_control_plane_keys: dict[str, bytes],
    requested_by: str,
    evidence_directory: str | Path,
    report_output: str | Path,
    image: str = "python:3.12-slim",
    timeout_seconds: int = 3_600,
    docker_runner: DockerRunner = subprocess.run,
) -> dict[str, Any]:
    """Replay one signed package in a fresh network-disabled container.

    The returned report is deliberately unsigned.  The caller may submit it
    to a separately configured verifier, but this local launcher does not
    possess or emulate an external verifier key.
    """

    package = Path(package_path).resolve()
    evidence = Path(evidence_directory).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report_path = Path(report_output).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    verification = verify_reproduction_package(
        package,
        trusted_control_plane_keys=trusted_control_plane_keys,
    )
    if not verification["reproduction_ready"]:
        return {
            "status": "blocked",
            "verification": verification,
            "rf_e2_awarded": False,
        }
    manifest = ReproductionPackageManifest.model_validate(
        verification["manifest"]
    )
    policy = ReproductionPolicy.model_validate(verification["policy"])
    container_name = f"rf-clean-room-{uuid.uuid4().hex[:12]}"
    command = build_clean_room_docker_command(
        package_path=package,
        evidence_directory=evidence,
        entrypoint_logical_path=entrypoint_logical_path,
        image=image,
        container_name=container_name,
    )
    started = time.monotonic()
    process = docker_runner(
        command,
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    elapsed = time.monotonic() - started
    stdout_path = report_path.with_suffix(".stdout.log")
    stderr_path = report_path.with_suffix(".stderr.log")
    write_text_atomic(stdout_path, process.stdout)
    write_text_atomic(stderr_path, process.stderr)
    result_path = evidence / "reproduced-summary.json"
    if process.returncode != 0 or not result_path.is_file():
        return {
            "status": "failed",
            "exit_code": process.returncode,
            "command": command,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "rf_e2_awarded": False,
        }

    reproduced = read_json(result_path)
    # Read the original comparison target on the host only after candidate
    # execution.  It is used to construct the unsigned comparison report and
    # is not exposed as a separate mutable input to the worker.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="rf-clean-room-compare-") as temp:
        extracted = Path(temp) / "package"
        extracted.mkdir()
        _safe_extract(package, extracted)
        original = read_json(extracted / "original-evaluation.json")

    job = create_reproduction_job(
        package_path=package,
        manifest=manifest,
        policy=policy,
        requested_by=requested_by,
    ).model_copy(update={"status": ReproductionJobStatus.SUCCEEDED})
    image_id = hashlib.sha256(image.encode("utf-8")).hexdigest()
    worker = WorkerIdentity(
        trust_domain_id="local-docker-clean-room",
        cloud="local-docker",
        account_id=None,
        instance_id=container_name,
        image_id=image,
        instance_type="ephemeral-container",
        boot_id=str(uuid.uuid4()),
        agent_digest=image_id,
    )
    isolation = IsolationReport(
        fresh_worker=True,
        ephemeral_worker=True,
        development_directory_mounted=False,
        original_database_accessible=False,
        cache_reused=False,
        sealed_assets_only=True,
        candidate_network_enabled=False,
        credentials_visible_to_candidate=False,
        signing_key_accessible_to_worker=False,
        original_control_plane_accessible=False,
    )
    report = build_unsigned_worker_report(
        job=job,
        manifest=manifest,
        policy=policy,
        worker_identity=worker,
        isolation=isolation,
        original_summary=original,
        reproduced_summary=reproduced,
        executed_run_cells=int(reproduced.get("executed_run_cells", 0)),
        qualified_run_cells=int(reproduced.get("qualified_run_cells", 0)),
        checker_passed=True,
        artifact_hashes=dict(reproduced.get("output_hashes") or {}),
        resource_telemetry={
            "elapsed_seconds": elapsed,
            "container_image": image,
            "network": "none",
            "root_filesystem": "read_only",
            "scientific_input_count": 1,
            "external_verifier_receipt_present": False,
        },
    )
    write_json_atomic(report_path, report)
    return {
        "status": "controlled_clean_room_replay_completed",
        "job": job.model_dump(mode="json"),
        "worker_report": report.model_dump(mode="json"),
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path),
        "unsigned_worker_report": True,
        "comparison_passed": report.comparison.passed(),
        "coverage_full": report.coverage.full(),
        "clean_room_invariants_hold": isolation.clean_room_invariants_hold(),
        "evidence_grade": "RF-E1_package_verified",
        "rf_e2_awarded": False,
        "rf_e2_blocker": "external verifier receipt with independent KMS/HSM custody is absent",
    }


__all__ = [
    "build_clean_room_docker_command",
    "run_clean_room_docker_reproduction",
]
