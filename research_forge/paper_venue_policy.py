from __future__ import annotations

"""Versioned venue and reporting policies for Stage 4 publication work."""

from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel


class VenueVisualPolicy(StrictModel):
    schema_version: int = 1
    evidence_chart_generation: Literal["deterministic_only"] = "deterministic_only"
    scientific_image_generative_editing_allowed: Literal[False] = False
    method_diagram_generation_allowed: bool = True
    graphical_abstract_generation_allowed: bool = False
    graphical_abstract_evidence_status: Literal["explanatory_only"] = (
        "explanatory_only"
    )
    vector_output_required: bool = True
    color_vision_safe_required: bool = True
    grayscale_distinguishable_required: bool = True
    alt_text_required: bool = True
    embedded_fonts_required: bool = True
    minimum_font_points: float = Field(default=7.0, ge=5.0, le=14.0)
    main_figure_budget: int = Field(default=5, ge=0, le=30)
    supplementary_figure_budget: int = Field(default=8, ge=0, le=100)


class VenuePolicyProfile(StrictModel):
    """One frozen publication target policy.

    Readiness thresholds belong to the venue/genre profile instead of the
    generic paper writer.  They are engineering admission requirements, not
    universal scientific laws.
    """

    schema_version: int = 1
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,100}$")
    version: int = Field(default=1, ge=1)
    venue_family: Literal[
        "generic_journal",
        "acm",
        "ieee",
        "springer_lncs",
        "elsevier",
        "nature_style",
    ]
    document_type: Literal["research_article", "short_report"] = "research_article"
    structure_profile_id: str
    manuscript_depth_profile: Literal["journal-article", "short-report"]
    minimum_verified_papers: int = Field(default=15, ge=0, le=500)
    minimum_numeric_evidence: int = Field(default=15, ge=0, le=10_000)
    abstract_style: Literal["unstructured", "structured"] = "unstructured"
    abstract_paragraph_count: int = Field(default=1, ge=1, le=8)
    anonymous_review: bool = False
    supplementary_material_allowed: bool = True
    ai_disclosure_required_for_generation: bool = True
    ai_copy_edit_disclosure: Literal[
        "required", "not_required", "venue_specific"
    ] = "venue_specific"
    mandatory_reporting_categories: list[
        Literal["primary", "secondary", "negative", "safety", "limitation"]
    ] = Field(
        default_factory=lambda: [
            "primary",
            "secondary",
            "negative",
            "safety",
            "limitation",
        ]
    )
    visual_policy: VenueVisualPolicy = Field(default_factory=VenueVisualPolicy)

    @model_validator(mode="after")
    def validate_abstract_contract(self) -> "VenuePolicyProfile":
        if self.abstract_style == "unstructured" and self.abstract_paragraph_count != 1:
            raise ValueError("unstructured abstracts must contain exactly one paragraph")
        if len(self.mandatory_reporting_categories) != len(
            set(self.mandatory_reporting_categories)
        ):
            raise ValueError("mandatory reporting categories must be unique")
        return self


GENERIC_JOURNAL_POLICY = VenuePolicyProfile(
    profile_id="generic-journal-v1",
    venue_family="generic_journal",
    structure_profile_id="generic-journal-article-v1",
    manuscript_depth_profile="journal-article",
)

GENERIC_SHORT_REPORT_POLICY = VenuePolicyProfile(
    profile_id="generic-short-report-v1",
    venue_family="generic_journal",
    document_type="short_report",
    structure_profile_id="generic-journal-article-v1",
    manuscript_depth_profile="short-report",
    minimum_verified_papers=8,
    minimum_numeric_evidence=8,
    visual_policy=VenueVisualPolicy(
        main_figure_budget=4,
        supplementary_figure_budget=6,
    ),
)

ACM_CONFERENCE_POLICY = VenuePolicyProfile(
    profile_id="acm-conference-v1",
    venue_family="acm",
    structure_profile_id="generic-journal-article-v1",
    manuscript_depth_profile="short-report",
    minimum_verified_papers=10,
    minimum_numeric_evidence=8,
    anonymous_review=True,
)

IEEE_CONFERENCE_POLICY = VenuePolicyProfile(
    profile_id="ieee-conference-v1",
    venue_family="ieee",
    structure_profile_id="generic-journal-article-v1",
    manuscript_depth_profile="short-report",
    minimum_verified_papers=10,
    minimum_numeric_evidence=8,
    anonymous_review=True,
)

SPRINGER_LNCS_POLICY = VenuePolicyProfile(
    profile_id="springer-lncs-v1",
    venue_family="springer_lncs",
    structure_profile_id="generic-journal-article-v1",
    manuscript_depth_profile="short-report",
    minimum_verified_papers=10,
    minimum_numeric_evidence=8,
)

ELSEVIER_JOURNAL_POLICY = VenuePolicyProfile(
    profile_id="elsevier-journal-v1",
    venue_family="elsevier",
    structure_profile_id="generic-journal-article-v1",
    manuscript_depth_profile="journal-article",
)

NATURE_STYLE_POLICY = VenuePolicyProfile(
    profile_id="nature-style-v1",
    venue_family="nature_style",
    structure_profile_id="generic-journal-article-v1",
    manuscript_depth_profile="journal-article",
    ai_copy_edit_disclosure="venue_specific",
    visual_policy=VenueVisualPolicy(
        graphical_abstract_generation_allowed=False,
        minimum_font_points=7.0,
    ),
)


VENUE_POLICIES = {
    item.profile_id: item
    for item in (
        GENERIC_JOURNAL_POLICY,
        GENERIC_SHORT_REPORT_POLICY,
        ACM_CONFERENCE_POLICY,
        IEEE_CONFERENCE_POLICY,
        SPRINGER_LNCS_POLICY,
        ELSEVIER_JOURNAL_POLICY,
        NATURE_STYLE_POLICY,
    )
}


def get_venue_policy(profile_id: str) -> VenuePolicyProfile:
    try:
        return VENUE_POLICIES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown venue policy profile: {profile_id}") from exc


__all__ = [
    "ACM_CONFERENCE_POLICY",
    "ELSEVIER_JOURNAL_POLICY",
    "GENERIC_JOURNAL_POLICY",
    "GENERIC_SHORT_REPORT_POLICY",
    "IEEE_CONFERENCE_POLICY",
    "NATURE_STYLE_POLICY",
    "SPRINGER_LNCS_POLICY",
    "VENUE_POLICIES",
    "VenuePolicyProfile",
    "VenueVisualPolicy",
    "get_venue_policy",
]
