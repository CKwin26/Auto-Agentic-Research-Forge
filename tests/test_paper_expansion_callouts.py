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
    academicize_paper_draft,
    restore_required_artifact_callouts,
)
from research_forge.paper_pipeline import GENERIC_JOURNAL_ARTICLE


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
