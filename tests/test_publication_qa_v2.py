from __future__ import annotations

from research_forge.publication_qa_v2 import (
    NumericalSurfaceValue,
    audit_citation_resolution_v2,
    audit_compiled_pdf_v2,
    audit_figure_table_v2,
    audit_language_style_v2,
    audit_manuscript_source_v2,
    audit_numerical_consistency_v2,
    audit_statistical_semantics_v2,
    build_publication_acceptance_report_v2,
    citation_registry_keys_from_sources,
)
from research_forge.models import LiteratureSource, LiteratureSourceType


def _write_manuscript(tmp_path, text: str):
    path = tmp_path / "manuscript.tex"
    path.write_text(text, encoding="utf-8")
    return path


def test_known_acceptance_corpus_placeholders_are_hard_blocked(tmp_path) -> None:
    path = _write_manuscript(
        tmp_path,
        r"""
\section{Related Work}
This is an unresolved callout that cites [3, ?] and [?, ?].
\section{Results}
The registered registered decision was retained.
\section{Limitations}
An not fully specified panel remains a limitation.
""",
    )
    source = audit_manuscript_source_v2(path)
    language = audit_language_style_v2(path.read_text(encoding="utf-8"))
    codes = {item.code for item in source.findings + language.findings}
    assert "UNRESOLVED" in codes
    assert "QUESTION_MARK_CITATION" in codes
    assert "ADJACENT_DUPLICATE_WORD" in codes
    assert "INVALID_ARTICLE_CONSTRUCTION" in codes
    assert source.passed is False
    assert language.passed is False


def test_reader_facing_internal_tokens_and_local_paths_are_blocked(tmp_path) -> None:
    path = _write_manuscript(
        tmp_path,
        r"""
\section{Methods}
The outcome dual_quality_top5 was loaded from C:\\Users\\austa\\data.json.
\section{Related Work}
This section contains enough substantive prose to avoid an empty-section finding, but the internal implementation token and local machine path must still fail the publication source audit deterministically.
""",
    )
    report = audit_manuscript_source_v2(path)
    codes = {item.code for item in report.findings}
    assert "READER_FACING_SNAKE_CASE" in codes
    assert "LOCAL_ABSOLUTE_PATH" in codes


def test_unresolved_citation_key_is_critical() -> None:
    report = audit_citation_resolution_v2(
        r"Prior work supports the design \cite{known,missing}.",
        registry_keys={"known"},
    )
    assert report.passed is False
    assert report.unresolved_keys == ["missing"]


def test_metadata_only_related_work_blocks_publication_ready() -> None:
    report = audit_citation_resolution_v2(
        r"Prior work is cited as \cite{known}.",
        registry_keys={"known"},
        related_work_submission_ready=False,
    )
    assert report.passed is False
    assert "RELATED_WORK_NOT_SUBMISSION_READY" in {
        item.code for item in report.findings
    }


def test_citation_registry_accepts_pydantic_dict_and_string_sources() -> None:
    source = LiteratureSource(
        source_id="lit-source-01",
        source_type=LiteratureSourceType.PAPER,
        title="A verified source",
        authors=["A. Author"],
        year=2026,
        locator="doi:10.0000/example",
        verified=True,
        verification_method="test",
        origin="discovery",
        origin_id="lit-source-01",
    )
    keys = citation_registry_keys_from_sources(
        [
            source,
            {"citation_key": "manual-key", "resource_id": "resource-key"},
            "string-key",
        ]
    )
    assert {"lit-source-01", "manual-key", "resource-key", "string-key"} <= keys
    report = audit_citation_resolution_v2(
        r"Prior work is cited as [lit-source-01] and \cite{manual-key}.",
        registry_keys=keys,
    )
    assert report.passed is True


def test_numerical_surfaces_must_share_value_unit_direction_and_precision() -> None:
    surfaces = [
        NumericalSurfaceValue(
            surface_id="primary-effect",
            artifact_id="evaluation-1",
            artifact_sha256="a" * 64,
            field="effect",
            value=0.12,
            unit="points",
            direction="treatment-minus-control",
            display_precision=2,
        ),
        NumericalSurfaceValue(
            surface_id="primary-effect",
            artifact_id="evaluation-1",
            artifact_sha256="a" * 64,
            field="effect",
            value=0.21,
            unit="points",
            direction="treatment-minus-control",
            display_precision=2,
        ),
    ]
    report = audit_numerical_consistency_v2(surfaces)
    assert report.passed is False
    assert report.findings[0].code == "SURFACE_VALUE_CONFLICT"


def test_any_critical_error_forces_publication_ready_false(tmp_path) -> None:
    path = _write_manuscript(
        tmp_path,
        r"""
\section{Related Work}
This substantive section is long enough for the source structure check, but it cites \cite{missing} and therefore cannot pass a formal publication gate until that key resolves through the frozen registry.
\section{Results}
The registered comparison was reported without changing the scientific verdict.
""",
    )
    text = path.read_text(encoding="utf-8")
    source = audit_manuscript_source_v2(path)
    citation = audit_citation_resolution_v2(text, registry_keys=set())
    numerical = audit_numerical_consistency_v2([])
    semantics = audit_statistical_semantics_v2([])
    figures = audit_figure_table_v2(
        planned_figure_ids=[], figure_reports=[], manuscript_text=text
    )
    language = audit_language_style_v2(text)
    pdf = audit_compiled_pdf_v2(None)
    report = build_publication_acceptance_report_v2(
        manuscript_id="manuscript-test",
        scientific_verdict="supported",
        manuscript_source=source,
        citation_resolution=citation,
        numerical_consistency=numerical,
        statistical_semantics=semantics,
        figure_table=figures,
        language_style=language,
        compiled_pdf=pdf,
    )
    assert report.publication_ready is False
    assert report.scientific_verdict == "supported"
    assert report.scientific_verdict_unchanged is True
    assert report.critical_findings
