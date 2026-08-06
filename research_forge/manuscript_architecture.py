from __future__ import annotations

"""Nature-inspired manuscript architecture checks owned by Research Forge.

The module does not import or execute third-party writing skills.  It turns
three useful editorial principles into auditable platform contracts:

* reverse-outline a manuscript before the final prose pass;
* distinguish bibliography hygiene from claim--source support; and
* refuse to polish a draft whose evidence, literature, or visual inputs have
  changed since drafting.
"""

import hashlib
import json
import re
from typing import Any

from pydantic import Field

from .models import StrictModel, utc_now


_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]{1,79})\]")
_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
_SNAPSHOT_VOLATILE_FIELDS = {
    "added_at",
    "created_at",
    "updated_at",
    "audited_at",
    "built_at",
    "generated_at",
}


def _snapshot_value(value: Any) -> Any:
    """Remove reconstruction-time metadata from a scientific source view.

    Some reader-facing adapters reconstruct typed objects on each call. Their
    automatically assigned timestamps are not source changes and must not make
    a manuscript appear stale. Scientific metadata, identifiers, hashes, and
    content remain in the snapshot.
    """

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _snapshot_value(item)
            for key, item in value.items()
            if str(key) not in _SNAPSHOT_VOLATILE_FIELDS
        }
    if isinstance(value, list):
        return [_snapshot_value(item) for item in value]
    if isinstance(value, tuple):
        return [_snapshot_value(item) for item in value]
    return value


