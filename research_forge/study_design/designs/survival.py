"""Certified two-arm right-censored survival Study Design."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean, stdev
from typing import Any

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
    data.setdefault("study_design_id", "survival_analysis_v1")
    data.setdefault("study_design_version", "1")
    return AnalysisPlan.model_validate(data)


def _km_rmst(records: list[tuple[float, bool]], tau: float) -> float:
    """Area under the Kaplan–Meier curve from zero through frozen tau."""

    survival = 1.0
    area = 0.0
    previous = 0.0
    for time in sorted({duration for duration, _ in records if duration <= tau}):
        area += survival * (time - previous)
        at_risk = sum(duration >= time for duration, _ in records)
        events = sum(
            duration == time and observed for duration, observed in records
        )
        if at_risk and events:
            survival *= 1.0 - events / at_risk
        previous = time
    area += survival * (tau - previous)
    return area


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class SurvivalAnalysis:
    descriptor = StudyDesignDescriptor(
        design_id="survival_analysis_v1",
        version="1",
        title="Right-censored survival analysis",
        summary=(
            "Compares restricted mean event-free time between two randomized "
            "arms using Kaplan–Meier risk sets and a frozen restriction time."
        ),
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
        known_limits=(
            "exactly two randomized arms and one right-censored time-to-first-event outcome",
            "Kaplan–Meier RMST difference with subject-level stratified bootstrap uncertainty",
            "no competing risks, recurrent events, left/interval censoring, covariate adjustment, or observational identification",
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
            "unit_structure": "Freeze the subject as the independent time-to-event unit.",
            "allocation": "Freeze randomized subject-level allocation evidence.",
            "arms": "Define one control and one treatment arm.",
            "outcomes": "Register one time-to-first-event outcome.",
            "estimands": "Register the treatment-minus-control RMST difference.",
            "survival": "Freeze time origin, duration/event fields, censor code, tau, and risk-set requirements.",
            "missingness": "Distinguish administrative right censoring from missing event data.",
            "decision_rules": "Freeze the beneficial direction and superiority rule.",
        }
        return StudyDesignCompletionPatch(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            issues=[
                CompletionIssue(
                    issue_id=f"survival-{field}-missing",
                    field_path=f"study_design_spec.{field}",
                    severity="error",
                    repair_class=RepairClass.OWNER_SCIENTIFIC_DECISION,
                    expected=expected,
                    observed=data.get(field),
                    provenance="survival_analysis_v1 schema",
                    owner_approval_required=True,
                )
                for field, expected in required.items()
                if not data.get(field)
            ],
        )

    def validate_contract(self, contract: Any) -> list[str]:
        try:
            plan = _as_plan(contract)
        except Exception as exc:
            return [f"invalid survival analysis plan: {exc}"]
        errors: list[str] = []
        survival = plan.survival
        if survival is None:
            return ["survival analysis requires a frozen survival extension"]
        if len(plan.arms) != 2 or {arm.role for arm in plan.arms} != {
            "control", "treatment"
        }:
            errors.append("survival comparison requires one control and one treatment arm")
        if plan.allocation.mechanism != "randomized":
            errors.append("the first survival kernel requires verified randomized allocation")
        unit = plan.unit_structure
        if len({unit.assignment_unit, unit.analysis_unit, unit.variance_unit, unit.independent_unit}) != 1:
            errors.append("assignment, analysis, variance, and independent units must all be the subject")
        if unit.repeated_measure_unit or unit.cluster_unit:
            errors.append("clustered or repeated-event survival data require another Study Design")
        if len(plan.outcomes) != 1 or plan.outcomes[0].kind != "time_to_event":
            errors.append("the first survival kernel supports one time-to-event outcome")
        if len(plan.estimands) != 1 or plan.estimands[0].effect_measure != "rmst_difference":
            errors.append("the survival estimand must be a treatment-minus-control RMST difference")
        if plan.outcomes:
            outcome = plan.outcomes[0]
            estimator = plan.estimator_plan.get(outcome.outcome_id)
            if not estimator or estimator.estimator_id != "kaplan_meier_rmst_difference_v1":
                errors.append("the survival outcome requires the Kaplan–Meier RMST estimator")
            rule = plan.decision_rules.get(outcome.outcome_id)
            if not rule or rule.mode != "superiority":
                errors.append("the first survival kernel requires one superiority decision rule")
            elif rule.beneficial_direction != outcome.beneficial_direction:
                errors.append("the survival decision direction contradicts the outcome")
            if plan.multiplicity.hypothesis_ids != [outcome.outcome_id]:
                errors.append("the confirmatory family must contain only the primary survival outcome")
        if plan.multiplicity.method != "no_correction":
            errors.append("one survival primary estimand does not require multiplicity correction")
        if plan.missingness.policy != "right_censoring":
            errors.append("survival missingness must use the frozen right-censoring rule")
        return list(dict.fromkeys(errors))

    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan:
        errors = self.validate_contract(contract)
        if errors:
            raise ValueError("; ".join(errors))
        return _as_plan(contract)

    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]:
        survival = plan.survival
        assert survival is not None
        arms = {arm.arm_id for arm in plan.arms}
        required = {
            survival.subject_id_field,
            "arm",
            survival.duration_field,
            survival.event_field,
        }
        seen: set[str] = set()
        errors: list[str] = []
        for index, row in enumerate(rows):
            missing = sorted(required - set(row))
            if missing:
                errors.append(f"row {index} is missing {', '.join(missing)}")
                continue
            subject = str(row[survival.subject_id_field])
            if subject in seen:
                errors.append(f"subject {subject} appears more than once")
            seen.add(subject)
            if str(row["arm"]) not in arms:
                errors.append(f"row {index} uses an unregistered arm")
            try:
                duration = float(row[survival.duration_field])
            except (TypeError, ValueError):
                errors.append(f"row {index} has a nonnumeric duration")
                continue
            if not math.isfinite(duration) or duration <= 0:
                errors.append(f"row {index} has a nonpositive or nonfinite duration")
            if row[survival.event_field] not in {
                survival.event_value, survival.censor_value
            }:
                errors.append(f"row {index} has an unregistered event status")
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
        survival = plan.survival
        assert survival is not None
        outcome = plan.outcomes[0]
        by_arm: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        for row in rows:
            by_arm[str(row["arm"])].append((
                float(row[survival.duration_field]),
                row[survival.event_field] == survival.event_value,
            ))
        roles = {arm.role: arm.arm_id for arm in plan.arms}
        control = by_arm[roles["control"]]
        treatment = by_arm[roles["treatment"]]
        event_counts = {
            arm: sum(observed for _, observed in records)
            for arm, records in by_arm.items()
        }
        enough = (
            min(len(control), len(treatment)) >= survival.minimum_subjects_per_arm
            and min(event_counts.values(), default=0) >= survival.minimum_events_per_arm
        )
        if not enough:
            result = OutcomeEvaluation(
                outcome_id=outcome.outcome_id,
                kind="time_to_event",
                eligible=False,
                arm_statistics={},
                effect_measure="rmst_difference",
                denominator=len(control) + len(treatment),
                decision="unverifiable",
                details={"reason": "too few subjects or observed events per arm"},
            )
        else:
            tau = survival.restriction_time
            control_rmst = _km_rmst(control, tau)
            treatment_rmst = _km_rmst(treatment, tau)
            effect = treatment_rmst - control_rmst
            rng = random.Random(survival.bootstrap_seed)
            draws: list[float] = []
            for _ in range(survival.bootstrap_repetitions):
                c = [control[rng.randrange(len(control))] for _ in control]
                t = [treatment[rng.randrange(len(treatment))] for _ in treatment]
                draws.append(_km_rmst(t, tau) - _km_rmst(c, tau))
            confidence = plan.inference_plan[outcome.outcome_id].confidence_level
            tail = (1 - confidence) / 2
            interval = (_percentile(draws, tail), _percentile(draws, 1 - tail))
            se = stdev(draws)
            p_value = min(
                1.0,
                2 * min(
                    sum(value <= 0 for value in draws) / len(draws),
                    sum(value >= 0 for value in draws) / len(draws),
                ),
            )
            result = OutcomeEvaluation(
                outcome_id=outcome.outcome_id,
                kind="time_to_event",
                eligible=True,
                arm_statistics={
                    roles["control"]: {
                        "n_subjects": len(control),
                        "events": event_counts[roles["control"]],
                        "censored": len(control) - event_counts[roles["control"]],
                        "rmst": control_rmst,
                    },
                    roles["treatment"]: {
                        "n_subjects": len(treatment),
                        "events": event_counts[roles["treatment"]],
                        "censored": len(treatment) - event_counts[roles["treatment"]],
                        "rmst": treatment_rmst,
                    },
                },
                effect_measure="rmst_difference",
                effect=effect,
                standard_error=se,
                confidence_interval=interval,
                p_value=p_value,
                raw_p_value=p_value,
                adjusted_p_value=p_value,
                denominator=len(control) + len(treatment),
                details={
                    "restriction_time": tau,
                    "time_origin": survival.time_origin,
                    "time_unit": survival.time_unit,
                    "bootstrap_repetitions": survival.bootstrap_repetitions,
                    "bootstrap_seed": survival.bootstrap_seed,
                },
            )
        evaluation = StudyDesignEvaluation(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            eligible=result.eligible,
            qualification_checks={
                "realized_data_valid": True,
                "subject_is_independent_unit": True,
                "right_censoring_explicit": True,
                "restriction_time_frozen": True,
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
        if rule.beneficial_direction == "higher":
            result.decision = "supported" if low > 0 else "refuted" if high < 0 else "inconclusive"
        else:
            result.decision = "supported" if high < 0 else "refuted" if low > 0 else "inconclusive"
        evaluation.primary_decision = result.decision
        return evaluation

    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope:
        survival = plan.survival
        assert survival is not None
        result = evaluation.outcomes[0]
        interval = result.confidence_interval
        effect = (
            f"The treatment-minus-control restricted mean event-free time difference through {survival.restriction_time:g} {survival.time_unit} was {result.effect:.4g} {survival.time_unit}"
            + (f", with a bootstrap interval from {interval[0]:.4g} to {interval[1]:.4g}." if interval else ".")
            if result.effect is not None
            else "The registered RMST difference was not estimable."
        )
        return ClaimEnvelope(
            study_design_name="Randomized right-censored time-to-event comparison",
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
            uncertainty_method="subject-level stratified nonparametric bootstrap of the Kaplan–Meier RMST difference",
            multiplicity_method="no correction; one confirmatory estimand",
            scientific_verdict=evaluation.primary_decision,
            permitted_claims=[
                "Report the registered RMST difference only through the frozen restriction time.",
                "Report observed events and right-censored subjects separately by arm.",
            ],
            prohibited_claims=[
                "Do not treat a censored subject as having experienced the event.",
                "Do not claim a constant hazard ratio or proportional hazards.",
                "Do not infer competing-risk, recurrent-event, or causal observational effects.",
                "Do not extrapolate survival beyond the frozen restriction time.",
            ],
            generalization_boundary=plan.claim_boundary.get(
                "generalization", "Only the frozen randomized survival population is represented."
            ),
            reproduction_materials=list(plan.claim_boundary.get("reproduction_materials", [])),
            design_details={
                "time_origin": survival.time_origin,
                "time_unit": survival.time_unit,
                "restriction_time": survival.restriction_time,
                "event_field": survival.event_field,
                "censoring_rule": survival.censoring_rule,
            },
        )


__all__ = ["SurvivalAnalysis"]
