from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_forge.capability_registry import capability_map
from research_forge.profiles import (
    ComponentKind,
    ContractCompletionKind,
    OperationReadiness,
    PROFILE_BUNDLE_REGISTRY,
    PROFILE_RUNTIME_REGISTRY,
    ProfileAutomationMode,
    ProfileCertificationStatus,
    ProfileMaturity,
    ProfileOperation,
    ProfileQualificationStatus,
    component_registry,
    profile_catalog_snapshot,
    profile_descriptor,
    profile_bundle,
    profile_runtime,
    qualify_profile_contract,
    resolve_profile_capability,
)
from research_forge.profiles.compatibility import profile_compatibility
from research_forge.profiles.contracts import PairedMultiArmParameters
from research_forge.workflow_domain import (
    ProfileCapabilityStatus,
    ResearchContractVersion,
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


def test_every_bundle_has_one_lifecycle_runtime_and_descriptor() -> None:
    assert set(PROFILE_RUNTIME_REGISTRY) == set(PROFILE_BUNDLE_REGISTRY)
    for profile, runtime in PROFILE_RUNTIME_REGISTRY.items():
        assert runtime.bundle is PROFILE_BUNDLE_REGISTRY[profile]
        assert runtime.descriptor.profile_id == profile.value
        assert (
            runtime.descriptor.profile_version
            == runtime.bundle.profile_version
        )
        assert set(runtime.descriptor.operations) == set(ProfileOperation)


def test_runtime_contract_completion_classifies_missing_field_authority() -> None:
    runtime = profile_runtime(Stage3Profile.TIME_SERIES_BACKTEST_V1)
    contract = ResearchContractVersion.model_construct(
        experiment_profile=Stage3Profile.TIME_SERIES_BACKTEST_V1,
        data_boundary={},
        baseline={},
        treatment={},
        profile_parameters={},
        statistical_rules={},
        estimand={},
        data_requirements={},
        implementation_requirements={},
        environment_requirements={},
        resource_policy={},
    )
    patch = runtime.complete_contract(
        contract,
        deterministic_values={"data_boundary": {"manifest": "frozen"}},
        profile_defaults={"statistical_rules": {"confidence_level": 0.95}},
        unresolvable_reasons={"baseline": "No authorized baseline implementation."},
    )
    kinds = {item.field_path: item.kind for item in patch.items}
    assert kinds["profile.data_boundary"] is (
        ContractCompletionKind.DETERMINISTIC_DERIVATION
    )
    assert kinds["profile.statistical_rules"] is (
        ContractCompletionKind.PROFILE_DEFAULT
    )
    assert kinds["profile.baseline"] is ContractCompletionKind.UNRESOLVABLE
    assert kinds["profile.treatment"] is ContractCompletionKind.SCIENTIFIC_DECISION


def test_planned_profile_is_visible_but_cannot_be_selected_as_formal_work() -> None:
    descriptor = profile_descriptor("human_behavior_v1")

    assert descriptor.maturity is ProfileMaturity.C0_DESCRIBED
    assert descriptor.automation_mode is (
        ProfileAutomationMode.DESIGN_AND_IMPORT_ONLY
    )
    assert descriptor.ethics_or_safety_gates
    assert descriptor.formal_path_connected() is False
    assert all(
        readiness is OperationReadiness.NOT_IMPLEMENTED
        for readiness in descriptor.operations.values()
    )
    with pytest.raises(ValueError, match="blocked_unsupported_design"):
        profile_bundle("human_behavior_v1")


def test_profile_catalog_reports_current_and_planned_families() -> None:
    catalog = {item["profile_id"]: item for item in profile_catalog_snapshot()}

    assert catalog["tabular_ml_v1"]["maturity"] == "c3_real_fixture"
    assert catalog["tabular_ml_v1"]["formal_execution_supported"] is True
    assert catalog["time_series_backtest_v1"]["maturity"] == "c2_dry_run"
    assert catalog["time_series_backtest_v1"]["verified_maturity"] == "c2_dry_run"
    assert catalog["time_series_backtest_v1"]["acceptance_report_present"] is False
    assert catalog["time_series_backtest_v1"]["availability"] == "degraded"
    assert (
        catalog["time_series_backtest_v1"][
            "scientific_formal_execution_eligible"
        ]
        is False
    )
    assert (
        catalog["time_series_backtest_v1"]["formal_execution_supported"]
        is True
    )
    assert catalog["human_behavior_v1"]["registered_runtime"] is False
    assert catalog["llm_evaluation_v1"]["operations"]["execute"] == (
        "integration_tested"
    )
    assert catalog["llm_evaluation_v1"]["verified_maturity"] == "c2_dry_run"
    assert catalog["llm_evaluation_v1"]["acceptance_report_present"] is False


def test_capability_manifest_formal_profile_claim_matches_runtime_catalog() -> None:
    formal = {
        item["profile_id"]
        for item in profile_catalog_snapshot()
        if item["formal_execution_supported"] is True
    }
    declared = set(
        capability_map()["scientific_execution.typed_profiles"].supported_profiles
    )
    assert declared == formal


def test_stage_two_profile_qualification_allows_pending_stage_three_bindings() -> None:
    parameters = {
        "task_type": "classification",
        "dataset_path": "data.csv",
        "id_field": "sample_id",
        "target_field": "label",
        "feature_fields": ["x"],
        "split_strategy": "fixed_split",
        "split_field": "split",
        "train_values": ["train"],
        "test_values": ["test"],
        "primary_metric": "accuracy",
        "metrics": ["accuracy"],
        "positive_label": "1",
        "baseline_estimator": {"import_path": "pkg.models:Baseline"},
        "treatment_estimator": {"import_path": "pkg.models:Treatment"},
    }
    contract = ResearchContractVersion.model_construct(
        experiment_profile=Stage3Profile.TABULAR_ML_V1,
        profile_parameters=parameters,
        data_boundary={"population": "frozen fixture"},
        metrics=[{"name": "accuracy"}],
        baseline={"action_id": "baseline"},
        treatment={"action_id": "treatment"},
        statistical_rules={"method": "paired_run_difference"},
    )

    report = qualify_profile_contract(
        Stage3Profile.TABULAR_ML_V1,
        contract,
        manifest=None,
    )

    assert report.status is ProfileQualificationStatus.BUILD_REQUIRED
    assert report.formal_execution_allowed is False
    assert any(
        item.code == "EXECUTION_BINDINGS_PENDING" and not item.blocking
        for item in report.issues
    )
