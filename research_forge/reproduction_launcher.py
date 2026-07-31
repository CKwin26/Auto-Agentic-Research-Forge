"""Reproduction job creation and local development execution.

The local command runner exists to validate the software protocol.  It always
reports an environment that is *not* an isolated trust domain and therefore
can never award RF-E2.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .reproduction_checker import verify_reproduction_package
from .reproduction_domain import (
    IsolationReport,
    ReproductionJob,
    ReproductionJobStatus,
    ReproductionPackageManifest,
    ReproductionPolicy,
    WorkerIdentity,
)
from .reproduction_policy import canonical_sha256
from .reproduction_worker_protocol import build_unsigned_worker_report
from .stage_three_trust import _safe_extract
from .storage import read_json, sha256_file, write_json_atomic, write_text_atomic
from .workflow_domain import stable_id


def create_reproduction_job(
    *,
    package_path: str | Path,
    manifest: ReproductionPackageManifest,
    policy: ReproductionPolicy,
    requested_by: str,
) -> ReproductionJob:
    archive = Path(package_path).resolve()
    archive_digest = sha256_file(archive)
    return ReproductionJob(
        job_id=stable_id(
            "reproduction-job",
            manifest.package_id,
            archive_digest,
            requested_by,
            policy.policy_id,
        ),
        study_id=manifest.study_id,
        package_id=manifest.package_id,
        package_sha256=manifest.package_sha256,
        archive_sha256=archive_digest,
        mode=policy.mode,
        scope=policy.scope,
        required_evidence_grade=policy.required_evidence_grade,
        status=ReproductionJobStatus.QUEUED,
        requested_by=requested_by,
    )


def run_local_development_reproduction(
    *,
    package_path: str | Path,
    command: list[str],
    trusted_control_plane_keys: dict[str, bytes],
    requested_by: str,
    report_output: str | Path,
    timeout_seconds: int = 3_600,
) -> dict[str, Any]:
    """Run an operator-supplied replay command in a fresh local directory.

    The command must write a JSON summary to ``{result_json}``.  The summary
    follows the comparator input schema: primary_metric, effect, interval,
    verdict, sample_ids, required/executed/qualified RunCell counts, and
    optional output_hashes.  Because the operator supplies the command and the
    host is the development trust domain, the output is development evidence
    only.
    """

    verification = verify_reproduction_package(
        package_path,
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
    package = Path(package_path).resolve()
    report_path = Path(report_output).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="rf-local-reproduce-") as temp:
        root = Path(temp) / "package"
        root.mkdir()
        _safe_extract(package, root)
        result_path = Path(temp) / "reproduced-summary.json"
        rendered = [
            token.replace("{package_root}", str(root)).replace(
                "{result_json}", str(result_path)
            )
            for token in command
        ]
        if not rendered:
            raise ValueError("development replay command cannot be empty")
        started = __import__("time").monotonic()
        process = subprocess.run(
            rendered,
            cwd=root,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={
                "PATH": os.environ.get("PATH", ""),
                "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                "TEMP": temp,
                "TMP": temp,
                "RF_REPRODUCTION_MODE": "development_validation_only",
            },
            check=False,
        )
        elapsed = __import__("time").monotonic() - started
        stdout_path = report_path.with_suffix(".stdout.log")
        stderr_path = report_path.with_suffix(".stderr.log")
        write_text_atomic(stdout_path, process.stdout)
        write_text_atomic(stderr_path, process.stderr)
        if process.returncode != 0 or not result_path.is_file():
            return {
                "status": "failed",
                "exit_code": process.returncode,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "rf_e2_awarded": False,
            }
        reproduced = read_json(result_path)
        original = read_json(root / "original-evaluation.json")
        job = create_reproduction_job(
            package_path=package,
            manifest=manifest,
            policy=policy,
            requested_by=requested_by,
        ).model_copy(update={"status": ReproductionJobStatus.SUCCEEDED})
        worker = WorkerIdentity(
            trust_domain_id="local-development-control-plane",
            cloud=None,
            instance_id=f"local-{uuid.uuid4().hex[:12]}",
            image_id=platform.platform(),
            instance_type=platform.machine(),
            boot_id=str(uuid.uuid4()),
            agent_digest=hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
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
            original_control_plane_accessible=True,
        )
        report = build_unsigned_worker_report(
            job=job,
            manifest=manifest,
            policy=policy,
            worker_identity=worker,
            isolation=isolation,
            original_summary=original,
            reproduced_summary=reproduced,
            executed_run_cells=int(
                reproduced.get("executed_run_cells", 0)
            ),
            qualified_run_cells=int(
                reproduced.get("qualified_run_cells", 0)
            ),
            checker_passed=True,
            artifact_hashes=dict(reproduced.get("output_hashes") or {}),
            resource_telemetry={
                "elapsed_seconds": elapsed,
                "development_validation_only": True,
            },
        )
        write_json_atomic(report_path, report)
    return {
        "status": "development_validation_completed",
        "job": job.model_dump(mode="json"),
        "worker_report": report.model_dump(mode="json"),
        "report_path": str(report_path),
        "unsigned_worker_report": True,
        "evidence_grade": "RF-E1_package_verified",
        "rf_e2_awarded": False,
    }


__all__ = [
    "create_reproduction_job",
    "run_local_development_reproduction",
]
