from __future__ import annotations

"""Contribution candidates and evidence-bound central contribution selection."""

from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


class ContributionCandidate(StrictModel):
    schema_version: int = 1
    contribution_id: str = Field(pattern=r"^contribution-[a-z0-9-]{2,100}$")
    contribution_type: Literal[
        "new_capability",
        "new_problem",
        "new_method",
        "new_mechanism",
        "new_perspective",
        "broader_applicability",
        "lower_cost",
        "higher_efficiency",
        "better_scalability",
        "meaningful_tradeoff",
        "counterintuitive_negative_result",
    ]
    problem: str = Field(min_length=10, max_length=3000)
    proposed_value: str = Field(min_length=10, max_length=3000)
    supporting_claim_ids: list[str] = Field(min_length=1)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    novelty_basis: list[str] = Field(default_factory=list)
    practical_value: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    publication_strength: Literal["strong", "moderate", "weak"]
    eligible_as_main_story: bool
    disqualifying_reasons: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def eligibility_is_explainable(self) -> "ContributionCandidate":
        if not self.eligible_as_main_story and not self.disqualifying_reasons:
            raise ValueError("an ineligible contribution requires a reason")
        if len(self.supporting_claim_ids) != len(set(self.supporting_claim_ids)):
            raise ValueError("supporting claim IDs must be unique")
        return self


def rank_contribution_candidates(
    candidates: list[ContributionCandidate],
) -> list[ContributionCandidate]:
    strength = {"strong": 3, "moderate": 2, "weak": 1}
    return sorted(
        candidates,
        key=lambda item: (
            not item.eligible_as_main_story,
            -strength[item.publication_strength],
            -len(item.supporting_claim_ids),
            item.contribution_id,
        ),
    )


def select_central_contribution(
    candidates: list[ContributionCandidate],
    contribution_id: str,
    *,
    allowed_claim_ids: set[str],
) -> ContributionCandidate:
    selected = next(
        (item for item in candidates if item.contribution_id == contribution_id),
        None,
    )
    if selected is None:
        raise ValueError("selected contribution does not exist")
    if not selected.eligible_as_main_story:
        raise ValueError("selected contribution is not eligible as the main story")
    unknown = sorted(set(selected.supporting_claim_ids) - allowed_claim_ids)
    if unknown:
        raise ValueError(
            "selected contribution references claims outside the frozen envelope: "
            + ", ".join(unknown)
        )
    return selected


__all__ = [
    "ContributionCandidate",
    "rank_contribution_candidates",
    "select_central_contribution",
]
