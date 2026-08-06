from ..base import ExperimentProfileBundle, ProfileCertificationStatus
from ...workflow_domain import Stage3Profile


BUNDLE = ExperimentProfileBundle(
    profile_id=Stage3Profile.TIME_SERIES_BACKTEST_V1,
    profile_version="1.0.0",
    certification_status=ProfileCertificationStatus.CERTIFIED,
    design_id="point_in_time_cross_sectional_ranking_v1",
    outcome_id="period_portfolio_return_v1",
    estimand_id="paired_mean_net_return_difference_v1",
    estimator_id="equal_weight_top_k_v1",
    inference_id="paired_period_descriptive_v1",
    missingness_id="block_on_missing_formal_target_v1",
    multiplicity_id="single_primary_hypothesis_v1",
    verdict_policy_id="superiority_threshold_v1",
    contract_schema_id="time_series_backtest_contract_v1",
    run_plan_compiler_id="time_series_backtest_compiler_v1",
    candidate_schema_id="point_in_time_signal_csv_v1",
    analysis_table_id="paired_period_return_table_v1",
    qualification_id="point_in_time_backtest_qualification_v1",
    evaluator_id="isolated_return_evaluator_v1",
    claim_envelope_id="paired_backtest_claim_envelope_v1",
    repair_policy_id="bounded_scientific_successor_v2",
    reproduction_comparator_id="time_series_backtest_replay_v1",
    assurance_suite_id="time_series_backtest_assurance_v1",
    frontend_renderer_id="time_series_backtest_matrix_v1",
    required_contract_fields=(
        "data_boundary",
        "baseline",
        "treatment",
        "profile_parameters",
        "statistical_rules",
    ),
    supported_data_types=("csv",),
    builder_plugins=("time_series_backtest_deterministic_v1",),
    notes=(
        "Admitted for controlled acceptance testing only until a complete "
        "ProfileAcceptanceReport derives C3 maturity.",
        "Raw market-data ingestion, exchange-calendar verification and "
        "corporate-action return construction remain outside the boundary.",
    ),
)
