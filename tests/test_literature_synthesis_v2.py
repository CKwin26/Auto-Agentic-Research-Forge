from __future__ import annotations

import pytest

from research_forge.literature_synthesis_v2 import (
    EvidenceSpan,
    LITERATURE_TOOL_ADAPTERS,
    LiteratureComparisonMatrix,
    LiteratureComparisonRow,
    LiteratureSynthesisBundle,
    PaperContributionCard,
    RelatedWorkOutline,
    RelatedWorkSectionPlan,
)


def _span() -> EvidenceSpan:
    return EvidenceSpan(
        evidence_span_id="lit-span-paper-01-method",
        paper_id="paper-01",
        snapshot_artifact_id="artifact-paper-01",
        snapshot_sha256="a" * 64,
        page_start=2,
        page_end=2,
        section="Methods",
        quote_hash="b" * 64,
        text="The study used a randomized comparison with a prespecified outcome.",
        evidence_role="method",
    )


def test_metadata_only_card_cannot_invent_methods_or_results() -> None:
    with pytest.raises(ValueError, match="metadata-only"):
        PaperContributionCard(
            paper_id="paper-01",
            title="A title is not full-text evidence",
            authors=["Author"],
            year=2025,
            full_text_status="metadata_only",
            intervention_or_method="randomized comparison",
            evidence_span_ids=["lit-span-paper-01-method"],
            extraction_confidence=0.9,
        )


def test_substantive_card_requires_evidence_span() -> None:
    with pytest.raises(ValueError, match="EvidenceSpan"):
        PaperContributionCard(
            paper_id="paper-01",
            title="Study",
            authors=["Author"],
            year=2025,
            full_text_status="abstract_only",
            major_results=["The intervention improved the outcome."],
            extraction_confidence=0.9,
        )


def test_metadata_context_cannot_pass_submission_ready_gate() -> None:
    with pytest.raises(ValueError, match="metadata-only context"):
        LiteratureSynthesisBundle(
            study_id="study-01",
            mode="metadata_context",
            contribution_cards=[
                PaperContributionCard(
                    paper_id="paper-01",
                    title="Study",
                    authors=["Author"],
                    year=2025,
                    full_text_status="metadata_only",
                    extraction_confidence=0.8,
                )
            ],
            evidence_spans=[],
            submission_ready_related_work=True,
        )


def test_narrative_related_work_is_organized_by_method_family_and_evidence() -> None:
    span = _span()
    card = PaperContributionCard(
        paper_id="paper-01",
        title="Study",
        authors=["Author"],
        year=2025,
        full_text_status="full_text_frozen",
        intervention_or_method="randomized comparison",
        evidence_span_ids=[span.evidence_span_id],
        extraction_confidence=0.9,
    )
    matrix = LiteratureComparisonMatrix(
        study_id="study-01",
        mode="narrative_related_work",
        rows=[
            LiteratureComparisonRow(
                family_id="family-randomized",
                family_label="Randomized evaluations",
                research_problem="Estimate a condition contrast.",
                common_methods=["random assignment"],
                data_and_evaluation=["task outcomes"],
                known_results=["Results depend on the registered outcome."],
                known_limitations=["Single-domain studies may not generalize."],
                current_study_difference="The current study freezes evidence bindings.",
                paper_ids=[card.paper_id],
                evidence_span_ids=[span.evidence_span_id],
            )
        ],
    )
    outline = RelatedWorkOutline(
        study_id="study-01",
        mode="narrative_related_work",
        sections=[
            RelatedWorkSectionPlan(
                section_id="related-randomized",
                method_family_or_problem="Randomized evaluations",
                comparison_row_ids=["family-randomized"],
                evidence_span_ids=[span.evidence_span_id],
            )
        ],
    )
    bundle = LiteratureSynthesisBundle(
        study_id="study-01",
        mode="narrative_related_work",
        contribution_cards=[card],
        evidence_spans=[span],
        comparison_matrix=matrix,
        related_work_outline=outline,
        submission_ready_related_work=True,
    )
    assert bundle.submission_ready_related_work is True


def test_external_tools_are_typed_adapters_not_embedded_agents() -> None:
    assert {
        "openalex_pyalex",
        "grobid",
        "paperqa2",
        "asreview",
        "bibliometrix",
        "manubot_resolver",
    } <= set(LITERATURE_TOOL_ADAPTERS)
    assert all(not item.product_code_embedded for item in LITERATURE_TOOL_ADAPTERS.values())
    assert all(not item.instruction_authority for item in LITERATURE_TOOL_ADAPTERS.values())
