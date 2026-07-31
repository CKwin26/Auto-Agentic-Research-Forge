"""Unsigned worker-report protocol.

Workers can execute experiments and submit evidence.  They cannot create a
formal ReproductionReceipt and they never receive receipt-signing authority.
"""

from __future__ import annotations

from typing import Any

from .reproduction_domain import (
    IsolationReport,
    ReproductionCoverage,
    ReproductionJob,
    ReproductionPackageManifest,
    ReproductionPolicy,
    WorkerExecutionReport,
    WorkerIdentity,
)
from .reproduction_policy import compare_reproduction
from .workflow_domain import stable_id


def build_unsigned_worker_report(
    *,
    job: ReproductionJob,
    manifest: ReproductionPackageManifest,
    policy: ReproductionPolicy,
    worker_identity: WorkerIdentity,
    isolation: IsolationReport,
    original_summary: dict[str, Any],
    reproduced_summary: dict[str, Any],
    executed_run_cells: int,
    qualified_run_cells: int,
    checker_passed: bool,
    artifact_hashes: dict[str, str] | None = None,
    resource_telemetry: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
) -> WorkerExecutionReport:
    if manifest.package_id != job.package_id:
        raise ValueError("job does not bind the supplied package")
    if manifest.package_sha256 != job.package_sha256:
        raise ValueError("job package digest does not match")
    comparison = compare_reproduction(
        profile_id=manifest.profile_id,
        original=original_summary,
        reproduced=reproduced_summary,
        policy=policy,
    )
    sample_ids_complete = (
        comparison.sample_ids_match
        if policy.comparison_policy.sample_ids_must_match
        else bool(reproduced_summary.get("sample_ids"))
    )
    coverage = ReproductionCoverage(
        required_run_cells=manifest.required_run_cells,
        executed_run_cells=executed_run_cells,
        qualified_run_cells=qualified_run_cells,
        sample_ids_complete=sample_ids_complete,
    )
    report_id = stable_id(
        "worker-report",
        job.job_id,
        worker_identity.instance_id,
        reproduced_summary,
        artifact_hashes or {},
    )
    return WorkerExecutionReport(
        report_id=report_id,
        job_id=job.job_id,
        study_id=job.study_id,
        package_id=manifest.package_id,
        package_sha256=manifest.package_sha256,
        archive_sha256=job.archive_sha256,
        profile_id=manifest.profile_id,
        worker_identity=worker_identity,
        isolation=isolation,
        coverage=coverage,
        comparison=comparison,
        checker_passed=checker_passed,
        artifact_hashes=artifact_hashes or {},
        resource_telemetry=resource_telemetry or {},
        failure=failure,
    )


__all__ = ["build_unsigned_worker_report"]