def _canonical_sha256(value: Any) -> str:
    value = _snapshot_value(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ManuscriptSourceSnapshot(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    component_sha256: dict[str, str]
    combined_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SourceFreshnessAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    changed_components: list[str]
    drafted_combined_sha256: str
    current_combined_sha256: str
    legacy_snapshot_migrated: bool = False


class ReverseOutlineParagraph(StrictModel):
    paragraph_index: int = Field(ge=1)
    rhetorical_job: str
    opening_sentence: str
    citation_keys: list[str]
    numeric_token_count: int = Field(ge=0)
    visual_callout_present: bool = False


class ReverseOutlineSection(StrictModel):
    section_key: str
    thesis: str
    expected_moves: list[str]
    paragraphs: list[ReverseOutlineParagraph]


class ReverseOutlineReport(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    passed: bool
    sections: list[ReverseOutlineSection]
    violations: list[str]


class CitationSupportItem(StrictModel):
    source_id: str
    cited_in_sections: list[str]
    binding_claim_ids: list[str]
    status: str
    reason: str


class ManuscriptCitationAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    bibliography_hygiene_passed: bool
    claim_source_support_passed: bool
    cited_source_ids: list[str]
    unknown_source_ids: list[str]
    uncited_registered_source_ids: list[str]
    support_items: list[CitationSupportItem]
    rejected_or_unverified_count: int = Field(ge=0)


def build_manuscript_source_snapshot(
    components: dict[str, Any],
) -> ManuscriptSourceSnapshot:
    """Freeze the exact upstream views used by a manuscript operation."""

    component_hashes = {
        key: _canonical_sha256(value)
        for key, value in sorted(components.items())
    }
    return ManuscriptSourceSnapshot(
        component_sha256=component_hashes,
        combined_sha256=_canonical_sha256(component_hashes),
    )


def audit_source_freshness(
    drafted: ManuscriptSourceSnapshot,
    current: ManuscriptSourceSnapshot,
    *,
    legacy_snapshot_migrated: bool = False,
    compatible_reconstructed_components: set[str] | None = None,
) -> SourceFreshnessAudit:
    keys = sorted(set(drafted.component_sha256) | set(current.component_sha256))
    changed = [
        key
        for key in keys
        if drafted.component_sha256.get(key) != current.component_sha256.get(key)
    ]
    if legacy_snapshot_migrated and compatible_reconstructed_components:
        changed = [
            key
            for key in changed
            if key not in compatible_reconstructed_components
        ]
    return SourceFreshnessAudit(
        passed=not changed,
        changed_components=changed,
        drafted_combined_sha256=drafted.combined_sha256,
        current_combined_sha256=current.combined_sha256,
        legacy_snapshot_migrated=legacy_snapshot_migrated,
    )


def _paragraphs(value: str) -> list[str]:
    blocks = []
    for raw in re.split(r"\n\s*\n", value.strip()):
        text = re.sub(r"(?m)^#{1,6}\s+.*$", "", raw).strip()
        if text and not re.fullmatch(r"\[(?:FIGURE|TABLE):[^]]+\]", text):
            blocks.append(re.sub(r"\s+", " ", text))
    return blocks


def build_reverse_outline(
    sections: dict[str, str],
    narrative_contract: Any,
) -> ReverseOutlineReport:
    """Expose the job of every paragraph before final manuscript polishing.

    This is an editorial map, not a scientific judgement.  It intentionally
    records paragraph openings rather than generating new summaries that could
    introduce claims.
    """

    reports: list[ReverseOutlineSection] = []
    violations: list[str] = []
    for spec in narrative_contract.sections:
        text = str(sections.get(spec.key) or "")
        paragraphs = _paragraphs(text)
        if not paragraphs:
            violations.append(f"{spec.key} has no substantive paragraph")
            continue
        entries: list[ReverseOutlineParagraph] = []
        for index, paragraph in enumerate(paragraphs):
            sentences = [item.strip() for item in _SENTENCE_RE.split(paragraph) if item.strip()]
            opening = (sentences[0] if sentences else paragraph)[:500]
            job = (
                spec.required_moves[index]
                if index < len(spec.required_moves)
                else "supporting_detail"
            )
            entries.append(
                ReverseOutlineParagraph(
                    paragraph_index=index + 1,
                    rhetorical_job=job,
                    opening_sentence=opening,
                    citation_keys=list(dict.fromkeys(_CITATION_RE.findall(paragraph))),
                    numeric_token_count=len(
                        re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?%?", paragraph)
                    ),
                    visual_callout_present=bool(
                        re.search(r"\[(?:FIGURE|TABLE):[^]]+\]", paragraph)
                    ),
                )
            )
        reports.append(
            ReverseOutlineSection(
                section_key=spec.key,
                thesis=entries[0].opening_sentence,
                expected_moves=list(spec.required_moves),
                paragraphs=entries,
            )
        )
    return ReverseOutlineReport(
        passed=not violations,
        sections=reports,
        violations=violations,
    )


def audit_manuscript_citations(
    *,
    sections: dict[str, str],
    evidence_claim_map: Any,
    literature_sources: list[Any],
) -> ManuscriptCitationAudit:
    """Keep citation parsing and semantic support as separate verdicts.

    A verified registry entry proves identity and metadata.  Direct support is
    credited only when the Evidence--Claim Map contains a *bound* literature
    pointer for that source.  ``context_only`` screening notes remain useful
    discovery context but cannot silently become claim-level verification.
    """

    cited_in: dict[str, set[str]] = {}
    for section, prose in sections.items():
        for source_id in _CITATION_RE.findall(str(prose or "")):
            cited_in.setdefault(source_id, set()).add(section)

    registered = {
        str(getattr(source, "source_id", None) or source.get("source_id"))
        for source in literature_sources
    }
    registered.discard("None")
    cited = sorted(cited_in)
    unknown = sorted(set(cited) - registered)
    items: list[CitationSupportItem] = []
    for source_id in sorted(set(cited) & registered):
        bound_claims: list[str] = []
        contextual_claims: list[str] = []
        for binding in evidence_claim_map.bindings:
            if not any(
                pointer.source_id == source_id
                and pointer.evidence_type == "verified_literature"
                for pointer in binding.evidence
            ):
                continue
            if binding.evidence_status == "bound":
                bound_claims.append(binding.claim_id)
            else:
                contextual_claims.append(binding.claim_id)
        if bound_claims:
            status = "supported"
            reason = "a bound literature pointer supports a registered claim"
            claim_ids = bound_claims
        elif contextual_claims:
            status = "unverified"
            reason = (
                "the source is registered but only context-screened; direct "
                "claim--source support has not been verified"
            )
            claim_ids = contextual_claims
        else:
            status = "rejected"
            reason = "the cited source has no claim binding"
            claim_ids = []
        items.append(
            CitationSupportItem(
                source_id=source_id,
                cited_in_sections=sorted(cited_in[source_id]),
                binding_claim_ids=sorted(claim_ids),
                status=status,
                reason=reason,
            )
        )
    rejected = sum(item.status != "supported" for item in items) + len(unknown)
    return ManuscriptCitationAudit(
        bibliography_hygiene_passed=not unknown,
        claim_source_support_passed=not unknown and rejected == 0,
        cited_source_ids=cited,
        unknown_source_ids=unknown,
        uncited_registered_source_ids=sorted(registered - set(cited)),
        support_items=items,
        rejected_or_unverified_count=rejected,
    )


__all__ = [
    "ManuscriptCitationAudit",
    "ManuscriptSourceSnapshot",
    "ReverseOutlineReport",
    "SourceFreshnessAudit",
    "audit_manuscript_citations",
    "audit_source_freshness",
    "build_manuscript_source_snapshot",
    "build_reverse_outline",
]
