from __future__ import annotations

from research_forge.external_validators import (
    ExternalValidationStatus,
    blocked_optional_validator,
    normalize_evidently_snapshot,
    normalize_great_expectations_checkpoint,
)
from research_forge.scientific_validity import (
    ClaimTier,
    IdentificationTarget,
    OutcomeStructure,
    ScientificValidityContract,
    ValiditySeverity,
    audit_design_validity,
    audit_evidence_validity,
    audit_manuscript_validity,
    summarize_classification_rows,
)
from research_forge.workflow_domain import (
    ArtifactStatus,
    Hypothesis,
    HypothesisRole,
    ResearchContractVersion,
)


def _complete_contract(**updates: object) -> ScientificValidityContract:
    values: dict[str, object] = {
        "enforcement_level": "confirmatory",
        "requested_claim_tier": "generalizable_effect",
        "metric_lower_bound": 0.0,
        "metric_upper_bound": 1.0,
        "expected_baseline": 0.6,
        "minimum_meaningful_effect": 0.1,
        "metric_direction": "maximize",
        "threshold_basis": "pilot v1 and bounded metric scale",
        "construct_label": "patience",
        "instrument_validation": [
            "independent raters",
            "inter-rater agreement",
            "balanced wording",
        ],
        "target_rule_disclosure": ["target formula", "tie rule"],
        "model_specification": ["features", "loss", "regularization"],
        "train_evaluation_separation": ["held-out rule family", "OOD matrix"],
        "baseline_ids": ["direct-rule", "matched-control"],
        "ablation_ids": ["delay-only", "curriculum-only"],
        "treatment_components": ["delay signal", "curriculum"],
        "manipulation_checks": ["held-out perplexity", "adapter norm"],
        "input_integrity_checks": ["rendered prompt", "tokenizer input"],
        "uncertainty_plan": ["five seeds", "family effects", "LOFO"],
        "data_provenance_mode": "synthetic",
        "data_generator_disclosure": [
            "variable distributions",
            "scenario families",
            "split overlap",
            "seed roles",
            "tie cases",
            "boundary margin",
        ],
        "seed_role_plan": [
            "generator seed",
            "split seed",
            "model seed",
            "resampling seed",
        ],
        "boundary_analysis_plan": ["tie policy", "boundary margin bins"],
        "threshold_sensitivity_plan": [
            "target-rule threshold",
            "effect threshold",
            "safeguard threshold",
        ],
        "reproducibility_release_plan": [
            "code",
            "data generator",
            "configuration",
            "environment lock",
            "execution command",
            "artifact manifest",
        ],
    }
    values.update(updates)
    return ScientificValidityContract.model_validate(values)


def test_unreachable_threshold_blocks_even_exploratory_freeze() -> None:
    contract = _complete_contract(
        enforcement_level="exploratory",
        expected_baseline=0.95,
        minimum_meaningful_effect=0.10,
    )
    report = audit_design_validity(contract)

    assert report.passed is False
    finding = next(
        item
        for item in report.findings
        if item.code == "SV-DESIGN-THRESHOLD-UNREACHABLE"
    )
    assert finding.severity is ValiditySeverity.BLOCKING
    assert finding.repair_route == "stage2_protocol_vnext"


def test_exploratory_missing_validation_lowers_claim_without_blocking() -> None:
    contract = ScientificValidityContract(
        enforcement_level="exploratory",
        requested_claim_tier=ClaimTier.GENERALIZABLE_EFFECT,
        construct_label="ideological alignment",
        baseline_ids=["untreated"],
    )
    report = audit_design_validity(contract)

    assert report.passed is True
    assert report.maximum_claim_tier is ClaimTier.IN_DISTRIBUTION_ASSOCIATION
    assert any(
        item.code == "SV-DESIGN-CONSTRUCT-UNVALIDATED"
        and item.severity is ValiditySeverity.MAJOR
        for item in report.findings
    )


