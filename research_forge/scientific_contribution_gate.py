from __future__ import annotations

"""Pre-freeze checks that distinguish a scientific study from a smoke test."""

from enum import StrEnum
from typing import Any

from pydantic import Field

from .models import StrictModel, utc_now
from .workflow_domain import ResearchContractVersion, stable_id


class ContributionGateStatus(StrEnum):
    PASS = "pass"
    CONDITIONAL = "conditional"
    BLOCKED = "blocked"


class ContributionCheck(StrictModel):
    check_id: str
    passed: bool
    blocking: bool
    detail: str


class ScientificContributionReport(StrictModel):
    schema_version: int = 1
    report_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    status: ContributionGateStatus
    checks: list[ContributionCheck]
    blocking_issues: list[str]
    warnings: list[str]
    generated_at: str = Field(default_factory=utc_now)


def _arm_text(arm: dict[str, Any]) -> str:
    return " ".join(
        str(arm.get(name) or "")
        for name in ("name", "operation", "behavior", "implementation_spec")
    ).casefold()


def assess_scientific_contribution(
    contract: ResearchContractVersion,
) -> ScientificContributionReport:
    baseline_text = _arm_text(contract.baseline)
    treatment_text = _arm_text(contract.treatment)
    weak_baseline_tokens = (
        "constant baseline",
        "always zero",
        "always one",
        "random guess",
        "dummy baseline",
        "no-op baseline",
    )
    baseline_substantive = not any(
        token in baseline_text for token in weak_baseline_tokens
    )
    arm_delta = bool(
        baseline_text.strip()
        and treatment_text.strip()
        and baseline_text.strip() != treatment_text.strip()
    )

    validity = dict(contract.scientific_validity_contract or {})
    mechanism_claims = [
        str(item) for item in validity.get("mechanism_claims", []) if str(item)
    ]
    ablations = [
        *[str(item) for item in validity.get("feature_ablation_ids", [])],
        *[str(item) for item in validity.get("arm_variation_dimensions", [])],
    ]
    ablation_sufficient = not mechanism_claims or bool(ablations)

    unit = str(
        (contract.data_requirements or contract.data_boundary).get(
            "unit_of_analysis"
        )
        or contract.estimand.get("experimental_unit")
        or ""
    ).strip()
    variance_unit = str(
        contract.estimand.get("variance_unit") or ""
    ).strip()
    independent_units_declared = bool(unit and variance_unit)

    requested_tier = str(
        validity.get("requested_claim_tier") or "controlled_effect"
    ).casefold()
    identification_target = str(
        validity.get("identification_target") or "bundled_intervention_effect"
    ).casefold()
    causal_or_mechanistic = any(
        token in requested_tier for token in ("causal", "mechanism")
    )
    claim_scope_valid = not causal_or_mechanistic or identification_target not in {
        "bundled_intervention_effect",
        "association_only",
        "unknown",
    }

    literature_ids = [
        str(item)
        for item in contract.runtime_binding.get("literature_resource_ids", [])
        if str(item)
    ]
    novelty_bound = bool(literature_ids) or requested_tier not in {
        "novelty",
        "novel_mechanism",
    }

    checks = [
        ContributionCheck(
            check_id="substantive_baseline",
            passed=baseline_substantive,
            blocking=True,
            detail="The comparator must not be a trivial constant or dummy baseline.",
        ),
        ContributionCheck(
            check_id="identifiable_arm_delta",
            passed=arm_delta,
            blocking=True,
            detail="Baseline and treatment must differ by a registered intervention.",
        ),
        ContributionCheck(
            check_id="mechanism_ablation",
            passed=ablation_sufficient,
            blocking=True,
            detail="Mechanism claims require a registered ablation or arm variation.",
        ),
        ContributionCheck(
            check_id="independent_units",
            passed=independent_units_declared,
            blocking=True,
            detail="Experimental and variance units must both be frozen.",
        ),
        ContributionCheck(
            check_id="claim_identification_scope",
            passed=claim_scope_valid,
            blocking=True,
            detail="The planned claim tier must not exceed the identification design.",
        ),
        ContributionCheck(
            check_id="novelty_comparison_set",
            passed=novelty_bound,
            blocking=False,
            detail="Novelty claims require a frozen closest-work comparison set.",
        ),
    ]
    blockers = [
        f"CONTRIBUTION_{item.check_id.upper()}: {item.detail}"
        for item in checks
        if item.blocking and not item.passed
    ]
    warnings = [
        f"CONTRIBUTION_{item.check_id.upper()}: {item.detail}"
        for item in checks
        if not item.blocking and not item.passed
    ]
    status = (
        ContributionGateStatus.BLOCKED
        if blockers
        else ContributionGateStatus.CONDITIONAL
        if warnings
        else ContributionGateStatus.PASS
    )
    return ScientificContributionReport(
        report_id=stable_id(
            "scientific-contribution",
            contract.study_id,
            contract.version,
            status.value,
        ),
        study_id=contract.study_id,
        contract_version=contract.version,
        status=status,
        checks=checks,
        blocking_issues=blockers,
        warnings=warnings,
    )


__all__ = [
    "ContributionCheck",
    "ContributionGateStatus",
    "ScientificContributionReport",
    "assess_scientific_contribution",
]
