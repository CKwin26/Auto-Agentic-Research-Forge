from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from research_forge.rocrate_validation import validate_rocrate_external
from research_forge.storage import read_json, sha256_file


def _crate(root: Path) -> Path:
    root.mkdir()
    (root / "artifact.json").write_text("{}\n", encoding="utf-8")
    metadata = {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {
                "@id": "ro-crate-metadata.json",
                "@type": "CreativeWork",
                "about": {"@id": "./"},
                "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            },
            {
                "@id": "./",
                "@type": "Dataset",
                "name": "fixture",
                "description": "validator fixture",
                "license": "NOASSERTION",
                "datePublished": "2026-07-31",
                "hasPart": [{"@id": "artifact.json"}],
            },
            {"@id": "artifact.json", "@type": "File", "name": "artifact.json"},
        ],
    }
    (root / "ro-crate-metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return root


def _fake_validator(path: Path, *, passed: bool) -> list[str]:
    script = path / "fake_validator.py"
    script.write_text(
        """
import json, pathlib, sys
if '--version' in sys.argv:
    print('roc-validator test-double 1.0')
    raise SystemExit(0)
out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
passed = %s
out.write_text(json.dumps({'passed': passed, 'issues': [] if passed else [{'severity': 'REQUIRED', 'message': 'bad crate'}]}), encoding='utf-8')
raise SystemExit(0 if passed else 1)
""" % ("True" if passed else "False"),
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_external_validator_report_is_frozen_outside_crate(tmp_path: Path) -> None:
    crate = _crate(tmp_path / "crate")
    evidence_path = tmp_path / "evidence" / "validation.json"
    result = validate_rocrate_external(
        crate,
        evidence_output=evidence_path,
        validator_command=_fake_validator(tmp_path, passed=True),
    )
    assert result["passed"] is True
    assert result["offline"] is True
    assert result["required_issue_count"] == 0
    assert result["crate_metadata_sha256"] == sha256_file(
        crate / "ro-crate-metadata.json"
    )
    assert read_json(evidence_path)["checker"] == "crs4/rocrate-validator"


def test_external_validator_failure_is_not_upgraded(tmp_path: Path) -> None:
    crate = _crate(tmp_path / "crate")
    result = validate_rocrate_external(
        crate,
        evidence_output=tmp_path / "failed.json",
        validator_command=_fake_validator(tmp_path, passed=False),
    )
    assert result["passed"] is False
    assert result["required_issue_count"] == 1


def test_validator_evidence_cannot_modify_the_crate(tmp_path: Path) -> None:
    crate = _crate(tmp_path / "crate")
    with pytest.raises(ValueError, match="outside the crate"):
        validate_rocrate_external(
            crate,
            evidence_output=crate / "validation.json",
            validator_command=_fake_validator(tmp_path, passed=True),
        )


def test_workflow_run_fixture_passes_maintained_external_validator(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    crate = root / "examples" / "rocrate-validation" / "stage3-minimal"
    try:
        result = validate_rocrate_external(
            crate,
            evidence_output=tmp_path / "workflow-run-validation.json",
            profile="workflow-run-crate-0.5",
        )
    except FileNotFoundError as exc:
        pytest.skip(str(exc))

    assert result["passed"] is True
    assert result["profile"] == "workflow-run-crate-0.5"
    assert result["required_issue_count"] == 0
    statistics = result["validator_report"]["statistics"]
    assert statistics["total_requirements"] == 24
    assert statistics["total_failed_requirements"] == 0
    assert statistics["total_checks"] == 55
    assert statistics["total_failed_checks"] == 0
