from __future__ import annotations

"""Cross-domain scientific validity gates for Stages 2, 3, and 4.

The checks in this module are deliberately deterministic.  They do not decide
whether a hypothesis is true; they decide what a frozen design or evidence
bundle is allowed to claim and where a repair must be routed.
"""

import re
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


class ValidityStage(StrEnum):
    DESIGN = "stage2_design"
    EVIDENCE = "stage3_evidence"
    MANUSCRIPT = "stage4_manuscript"


class ValiditySeverity(StrEnum):
    BLOCKING = "blocking"
    MAJOR = "major"
    WARNING = "warning"


class ClaimTier(StrEnum):
    DESCRIPTIVE = "descriptive"
    IN_DISTRIBUTION_ASSOCIATION = "in_distribution_association"
    CONTROLLED_EFFECT = "controlled_effect"
    GENERALIZABLE_EFFECT = "generalizable_effect"


class IdentificationTarget(StrEnum):
    BUNDLED_INTERVENTION = "bundled_intervention_effect"
    CURRICULUM_ORDERING = "curriculum_ordering_effect"
    RULE_SUPERVISION = "rule_supervision_effect"
    FEATURE_REPRESENTATION = "feature_representation_effect"
    GENERALIZATION = "generalization_effect"


class OutcomeStructure(StrEnum):
    OTHER = "other"
    CONTINUOUS = "continuous"
    BINARY_CLASSIFICATION = "binary_classification"
    MULTICLASS_CLASSIFICATION = "multiclass_classification"
    RANKING = "ranking"
    GENERATIVE = "generative"


class DataProvenanceMode(StrEnum):
    UNKNOWN = "unknown"
    REAL_WORLD = "real_world"
    SYNTHETIC = "synthetic"
    MIXED = "mixed"


class ScientificValidityContract(StrictModel):
    schema_version: int = 2
    enforcement_level: str = Field(
        default="exploratory", pattern=r"^(exploratory|confirmatory)$"
    )
    requested_claim_tier: ClaimTier = ClaimTier.CONTROLLED_EFFECT
    identification_target: IdentificationTarget = (
        IdentificationTarget.BUNDLED_INTERVENTION
    )
    mechanism_claims: list[str] = Field(default_factory=list)
    arm_variation_dimensions: list[str] = Field(default_factory=list)
    invariant_dimensions: list[str] = Field(default_factory=list)
    matched_control_ids: list[str] = Field(default_factory=list)
    metric_lower_bound: float | None = None
    metric_upper_bound: float | None = None
    expected_baseline: float | None = None
    minimum_meaningful_effect: float | None = None
    expected_information_value: str | None = None
    sample_adequacy_basis: str | None = None
    independence_justification: str | None = None
    metric_direction: str = Field(default="maximize", pattern=r"^(maximize|minimize)$")
    threshold_basis: str | None = None
    construct_label: str | None = None
    instrument_validation: list[str] = Field(default_factory=list)
    target_rule_disclosure: list[str] = Field(default_factory=list)
    model_specification: list[str] = Field(default_factory=list)
    train_evaluation_separation: list[str] = Field(default_factory=list)
    baseline_ids: list[str] = Field(default_factory=list)
    ablation_ids: list[str] = Field(default_factory=list)
    treatment_components: list[str] = Field(default_factory=list)
    manipulation_checks: list[str] = Field(default_factory=list)
    input_integrity_checks: list[str] = Field(default_factory=list)
    uncertainty_plan: list[str] = Field(default_factory=list)
    scoring_contract: list[str] = Field(default_factory=list)
    diagnostic_followups: list[str] = Field(default_factory=list)
    outcome_structure: OutcomeStructure = OutcomeStructure.OTHER
    data_provenance_mode: DataProvenanceMode = DataProvenanceMode.UNKNOWN
    feature_target_relationships: list[dict[str, Any]] = Field(default_factory=list)
    feature_ablation_ids: list[str] = Field(default_factory=list)
    data_generator_disclosure: list[str] = Field(default_factory=list)
    behavioral_decomposition_plan: list[str] = Field(default_factory=list)
    heterogeneity_plan: list[str] = Field(default_factory=list)
    robust_inference_plan: list[str] = Field(default_factory=list)
    threshold_sensitivity_plan: list[str] = Field(default_factory=list)
    boundary_analysis_plan: list[str] = Field(default_factory=list)
    seed_role_plan: list[str] = Field(default_factory=list)
    safeguard_metric_names: list[str] = Field(default_factory=list)
    reproducibility_release_plan: list[str] = Field(default_factory=list)
    preferred_metric_names: dict[str, str] = Field(default_factory=dict)
    required_manuscript_disclosures: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bounds(self) -> ScientificValidityContract:
        if (
            self.metric_lower_bound is not None
            and self.metric_upper_bound is not None
            and self.metric_lower_bound >= self.metric_upper_bound
        ):
            raise ValueError("metric_lower_bound must be below metric_upper_bound")
        return self


class ScientificValidityFinding(StrictModel):
    code: str
    stage: ValidityStage
    severity: ValiditySeverity
    message: str
    required_action: str
    repair_route: str
    evidence: list[str] = Field(default_factory=list)


