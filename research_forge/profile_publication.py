from __future__ import annotations

"""Profile-specific publication and independent-recalculation contracts."""

from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .scientific_figure_engine import PROFILE_FIGURE_ADAPTERS, FigureType


class MinimumReproducibleResultSchema(StrictModel):
    schema_version: int = 1
    profile_id: str
    natural_language_name: str
    required_fields: list[str] = Field(min_length=1)
    required_artifact_kinds: list[str] = Field(min_length=1)
    recomputation_scope: str


class ReproducibleResultField(StrictModel):
    field: str
    artifact_id: str
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    json_path: str | None = None
    unit: str | None = None
    present: bool = True


class MinimumReproducibleResultReport(StrictModel):
    schema_version: int = 1
    study_id: str
    profile_id: str
    schema_fields: list[str]
    supplied_fields: list[str]
    missing_fields: list[str]
    supplied_artifact_kinds: list[str]
    missing_artifact_kinds: list[str]
    independently_recalculable: bool
    submission_ready_results_allowed: bool
    generated_at: str = Field(default_factory=utc_now)


class ProfilePublicationAdapter(StrictModel):
    schema_version: int = 1
    profile_id: str
    natural_language_study_design_name: str
    required_methods_fields: list[str] = Field(min_length=1)
    required_results_fields: list[str] = Field(min_length=1)
    required_figures: list[FigureType] = Field(min_length=1)
    required_tables: list[str] = Field(min_length=1)
    reporting_checklist: list[str] = Field(min_length=1)
    claim_envelope: str
    forbidden_claims: list[str] = Field(min_length=1)
    minimum_literature_categories: list[str] = Field(min_length=1)
    minimum_reproducibility_fields: list[str] = Field(min_length=1)
    profile_specific_limitations: list[str] = Field(min_length=1)
    target_venue_compatibility: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def figures_match_profile_registry(self) -> "ProfilePublicationAdapter":
        figure_adapter = PROFILE_FIGURE_ADAPTERS.get(self.profile_id)
        if figure_adapter is None:
            raise ValueError("Profile Publication Adapter lacks a Figure Adapter")
        required = {item.figure_type for item in figure_adapter.requirements}
        if not required <= set(self.required_figures):
            raise ValueError("Profile Publication Adapter omits required Profile figures")
        return self


_COMMON_METHODS = [
    "research question and registered hypotheses",
    "population, unit of analysis, eligibility, and exclusions",
    "arm definitions and authorized intervention contrast",
    "outcomes, estimands, denominators, and missing-data rule",
    "estimator, uncertainty procedure, and decision rule",
    "execution matrix, seeds or replicates, and frozen environment",
]
_COMMON_RESULTS = [
    "analyzed denominators and exclusions by outcome and arm",
    "arm-specific estimates",
    "registered contrast and complete uncertainty interval",
    "qualification status and scientific decision",
]
_COMMON_FORBIDDEN = [
    "claims broader than the registered population, tasks, outcomes, and environment",
    "causal language without a causal identification contract",
    "unsupported claims inferred from metadata-only literature",
    "changes to the frozen Scientific Verdict",
]
_COMMON_LITERATURE = [
    "study-design methodology",
    "estimator and uncertainty literature",
    "domain application literature",
    "known limitations and alternative methods",
]
_COMMON_CHECKLIST = [
    "claim-to-artifact binding complete",
    "all registered outcomes reported",
    "denominators and exclusions reconciled",
    "figure semantic acceptance passed",
    "citation and numerical QA passed",
    "negative or inconclusive results preserved",
]


MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS: dict[str, MinimumReproducibleResultSchema] = {
    "independent_group_comparison_v1": MinimumReproducibleResultSchema(
        profile_id="independent_group_comparison_v1",
        natural_language_name="independent-group comparison",
        required_fields=[
            "arm_analyzed_n", "arm_mean_or_proportion", "arm_sd_or_sufficient_statistics",
            "effect", "standard_error", "degrees_of_freedom", "ci_lower", "ci_upper",
            "missing_count", "excluded_count", "estimator_settings",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "run_manifest"],
        recomputation_scope="recalculate arm summaries, the registered effect, uncertainty interval, and decision",
    ),
    "factorial_experiment_v1": MinimumReproducibleResultSchema(
        profile_id="factorial_experiment_v1",
        natural_language_name="two-by-two factorial experiment",
        required_fields=[
            "cell_n", "cell_means", "design_matrix", "contrast_coding", "coefficients",
            "covariance", "contrast_matrix", "ci_lower", "ci_upper", "multiplicity_outputs",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "design_matrix"],
        recomputation_scope="recalculate cell means, main effects, interaction, covariance, intervals, and adjusted decisions",
    ),
    "longitudinal_repeated_measures_v1": MinimumReproducibleResultSchema(
        profile_id="longitudinal_repeated_measures_v1",
        natural_language_name="longitudinal repeated-measures study",
        required_fields=[
            "subject_count_by_arm", "visit_count", "subject_level_slopes_or_sufficient_statistics",
            "within_arm_slope_variance", "effect", "standard_error", "degrees_of_freedom",
            "ci_lower", "ci_upper",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "trajectory_data"],
        recomputation_scope="recalculate arm trajectories, subject slopes, slope contrast, uncertainty, and decision",
    ),
    "survival_analysis_v1": MinimumReproducibleResultSchema(
        profile_id="survival_analysis_v1",
        natural_language_name="time-to-event study",
        required_fields=[
            "subject_duration", "event_status", "arm", "horizon", "rmst_by_arm",
            "rmst_effect", "bootstrap_seed", "bootstrap_repetitions", "contrast_distribution",
            "ci_lower", "ci_upper", "at_risk_counts",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "survival_records"],
        recomputation_scope="recalculate Kaplan-Meier curves, at-risk counts, RMST contrast, bootstrap interval, and decision",
    ),
    "causal_inference_v1": MinimumReproducibleResultSchema(
        profile_id="causal_inference_v1",
        natural_language_name="causal observational analysis",
        required_fields=[
            "exposure", "outcome", "covariates", "fold_assignment", "nuisance_predictions",
            "propensity_values", "pseudo_outcomes", "balance_diagnostics", "overlap_diagnostics",
            "ate", "standard_error", "ci_lower", "ci_upper",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "causal_diagnostics"],
        recomputation_scope="recalculate overlap, balance, nuisance-adjusted ATE, uncertainty, and qualification",
    ),
    "noninferiority_equivalence_v1": MinimumReproducibleResultSchema(
        profile_id="noninferiority_equivalence_v1",
        natural_language_name="noninferiority or equivalence experiment",
        required_fields=[
            "arm_analyzed_n", "arm_estimates", "effect", "standard_error", "ci_lower", "ci_upper",
            "lower_margin", "upper_margin", "margin_unit", "decision_rule", "decision",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "frozen_contract"],
        recomputation_scope="recalculate the contrast and verify whether its complete interval lies within registered margins",
    ),
    "bayesian_inference_v1": MinimumReproducibleResultSchema(
        profile_id="bayesian_inference_v1",
        natural_language_name="Bayesian analysis",
        required_fields=[
            "posterior_draws_or_sufficient_statistics", "prior", "likelihood", "posterior_estimate",
            "credible_lower", "credible_upper", "rope_lower", "rope_upper", "posterior_probability",
            "sampler_settings", "diagnostics", "decision",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "posterior_summary"],
        recomputation_scope="recalculate posterior summaries, credible interval, ROPE probability, diagnostics, and decision",
    ),
    "online_ab_test_v1": MinimumReproducibleResultSchema(
        profile_id="online_ab_test_v1",
        natural_language_name="online randomized A/B test",
        required_fields=[
            "allocation_by_arm", "exposure_by_arm", "admission_by_arm", "analyzed_by_arm",
            "primary_events", "guardrail_events", "risk_differences", "ci_lower", "ci_upper",
            "srm_test", "analysis_window", "decision",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "exposure_flow"],
        recomputation_scope="recalculate allocation diagnostics, primary and guardrail risk differences, intervals, and decisions",
    ),
    "open_generation_human_rating_v1": MinimumReproducibleResultSchema(
        profile_id="open_generation_human_rating_v1",
        natural_language_name="open-ended generation with blinded human ratings",
        required_fields=[
            "item_id", "arm_outputs", "presentation_order", "rater_id", "ratings",
            "item_denominator", "rater_denominator", "paired_differences", "arm_means",
            "agreement_statistic", "ci_lower", "ci_upper", "panel_completeness",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "blinded_rating_panel"],
        recomputation_scope="recalculate paired item effects, arm summaries, agreement, completeness, interval, and decision",
    ),
    "multiplicity_control_v1": MinimumReproducibleResultSchema(
        profile_id="multiplicity_control_v1",
        natural_language_name="multi-outcome confirmatory analysis with multiplicity control",
        required_fields=[
            "confirmatory_family", "outcome_roles", "outcome_denominators", "arm_estimates",
            "effects", "standard_errors", "ci_lower", "ci_upper", "raw_p_values",
            "adjusted_p_values", "adjustment_method", "outcome_decisions", "family_decision",
        ],
        required_artifact_kinds=["formal_output", "evaluation", "statistics", "multiplicity_family"],
        recomputation_scope="recalculate every outcome estimate, raw and adjusted p-value, outcome decision, and family-level interpretation",
    ),
}


