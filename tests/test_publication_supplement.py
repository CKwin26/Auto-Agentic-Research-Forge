from __future__ import annotations

from pathlib import Path

from research_forge.publication_supplement import (
    _package_bytes,
    audit_anonymous_supplement,
    prepare_anonymous_supplement,
)
from research_forge.storage import write_json_atomic


def _write(path: Path, value: str = "{}\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _publication_project(tmp_path: Path) -> Path:
    project = tmp_path / "publication-project"
    write_json_atomic(
        project / "stage2" / "protocol.json",
        {"protocol_id": "stage2-test", "study_intent": "publication"},
    )
    for relative in (
        "stage2/frozen_manifest.json",
        "stage2/publication_pair_audit.json",
        "stage2/protected_nli_evaluation/manifest.json",
        "stage2/protected_nli_evaluation/summary.json",
        "stage2/protected_nli_evaluation/unblinding.json",
        "design_revisions/independent_calibration_contract.json",
        "synthesis/publication_analysis.json",
        "synthesis/publication_claims.json",
        "synthesis/publication_synthesis_audit.json",
        "synthesis/publication_manuscript_depth.json",
    ):
        _write(project / relative)
    _write(project / "synthesis" / "publication_manuscript.md", "# Anonymous manuscript\n")
    return project


def test_local_anonymous_supplement_is_hash_bound_but_not_claimed_public(tmp_path: Path) -> None:
    project = _publication_project(tmp_path)

    result = prepare_anonymous_supplement(project)
    audit = audit_anonymous_supplement(project)

    assert result["status"] == "prepared_locally_not_public"
    assert audit["passed"] is True
    assert audit["external_release"] == "not_public"
    package = project / result["package_relative_path"]
    assert (package / "MANIFEST.json").is_file()
    assert "not publicly accessible" in (package / "README.md").read_text(encoding="utf-8")


def test_local_anonymous_supplement_rejects_stale_source_binding(tmp_path: Path) -> None:
    project = _publication_project(tmp_path)
    prepare_anonymous_supplement(project)
    _write(project / "synthesis" / "publication_manuscript.md", "# Edited after package\n")

    audit = audit_anonymous_supplement(project)

    assert audit["passed"] is False
    assert audit["checks"]["protocol_and_manuscript_current"] is False


def test_pair_audit_timestamp_does_not_change_supplement_content_address(tmp_path: Path) -> None:
    audit_path = tmp_path / "publication_pair_audit.json"
    _write(audit_path, '{"audited_at":"2026-07-19T01:00:00Z","passed":true}\n')
    first = _package_bytes(audit_path)
    _write(audit_path, '{"audited_at":"2026-07-19T02:00:00Z","passed":true}\n')

    assert _package_bytes(audit_path) == first
