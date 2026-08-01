from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v1_release_acceptance_does_not_require_c5() -> None:
    manifest = json.loads(
        (ROOT / "research_forge" / "release_acceptance.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["release_id"] == "research-forge-v1"
    assert manifest["release_status"] == "accepted"
    assert manifest["accepted_maturity_ceiling"] == "C4_real_case_validated"
    assert manifest["independent_external_validation"] == {
        "required_for_release": False,
        "maturity_level": "C5_independently_validated",
        "current_status": "not_claimed",
        "verifier_available": True,
    }
    assert "C5 independent validation" in manifest["forbidden_claims"]


def test_v1_release_evidence_is_complete_and_machine_readable() -> None:
    manifest = json.loads(
        (ROOT / "research_forge" / "release_acceptance.json").read_text(
            encoding="utf-8"
        )
    )

    evidence = manifest["required_evidence"]
    assert evidence["capability_manifest_audit"] == "18/18 supported"
    assert evidence["core_capability_benchmark"] == "12/12 passed"
    assert evidence["controlled_clean_room_replays"] == "2/2 passed"
    assert evidence["public_acceptance_packages"] == "2/2 passed"
    assert evidence["github_hosted_signed_replay"] == "passed"
    assert evidence["python_test_suite"] == "latest main CI passed"

    audit = ROOT / manifest["authoritative_audit"]
    assert audit.is_file()
