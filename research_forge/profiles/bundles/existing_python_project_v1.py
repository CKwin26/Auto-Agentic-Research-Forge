from ..base import ExperimentProfileBundle, ProfileCertificationStatus
from ...workflow_domain import Stage3Profile


BUNDLE = ExperimentProfileBundle(
    profile_id=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
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
    contract_schema_id="existing_python_project_contract_v1",
    run_plan_compiler_id="existing_python_project_compiler_v1",
    candidate_schema_id="existing_project_prediction_artifact_v1",
    analysis_table_id="paired_run_metric_table_v1",
    qualification_id="existing_project_replay_qualification_v1",
    evaluator_id="isolated_project_result_parser_v1",
    claim_envelope_id="reproduction_claim_envelope_v1",
    repair_policy_id="bounded_scientific_successor_v2",
    reproduction_comparator_id="existing_python_clean_replay_v1",
    assurance_suite_id="existing_python_project_assurance_v1",
    frontend_renderer_id="existing_project_replay_v1",
    required_contract_fields=(
        "data_boundary",
        "metrics",
        "baseline",
        "treatment",
        "profile_parameters",
        "statistical_rules",
        "environment_requirements",
    ),
    supported_data_types=("json", "jsonl", "csv", "parquet"),
    builder_plugins=("existing_python_project_adapter_v1",),
    notes=(
        "Replays explicit baseline and treatment commands from a frozen project snapshot.",
        "Requires a pinned container, requirements lock, isolated evaluator, and clean replay.",
    ),
)
