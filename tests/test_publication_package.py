from __future__ import annotations

from pathlib import Path

from research_forge import publication_package
from research_forge.storage import write_json_atomic


def _write(path: Path, text: str = "{}\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_review_package_is_local_only_and_detects_current_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    for relative in (
        "synthesis/final_pdf/publication_manuscript.pdf",
        "synthesis/publication_manuscript.tex",
        "synthesis/publication_manuscript.md",
        "synthesis/final_pdf/publication_manuscript.finalization.json",
        "synthesis/publication_claims.json",
        "synthesis/publication_analysis.json",
        "synthesis/publication_readiness.json",
        "synthesis/publication_synthesis_audit.json",
        "synthesis/publication_manuscript_depth.json",
    ):
        _write(project / relative, "pdf" if relative.endswith(".pdf") else "{}\n")
    write_json_atomic(
        project / "synthesis/final_pdf/publication_manuscript.finalization.json",
        {"finalization_authorized": True},
    )
    write_json_atomic(project / "synthesis/publication_readiness.json", {"readiness_score": 0.8})
    supplement = project / "supplement_packages/anon"
    _write(supplement / "MANIFEST.json")

    fake_supplement = {
        "package_id": "anon",
        "package_relative_path": "supplement_packages/anon",
        "audit": {"passed": True},
    }
    monkeypatch.setattr(publication_package, "publication_layout_gate", lambda *_args, **_kwargs: {"layout_allowed": True})
    monkeypatch.setattr(publication_package, "prepare_anonymous_supplement", lambda _project: fake_supplement)
    monkeypatch.setattr(publication_package, "audit_anonymous_supplement", lambda _project: {"passed": True})

    result = publication_package.prepare_review_submission_package(
        project, project / "synthesis/publication_readiness.json"
    )
    audit = publication_package.audit_review_submission_package(project)

    assert result["status"] == "prepared_locally_not_externally_submitted"
    assert audit["passed"] is True
    manifest = (project / result["package_relative_path"] / "MANIFEST.json").read_text(encoding="utf-8")
    assert "local_review_submission_package_not_externally_submitted" in manifest
    assert '"performed": false' in manifest
