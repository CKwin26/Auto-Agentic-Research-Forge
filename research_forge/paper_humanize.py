from __future__ import annotations

"""Evidence-preserving academic humanization and deterministic integrity checks."""

from collections import Counter
import hashlib
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?")
_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]{1,79})\]")
_FIGURE_RE = re.compile(r"\b(?:Figure|Fig\.?|Table)\s+([A-Za-z0-9.-]+)")
_EM_DASH_RE = re.compile("\u2014")
_CAUSAL_LANGUAGE_RE = re.compile(
    r"\b(?:causes?|caused by|leads? to|results? in|drives?|because of)\b",
    re.IGNORECASE,
)
_ABSOLUTE_LANGUAGE_RE = re.compile(
    r"\b(?:universal(?:ly)?|always|never|"
    r"completely\s+(?:solves?|eliminates?|prevents?|removes?|guarantees?)|"
    r"fully solves?|guarantees?)\b",
    re.IGNORECASE,
)
_AI_TELL_PATTERNS: dict[str, re.Pattern[str]] = {
    "empty_emphasis": re.compile(
        r"\b(?:it is worth noting that|it should be emphasized that|notably|importantly)\b",
        re.IGNORECASE,
    ),
    "significance_hype": re.compile(
        r"\b(?:paves the way|sheds light on|of paramount importance|revolutioni[sz]e)\b",
        re.IGNORECASE,
    ),
    "generic_ai_vocabulary": re.compile(
        r"\b(?:delve|intricate|tapestry|testament|pivotal|showcase|seamless|realm)\b",
        re.IGNORECASE,
    ),
    "formulaic_opener": re.compile(
        r"\b(?:in recent years|with the rapid development of|despite recent advances)\b",
        re.IGNORECASE,
    ),
}


class AcademicSemanticSnapshot(StrictModel):
    """Structured scientific meaning that prose editing must not change."""

    schema_version: int = 1
    claim_ids: list[str]
    evidence_ids: list[str]
    claim_relationships: dict[str, Literal[
        "descriptive", "associational", "comparative", "causal", "limitation"
    ]]
    scope_qualifiers: dict[str, list[str]] = Field(default_factory=dict)
    uncertainty_levels: dict[
        str, Literal["confirmed", "estimated", "suggestive", "uncertain", "abstained"]
    ] = Field(default_factory=dict)
    result_roles: dict[str, Literal["primary", "secondary", "negative", "safety", "limitation"]] = Field(
        default_factory=dict
    )
    confirmatory_status: str
    evidence_maturity: str
    definitions: dict[str, str] = Field(default_factory=dict)
    number_multiset: dict[str, int] = Field(default_factory=dict)
    citation_multiset: dict[str, int] = Field(default_factory=dict)
    figure_reference_multiset: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "AcademicSemanticSnapshot":
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("claim IDs in semantic snapshot must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence IDs in semantic snapshot must be unique")
        if set(self.claim_relationships) != set(self.claim_ids):
            raise ValueError("every claim must have a frozen relationship type")
        return self


class HumanizationPlan(StrictModel):
    schema_version: int = 1
    plan_id: str = Field(pattern=r"^humanize-[a-z0-9-]{2,100}$")
    study_id: str
    source_artifact_id: str
    narrative_contract_id: str
    voice_profile_id: str
    level: Literal["copy_edit", "authorial_voice", "narrative_realization"]
    sections: list[str]
    operations: list[
        Literal[
            "grammar",
            "spelling",
            "punctuation",
            "conciseness",
            "remove_ai_tells",
            "sentence_rhythm",
            "terminology",
            "paragraph_logic",
            "calibrate_claim_verbs",
            "neutral_limitation_framing",
            "reorder_within_narrative_contract",
        ]
    ]
    prohibited_operations: list[str] = Field(
        default_factory=lambda: [
            "evade_ai_detection",
            "random_synonym_substitution",
            "intentional_grammar_errors",
            "invent_personal_experience",
            "add_claims",
            "add_evidence",
            "add_citations",
            "change_results",
        ]
    )
    created_at: str = Field(default_factory=utc_now)


class HumanizationSectionDiff(StrictModel):
    section_key: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    humanized_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    changed: bool
    operation_summary: list[str]
    author_decision: Literal["pending", "approved", "rejected", "needs_revision"] = (
        "pending"
    )


class HumanizationTrace(StrictModel):
    schema_version: int = 1
    trace_id: str = Field(pattern=r"^humanization-trace-[a-z0-9-]{2,100}$")
    plan_id: str
    source_artifact_id: str
    output_artifact_id: str
    section_diffs: list[HumanizationSectionDiff]
    model_id: str | None = None
    prompt_version: str
    created_at: str = Field(default_factory=utc_now)