def _adapter(
    profile_id: str,
    *,
    methods: list[str],
    results: list[str],
    tables: list[str],
    envelope: str,
    limitations: list[str],
    venues: list[str] | None = None,
) -> ProfilePublicationAdapter:
    figures = [item.figure_type for item in PROFILE_FIGURE_ADAPTERS[profile_id].requirements]
    schema = MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS[profile_id]
    return ProfilePublicationAdapter(
        profile_id=profile_id,
        natural_language_study_design_name=schema.natural_language_name,
        required_methods_fields=_COMMON_METHODS + methods,
        required_results_fields=_COMMON_RESULTS + results,
        required_figures=figures,
        required_tables=tables,
        reporting_checklist=_COMMON_CHECKLIST,
        claim_envelope=envelope,
        forbidden_claims=_COMMON_FORBIDDEN,
        minimum_literature_categories=_COMMON_LITERATURE,
        minimum_reproducibility_fields=schema.required_fields,
        profile_specific_limitations=limitations,
        target_venue_compatibility=venues or ["general empirical methods journal", "domain journal accepting the registered design"],
    )


PROFILE_PUBLICATION_ADAPTERS: dict[str, ProfilePublicationAdapter] = {
    "independent_group_comparison_v1": _adapter(
        "independent_group_comparison_v1",
        methods=["independent allocation and variance assumptions"],
        results=["arm distributions and outcome-specific sample flow"],
        tables=["arm characteristics and primary outcome estimates"],
        envelope="A contrast between independently assigned groups within the registered population and outcome definition.",
        limitations=["Independence and variance assumptions remain design-specific."],
    ),
    "factorial_experiment_v1": _adapter(
        "factorial_experiment_v1",
        methods=["factor coding, design matrix, interaction contrast, and cell allocation"],
        results=["four cell means, two main effects, interaction, and adjusted decisions"],
        tables=["factorial cell summaries", "main and interaction effects"],
        envelope="Main effects and interaction within the registered two-by-two factorial design.",
        limitations=["Main effects are conditional on the registered coding and interaction structure."],
    ),
    "longitudinal_repeated_measures_v1": _adapter(
        "longitudinal_repeated_measures_v1",
        methods=["visit schedule, within-subject covariance, slope definition, and attrition rule"],
        results=["arm trajectories, subject slopes, slope variance, and treatment-by-time effect"],
        tables=["visit and subject flow", "trajectory and slope estimates"],
        envelope="A difference in registered longitudinal change between arms, not a cross-sectional endpoint contrast.",
        limitations=["Trajectory interpretation is bounded to the registered follow-up schedule and missingness rule."],
    ),
    "survival_analysis_v1": _adapter(
        "survival_analysis_v1",
        methods=["time origin, event definition, censoring, horizon, and RMST estimator"],
        results=["events, censoring, at-risk counts, survival curves, and RMST contrast"],
        tables=["event and censoring flow", "RMST estimates"],
        envelope="A time-to-event or restricted-mean contrast under the registered censoring and horizon definitions.",
        limitations=["Independent censoring and follow-up horizon assumptions remain explicit."],
    ),
    "causal_inference_v1": _adapter(
        "causal_inference_v1",
        methods=["causal graph, identification assumptions, nuisance models, overlap, and balance"],
        results=["overlap, covariate balance, adjusted ATE, sensitivity, and qualification"],
        tables=["causal identification assumptions", "adjusted effect and diagnostics"],
        envelope="An adjusted causal estimand only when the frozen identification and diagnostic gates pass.",
        limitations=["Unmeasured confounding and positivity assumptions cannot be established from observed data alone."],
    ),
    "noninferiority_equivalence_v1": _adapter(
        "noninferiority_equivalence_v1",
        methods=["margin justification, direction, analysis population, and interval rule"],
        results=["complete interval relative to both margins and zero"],
        tables=["margin justification", "effect, interval, and boundary decision"],
        envelope="Noninferiority or equivalence only when the complete registered interval satisfies the frozen margin rule.",
        limitations=["Failure to reject superiority is not evidence of equivalence."],
    ),
    "bayesian_inference_v1": _adapter(
        "bayesian_inference_v1",
        methods=["prior, likelihood, sampler, diagnostics, credible interval, and ROPE rule"],
        results=["posterior distribution, credible interval, ROPE probability, and sensitivity"],
        tables=["prior and sampler specification", "posterior summaries and diagnostics"],
        envelope="A posterior statement under the frozen model, prior, likelihood, and decision rule.",
        limitations=["Posterior conclusions are conditional on model and prior assumptions."],
    ),
    "online_ab_test_v1": _adapter(
        "online_ab_test_v1",
        methods=["allocation unit, exposure, admission, analysis window, SRM, and guardrails"],
        results=["allocation/exposure flow, SRM, primary outcome, and guardrail outcomes"],
        tables=["allocation and analysis flow", "primary and guardrail effects"],
        envelope="An intent-to-treat or registered exposure effect within the frozen online experiment window.",
        limitations=["Interference, novelty effects, and repeated exposure remain bounded by the registered design."],
    ),
    "open_generation_human_rating_v1": _adapter(
        "open_generation_human_rating_v1",
        methods=["prompt/item sampling, blinding, presentation order, rating scale, and adjudication"],
        results=["paired item effects, arm means, agreement, and panel completeness"],
        tables=["item and rater flow", "rating effects and agreement"],
        envelope="A blinded paired difference in registered human ratings across the frozen item and rater panels.",
        limitations=["Human ratings remain scale-, panel-, and prompt-population specific."],
    ),
    "multiplicity_control_v1": _adapter(
        "multiplicity_control_v1",
        methods=["confirmatory family, outcome roles, adjustment method, and gatekeeping order"],
        results=["all outcome effects, raw and adjusted p-values, and family-level decision"],
        tables=["confirmatory outcome family", "raw and adjusted decisions"],
        envelope="Outcome-specific and family-level decisions after the prespecified multiplicity procedure.",
        limitations=["Multiplicity control does not establish effect importance or protect unregistered analyses."],
    ),
}


