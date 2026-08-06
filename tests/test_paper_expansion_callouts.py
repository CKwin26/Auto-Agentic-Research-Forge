from research_forge.paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
    HierarchicalPaperOutline,
    OutlineNode,
)
from research_forge.paper_expansion import (
    PaperDraftSections,
    _draft_contract_violations,
    _polish_trace,
    academicize_paper_draft,
    render_full_manuscript,
    restore_required_artifact_callouts,
    _references,
)
from research_forge.paper_pipeline import GENERIC_JOURNAL_ARTICLE
from research_forge.models import LiteratureSource, LiteratureSourceType


def _draft(**updates: str) -> PaperDraftSections:
    values = {
        "title": "A sufficiently descriptive research paper title",
        "abstract": "A" * 120,
        "introduction": "I" * 320,
        "related_work": "R" * 320,
        "methods": "M" * 520,
        "results": "S" * 420,
        "discussion": "D" * 520,
        "limitations": "L" * 220,
        "conclusion": "C" * 170,
        "data_availability": "Data are available in the frozen bundle.",
        "ethics_statement": "No human participants were involved.",
        "author_contributions": "The authors designed and audited the study.",
        "conflict_of_interest": "None declared.",
        "funding": "No external funding.",
        "ai_disclosure": "AI tools assisted drafting under human oversight.",
    }
    values.update(updates)
    return PaperDraftSections(**values)


def test_full_manuscript_renderer_uses_requested_english_labels() -> None:
    manuscript = render_full_manuscript(
        _draft(),
        verdict={"numeric_evidence": [{"path": "$.effect", "value": 0.25}]},
        sources=[],
        language="en",
    )

    assert "## Abstract" in manuscript
    assert "## Related Work" in manuscript
    assert "### Primary outcome summary" in manuscript
    assert "| Measure | Value |" in manuscript
    assert "Evidence-bound complete-manuscript candidate" not in manuscript
    assert "writing agent" not in manuscript
    assert "证据约束" not in manuscript


def test_full_manuscript_lists_only_sources_cited_in_prose() -> None:
    cited = LiteratureSource(
        source_id="source-cited",
        source_type=LiteratureSourceType.PAPER,
        title="Cited paper",
        authors=["A. Author"],
        year=2025,
        locator="https://example.org/cited",
        verified=True,
        verification_method="fixture",
    )
    unused = cited.model_copy(
        update={
            "source_id": "source-unused",
            "title": "Unused paper",
            "locator": "https://example.org/unused",
        }
    )
    manuscript = render_full_manuscript(
        _draft(related_work="R" * 320 + " [source-cited]"),
        verdict={"numeric_evidence": []},
        sources=[cited, unused],
        language="en",
    )

    assert "Cited paper" in manuscript
    assert "Unused paper" not in manuscript


def test_references_do_not_present_missing_author_placeholder_as_an_author() -> None:
    source = LiteratureSource(
        source_id="source-missing-author",
        source_type=LiteratureSourceType.PAPER,
        title="Verified paper with incomplete metadata",
        authors=["Author metadata unavailable"],
        year=2026,
        locator="10.1234/example",
        verified=True,
        verification_method="fixture",
    )

    rendered = _references([source])

    assert "Author metadata unavailable" not in rendered
    assert "(2026). *Verified paper with incomplete metadata*." in rendered


def test_narrative_repair_may_move_numbers_out_of_conclusion() -> None:
    source = _draft(
        results="S" * 420,
        conclusion=(
            "The comparison used 40 pairs and produced estimates of 0.25 and 0.50. "
            + "C" * 170
        ),
    )
    polished = _draft(
        results=(
            "The comparison used 40 pairs and produced estimates of 0.25 and 0.50. "
            + "S" * 420
        ),
        conclusion=(
            "The candidate method improved the registered outcome in the studied setting. "
            + "C" * 170
        ),
    )

    trace = _polish_trace(
        source,
        polished,
        frozen_conclusion=source.conclusion,
    )

    assert trace.number_multiset_preserved
    assert trace.frozen_conclusion_preserved
    assert trace.accepted


