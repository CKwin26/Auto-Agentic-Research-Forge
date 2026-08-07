from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from research_forge.paper_author_voice import (
    AuthorVoiceProfile,
    SentenceLengthDistribution,
)
from research_forge.paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
    HierarchicalPaperOutline,
    OutlineNode,
    PaperArtifactManifest,
    PaperArtifactRecord,
)
from research_forge.paper_contribution import ContributionCandidate
from research_forge.paper_expansion import (
    PaperDraftSections,
    _restore_required_outline_structure,
    _sanitize_outline_references,
    normalize_reader_facing_governance,
    normalize_paper_draft,
)
from research_forge.paper_humanize import (
    audit_humanization_integrity,
    snapshot_semantics,
)
from research_forge.paper_narrative import (
    ComparatorDecision,
    PublicationNarrativeContract,
    seal_publication_narrative,
    validate_publication_narrative,
    verify_publication_narrative_seal,
)
from research_forge.paper_pipeline import GENERIC_JOURNAL_ARTICLE
from research_forge.paper_reporting_compliance import (
    MandatoryReportingItem,
    MandatoryReportingRegister,
    audit_reporting_integrity,
)
from research_forge.paper_reviewer_attack_surface import (
    audit_reviewer_attack_surface,
)
from research_forge.manuscript_depth import ENGLISH_SHORT_REPORT
from research_forge.paper_venue_policy import (
    GENERIC_JOURNAL_POLICY,
    GENERIC_SHORT_REPORT_POLICY,
    get_venue_policy,
)
from research_forge.paper_visual_integrity import audit_visual_integrity
from research_forge.paper_visual_strategy import (
    CaptionClaimBinding,
    FigureSelectionRecord,
    FigureSpec,
    TableSpec,
    VisualArgumentPlan,
)
from research_forge.stage_four import (
    STAGE4_STEP_DEFINITIONS,
    _completion_artifact_hashes,
    _draft_depth_violations,
    _localized_depth_contract,
    _merge_verified_background_sources,
    _numeric_evidence_from_bound_claims,
    _drafting_evidence_view,
    _drafting_literature_view,
    _humanization_citation_view,
    _humanization_evidence_view,
    _humanization_reverse_outline_view,
    _materialize_profile_figure_markdown,
    _effective_depth_profile,
    _effective_evaluation_arm_estimates,
    _finding_requires_stage3_backfill,
    _has_blocking_review_findings,
    _normalize_verified_citation_prefixes,
    _publication_aliases_for_contract,
    _select_primary_visual_bindings,
    ensure_stage_four_dag,
    request_stage_four_revision,
    stage4_read_model,
)


