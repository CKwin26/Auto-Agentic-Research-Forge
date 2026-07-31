from __future__ import annotations

"""Build venue-aware AI-use disclosures from immutable execution logs."""

from typing import Literal

from pydantic import Field

from .models import StrictModel, utc_now
from .paper_venue_policy import VenuePolicyProfile


class AIUseEvent(StrictModel):
    event_id: str
    model_id: str
    provider: str
    step_type: str
    purpose: Literal[
        "claim_extraction",
        "outline_generation",
        "draft_generation",
        "scientific_review",
        "copy_edit",
        "authorial_voice",
        "visual_explanation",
    ]
    generated_scientific_content: bool
    human_reviewed: bool
    output_artifact_ids: list[str] = Field(default_factory=list)


class AIUseDisclosure(StrictModel):
    schema_version: int = 1
    study_id: str
    venue_policy_id: str
    disclosure_required: bool
    events: list[AIUseEvent]
    disclosure_text: str
    human_accountability_statement: str
    generated_at: str = Field(default_factory=utc_now)


def build_ai_use_disclosure(
    *,
    study_id: str,
    venue_policy: VenuePolicyProfile,
    events: list[AIUseEvent],
    responsible_author: str,
) -> AIUseDisclosure:
    substantive = any(
        event.generated_scientific_content or event.purpose != "copy_edit"
        for event in events
    )
    disclosure_required = (
        venue_policy.ai_disclosure_required_for_generation and substantive
    ) or (
        venue_policy.ai_copy_edit_disclosure == "required"
        and any(event.purpose == "copy_edit" for event in events)
    )
    grouped = sorted(
        {
            f"{event.model_id} ({event.purpose.replace('_', ' ')})"
            for event in events
        }
    )
    disclosure_text = (
        "AI-assisted tools were used for "
        + ", ".join(grouped)
        + ". Their outputs were reviewed against frozen claims, evidence, numbers, "
        "citations, and venue requirements."
        if events
        else "No AI-assisted writing or review event was recorded for this manuscript."
    )
    accountability = (
        f"{responsible_author} reviewed the final manuscript and remains responsible "
        "for its accuracy, integrity, originality, and compliance."
    )
    return AIUseDisclosure(
        study_id=study_id,
        venue_policy_id=venue_policy.profile_id,
        disclosure_required=disclosure_required,
        events=events,
        disclosure_text=disclosure_text,
        human_accountability_statement=accountability,
    )


__all__ = ["AIUseDisclosure", "AIUseEvent", "build_ai_use_disclosure"]
