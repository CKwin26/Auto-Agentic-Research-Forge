from ..base import ExperimentProfileBundle, ProfileCertificationStatus
from ...workflow_domain import Stage3Profile


BUNDLE = ExperimentProfileBundle(
    profile_id=Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1,
    profile_version="1.0.0",
    certification_status=ProfileCertificationStatus.CERTIFIED,
    design_id="paired_multi_arm_v1",
    outcome_id="continuous_outcome_v1",
    estimand_id="paired_mean_difference_v1",
    estimator_id="paired_mean_estimator_v1",
    inference_id="cluster_bootstrap_continuous_v1",
    missingness_id="block_on_missing_pair_v1",
    multiplicity_id="holm_family_v1",
    verdict_policy_id="superiority_with_safety_gate_v1",
    contract_schema_id="paired_multi_arm_contract_v1",
    run_plan_compiler_id="paired_multi_arm_compiler_v1",
    candidate_schema_id="sample_level_continuous_multi_arm_v1",
    analysis_table_id="paired_multi_arm_cluster_table_v1",
    qualification_id="paired_multi_arm_qualification_v1",
    evaluator_id="paired_multi_arm_conjunction_v1",
    claim_envelope_id="paired_multi_arm_claim_envelope_v1",
    repair_policy_id="bounded_scientific_successor_v2",
    reproduction_comparator_id="paired_multi_arm_ablation_v1",
    assurance_suite_id="paired_multi_arm_cluster_assurance_v1",
    frontend_renderer_id="paired_multi_arm_matrix_v1",
    required_contract_fields=(
        "estimand",
        "variance_unit",
        "aggregation_hierarchy",
        "model_selection_plan",
        "arm_fairness_contract",
        "inference_spec",
        "primary_contrasts",
    ),
    supported_data_types=("jsonl", "parquet"),
    builder_plugins=("paired_multi_arm_codex_v1",),
    notes=(
        "Three or more paired arms with a preregistered conjunction over "
        "treatment-versus-control contrasts and protected safeguards.",
    ),
)