def test_typesetting_numeric_evidence_uses_bound_profile_statistics(
    tmp_path: Path,
) -> None:
    import hashlib
    import json

    statistics = {
        "baseline_accuracy": 0.76,
        "treatment_accuracy": 0.9466666666666667,
        "paired_accuracy_difference": 0.18666666666666668,
        "paired_bootstrap_95_ci": [0.11333333333333333, 0.26],
        "mcnemar": {"exact_two_sided_p": 1.941574737429619e-06},
    }
    source = tmp_path / "statistics.json"
    source.write_text(json.dumps(statistics), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence_map = EvidenceClaimMap(
        track_id="profile-test",
        frozen_conclusion="The registered comparison was supported.",
        bindings=[
            EvidenceClaimBinding(
                claim_id="profile-primary-effect",
                kind="result_metric",
                statement=(
                    "Baseline accuracy was 0.760, treatment accuracy was 0.947, "
                    "and the paired difference was 0.187 with an interval from "
                    "0.113 to 0.260; the exact two-sided McNemar p-value was "
                    "1.94157e-06."
                ),
                evidence=[
                    EvidencePointer(
                        path=str(source),
                        sha256=digest,
                        evidence_type="project_artifact",
                    )
                ],
                allowed_sections=["results"],
                claim_strength="comparative",
                evidence_status="bound",
            )
        ],
        verified_source_ids=[],
        forbidden_moves=[],
        source_registry_sha256="0" * 64,
    )

    rows = _numeric_evidence_from_bound_claims(
        evidence_map,
        repository_root=tmp_path,
    )

    assert {item["path"] for item in rows} == {
        "statistics.json$.baseline_accuracy",
        "statistics.json$.treatment_accuracy",
        "statistics.json$.paired_accuracy_difference",
        "statistics.json$.paired_bootstrap_95_ci[0]",
        "statistics.json$.paired_bootstrap_95_ci[1]",
        "statistics.json$.mcnemar.exact_two_sided_p",
    }
    interval = [
        item["value"]
        for item in rows
        if "paired_bootstrap_95_ci" in item["path"]
    ]
    assert interval == [0.11333333333333333, 0.26]
    assert interval != [0.1288836925413637, 0.24444964079196965]


def test_profile_figures_are_materialized_as_relative_manuscript_assets(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "repository")
    project = repository.create_project("Profile figure materialization")
    study = repository.create_study(
        project.project_id,
        "Stage 4 figure binding",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-stage4-figure-binding",
    )
    source = tmp_path / "source-figure.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 256)
    context = SimpleNamespace(repository=repository, study_id=study.study_id)

    markdown, artifact_ids = _materialize_profile_figure_markdown(
        context,
        tmp_path / "work",
        {
            "status": "accepted",
            "figures": [
                {
                    "figure_id": "fig-primary-effect",
                    "caption": "Primary effect with a complete interval.",
                    "png_path": str(source),
                }
            ],
        },
    )

    assert "manuscript_assets/figures/fig-primary-effect.png" in markdown
    assert str(source) not in markdown
    assert artifact_ids
    assert (
        tmp_path
        / "work"
        / "stage_4_synthesis"
        / "manuscript_assets"
        / "figures"
        / "fig-primary-effect.png"
    ).is_file()


def test_contract_implementation_names_are_projected_to_publication_roles() -> None:
    contract = SimpleNamespace(
        baseline={"name": "v3_tech_quality_top5 ranking"},
        treatment={"name": "dual_quality_top5 ranking"},
        metrics=[{"name": "top_k_event_identification_rate"}],
        tasks=["formal-primary-task-b6b855d916b9ad5f"],
    )

    aliases = _publication_aliases_for_contract(contract)

    assert aliases["v3_tech_quality_top5"] == "参考方法"
    assert aliases["dual_quality_top5"] == "候选方法"
    assert aliases["top_k_event_identification_rate"] == "主要结局指标"
    assert aliases["formal-primary-task-b6b855d916b9ad5f"] == "注册研究任务"


def test_contract_implementation_names_use_english_publication_roles() -> None:
    contract = SimpleNamespace(
        baseline={"name": "v3_tech_quality_top5 ranking"},
        treatment={"name": "dual_quality_top5 ranking"},
        metrics=[{"name": "top_k_event_identification_rate"}],
        tasks=["formal-primary-task-b6b855d916b9ad5f"],
    )

    aliases = _publication_aliases_for_contract(contract, language="en")

    assert aliases["v3_tech_quality_top5"] == "reference method"
    assert aliases["dual_quality_top5"] == "candidate method"
    assert aliases["top_k_event_identification_rate"] == "primary outcome"
    assert aliases["formal-primary-task-b6b855d916b9ad5f"] == "registered study task"
    assert not any(re.search(r"[\u4e00-\u9fff]", value) for value in aliases.values())


def test_bound_evidence_mischaracterization_stays_in_stage_four() -> None:
    evidence_map = SimpleNamespace(
        bindings=[
            SimpleNamespace(
                claim_id="scoring-and-pairing",
                evidence_status="bound",
            )
        ]
    )
    finding = SimpleNamespace(
        category="evidence",
        claim_ids=["scoring-and-pairing"],
        diagnosis=(
            "The draft states that no rule exists, contradicts the supplied "
            "binding, and omits the bound evidence."
        ),
        required_change="Correct the evidence characterization and report the bound scoring rule.",
    )

    assert not _finding_requires_stage3_backfill(finding, evidence_map)


def test_missing_bound_claim_still_requires_stage_three_backfill() -> None:
    evidence_map = SimpleNamespace(bindings=[])
    finding = SimpleNamespace(
        category="evidence",
        claim_ids=["missing-claim"],
        diagnosis="No frozen evidence binds the requested comparison.",
        required_change="Obtain and freeze the missing result evidence.",
    )

    assert _finding_requires_stage3_backfill(finding, evidence_map)


def test_unbound_manuscript_statistic_is_removed_in_stage_four() -> None:
    evidence_map = SimpleNamespace(
        bindings=[
            SimpleNamespace(
                claim_id="evaluation:method",
                evidence_status="bound",
            )
        ]
    )
    finding = SimpleNamespace(
        category="evidence",
        claim_ids=["evaluation:method"],
        diagnosis=(
            "The draft reports an abstention count that is not contained in "
            "the supplied claim binding."
        ),
        required_change=(
            "Remove the unbound abstention statement, or add explicit frozen "
            "evidence before publication."
        ),
    )

    assert not _finding_requires_stage3_backfill(finding, evidence_map)


def test_primary_visual_prefers_semantic_result_over_first_required_claim() -> None:
    arm = SimpleNamespace(
        claim_id="profile-arm-definition",
        kind="operational",
        claim_strength="descriptive",
        evidence_status="bound",
        evidence_facets=["arm_definition"],
    )
    result = SimpleNamespace(
        claim_id="profile-primary-effect",
        kind="result_metric",
        claim_strength="comparative",
        evidence_status="bound",
        evidence_facets=["primary_result"],
    )
    denominator = SimpleNamespace(
        claim_id="profile-denominator",
        kind="operational",
        claim_strength="descriptive",
        evidence_status="bound",
        evidence_facets=["denominator_reconciliation"],
    )
    verdict = SimpleNamespace(
        claim_id="profile-frozen-verdict",
        kind="result_metric",
        claim_strength="comparative",
        evidence_status="bound",
        evidence_facets=[],
    )
    evidence_map = SimpleNamespace(bindings=[arm, result, verdict, denominator])
    narrative = SimpleNamespace(required_claim_ids=[arm.claim_id, result.claim_id])

    primary, plotted, contrasts, selected_denominator = (
        _select_primary_visual_bindings(evidence_map, narrative)
    )

    assert primary is arm
    assert plotted == [result]
    assert contrasts == [result]
    assert selected_denominator is denominator


def test_profile_evidence_map_inherits_frozen_background_sources() -> None:
    evidence_map = EvidenceClaimMap(
        track_id="profile-track",
        frozen_conclusion="The registered comparison was supported.",
        bindings=[],
        verified_source_ids=[],
        forbidden_moves=[],
        source_registry_sha256="a" * 64,
    )

    merged = _merge_verified_background_sources(
        evidence_map, ["source-b", "source-a", "source-a"]
    )

    assert merged.verified_source_ids == ["source-a", "source-b"]
    assert merged.source_registry_sha256 != evidence_map.source_registry_sha256
    assert evidence_map.verified_source_ids == []


def test_profile_evidence_map_binds_verified_background_metadata() -> None:
    evidence_map = EvidenceClaimMap(
        track_id="profile-track",
        frozen_conclusion="The registered comparison was supported.",
        bindings=[],
        verified_source_ids=[],
        forbidden_moves=[],
        source_registry_sha256="a" * 64,
    )

    merged = _merge_verified_background_sources(
        evidence_map,
        [
            {
                "resource_id": "resource-methods",
                "title": "A Verified Methods Paper",
                "canonical_metadata_hash": "b" * 64,
                "canonical_identifier": "doi:10.1000/methods",
            }
        ],
    )

    assert merged.verified_source_ids == ["resource-methods"]
    assert len(merged.bindings) == 1
    binding = merged.bindings[0]
    assert binding.kind == "literature_context"
    assert binding.evidence_status == "bound"
    assert binding.evidence[0].source_id == "resource-methods"
    assert binding.evidence[0].evidence_type == "verified_literature"
from research_forge.workflow_domain import (
    EntryMode,
    ExecutionStatus,
    GateType,
    Phase,
    StudyLifecycle,
    WorkflowRepository,
)


def test_advisory_review_findings_do_not_block_publication() -> None:
    assert not _has_blocking_review_findings(
        SimpleNamespace(
            decision="revise",
            blocking_finding_ids=[],
            required_finding_ids=["clarity-1"],
        )
    )
    assert _has_blocking_review_findings(
        SimpleNamespace(
            decision="revise",
            blocking_finding_ids=["evidence-1"],
            required_finding_ids=["evidence-1"],
        )
    )


def _reporting_register() -> MandatoryReportingRegister:
    return MandatoryReportingRegister(
        study_id="study-stage4-test",
        source_claim_envelope_id="claim-envelope-1",
        items=[
            MandatoryReportingItem(
                reporting_item_id="report-primary-effect",
                claim_id="claim-primary",
                category="primary",
                material=True,
                preregistered=True,
                required_destination="main_text",
                destination_section="results",
                rationale="The preregistered primary outcome must be reported.",
            ),
            MandatoryReportingItem(
                reporting_item_id="report-runtime-tradeoff",
                claim_id="claim-runtime-negative",
                category="negative",
                material=True,
                preregistered=True,
                required_destination="main_text",
                destination_section="discussion",
                rationale="The runtime cost changes the practical interpretation.",
            ),
        ],
        frozen=True,
        frozen_at="2026-07-27T00:00:00+00:00",
    )


def test_reviewer_attack_audit_allows_explicit_scope_disclaimers() -> None:
    report = audit_reviewer_attack_surface(
        title="A controlled paired evaluation",
        sections={
            "introduction": (
                "The study does not assess state-of-the-art performance or "
                "universal prompt effectiveness."
            ),
            "limitations": (
                "State-of-the-art capability cannot be inferred from this "
                "configuration."
            ),
        },
        reporting_register=_reporting_register(),
        presented_claim_ids=["claim-primary", "claim-runtime-negative"],
    )

    assert report.passed


def test_reviewer_attack_audit_allows_absolute_term_in_negated_list() -> None:
    register = _reporting_register()
    report = audit_reviewer_attack_surface(
        title="A bounded comparison",
        sections={
            "discussion": (
                "The result supports only the registered comparison, not live "
                "effectiveness, causal identification, universal superiority, "
                "or performance under untested conditions."
            )
        },
        reporting_register=register,
        presented_claim_ids=["claim-primary", "claim-runtime-negative"],
    )

    assert report.passed
    assert not report.findings


def test_reviewer_attack_audit_allows_absolute_term_in_no_claim_boundary() -> None:
    report = audit_reviewer_attack_surface(
        title="A bounded time-to-event comparison",
        sections={
            "introduction": (
                "The manuscript makes no literature-wide novelty claim, no "
                "claim that the endpoint is universally preferable, and no "
                "assertion that the fixture validates production settings."
            )
        },
        reporting_register=_reporting_register(),
        presented_claim_ids=["claim-primary", "claim-runtime-negative"],
    )

    assert report.passed
    assert not report.findings


def test_reviewer_attack_audit_distinguishes_order_from_novelty_first() -> None:
    register = _reporting_register()
    ranked = audit_reviewer_attack_surface(
        title="A bounded comparison",
        sections={"methods": "Eligible assets were ranked first by the registered score."},
        reporting_register=register,
        presented_claim_ids=["claim-primary", "claim-runtime-negative"],
    )
    novelty = audit_reviewer_attack_surface(
        title="A bounded comparison",
        sections={"introduction": "This is the first framework for the task."},
        reporting_register=register,
        presented_claim_ids=["claim-primary", "claim-runtime-negative"],
    )

    assert ranked.passed
    assert not novelty.passed


def test_reviewer_attack_audit_still_blocks_positive_absolute_claims() -> None:
    report = audit_reviewer_attack_surface(
        title="A controlled paired evaluation",
        sections={
            "introduction": "The method achieves state-of-the-art performance.",
        },
        reporting_register=_reporting_register(),
        presented_claim_ids=["claim-primary", "claim-runtime-negative"],
    )

    assert not report.passed
    assert report.findings[0].attack_type == "unsupported_absolute"


def test_legacy_paired_evaluation_exposes_both_arm_estimates() -> None:
    evaluation = SimpleNamespace(
        arm_estimates={},
        baseline_estimate=0.5,
        treatment_estimate=1.0,
    )

    assert _effective_evaluation_arm_estimates(evaluation) == {
        "baseline": 0.5,
        "treatment": 1.0,
    }


def test_verified_citation_prefix_normalization_is_bounded() -> None:
    draft = PaperDraftSections(
        title="足够长度的论文标题",
        abstract="摘要" * 60,
        introduction="引言" * 160,
        related_work=(
            "相关工作 [source-abc123] [source-unregistered] "
            "[resource-invented] " * 20
        ),
        methods="方法" * 260,
        results="结果" * 210,
        discussion="讨论" * 260,
        limitations="局限" * 110,
        conclusion="结论" * 80,
        data_availability="数据可用性说明" * 5,
        ethics_statement="伦理声明" * 8,
        author_contributions="作者贡献" * 8,
        conflict_of_interest="无利益冲突" * 4,
        funding="无外部资助" * 4,
        ai_disclosure="人工智能辅助披露" * 5,
    )

    normalized = _normalize_verified_citation_prefixes(
        draft,
        ["resource-abc123"],
    )

    assert "[resource-abc123]" in normalized.related_work
    assert "[source-abc123]" not in normalized.related_work
    assert "[source-unregistered]" not in normalized.related_work
    assert "[resource-invented]" not in normalized.related_work


def test_stage_four_drafting_views_bound_prompt_without_losing_claims() -> None:
    full_map = {
        "frozen_conclusion": "The frozen decision is inconclusive.",
        "forbidden_moves": ["Do not claim support."],
        "bindings": [
            {
                "claim_id": "claim-primary",
                "kind": "result",
                "statement": "The registered conjunction was not satisfied.",
                "claim_strength": "comparative",
                "evidence_status": "bound",
                "allowed_sections": ["abstract", "results"],
                "evidence": [
                    {
                        "path": "stage3/evaluations/result.json",
                        "sha256": "a" * 64,
                        "json_path": "$.decision",
                    }
                ],
            },
            {
                "claim_id": "claim-secondary",
                "kind": "limitation",
                "statement": "Inference is conditional on observed seeds.",
                "claim_strength": "descriptive",
                "evidence_status": "bound",
                "allowed_sections": ["discussion"],
                "evidence": [{"path": "context.json", "sha256": "b" * 64}],
            },
        ],
    }

    compact = _drafting_evidence_view(
        full_map,
        claim_ids={"claim-primary"},
    )

    assert [item["claim_id"] for item in compact["bindings"]] == [
        "claim-primary"
    ]
    assert compact["bindings"][0]["statement"] == (
        "The registered conjunction was not satisfied."
    )
    assert "evidence" not in compact["bindings"][0]
    assert "immutable full EvidenceClaimMap" in compact["provenance_note"]

    sources = _drafting_literature_view(
        [
            {
                "source_id": "source-1",
                "title": "Verified paper",
                "authors": ["A. Author"],
                "year": 2026,
                "locator": "doi:10.1/example",
                "notes": "n" * 5000,
                "verified": True,
                "verification_method": "Crossref",
                "added_at": "2026-07-27T00:00:00+00:00",
            }
        ]
    )
    assert len(sources[0]["notes"]) == 1200
    assert sources[0]["source_id"] == "source-1"
    assert "verification_method" not in sources[0]


def test_humanization_views_keep_authority_keys_without_repeating_long_prose() -> None:
    import hashlib

    long_statement = "registered protocol detail " * 600
    evidence = {
        "frozen_conclusion": "The estimate is assumption-bounded.",
        "forbidden_moves": ["Do not claim randomization."],
        "bindings": [
            {
                "claim_id": "claim-long-protocol",
                "kind": "registered_protocol",
                "statement": long_statement,
                "claim_strength": "descriptive",
                "evidence_status": "bound",
                "evidence_facets": ["protocol"],
                "allowed_sections": ["methods"],
            }
        ],
    }
    compact = _humanization_evidence_view(evidence)
    binding = compact["bindings"][0]
    assert binding["claim_id"] == "claim-long-protocol"
    assert binding["statement_excerpted"] is True
    assert len(binding["statement_excerpt"]) < len(long_statement)
    assert binding["statement_sha256"] == hashlib.sha256(
        long_statement.encode("utf-8")
    ).hexdigest()

    reverse = _humanization_reverse_outline_view(
        {
            "passed": True,
            "violations": [],
            "sections": [
                {
                    "section_key": "methods",
                    "thesis": "The estimator follows the frozen protocol.",
                    "expected_moves": ["design", "estimator"],
                    "paragraphs": [
                        {
                            "opening_sentence": "A long paragraph is omitted.",
                            "citation_keys": ["source-1"],
                            "numeric_token_count": 4,
                            "visual_callout_present": False,
                        }
                    ],
                }
            ],
        }
    )
    assert reverse["sections"][0]["paragraph_count"] == 1
    assert reverse["sections"][0]["citation_keys"] == ["source-1"]
    assert "opening_sentence" not in reverse["sections"][0]

    citation = _humanization_citation_view(
        {
            "bibliography_hygiene_passed": True,
            "claim_source_support_passed": True,
            "cited_source_ids": ["source-1"],
            "unknown_source_ids": [],
            "uncited_registered_source_ids": [],
            "rejected_or_unverified_count": 0,
            "support_items": [{"reason": "repeated verbose prose"}],
        }
    )
    assert citation["claim_source_support_passed"] is True
    assert "support_items" not in citation


def test_outline_sanitizer_drops_only_unregistered_identifiers() -> None:
    from research_forge.paper_authoring import (
        EvidenceClaimBinding,
        EvidenceClaimMap,
    )

    outline = HierarchicalPaperOutline(
        title="Bounded outline",
        thesis="Only registered identifiers may carry scientific authority.",
        abstract_moves=[
            "context",
            "question",
            "method",
            "result",
            "implication",
        ],
        sections=[
            OutlineNode(
                node_id="sec-abstract",
                section_key="abstract",
                heading="Abstract",
                level=2,
                purpose="Summarize the bounded study.",
                argument="Report only registered evidence.",
                claim_ids=["claim-known", "claim-invented"],
                source_ids=["source-known", "source-invented"],
                figure_slot_ids=["fig-model-invented"],
                table_slot_ids=["tab-model-invented"],
                children=[],
            ),
            *[
                OutlineNode(
                    node_id=f"sec-{key.replace('_', '-')}",
                    section_key=key,
                    heading=key.replace("_", " ").title(),
                    level=2,
                    purpose=f"Provide the {key} section.",
                    argument="Stay inside registered evidence.",
                    claim_ids=[],
                    source_ids=[],
                    figure_slot_ids=[],
                    table_slot_ids=[],
                    children=[],
                )
                for key in (
                    "introduction",
                    "related_work",
                    "methods",
                    "results",
                    "discussion",
                )
            ],
        ],
    )
    evidence_map = EvidenceClaimMap(
        track_id="track-test",
        frozen_conclusion="The result is bounded.",
        bindings=[
            EvidenceClaimBinding(
                claim_id="claim-known",
                kind="result",
                statement="The registered result is bounded.",
                claim_strength="descriptive",
                evidence_status="bound",
                allowed_sections=["abstract"],
                evidence=[],
            )
        ],
        verified_source_ids=["source-known"],
        source_registry_sha256="a" * 64,
        forbidden_moves=[],
    )

    sanitized = _sanitize_outline_references(
        outline,
        evidence_claim_map=evidence_map,
    )

    assert sanitized.sections[0].claim_ids == ["claim-known"]
    assert sanitized.sections[0].source_ids == ["source-known"]
    assert sanitized.sections[0].figure_slot_ids == []
    assert sanitized.sections[0].table_slot_ids == []
    assert outline.sections[0].claim_ids == [
        "claim-known",
        "claim-invented",
    ]


def _contribution() -> ContributionCandidate:
    return ContributionCandidate(
        contribution_id="contribution-evidence-gate",
        contribution_type="meaningful_tradeoff",
        problem="Research-agent claims can reach delivery without inspectable evidence.",
        proposed_value="A pre-delivery evidence gate reduces unsupported claims in the frozen study.",
        supporting_claim_ids=["claim-primary"],
        supporting_evidence_ids=["evidence-evaluation"],
        novelty_basis=["frozen same-backbone comparison"],
        practical_value=["pre-delivery claim inspection"],
        limitations=["registered tasks only"],
        publication_strength="moderate",
        eligible_as_main_story=True,
    )


def _narrative() -> PublicationNarrativeContract:
    return PublicationNarrativeContract(
        study_id="study-stage4-test",
        selected_contribution_id="contribution-evidence-gate",
        central_thesis=(
            "A pre-delivery evidence gate reduced unsupported claims under the "
            "registered evaluator and task set."
        ),
        reader_problem=(
            "Autonomous research agents can deliver claims whose evidentiary "
            "support is difficult to inspect."
        ),
        existing_gap=(
            "Existing delivery workflows do not preserve an explicit, auditable "
            "claim-to-evidence decision boundary."
        ),
        proposed_resolution=(
            "The study inserts a frozen evidence gate before delivery and audits "
            "its result with a same-backbone comparison."
        ),
        primary_advantage="Fewer unsupported claims in the frozen evaluation.",
        advantage_conditions=["three registered tasks", "frozen evaluator"],
        mechanism_explanation=["evidence qualification precedes delivery"],
        required_claim_ids=["claim-primary"],
        mandatory_negative_claim_ids=["claim-runtime-negative"],
        comparator_decisions=[
            ComparatorDecision(
                comparator_id="baseline-no-gate",
                comparator_name="same-backbone delivery without the gate",
                supports_claim_ids=["claim-primary"],
                necessity="required",
                placement="main_text",
                frozen_in_research_contract=True,
                rationale="This comparator isolates the delivery-time gate.",
            )
        ],
        reader_memory_point=(
            "Evidence gating can reduce unsupported delivery claims, with a "
            "measured runtime trade-off in this configuration."
        ),
        prohibited_story_moves=[
            "claim universal reliability",
            "hide the registered runtime outcome",
        ],
    )


def test_narrative_preserves_material_negative_results_and_seals() -> None:
    register = _reporting_register()
    candidate = _contribution()
    narrative = _narrative()
    assert (
        validate_publication_narrative(
            narrative,
            selected_contribution=candidate,
            reporting_register=register,
            allowed_claim_ids={"claim-primary", "claim-runtime-negative"},
        )
        == []
    )
    frozen = seal_publication_narrative(narrative, approved_by="owner")
    assert frozen.status == "frozen"
    assert verify_publication_narrative_seal(frozen)
    assert not verify_publication_narrative_seal(
        frozen.model_copy(update={"central_thesis": frozen.central_thesis + " Broader."})
    )

    omitted = narrative.model_copy(update={"mandatory_negative_claim_ids": []})
    violations = validate_publication_narrative(
        omitted,
        selected_contribution=candidate,
        reporting_register=register,
        allowed_claim_ids={"claim-primary", "claim-runtime-negative"},
    )
    assert any("negative" in item for item in violations)


def test_reporting_and_attack_surface_cannot_hide_registered_result() -> None:
    register = _reporting_register()
    report = audit_reporting_integrity(
        register,
        presented_claim_ids=["claim-primary"],
        allowed_claim_ids={"claim-primary", "claim-runtime-negative"},
    )
    assert not report.passed
    assert report.omitted_claim_ids == ["claim-runtime-negative"]

    attack = audit_reviewer_attack_surface(
        title="A Universal Framework for Research Reliability",
        sections={"results": "The gate completely solves unsupported claims."},
        reporting_register=register,
        presented_claim_ids=["claim-primary"],
    )
    assert not attack.passed
    assert attack.suppression_detected
    assert {item.attack_type for item in attack.findings} >= {
        "title_overpromise",
        "unsupported_absolute",
        "mandatory_result_omission",
    }

    factorial_language = audit_reviewer_attack_surface(
        title="A Randomized Factorial Study",
        sections={
            "methods": (
                "The two registered factors were completely crossed, so every "
                "level of one factor occurred with every level of the other."
            )
        },
        reporting_register=register,
        presented_claim_ids=["claim-primary", "claim-runtime-negative"],
    )
    assert factorial_language.passed

    colon_register = register.model_copy(
        update={
            "items": [
                register.items[0].model_copy(
                    update={
                        "claim_id": (
                            "claim-envelope-0c7c2ef566ec731c:limitation-1"
                        )
                    }
                )
            ]
        }
    )
    colon_attack = audit_reviewer_attack_surface(
        title="Bounded registered result",
        sections={"results": "No mandatory claim is presented here."},
        reporting_register=colon_register,
        presented_claim_ids=[],
    )
    omission_id = colon_attack.findings[0].finding_id
    assert ":" not in omission_id
    assert len(omission_id) <= 107


def test_humanization_preserves_full_scientific_semantics() -> None:
    source = snapshot_semantics(
        prose="Accuracy improved by 4.0% under the frozen task set [source-a]. Figure 2 reports the result.",
        claim_ids=["claim-primary"],
        evidence_ids=["evidence-a"],
        claim_relationships={"claim-primary": "comparative"},
        scope_qualifiers={"claim-primary": ["frozen task set"]},
        uncertainty_levels={"claim-primary": "estimated"},
        result_roles={"claim-primary": "primary"},
        confirmatory_status="confirmatory_used",
        evidence_maturity="prospective_blind",
        definitions={"Accuracy": "correct predictions / eligible predictions"},
    )
    identical = audit_humanization_integrity(source, source)
    assert identical.passed

    repeated_number = snapshot_semantics(
        prose=(
            "There were 0 eligible exclusions. "
            "The exclusion count was therefore 0."
        ),
        claim_ids=["claim-primary"],
        evidence_ids=["evidence-a"],
        claim_relationships={"claim-primary": "comparative"},
        scope_qualifiers={"claim-primary": []},
        uncertainty_levels={"claim-primary": "estimated"},
        result_roles={"claim-primary": "primary"},
        confirmatory_status="confirmatory_used",
        evidence_maturity="prospective_blind",
        definitions={},
    )
    deduplicated_number = snapshot_semantics(
        prose="There were 0 eligible exclusions.",
        claim_ids=["claim-primary"],
        evidence_ids=["evidence-a"],
        claim_relationships={"claim-primary": "comparative"},
        scope_qualifiers={"claim-primary": []},
        uncertainty_levels={"claim-primary": "estimated"},
        result_roles={"claim-primary": "primary"},
        confirmatory_status="confirmatory_used",
        evidence_maturity="prospective_blind",
        definitions={},
    )
    assert audit_humanization_integrity(
        repeated_number, deduplicated_number
    ).passed

    changed_number = source.model_copy(
        update={"number_multiset": {"5.0%": 1}}
    )
    numeric_change = audit_humanization_integrity(source, changed_number)
    assert not numeric_change.passed
    assert "numeric values changed" in numeric_change.violations[0]

    causal = source.model_copy(
        update={"claim_relationships": {"claim-primary": "causal"}}
    )
    changed = audit_humanization_integrity(source, causal)
    assert not changed.passed
    assert not changed.checks["claim_relationships_preserved"]
    causal_prose = audit_humanization_integrity(
        source,
        source,
        source_prose="The method improved accuracy by 4.0%.",
        humanized_prose="The method causes accuracy to improve by 4.0%.",
    )
    assert not causal_prose.passed
    assert not causal_prose.checks["no_new_causal_language"]

    profile = AuthorVoiceProfile(
        profile_id="voice-owner-001",
        language="en",
        source_types=["style_questionnaire"],
        sentence_length_distribution=SentenceLengthDistribution(
            short=0.25, medium=0.55, long=0.20
        ),
    )
    assert "paves the way" in profile.banned_phrases


def test_visual_case_selection_is_frozen_and_bounded() -> None:
    with pytest.raises(ValueError, match="outside"):
        FigureSelectionRecord(
            selection_id="selection-invalid-case",
            rule="median_case",
            eligible_sample_ids=["sample-1"],
            selected_sample_ids=["sample-2"],
            rationale="Select the median registered example for inspection.",
        )
    with pytest.raises(ValueError, match="seed"):
        FigureSelectionRecord(
            selection_id="selection-random-case",
            rule="random_registered",
            eligible_sample_ids=["sample-1", "sample-2"],
            selected_sample_ids=["sample-1"],
            rationale="Select one example from the frozen eligible pool.",
        )


def test_visual_integrity_checks_real_plan_and_manifest_fields() -> None:
    figure = FigureSpec(
        figure_id="fig-primary-result",
        role="primary_result",
        reader_question="What is the frozen intervention-comparator effect?",
        claim_ids=["claim-primary"],
        source_artifact_ids=["artifact-primary"],
        source_paths=["stage3/claim.json"],
        visual_type="effect_interval",
        main_or_supplement="main",
        must_show=["effect", "denominator"],
        must_not_imply=["causality"],
        alt_text="Registered effect and uncertainty from the frozen claim.",
        minimum_font_points=7,
        color_vision_safe=True,
        output_format="svg",
        caption_binding=CaptionClaimBinding(
            caption_id="caption-primary",
            figure_id="fig-primary-result",
            reader_question="What is the frozen result?",
            comparison="intervention versus comparator",
            denominator_claim_ids=["claim-primary"],
            uncertainty_claim_ids=["claim-primary"],
            observation_claim_ids=["claim-primary"],
            limitation_claim_ids=[],
            caption_text="Registered effect with eligible denominator.",
        ),
    )
    plan = VisualArgumentPlan(
        study_id="study-stage4-test",
        narrative_contract_id="a" * 64,
        visual_thesis="Show the registered result without broadening its scope.",
        main_figure_budget=5,
        supplementary_figure_budget=8,
        figures=[figure],
    )
    manifest = PaperArtifactManifest(
        ready=True,
        records=[
            PaperArtifactRecord(
                slot_id="fig-primary-result",
                kind="figure",
                status="rendered",
                evidence_claim_ids=["claim-primary"],
                source_paths=["stage3/claim.json"],
                artifact_path="figures/fig-primary-result.svg",
                artifact_sha256="b" * 64,
                renderer="native_svg",
                rendering_code_path="research_forge/paper_authoring.py",
                rendering_code_sha256="c" * 64,
                reason="Rendered from frozen evidence.",
            )
        ],
        unresolved_slot_ids=[],
    )
    assert audit_visual_integrity(
        plan, manifest, venue_policy=GENERIC_JOURNAL_POLICY
    ).passed
    unsafe = plan.model_copy(
        update={
            "figures": [
                figure.model_copy(
                    update={
                        "dual_axis": True,
                        "output_format": "png",
                    }
                )
            ]
        }
    )
    report = audit_visual_integrity(
        unsafe, manifest, venue_policy=GENERIC_JOURNAL_POLICY
    )
    assert not report.passed
    assert not report.checks["axes_and_encoding_safe"]
    assert not report.checks["venue_vector_policy_satisfied"]


def test_readiness_thresholds_live_in_venue_profiles() -> None:
    assert GENERIC_JOURNAL_POLICY.minimum_verified_papers == 15
    assert GENERIC_SHORT_REPORT_POLICY.minimum_verified_papers == 8
    assert get_venue_policy("generic-short-report-v1").minimum_numeric_evidence == 8


def test_visual_integrity_accepts_hashed_deterministic_markdown_table() -> None:
    table = TableSpec(
        table_id="tab-lineage",
        role="experiment_setup",
        reader_question="Which frozen attempts produced each reported result?",
        claim_ids=["claim-lineage"],
        source_artifact_ids=["artifact-lineage"],
        source_paths=["stage3/context/lineage.json"],
        columns=["Arm", "Attempt", "Result"],
        main_or_supplement="main",
        caption_binding=CaptionClaimBinding(
            caption_id="caption-lineage",
            figure_id="tab-lineage",
            reader_question="Which attempts produced the reported results?",
            comparison="registered arms and seeds",
            denominator_claim_ids=["claim-lineage"],
            uncertainty_claim_ids=[],
            observation_claim_ids=["claim-lineage"],
            limitation_claim_ids=[],
            caption_text="Canonical frozen execution lineage.",
        ),
    )
    plan = VisualArgumentPlan(
        study_id="study-stage4-test",
        narrative_contract_id="d" * 64,
        visual_thesis="Show deterministic execution lineage without adding claims.",
        main_figure_budget=1,
        supplementary_figure_budget=1,
        figures=[],
        tables=[table],
    )
    manifest = PaperArtifactManifest(
        ready=True,
        records=[
            PaperArtifactRecord(
                slot_id="tab-lineage",
                kind="table",
                status="rendered",
                evidence_claim_ids=["claim-lineage"],
                source_paths=["stage3/context/lineage.json"],
                data_path="paper_assets/data/tab-lineage.csv",
                data_sha256="e" * 64,
                rendered_markdown="Table: Lineage\n| Arm | Attempt | Result |",
                rendering_code_path="research_forge/paper_authoring.py",
                rendering_code_sha256="f" * 64,
                reason="Rendered deterministically from frozen lineage.",
            )
        ],
        unresolved_slot_ids=[],
    )

    assert audit_visual_integrity(
        plan,
        manifest,
        venue_policy=GENERIC_JOURNAL_POLICY,
    ).passed


def test_outline_revision_restores_required_approved_section_without_new_claims() -> None:
    required_keys = [
        item.key for item in GENERIC_JOURNAL_ARTICLE.sections if item.required
    ]

    def node(key: str) -> OutlineNode:
        return OutlineNode(
            node_id=f"section-{key.replace('_', '-')}",
            section_key=key,
            heading=GENERIC_JOURNAL_ARTICLE.section(key).english_title,
            level=2,
            purpose=f"Present the registered {key} material without changing scope.",
            argument=f"This section organizes already approved material for {key}.",
        )

    initial = HierarchicalPaperOutline(
        title="Evidence-bound evaluation for autonomous research agents",
        thesis="The paper reports the frozen study within its registered evidence boundary.",
        abstract_moves=["context", "objective", "method", "result", "implication"],
        sections=[node(key) for key in required_keys],
    )
    revised = initial.model_copy(
        update={
            "sections": [
                item for item in reversed(initial.sections)
                if item.section_key != "related_work"
            ]
        }
    )
    restored = _restore_required_outline_structure(
        revised=revised,
        approved_initial=initial,
        contract=GENERIC_JOURNAL_ARTICLE,
    )
    assert [item.section_key for item in restored.sections] == required_keys
    related = next(
        item for item in restored.sections if item.section_key == "related_work"
    )
    original_related = next(
        item for item in initial.sections if item.section_key == "related_work"
    )
    assert related == original_related


def test_outline_repair_removes_undeclared_visual_slots() -> None:
    required_keys = [item.key for item in GENERIC_JOURNAL_ARTICLE.sections]

    def node(key: str) -> OutlineNode:
        return OutlineNode(
            node_id=f"section-{key.replace('_', '-')}",
            section_key=key,
            heading=GENERIC_JOURNAL_ARTICLE.section(key).english_title,
            level=2,
            purpose=f"Present the registered {key} material without changing scope.",
            argument=f"This section organizes approved material for {key}.",
        )

    initial = HierarchicalPaperOutline(
        title="Evidence-bound evaluation for autonomous research agents",
        thesis="The paper reports the frozen study within its registered evidence boundary.",
        abstract_moves=["context", "objective", "method", "result", "implication"],
        sections=[node(key) for key in required_keys],
    )
    revised_sections = list(initial.sections)
    revised_sections[0] = revised_sections[0].model_copy(
        update={"table_slot_ids": ["tab-never-declared"]}
    )
    revised = initial.model_copy(update={"sections": revised_sections})

    restored = _restore_required_outline_structure(
        revised=revised,
        approved_initial=initial,
        contract=GENERIC_JOURNAL_ARTICLE,
    )

    assert restored.sections[0].table_slot_ids == []


def test_stage_four_dag_is_persisted_with_owner_approval_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = WorkflowRepository(tmp_path / "repository")
    project = repository.create_project("Stage 4 test")
    study = repository.create_study(
        project.project_id,
        "Publication control",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-stage4-test",
    )
    authority = {
        "schema_version": 1,
        "study_id": study.study_id,
        "source_completion_id": "stage3-completion-test",
        "claim_registry_source": "scientific_claim_envelope_only",
        "claims": [{"claim_envelope_id": "claim-envelope-test"}],
        "raw_logs_are_claim_authority": False,
        "may_narrow_claims": True,
        "may_broaden_claims": False,
    }
    monkeypatch.setattr(
        "research_forge.stage_four.stage4_claim_authority",
        lambda _repository, _study_id: authority,
    )
    returned, steps = ensure_stage_four_dag(repository, study.study_id)
    assert returned == authority
    assert len(steps) == len(STAGE4_STEP_DEFINITIONS) == 31
    assert all(step.phase is Phase.PAPER for step in steps)
    assert [step.step_type for step in steps] == [
        item.step_type for item in STAGE4_STEP_DEFINITIONS
    ]
    assert next(
        step for step in steps if step.step_type == "bounded_content_revision"
    ).max_retries == 5
    assert next(
        step for step in steps if step.step_type == "draft_generation"
    ).max_retries == 3
    assert {item.gate_type for item in repository.list_gates(study.study_id)} >= {
        GateType.PUBLICATION_NARRATIVE,
        GateType.VISUAL_ARGUMENT,
        GateType.AUTHOR_VOICE,
        GateType.FINAL_SUBMISSION,
    }
    _, repeated = ensure_stage_four_dag(repository, study.study_id)
    assert {item.step_instance_id for item in repeated} == {
        item.step_instance_id for item in steps
    }
    repository.save_study(
        repository.load_study(study.study_id).model_copy(
            update={
                "execution_status": ExecutionStatus.SUCCEEDED,
                "lifecycle": StudyLifecycle.COMPLETED,
            }
        ),
        "test_previous_revision_completed",
    )
    _, repaired = ensure_stage_four_dag(
        repository,
        study.study_id,
        workflow_revision=2,
    )
    assert len(repaired) == len(STAGE4_STEP_DEFINITIONS)
    assert not (
        {item.step_instance_id for item in repaired}
        & {item.step_instance_id for item in steps}
    )
    assert all(
        item.parameters["stage4_workflow_revision"] == 2
        for item in repaired
    )
    assert (
        repository.load_study(study.study_id).execution_status
        is ExecutionStatus.QUEUED
    )
    assert (
        repository.load_study(study.study_id).lifecycle
        is StudyLifecycle.ACTIVE
    )
    revision_root = (
        repository.root
        / "studies"
        / study.study_id
        / "stage4_revisions"
        / "v2"
        / "stage4"
    )
    revision_root.mkdir(parents=True, exist_ok=True)
    (revision_root / "stage4_evidence_backfill_request_v1.json").write_text(
        '{"status":"sufficient","revision":2}',
        encoding="utf-8",
    )
    read_model = stage4_read_model(repository, study.study_id)
    assert read_model["initialized"] is True
    assert read_model["overview"]["total_steps"] == 62
    assert read_model["overview"]["workflow_revision"] == 2
    assert read_model["artifacts"][
        "stage4_evidence_backfill_request_v1"
    ]["revision"] == 2
    assert len(read_model["gates"]) == 10


def test_stage_four_gate_revision_preserves_history_and_creates_vnext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = WorkflowRepository(tmp_path / "repository")
    project = repository.create_project("Stage 4 revision test")
    study = repository.create_study(
        project.project_id,
        "Stage 4 revision test",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    authority = {
        "schema_version": 1,
        "study_id": study.study_id,
        "source_completion_id": "stage3-completion-revision-test",
        "claim_registry_source": "scientific_claim_envelope_only",
        "claims": [{"claim_envelope_id": "claim-envelope-test"}],
        "raw_logs_are_claim_authority": False,
        "may_narrow_claims": True,
        "may_broaden_claims": False,
    }
    monkeypatch.setattr(
        "research_forge.stage_four.stage4_claim_authority",
        lambda _repository, _study_id: authority,
    )
    _, original_steps = ensure_stage_four_dag(repository, study.study_id)
    gate = next(
        item
        for item in repository.list_gates(study.study_id)
        if item.gate_type is GateType.PUBLICATION_NARRATIVE
    )

    _, revised_steps, request = request_stage_four_revision(
        repository,
        study.study_id,
        gate.gate_id,
        reason="摘要保持一个自然段，并明确报告配对效应。",
    )

    assert request["workflow_revision"] == 2
    assert request["historical_artifacts_preserved"] is True
    assert len(revised_steps) == len(STAGE4_STEP_DEFINITIONS)
    assert not (
        {item.step_instance_id for item in revised_steps}
        & {item.step_instance_id for item in original_steps}
    )
    assert all(
        item.parameters["owner_revision_instruction"]
        == "摘要保持一个自然段，并明确报告配对效应。"
        for item in revised_steps
    )
    assert next(
        item
        for item in repository.list_gates(study.study_id)
        if item.gate_id == gate.gate_id
    ).status.value == "rejected"
    request_path = (
        repository.root
        / "studies"
        / study.study_id
        / "stage4_revisions"
        / "v2"
        / "stage4"
        / "owner_revision_request_v2.json"
    )
    assert request_path.is_file()


def test_completion_hashes_exclude_the_completion_steps_own_result(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "repository")
    project = repository.create_project("Completion self-reference test")
    study = repository.create_study(
        project.project_id,
        "Completion self-reference test",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    study_root = repository.root / "studies" / study.study_id
    own_result = study_root / "step_results" / "step-completion.json"
    other_result = study_root / "step_results" / "step-upstream.json"
    own_result.parent.mkdir(parents=True, exist_ok=True)
    own_result.write_text('{"old": true}', encoding="utf-8")
    other_result.write_text('{"stable": true}', encoding="utf-8")
    repository.register_artifact(
        study.study_id,
        str(own_result),
        "0" * 64,
        kind="step_result",
    )
    repository.register_artifact(
        study.study_id,
        str(other_result),
        "1" * 64,
        kind="step_result",
    )

    hashes = _completion_artifact_hashes(
        repository,
        study.study_id,
        completion_step_id="step-completion",
    )

    assert "step_results/step-completion.json" not in hashes
    assert "step_results/step-upstream.json" in hashes


def test_stage_four_prompts_do_not_embed_a_previous_study() -> None:
    source = (
        Path(__file__).parents[1] / "research_forge" / "stage_four.py"
    ).read_text(encoding="utf-8")
    forbidden_previous_study_details = {
        "AdamW learning rate 2e-4",
        "LoRA targets",
        "15 domains",
        "60-item instrument",
        "question/answer labels were mojibake",
        "A/B candidate-scoring procedure",
    }
    assert all(
        detail not in source for detail in forbidden_previous_study_details
    )
    assert "unless each retained fact is explicitly present" in source
    assert "supplied Study-specific evidence bindings" in source


def test_primary_visual_question_does_not_assume_three_arms() -> None:
    source = (
        Path(__file__).parents[1] / "research_forge" / "stage_four.py"
    ).read_text(encoding="utf-8")
    typesetting = (
        Path(__file__).parents[1] / "research_forge" / "paper_typesetting.py"
    ).read_text(encoding="utf-8")

    assert "冻结三臂的规范对齐得分" not in source
    assert "冻结三臂的规范对齐得分" not in typesetting
    assert "比较条件之间的主要结果有何差异？" in source


def test_draft_normalization_collapses_accidental_repeated_academic_term() -> None:
    draft = PaperDraftSections.model_construct(
        title="A registered comparison",
        abstract=(
            "We evaluated a registered study task task under a fixed split and "
            "preserved the bounded scientific conclusion."
        ),
        introduction="Introduction.",
        related_work="Related work.",
        methods="Methods.",
        results="Results.",
        discussion="Discussion.",
        limitations="Limitations.",
        conclusion="Conclusion.",
        data_availability="Data availability.",
        ethics_statement="Ethics statement.",
        author_contributions="Author contributions.",
        conflict_of_interest="No conflicts.",
        funding="No funding.",
        ai_disclosure="AI disclosure.",
    )

    normalized, trace = normalize_paper_draft(draft)

    assert "task task" not in normalized.abstract
    assert "study task under" in normalized.abstract
    assert "collapsed_accidental_adjacent_term_duplicate" in trace.operations


def test_reader_facing_governance_normalization_preserves_methods_boundary() -> None:
    draft = PaperDraftSections.model_construct(
        title="A registered comparison",
        abstract="A complete abstract describing a frozen comparison without other internal workflow language.",
        introduction="We study a frozen comparison.",
        related_work="Prior work is summarized here.",
        methods="The protocol and artifacts were frozen before execution.",
        results=(
            "The frozen comparison favored the candidate, while one secondary "
            "assessment remained incomplete.\n\n"
            "The callout marks a pending artifact that was not supplied for inspection."
        ),
        discussion="The frozen result answers the bounded question.",
        limitations="The frozen boundary excludes other populations.",
        conclusion="Within the frozen comparison, the candidate performed better.",
        data_availability="Available with the registered package.",
        ethics_statement="No human participants were involved.",
        author_contributions="The author designed and reported the study.",
        conflict_of_interest="No conflicts were declared.",
        funding="No external funding was reported.",
        ai_disclosure="AI-assisted tools supported language editing.",
    )

    normalized = normalize_reader_facing_governance(draft)

    assert "frozen" not in normalized.abstract.casefold()
    assert "frozen" not in normalized.introduction.casefold()
    assert "registered comparison" in normalized.results.casefold()
    assert "pending artifact" not in normalized.results.casefold()
    assert "favored the candidate" in normalized.results.casefold()
    assert "incomplete" not in normalized.results.casefold()
    assert "not fully specified" in normalized.results.casefold()
    assert "frozen" in normalized.methods.casefold()
    assert "frozen" in normalized.limitations.casefold()


def test_short_report_uses_its_own_depth_contract() -> None:
    def paragraphs(count: int, size: int, headings: int = 0) -> str:
        values = ["测" * size for _ in range(count)]
        for index in range(min(headings, count)):
            values[index] = f"### 小节{index + 1}\n" + values[index]
        return "\n\n".join(values)

    draft = SimpleNamespace(
        abstract="测" * 380,
        introduction=paragraphs(3, 300),
        related_work=paragraphs(3, 250),
        methods=paragraphs(5, 300, 3),
        results=paragraphs(4, 300, 2),
        discussion=paragraphs(4, 300, 2),
        limitations="测" * 650,
        conclusion="测" * 320,
    )
    assert _draft_depth_violations(
        draft,
        profile="short-report",
    ) == []
    assert _draft_depth_violations(
        draft,
        profile="journal-article",
    )


def test_short_report_abstract_does_not_stop_at_the_hard_minimum() -> None:
    def paragraphs(count: int, size: int, headings: int = 0) -> str:
        values = ["研究" * size for _ in range(count)]
        for index in range(min(headings, count)):
            values[index] = f"### 小节{index + 1}\n" + values[index]
        return "\n\n".join(values)

    draft = SimpleNamespace(
        # The Chinese short-report hard floor is 300 Han characters, but the
        # preferred complete-abstract range starts at 340. Crossing 300 must
        # not make the draft appear complete.
        abstract="研" * 320,
        introduction=paragraphs(3, 300),
        related_work=paragraphs(3, 250),
        methods=paragraphs(5, 300, 3),
        results=paragraphs(4, 300, 2),
        discussion=paragraphs(4, 300, 2),
        limitations="研" * 650,
        conclusion="研" * 320,
    )

    violations = _draft_depth_violations(
        draft,
        profile="short-report",
        language="zh",
    )

    assert any(
        "below the preferred complete-abstract range" in item
        and "target 380" in item
        for item in violations
    )


def test_stage_four_depth_precheck_matches_canonical_short_report_profile() -> None:
    contract = _localized_depth_contract("short-report", language="en")

    assert (
        contract["sections"]["results"]
        == ENGLISH_SHORT_REPORT.section_minimums["results"]
    )
    assert contract["total"] == ENGLISH_SHORT_REPORT.minimum_total
    assert contract["generation_total"] >= int(contract["total"] * 1.09)
    assert (
        contract["generation_sections"]["results"]
        > contract["sections"]["results"]
    )
    assert contract["generation_sections"]["results"] >= int(
        contract["sections"]["results"] * 1.19
    )


def test_english_manuscript_depth_uses_words_instead_of_han_characters() -> None:
    contract = _localized_depth_contract("short-report", language="en")

    def prose(section: str) -> str:
        paragraph_count = max(
            contract["paragraphs"].get(section, 1),
            contract["subsections"].get(section, 0),
        )
        target = int(contract["sections"][section] * 1.2)
        words_per_paragraph = max(20, target // paragraph_count + 1)
        paragraphs = [
            " ".join(["evidence"] * words_per_paragraph)
            for _ in range(paragraph_count)
        ]
        for index in range(contract["subsections"].get(section, 0)):
            paragraphs[index] = (
                f"### Registered analysis {index + 1}\n" + paragraphs[index]
            )
        return "\n\n".join(paragraphs)

    draft = SimpleNamespace(
        **{section: prose(section) for section in contract["sections"]}
    )

    assert _draft_depth_violations(
        draft,
        profile="short-report",
        language="en",
    ) == []
    assert any(
        "English words" in item
        for item in _draft_depth_violations(
            SimpleNamespace(**{section: "" for section in contract["sections"]}),
            profile="short-report",
            language="en",
        )
    )


def test_english_depth_precheck_does_not_count_result_numbers_as_prose_words() -> None:
    contract = _localized_depth_contract("journal-article", language="en")

    def section_prose(section: str, word_count: int) -> str:
        paragraph_count = max(
            contract["paragraphs"].get(section, 1),
            contract["subsections"].get(section, 0),
        )
        per_paragraph = max(20, word_count // paragraph_count)
        paragraphs = [" ".join(["evidence"] * per_paragraph) for _ in range(paragraph_count)]
        remaining = word_count - sum(item.count("evidence") for item in paragraphs)
        paragraphs[-1] += " " + " ".join(["evidence"] * max(0, remaining))
        for index in range(contract["subsections"].get(section, 0)):
            paragraphs[index] = f"### Registered analysis\n{paragraphs[index]}"
        return "\n\n".join(paragraphs)

    draft = SimpleNamespace(
        **{
            section: section_prose(
                section,
                minimum - 42 if section == "results" else int(minimum * 1.25),
            )
            + (" 1 2 3 4 5 6 7 8 9 10 11 12" if section == "results" else "")
            for section, minimum in contract["sections"].items()
        }
    )

    violations = _draft_depth_violations(
        draft,
        profile="journal-article",
        language="en",
    )

    assert any(
        item.startswith("results:")
        and "English words < 1000" in item
        and "counting tolerance floor 980" in item
        for item in violations
    )


def test_english_manuscript_depth_rejects_chinese_core_narrative() -> None:
    contract = _localized_depth_contract("short-report", language="en")
    draft = SimpleNamespace(
        **{
            section: ("### 小节\n\n" if section in {"methods", "results", "discussion"} else "")
            + "这是中文研究叙述。" * max(80, minimum)
            for section, minimum in contract["sections"].items()
        }
    )

    violations = _draft_depth_violations(
        draft,
        profile="short-report",
        language="en",
    )

    assert any("manuscript language mismatch" in item for item in violations)


def test_evidence_boundary_report_uses_short_report_depth_contract() -> None:
    claim = {
        "maximum_claim_tier": "evidence_boundary_report",
        "evidence_level": "L0_boundary_only",
    }

    assert _effective_depth_profile("journal-article", claim) == "short-report"
    assert (
        _effective_depth_profile(
            "journal-article",
            {"maximum_claim_tier": "confirmatory"},
        )
        == "journal-article"
    )
