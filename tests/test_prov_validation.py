from __future__ import annotations

from pathlib import Path

import pytest

from research_forge.prov_validation import validate_prov_external
from research_forge.stage_three_trust import _write_interchange_exports
from research_forge.storage import read_json


def _completion() -> dict[str, object]:
    return {
        "study_id": "study-prov",
        "plan_id": "plan-prov",
        "confirmatory_status": "descriptive_only",
        "artifact_hashes": {
            "stage3/results/a.json": "a" * 64,
            "stage3/evaluations/b.json": "b" * 64,
        },
    }


def test_external_shacl_engine_accepts_stage3_prov_projection(tmp_path: Path) -> None:
    crate = tmp_path / "crate"
    crate.mkdir()
    _write_interchange_exports(crate, _completion())
    report_path = tmp_path / "external-evidence" / "prov-report.json"

    report = validate_prov_external(
        prov_path=crate / "prov.jsonld",
        report_output=report_path,
    )

    assert report["conforms"] is True
    assert report["validator_version"] == "0.40.1"
    assert report["entity_count"] == 2
    assert report["activity_count"] == 1
    assert report["shacl_findings"] == []
    assert read_json(report_path)["source_sha256"] == report["source_sha256"]
    assert "not official W3C" in report["claim_boundary"]


def test_missing_generated_entity_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "prov.jsonld"
    source.write_text(
        '{"@context":"https://www.w3.org/ns/prov.jsonld",'
        '"entity":{"rf:a":{"prov:type":"rf:FrozenArtifact",'
        '"rf:sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}},'
        '"activity":{"rf:p":{"prov:type":"rf:Stage3Execution",'
        '"prov:generated":["rf:missing"]}}}',
        encoding="utf-8",
    )

    report = validate_prov_external(
        prov_path=source,
        report_output=tmp_path / "evidence" / "report.json",
    )

    assert report["conforms"] is False
    assert any(
        item.startswith("PROV_GENERATED_ENTITY_MISSING")
        for item in report["deterministic_violations"]
    )
    assert report["shacl_findings"]


def test_external_report_cannot_mutate_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "prov.jsonld"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="outside"):
        validate_prov_external(
            prov_path=source,
            report_output=source / "report.json",
        )