def test_narrative_repair_may_remove_only_repeated_numeric_recaps() -> None:
    source = _draft(
        results="The registered effect was 0.50. " + "S" * 420,
        conclusion="The registered effect was 0.50. " + "C" * 170,
    )
    polished = _draft(
        results="The registered effect was 0.50. " + "S" * 420,
        conclusion="The registered comparison favored the candidate. " + "C" * 170,
    )

    trace = _polish_trace(
        source,
        polished,
        frozen_conclusion=source.conclusion,
        allow_numeric_deduplication=True,
    )

    assert trace.number_multiset_preserved
    assert trace.accepted
    assert trace.policy == "academic_narrative_numeric_deduplication"


def test_narrative_numeric_deduplication_cannot_add_a_new_value() -> None:
    source = _draft(results="The registered effect was 0.50. " + "S" * 420)
    polished = _draft(results="The registered effect was 0.75. " + "S" * 420)

    trace = _polish_trace(
        source,
        polished,
        frozen_conclusion=source.conclusion,
        allow_numeric_deduplication=True,
    )

    assert not trace.number_multiset_preserved
    assert not trace.accepted


def test_restore_required_artifact_callouts_to_original_sections() -> None:
    source = _draft(
        results="S" * 420 + "\n\n[FIGURE:fig-primary-result]",
        methods="M" * 520 + "\n\n[TABLE:tab-study-design]",
    )
    revised = _draft(results="Revised " + "S" * 420)

    restored = restore_required_artifact_callouts(source, revised)

    assert "[FIGURE:fig-primary-result]" in restored.results
    assert "[TABLE:tab-study-design]" in restored.methods


def test_draft_contract_rejects_code_identifier_in_title() -> None:
    binding = EvidenceClaimBinding(
        claim_id="claim-001",
        kind="result_metric",
        statement="The registered comparison is not evaluable.",
        evidence=[
            EvidencePointer(
                path="result.json",
                sha256="a" * 64,
                json_path="decision",
                evidence_type="project_artifact",
            )
        ],
        allowed_sections=["results", "conclusion"],
        claim_strength="descriptive",
        evidence_status="bound",
    )
    evidence = EvidenceClaimMap(
        track_id="track-1",
        frozen_conclusion="The registered comparison is not evaluable.",
        bindings=[binding],
        verified_source_ids=[],
        forbidden_moves=[],
        source_registry_sha256="b" * 64,
    )
    outline = HierarchicalPaperOutline(
        title="Evidence-bound comparative evaluation",
        thesis="The study reports the evidence boundary of a registered comparison.",
        abstract_moves=["background", "objective", "method", "result", "conclusion"],
        sections=[
            OutlineNode(
                node_id=f"section-{item.key.replace('_', '-')}",
                section_key=item.key,
                heading=item.english_title,
                level=2,
                purpose="Describe the registered study without changing its evidence.",
                argument="Report only the frozen comparison and its evidence boundary.",
            )
            for item in GENERIC_JOURNAL_ARTICLE.sections
        ],
    )
    draft = _draft(
        title="Evaluation of dual_quality_top5 in registered comparisons",
        conclusion=(
            "C" * 170
            + " The registered comparison is not evaluable."
        ),
    )

    violations = _draft_contract_violations(
        draft,
        outline=outline,
        evidence_claim_map=evidence,
    )

    assert any("title exposes internal audit tokens" in item for item in violations)


def test_academicize_draft_replaces_contract_roles_and_audit_ids() -> None:
    draft = _draft(
        title="dual_quality_top5 comparison",
        methods=(
            "M" * 520
            + " evaluator_policy uses frozen protocol and eligibility; "
            + "resource-selection-e64d48c6061fc702 was recorded."
        ),
    )

    rendered = academicize_paper_draft(
        draft,
        aliases={"dual_quality_top5": "候选排序方法"},
    )

    assert rendered.title == "候选排序方法 comparison"
    assert "evaluator_policy" not in rendered.methods
    assert "frozen protocol and eligibility" not in rendered.methods
    assert "resource-selection-e64d48c6061fc702" not in rendered.methods
    assert "冻结的内部记录" in rendered.methods


