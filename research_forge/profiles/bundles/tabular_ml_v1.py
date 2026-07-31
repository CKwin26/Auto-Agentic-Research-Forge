from ..base import ExperimentProfileBundle, ProfileCertificationStatus
from ...workflow_domain import Stage3Profile


BUNDLE = ExperimentProfileBundle(
    profile_id=Stage3Profile.TABULAR_ML_V1,
    profile_version="1.0.0",
    certification_status=ProfileCertificationStatus.CERTIFIED,
    design_id="paired_two_arm_v1",
    outcome_id="continuous_outcome_v1",
    estimand_id="paired_mean_difference_v1",
    estimator_id="paired_mean_estimator_v1",
    inference_id="legacy_paired_interval_v1",
    missingness_id="block_on_missing_pair_v1",
    multiplicity_id="single_primary_hypothesis_v1",
    verdict_policy_id="superiority_threshold_v1",
    contract_schema_id="tabular_ml_contract_v1",
    run_plan_compiler_id="tabular_ml_compiler_v1",
    candidate_schema_id="tabular_ml_predictions_v1",
    analysis_table_id="paired_run_metric_table_v1",
    qualification_id="tabular_ml_qualification_v1",
    evaluator_id="tabular_ml_independent_evaluator_v1",
    claim_envelope_id="paired_metric_claim_envelope_v1",
    repair_policy_id="bounded_scientific_successor_v2",
    reproduction_comparator_id="tabular_ml_replay_v1",
    assurance_suite_id="tabular_ml_assurance_v1",
    frontend_renderer_id="tabular_ml_matrix_v1",
    required_contract_fields=(
        "data_boundary",
        "metrics",
        "baseline",
        "treatment",
        "profile_parameters",
        "statistical_rules",
    ),
    supported_data_types=("csv",),
    builder_plugins=("tabular_ml_deterministic_v1",),
    notes=(
        "Numeric CSV classification/regression with fixed split or K-fold CV.",
        "Estimator classes must implement fit/predict; probability metrics "
        "also require predict_proba or decision_function.",
        "Formal fixed-split execution materializes target-free candidate data, "
        "evaluator-only targets, self-contained code, smoke inputs, and an "
        "isolated container manifest. K-fold remains feasibility-only.",
    ),
)
