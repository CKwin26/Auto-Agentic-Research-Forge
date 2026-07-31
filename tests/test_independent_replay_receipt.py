from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from research_forge.independent_replay_receipt import (
    IndependentReplayExpectation,
    IndependentScientificReplayReceipt,
    independent_receipt_signing_bytes,
    verify_independent_replay_receipt,
)
from research_forge.cli import main as cli_main


PACKAGE_SHA = "8c788149ac3a892e8e83d6f3d217d7671d50d507094611317ca273d7943a0eab"


def _expectation() -> IndependentReplayExpectation:
    return IndependentReplayExpectation(
        expectation_id="rf-public-openml-39-hidden-target-v1",
        package_sha256=PACKAGE_SHA,
        baseline_accuracy=0.5714285714285714,
        treatment_accuracy=0.9047619047619048,
        paired_effect=0.33333333333333337,
        verdict="supported",
    )


def _signed_receipt(
    *,
    operator: str = "independent-lab",
    effect: float = 0.33333333333333337,
) -> tuple[IndependentScientificReplayReceipt, bytes]:
    key = Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    unsigned = {
        "schema_version": 1,
        "receipt_kind": "independent_scientific_replay",
        "operator_identity": operator,
        "operator_independent_of_project_owner": True,
        "operator_controls_signing_key": True,
        "package_sha256": PACKAGE_SHA,
        "environment": {"os": "Ubuntu 24.04", "python": "3.12.4"},
        "standalone_verifier_passed": True,
        "baseline_accuracy": 0.5714285714285714,
        "treatment_accuracy": 0.9047619047619048,
        "paired_effect": effect,
        "verdict": "supported",
        "unrecorded_inputs_required": False,
        "manual_code_patch_required": False,
        "original_agent_conversation_required": False,
        "replay_log_sha256": "a" * 64,
        "created_at": "2026-08-01T00:00:00Z",
        "signature_method": "Ed25519",
        "key_id": "independent-lab-key-1",
        "public_key_fingerprint": hashlib.sha256(public_key).hexdigest(),
        "signature_or_bundle_url": "https://example.org/replay/receipt.sig",
        "signature_base64": "pending",
    }
    pending = IndependentScientificReplayReceipt.model_validate(unsigned)
    signature = key.sign(independent_receipt_signing_bytes(pending))
    receipt = pending.model_copy(
        update={"signature_base64": base64.b64encode(signature).decode("ascii")}
    )
    return receipt, public_key


def test_valid_external_receipt_becomes_c5_eligible_only_after_identity_approval() -> None:
    receipt, public_key = _signed_receipt()

    pending = verify_independent_replay_receipt(
        receipt,
        expectation=_expectation(),
        trusted_public_keys={receipt.key_id: public_key},
        project_owner_identities={"CKwin26"},
        identity_independence_approved=False,
    )
    approved = verify_independent_replay_receipt(
        receipt,
        expectation=_expectation(),
        trusted_public_keys={receipt.key_id: public_key},
        project_owner_identities={"CKwin26"},
        identity_independence_approved=True,
    )

    assert pending.signature_valid is True
    assert pending.numerical_results_match is True
    assert pending.c5_eligible is False
    assert approved.c5_eligible is True


def test_self_asserted_project_owner_never_becomes_independent() -> None:
    receipt, public_key = _signed_receipt(operator="CKwin26")
    result = verify_independent_replay_receipt(
        receipt,
        expectation=_expectation(),
        trusted_public_keys={receipt.key_id: public_key},
        project_owner_identities={"CKwin26"},
        identity_independence_approved=True,
    )

    assert result.identity_independence_approved is False
    assert result.c5_eligible is False
    assert "operator identity is a project-owner identity" in result.findings


def test_changed_metric_or_signature_is_rejected() -> None:
    receipt, public_key = _signed_receipt(effect=0.2)
    changed_signature = receipt.model_copy(update={"signature_base64": "AAAA"})
    result = verify_independent_replay_receipt(
        changed_signature,
        expectation=_expectation(),
        trusted_public_keys={receipt.key_id: public_key},
        project_owner_identities={"CKwin26"},
        identity_independence_approved=True,
    )

    assert result.numerical_results_match is False
    assert result.signature_valid is False
    assert result.c5_eligible is False


def test_cli_requires_explicit_identity_approval(
    tmp_path: Path, capsys
) -> None:
    receipt, public_key = _signed_receipt()
    receipt_path = tmp_path / "receipt.json"
    expectation_path = tmp_path / "expectation.json"
    key_path = tmp_path / "operator.pem"
    receipt_path.write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    expectation_path.write_text(
        json.dumps(_expectation().model_dump(mode="json")), encoding="utf-8"
    )
    key_path.write_bytes(public_key)
    arguments = [
        "verify-independent-replay-receipt",
        str(receipt_path),
        "--expectation",
        str(expectation_path),
        "--trusted-key",
        f"{receipt.key_id}={key_path}",
        "--project-owner-identity",
        "CKwin26",
    ]

    assert cli_main(arguments) == 2
    pending = json.loads(capsys.readouterr().out)
    assert pending["c5_eligible"] is False
    assert cli_main([*arguments, "--approve-independent-identity"]) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["c5_eligible"] is True