def test_academicize_english_draft_removes_internal_governance_phrasing() -> None:
    draft = _draft(
        methods="M" * 520 + " This was a controlled acceptance fixture.",
        discussion=(
            "D" * 520
            + " Under the frozen controlled acceptance design, the treatment improved. "
            + "The frozen scientific verdict was supported."
        ),
    )

    rendered = academicize_paper_draft(draft, language="en")

    assert "controlled acceptance fixture" not in rendered.methods
    assert "frozen scientific verdict" not in rendered.discussion.lower()
    assert "prespecified paired design" in rendered.discussion
    assert "prespecified support criterion was met" in rendered.discussion


def test_academicize_english_draft_uses_decision_language_not_verdict_jargon() -> None:
    draft = _draft(
        methods="M" * 520 + " The verdict criterion was prespecified.",
        conclusion=(
            "The prespecified scientific verdict was supported. " + "C" * 180
        ),
    )

    rendered = academicize_paper_draft(draft, language="en")

    assert "decision criterion" in rendered.methods
    assert "verdict" not in rendered.methods.lower()
    assert "prespecified support criterion was met" in rendered.conclusion
    assert "scientific verdict" not in rendered.conclusion.lower()


def test_academicize_compacts_repeated_legacy_ai_disclosure_model_names() -> None:
    draft = _draft(
        ai_disclosure=(
            "AI-assisted tools supported model-x (draft generation), model-x "
            "(scientific review). Their outputs were reviewed by the authors."
        )
    )

    rendered = academicize_paper_draft(draft, language="en")

    assert rendered.ai_disclosure.count("model-x") == 1
    assert "draft generation, scientific review using model-x" in rendered.ai_disclosure


def test_restore_does_not_duplicate_callout_moved_by_revision() -> None:
    source = _draft(results="S" * 420 + "\n\n[FIGURE:fig-primary-result]")
    revised = _draft(
        results="Revised " + "S" * 420,
        discussion="D" * 520 + "\n\n[FIGURE:fig-primary-result]",
    )

    restored = restore_required_artifact_callouts(source, revised)

    assert (
        sum(
            value.count("[FIGURE:fig-primary-result]")
            for value in restored.model_dump().values()
        )
        == 1
    )


def test_restore_removes_duplicate_required_callout() -> None:
    callout = "[FIGURE:fig-primary-result]"
    source = _draft(results="S" * 420 + "\n\n" + callout)
    revised = _draft(
        methods="M" * 520 + "\n\n" + callout,
        results="Revised " + "S" * 420 + "\n\n" + callout,
    )

    restored = restore_required_artifact_callouts(source, revised)

    assert sum(
        value.count(callout)
        for value in restored.model_dump().values()
    ) == 1


def test_restore_collapses_duplicate_callout_already_present_in_source() -> None:
    callout = "[FIGURE:fig-primary-result]"
    source = _draft(
        methods="M" * 520 + "\n\n" + callout,
        results="S" * 420 + "\n\n" + callout,
    )
    revised = _draft(
        methods="Revised " + "M" * 520 + "\n\n" + callout,
        results="Revised " + "S" * 420 + "\n\n" + callout,
    )

    restored = restore_required_artifact_callouts(source, revised)

    assert sum(
        value.count(callout)
        for value in restored.model_dump().values()
    ) == 1


def test_restore_removes_callout_invented_by_revision() -> None:
    source = _draft(results="S" * 420 + "\n\n[FIGURE:fig-primary-result]")
    revised = _draft(
        results=(
            "Revised "
            + "S" * 420
            + "\n\n[FIGURE:fig-primary-result]"
            + "\n\n[TABLE:tbl-invented]"
        )
    )

    restored = restore_required_artifact_callouts(source, revised)

    assert "[FIGURE:fig-primary-result]" in restored.results
    assert "[TABLE:tbl-invented]" not in restored.results
