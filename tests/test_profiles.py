from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_forge.profiles import (
    ComponentKind,
    PROFILE_BUNDLE_REGISTRY,
    ProfileCertificationStatus,
    component_registry,
    profile_bundle,
    resolve_profile_capability,
)
from research_forge.profiles.compatibility import profile_compatibility
from research_forge.profiles.contracts import PairedMultiArmParameters
from research_forge.workflow_domain import (
    ProfileCapabilityStatus,
    Stage3Profile,
)


def test_every_profile_bundle_uses_whitelisted_components() -> None:
    for bundle in PROFILE_BUNDLE_REGISTRY.values():
        for kind, component_id in bundle.component_ids().items():
            assert component_id in component_registry(kind)


def test_historical_v1_is_frozen_and_keeps_legacy_bindings() -> None:
    bundle = profile_bundle(
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1
    )

    assert bundle.certification_status is (
        ProfileCertificationStatus.LEGACY_FROZEN
    )
    assert bundle.run_plan_compiler_id == "stage3-compiler-v2"
    assert bundle.evaluator_id == "independent_sample_metric_v1"
    with pytest.raises(ValidationError):
        bundle.profile_version = "silently-changed"


def test_assured_paired_bundles_are_formally_runnable() -> None:
    for profile in (
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
        Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1,
    ):
        bundle = profile_bundle(profile)
        capability = resolve_profile_capability(
            profile,
            runnable_assets_present=True,
        )
        assert (
            bundle.certification_status
            is ProfileCertificationStatus.CERTIFIED
        )
        assert capability is ProfileCapabilityStatus.SUPPORTED


def test_v1_to_v2_requires_scientific_successor_and_no_result_reuse() -> None:
    compatibility = profile_compatibility(
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
    )

    assert compatibility.requires_scientific_successor is True
    assert compatibility.run_cell_reuse_allowed is False
    assert compatibility.result_reuse_allowed is False
    assert compatibility.verdict_reuse_allowed is False


def test_component_layers_are_not_mislabelled_as_profiles() -> None:
    inference = component_registry(ComponentKind.INFERENCE)
    multiplicity = component_registry(ComponentKind.MULTIPLICITY)

    assert "exact_mcnemar_v1" in inference
    assert "holm_family_v1" in multiplicity
    assert "exact_mcnemar_v1" not in {
        item.profile_id.value for item in PROFILE_BUNDLE_REGISTRY.values()
    }


def test_multi_arm_profile_requires_one_treatment_and_two_controls() -> None:
    parameters = PairedMultiArmParameters.model_validate(
        {
            "pairing_key": "item_id",
            "cluster_id_field": "scenario_family",
            "variance_unit": "cluster",
            "inference_spec": {
                "method": "cluster_bootstrap",
                "resample_unit": "scenario_family",
                "resamples": 10_000,
                "seed": 20260727,
                "confidence_level": 0.95,
            },
            "arms": ["backbone", "neutral_lora", "marxist_lora"],
            "treatment_arm": "marxist_lora",
            "control_arms": ["backbone", "neutral_lora"],
        }
    )

    assert parameters.treatment_arm == "marxist_lora"
    assert parameters.control_arms == ["backbone", "neutral_lora"]
    with pytest.raises(ValidationError):
        PairedMultiArmParameters.model_validate(
            {
                **parameters.model_dump(mode="json"),
                "control_arms": ["backbone"],
            }
        )
