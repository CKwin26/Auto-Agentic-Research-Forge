from __future__ import annotations

"""Audit avoidable reviewer attack surfaces without suppressing material results."""

import hashlib
import re
from typing import Literal

from pydantic import Field

from .models import StrictModel, utc_now
from .paper_narrative import PublicationNarrativeContract
from .paper_reporting_compliance import MandatoryReportingRegister


class ReviewerAttackFinding(StrictModel):
    finding_id: str = Field(pattern=r"^attack-[a-z0-9-]{2,100}$")
    severity: Literal["blocking", "major", "minor"]
    location: str
    problematic_text: str
    attack_type: Literal[
        "unsupported_absolute",
        "scope_overreach",
        "weak_comparator",
        "metric_value_mismatch",
        "mechanism_overreach",
        "negative_framing",
        "figure_text_conflict",
        "selective_examples",
        "limitation_imbalance",
        "title_overpromise",
        "mandatory_result_omission",
    ]
    claim_id: str | None = None
    evidence_status: Literal["bound", "unbound", "unknown"]
    recommended_action: str
    disposition: Literal["rewrite", "stage4_blocker", "stage3_successor"]


class ReviewerAttackSurfaceReport(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    findings: list[ReviewerAttackFinding]
    mandatory_claims_checked: list[str]
    suppression_detected: bool


_ABSOLUTE_RE = re.compile(
    r"\b(?:first|universal(?:ly)?|always|never|completely|fully solves?|"
    r"state[- ]of[- ]the[- ]art|significantly outperforms all)\b",
    re.IGNORECASE,
)
_MECHANISM_RE = re.compile(
    r"\b(?:proves? that .* causes?|establishes? the mechanism|because of this mechanism)\b",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"\b(?:serious limitation|severe degradation|failed badly|despite losing)\b",
    re.IGNORECASE,
)
_GENERAL_TITLE_RE = re.compile(
    r"\b(?:general|universal|comprehensive|complete)\b", re.IGNORECASE
)


def _finding_id_for_claim(prefix: str, claim_id: str) -> str:
    """Create a schema-safe, collision-resistant finding identifier."""

    slug = re.sub(r"[^a-z0-9-]+", "-", claim_id.casefold()).strip("-")
    digest = hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:10]
    return f"attack-{prefix}-{slug[:68]}-{digest}"


def audit_reviewer_attack_surface(
    *,
    title: str,
    sections: dict[str, str],
    reporting_register: MandatoryReportingRegister,
    presented_claim_ids: list[str],
    narrative_contract: PublicationNarrativeContract | None = None,
    claim_relationships: dict[str, str] | None = None,
) -> ReviewerAttackSurfaceReport:
    findings: list[ReviewerAttackFinding] = []
    if _GENERAL_TITLE_RE.search(title):
        findings.append(
            ReviewerAttackFinding(
                finding_id="attack-title-overpromise",
                severity="major",
                location="title",
                problematic_text=title,
                attack_type="title_overpromise",
                evidence_status="unknown",
                recommended_action="narrow the title to the frozen study scope",
                disposition="rewrite",
            )
        )
    for section, prose in sections.items():
        for index, match in enumerate(_ABSOLUTE_RE.finditer(prose), start=1):
            findings.append(
                ReviewerAttackFinding(
                    finding_id=f"attack-absolute-{section}-{index}",
                    severity="major",
                    location=section,
                    problematic_text=match.group(0),
                    attack_type="unsupported_absolute",
                    evidence_status="unknown",
                    recommended_action="replace the absolute with a scope- and evidence-bound statement",
                    disposition="rewrite",
                )
            )
        for index, match in enumerate(_MECHANISM_RE.finditer(prose), start=1):
            findings.append(
                ReviewerAttackFinding(
                    finding_id=f"attack-mechanism-{section}-{index}",
                    severity="major",
                    location=section,
                    problematic_text=match.group(0),
                    attack_type="mechanism_overreach",
                    evidence_status="unknown",
                    recommended_action="soften the mechanism claim or request a Stage 3 successor experiment",
                    disposition="stage3_successor",
                )
            )
        for index, match in enumerate(_NEGATIVE_RE.finditer(prose), start=1):
            findings.append(
                ReviewerAttackFinding(
                    finding_id=f"attack-negative-framing-{section}-{index}",
                    severity="minor",
                    location=section,
                    problematic_text=match.group(0),
                    attack_type="negative_framing",
                    evidence_status="bound",
                    recommended_action="state the measured trade-off and its operating conditions neutrally",
                    disposition="rewrite",
                )
            )
    relationships = claim_relationships or {}
    if narrative_contract is not None and narrative_contract.mechanism_explanation:
        mechanism_claims = {
            *narrative_contract.required_claim_ids,
            *narrative_contract.supporting_claim_ids,
        }
        if not any(
            relationships.get(claim_id) == "causal"
            for claim_id in mechanism_claims
        ):
            findings.append(
                ReviewerAttackFinding(
                    finding_id="attack-unbound-mechanism-contract",
                    severity="blocking",
                    location="PublicationNarrativeContract.mechanism_explanation",
                    problematic_text="; ".join(
                        narrative_contract.mechanism_explanation
                    ),
                    attack_type="mechanism_overreach",
                    evidence_status="unbound",
                    recommended_action=(
                        "remove the mechanism from the publication contract or "
                        "request a Stage 3 successor experiment"
                    ),
                    disposition="stage3_successor",
                )
            )
    mandatory = reporting_register.main_text_claim_ids()
    omitted = sorted(mandatory - set(presented_claim_ids))
    for claim_id in omitted:
        findings.append(
            ReviewerAttackFinding(
                finding_id=_finding_id_for_claim("omission", claim_id),
                severity="blocking",
                location="publication package",
                problematic_text=f"omitted mandatory claim {claim_id}",
                attack_type="mandatory_result_omission",
                claim_id=claim_id,
                evidence_status="bound",
                recommended_action="restore the claim in its registered destination",
                disposition="stage4_blocker",
            )
        )
    suppression = bool(omitted)
    return ReviewerAttackSurfaceReport(
        passed=not any(
            item.severity in {"blocking", "major"} for item in findings
        ),
        findings=findings,
        mandatory_claims_checked=sorted(mandatory),
        suppression_detected=suppression,
    )


__all__ = [
    "ReviewerAttackFinding",
    "ReviewerAttackSurfaceReport",
    "audit_reviewer_attack_surface",
]
