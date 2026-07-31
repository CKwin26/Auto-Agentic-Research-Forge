from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_forge.profiles import (
    ProfileCertificationStatus,
    profile_bundle,
    resolve_profile_capability,
)
from research_forge.profiles.contracts import (
    DeterministicSimulationParameters,
)
from research_forge.profiles.deterministic_simulation import (
    DeterministicSimulationResult,
    evaluate_deterministic_simulation,
    run_deterministic_simulation,
)
from research_forge.workflow_domain import (
    ProfileCapabilityStatus,
    Stage3Profile,
)


def _parameters(**changes: object) -> DeterministicSimulationParameters:
    payload: dict[str, object] = {
        "sample_count": 100,
        "baseline_location": 2.0,
        "treatment_effect": 0.25,
        "noise_scale": 3.0,
        "effect_threshold": 0.2,
        "direction": "higher_is_better",
    }
    payload.update(changes)
    return DeterministicSimulationParameters.model_validate(payload)


def test_known_effect_is_recovered_and_supported() -> None:
    parameters = _parameters()
    baseline = run_deterministic_simulation(
        parameters, arm="baseline", seed=97
    )
    treatment = run_deterministic_simulation(
        parameters, arm="treatment", seed=97
    )

    evaluation = evaluate_deterministic_simulation(
        baseline, treatment, parameters
    )

    assert evaluation.effect == pytest.approx(0.25, abs=1e-12)
    assert evaluation.denominator == 100
    assert evaluation.verdict == "supported"


def test_threshold_boundary_is_inconclusive() -> None:
    parameters = _parameters(effect_threshold=0.25)
    evaluation = evaluate_deterministic_simulation(
        run_deterministic_simulation(parameters, arm="baseline", seed=3),
        run_deterministic_simulation(parameters, arm="treatment", seed=3),
        parameters,
    )
    assert evaluation.verdict == "inconclusive"


def test_tampered_or_unpaired_results_are_rejected() -> None:
    parameters = _parameters()
    baseline = run_deterministic_simulation(parameters, arm="baseline", seed=1)
    treatment = run_deterministic_simulation(parameters, arm="treatment", seed=2)
    with pytest.raises(ValueError, match="same seed"):
        evaluate_deterministic_simulation(baseline, treatment, parameters)

    payload = baseline.model_dump(mode="json")
    payload["mean_value"] += 1.0
    with pytest.raises(ValidationError, match="mean_value"):
        DeterministicSimulationResult.model_validate(payload)


def test_zero_effect_is_not_a_scientific_intervention() -> None:
    with pytest.raises(ValidationError, match="non-zero"):
        _parameters(treatment_effect=0.0)


def test_profile_remains_development_until_formal_packaging_is_certified() -> None:
    bundle = profile_bundle(Stage3Profile.DETERMINISTIC_SIMULATION_V1)
    assert bundle.certification_status is ProfileCertificationStatus.DEVELOPMENT
    assert resolve_profile_capability(
        Stage3Profile.DETERMINISTIC_SIMULATION_V1,
        runnable_assets_present=True,
    ) is ProfileCapabilityStatus.UNSUPPORTED