def test_confirmatory_generalization_requires_rule_separation() -> None:
    contract = _complete_contract(
        train_evaluation_separation=[],
        target_rule_disclosure=[],
    )
    report = audit_design_validity(contract)

    assert report.passed is False
    assert report.maximum_claim_tier is ClaimTier.IN_DISTRIBUTION_ASSOCIATION
    assert any(
        item.code == "SV-DESIGN-GENERALIZATION-IDENTIFICATION-GAP"
        and item.severity is ValiditySeverity.BLOCKING
        for item in report.findings
    )


def test_curriculum_mechanism_requires_same_data_matched_order_control() -> None:
    contract = _complete_contract(
        identification_target=IdentificationTarget.CURRICULUM_ORDERING,
        arm_variation_dimensions=[
            "sample order",
            "task coverage",
            "label rule",
        ],
        invariant_dimensions=["feature representation", "training objective"],
        matched_control_ids=[],
    )
    report = audit_design_validity(contract)

    assert report.passed is False
    finding = next(
        item
        for item in report.findings
        if item.code == "SV-DESIGN-CURRICULUM-EFFECT-NOT-ISOLATED"
    )
    assert finding.severity is ValiditySeverity.BLOCKING
    assert "confounded_dimension=task_coverage" in finding.evidence
    assert "matched_order_control=missing" in finding.evidence


def test_curriculum_mechanism_accepts_matched_order_only_design() -> None:
    contract = _complete_contract(
        identification_target=IdentificationTarget.CURRICULUM_ORDERING,
        arm_variation_dimensions=["sample order"],
        invariant_dimensions=[
            "task coverage",
            "label rule",
            "feature representation",
            "sample composition",
            "training objective",
        ],
        matched_control_ids=["random-order"],
    )
    report = audit_design_validity(contract)

    assert not any(
        item.code == "SV-DESIGN-CURRICULUM-EFFECT-NOT-ISOLATED"
        for item in report.findings
    )


def test_target_sufficient_feature_requires_registered_ablation() -> None:
    contract = _complete_contract(
        feature_target_relationships=[
            {
                "feature_id": "expected_advantage",
                "relationship": "sufficient_statistic",
            }
        ],
        feature_ablation_ids=[],
    )
    report = audit_design_validity(contract)

    assert report.passed is False
    assert report.maximum_claim_tier is ClaimTier.IN_DISTRIBUTION_ASSOCIATION
    assert any(
        item.code == "SV-DESIGN-TARGET-SUFFICIENT-FEATURE-UNABLATED"
        for item in report.findings
    )


def test_binary_outcome_requires_behavioral_decomposition_plan() -> None:
    contract = _complete_contract(
        outcome_structure=OutcomeStructure.BINARY_CLASSIFICATION,
        behavioral_decomposition_plan=[],
    )
    report = audit_design_validity(contract)

    assert report.passed is False
    assert any(
        item.code == "SV-DESIGN-BEHAVIORAL-DECOMPOSITION-MISSING"
        and "matthews correlation coefficient" in item.evidence
        for item in report.findings
    )


def test_real_world_data_does_not_require_synthetic_generator_fields() -> None:
    contract = _complete_contract(
        data_provenance_mode="real_world",
        data_generator_disclosure=[],
        seed_role_plan=[],
        boundary_analysis_plan=[],
    )
    report = audit_design_validity(contract)
    codes = {item.code for item in report.findings}

    assert "SV-DESIGN-GENERATOR-DISCLOSURE-INCOMPLETE" not in codes
    assert "SV-DESIGN-SEED-ROLES-MISSING" not in codes
    assert "SV-DESIGN-BOUNDARY-ANALYSIS-MISSING" not in codes