class ScientificValidityReport(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    stage: ValidityStage
    passed: bool
    maximum_claim_tier: ClaimTier
    findings: list[ScientificValidityFinding]


def _finding(
    code: str,
    stage: ValidityStage,
    severity: ValiditySeverity,
    message: str,
    required_action: str,
    repair_route: str,
    *evidence: str,
) -> ScientificValidityFinding:
    return ScientificValidityFinding(
        code=code,
        stage=stage,
        severity=severity,
        message=message,
        required_action=required_action,
        repair_route=repair_route,
        evidence=list(evidence),
    )


def _missing(values: list[str], required_tokens: tuple[str, ...]) -> list[str]:
    corpus = " ".join(values).casefold()
    return [token for token in required_tokens if token not in corpus]


def _claim_tier_min(left: ClaimTier, right: ClaimTier) -> ClaimTier:
    return min(left, right, key=list(ClaimTier).index)


def _normalized_dimensions(values: list[str]) -> set[str]:
    aliases = {
        "task": "task_coverage",
        "tasks": "task_coverage",
        "task mix": "task_coverage",
        "label": "label_rule",
        "labels": "label_rule",
        "target": "label_rule",
        "target rule": "label_rule",
        "feature": "feature_representation",
        "features": "feature_representation",
        "sample": "sample_composition",
        "samples": "sample_composition",
        "data distribution": "sample_composition",
        "objective": "training_objective",
        "loss": "training_objective",
        "order": "sample_order",
        "ordering": "sample_order",
        "difficulty": "difficulty_schedule",
        "stages": "training_stages",
    }
    normalized: set[str] = set()
    for value in values:
        token = re.sub(r"[\s-]+", "_", str(value).strip().casefold())
        normalized.add(aliases.get(token.replace("_", " "), token))
    return normalized


def summarize_classification_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return deterministic conditional diagnostics when rows expose truth/prediction.

    Adapters may use common field aliases, but the function never infers labels
    from aggregate accuracy or model scores.
    """

    truth_fields = (
        "truth",
        "gold",
        "label",
        "target",
        "gold_label",
        "y_true",
        "gold_patient_action",
    )
    prediction_fields = (
        "prediction",
        "predicted",
        "predicted_label",
        "y_pred",
        "predicted_patient_action",
    )

    def first_value(row: dict[str, Any], names: tuple[str, ...]) -> Any:
        for name in names:
            if name in row:
                return row[name]
        return None

    parsed: list[tuple[int, int, str]] = []
    for row in rows:
        truth = first_value(row, truth_fields)
        prediction = first_value(row, prediction_fields)
        truth_is_binary = isinstance(truth, (bool, int)) and truth in (0, 1)
        prediction_is_binary = isinstance(prediction, (bool, int)) and prediction in (
            0,
            1,
        )
        if not truth_is_binary or not prediction_is_binary:
            continue
        parsed.append(
            (
                int(bool(truth)),
                int(bool(prediction)),
                str(
                    row.get("task")
                    or row.get("subgroup")
                    or row.get("scenario_family")
                    or row.get("family")
                    or "all"
                ),
            )
        )
    if not parsed:
        return {}

    def diagnostics(items: list[tuple[int, int, str]]) -> dict[str, Any]:
        tp = sum(truth == 1 and pred == 1 for truth, pred, _ in items)
        tn = sum(truth == 0 and pred == 0 for truth, pred, _ in items)
        fp = sum(truth == 0 and pred == 1 for truth, pred, _ in items)
        fn = sum(truth == 1 and pred == 0 for truth, pred, _ in items)
        positives = tp + fn
        negatives = tn + fp
        sensitivity = tp / positives if positives else None
        specificity = tn / negatives if negatives else None
        balanced = (
            (sensitivity + specificity) / 2
            if sensitivity is not None and specificity is not None
            else None
        )
        mcc_denominator = ((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)) ** 0.5
        mcc = ((tp * tn) - (fp * fn)) / mcc_denominator if mcc_denominator else None
        return {
            "denominator": len(items),
            "class_prevalence": {
                "positive": positives,
                "negative": negatives,
                "positive_rate": positives / len(items),
            },
            "confusion_matrix": {
                "true_positive": tp,
                "true_negative": tn,
                "false_positive": fp,
                "false_negative": fn,
            },
            "sensitivity": sensitivity,
            "specificity": specificity,
            "balanced_accuracy": balanced,
            "matthews_correlation_coefficient": mcc,
            "action_rate": (tp + fp) / len(items),
        }

    per_task: dict[str, Any] = {}
    for task in sorted({task for _, _, task in parsed}):
        per_task[task] = diagnostics([item for item in parsed if item[2] == task])
    return {
        **diagnostics(parsed),
        "per_task": per_task,
    }


def audit_design_validity(
    contract: ScientificValidityContract,
) -> ScientificValidityReport:
    findings: list[ScientificValidityFinding] = []
    strict = contract.enforcement_level == "confirmatory"
    ceiling = contract.requested_claim_tier

    if (
        contract.expected_baseline is not None
        and contract.minimum_meaningful_effect is not None
    ):
        target = (
            contract.expected_baseline + contract.minimum_meaningful_effect
            if contract.metric_direction == "maximize"
            else contract.expected_baseline - contract.minimum_meaningful_effect
        )
        impossible = (
            contract.metric_upper_bound is not None
            and target > contract.metric_upper_bound
        ) or (
            contract.metric_lower_bound is not None
            and target < contract.metric_lower_bound
        )
        if impossible:
            findings.append(
                _finding(
                    "SV-DESIGN-THRESHOLD-UNREACHABLE",
                    ValidityStage.DESIGN,
                    ValiditySeverity.BLOCKING,
                    "The success threshold is outside the reachable metric scale.",
                    "Revise the effect threshold from a pilot or a scale-bound calculation before freezing.",
                    "stage2_protocol_vnext",
                    f"derived_target={target}",
                )
            )

    if not contract.threshold_basis:
        findings.append(
            _finding(
                "SV-DESIGN-THRESHOLD-BASIS-MISSING",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                "The minimum effect has no pilot, literature, or scale-based justification.",
                "Record the threshold basis and its source before confirmatory freeze.",
                "stage2_protocol_vnext",
            )
        )

    if contract.identification_target is IdentificationTarget.CURRICULUM_ORDERING:
        varied = _normalized_dimensions(contract.arm_variation_dimensions)
        allowed = {
            "sample_order",
            "difficulty_schedule",
            "training_stages",
        }
        confounded = sorted(varied - allowed)
        missing_invariants = sorted(
            {
                "task_coverage",
                "label_rule",
                "feature_representation",
                "sample_composition",
                "training_objective",
            }
            - _normalized_dimensions(contract.invariant_dimensions)
        )
        if confounded or missing_invariants or not contract.matched_control_ids:
            ceiling = _claim_tier_min(ceiling, ClaimTier.CONTROLLED_EFFECT)
            findings.append(
                _finding(
                    "SV-DESIGN-CURRICULUM-EFFECT-NOT-ISOLATED",
                    ValidityStage.DESIGN,
                    (ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR),
                    "The design changes more than curriculum ordering, so it cannot identify a curriculum-order effect.",
                    "Hold task coverage, labels, features, sample composition, objective, and sample count fixed; compare an ordered curriculum with a randomized order and optionally a reverse order.",
                    "stage2_design_vnext",
                    *(
                        [f"confounded_dimension={item}" for item in confounded]
                        + [f"unfrozen_invariant={item}" for item in missing_invariants]
                        + (
                            ["matched_order_control=missing"]
                            if not contract.matched_control_ids
                            else []
                        )
                    ),
                )
            )

    if (
        contract.identification_target
        in {
            IdentificationTarget.RULE_SUPERVISION,
            IdentificationTarget.FEATURE_REPRESENTATION,
        }
        and not contract.matched_control_ids
    ):
        ceiling = _claim_tier_min(ceiling, ClaimTier.CONTROLLED_EFFECT)
        findings.append(
            _finding(
                "SV-DESIGN-MATCHED-CONTROL-MISSING",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                "The requested mechanism lacks a task-, label-, and budget-matched control.",
                "Register a control that changes only the claimed supervision or feature dimension.",
                "stage2_protocol_vnext",
            )
        )

    leaking_features = [
        str(item.get("feature_id") or item.get("name") or "unnamed_feature")
        for item in contract.feature_target_relationships
        if str(item.get("relationship") or "").casefold()
        in {
            "direct_target",
            "derived_from_target_rule",
            "sufficient_statistic",
            "near_sufficient_statistic",
        }
    ]
    if leaking_features and not contract.feature_ablation_ids:
        ceiling = _claim_tier_min(ceiling, ClaimTier.IN_DISTRIBUTION_ASSOCIATION)
        findings.append(
            _finding(
                "SV-DESIGN-TARGET-SUFFICIENT-FEATURE-UNABLATED",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                "One or more input features directly encode or nearly determine the target rule.",
                "Add raw-feature, engineered-feature, and one-feature-removed ablations before claiming learned mechanism or generalization.",
                "stage2_measurement_vnext",
                *leaking_features,
            )
        )

    if (
        strict
        and contract.data_provenance_mode
        in {DataProvenanceMode.SYNTHETIC, DataProvenanceMode.MIXED}
        and contract.requested_claim_tier
        in {
            ClaimTier.CONTROLLED_EFFECT,
            ClaimTier.GENERALIZABLE_EFFECT,
        }
    ):
        missing_generator = _missing(
            contract.data_generator_disclosure,
            (
                "variable distributions",
                "scenario families",
                "split overlap",
                "seed roles",
                "tie cases",
                "boundary margin",
            ),
        )
        if missing_generator:
            findings.append(
                _finding(
                    "SV-DESIGN-GENERATOR-DISCLOSURE-INCOMPLETE",
                    ValidityStage.DESIGN,
                    ValiditySeverity.BLOCKING,
                    "The synthetic data generator is not specified well enough to assess task difficulty or leakage.",
                    "Freeze variable ranges and distributions, family construction, split-overlap checks, seed roles, tie handling, and decision-boundary margin analysis.",
                    "stage2_data_boundary_vnext",
                    *missing_generator,
                )
            )

        if not contract.seed_role_plan:
            findings.append(
                _finding(
                    "SV-DESIGN-SEED-ROLES-MISSING",
                    ValidityStage.DESIGN,
                    ValiditySeverity.BLOCKING,
                    "Synthetic-data seeds and optimization or model seeds are not assigned distinct registered roles.",
                    "Freeze separate generator, split, model, and resampling seed roles so repeated runs do not silently reuse the same source of variation.",
                    "stage2_data_boundary_vnext",
                )
            )

        if not contract.boundary_analysis_plan:
            findings.append(
                _finding(
                    "SV-DESIGN-BOUNDARY-ANALYSIS-MISSING",
                    ValidityStage.DESIGN,
                    ValiditySeverity.BLOCKING,
                    "No analysis is registered for ties or samples near the target decision boundary.",
                    "Freeze tie handling, boundary-margin bins, and performance or action-rate reporting by margin.",
                    "stage2_analysis_plan_vnext",
                )
            )

    if strict and contract.outcome_structure in {
        OutcomeStructure.BINARY_CLASSIFICATION,
        OutcomeStructure.MULTICLASS_CLASSIFICATION,
    }:
        missing_decomposition = _missing(
            contract.behavioral_decomposition_plan,
            (
                "prevalence",
                "confusion matrix",
                "balanced accuracy",
                "matthews correlation coefficient",
                "sensitivity",
                "specificity",
                "per-task",
            ),
        )
        if missing_decomposition:
            findings.append(
                _finding(
                    "SV-DESIGN-BEHAVIORAL-DECOMPOSITION-MISSING",
                    ValidityStage.DESIGN,
                    ValiditySeverity.BLOCKING,
                    "Aggregate accuracy is registered without the conditional analyses needed to rule out class-balance or action-rate artifacts.",
                    "Freeze prevalence, confusion matrices, balanced accuracy, Matthews correlation coefficient, sensitivity, specificity, and task-conditional reporting.",
                    "stage2_analysis_plan_vnext",
                    *missing_decomposition,
                )
            )

    if strict and not contract.threshold_sensitivity_plan:
        findings.append(
            _finding(
                "SV-DESIGN-THRESHOLD-SENSITIVITY-MISSING",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING,
                "The frozen thresholds have no registered sensitivity analysis.",
                "Register plausible alternative rule, effect, and safeguard thresholds without changing the primary rule.",
                "stage2_analysis_plan_vnext",
            )
        )

    if strict and not contract.reproducibility_release_plan:
        findings.append(
            _finding(
                "SV-DESIGN-REPRODUCIBILITY-RELEASE-PLAN-MISSING",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING,
                "The confirmatory study has no frozen reproducibility release plan.",
                "Register the code, data or generator, configuration, environment lock, execution command, and artifact manifest that will be released or reviewer-accessible.",
                "stage2_reproducibility_vnext",
            )
        )

    if contract.construct_label and not contract.instrument_validation:
        ceiling = min(
            ceiling, ClaimTier.IN_DISTRIBUTION_ASSOCIATION, key=list(ClaimTier).index
        )
        findings.append(
            _finding(
                "SV-DESIGN-CONSTRUCT-UNVALIDATED",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                "The named construct is not backed by an instrument-validation plan.",
                "Add independent ratings, agreement, competing labels, balance and leakage checks, or rename the outcome as an operational proxy.",
                "stage2_measurement_vnext",
                f"construct={contract.construct_label}",
            )
        )

    if contract.requested_claim_tier is ClaimTier.GENERALIZABLE_EFFECT:
        missing = []
        if not contract.train_evaluation_separation:
            missing.append("train/evaluation rule separation or OOD matrix")
        if not contract.target_rule_disclosure:
            missing.append("target-label rule disclosure")
        if missing:
            ceiling = ClaimTier.IN_DISTRIBUTION_ASSOCIATION
            findings.append(
                _finding(
                    "SV-DESIGN-GENERALIZATION-IDENTIFICATION-GAP",
                    ValidityStage.DESIGN,
                    ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                    "The design cannot distinguish learned generalization from same-generator rule matching.",
                    "Freeze target-label formulas and a rule-separated/OOD evaluation matrix.",
                    "stage2_design_vnext",
                    *missing,
                )
            )

    if not contract.baseline_ids:
        ceiling = ClaimTier.DESCRIPTIVE
        findings.append(
            _finding(
                "SV-DESIGN-BASELINE-MISSING",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                "No explicit comparator is registered.",
                "Register at least one fair baseline appropriate to the scientific claim.",
                "stage2_protocol_vnext",
            )
        )
    if len(contract.treatment_components) > 1 and not contract.ablation_ids:
        ceiling = min(ceiling, ClaimTier.CONTROLLED_EFFECT, key=list(ClaimTier).index)
        findings.append(
            _finding(
                "SV-DESIGN-COMPONENT-IDENTIFICATION-GAP",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                "A multi-component treatment has no component ablation.",
                "Add factorial or one-component-removed arms; otherwise claim only the bundled treatment effect.",
                "stage2_protocol_vnext",
            )
        )

    if not contract.uncertainty_plan:
        findings.append(
            _finding(
                "SV-DESIGN-UNCERTAINTY-PLAN-MISSING",
                ValidityStage.DESIGN,
                ValiditySeverity.BLOCKING if strict else ValiditySeverity.MAJOR,
                "Seeds, clusters, heterogeneity, and sensitivity reporting are not frozen.",
                "Freeze per-seed/per-family reporting, interval method, and sensitivity analyses.",
                "stage2_analysis_plan_vnext",
            )
        )

    return ScientificValidityReport(
        stage=ValidityStage.DESIGN,
        passed=not any(item.severity is ValiditySeverity.BLOCKING for item in findings),
        maximum_claim_tier=ceiling,
        findings=findings,
    )


def audit_evidence_validity(
    contract: ScientificValidityContract,
    evidence_summary: dict[str, Any],
) -> ScientificValidityReport:
    findings: list[ScientificValidityFinding] = []
    ceiling = contract.requested_claim_tier

    failed_integrity = [
        name
        for name, passed in dict(evidence_summary.get("input_integrity") or {}).items()
        if passed is not True
    ]
    if failed_integrity:
        ceiling = ClaimTier.DESCRIPTIVE
        findings.append(
            _finding(
                "SV-EVIDENCE-INPUT-INTEGRITY-FAILED",
                ValidityStage.EVIDENCE,
                ValiditySeverity.BLOCKING,
                "Rendered inputs or tokenizer/scoring inputs failed integrity checks.",
                "Keep the damaged run as history and execute a successor after fixing the input pipeline.",
                "stage3_experimental_successor",
                *failed_integrity,
            )
        )

    completed_manipulation = set(
        evidence_summary.get("completed_manipulation_checks") or []
    )
    missing_manipulation = sorted(
        set(contract.manipulation_checks) - completed_manipulation
    )
    if missing_manipulation:
        ceiling = min(
            ceiling, ClaimTier.IN_DISTRIBUTION_ASSOCIATION, key=list(ClaimTier).index
        )
        findings.append(
            _finding(
                "SV-EVIDENCE-MANIPULATION-CHECK-MISSING",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "The intended treatment exposure was not demonstrated.",
                "Run the frozen manipulation checks before interpreting a treatment effect.",
                "stage3_analysis_successor",
                *missing_manipulation,
            )
        )

    required_controls = set(contract.matched_control_ids)
    completed_controls = set(evidence_summary.get("completed_control_ids") or [])
    missing_controls = sorted(required_controls - completed_controls)
    if missing_controls:
        ceiling = _claim_tier_min(ceiling, ClaimTier.CONTROLLED_EFFECT)
        findings.append(
            _finding(
                "SV-EVIDENCE-MATCHED-CONTROL-NOT-RUN",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "One or more registered matched controls were not completed.",
                "Run the matched controls or narrow the claim to the bundled intervention actually compared.",
                "stage3_experimental_successor",
                *missing_controls,
            )
        )

    completed_feature_ablations = set(
        evidence_summary.get("completed_feature_ablation_ids") or []
    )
    missing_feature_ablations = sorted(
        set(contract.feature_ablation_ids) - completed_feature_ablations
    )
    if missing_feature_ablations:
        ceiling = _claim_tier_min(ceiling, ClaimTier.IN_DISTRIBUTION_ASSOCIATION)
        findings.append(
            _finding(
                "SV-EVIDENCE-FEATURE-ABLATION-NOT-RUN",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "Registered target-leakage or feature-representation ablations are incomplete.",
                "Complete the frozen raw-feature and feature-removal matrix before interpreting learned mechanism.",
                "stage3_experimental_successor",
                *missing_feature_ablations,
            )
        )

    if contract.data_generator_disclosure and not evidence_summary.get(
        "generator_disclosure_verified"
    ):
        findings.append(
            _finding(
                "SV-EVIDENCE-GENERATOR-AUDIT-MISSING",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "The frozen generator disclosure was not verified against produced samples.",
                "Verify ranges, distributions, duplicate/overlap checks, family counts, seed roles, tie counts, and boundary margins.",
                "stage3_analysis_successor",
            )
        )

    if contract.outcome_structure in {
        OutcomeStructure.BINARY_CLASSIFICATION,
        OutcomeStructure.MULTICLASS_CLASSIFICATION,
    }:
        decomposition = dict(evidence_summary.get("behavioral_decomposition") or {})
        required = {
            "class_prevalence",
            "confusion_matrix",
            "balanced_accuracy",
            "matthews_correlation_coefficient",
            "sensitivity",
            "specificity",
            "per_task",
        }
        missing = sorted(required - set(decomposition))
        if missing:
            findings.append(
                _finding(
                    "SV-EVIDENCE-BEHAVIORAL-DECOMPOSITION-INCOMPLETE",
                    ValidityStage.EVIDENCE,
                    ValiditySeverity.MAJOR,
                    "Aggregate performance lacks the registered conditional behavior decomposition.",
                    "Report prevalence, confusion matrices, balanced accuracy, Matthews correlation coefficient, sensitivity, specificity, and per-task action rates with numerators and denominators.",
                    "stage3_analysis_successor",
                    *missing,
                )
            )

    missing_heterogeneity = sorted(
        set(contract.heterogeneity_plan)
        - set(evidence_summary.get("completed_heterogeneity_analyses") or [])
    )
    if missing_heterogeneity:
        findings.append(
            _finding(
                "SV-EVIDENCE-HETEROGENEITY-INCOMPLETE",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "Registered seed- or family-level heterogeneity analyses are incomplete.",
                "Report per-seed, per-family, and leave-one-family-out results as registered.",
                "stage3_analysis_successor",
                *missing_heterogeneity,
            )
        )

    missing_robustness = sorted(
        set(contract.robust_inference_plan)
        - set(evidence_summary.get("completed_robust_inference") or [])
    )
    if missing_robustness:
        findings.append(
            _finding(
                "SV-EVIDENCE-ROBUST-INFERENCE-INCOMPLETE",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "Small-cluster or alternative-inference checks registered in the contract are incomplete.",
                "Complete the frozen cluster permutation, wild bootstrap, or equivalent sensitivity analysis.",
                "stage3_analysis_successor",
                *missing_robustness,
            )
        )

    if contract.threshold_sensitivity_plan and not evidence_summary.get(
        "threshold_sensitivity_completed"
    ):
        findings.append(
            _finding(
                "SV-EVIDENCE-THRESHOLD-SENSITIVITY-INCOMPLETE",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "Registered threshold sensitivity results are missing.",
                "Evaluate the frozen alternative rule, effect, and safeguard thresholds without changing the primary verdict.",
                "stage3_analysis_successor",
            )
        )

    if contract.boundary_analysis_plan and not evidence_summary.get(
        "boundary_analysis_completed"
    ):
        findings.append(
            _finding(
                "SV-EVIDENCE-BOUNDARY-ANALYSIS-INCOMPLETE",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "Registered tie and decision-boundary analyses are missing.",
                "Report counts and outcomes by frozen boundary-margin bins, including the registered tie policy.",
                "stage3_analysis_successor",
            )
        )

    if contract.seed_role_plan and not evidence_summary.get("seed_roles_verified"):
        findings.append(
            _finding(
                "SV-EVIDENCE-SEED-ROLES-UNVERIFIED",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "The produced runs do not verify the frozen generator, split, model, and resampling seed roles.",
                "Bind every seed role to the run manifest and verify that independent sources of variation were not conflated.",
                "stage3_integrity_successor",
            )
        )

    safeguard_counts = dict(evidence_summary.get("safeguard_counts") or {})
    missing_safeguard_counts = sorted(
        (set(contract.safeguard_metric_names) - set(safeguard_counts))
        | {
            name
            for name, payload in safeguard_counts.items()
            if not isinstance(payload, dict)
            or payload.get("numerator") is None
            or payload.get("denominator") is None
        }
    )
    if missing_safeguard_counts:
        findings.append(
            _finding(
                "SV-EVIDENCE-SAFEGUARD-DENOMINATOR-MISSING",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "A safeguard rate is reported without an auditable numerator and denominator.",
                "Report exact counts for every safeguard rate.",
                "stage3_reporting_repair",
                *missing_safeguard_counts,
            )
        )

    if contract.reproducibility_release_plan and not evidence_summary.get(
        "reproducibility_package_ready"
    ):
        findings.append(
            _finding(
                "SV-EVIDENCE-REPRODUCIBILITY-PACKAGE-INCOMPLETE",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "The registered reproducibility package is incomplete or has not passed a clean-room verification.",
                "Package the frozen code, data or generator, configuration, environment lock, execution command, and manifest; then verify it without relying on the author workspace.",
                "stage3_reproduction_successor",
            )
        )

    diagnostic_flags = set(evidence_summary.get("diagnostic_flags") or [])
    completed_followups = set(
        evidence_summary.get("completed_diagnostic_followups") or []
    )
    missing_followups = sorted(diagnostic_flags - completed_followups)
    if missing_followups:
        findings.append(
            _finding(
                "SV-EVIDENCE-DIAGNOSTIC-FOLLOWUP-MISSING",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "A diagnostic metric conflicts with the aggregate result and lacks conditional analysis.",
                "Report conditional rates, confusion/calibration views, prevalence, and the registered diagnostic follow-up.",
                "stage3_analysis_successor",
                *missing_followups,
            )
        )

    clusters = int(evidence_summary.get("independent_clusters") or 0)
    if 0 < clusters < 20:
        if (
            contract.enforcement_level == "confirmatory"
            and not contract.robust_inference_plan
        ):
            findings.append(
                _finding(
                    "SV-EVIDENCE-SMALL-CLUSTER-ROBUSTNESS-NOT-REGISTERED",
                    ValidityStage.EVIDENCE,
                    ValiditySeverity.MAJOR,
                    "A confirmatory analysis has few independent clusters but no registered small-sample robust inference.",
                    "Add a successor analysis using a frozen cluster permutation, wild cluster bootstrap, or justified equivalent; report family-level effects and leave-one-family-out sensitivity.",
                    "stage3_analysis_successor",
                    f"independent_clusters={clusters}",
                )
            )
        findings.append(
            _finding(
                "SV-EVIDENCE-SMALL-CLUSTER-INFERENCE",
                ValidityStage.EVIDENCE,
                ValiditySeverity.WARNING,
                "The number of independent clusters is small.",
                "Report p-value resolution, cluster-aware intervals, family effects, and leave-one-cluster-out sensitivity.",
                "stage3_reporting_repair",
                f"independent_clusters={clusters}",
            )
        )

    if (
        contract.requested_claim_tier is ClaimTier.GENERALIZABLE_EFFECT
        and not evidence_summary.get("ood_or_rule_separated_evaluation_passed")
    ):
        ceiling = ClaimTier.IN_DISTRIBUTION_ASSOCIATION
        findings.append(
            _finding(
                "SV-EVIDENCE-OOD-EVIDENCE-MISSING",
                ValidityStage.EVIDENCE,
                ValiditySeverity.MAJOR,
                "No rule-separated or out-of-distribution evidence supports the requested generalization.",
                "Narrow the claim or execute the frozen OOD matrix.",
                "stage3_experimental_successor",
            )
        )

    return ScientificValidityReport(
        stage=ValidityStage.EVIDENCE,
        passed=not any(item.severity is ValiditySeverity.BLOCKING for item in findings),
        maximum_claim_tier=ceiling,
        findings=findings,
    )


_RESIDUE_PATTERNS = {
    "SV-MANUSCRIPT-FIGURE-PLACEHOLDER": r"待呈现图位|figure\s+placeholder",
    "SV-MANUSCRIPT-UNKNOWN-AUTHOR": r"\bunknown author\b|作者[：:]\s*(?:unknown|待定)",
    "SV-MANUSCRIPT-PENDING-DECLARATION": (
        r"\b(?:ethics|conflict of interest|funding|data availability)\b.{0,80}"
        r"\b(?:pending|tbd|to be completed)\b"
    ),
}


def audit_manuscript_validity(
    *,
    manuscript: str,
    abstract: str,
    figure_specs: list[dict[str, Any]] | None = None,
    result_table_columns: list[str] | None = None,
    main_text_audit_paths: list[str] | None = None,
    claim_context: dict[str, Any] | None = None,
) -> ScientificValidityReport:
    findings: list[ScientificValidityFinding] = []
    context = dict(claim_context or {})
    try:
        ceiling = ClaimTier(
            str(context.get("maximum_claim_tier") or ClaimTier.GENERALIZABLE_EFFECT)
        )
    except ValueError:
        ceiling = ClaimTier.DESCRIPTIVE
    for code, pattern in _RESIDUE_PATTERNS.items():
        if re.search(pattern, manuscript, re.IGNORECASE | re.DOTALL):
            findings.append(
                _finding(
                    code,
                    ValidityStage.MANUSCRIPT,
                    ValiditySeverity.BLOCKING,
                    "Submission-facing text contains generation residue or an unresolved disclosure placeholder.",
                    "Replace the placeholder with verified metadata or block submission.",
                    "stage4_manuscript_repair",
                )
            )

    abstract_paragraphs = [
        item.strip() for item in re.split(r"\n\s*\n", abstract) if item.strip()
    ]
    if len(abstract_paragraphs) != 1 or re.search(
        r"^\s*(?:background|method|results?|conclusion)\s*[:：]",
        abstract,
        re.IGNORECASE | re.MULTILINE,
    ):
        findings.append(
            _finding(
                "SV-MANUSCRIPT-ABSTRACT-STRUCTURE",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "The abstract is not a single unlabelled scientific narrative.",
                "Use one paragraph covering question, design, primary result, interpretation, and principal limitation.",
                "stage4_manuscript_repair",
            )
        )

    if main_text_audit_paths:
        findings.append(
            _finding(
                "SV-MANUSCRIPT-AUDIT-DETAIL-IN-MAIN-TEXT",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "Internal paths, hashes, or audit-ledger detail remain in the main scientific narrative.",
                "Move reproducibility and ledger detail to the supplement; keep effects and uncertainty in the main text.",
                "stage4_structure_repair",
                *main_text_audit_paths,
            )
        )

    figures = figure_specs or []
    if figures and not any(
        bool(item.get("effect_estimate"))
        and bool(item.get("confidence_interval"))
        and bool(item.get("zero_reference"))
        for item in figures
    ):
        findings.append(
            _finding(
                "SV-MANUSCRIPT-EFFECT-FIGURE-MISSING",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "Primary visuals do not center the treatment-control effect and uncertainty.",
                "Add an effect plot with confidence interval, zero reference, threshold, and seed/family distribution where applicable.",
                "stage4_visual_repair",
            )
        )

    required_columns = {
        "comparison",
        "treatment_mean",
        "control_mean",
        "effect",
        "confidence_interval",
        "status",
    }
    normalized_columns = {
        str(item).strip().casefold() for item in (result_table_columns or [])
    }
    if result_table_columns is not None and not required_columns.issubset(
        normalized_columns
    ):
        findings.append(
            _finding(
                "SV-MANUSCRIPT-RESULT-TABLE-INCOMPLETE",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "The main results table omits the standard comparison/effect fields.",
                "Report arm means, effect, interval, multiplicity-adjusted inference when applicable, threshold, and status.",
                "stage4_table_repair",
                *sorted(required_columns - normalized_columns),
            )
        )

    mechanism_identified = bool(context.get("mechanism_identified"))
    curriculum_claim = re.search(
        r"\b(?:curriculum learning|curriculum-order(?:ing)? effect)\b|"
        r"课程学习(?:效应|机制|增益)?|课程顺序(?:效应|机制|增益)",
        manuscript,
        re.IGNORECASE,
    )
    if curriculum_claim and not mechanism_identified:
        ceiling = _claim_tier_min(ceiling, ClaimTier.CONTROLLED_EFFECT)
        findings.append(
            _finding(
                "SV-MANUSCRIPT-UNIDENTIFIED-CURRICULUM-MECHANISM",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "The manuscript attributes the result to curriculum learning although the frozen design identifies only a bundled training-program effect.",
                "Rename the intervention as a training scheme or add a same-data, same-label random-order control before claiming a curriculum mechanism.",
                "stage4_claim_scope_repair",
            )
        )

    if context.get("target_feature_overlap") and not re.search(
        r"(?:feature|特征).{0,80}(?:target|label|目标|标签).{0,80}"
        r"(?:overlap|leak|sufficient|重合|泄漏|充分统计)",
        manuscript,
        re.IGNORECASE | re.DOTALL,
    ):
        ceiling = _claim_tier_min(ceiling, ClaimTier.IN_DISTRIBUTION_ASSOCIATION)
        findings.append(
            _finding(
                "SV-MANUSCRIPT-TARGET-FEATURE-OVERLAP-UNDISCLOSED",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "The manuscript omits a known overlap between engineered features and the target rule.",
                "Disclose the sufficient-statistic or target-rule overlap and limit the claim to the evaluated representation unless ablations resolve it.",
                "stage4_claim_scope_repair",
            )
        )

    if context.get("adaptive_evidence") and not re.search(
        r"\badaptive(?: follow-up| reuse| evidence)?\b|"
        r"自适应(?:后续|复用|证据)|结果暴露",
        manuscript,
        re.IGNORECASE,
    ):
        findings.append(
            _finding(
                "SV-MANUSCRIPT-ADAPTIVE-EVIDENCE-UNDISCLOSED",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "The manuscript presents adaptive follow-up evidence without disclosing prior result exposure.",
                "State the adaptive status in the abstract and methods or results, and do not call it independent confirmation.",
                "stage4_claim_scope_repair",
            )
        )

    if context.get("reproducibility_release_planned") and not re.search(
        r"\b(?:code|data|artifact|reproducibility)\s+availability\b|"
        r"(?:代码|数据|工件|复现)(?:可用性|开放|材料|声明)",
        manuscript,
        re.IGNORECASE,
    ):
        findings.append(
            _finding(
                "SV-MANUSCRIPT-REPRODUCIBILITY-STATEMENT-MISSING",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "The manuscript omits the registered reproducibility-package availability statement.",
                "State what frozen code, data or generator, configuration, environment, and manifest are available to reviewers or the public.",
                "stage4_disclosure_repair",
            )
        )

    required_disclosures = [
        str(item) for item in context.get("required_disclosures") or []
    ]
    missing_disclosures = [
        item
        for item in required_disclosures
        if item.casefold() not in manuscript.casefold()
    ]
    if missing_disclosures:
        findings.append(
            _finding(
                "SV-MANUSCRIPT-REQUIRED-SCIENTIFIC-DISCLOSURE-MISSING",
                ValidityStage.MANUSCRIPT,
                ValiditySeverity.MAJOR,
                "A claim-limiting design or evidence condition is absent from the manuscript.",
                "Add the required limitation to the abstract, methods, results, or discussion according to its role.",
                "stage4_claim_scope_repair",
                *missing_disclosures,
            )
        )

    preferred_metrics = dict(context.get("preferred_metric_names") or {})
    for internal_name, preferred_name in preferred_metrics.items():
        occurrences = len(
            re.findall(re.escape(str(internal_name)), manuscript, re.IGNORECASE)
        )
        if (
            occurrences > 1
            and str(preferred_name).casefold() not in manuscript.casefold()
        ):
            findings.append(
                _finding(
                    "SV-MANUSCRIPT-INTERNAL-METRIC-NAME-OVERUSED",
                    ValidityStage.MANUSCRIPT,
                    ValiditySeverity.MAJOR,
                    "An internal metric name with construct-like wording is used throughout the scientific narrative.",
                    "Define the internal field once in Methods and use the operational scientific name elsewhere.",
                    "stage4_manuscript_repair",
                    f"{internal_name}->{preferred_name}",
                )
            )

    return ScientificValidityReport(
        stage=ValidityStage.MANUSCRIPT,
        passed=not any(
            item.severity
            in {
                ValiditySeverity.BLOCKING,
                ValiditySeverity.MAJOR,
            }
            for item in findings
        ),
        maximum_claim_tier=ceiling,
        findings=findings,
    )


__all__ = [
    "ClaimTier",
    "DataProvenanceMode",
    "IdentificationTarget",
    "OutcomeStructure",
    "ScientificValidityContract",
    "ScientificValidityFinding",
    "ScientificValidityReport",
    "ValiditySeverity",
    "ValidityStage",
    "audit_design_validity",
    "audit_evidence_validity",
    "audit_manuscript_validity",
    "summarize_classification_rows",
]