class HumanizationIntegrityReport(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    checks: dict[str, bool]
    violations: list[str]
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    detected_ai_tells: dict[str, int] = Field(default_factory=dict)
    em_dash_count: int = 0


def snapshot_semantics(
    *,
    prose: str,
    claim_ids: list[str],
    evidence_ids: list[str],
    claim_relationships: dict[str, str],
    scope_qualifiers: dict[str, list[str]] | None = None,
    uncertainty_levels: dict[str, str] | None = None,
    result_roles: dict[str, str] | None = None,
    confirmatory_status: str,
    evidence_maturity: str,
    definitions: dict[str, str] | None = None,
) -> AcademicSemanticSnapshot:
    return AcademicSemanticSnapshot.model_validate(
        {
            "claim_ids": sorted(set(claim_ids)),
            "evidence_ids": sorted(set(evidence_ids)),
            "claim_relationships": claim_relationships,
            "scope_qualifiers": scope_qualifiers or {},
            "uncertainty_levels": uncertainty_levels or {},
            "result_roles": result_roles or {},
            "confirmatory_status": confirmatory_status,
            "evidence_maturity": evidence_maturity,
            "definitions": definitions or {},
            "number_multiset": dict(Counter(_NUMBER_RE.findall(prose))),
            "citation_multiset": dict(Counter(_CITATION_RE.findall(prose))),
            "figure_reference_multiset": dict(Counter(_FIGURE_RE.findall(prose))),
        }
    )


def _snapshot_sha256(snapshot: AcademicSemanticSnapshot) -> str:
    payload = json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def audit_humanization_integrity(
    source: AcademicSemanticSnapshot,
    output: AcademicSemanticSnapshot,
    *,
    source_prose: str = "",
    humanized_prose: str = "",
) -> HumanizationIntegrityReport:
    # Humanization may legitimately remove a repeated sentence while retaining
    # the same numeric fact.  Protect the set of numeric values, rather than the
    # prose-level occurrence count, so repetition is not mistaken for a
    # scientific change.  A genuinely added, removed, or changed value still
    # fails this gate.
    source_numbers = set(source.number_multiset)
    output_numbers = set(output.number_multiset)
    checks = {
        "claim_ids_preserved": source.claim_ids == output.claim_ids,
        "evidence_ids_preserved": source.evidence_ids == output.evidence_ids,
        "claim_relationships_preserved": (
            source.claim_relationships == output.claim_relationships
        ),
        "scope_qualifiers_preserved": source.scope_qualifiers == output.scope_qualifiers,
        "uncertainty_levels_preserved": (
            source.uncertainty_levels == output.uncertainty_levels
        ),
        "result_roles_preserved": source.result_roles == output.result_roles,
        "confirmatory_status_preserved": (
            source.confirmatory_status == output.confirmatory_status
        ),
        "evidence_maturity_preserved": (
            source.evidence_maturity == output.evidence_maturity
        ),
        "definitions_preserved": source.definitions == output.definitions,
        "numbers_preserved": source_numbers == output_numbers,
        "citations_preserved": source.citation_multiset == output.citation_multiset,
        "figure_references_preserved": (
            source.figure_reference_multiset == output.figure_reference_multiset
        ),
        "no_new_causal_language": not (
            all(
                relation != "causal"
                for relation in source.claim_relationships.values()
            )
            and len(_CAUSAL_LANGUAGE_RE.findall(humanized_prose))
            > len(_CAUSAL_LANGUAGE_RE.findall(source_prose))
        ),
        "no_new_absolute_language": (
            len(_ABSOLUTE_LANGUAGE_RE.findall(humanized_prose))
            <= len(_ABSOLUTE_LANGUAGE_RE.findall(source_prose))
        ),
        "scope_qualifier_text_preserved": all(
            qualifier in humanized_prose
            for qualifiers in source.scope_qualifiers.values()
            for qualifier in qualifiers
            if qualifier in source_prose
        ),
    }
    violation_messages = {
        "numbers_preserved": (
            "numeric values changed"
            f" (missing: {sorted(source_numbers - output_numbers) or 'none'};"
            f" added: {sorted(output_numbers - source_numbers) or 'none'})"
        ),
        "citations_preserved": "citation references changed",
        "figure_references_preserved": "figure or table references changed",
        "no_new_causal_language": "new causal language was introduced",
        "no_new_absolute_language": "new absolute language was introduced",
        "scope_qualifier_text_preserved": "scope qualifier text was removed",
    }
    violations = [
        violation_messages.get(name, f"{name.replace('_', ' ')} check failed")
        for name, passed in checks.items()
        if not passed
    ]
    tells = {
        name: len(pattern.findall(humanized_prose))
        for name, pattern in _AI_TELL_PATTERNS.items()
    }
    return HumanizationIntegrityReport(
        passed=all(checks.values()),
        checks=checks,
        violations=violations,
        source_snapshot_sha256=_snapshot_sha256(source),
        output_snapshot_sha256=_snapshot_sha256(output),
        detected_ai_tells=tells,
        em_dash_count=len(_EM_DASH_RE.findall(humanized_prose)),
    )


__all__ = [
    "AcademicSemanticSnapshot",
    "HumanizationIntegrityReport",
    "HumanizationPlan",
    "HumanizationSectionDiff",
    "HumanizationTrace",
    "audit_humanization_integrity",
    "snapshot_semantics",
]