def test_classification_rows_produce_conditional_diagnostics() -> None:
    summary = summarize_classification_rows(
        [
            {"truth": 1, "prediction": 1, "scenario_family": "wait"},
            {"truth": 1, "prediction": 0, "scenario_family": "wait"},
            {"truth": 0, "prediction": 1, "scenario_family": "persist"},
            {"truth": 0, "prediction": 0, "scenario_family": "persist"},
        ]
    )

    assert summary["denominator"] == 4
    assert summary["balanced_accuracy"] == 0.5
    assert summary["matthews_correlation_coefficient"] == 0.0
    assert summary["per_task"]["wait"]["action_rate"] == 0.5
    assert summary["per_task"]["persist"]["action_rate"] == 0.5


def test_stage3_requires_registered_controls_ablations_and_release_package() -> None:
    contract = _complete_contract(
        matched_control_ids=["random-order"],
        feature_ablation_ids=["raw-features"],
    )
    report = audit_evidence_validity(
        contract,
        {
            "input_integrity": {"all_registered_inputs_match": True},
            "completed_manipulation_checks": list(contract.manipulation_checks),
            "completed_control_ids": [],
            "completed_feature_ablation_ids": [],
            "generator_disclosure_verified": True,
            "seed_roles_verified": True,
            "boundary_analysis_completed": True,
            "threshold_sensitivity_completed": True,
            "ood_or_rule_separated_evaluation_passed": True,
        },
    )
    codes = {item.code for item in report.findings}

    assert "SV-EVIDENCE-MATCHED-CONTROL-NOT-RUN" in codes
    assert "SV-EVIDENCE-FEATURE-ABLATION-NOT-RUN" in codes
    assert "SV-EVIDENCE-REPRODUCIBILITY-PACKAGE-INCOMPLETE" in codes


def test_manuscript_cannot_claim_curriculum_from_bundled_intervention() -> None:
    report = audit_manuscript_validity(
        manuscript=(
            "# Study\n\n## Abstract\n\n"
            "We test curriculum learning and report an improvement."
        ),
        abstract="We test curriculum learning and report an improvement.",
        claim_context={
            "maximum_claim_tier": "controlled_effect",
            "identification_target": "bundled_intervention_effect",
            "mechanism_identified": False,
        },
    )

    assert any(
        item.code == "SV-MANUSCRIPT-UNIDENTIFIED-CURRICULUM-MECHANISM"
        for item in report.findings
    )


def test_manuscript_requires_registered_reproducibility_statement() -> None:
    report = audit_manuscript_validity(
        manuscript="# Study\n\n## Abstract\n\nWe report a bounded effect.",
        abstract="We report a bounded effect.",
        claim_context={"reproducibility_release_planned": True},
    )

    assert any(
        item.code == "SV-MANUSCRIPT-REPRODUCIBILITY-STATEMENT-MISSING"
        for item in report.findings
    )


def test_stage3_input_damage_routes_to_experimental_successor() -> None:
    report = audit_evidence_validity(
        _complete_contract(),
        {
            "input_integrity": {
                "rendered_prompt_utf8": False,
                "tokenizer_input_hash_matches": True,
            },
            "completed_manipulation_checks": [
                "held-out perplexity",
                "adapter norm",
            ],
            "ood_or_rule_separated_evaluation_passed": True,
            "independent_clusters": 12,
        },
    )

    assert report.passed is False
    assert report.maximum_claim_tier is ClaimTier.DESCRIPTIVE
    finding = next(
        item
        for item in report.findings
        if item.code == "SV-EVIDENCE-INPUT-INTEGRITY-FAILED"
    )
    assert finding.repair_route == "stage3_experimental_successor"


def test_stage3_diagnostic_anomaly_requires_conditional_analysis() -> None:
    report = audit_evidence_validity(
        _complete_contract(),
        {
            "input_integrity": {"all_registered_inputs_match": True},
            "completed_manipulation_checks": [
                "held-out perplexity",
                "adapter norm",
            ],
            "diagnostic_flags": ["patient_action_rate"],
            "completed_diagnostic_followups": [],
            "ood_or_rule_separated_evaluation_passed": True,
        },
    )

    assert any(
        item.code == "SV-EVIDENCE-DIAGNOSTIC-FOLLOWUP-MISSING"
        for item in report.findings
    )


