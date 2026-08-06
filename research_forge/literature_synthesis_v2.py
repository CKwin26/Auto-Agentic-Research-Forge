from __future__ import annotations

"""Evidence-granular literature synthesis contracts for Stage 4.

Network transport remains in ``research_forge.retrieval``.  The adapters below
describe typed capabilities and licenses; they do not call provider SDKs or
grant untrusted external text instruction authority.
"""

import re
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


LiteratureMode = Literal[
    "metadata_context",
    "narrative_related_work",
    "systematic_review",
    "bibliometric_map",
]


class EvidenceSpan(StrictModel):
    evidence_span_id: str = Field(pattern=r"^lit-span-[a-z0-9-]{2,120}$")
    paper_id: str
    snapshot_artifact_id: str
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section: str | None = None
    quote_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=10_000)
    evidence_role: Literal[
        "research_question",
        "method",
        "data",
        "comparator",
        "result",
        "limitation",
        "citation_context",
    ]

    @model_validator(mode="after")
    def valid_page_range(self) -> "EvidenceSpan":
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("literature evidence page range is reversed")
        return self


class PaperContributionCard(StrictModel):
    schema_version: int = 2
    paper_id: str
    title: str
    authors: list[str]
    year: int | None = Field(default=None, ge=1400, le=2200)
    venue: str | None = None
    doi: str | None = None
    full_text_status: Literal[
        "metadata_only", "abstract_only", "full_text_frozen", "unavailable"
    ]
    study_type: str | None = None
    research_question: str | None = None
    population_or_task: str | None = None
    dataset: str | None = None
    intervention_or_method: str | None = None
    comparator: str | None = None
    outcomes: list[str] = Field(default_factory=list)
    estimands: list[str] = Field(default_factory=list)
    evaluation_design: str | None = None
    major_results: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    relation_to_current_study: str | None = None
    supportive_or_conflicting: Literal[
        "supportive", "conflicting", "mixed", "context_only", "unknown"
    ] = "unknown"
    evidence_span_ids: list[str] = Field(default_factory=list)
    extraction_confidence: float = Field(ge=0, le=1)
    human_review_status: Literal[
        "not_reviewed", "accepted", "revised", "rejected"
    ] = "not_reviewed"

    @model_validator(mode="after")
    def substantive_fields_require_evidence(self) -> "PaperContributionCard":
        substantive = any(
            (
                self.study_type,
                self.research_question,
                self.population_or_task,
                self.dataset,
                self.intervention_or_method,
                self.comparator,
                self.outcomes,
                self.estimands,
                self.evaluation_design,
                self.major_results,
                self.limitations,
            )
        )
        if substantive and not self.evidence_span_ids:
            raise ValueError(
                "substantive literature claims require abstract/full-text EvidenceSpan IDs"
            )
        if self.full_text_status == "metadata_only" and substantive:
            raise ValueError("metadata-only sources cannot populate substantive contribution fields")
        return self


class LiteratureComparisonRow(StrictModel):
    family_id: str
    family_label: str
    research_problem: str
    common_methods: list[str]
    data_and_evaluation: list[str]
    known_results: list[str]
    known_limitations: list[str]
    current_study_difference: str
    paper_ids: list[str] = Field(min_length=1)
    evidence_span_ids: list[str] = Field(min_length=1)


class LiteratureComparisonMatrix(StrictModel):
    schema_version: int = 2
    study_id: str
    mode: LiteratureMode
    rows: list[LiteratureComparisonRow]
    created_at: str = Field(default_factory=utc_now)


class ContradictionRecord(StrictModel):
    contradiction_id: str
    question: str
    positions: dict[str, list[str]]
    evidence_span_ids: list[str] = Field(min_length=2)
    unresolved: bool = True


class ContradictionMap(StrictModel):
    study_id: str
    records: list[ContradictionRecord]


class NoveltyGap(StrictModel):
    gap_id: str
    statement: str
    bounded_search_scope: str
    supporting_paper_ids: list[str]
    evidence_span_ids: list[str]
    certainty: Literal["candidate", "supported_within_boundary", "disconfirmed"]


class NoveltyGapMap(StrictModel):
    study_id: str
    gaps: list[NoveltyGap]


