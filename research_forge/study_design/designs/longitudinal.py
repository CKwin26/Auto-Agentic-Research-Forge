"""Certified two-arm repeated-measures linear-trajectory Study Design."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, variance
from typing import Any

from ..math_utils import student_t_cdf, student_t_ppf
from ..schemas import (
    AnalysisPlan,
    ClaimEnvelope,
    CompletionIssue,
    OutcomeEvaluation,
    RepairClass,
    StudyDesignCompletionPatch,
    StudyDesignEvaluation,
    StudyDesignMaturity,
)
from ..sdk import StudyDesignDescriptor


def _payload(contract: Any) -> dict[str, Any]:
    value = getattr(contract, "study_design_spec", None)
    if value is None and isinstance(contract, dict):
        value = contract.get("study_design_spec")
    return dict(value or {})


def _as_plan(contract: Any) -> AnalysisPlan:
    data = _payload(contract)
    data.setdefault("study_design_id", "longitudinal_repeated_measures_v1")
    data.setdefault("study_design_version", "1")
    return AnalysisPlan.model_validate(data)


def _subject_slope(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    times = [item[0] for item in points]
    values = [item[1] for item in points]
    time_mean = mean(times)
    value_mean = mean(values)
    denominator = sum((time - time_mean) ** 2 for time in times)
    if denominator <= 0:
        return None
    return sum(
        (time - time_mean) * (value - value_mean)
        for time, value in points
    ) / denominator


class LongitudinalRepeatedMeasures:
    descriptor = StudyDesignDescriptor(
        design_id="longitudinal_repeated_measures_v1",
        version="1",
        title="Repeated measures and longitudinal study",
        summary=(
            "Compares mean subject-specific linear outcome trajectories between "
            "two randomized arms while keeping the subject as the independent unit."
        ),
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
        known_limits=(
            "exactly two randomized arms and one continuous outcome",
            "prespecified numeric time grid and linear subject trajectories only",
            "Welch comparison of subject slopes; no mixed effects, nonlinear time, clusters, or informative dropout",
        ),
    )

    def qualify(self, contract: Any, resources: Any | None = None) -> dict[str, Any]:
        violations = self.validate_contract(contract)
        return {"qualified": not violations, "violations": violations}

    def complete_contract(
        self, task_brief: Any, draft_contract: Any, resources: Any | None = None
    ) -> StudyDesignCompletionPatch:
        data = _payload(draft_contract)
        required = {
            "unit_structure": "Define the subject as assignment, analysis, variance, and independent unit.",
            "allocation": "Freeze randomized subject-level allocation evidence.",
            "arms": "Define one control and one treatment arm.",
            "outcomes": "Register one continuous longitudinal outcome.",
            "estimands": "Register the between-arm difference in mean subject slopes.",
            "longitudinal": "Freeze the subject field, numeric time grid, time unit, and trajectory eligibility rule.",
            "missingness": "Freeze the minimum-observed-trajectory denominator rule.",
            "decision_rules": "Freeze the direction and success rule before outcomes are exposed.",
        }
        issues = [
            CompletionIssue(
                issue_id=f"longitudinal-{field}-missing",
                field_path=f"study_design_spec.{field}",
                severity="error",
                repair_class=RepairClass.OWNER_SCIENTIFIC_DECISION,
                expected=expected,
                observed=data.get(field),
                provenance="longitudinal_repeated_measures_v1 schema",
                owner_approval_required=True,
            )
            for field, expected in required.items()
            if not data.get(field)
        ]
        return StudyDesignCompletionPatch(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            issues=issues,
        )

    def validate_contract(self, contract: Any) -> list[str]:
        try:
            plan = _as_plan(contract)
        except Exception as exc:
            return [f"invalid longitudinal analysis plan: {exc}"]
        violations: list[str] = []
        longitudinal = plan.longitudinal
        if longitudinal is None:
            return ["longitudinal study requires a frozen longitudinal extension"]
        if len(plan.arms) != 2 or {arm.role for arm in plan.arms} != {
            "control",
            "treatment",
        }:
            violations.append("longitudinal comparison requires one control and one treatment arm")
        if plan.allocation.mechanism != "randomized":
            violations.append("the first longitudinal kernel requires verified randomized allocation")
        unit = plan.unit_structure
        subject_units = {
            unit.assignment_unit,
            unit.analysis_unit,
            unit.variance_unit,
            unit.independent_unit,
        }
        if len(subject_units) != 1 or unit.repeated_measure_unit != unit.independent_unit:
            violations.append(
                "assignment, analysis, variance, independent, and repeated-measure units must all be the subject"
            )
        if unit.cluster_unit:
            violations.append("clustered longitudinal studies require another Study Design")
        if len(plan.outcomes) != 1 or plan.outcomes[0].kind != "continuous":
            violations.append("the first longitudinal kernel supports exactly one continuous outcome")
        if len(plan.estimands) != 1 or plan.estimands[0].effect_measure != "slope_difference":
            violations.append("the longitudinal estimand must be a between-arm slope difference")
        if plan.outcomes:
            outcome = plan.outcomes[0]
            estimator = plan.estimator_plan.get(outcome.outcome_id)
            if not estimator or estimator.estimator_id != "mean_subject_slope_difference_v1":
                violations.append("the longitudinal outcome requires the mean subject-slope estimator")
            if set(plan.decision_rules) != {outcome.outcome_id}:
                violations.append("the longitudinal outcome requires exactly one decision rule")
            else:
                rule = plan.decision_rules[outcome.outcome_id]
                if rule.mode != "superiority":
                    violations.append("the first longitudinal kernel supports superiority only")
                if rule.beneficial_direction != outcome.beneficial_direction:
                    violations.append("the longitudinal decision direction contradicts the outcome")
            if plan.multiplicity.hypothesis_ids != [outcome.outcome_id]:
                violations.append("the confirmatory family must contain only the primary longitudinal outcome")
        if plan.multiplicity.method != "no_correction":
            violations.append("one longitudinal primary outcome does not require multiplicity correction")
        if plan.missingness.policy != "minimum_observed_trajectory":
            violations.append("longitudinal missingness must use the frozen minimum-observed-trajectory rule")
        return list(dict.fromkeys(violations))

    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan:
        violations = self.validate_contract(contract)
        if violations:
            raise ValueError("; ".join(violations))
        return _as_plan(contract)

    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]:
        longitudinal = plan.longitudinal
        assert longitudinal is not None
        outcome = plan.outcomes[0]
        arm_ids = {arm.arm_id for arm in plan.arms}
        planned = set(longitudinal.planned_time_points)
        subject_arms: dict[str, set[str]] = defaultdict(set)
        subject_times: dict[str, set[float]] = defaultdict(set)
        required = {
            longitudinal.subject_id_field,
            "arm",
            longitudinal.time_field,
            outcome.field,
        }
        errors: list[str] = []
        for index, row in enumerate(rows):
            missing = sorted(required - set(row))
            if missing:
                errors.append(f"row {index} is missing {', '.join(missing)}")
                continue
            subject = str(row[longitudinal.subject_id_field])
            arm = str(row["arm"])
            try:
                time = float(row[longitudinal.time_field])
            except (TypeError, ValueError):
                errors.append(f"row {index} has a nonnumeric time value")
                continue
            if arm not in arm_ids:
                errors.append(f"row {index} uses an unregistered arm")
            if time not in planned:
                errors.append(f"row {index} uses a time outside the frozen grid")
            if time in subject_times[subject]:
                errors.append(f"subject {subject} has duplicate observations at one time")
            subject_times[subject].add(time)
            subject_arms[subject].add(arm)
        if any(len(arms) != 1 for arms in subject_arms.values()):
            errors.append("a subject cannot switch randomized arms over time")
        return list(dict.fromkeys(errors))

    def evaluate(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> StudyDesignEvaluation:
        errors = self.validate_realized_data(plan, rows)
        if errors:
            return StudyDesignEvaluation(
                study_design_id=self.descriptor.design_id,
                study_design_version=self.descriptor.version,
                eligible=False,
                qualification_checks={"realized_data_valid": False},
                outcomes=[],
                primary_decision="unverifiable",
                limitations=errors,
            )
        longitudinal = plan.longitudinal
        assert longitudinal is not None
        outcome = plan.outcomes[0]
        trajectories: dict[str, dict[str, Any]] = {}
        missing_rows = 0
        for row in rows:
            subject = str(row[longitudinal.subject_id_field])
            item = trajectories.setdefault(
                subject, {"arm": str(row["arm"]), "points": []}
            )
            value = row.get(outcome.field)
            if value is None or value == "":
                missing_rows += 1
                continue
            item["points"].append(
                (float(row[longitudinal.time_field]), float(value))
            )
        slopes: dict[str, list[float]] = {arm.arm_id: [] for arm in plan.arms}
        excluded = 0
        for item in trajectories.values():
            points = item["points"]
            if len(points) < longitudinal.minimum_observed_time_points:
                excluded += 1
                continue
            slope = _subject_slope(points)
            if slope is None:
                excluded += 1
                continue
            slopes[item["arm"]].append(slope)
        roles = {arm.role: arm.arm_id for arm in plan.arms}
        control = slopes[roles["control"]]
        treatment = slopes[roles["treatment"]]
        if min(len(control), len(treatment)) < longitudinal.minimum_subjects_per_arm:
            result = OutcomeEvaluation(
                outcome_id=outcome.outcome_id,
                kind="continuous",
                eligible=False,
                arm_statistics={},
                effect_measure="slope_difference",
                missing_count=missing_rows,
                excluded_count=excluded,
                denominator=len(control) + len(treatment),
                decision="unverifiable",
                details={"reason": "too few eligible subject trajectories per arm"},
            )
        else:
            control_mean, treatment_mean = mean(control), mean(treatment)
            control_variance, treatment_variance = variance(control), variance(treatment)
            effect = treatment_mean - control_mean
            se2 = control_variance / len(control) + treatment_variance / len(treatment)
            se = math.sqrt(se2)
            df = (
                se2 * se2
                / (
                    (control_variance / len(control)) ** 2 / (len(control) - 1)
                    + (treatment_variance / len(treatment)) ** 2 / (len(treatment) - 1)
                )
                if se > 0
                else math.inf
            )
            confidence = plan.inference_plan[outcome.outcome_id].confidence_level
            critical = (
                student_t_ppf(0.5 + confidence / 2, df)
                if math.isfinite(df)
                else 0.0
            )
            interval = (effect - critical * se, effect + critical * se)
            p_value = (
                2 * (1 - student_t_cdf(abs(effect / se), df))
                if se > 0
                else (0.0 if effect else 1.0)
            )
            result = OutcomeEvaluation(
                outcome_id=outcome.outcome_id,
                kind="continuous",
                eligible=True,
                arm_statistics={
                    roles["control"]: {
                        "n_subjects": len(control),
                        "mean_slope": control_mean,
                        "sd_slope": math.sqrt(control_variance),
                    },
                    roles["treatment"]: {
                        "n_subjects": len(treatment),
                        "mean_slope": treatment_mean,
                        "sd_slope": math.sqrt(treatment_variance),
                    },
                },
                effect_measure="slope_difference",
                effect=effect,
                standard_error=se,
                confidence_interval=interval,
                p_value=p_value,
                raw_p_value=p_value,
                adjusted_p_value=p_value,
                degrees_of_freedom=df if math.isfinite(df) else None,
                missing_count=missing_rows,
                excluded_count=excluded,
                denominator=len(control) + len(treatment),
                details={
                    "time_field": longitudinal.time_field,
                    "time_unit": longitudinal.time_unit,
                    "trajectory_model": longitudinal.trajectory_model,
                    "uncertainty": "Welch t interval over independent subject-specific slopes",
                },
            )
        evaluation = StudyDesignEvaluation(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            eligible=result.eligible,
            qualification_checks={
                "realized_data_valid": True,
                "subject_is_independent_unit": True,
                "repeated_observations_bound_to_subject": True,
                "trajectory_denominator_explicit": True,
            },
            outcomes=[result],
            primary_decision="inconclusive" if result.eligible else "unverifiable",
        )
        return self.adjudicate(plan, evaluation)

    def adjudicate(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> StudyDesignEvaluation:
        if not evaluation.outcomes:
            return evaluation
        result = evaluation.outcomes[0]
        if not result.eligible or not result.confidence_interval:
            result.decision = "unverifiable"
            evaluation.primary_decision = "unverifiable"
            evaluation.eligible = False
            return evaluation
        rule = plan.decision_rules[result.outcome_id]
        low, high = result.confidence_interval
        result.decision = (
            "supported"
            if (
                (rule.beneficial_direction == "higher" and low > 0)
                or (rule.beneficial_direction == "lower" and high < 0)
            )
            else "inconclusive"
        )
        evaluation.primary_decision = result.decision
        return evaluation

    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope:
        longitudinal = plan.longitudinal
        assert longitudinal is not None
        result = evaluation.outcomes[0]
        interval = result.confidence_interval
        effect = (
            f"The treatment-minus-control difference in mean subject slope was {result.effect:.4g}"
            + (
                f" {longitudinal.time_unit}^-1, with an interval from {interval[0]:.4g} to {interval[1]:.4g}."
                if interval and result.effect is not None
                else "."
            )
            if result.effect is not None
            else "The registered mean subject-slope difference was not estimable."
        )
        return ClaimEnvelope(
            study_design_name="Randomized repeated-measures linear-trajectory comparison",
            arm_definitions=[f"{arm.label}: {arm.definition}" for arm in plan.arms],
            allocation_verified_randomized=True,
            observation_unit=plan.unit_structure.observation_unit,
            analysis_unit=plan.unit_structure.analysis_unit,
            independent_unit=plan.unit_structure.independent_unit,
            primary_outcomes=[plan.outcomes[0].label],
            secondary_outcomes=[],
            denominator=result.denominator,
            missing_count=result.missing_count,
            excluded_count=result.excluded_count,
            effect_estimates=[effect],
            uncertainty_method="Welch t interval over independent subject-specific linear slopes",
            multiplicity_method="no correction; one confirmatory estimand",
            scientific_verdict=evaluation.primary_decision,
            permitted_claims=[
                "Report the registered between-arm difference in mean subject-specific linear slopes.",
                "Interpret time using the frozen numeric grid and declared time unit.",
            ],
            prohibited_claims=[
                "Do not treat repeated rows as independent subjects.",
                "Do not infer nonlinear trajectories, random effects, or dropout mechanisms.",
                "Do not generalize beyond the frozen randomized longitudinal population.",
            ],
            generalization_boundary=plan.claim_boundary.get(
                "generalization",
                "Only the frozen randomized population and linear time grid are represented.",
            ),
            reproduction_materials=list(
                plan.claim_boundary.get("reproduction_materials", [])
            ),
            design_details={
                "time_field": longitudinal.time_field,
                "time_unit": longitudinal.time_unit,
                "planned_time_points": longitudinal.planned_time_points,
                "minimum_observed_time_points": longitudinal.minimum_observed_time_points,
                "trajectory_model": longitudinal.trajectory_model,
            },
        )


__all__ = ["LongitudinalRepeatedMeasures"]
