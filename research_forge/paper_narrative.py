from __future__ import annotations

"""Frozen publication-story contracts bounded by Stage 3 claim authority."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .paper_contribution import ContributionCandidate
from .paper_reporting_compliance import MandatoryReportingRegister


class ComparatorDecision(StrictModel):
    comparator_id: str
    comparator_name: str
    supports_claim_ids: list[str]
    necessity: Literal["required", "supporting", "not_relevant"]
    placement: Literal["main_text", "supplement", "excluded"]
    frozen_in_research_contract: bool
    weak_baseline_risk: bool = False
    rationale: str = Field(min_length=10, max_length=2500)
    exclusion_reason: str | None = None

    @model_validator(mode="after")
    def exclusion_is_explained(self) -> "ComparatorDecision":
        if self.placement == "excluded" and not self.exclusion_reason:
            raise ValueError("excluded comparators require a reason")
        if self.necessity == "required" and self.placement == "excluded":
            raise ValueError("a required comparator cannot be excluded")
        if self.weak_baseline_risk and self.placement == "main_text":
            raise ValueError(
                "a weak-baseline-risk comparator cannot be the only main-text comparator"
            )
        return self


class ReaderMemoryContract(StrictModel):
    schema_version: int = 1
    memory_point: str = Field(min_length=20, max_length=1200)
    supporting_claim_ids: list[str] = Field(min_length=1)
    scope_conditions: list[str] = Field(min_length=1)
    prohibited_generalizations: list[str] = Field(default_factory=list)


class RhetoricalMoveContract(StrictModel):
    schema_version: int = 1
    problem_claim_ids: list[str]
    gap_source_ids: list[str]
    resolution_claim_ids: list[str]
    result_claim_ids: list[str]
    value_claim_ids: list[str]


class PublicationNarrativeContract(StrictModel):
    schema_version: int = 1
    study_id: str
    version: int = Field(default=1, ge=1)
    status: Literal["draft", "frozen", "superseded"] = "draft"
    selected_contribution_id: str
    central_thesis: str = Field(min_length=20, max_length=3000)
    reader_problem: str = Field(min_length=20, max_length=3000)
    existing_gap: str = Field(min_length=20, max_length=3000)
    proposed_resolution: str = Field(min_length=20, max_length=3000)
    primary_advantage: str = Field(min_length=10, max_length=2000)
    advantage_conditions: list[str]
    mechanism_explanation: list[str] = Field(default_factory=list)
    required_claim_ids: list[str] = Field(min_length=1)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    mandatory_negative_claim_ids: list[str] = Field(default_factory=list)
    comparator_decisions: list[ComparatorDecision] = Field(default_factory=list)
    reader_memory_point: str = Field(min_length=20, max_length=1200)
    prohibited_story_moves: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: str | None = None
    frozen_at: str | None = None
    contract_sha256: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_contract(self) -> "PublicationNarrativeContract":
        all_claims = [
            *self.required_claim_ids,
            *self.supporting_claim_ids,
            *self.mandatory_negative_claim_ids,
        ]
        if len(all_claims) != len(set(all_claims)):
            raise ValueError("narrative claim roles must not overlap")
        comparator_ids = [item.comparator_id for item in self.comparator_decisions]
        if len(comparator_ids) != len(set(comparator_ids)):
            raise ValueError("comparator IDs must be unique")
        if self.status == "frozen":
            if not self.approved_by or not self.approved_at or not self.frozen_at:
                raise ValueError("a frozen narrative requires owner approval metadata")
        return self


def validate_publication_narrative(
    contract: PublicationNarrativeContract,
    *,
    selected_contribution: ContributionCandidate,
    reporting_register: MandatoryReportingRegister,
    allowed_claim_ids: set[str],
) -> list[str]:
    violations: list[str] = []
    if contract.selected_contribution_id != selected_contribution.contribution_id:
        violations.append("narrative selects a different contribution candidate")
    narrative_claims = {
        *contract.required_claim_ids,
        *contract.supporting_claim_ids,
        *contract.mandatory_negative_claim_ids,
    }
    unknown = sorted(narrative_claims - allowed_claim_ids)
    if unknown:
        violations.append(
            "narrative references claims outside the frozen envelope: "
            + ", ".join(unknown)
        )
    missing_contribution = sorted(
        set(selected_contribution.supporting_claim_ids) - narrative_claims
    )
    if missing_contribution:
        violations.append(
            "central contribution evidence is missing from the narrative: "
            + ", ".join(missing_contribution)
        )
    missing_reporting = sorted(
        reporting_register.main_text_claim_ids() - narrative_claims
    )
    if missing_reporting:
        violations.append(
            "mandatory main-text results are absent from the narrative: "
            + ", ".join(missing_reporting)
        )
    registered_negative = {
        item.claim_id
        for item in reporting_register.items
        if item.category in {"negative", "safety", "limitation"} and item.material
    }
    missing_negative = sorted(
        registered_negative - set(contract.mandatory_negative_claim_ids)
    )
    if missing_negative:
        violations.append(
            "material negative or limitation claims are not explicitly preserved: "
            + ", ".join(missing_negative)
        )
    required_comparators = [
        item
        for item in contract.comparator_decisions
        if item.frozen_in_research_contract or item.necessity == "required"
    ]
    excluded_required = [
        item.comparator_id
        for item in required_comparators
        if item.placement == "excluded"
    ]
    if excluded_required:
        violations.append(
            "required comparators are excluded: " + ", ".join(excluded_required)
        )
    return list(dict.fromkeys(violations))


def seal_publication_narrative(
    contract: PublicationNarrativeContract,
    *,
    approved_by: str,
    approved_at: str | None = None,
) -> PublicationNarrativeContract:
    timestamp = approved_at or utc_now()
    candidate = contract.model_copy(
        update={
            "status": "frozen",
            "approved_by": approved_by,
            "approved_at": timestamp,
            "frozen_at": timestamp,
            "contract_sha256": "0" * 64,
        }
    )
    payload = candidate.model_dump(mode="json", exclude={"contract_sha256"})
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return candidate.model_copy(update={"contract_sha256": digest})


def verify_publication_narrative_seal(
    contract: PublicationNarrativeContract,
) -> bool:
    if contract.status != "frozen":
        return False
    payload = contract.model_dump(mode="json", exclude={"contract_sha256"})
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return expected == contract.contract_sha256


def compile_reader_memory_contract(
    contract: PublicationNarrativeContract,
) -> ReaderMemoryContract:
    return ReaderMemoryContract(
        memory_point=contract.reader_memory_point,
        supporting_claim_ids=[
            *contract.required_claim_ids,
            *contract.supporting_claim_ids,
        ],
        scope_conditions=contract.advantage_conditions,
        prohibited_generalizations=contract.prohibited_story_moves,
    )


__all__ = [
    "ComparatorDecision",
    "PublicationNarrativeContract",
    "ReaderMemoryContract",
    "RhetoricalMoveContract",
    "compile_reader_memory_contract",
    "seal_publication_narrative",
    "validate_publication_narrative",
    "verify_publication_narrative_seal",
]