class RelatedWorkSectionPlan(StrictModel):
    section_id: str
    method_family_or_problem: str
    comparison_row_ids: list[str] = Field(min_length=1)
    evidence_span_ids: list[str] = Field(min_length=1)
    required_questions: list[str] = Field(
        default_factory=lambda: [
            "What problem does this family address?",
            "Which methods are commonly used?",
            "Which data and evaluations are used?",
            "What findings and limitations are known?",
            "How is the current study specifically different?",
        ]
    )


class RelatedWorkOutline(StrictModel):
    study_id: str
    mode: LiteratureMode
    sections: list[RelatedWorkSectionPlan]


class LiteratureEvidenceGap(StrictModel):
    gap_id: str
    paper_id: str | None = None
    requested_field: str
    reason: str
    required_action: str


class SystematicReviewProtocol(StrictModel):
    protocol_id: str
    databases: list[str] = Field(min_length=1)
    search_date: str
    search_strings: dict[str, str]
    inclusion_criteria: list[str] = Field(min_length=1)
    exclusion_criteria: list[str] = Field(min_length=1)
    deduplication_rule: str
    title_abstract_screening: str
    full_text_screening: str
    exclusion_reason_codes: list[str] = Field(min_length=1)
    quality_appraisal: str
    owner_gate_status: Literal["pending", "approved", "rejected"]


class LiteratureSynthesisBundle(StrictModel):
    schema_version: int = 2
    study_id: str
    mode: LiteratureMode
    contribution_cards: list[PaperContributionCard]
    evidence_spans: list[EvidenceSpan]
    comparison_matrix: LiteratureComparisonMatrix | None = None
    contradiction_map: ContradictionMap | None = None
    novelty_gap_map: NoveltyGapMap | None = None
    related_work_outline: RelatedWorkOutline | None = None
    evidence_gaps: list[LiteratureEvidenceGap] = Field(default_factory=list)
    systematic_review_protocol: SystematicReviewProtocol | None = None
    submission_ready_related_work: bool
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def enforce_mode_authority(self) -> "LiteratureSynthesisBundle":
        spans = {item.evidence_span_id: item for item in self.evidence_spans}
        cards = {item.paper_id: item for item in self.contribution_cards}
        referenced = {
            span_id
            for card in self.contribution_cards
            for span_id in card.evidence_span_ids
        }
        if not referenced <= set(spans):
            raise ValueError("Contribution Card references an unknown EvidenceSpan")
        if any(spans[span_id].paper_id != card.paper_id for card in self.contribution_cards for span_id in card.evidence_span_ids):
            raise ValueError("Contribution Card evidence belongs to another paper")
        if self.mode == "metadata_context" and self.submission_ready_related_work:
            raise ValueError("metadata-only context cannot pass the formal Related Work gate")
        if self.mode == "narrative_related_work":
            if self.comparison_matrix is None or self.related_work_outline is None:
                raise ValueError("narrative Related Work requires a comparison matrix and outline")
            substantive_cards = [
                card
                for card in cards.values()
                if card.full_text_status in {"abstract_only", "full_text_frozen"}
            ]
            if self.submission_ready_related_work and not substantive_cards:
                raise ValueError("submission-ready Related Work requires abstract/full-text evidence")
        if self.mode == "systematic_review":
            protocol = self.systematic_review_protocol
            if protocol is None or protocol.owner_gate_status != "approved":
                raise ValueError("systematic review mode requires an approved search protocol")
        return self


class LiteratureToolAdapter(StrictModel):
    adapter_id: str
    tool_name: str
    pinned_version: str
    license: str
    execution: Literal["retrieval_gateway_provider", "independent_cli", "local_service"]
    supported_modes: list[LiteratureMode]
    capabilities: list[str]
    product_code_embedded: Literal[False] = False
    instruction_authority: Literal[False] = False
    status: Literal["connected", "adapter_ready", "not_installed"]


