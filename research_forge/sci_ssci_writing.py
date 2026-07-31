"""Evidence-preserving SCI/SSCI writing policy for Stage 4.

The policy adapts selected workflow concepts from Yila-AI/sci-ssci-skills.
It is intentionally implemented as prompt guidance plus deterministic checks:
it has no authority to change a frozen claim, experiment result, or verdict.
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping


SCI_SSCI_SOURCE_REPOSITORY = "https://github.com/Yila-AI/sci-ssci-skills"
SCI_SSCI_LICENSE = "Apache-2.0"
SCI_SSCI_ADAPTATION_NOTICE = (
    "Adapted from Yila-AI/sci-ssci-skills, including the "
    "Evidence-Preserving Draft Contract and Claim-Strength Contract."
)

# Git blob identifiers make the locally adapted policy auditable without
# downloading or executing third-party code at runtime.
SCI_SSCI_SOURCE_BLOBS: dict[str, str] = {
    "LICENSE": "261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64",
    "science-research-writing/input-output-contract.md": (
        "d6b99db9aa2a968f654308002ac4fa4ef86d93d9"
    ),
    "science-research-writing/certainty-and-claim-strength.md": (
        "8a91d485d725f9cfa3711ae5a7a588f14e6e8438"
    ),
    "science-research-writing/reverse-engineering-protocol.md": (
        "985ab4b87ebf0210e5e7261f34135bac05b55bea"
    ),
    "science-research-writing/section-function-map.md": (
        "16d0cf280978837679a13cc0bb947a638fb63258"
    ),
    "science-research-writing/title.md": (
        "66d2789654859ebbca859923c9f9779cda8cb69f"
    ),
    "science-research-writing/abstract.md": (
        "37dfde46a0ac74d17da62d61db1b33bed5d3ecdf"
    ),
    "science-research-writing/results.md": (
        "7f0d844c2fa3340c254b2f782e956aaaa1f33d4e"
    ),
    "science-research-writing/discussion.md": (
        "7f179006fcb091a8caba3a8a3fefed4ac17a3fa4"
    ),
    "sci-ssci-polishing/invariants.md": (
        "9727dcd858581fd9ec9c7709bce9198e3b069d17"
    ),
    "sci-ssci-polishing/rhetorical-routing.md": (
        "f34044e67748364ad8dd9758d6665fac95340b58"
    ),
    "sci-ssci-polishing/output-contract.md": (
        "3ea20b5a1abb6df669f4143d4e0a4c945c48c745"
    ),
}

_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]{1,79})\]")
_INTERNAL_IDENTIFIER_RE = re.compile(
    r"(?<!\w)(?:task|study|step|run)-[a-z0-9-]+(?!\w)"
    r"|(?<!\w)[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?!\w)",
    re.IGNORECASE,
)
_ABSTRACT_LABEL_RE = re.compile(
    r"^\s*(?:background|objective|methods?|results?|conclusions?|"
    r"背景|目的|方法|结果|结论)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)
_CAUSAL_MARKERS = (
    " causes ",
    " caused ",
    " leads to ",
    " affects ",
    " demonstrates that ",
    "证明了",
    "导致",
    "造成",
    "影响了",
)
_NEGATED_CAUSAL_MARKERS = (
    "does not establish caus",
    "cannot establish caus",
    "not a causal",
    "不构成因果",
    "不能证明因果",
    "无法证明因果",
)


def stage_four_sci_ssci_contract(*, language: str = "zh") -> dict[str, Any]:
    """Return a prompt-safe, versioned writing contract for Stage 4."""

    return {
        "schema_version": 1,
        "policy_id": "research-forge-sci-ssci-writing-v1",
        "language": language,
        "source": {
            "repository": SCI_SSCI_SOURCE_REPOSITORY,
            "license": SCI_SSCI_LICENSE,
            "adaptation_notice": SCI_SSCI_ADAPTATION_NOTICE,
            "source_blobs": dict(SCI_SSCI_SOURCE_BLOBS),
        },
        "scientific_authority_boundary": {
            "may_rewrite_prose": True,
            "may_reorder_within_frozen_section_contract": True,
            "may_change_frozen_claims": False,
            "may_change_numbers_or_citations": False,
            "may_change_experiment_verdict": False,
            "may_add_evidence_or_sources": False,
            "missing_support_action": "narrow, disclose, or abstain",
        },
        "section_function_map": {
            "title": "What was studied, in whom or what, and by what design?",
            "abstract": (
                "What problem, gap, approach, principal evidence, and calibrated "
                "implication should the reader retain?"
            ),
            "introduction": (
                "Why does the problem matter, what is unresolved, and what exact "
                "question does this study answer?"
            ),
            "methods": (
                "How were the design, data boundary, comparison, measurement, "
                "and analysis specified so the study can be audited?"
            ),
            "results": (
                "What was observed, with direction, magnitude, denominator, and "
                "uncertainty, without mechanism or advocacy?"
            ),
            "discussion": (
                "How should the observations be interpreted, bounded, compared "
                "with literature, and limited?"
            ),
            "limitations": (
                "Which conclusions remain unsupported or non-generalizable, and why?"
            ),
        },
        "title_promise_check": [
            "Every title element maps to the frozen objective, design, population, "
            "variables, evidence, or conclusion.",
            "Never expose code variables, workflow states, task identifiers, or "
            "internal metric keys.",
            "Do not promise causality, generality, or validation beyond the frozen "
            "claim strength.",
        ],
        "abstract_route": [
            "problem_or_context",
            "gap_and_objective",
            "approach",
            "principal_findings",
            "calibrated_implication",
        ],
        "claim_strength_ladder": [
            "consistent_with_or_may_suggest",
            "associated_with",
            "predicts",
            "contributes_to",
            "affects_or_leads_to",
            "causes_or_demonstrates",
        ],
        "invariants": {
            "exact": [
                "numbers",
                "units",
                "statistics",
                "sample_and_group_labels",
                "official_model_and_dataset_names",
                "citations",
                "figure_and_table_callouts",
            ],
            "semantic": [
                "actor_population_setting",
                "intervention_comparator_outcome",
                "direction_and_temporal_order",
                "association_prediction_effect_causation",
                "observation_versus_interpretation",
                "scope_and_limitations",
            ],
            "no_silent_claim_strength_change": True,
        },
        "target_journal_model": {
            "minimum_relevant_recent_papers_when_available": 4,
            "record_functions_and_source_locators_not_source_wording": True,
            "held_out_validation_required_before_freeze": True,
            "cannot_add_scientific_evidence": True,
        },
    }


def _plain_draft(draft: Any) -> dict[str, str]:
    value = (
        draft.model_dump(mode="json")
        if hasattr(draft, "model_dump")
        else dict(draft)
    )
    return {str(key): str(item or "") for key, item in value.items()}


def _contains_unnegated_causal_marker(text: str) -> bool:
    lowered = f" {text.lower()} "
    if any(marker in lowered for marker in _NEGATED_CAUSAL_MARKERS):
        lowered = re.sub(
            r"(?:does not|cannot|can not|fails to)\s+establish\s+caus\w*"
            r"|not a causal\w*|不构成因果\w*|不能证明因果\w*|无法证明因果\w*",
            "",
            lowered,
        )
    return any(marker in lowered for marker in _CAUSAL_MARKERS)


def audit_stage_four_manuscript(
    draft: Any,
    *,
    verified_source_ids: set[str] | list[str] | tuple[str, ...],
    causal_claim_authorized: bool,
) -> dict[str, Any]:
    """Run deterministic checks derived from the adapted writing contract.

    These checks are intentionally conservative and narrow. Semantic review
    remains the job of the scientific panel; this function only enforces
    machine-checkable publication invariants.
    """

    sections = _plain_draft(draft)
    title = sections.get("title", "")
    abstract = sections.get("abstract", "")
    all_text = "\n".join(sections.values())
    verified = set(verified_source_ids)
    citations = Counter(_CITATION_RE.findall(all_text))
    unknown_citations = sorted(set(citations) - verified)
    title_identifiers = sorted(set(_INTERNAL_IDENTIFIER_RE.findall(title)))
    abstract_is_one_paragraph = len(
        [part for part in re.split(r"\n\s*\n", abstract.strip()) if part.strip()]
    ) <= 1
    abstract_has_no_labels = _ABSTRACT_LABEL_RE.search(abstract) is None
    causal_overreach = (
        not causal_claim_authorized
        and _contains_unnegated_causal_marker(title + "\n" + abstract)
    )
    findings: list[dict[str, str]] = []
    if title_identifiers:
        findings.append(
            {
                "code": "TITLE_INTERNAL_IDENTIFIER",
                "message": (
                    "Title exposes internal identifiers: "
                    + ", ".join(title_identifiers)
                ),
            }
        )
    if not abstract_is_one_paragraph:
        findings.append(
            {
                "code": "ABSTRACT_NOT_SINGLE_PARAGRAPH",
                "message": "Abstract must be one unstructured paragraph.",
            }
        )
    if not abstract_has_no_labels:
        findings.append(
            {
                "code": "ABSTRACT_SECTION_LABEL",
                "message": "Abstract contains a structured section label.",
            }
        )
    if unknown_citations:
        findings.append(
            {
                "code": "UNVERIFIED_CITATION",
                "message": (
                    "Draft cites sources outside the frozen verified set: "
                    + ", ".join(unknown_citations)
                ),
            }
        )
    if causal_overreach:
        findings.append(
            {
                "code": "CLAIM_STRENGTH_ESCALATION",
                "message": (
                    "Title or abstract uses causal language without a causal "
                    "EvidenceClaimBinding."
                ),
            }
        )
    return {
        "schema_version": 1,
        "policy_id": "research-forge-sci-ssci-writing-v1",
        "source_repository": SCI_SSCI_SOURCE_REPOSITORY,
        "license": SCI_SSCI_LICENSE,
        "checks": {
            "title_promise_has_no_internal_identifiers": not title_identifiers,
            "abstract_is_one_unstructured_paragraph": abstract_is_one_paragraph,
            "abstract_has_no_section_labels": abstract_has_no_labels,
            "citations_belong_to_frozen_verified_set": not unknown_citations,
            "claim_strength_not_escalated": not causal_overreach,
        },
        "findings": findings,
        "passed": not findings,
    }


__all__ = [
    "SCI_SSCI_ADAPTATION_NOTICE",
    "SCI_SSCI_LICENSE",
    "SCI_SSCI_SOURCE_BLOBS",
    "SCI_SSCI_SOURCE_REPOSITORY",
    "audit_stage_four_manuscript",
    "stage_four_sci_ssci_contract",
]
