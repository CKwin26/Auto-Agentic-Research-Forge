from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from research_forge.stage_three_trust import (
    _write_interchange_exports,
    assess_clean_room_reproduction,
    sign_subject,
    verify_attestation,
)
from research_forge.storage import read_json, sha256_file


def _private_key_bytes() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def test_signed_attestation_detects_tampering(tmp_path: Path) -> None:
    subject = tmp_path / "subject.bin"
    subject.write_bytes(b"frozen")
    attestation = sign_subject(
        subject_type="RunPlan",
        subject_path="stage3/run_plans/run-plan.json",
        subject_digest=sha256_file(subject),
        private_key_pem=_private_key_bytes(),
        identity="control-plane",
        source_commit="abc123",
    )

    assert verify_attestation(attestation) is True
    assert verify_attestation(
        attestation.model_copy(update={"subject_digest": "0" * 64})
    ) is False


def test_legacy_worker_self_attestation_cannot_award_clean_room_grade(
    tmp_path: Path,
) -> None:
    package = tmp_path / "completion.zip"
    package.write_bytes(b"portable package")
    parameters = {
        "fresh_worker": True,
        "original_development_directory_mounted": False,
        "original_cache_mounted": False,
        "sealed_assets_only": True,
        "signatures_and_hashes_verified": True,
        "rerun_scope": "approved subset",
        "effect_matches": True,
        "interval_matches": True,
        "verdict_matches": True,
    }
    attestation = sign_subject(
        subject_type="CleanRoomReproduction",
        subject_path=package.name,
        subject_digest=sha256_file(package),
        private_key_pem=_private_key_bytes(),
        identity="fresh-worker-01",
        source_commit="worker-image-digest",
        external_parameters=parameters,
    )
    receipt = assess_clean_room_reproduction(
        package_path=package, worker_attestation=attestation
    )
    failed = assess_clean_room_reproduction(
        package_path=package,
        worker_attestation=attestation.model_copy(
            update={
                "external_parameters": {
                    **parameters,
                    "original_cache_mounted": True,
                }
            }
        ),
    )

    assert receipt.status == "legacy_worker_self_attestation_rejected"
    assert receipt.evidence_level == "RF-E1_package_verified"
    assert failed.status == "failed"
    assert failed.evidence_level == "RF-E1_package_verified"


def test_completion_interchange_exports_have_bounded_prov_and_ro_crate_shapes(
    tmp_path: Path,
) -> None:
    completion = {
        "study_id": "study-001",
        "plan_id": "plan-001",
        "confirmatory_status": "descriptive_only",
        "artifact_hashes": {
            "stage3/evaluations/result.json": "a" * 64,
            "stage3/completion/completion.json": "b" * 64,
        },
    }

    exported = _write_interchange_exports(tmp_path, completion)
    prov = read_json(tmp_path / "prov.jsonld")
    crate = read_json(tmp_path / "ro-crate-metadata.json")

    assert exported == [
        "prov.jsonld",
        "workflow.json",
        "ro-crate-metadata.json",
        "report.html",
    ]
    assert prov["@context"] == "https://www.w3.org/ns/prov.jsonld"
    assert set(prov["entity"]) == {
        "rf:stage3/evaluations/result.json",
        "rf:stage3/completion/completion.json",
    }
    assert prov["activity"]["rf:plan-001"]["prov:generated"] == [
        "rf:stage3/evaluations/result.json",
        "rf:stage3/completion/completion.json",
    ]
    assert crate["@context"] == "https://w3id.org/ro/crate/1.1/context"
    assert crate["@graph"][0]["@id"] == "ro-crate-metadata.json"
    assert crate["@graph"][1]["hasPart"] == [
        {"@id": "stage3/completion/completion.json"},
        {"@id": "stage3/evaluations/result.json"},
        {"@id": "workflow.json"},
    ]
    assert crate["@graph"][0]["conformsTo"] == {
        "@id": "https://w3id.org/ro/crate/1.1"
    }
    assert crate["@graph"][1]["description"]
    assert crate["@graph"][1]["license"] == "NOASSERTION"
    assert crate["@graph"][1]["datePublished"]
    assert [item["@id"] for item in crate["@graph"][2:4]] == [
        "stage3/completion/completion.json",
        "stage3/evaluations/result.json",
    ]
    assert crate["@graph"][1]["mainEntity"] == {"@id": "workflow.json"}
    assert crate["@graph"][2]["identifier"] == {
        "@id": "#sha256-" + "b" * 64,
    }
    assert crate["@graph"][4]["@type"] == "PropertyValue"
    assert crate["@graph"][4]["value"] == "a" * 64
    assert crate["@graph"][6]["@type"] == [
        "File",
        "SoftwareSourceCode",
        "ComputationalWorkflow",
    ]
    workflow = read_json(tmp_path / "workflow.json")
    assert workflow["plan_id"] == "plan-001"
    assert workflow["authority_note"]
