from pathlib import Path

from scripts.compress_rirp_manuscript import main_text, split_document, word_count
from scripts.finalize_rirp_submission import (
    apply_submission_metadata,
    author_tex,
    enforce_cite_time_gate,
    enforce_submission_scope,
    final_cover_letter_markdown,
    final_title_page_markdown,
    markdown_to_tex,
    normalize_submission_tail,
    validate_submission_metadata,
)


def complete_submission_metadata() -> dict:
    return {
        "schema_version": 1,
        "authors": [
            {"name": "Ada Example", "affiliation_ids": ["1"], "orcid": "0000-0002-1825-0097"}
        ],
        "affiliations": [
            {"id": "1", "institution": "Example Institute", "city": "Shanghai", "country": "China"}
        ],
        "corresponding_author": {"name": "Ada Example", "email": "ada@example.org"},
        "declarations": {
            "competing_interests": "The author declares no competing interests.",
            "funding": "No external funding.",
            "authors_contributions": "AE completed all CRediT roles reported for this study.",
            "acknowledgements": "Not applicable.",
        },
        "osf_view_only_url": "https://osf.io/example/?view_only=token",
        "confirmations": {
            "all_authors_qualify": True,
            "all_authors_approved": True,
            "not_under_consideration_elsewhere": True,
            "competing_interests_complete": True,
            "funding_complete": True,
            "osf_link_tested_signed_out": True,
            "final_human_read_complete": True,
            "approved_for_submission": True,
        },
    }


def test_structured_abstract_is_not_rendered_as_numbered_body_sections() -> None:
    markdown = """# Test manuscript

## Abstract

**Background:** Two complete sentences explain the problem. The second establishes the research question.

**Methods:** The registered design and audit are described here.

**Results:** The bounded result is reported here.

**Conclusions:** The interpretation remains appropriately limited.

**Keywords:** provenance; auditing.

## Background

The article starts here.
"""

    tex = markdown_to_tex(markdown, Path("figures"))

    assert r"\begin{abstract}" in tex
    assert r"\end{abstract}" in tex
    assert r"\section{Abstract}" not in tex
    assert r"\subsection{Background}" not in tex
    assert r"\textbf{Background:}" in tex
    assert r"\textbf{Methods:}" in tex
    assert r"\section{Background}" in tex
    assert r"\setcounter{secnumdepth}{2}" in tex
    assert "Blinded manuscript" not in tex
    assert "Content-review copy" in tex


def test_body_hierarchy_is_numbered_but_submission_tail_is_not() -> None:
    markdown = """# Test manuscript

## Abstract

Abstract text.

## Background

Background text.

### Related Work and Registered Sources

Prior work.

## Methods

### Study design

Method detail.

## Declarations

### Funding

No external funding.

## References

1. Reference.
"""

    tex = markdown_to_tex(markdown, Path("figures"))

    assert r"\section{Background}" in tex
    assert r"\subsection{Related Work and Registered Sources}" in tex
    assert r"\section{Methods}" in tex
    assert r"\subsection{Study design}" in tex
    assert r"\section*{Declarations}" in tex
    assert r"\subsection*{Funding}" in tex
    assert r"\section*{References}" in tex


def test_submission_scope_gate_keeps_ablation_primary_and_diagnosis_secondary() -> None:
    markdown = """# Pre-Delivery Claim–Evidence Gating in Autonomous Research Agents: A Same-Backbone Paired Ablation Study

## Abstract

The same-backbone experiment is reported as non-confirmatory after human audit.

## Background

The primary research question is: **When the same frozen upstream artifact is processed by two otherwise matched branches, does adding a pre-delivery claim–evidence gate change the protected unsupported-claim rate without reducing task-native performance?** A secondary diagnosis follows.
"""

    enforce_submission_scope(markdown)


def test_submission_scope_gate_refuses_fault_localization_as_the_title() -> None:
    markdown = """# Fault Ownership and Rollback in Autonomous Research Agents

## Abstract

The same-backbone experiment is reported as non-confirmatory after human audit.

## Background

When the same frozen upstream artifact is processed by two otherwise matched branches, does adding a pre-delivery claim-evidence gate change the protected unsupported-claim rate without reducing task-native performance?
"""

    import pytest

    with pytest.raises(ValueError, match="title must remain"):
        enforce_submission_scope(markdown)


