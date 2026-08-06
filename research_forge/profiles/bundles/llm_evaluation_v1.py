from ..base import ExperimentProfileBundle, ProfileCertificationStatus
from ...workflow_domain import Stage3Profile


BUNDLE = ExperimentProfileBundle(
    profile_id=Stage3Profile.LLM_EVALUATION_V1,
    profile_version="1.0.0",
    certification_status=ProfileCertificationStatus.CERTIFIED,
    design_id="frozen_llm_response_comparison_v1",
    outcome_id="deterministic_task_score_v1",
    estimand_id="paired_mean_task_score_difference_v1",
    estimator_id="normalized_exact_match_v1",
    inference_id="paired_task_descriptive_v1",
    missingness_id="score_zero_on_missing_response_v1",
    multiplicity_id="single_primary_hypothesis_v1",
    verdict_policy_id="superiority_threshold_v1",
    contract_schema_id="llm_evaluation_contract_v1",
    run_plan_compiler_id="llm_evaluation_compiler_v1",
    candidate_schema_id="frozen_llm_response_jsonl_v1",
    analysis_table_id="paired_llm_task_score_v1",
    qualification_id="llm_evaluation_qualification_v1",
    evaluator_id="normalized_exact_match_evaluator_v1",
    claim_envelope_id="paired_llm_score_claim_v1",
    repair_policy_id="bounded_scientific_successor_v2",
    reproduction_comparator_id="llm_evaluation_replay_v1",
    assurance_suite_id="llm_evaluation_assurance_v1",
    frontend_renderer_id="llm_task_matrix_v1",
    required_contract_fields=(
        "data_boundary",
        "baseline",
        "treatment",
        "profile_parameters",
        "statistical_rules",
    ),
    supported_data_types=("jsonl",),
    builder_plugins=("deterministic_llm_response_evaluator_v1",),
    notes=(
        "Controlled end-to-end paired MCQ execution with target-isolated scoring.",
        "Formal availability is evidence-derived from the acceptance report; open-ended generation and LLM-as-judge are not certified.",
    ),
)
