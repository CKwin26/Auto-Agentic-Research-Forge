"""Typed contracts shared by composable Study Designs and Inference Modules."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from ..models import StrictModel


class StudyDesignMaturity(StrEnum):
    C0_DESCRIBED = "c0_described"
    C1_SCHEMA = "c1_schema"
    C2_DRY_RUN = "c2_dry_run"
    C3_REAL_FIXTURE = "c3_real_fixture"
    C4_INDEPENDENT_REPLAY = "c4_independent_replay"
    C5_MULTI_PROJECT = "c5_multi_project"


class PaperPackageArtifact(StrictModel):
    ordinal: int = Field(ge=1, le=18)
    name: str = Field(min_length=3)
    relative_path: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    present: bool


class ProfilePaperPackage(StrictModel):
    schema_version: int = 1
    package_id: str = Field(min_length=3)
    profile_id: str = Field(min_length=3)
    study_id: str = Field(min_length=3)
    case_kind: Literal["normal", "null_or_weak", "invalid"]
    artifacts: list[PaperPackageArtifact]
    scientific_verdict: Literal[
        "supported", "refuted", "mixed", "inconclusive", "unverifiable"
    ]
    automatic_acceptance: Literal["pass", "fail", "incomplete"]
    independent_recalculation_passed: bool
    paper_audit_passed: bool
    formal_workflow_completed: bool = False
    canonical_stage_four_completed: bool = False
    workflow_receipts: dict[str, bool] = Field(default_factory=dict)
    clean_room_replay_passed: bool = False
    real_nonfixture_case: bool = False
    external_independent_reproduction: bool = False

    @property
    def complete(self) -> bool:
        return len(self.artifacts) == 18 and all(item.present and item.sha256 for item in self.artifacts)

    @property
    def derived_maturity(self) -> StudyDesignMaturity:
        from .workflow_evidence import REQUIRED_C3_WORKFLOW_RECEIPTS

        if (
            self.external_independent_reproduction
            and self.complete and self.automatic_acceptance == "pass"
        ):
            return StudyDesignMaturity.C5_MULTI_PROJECT
        if (
            self.real_nonfixture_case and self.clean_room_replay_passed
            and self.complete and self.automatic_acceptance == "pass"
        ):
            return StudyDesignMaturity.C4_INDEPENDENT_REPLAY
        if (
            self.complete and self.automatic_acceptance == "pass"
            and self.independent_recalculation_passed
            and self.paper_audit_passed
            and self.formal_workflow_completed
            and self.canonical_stage_four_completed
            and all(
                self.workflow_receipts.get(required) is True
                for required in REQUIRED_C3_WORKFLOW_RECEIPTS
            )
        ):
            return StudyDesignMaturity.C3_REAL_FIXTURE
        return StudyDesignMaturity.C2_DRY_RUN


class HumanProfileReviewRecord(StrictModel):
    schema_version: int = 1
    review_id: str = Field(pattern=r"^profile-review-[a-f0-9]{16}$")
    package_id: str = Field(min_length=3)
    profile_id: str = Field(min_length=3)
    reviewer: str = Field(min_length=1)
    scientific_question_reasonable: bool
    experimental_design_reasonable: bool
    statistics_correct: bool
    result_expression_accurate: bool
    conclusion_within_evidence: bool
    paper_readable: bool
    figures_acceptable: bool
    needs_revision: bool
    decision: Literal["accepted", "rejected"]
    comments: str = ""
    machine_verdict_at_review: str = Field(min_length=1)
    created_at: str


class RepairClass(StrEnum):
    DETERMINISTIC_DERIVATION = "deterministic_derivation"
    PROFILE_DEFAULT = "profile_default"
    OWNER_SCIENTIFIC_DECISION = "owner_scientific_decision"
    UNRESOLVABLE_BLOCKER = "unresolvable_blocker"


class ComponentReference(StrictModel):
    id: str = Field(min_length=3)
    version: str = Field(min_length=1)
    extension: dict[str, Any] = Field(default_factory=dict)


class UnitStructureSpec(StrictModel):
    row_unit: str = Field(min_length=1)
    observation_unit: str = Field(min_length=1)
    assignment_unit: str = Field(min_length=1)
    analysis_unit: str = Field(min_length=1)
    variance_unit: str = Field(min_length=1)
    independent_unit: str = Field(min_length=1)
    cluster_unit: str | None = None
    repeated_measure_unit: str | None = None


class AllocationSpec(StrictModel):
    mechanism: Literal[
        "randomized", "nonrandomized", "observational", "unknown"
    ]
    evidence: str = Field(min_length=1)
    concealment: str | None = None


class ArmSpec(StrictModel):
    arm_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    role: Literal["control", "treatment", "factorial_cell"]
    definition: str = Field(min_length=1)


class OutcomeSpec(StrictModel):
    outcome_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: Literal["continuous", "binary", "time_to_event"]
    field: str = Field(min_length=1)
    role: Literal["primary", "secondary", "exploratory"]
    beneficial_direction: Literal["higher", "lower"]
    event_value: str | int | bool | None = None

    @model_validator(mode="after")
    def binary_outcome_has_event(self) -> "OutcomeSpec":
        if self.kind == "binary" and self.event_value is None:
            raise ValueError("binary outcome requires event_value")
        if self.kind == "continuous" and self.event_value is not None:
            raise ValueError("continuous outcome cannot define event_value")
        return self


class EstimandSpec(StrictModel):
    estimand_id: str = Field(min_length=1)
    outcome_id: str = Field(min_length=1)
    treatment_arm_id: str = Field(min_length=1)
    control_arm_id: str = Field(min_length=1)
    effect_measure: Literal[
        "mean_difference",
        "paired_mean_difference",
        "risk_difference",
        "risk_ratio",
        "odds_ratio",
        "slope_difference",
        "rmst_difference",
        "average_treatment_effect",
    ]
    analysis_population: Literal["complete_case", "all_observed"]


class EstimatorSpec(StrictModel):
    estimator_id: Literal[
        "welch_mean_difference_v1",
        "wald_risk_difference_v1",
        "log_risk_ratio_v1",
        "log_odds_ratio_v1",
        "factorial_ols_hc2_v1",
        "mean_subject_slope_difference_v1",
        "kaplan_meier_rmst_difference_v1",
        "cross_fitted_aipw_ate_v1",
        "paired_item_mean_difference_v1",
    ]
    equal_variance_assumed: Literal[False] = False


class InferencePlan(StrictModel):
    method: str = Field(min_length=1)
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    sidedness: Literal["two_sided", "one_sided_lower", "one_sided_upper"] = (
        "two_sided"
    )
    seed: int | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)


class MissingnessPlan(StrictModel):
    policy: Literal[
        "complete_case",
        "fail_if_any",
        "explicit_inconclusive",
        "minimum_observed_trajectory",
        "right_censoring",
    ]
    denominator_rule: str = Field(min_length=1)
    exclusion_reasons_required: bool = True


class MultiplicityPlan(StrictModel):
    method: Literal["no_correction", "bonferroni", "holm", "benjamini_hochberg"]
    family_id: str = Field(min_length=1)
    hypothesis_ids: list[str] = Field(min_length=1)
    alpha_or_q: float = Field(gt=0, lt=1)
    ordered: bool = False
    gatekeeping_rule: str | None = None
    registered_before_results: bool = True
    exploratory_hypothesis_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def registered_families_are_separate(self) -> "MultiplicityPlan":
        if len(self.hypothesis_ids) != len(set(self.hypothesis_ids)):
            raise ValueError("confirmatory hypothesis identifiers must be unique")
        if set(self.hypothesis_ids).intersection(self.exploratory_hypothesis_ids):
            raise ValueError("exploratory hypotheses cannot enter the confirmatory family")
        if not self.registered_before_results:
            raise ValueError("confirmatory family must be frozen before formal results")
        return self


class DecisionRuleSpec(StrictModel):
    mode: Literal["superiority", "noninferiority", "equivalence"]
    effect_measure: str = Field(min_length=1)
    beneficial_direction: Literal["higher", "lower"]
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    margin: float | None = None
    margin_unit: str | None = None
    margin_provenance: str | None = None
    lower_margin: float | None = None
    upper_margin: float | None = None
    owner_approved: bool = False

    @model_validator(mode="after")
    def margins_match_mode(self) -> "DecisionRuleSpec":
        if self.mode == "noninferiority":
            if self.margin is None or self.margin <= 0:
                raise ValueError("noninferiority requires a positive margin")
            if not self.margin_unit or not self.margin_provenance:
                raise ValueError(
                    "noninferiority requires margin unit and provenance"
                )
            if not self.owner_approved:
                raise ValueError("noninferiority margin requires owner approval")
        elif self.mode == "equivalence":
            if self.lower_margin is None or self.upper_margin is None:
                raise ValueError("equivalence requires lower and upper margins")
            if self.lower_margin >= self.upper_margin:
                raise ValueError("equivalence lower margin must be below upper")
            if not self.margin_unit or not self.margin_provenance:
                raise ValueError("equivalence margins require unit and provenance")
            if not self.owner_approved:
                raise ValueError("equivalence margins require owner approval")
        return self


class FactorSpec(StrictModel):
    """One frozen two-level factor in the first factorial kernel."""

    factor_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    field: str = Field(min_length=1)
    levels: list[str] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def levels_are_distinct(self) -> "FactorSpec":
        if len(set(self.levels)) != 2:
            raise ValueError("factor levels must be distinct")
        return self


class FactorialCellSpec(StrictModel):
    arm_id: str = Field(min_length=1)
    levels: dict[str, str] = Field(min_length=2)


class FactorialContrastSpec(StrictModel):
    contrast_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    outcome_id: str = Field(min_length=1)
    kind: Literal["main_effect", "interaction"]
    factor_ids: list[str] = Field(min_length=1, max_length=2)
    role: Literal["primary", "secondary", "exploratory"]
    beneficial_direction: Literal["higher", "lower"]

    @model_validator(mode="after")
    def arity_matches_kind(self) -> "FactorialContrastSpec":
        expected = 1 if self.kind == "main_effect" else 2
        if len(self.factor_ids) != expected:
            raise ValueError(
                f"{self.kind} requires exactly {expected} factor identifier(s)"
            )
        if len(set(self.factor_ids)) != len(self.factor_ids):
            raise ValueError("contrast factor identifiers must be distinct")
        return self


class FactorialDesignSpec(StrictModel):
    """Frozen 2x2 independent-unit factorial extension."""

    factors: list[FactorSpec] = Field(min_length=2, max_length=2)
    cells: list[FactorialCellSpec] = Field(min_length=4, max_length=4)
    contrasts: list[FactorialContrastSpec] = Field(min_length=1)
    coding: Literal["effect_coding"] = "effect_coding"
    covariance: Literal["hc2_robust"] = "hc2_robust"
    minimum_observed_per_cell: int = Field(default=2, ge=2)


class LongitudinalDesignSpec(StrictModel):
    """Frozen linear-trajectory extension for repeated measurements."""

    subject_id_field: str = Field(default="subject_id", min_length=1)
    time_field: str = Field(min_length=1)
    time_unit: str = Field(min_length=1)
    planned_time_points: list[float] = Field(min_length=3)
    minimum_observed_time_points: int = Field(default=3, ge=3)
    minimum_subjects_per_arm: int = Field(default=3, ge=2)
    trajectory_model: Literal["subject_specific_linear_slope"] = (
        "subject_specific_linear_slope"
    )
    covariance: Literal["welch_between_subject_slopes"] = (
        "welch_between_subject_slopes"
    )

    @model_validator(mode="after")
    def time_grid_is_identifiable(self) -> "LongitudinalDesignSpec":
        if len(set(self.planned_time_points)) != len(self.planned_time_points):
            raise ValueError("planned longitudinal time points must be distinct")
        if self.minimum_observed_time_points > len(self.planned_time_points):
            raise ValueError(
                "minimum observed time points cannot exceed the planned time grid"
            )
        return self


class SurvivalDesignSpec(StrictModel):
    """Frozen right-censored, two-arm time-to-event extension."""

    subject_id_field: str = Field(default="subject_id", min_length=1)
    duration_field: str = Field(min_length=1)
    event_field: str = Field(min_length=1)
    event_value: str | int | bool
    censor_value: str | int | bool
    time_origin: str = Field(min_length=1)
    time_unit: str = Field(min_length=1)
    restriction_time: float = Field(gt=0)
    minimum_subjects_per_arm: int = Field(default=5, ge=3)
    minimum_events_per_arm: int = Field(default=2, ge=1)
    censoring_rule: str = Field(min_length=1)
    estimator: Literal["kaplan_meier_rmst"] = "kaplan_meier_rmst"
    bootstrap_repetitions: int = Field(default=1000, ge=200, le=10000)
    bootstrap_seed: int = 20260804

    @model_validator(mode="after")
    def event_and_censor_are_distinct(self) -> "SurvivalDesignSpec":
        if self.event_value == self.censor_value:
            raise ValueError("event and censor values must be distinct")
        return self


class CausalGraphEdge(StrictModel):
    """One directed edge in the owner-frozen causal graph."""

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)

    @model_validator(mode="after")
    def edge_is_not_self_referential(self) -> "CausalGraphEdge":
        if self.source == self.target:
            raise ValueError("causal graph cannot contain a self-edge")
        return self


class OnlineABDesignSpec(StrictModel):
    """Frozen exposure and stopping semantics for an online controlled test."""

    experiment_id: str = Field(min_length=1)
    randomization_unit_field: str = Field(default="subject_id", min_length=1)
    exposure_id_field: str = Field(default="exposure_id", min_length=1)
    exposure_timestamp_field: str = Field(default="exposure_timestamp", min_length=1)
    exposure_definition: str = Field(min_length=1)
    analysis_population: Literal["first_valid_exposure_only"] = (
        "first_valid_exposure_only"
    )
    one_exposure_per_randomization_unit: Literal[True] = True
    window_start: str = Field(min_length=1)
    window_end: str = Field(min_length=1)
    planned_total_exposures: int = Field(ge=100)
    minimum_exposures_per_arm: int = Field(ge=20)
    allocation_ratio: dict[str, float] = Field(min_length=2, max_length=2)
    srm_alpha: float = Field(default=0.01, gt=0.0, lt=0.5)
    analysis_mode: Literal["fixed_horizon_no_peeking"] = (
        "fixed_horizon_no_peeking"
    )
    stopping_rule: str = Field(min_length=1)
    guardrail_outcome_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def allocation_is_complete(self) -> "OnlineABDesignSpec":
        if any(value <= 0 for value in self.allocation_ratio.values()):
            raise ValueError("online A/B allocation ratios must be positive")
        if abs(sum(self.allocation_ratio.values()) - 1.0) > 1e-9:
            raise ValueError("online A/B allocation ratios must sum to one")
        if self.minimum_exposures_per_arm * 2 > self.planned_total_exposures:
            raise ValueError("per-arm minimum cannot exceed the planned exposure total")
        if len(self.guardrail_outcome_ids) != len(set(self.guardrail_outcome_ids)):
            raise ValueError("online A/B guardrail identifiers must be unique")
        return self


class HumanRatingDesignSpec(StrictModel):
    """Frozen blindness, rubric, panel, and aggregation rules for open outputs."""

    item_id_field: str = Field(default="item_id", min_length=1)
    output_id_field: str = Field(default="output_id", min_length=1)
    output_hash_field: str = Field(default="output_sha256", min_length=1)
    rater_id_field: str = Field(default="rater_id", min_length=1)
    blind_label_field: str = Field(default="blind_label", min_length=1)
    presentation_order_field: str = Field(
        default="presentation_order", min_length=1
    )
    rubric_id: str = Field(min_length=1)
    rating_scale_min: int
    rating_scale_max: int
    rater_panel_ids: list[str] = Field(min_length=2)
    raters_per_output: int = Field(ge=2)
    condition_label_map: dict[str, str] = Field(min_length=2, max_length=2)
    blindness: Literal["condition_blinded"] = "condition_blinded"
    presentation_randomization: Literal["randomized_per_rater"] = (
        "randomized_per_rater"
    )
    aggregation: Literal["mean_rating_per_item_and_arm"] = (
        "mean_rating_per_item_and_arm"
    )
    reliability_method: Literal["icc_2_1_absolute_agreement"] = (
        "icc_2_1_absolute_agreement"
    )
    minimum_reliability: float = Field(ge=-1.0, le=1.0)
    adjudication_rule: str = Field(min_length=1)

    @model_validator(mode="after")
    def rating_protocol_is_identifiable(self) -> "HumanRatingDesignSpec":
        if self.rating_scale_max <= self.rating_scale_min:
            raise ValueError("human-rating scale maximum must exceed its minimum")
        if len(set(self.rater_panel_ids)) != len(self.rater_panel_ids):
            raise ValueError("human-rating panel identifiers must be unique")
        if self.raters_per_output != len(self.rater_panel_ids):
            raise ValueError("raters_per_output must equal the frozen panel size")
        if len(set(self.condition_label_map.values())) != 2:
            raise ValueError("blind condition labels must be distinct")
        return self


class CausalDesignSpec(StrictModel):
    """Frozen point-treatment observational backdoor design."""

    subject_id_field: str = Field(default="subject_id", min_length=1)
    treatment_field: str = Field(min_length=1)
    treatment_value: str | int | bool
    control_value: str | int | bool
    treatment_definition: str = Field(min_length=1)
    control_definition: str = Field(min_length=1)
    outcome_field: str = Field(min_length=1)
    causal_graph_edges: list[CausalGraphEdge] = Field(min_length=1)
    adjustment_set: list[str] = Field(min_length=1)
    forbidden_adjustment_fields: list[str] = Field(default_factory=list)
    identification_strategy: Literal["backdoor_adjustment"] = "backdoor_adjustment"
    estimand: Literal["ate"] = "ate"
    estimator: Literal["cross_fitted_aipw"] = "cross_fitted_aipw"
    cross_fitting_folds: int = Field(default=5, ge=2, le=10)
    fold_seed: int = 20260805
    propensity_lower_bound: float = Field(default=0.05, gt=0, lt=0.5)
    propensity_upper_bound: float = Field(default=0.95, gt=0.5, lt=1)
    minimum_overlap_fraction: float = Field(default=0.9, gt=0, le=1)
    minimum_subjects_per_arm: int = Field(default=20, ge=5)
    exchangeability_assumption: str = Field(min_length=1)
    consistency_assumption: str = Field(min_length=1)
    no_interference_assumption: str = Field(min_length=1)

    @model_validator(mode="after")
    def causal_design_is_identifiable_on_its_declared_terms(self) -> "CausalDesignSpec":
        if self.treatment_value == self.control_value:
            raise ValueError("treatment and control values must be distinct")
        if self.propensity_lower_bound >= self.propensity_upper_bound:
            raise ValueError("propensity bounds must be ordered")
        if len(set(self.adjustment_set)) != len(self.adjustment_set):
            raise ValueError("adjustment-set fields must be unique")
        if set(self.adjustment_set).intersection(self.forbidden_adjustment_fields):
            raise ValueError("adjustment set contains a frozen forbidden field")

        nodes = {
            node
            for edge in self.causal_graph_edges
            for node in (edge.source, edge.target)
        }
        required = {self.treatment_field, self.outcome_field, *self.adjustment_set}
        if not required.issubset(nodes):
            raise ValueError("causal graph must contain treatment, outcome, and adjustment nodes")

        children: dict[str, set[str]] = {node: set() for node in nodes}
        indegree = {node: 0 for node in nodes}
        for edge in self.causal_graph_edges:
            if edge.target not in children[edge.source]:
                children[edge.source].add(edge.target)
                indegree[edge.target] += 1
        frontier = [node for node, degree in indegree.items() if degree == 0]
        visited = 0
        while frontier:
            node = frontier.pop()
            visited += 1
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    frontier.append(child)
        if visited != len(nodes):
            raise ValueError("causal graph must be acyclic")

        # A declared adjustment variable must be temporally upstream of the
        # treatment in the frozen graph; descendants are not valid backdoor
        # controls in this intentionally narrow first profile.
        parents_of_treatment = {
            edge.source
            for edge in self.causal_graph_edges
            if edge.target == self.treatment_field
        }
        if not set(self.adjustment_set).issubset(parents_of_treatment):
            raise ValueError("every adjustment field must be a declared treatment parent")
        parents_of_outcome = {
            edge.source
            for edge in self.causal_graph_edges
            if edge.target == self.outcome_field
        }
        if not set(self.adjustment_set).issubset(parents_of_outcome):
            raise ValueError("every adjustment field must be a declared outcome parent")
        return self


class OwnerDecisionRequirement(StrictModel):
    field_path: str = Field(min_length=1)
    question: str = Field(min_length=1)
    consequence: str = Field(min_length=1)


class CompletionIssue(StrictModel):
    issue_id: str = Field(min_length=3)
    field_path: str = Field(min_length=1)
    severity: Literal["info", "warning", "error", "critical"]
    repair_class: RepairClass
    expected: str = Field(min_length=1)
    observed: Any | None = None
    proposed_value: Any | None = None
    provenance: str = Field(min_length=1)
    owner_approval_required: bool

    @model_validator(mode="after")
    def authority_is_consistent(self) -> "CompletionIssue":
        if self.repair_class is RepairClass.DETERMINISTIC_DERIVATION:
            if self.proposed_value is None or self.owner_approval_required:
                raise ValueError("deterministic completion must be automatic")
        if self.repair_class is RepairClass.PROFILE_DEFAULT:
            if self.proposed_value is None or not self.owner_approval_required:
                raise ValueError("profile defaults require bundled approval")
        if self.repair_class is RepairClass.OWNER_SCIENTIFIC_DECISION:
            if not self.owner_approval_required:
                raise ValueError("scientific decisions require owner approval")
        if self.repair_class is RepairClass.UNRESOLVABLE_BLOCKER:
            if self.severity not in {"error", "critical"}:
                raise ValueError("unresolvable blockers must be errors")
        return self


class StudyDesignCompletionPatch(StrictModel):
    schema_version: int = 1
    study_design_id: str
    study_design_version: str
    issues: list[CompletionIssue] = Field(default_factory=list)

    @property
    def deterministic_updates(self) -> list[CompletionIssue]:
        return [
            item for item in self.issues
            if item.repair_class is RepairClass.DETERMINISTIC_DERIVATION
        ]

    @property
    def owner_confirmation_bundle(self) -> list[CompletionIssue]:
        return [item for item in self.issues if item.owner_approval_required]

    @property
    def blockers(self) -> list[CompletionIssue]:
        return [
            item for item in self.issues
            if item.repair_class is RepairClass.UNRESOLVABLE_BLOCKER
        ]


class ClaimEnvelope(StrictModel):
    schema_version: int = 1
    study_design_name: str
    arm_definitions: list[str]
    allocation_verified_randomized: bool
    observation_unit: str
    analysis_unit: str
    independent_unit: str
    primary_outcomes: list[str]
    secondary_outcomes: list[str]
    denominator: int
    missing_count: int
    excluded_count: int
    effect_estimates: list[str]
    uncertainty_method: str
    multiplicity_method: str
    scientific_verdict: str
    permitted_claims: list[str]
    prohibited_claims: list[str]
    generalization_boundary: str
    reproduction_materials: list[str]
    design_details: dict[str, Any] = Field(default_factory=dict)


class AnalysisPlan(StrictModel):
    schema_version: int = 1
    study_design_id: str
    study_design_version: str
    unit_structure: UnitStructureSpec
    allocation: AllocationSpec
    arms: list[ArmSpec]
    outcomes: list[OutcomeSpec]
    estimands: list[EstimandSpec]
    estimator_plan: dict[str, EstimatorSpec]
    inference_plan: dict[str, InferencePlan]
    missingness: MissingnessPlan
    multiplicity: MultiplicityPlan
    decision_rules: dict[str, DecisionRuleSpec]
    claim_boundary: dict[str, Any]
    factorial: FactorialDesignSpec | None = None
    longitudinal: LongitudinalDesignSpec | None = None
    survival: SurvivalDesignSpec | None = None
    causal: CausalDesignSpec | None = None
    online_ab: OnlineABDesignSpec | None = None
    human_rating: HumanRatingDesignSpec | None = None


class OutcomeEvaluation(StrictModel):
    outcome_id: str
    kind: Literal["continuous", "binary", "time_to_event"]
    eligible: bool
    arm_statistics: dict[str, dict[str, float | int | None]]
    effect_measure: str
    effect: float | None = None
    standard_error: float | None = None
    confidence_interval: tuple[float, float] | None = None
    p_value: float | None = None
    degrees_of_freedom: float | None = None
    missing_count: int = 0
    excluded_count: int = 0
    denominator: int = 0
    raw_p_value: float | None = None
    adjusted_p_value: float | None = None
    decision: Literal[
        "supported", "refuted", "inconclusive", "unverifiable"
    ] = "inconclusive"
    details: dict[str, Any] = Field(default_factory=dict)


class StudyDesignEvaluation(StrictModel):
    schema_version: int = 1
    study_design_id: str
    study_design_version: str
    eligible: bool
    qualification_checks: dict[str, bool]
    outcomes: list[OutcomeEvaluation]
    primary_decision: Literal[
        "supported", "refuted", "mixed", "inconclusive", "unverifiable"
    ]
    limitations: list[str] = Field(default_factory=list)


__all__ = [
    "AllocationSpec",
    "AnalysisPlan",
    "ArmSpec",
    "CausalDesignSpec",
    "CausalGraphEdge",
    "ClaimEnvelope",
    "CompletionIssue",
    "ComponentReference",
    "DecisionRuleSpec",
    "EstimandSpec",
    "EstimatorSpec",
    "FactorSpec",
    "FactorialCellSpec",
    "FactorialContrastSpec",
    "FactorialDesignSpec",
    "InferencePlan",
    "LongitudinalDesignSpec",
    "SurvivalDesignSpec",
    "MissingnessPlan",
    "MultiplicityPlan",
    "OutcomeEvaluation",
    "OutcomeSpec",
    "OnlineABDesignSpec",
    "OwnerDecisionRequirement",
    "PaperPackageArtifact",
    "ProfilePaperPackage",
    "HumanProfileReviewRecord",
    "HumanRatingDesignSpec",
    "RepairClass",
    "StudyDesignCompletionPatch",
    "StudyDesignEvaluation",
    "StudyDesignMaturity",
    "UnitStructureSpec",
]