def test_submission_audit_rejects_report_residue_and_structured_abstract() -> None:
    manuscript = """
# A Study

## Abstract

Background: We study a system.

Results: It changed.

## Results

See stage3/evaluation.json. 待呈现图位。
"""
    report = audit_manuscript_validity(
        manuscript=manuscript,
        abstract="Background: We study a system.\n\nResults: It changed.",
        figure_specs=[
            {
                "kind": "arm_mean_bar",
                "effect_estimate": False,
                "confidence_interval": False,
                "zero_reference": False,
            }
        ],
        result_table_columns=["arm", "mean"],
        main_text_audit_paths=["stage3/evaluation.json"],
    )

    assert report.passed is False
    codes = {item.code for item in report.findings}
    assert "SV-MANUSCRIPT-FIGURE-PLACEHOLDER" in codes
    assert "SV-MANUSCRIPT-ABSTRACT-STRUCTURE" in codes
    assert "SV-MANUSCRIPT-AUDIT-DETAIL-IN-MAIN-TEXT" in codes
    assert "SV-MANUSCRIPT-EFFECT-FIGURE-MISSING" in codes
    assert "SV-MANUSCRIPT-RESULT-TABLE-INCOMPLETE" in codes


def test_validity_contract_round_trips_through_research_contract() -> None:
    validity = _complete_contract().model_dump(mode="json")
    contract = ResearchContractVersion(
        schema_version=2,
        study_id="study-validity",
        version=1,
        scope_version=1,
        status=ArtifactStatus.DRAFT,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary",
                statement="The treatment changes the frozen outcome.",
                role=HypothesisRole.PRIMARY,
                decision_rule={"type": "frozen_threshold"},
            )
        ],
        data_boundary={"dataset": "frozen"},
        metrics=[{"name": "accuracy", "direction": "maximize"}],
        baseline={"name": "control"},
        treatment={"name": "treatment"},
        runtime_binding={},
        evaluator_policy={},
        scientific_validity_contract=validity,
    )

    restored = ResearchContractVersion.model_validate(contract.model_dump(mode="json"))
    parsed = ScientificValidityContract.model_validate(
        restored.scientific_validity_contract
    )
    assert parsed.requested_claim_tier is ClaimTier.GENERALIZABLE_EFFECT


def _external_kwargs() -> dict[str, object]:
    return {
        "tool_version": "test",
        "input_artifact_ids": ["artifact-data"],
        "input_hashes": {"artifact-data": "a" * 64},
        "configuration_sha256": "b" * 64,
        "raw_output_artifact_id": "artifact-raw-result",
    }


def test_great_expectations_result_is_diagnostic_only() -> None:
    record = normalize_great_expectations_checkpoint(
        {
            "success": False,
            "run_results": {
                "batch": {
                    "validation_result": {
                        "results": [
                            {
                                "success": False,
                                "expectation_config": {
                                    "type": "expect_column_values_to_not_be_null"
                                },
                            }
                        ]
                    }
                }
            },
        },
        **_external_kwargs(),
    )

    assert record.status is ExternalValidationStatus.FAILED
    assert record.authority == "diagnostic_only"
    assert record.findings[0].source_check == ("expect_column_values_to_not_be_null")


def test_evidently_warning_does_not_become_scientific_failure() -> None:
    record = normalize_evidently_snapshot(
        {
            "tests": [
                {
                    "name": "small segment",
                    "status": "warning",
                    "description": "Segment uncertainty is high.",
                }
            ]
        },
        **_external_kwargs(),
    )

    assert record.status is ExternalValidationStatus.PASSED
    assert record.findings[0].severity == "warning"
    assert record.authority == "diagnostic_only"


def test_missing_open_source_validator_is_explicitly_blocked() -> None:
    record = blocked_optional_validator(
        tool="evidently",
        **_external_kwargs(),
    )

    assert record.status is ExternalValidationStatus.BLOCKED_OPTIONAL_DEPENDENCY
