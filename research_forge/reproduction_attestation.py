"""Independent-verifier receipt creation and signature verification."""

from __future__ import annotations

import base64
import hashlib
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .reproduction_domain import (
    ExternalVerifierIdentity,
    ReproductionJob,
    ReproductionPolicy,
    ReproductionReceipt,
    ReproductionResult,
    ReproductionScope,
    ResearchForgeEvidenceGrade,
    SignedReproductionReceipt,
    WorkerExecutionReport,
)
from .reproduction_policy import canonical_json_bytes, canonical_sha256
from .workflow_domain import stable_id


class ReceiptSigningBackend(Protocol):
    """A KMS/HSM adapter.  Worker code must never implement this protocol."""

    @property
    def key_id(self) -> str: ...

    @property
    def algorithm(self) -> str: ...

    @property
    def key_custody(self) -> str: ...

    def sign(self, payload: bytes) -> bytes: ...

    def public_key_pem(self) -> bytes: ...


class LocalDevelopmentEd25519Signer:
    """Local signer for tests and RF-E1 development validation only."""

    def __init__(self, private_key_pem: bytes, *, key_id: str) -> None:
        key = serialization.load_pem_private_key(
            private_key_pem, password=None
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("development receipt key must be Ed25519")
        self._key = key
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def algorithm(self) -> str:
        return "Ed25519"

    @property
    def key_custody(self) -> str:
        return "local_development"

    def sign(self, payload: bytes) -> bytes:
        return self._key.sign(payload)

    def public_key_pem(self) -> bytes:
        return self._key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )


def create_reproduction_receipt(
    *,
    job: ReproductionJob,
    policy: ReproductionPolicy,
    report: WorkerExecutionReport,
    verifier_identity: ExternalVerifierIdentity,
    trusted_package: bool,
    development_validation: bool = False,
) -> ReproductionReceipt:
    """Validate an unsigned worker report and create a verifier-owned receipt."""

    if report.job_id != job.job_id:
        raise ValueError("worker report job does not match")
    if report.study_id != job.study_id:
        raise ValueError("worker report Study does not match")
    if report.package_id != job.package_id:
        raise ValueError("worker report package does not match")
    if report.package_sha256 != job.package_sha256:
        raise ValueError("worker report package digest does not match")
    if report.archive_sha256 != job.archive_sha256:
        raise ValueError("worker report archive digest does not match")
    if report.profile_id != policy.profile_id:
        raise ValueError("worker report profile does not match policy")

    clean_room = report.isolation.clean_room_invariants_hold()
    full_coverage = report.coverage.full()
    comparison_passed = report.comparison.passed()
    verified = trusted_package and report.checker_passed

    if development_validation:
        result = ReproductionResult.DEVELOPMENT_VALIDATION
        grade = ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    elif policy.scope is ReproductionScope.PARTIAL:
        result = ReproductionResult.PARTIAL_CHECK
        grade = ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    elif report.failure and report.failure.get("kind") == "missing_asset":
        result = ReproductionResult.BLOCKED_MISSING_ASSET
        grade = ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    elif not verified:
        result = ReproductionResult.BLOCKED_POLICY
        grade = ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    elif not clean_room or not full_coverage or not comparison_passed:
        result = ReproductionResult.MISMATCH
        grade = ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    else:
        result = ReproductionResult.REPRODUCED
        grade = ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED

    receipt = ReproductionReceipt(
        receipt_id=stable_id(
            "reproduction-receipt",
            job.job_id,
            canonical_sha256(report),
            verifier_identity.verifier_id,
        ),
        job_id=job.job_id,
        study_id=job.study_id,
        original_package_sha256=job.package_sha256,
        original_archive_sha256=job.archive_sha256,
        profile_id=policy.profile_id,
        reproduction_scope=policy.scope,
        reproduction_mode=policy.mode,
        worker_identity=report.worker_identity,
        isolation=report.isolation,
        coverage=report.coverage,
        comparison=report.comparison,
        result=result,
        evidence_grade_awarded=grade,
        verifier_identity=verifier_identity,
        worker_report_sha256=canonical_sha256(report),
    )
    return receipt


def sign_reproduction_receipt(
    receipt: ReproductionReceipt,
    *,
    signer: ReceiptSigningBackend,
) -> SignedReproductionReceipt:
    """Sign a receipt after the independent verifier has validated the report."""

    if signer.key_id != receipt.verifier_identity.key_id:
        raise ValueError("signing backend key does not match verifier identity")
    if (
        receipt.evidence_grade_awarded
        is ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
        and signer.key_custody
        not in {"kms", "hsm", "external_organization"}
    ):
        raise ValueError("a local or worker-held key cannot sign RF-E2")
    if signer.key_custody != receipt.verifier_identity.key_custody:
        raise ValueError("signing backend custody does not match verifier identity")
    public_key_pem = signer.public_key_pem()
    fingerprint = hashlib.sha256(public_key_pem).hexdigest()
    signature = signer.sign(canonical_json_bytes(receipt))
    return SignedReproductionReceipt(
        receipt=receipt,
        algorithm=signer.algorithm,
        key_id=signer.key_id,
        public_key_fingerprint=fingerprint,
        signature_base64=base64.b64encode(signature).decode("ascii"),
    )


def verify_signed_reproduction_receipt(
    signed: SignedReproductionReceipt,
    *,
    trusted_public_keys: dict[str, bytes],
) -> bool:
    """Verify against an external identity registry, never an embedded key."""

    public_key_pem = trusted_public_keys.get(signed.key_id)
    if public_key_pem is None:
        return False
    if hashlib.sha256(public_key_pem).hexdigest() != (
        signed.public_key_fingerprint
    ):
        return False
    try:
        key = serialization.load_pem_public_key(public_key_pem)
        signature = base64.b64decode(signed.signature_base64, validate=True)
        payload = canonical_json_bytes(signed.receipt)
        if signed.algorithm == "Ed25519" and isinstance(
            key, Ed25519PublicKey
        ):
            key.verify(signature, payload)
            return True
    except Exception:
        return False
    return False


__all__ = [
    "LocalDevelopmentEd25519Signer",
    "ReceiptSigningBackend",
    "create_reproduction_receipt",
    "sign_reproduction_receipt",
    "verify_signed_reproduction_receipt",
]
