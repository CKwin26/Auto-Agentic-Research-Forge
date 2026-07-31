"""Machine verification for an externally operated public replay receipt.

Cryptographic validity and scientific replay equivalence are deterministic.
Operator independence is not: a maintainer must explicitly approve the public
identity/key relationship after reviewing evidence outside the receipt itself.
"""

from __future__ import annotations

import base64
import hashlib
import math
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, model_validator

from .models import StrictModel
from .reproduction_policy import canonical_json_bytes
from .reproduction_domain import SHA256_PATTERN


class IndependentReplayExpectation(StrictModel):
    schema_version: int = 1
    expectation_id: str = Field(min_length=1, max_length=200)
    package_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_accuracy: float
    treatment_accuracy: float
    paired_effect: float
    verdict: str = Field(min_length=1, max_length=100)
    numeric_tolerance: float = Field(default=1e-12, ge=0)

    @model_validator(mode="after")
    def finite_numbers(self) -> "IndependentReplayExpectation":
        if not all(
            math.isfinite(value)
            for value in (
                self.baseline_accuracy,
                self.treatment_accuracy,
                self.paired_effect,
                self.numeric_tolerance,
            )
        ):
            raise ValueError("replay expectations must contain finite numbers")
        return self


class IndependentScientificReplayReceipt(StrictModel):
    schema_version: int = 1
    receipt_kind: Literal["independent_scientific_replay"]
    operator_identity: str = Field(min_length=1, max_length=500)
    operator_independent_of_project_owner: Literal[True]
    operator_controls_signing_key: Literal[True]
    package_sha256: str = Field(pattern=SHA256_PATTERN)
    environment: dict[str, str] = Field(min_length=1)
    standalone_verifier_passed: Literal[True]
    baseline_accuracy: float
    treatment_accuracy: float
    paired_effect: float
    verdict: str = Field(min_length=1, max_length=100)
    unrecorded_inputs_required: Literal[False]
    manual_code_patch_required: Literal[False]
    original_agent_conversation_required: Literal[False]
    replay_log_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: str = Field(min_length=1, max_length=100)
    signature_method: Literal["Ed25519"]
    key_id: str = Field(min_length=1, max_length=500)
    public_key_fingerprint: str = Field(pattern=SHA256_PATTERN)
    signature_or_bundle_url: str = Field(min_length=1, max_length=4_000)
    signature_base64: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def finite_numbers_and_environment(
        self,
    ) -> "IndependentScientificReplayReceipt":
        if not all(
            math.isfinite(value)
            for value in (
                self.baseline_accuracy,
                self.treatment_accuracy,
                self.paired_effect,
            )
        ):
            raise ValueError("replay receipt must contain finite numbers")
        required = {"os", "python"}
        if not required.issubset(self.environment) or any(
            not self.environment[item].strip() for item in required
        ):
            raise ValueError("receipt environment requires os and python")
        return self


class IndependentReplayReceiptVerification(StrictModel):
    schema_version: int = 1
    operator_identity: str
    package_digest_matches: bool
    numerical_results_match: bool
    verdict_matches: bool
    signature_valid: bool
    operational_independence_declared: bool
    identity_independence_approved: bool
    c5_eligible: bool
    findings: list[str] = Field(default_factory=list)


def independent_receipt_signing_bytes(
    receipt: IndependentScientificReplayReceipt,
) -> bytes:
    """Canonical bytes signed by the external operator."""

    return canonical_json_bytes(
        receipt.model_dump(mode="json", exclude={"signature_base64"})
    )


def verify_independent_replay_receipt(
    receipt: IndependentScientificReplayReceipt,
    *,
    expectation: IndependentReplayExpectation,
    trusted_public_keys: dict[str, bytes],
    project_owner_identities: set[str],
    identity_independence_approved: bool,
) -> IndependentReplayReceiptVerification:
    """Verify a signed replay without inferring operator independence.

    ``identity_independence_approved`` is an explicit human trust decision.  A
    self-asserted identity in the receipt can never set it automatically.
    """

    findings: list[str] = []
    digest_matches = receipt.package_sha256 == expectation.package_sha256
    if not digest_matches:
        findings.append("package digest does not match the frozen expectation")

    tolerance = expectation.numeric_tolerance
    numeric_matches = all(
        abs(observed - expected) <= tolerance
        for observed, expected in (
            (receipt.baseline_accuracy, expectation.baseline_accuracy),
            (receipt.treatment_accuracy, expectation.treatment_accuracy),
            (receipt.paired_effect, expectation.paired_effect),
        )
    )
    if not numeric_matches:
        findings.append("recomputed metrics differ from the frozen expectation")
    verdict_matches = receipt.verdict == expectation.verdict
    if not verdict_matches:
        findings.append("reproduced verdict differs from the frozen expectation")

    normalized_operator = receipt.operator_identity.strip().casefold()
    normalized_owners = {
        identity.strip().casefold() for identity in project_owner_identities
    }
    not_owner = normalized_operator not in normalized_owners
    if not not_owner:
        findings.append("operator identity is a project-owner identity")
    if not identity_independence_approved:
        findings.append("operator identity/key independence awaits human approval")

    signature_valid = False
    public_key_pem = trusted_public_keys.get(receipt.key_id)
    if public_key_pem is None:
        findings.append("receipt key is absent from the trusted identity registry")
    else:
        fingerprint = hashlib.sha256(public_key_pem).hexdigest()
        if fingerprint != receipt.public_key_fingerprint:
            findings.append("trusted public-key fingerprint does not match receipt")
        else:
            try:
                key = serialization.load_pem_public_key(public_key_pem)
                signature = base64.b64decode(
                    receipt.signature_base64, validate=True
                )
                if not isinstance(key, Ed25519PublicKey):
                    findings.append("trusted receipt key is not Ed25519")
                else:
                    key.verify(signature, independent_receipt_signing_bytes(receipt))
                    signature_valid = True
            except Exception:
                findings.append("receipt signature verification failed")

    operational = all(
        (
            receipt.operator_independent_of_project_owner,
            receipt.operator_controls_signing_key,
            receipt.standalone_verifier_passed,
            not receipt.unrecorded_inputs_required,
            not receipt.manual_code_patch_required,
            not receipt.original_agent_conversation_required,
        )
    )
    c5_eligible = all(
        (
            digest_matches,
            numeric_matches,
            verdict_matches,
            signature_valid,
            operational,
            not_owner,
            identity_independence_approved,
        )
    )
    return IndependentReplayReceiptVerification(
        operator_identity=receipt.operator_identity,
        package_digest_matches=digest_matches,
        numerical_results_match=numeric_matches,
        verdict_matches=verdict_matches,
        signature_valid=signature_valid,
        operational_independence_declared=operational,
        identity_independence_approved=(
            identity_independence_approved and not_owner
        ),
        c5_eligible=c5_eligible,
        findings=findings,
    )


__all__ = [
    "IndependentReplayExpectation",
    "IndependentReplayReceiptVerification",
    "IndependentScientificReplayReceipt",
    "independent_receipt_signing_bytes",
    "verify_independent_replay_receipt",
]