def test_final_submission_metadata_populates_title_page_and_declarations() -> None:
    metadata = complete_submission_metadata()
    validate_submission_metadata(metadata)
    markdown = """## Declarations

### Competing interests

Placeholder.

### Funding

Placeholder.

### Authors' contributions

Placeholder.

### Acknowledgements

Placeholder.

## References

1. Reference with [ANONYMIZED_OSF_VIEW_ONLY_URL].
"""

    populated = apply_submission_metadata(markdown, metadata)
    rendered_author = author_tex(metadata)

    assert "Placeholder." not in populated
    assert metadata["osf_view_only_url"] in populated
    assert "Ada Example" in rendered_author
    assert "ada@example.org" in rendered_author
    assert "Ada Example" in final_title_page_markdown(metadata)
    assert "not under consideration elsewhere" in final_cover_letter_markdown(metadata)


def test_final_submission_metadata_gate_refuses_unconfirmed_template() -> None:
    import pytest

    metadata = complete_submission_metadata()
    metadata["authors"][0]["name"] = ""
    metadata["osf_view_only_url"] = ""
    metadata["confirmations"]["approved_for_submission"] = False

    with pytest.raises(ValueError) as error:
        validate_submission_metadata(metadata)

    message = str(error.value)
    assert "authors[0].name is required" in message
    assert "osf_view_only_url" in message
    assert "approved_for_submission" in message


def test_submission_tail_places_declarations_before_references() -> None:
    markdown = """# Test manuscript

## Conclusions

Conclusion.

## References

1. Reference.

## Reproducibility

The evidence package is hash-bound.

## Declarations

### Availability of data and materials

Data statement.

### Competing interests

None.
"""

    normalized = normalize_submission_tail(markdown)

    assert "## Reproducibility" not in normalized
    assert normalized.index("## Declarations") < normalized.index("## References")
    assert "Data statement.\n\nThe evidence package is hash-bound." in normalized


def test_compactor_preserves_abbreviations_with_submission_tail() -> None:
    markdown = """# Test manuscript

## Abstract

Abstract body.

## Background

Background body.

## Methods

Methods body.

## Results

Results body.

## Discussion

Discussion body.

## Limitations

Limitations body.

## Conclusions

Conclusion body.

## List of abbreviations

NLI: natural language inference.

## Declarations

Declaration body.

## References

1. Reference.
"""

    _, sections, suffix = split_document(markdown)

    assert sections["Conclusions"].strip().endswith("Conclusion body.")
    assert suffix.startswith("## List of abbreviations")
    assert "## Declarations" in suffix


def test_main_text_word_count_excludes_declarations_and_references() -> None:
    markdown = """## Abstract

one two

## Conclusions

three four

## List of abbreviations

NLI: natural language inference.

## Declarations

these words are excluded

## References

these words are also excluded
"""

    counted = main_text(markdown)
    assert "these words are excluded" not in counted
    assert "these words are also excluded" not in counted
    assert word_count(counted) == 13


def test_cite_time_gate_normalizes_hash_bound_legacy_markers() -> None:
    source = "Claim [1]<!--ref:source--><!--anchor:section:Results-->."
    audit = {
        "verdict": "PASS",
        "manuscript_sha256": "abc",
        "verification": {
            "references_verified": 1,
            "references_total": 1,
            "orphan_references": 0,
            "dangling_citations": 0,
        },
    }

    normalized = enforce_cite_time_gate(source, source_sha256="abc", integrity_audit=audit)

    assert "<!--ref:source ok-->" in normalized


def test_cite_time_gate_refuses_anchorless_or_blocked_markers() -> None:
    audit = {
        "verdict": "PASS",
        "manuscript_sha256": "abc",
        "verification": {
            "references_verified": 1,
            "references_total": 1,
            "orphan_references": 0,
            "dangling_citations": 0,
        },
    }

    import pytest

    with pytest.raises(ValueError):
        enforce_cite_time_gate("Claim<!--ref:source-->.", source_sha256="abc", integrity_audit=audit)
    with pytest.raises(ValueError):
        enforce_cite_time_gate(
            "Claim<!--ref:source ok severity=HIGH-BLOCK--><!--anchor:section:Results-->.",
            source_sha256="abc",
            integrity_audit=audit,
        )
