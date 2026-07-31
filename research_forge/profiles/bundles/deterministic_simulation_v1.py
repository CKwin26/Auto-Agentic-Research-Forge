from ..base import ExperimentProfileBundle, ProfileCertificationStatus
from ...workflow_domain import Stage3Profile


BUNDLE = ExperimentProfileBundle(
    profile_id=Stage3Profile.DETERMINISTIC_SIMULATION_V1,
    profile_version="0.1.0",
    certification_status=ProfileCertificationStatus.DEVELOPMENT,
    design_id="paired_two_arm_v1",
    outcome_id="continuous_outcome_v1",
    estimand_id="paired_mean_difference_v1",
    estimator_id="paired_mean_estimator_v1",
    inference_id="legacy_paired_interval_v1",
    missingness_id="block_on_missing_pair_v1",
    multiplicity_id="single_primary_hypothesis_v1",
    verdict_policy_id="superiority_threshold_v1",
    contract_schema_id="deterministic_simulation_contract_v1",
    run_plan_compiler_id="deterministic_simulation_compiler_v1",
    candidate_schema_id="paired_simulation_rows_v1",
    analysis_table_id="paired_simulation_difference_v1",
    qualification_id="deterministic_simulation_qualification_v1",
    evaluator_id="deterministic_simulation_evaluator_v1",
    claim_envelope_id="paired_metric_claim_envelope_v1",
    repair_policy_id="bounded_scientific_successor_v2",
    reproduction_comparator_id="deterministic_simulation_replay_v1",
    assurance_suite_id="deterministic_simulation_assurance_v1",
    frontend_renderer_id="paired_simulation_v1",
    required_contract_fields=(
        "metrics",
        "baseline",
        "treatment",
        "profile_parameters",
        "statistical_rules",
    ),
    supported_data_types=("generated_numeric_rows",),
    builder_plugins=("deterministic_simulation_v1",),
    notes=(
        "Component-tested known-effect simulation fixture.",
        "Formal Stage 3 admission remains blocked until the isolated package "
        "materializer and contract validator are certified.",
    ),
)