LITERATURE_TOOL_ADAPTERS: dict[str, LiteratureToolAdapter] = {
    "openalex_pyalex": LiteratureToolAdapter(
        adapter_id="openalex_pyalex",
        tool_name="OpenAlex / PyAlex",
        pinned_version="paper-search-mcp==0.1.4 gateway",
        license="OpenAlex CC0 data; PyAlex MIT",
        execution="retrieval_gateway_provider",
        supported_modes=["metadata_context", "narrative_related_work", "bibliometric_map"],
        capabilities=["literature search", "similar works", "citation expansion", "metadata and open-access location"],
        status="connected",
    ),
    "grobid": LiteratureToolAdapter(
        adapter_id="grobid",
        tool_name="GROBID",
        pinned_version="0.8.2",
        license="Apache-2.0",
        execution="local_service",
        supported_modes=["narrative_related_work", "systematic_review"],
        capabilities=["PDF to TEI", "sections and references", "citation context", "figure and table captions"],
        status="not_installed",
    ),
    "paperqa2": LiteratureToolAdapter(
        adapter_id="paperqa2",
        tool_name="PaperQA2",
        pinned_version="2026.3.18",
        license="MIT",
        execution="local_service",
        supported_modes=["narrative_related_work", "systematic_review"],
        capabilities=["full-text index", "EvidenceSpan extraction", "batch structured questions", "abstention"],
        status="connected",
    ),
    "asreview": LiteratureToolAdapter(
        adapter_id="asreview",
        tool_name="ASReview",
        pinned_version="2.x adapter contract",
        license="Apache-2.0",
        execution="independent_cli",
        supported_modes=["systematic_review"],
        capabilities=["title and abstract screening", "active learning", "human label preservation"],
        status="adapter_ready",
    ),
    "bibliometrix": LiteratureToolAdapter(
        adapter_id="bibliometrix",
        tool_name="bibliometrix",
        pinned_version="4.x adapter contract",
        license="GPL-3.0",
        execution="independent_cli",
        supported_modes=["bibliometric_map"],
        capabilities=["co-citation", "bibliographic coupling", "co-word", "thematic evolution", "collaboration network"],
        status="adapter_ready",
    ),
    "manubot_resolver": LiteratureToolAdapter(
        adapter_id="manubot_resolver",
        tool_name="Manubot-style citation metadata resolver",
        pinned_version="typed-adapter-v1",
        license="BSD-2-Clause compatible adapter; upstream metadata licenses preserved",
        execution="independent_cli",
        supported_modes=["metadata_context", "narrative_related_work", "systematic_review"],
        capabilities=["DOI PMID arXiv resolution", "bibliography consistency"],
        status="adapter_ready",
    ),
}


def validate_literature_synthesis(bundle: LiteratureSynthesisBundle) -> list[LiteratureEvidenceGap]:
    gaps = list(bundle.evidence_gaps)
    spans = {item.evidence_span_id for item in bundle.evidence_spans}
    for card in bundle.contribution_cards:
        substantive = {
            "research_question": card.research_question,
            "intervention_or_method": card.intervention_or_method,
            "evaluation_design": card.evaluation_design,
            "major_results": card.major_results,
            "limitations": card.limitations,
        }
        if any(substantive.values()) and not set(card.evidence_span_ids) <= spans:
            gaps.append(
                LiteratureEvidenceGap(
                    gap_id=f"lit-gap-{re.sub(r'[^a-z0-9]+', '-', card.paper_id.casefold()).strip('-')}-evidence",
                    paper_id=card.paper_id,
                    requested_field="substantive contribution fields",
                    reason="one or more substantive claims lack a frozen EvidenceSpan",
                    required_action="retrieve and freeze abstract/full text, then rerun typed extraction",
                )
            )
    return gaps


__all__ = [
    "ContradictionMap",
    "ContradictionRecord",
    "EvidenceSpan",
    "LITERATURE_TOOL_ADAPTERS",
    "LiteratureComparisonMatrix",
    "LiteratureComparisonRow",
    "LiteratureEvidenceGap",
    "LiteratureMode",
    "LiteratureSynthesisBundle",
    "LiteratureToolAdapter",
    "NoveltyGap",
    "NoveltyGapMap",
    "PaperContributionCard",
    "RelatedWorkOutline",
    "RelatedWorkSectionPlan",
    "SystematicReviewProtocol",
    "validate_literature_synthesis",
]
