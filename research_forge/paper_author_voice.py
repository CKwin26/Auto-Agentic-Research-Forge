from __future__ import annotations

"""Author-owned academic voice profiles and approval records."""

from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


class SentenceLengthDistribution(StrictModel):
    short: float = Field(ge=0, le=1)
    medium: float = Field(ge=0, le=1)
    long: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def sums_to_one(self) -> "SentenceLengthDistribution":
        if abs((self.short + self.medium + self.long) - 1.0) > 0.001:
            raise ValueError("sentence length distribution must sum to 1")
        return self


class AuthorVoiceProfile(StrictModel):
    schema_version: int = 1
    profile_id: str = Field(pattern=r"^voice-[a-z0-9-]{2,100}$")
    version: int = Field(default=1, ge=1)
    language: Literal["en", "zh"]
    source_types: list[
        Literal[
            "prior_paper",
            "author_email",
            "author_report",
            "research_statement",
            "style_questionnaire",
            "pairwise_choice",
        ]
    ]
    source_artifact_ids: list[str] = Field(default_factory=list)
    directness: float = Field(default=0.7, ge=0, le=1)
    technical_density: float = Field(default=0.7, ge=0, le=1)
    sentence_length_distribution: SentenceLengthDistribution = Field(
        default_factory=lambda: SentenceLengthDistribution(
            short=0.25, medium=0.55, long=0.20
        )
    )
    first_person_policy: Literal["none", "limited_we", "standard_we"] = "limited_we"
    hedging_level: Literal["low", "moderate", "high"] = "moderate"
    paragraph_style: Literal[
        "claim_then_evidence", "evidence_then_interpretation", "mixed"
    ] = "claim_then_evidence"
    transition_style: Literal["implicit_or_short", "explicit", "mixed"] = (
        "implicit_or_short"
    )
    preferred_terms: dict[str, str] = Field(default_factory=dict)
    banned_phrases: list[str] = Field(
        default_factory=lambda: [
            "it is worth noting that",
            "in today's rapidly evolving",
            "plays a crucial role",
            "paves the way",
            "sheds light on",
        ]
    )
    notation_preferences: dict[str, str] = Field(default_factory=dict)
    author_approved_example_artifact_ids: list[str] = Field(default_factory=list)
    frozen: bool = False
    approved_by: str | None = None
    approved_at: str | None = None

    @model_validator(mode="after")
    def frozen_profile_is_approved(self) -> "AuthorVoiceProfile":
        if self.frozen and (not self.approved_by or not self.approved_at):
            raise ValueError("a frozen author voice profile requires owner approval")
        return self


class AuthorVoiceApproval(StrictModel):
    schema_version: int = 1
    approval_id: str = Field(pattern=r"^voice-approval-[a-z0-9-]{2,100}$")
    study_id: str
    voice_profile_id: str
    humanized_artifact_id: str
    status: Literal["approved", "rejected"]
    decided_by: str
    section_decisions: dict[str, Literal["approved", "rejected", "needs_revision"]]
    reason: str | None = None
    decided_at: str = Field(default_factory=utc_now)


__all__ = [
    "AuthorVoiceApproval",
    "AuthorVoiceProfile",
    "SentenceLengthDistribution",
]
