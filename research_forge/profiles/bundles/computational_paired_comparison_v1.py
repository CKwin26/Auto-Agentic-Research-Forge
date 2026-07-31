"""Frozen adapter for the historical Stage 3 Profile v1 semantics."""

from ..base import ExperimentProfileBundle, ProfileCertificationStatus
from ...workflow_domain import Stage3Profile

BUNDLE = ExperimentProfileBundle(
    profile_id=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
    profile_version="1.0.0",
    certification_status=ProfileCertificationStatus.LEGACY_FROZEN,
    design_id="paired_two_arm_v1",
    outcome_id="continuous_outcome_v1",
    estimand_id="paired_mean_difference_v1",
    estimator_id="paired_mean_estimator_v1",
    inference_id="legacy_paired_interval_v1",
    missingness_id="legacy_inconclusive_or_disqualify_v1",
    multiplicity_id="single_primary_hypothesis_v1",
    verdict_policy_id="superiority_threshold_v1",
    contract_schema_id="computational_paired_contract_v1",
    run_plan_compiler_id="stage3-compiler-v2",
    candidate_schema_id="sample_predictions_v1",
    analysis_table_id="paired_summary_table_v1",
    qualification_id="paired_summary_qualification_v1",
    evaluator_id="independent_sample_metric_v1",
    claim_envelope_id="paired_claim_envelope_v1",
    repair_policy_id="bounded_successor_v1",
    reproduction_comparator_id="computational_paired_comparison_v1",
    assurance_suite_id="paired_profile_assurance_legacy_v1",
    frontend_renderer_id="paired_matrix_v1",
    required_contract_fields=(
        "estimand",
        "data_requirements",
        "implementation_requirements",
        "environment_requirements",
        "hypotheses",
        "metrics",
        "statistical_rules",
    ),
    supported_data_types=("json", "jsonl", "csv"),
    builder_plugins=(
        "ready_made_experiment_v1",
        "computational_paired_comparison_codex_v1",
    ),
    historical_semantics_immutable=True,
    notes=(
        "Compatibility adapter only; semantic changes require Profile v2.",
        "Legacy ready-made summary adapters remain lower-assurance.",
    ),
)