def validate_minimum_reproducible_result(
    *,
    study_id: str,
    profile_id: str,
    fields: list[ReproducibleResultField],
    artifact_kinds: list[str],
) -> MinimumReproducibleResultReport:
    try:
        schema = MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS[profile_id]
    except KeyError as exc:
        raise ValueError(f"no Minimum Reproducible Result Schema for {profile_id}") from exc
    supplied = {item.field for item in fields if item.present}
    missing_fields = sorted(set(schema.required_fields) - supplied)
    missing_kinds = sorted(set(schema.required_artifact_kinds) - set(artifact_kinds))
    complete = not missing_fields and not missing_kinds
    return MinimumReproducibleResultReport(
        study_id=study_id,
        profile_id=profile_id,
        schema_fields=schema.required_fields,
        supplied_fields=sorted(supplied),
        missing_fields=missing_fields,
        supplied_artifact_kinds=sorted(set(artifact_kinds)),
        missing_artifact_kinds=missing_kinds,
        independently_recalculable=complete,
        submission_ready_results_allowed=complete,
    )


def profile_publication_adapter(profile_id: str) -> ProfilePublicationAdapter:
    try:
        return PROFILE_PUBLICATION_ADAPTERS[profile_id]
    except KeyError as exc:
        raise ValueError(f"no Profile Publication Adapter for {profile_id}") from exc


__all__ = [
    "MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS",
    "PROFILE_PUBLICATION_ADAPTERS",
    "MinimumReproducibleResultReport",
    "MinimumReproducibleResultSchema",
    "ProfilePublicationAdapter",
    "ReproducibleResultField",
    "profile_publication_adapter",
    "validate_minimum_reproducible_result",
]
