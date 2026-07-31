"""Verifier-owned receipt issuance, persistence, and conflict assessment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .reproduction_attestation import (
    ReceiptSigningBackend,
    create_reproduction_receipt,
    sign_reproduction_receipt,
    verify_signed_reproduction_receipt,
)
from .reproduction_checker import verify_reproduction_package
from .reproduction_domain import (
    ExternalVerifierIdentity,
    ReproductionConflict,
    ReproductionEvidenceAssessment,
    ReproductionJob,
    ReproductionJobStatus,
    ReproductionPackageManifest,
    ReproductionPolicy,
    ReproductionResult,
    ResearchForgeEvidenceGrade,
    SignedReproductionReceipt,
    WorkerExecutionReport,
)
from .stage_three_trust import _safe_extract
from .storage import read_json, write_json_atomic
from .workflow_domain import stable_id


def load_reproduction_trust_registry(
    workflow_root: str | Path,
    *,
    registry: str,
) -> dict[str, bytes]:
    """Load public keys only; private/KMS signing material is never accepted."""

    if registry not in {
        "control-plane-signers",
        "reproduction-verifiers",
    }:
        raise ValueError("unknown reproduction trust registry")
    path = (
        Path(workflow_root).resolve()
        / "reproduction-trust"
        / f"{registry}.json"
    )
    if not path.is_file():
        return {}
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("reproduction trust registry must be an object")
    keys: dict[str, bytes] = {}
    for key_id, pem in payload.items():
        if not isinstance(key_id, str) or not isinstance(pem, str):
            raise ValueError("trust registry entries must be PEM strings")
        if "PRIVATE KEY" in pem:
            raise ValueError("trust registry must never contain private keys")
        keys[key_id] = pem.encode("utf-8")
    return keys


class ReproductionStore:
    """Append-only workflow persistence for reproduction records."""

    def __init__(self, workflow_root: str | Path) -> None:
        self.root = Path(workflow_root).resolve()

    def _root(self, study_id: str) -> Path:
        return self.root / "studies" / study_id / "reproductions"

    @staticmethod
    def _save_immutable(path: Path, value: Any, label: str) -> None:
        payload = (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else value
        )
        if path.is_file() and read_json(path) != payload:
            raise ValueError(f"{label} is append-only")
        write_json_atomic(path, payload)

    def save_policy(self, policy: ReproductionPolicy) -> ReproductionPolicy:
        self._save_immutable(
            self._root(policy.study_id)
            / "policies"
            / f"{policy.policy_id}.json",
            policy,
            "ReproductionPolicy",
        )
        return policy

    def save_job(self, job: ReproductionJob) -> ReproductionJob:
        path = self._root(job.study_id) / "jobs" / f"{job.job_id}.json"
        if path.is_file():
            previous = ReproductionJob.model_validate(read_json(path))
            allowed = {
                ReproductionJobStatus.QUEUED: {
                    ReproductionJobStatus.LAUNCHING,
                    ReproductionJobStatus.CANCELLED,
                    ReproductionJobStatus.BLOCKED,
                },
                ReproductionJobStatus.LAUNCHING: {
                    ReproductionJobStatus.RUNNING,
                    ReproductionJobStatus.FAILED,
                    ReproductionJobStatus.CANCELLED,
                },
                ReproductionJobStatus.RUNNING: {
                    ReproductionJobStatus.VERIFYING,
                    ReproductionJobStatus.FAILED,
                    ReproductionJobStatus.CANCELLED,
                },
                ReproductionJobStatus.VERIFYING: {
                    ReproductionJobStatus.SUCCEEDED,
                    ReproductionJobStatus.MISMATCH,
                    ReproductionJobStatus.BLOCKED,
                    ReproductionJobStatus.FAILED,
                },
            }
            if (
                previous.status != job.status
                and job.status not in allowed.get(previous.status, set())
            ):
                raise ValueError(
                    f"invalid reproduction job transition: "
                    f"{previous.status} -> {job.status}"
                )
        write_json_atomic(path, job)
        return job

    def load_job(self, study_id: str, job_id: str) -> ReproductionJob:
        return ReproductionJob.model_validate(
            read_json(self._root(study_id) / "jobs" / f"{job_id}.json")
        )

    def list_jobs(self, study_id: str) -> list[ReproductionJob]:
        return [
            ReproductionJob.model_validate(read_json(path))
            for path in sorted(
                (self._root(study_id) / "jobs").glob(
                    "reproduction-job-*.json"
                )
            )
        ]

    def save_worker_report(
        self, report: WorkerExecutionReport
    ) -> WorkerExecutionReport:
        self._save_immutable(
            self._root(report.study_id)
            / "worker_reports"
            / f"{report.report_id}.json",
            report,
            "WorkerExecutionReport",
        )
        return report

    def save_receipt(
        self, receipt: SignedReproductionReceipt
    ) -> SignedReproductionReceipt:
        study_id = receipt.receipt.study_id
        self._save_immutable(
            self._root(study_id)
            / "receipts"
            / f"{receipt.receipt.receipt_id}.json",
            receipt,
            "ReproductionReceipt",
        )
        return receipt

    def list_receipts(self, study_id: str) -> list[SignedReproductionReceipt]:
        return [
            SignedReproductionReceipt.model_validate(read_json(path))
            for path in sorted(
                (self._root(study_id) / "receipts").glob(
                    "reproduction-receipt-*.json"
                )
            )
        ]

    def save_conflict(
        self, conflict: ReproductionConflict
    ) -> ReproductionConflict:
        self._save_immutable(
            self._root(conflict.study_id)
            / "conflicts"
            / f"{conflict.conflict_id}.json",
            conflict,
            "ReproductionConflict",
        )
        return conflict

    def list_conflicts(self, study_id: str) -> list[ReproductionConflict]:
        return [
            ReproductionConflict.model_validate(read_json(path))
            for path in sorted(
                (self._root(study_id) / "conflicts").glob(
                    "reproduction-conflict-*.json"
                )
            )
        ]

    def save_assessment(
        self, assessment: ReproductionEvidenceAssessment
    ) -> ReproductionEvidenceAssessment:
        write_json_atomic(
            self._root(assessment.study_id) / "evidence-assessment.json",
            assessment,
        )
        return assessment


def verify_and_sign_worker_report(
    *,
    package_path: str | Path,
    trusted_control_plane_keys: dict[str, bytes],
    job: ReproductionJob,
    policy: ReproductionPolicy,
    report: WorkerExecutionReport,
    verifier_identity: ExternalVerifierIdentity,
    signer: ReceiptSigningBackend,
    development_validation: bool = False,
) -> tuple[SignedReproductionReceipt, ReproductionConflict | None]:
    """Issue a receipt only in the verifier trust domain."""

    package_check = verify_reproduction_package(
        package_path,
        trusted_control_plane_keys=trusted_control_plane_keys,
    )
    if not package_check["trusted"]:
        raise ValueError("independent verifier does not trust the package signer")
    manifest = ReproductionPackageManifest.model_validate(
        package_check["manifest"]
    )
    if manifest.package_id != job.package_id:
        raise ValueError("job package identity differs from frozen manifest")
    if verifier_identity.trust_domain_id == manifest.signing_identity:
        raise ValueError(
            "package control plane cannot act as reproduction verifier"
        )
    receipt = create_reproduction_receipt(
        job=job,
        policy=policy,
        report=report,
        verifier_identity=verifier_identity,
        trusted_package=package_check["trusted"],
        development_validation=development_validation,
    )
    signed = sign_reproduction_receipt(receipt, signer=signer)
    conflict = None
    if receipt.result in {
        ReproductionResult.MISMATCH,
        ReproductionResult.FAILED_EXECUTION,
    }:
        with __import__("tempfile").TemporaryDirectory(
            prefix="rf-repro-conflict-"
        ) as temp:
            root = Path(temp) / "package"
            root.mkdir()
            _safe_extract(Path(package_path).resolve(), root)
            original = read_json(root / "original-evaluation.json")
        conflict = ReproductionConflict(
            conflict_id=stable_id(
                "reproduction-conflict",
                job.study_id,
                job.job_id,
                receipt.receipt_id,
            ),
            study_id=job.study_id,
            job_id=job.job_id,
            original_verdict_id=str(
                original.get("study_verdict_id") or "unknown-preserved"
            ),
            receipt_id=receipt.receipt_id,
            reproduction_result=receipt.result,
            details={
                "original_verdict": receipt.comparison.original_verdict,
                "reproduced_verdict": receipt.comparison.reproduced_verdict,
                "original_verdict_was_not_modified": True,
            },
        )
    return signed, conflict


def assess_reproduction_evidence(
    *,
    study_id: str,
    package_verified: bool,
    receipts: list[SignedReproductionReceipt],
    trusted_verifier_keys: dict[str, bytes],
    conflicts: list[ReproductionConflict] | None = None,
) -> ReproductionEvidenceAssessment:
    valid = [
        item
        for item in receipts
        if item.receipt.study_id == study_id
        and verify_signed_reproduction_receipt(
            item, trusted_public_keys=trusted_verifier_keys
        )
    ]
    grades = {
        item.receipt.evidence_grade_awarded for item in valid
    }
    if ResearchForgeEvidenceGrade.RF_E4_EXTERNALLY_REPLICATED in grades:
        highest = ResearchForgeEvidenceGrade.RF_E4_EXTERNALLY_REPLICATED
    elif ResearchForgeEvidenceGrade.RF_E3_INDEPENDENTLY_REPRODUCED in grades:
        highest = ResearchForgeEvidenceGrade.RF_E3_INDEPENDENTLY_REPRODUCED
    elif ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED in grades:
        highest = ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
    else:
        highest = ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    open_conflicts = any(
        item.status.value == "open" for item in (conflicts or [])
    )
    return ReproductionEvidenceAssessment(
        study_id=study_id,
        highest_grade=highest,
        package_verified=package_verified,
        clean_room_replayed=(
            ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED in grades
            or ResearchForgeEvidenceGrade.RF_E3_INDEPENDENTLY_REPRODUCED
            in grades
            or ResearchForgeEvidenceGrade.RF_E4_EXTERNALLY_REPLICATED
            in grades
        ),
        independently_reproduced=(
            ResearchForgeEvidenceGrade.RF_E3_INDEPENDENTLY_REPRODUCED
            in grades
            or ResearchForgeEvidenceGrade.RF_E4_EXTERNALLY_REPLICATED
            in grades
        ),
        externally_replicated=(
            ResearchForgeEvidenceGrade.RF_E4_EXTERNALLY_REPLICATED in grades
        ),
        receipt_ids=[item.receipt.receipt_id for item in valid],
        conflicts_open=open_conflicts,
        publication_readiness_effect=(
            "under_review"
            if open_conflicts
            else "positive"
            if highest
            is not ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
            else "none"
        ),
        reasons=(
            ["reproduction conflict preserves the original verdict for diagnosis"]
            if open_conflicts
            else []
        ),
    )


__all__ = [
    "ReproductionStore",
    "assess_reproduction_evidence",
    "load_reproduction_trust_registry",
    "verify_and_sign_worker_report",
]
