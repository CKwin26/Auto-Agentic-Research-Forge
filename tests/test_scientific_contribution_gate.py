from research_forge.scientific_contribution_gate import (
    ContributionGateStatus,
    assess_scientific_contribution,
)
from tests.test_contract_compiler import _contract


def test_well_scoped_controlled_effect_passes_contribution_gate() -> None:
    report = assess_scientific_contribution(_contract())

    assert report.status is ContributionGateStatus.PASS
    assert not report.blocking_issues


def test_trivial_baseline_is_blocked() -> None:
    contract = _contract()
    report = assess_scientific_contribution(
        contract.model_copy(
            update={
                "baseline": {
                    **contract.baseline,
                    "behavior": "Use a constant baseline that always zero.",
                }
            }
        )
    )

    assert report.status is ContributionGateStatus.BLOCKED
    assert any("SUBSTANTIVE_BASELINE" in item for item in report.blocking_issues)


def test_mechanism_claim_requires_registered_ablation() -> None:
    contract = _contract().model_copy(
        update={
            "scientific_validity_contract": {
                "requested_claim_tier": "mechanism",
                "identification_target": "specific_mechanism",
                "mechanism_claims": ["the added module causes the improvement"],
                "feature_ablation_ids": [],
                "arm_variation_dimensions": [],
            }
        }
    )

    report = assess_scientific_contribution(contract)

    assert report.status is ContributionGateStatus.BLOCKED
    assert any("MECHANISM_ABLATION" in item for item in report.blocking_issues)


def test_novelty_claim_without_literature_is_conditional_not_silently_passed() -> None:
    contract = _contract().model_copy(
        update={
            "scientific_validity_contract": {
                "requested_claim_tier": "novelty",
                "identification_target": "bundled_intervention_effect",
            }
        }
    )

    report = assess_scientific_contribution(contract)

    assert report.status is ContributionGateStatus.CONDITIONAL
    assert any("NOVELTY_COMPARISON_SET" in item for item in report.warnings)
