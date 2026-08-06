"""Certified two-arm independent-group comparison Study Design."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import mean, variance
from typing import Any

from ..math_utils import student_t_cdf, student_t_ppf
from ..schemas import (
    AnalysisPlan,
    ClaimEnvelope,
    CompletionIssue,
    RepairClass,
    StudyDesignCompletionPatch,
    StudyDesignEvaluation,
    StudyDesignMaturity,
    OutcomeEvaluation,
)
from ..sdk import StudyDesignDescriptor


def _payload(contract: Any) -> dict[str, Any]:
    value = getattr(contract, "study_design_spec", None)
    if value is None and isinstance(contract, dict):
        value = contract.get("study_design_spec")
    return dict(value or {})


def _as_plan(contract: Any) -> AnalysisPlan:
    data = _payload(contract)
    data.setdefault("study_design_id", "independent_group_comparison_v1")
    data.setdefault("study_design_version", "1")
    return AnalysisPlan.model_validate(data)


class IndependentGroupComparison:
    descriptor = StudyDesignDescriptor(
        design_id="independent_group_comparison_v1",
        version="1",
        title="Two independent groups",
        summary=(
            "Compares two non-overlapping groups on preregistered continuous "
            "or binary outcomes with explicit denominators and missingness."
        ),
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
        known_limits=(
            "exactly two arms",
            "no repeated, matched, clustered, survival, or causal estimands",
            "unadjusted comparisons only",
        ),
    )

    def qualify(self, contract: Any, resources: Any | None = None) -> dict[str, Any]:
        violations = self.validate_contract(contract)
        return {"qualified": not violations, "violations": violations}

    def complete_contract(
        self, task_brief: Any, draft_contract: Any, resources: Any | None = None
    ) -> StudyDesignCompletionPatch:
        data = _payload(draft_contract)
        issues: list[CompletionIssue] = []
        required = {
            "unit_structure": "Define observation, assignment, analysis, and independent units.",
            "allocation": "State whether assignment was randomized and cite evidence.",
            "arms": "Define exactly two non-overlapping arms.",
            "outcomes": "Register one primary outcome and any secondary outcomes.",
            "estimands": "Bind each outcome to its effect measure and arms.",
            "missingness": "Freeze an explicit missing-data and denominator rule.",
            "decision_rules": "Freeze the scientific decision rule before execution.",
        }
        for field, expected in required.items():
            if not data.get(field):
                issues.append(
                    CompletionIssue(
                        issue_id=f"independent-groups-{field}-missing",
                        field_path=f"study_design_spec.{field}",
                        severity="error",
                        repair_class=RepairClass.OWNER_SCIENTIFIC_DECISION,
                        expected=expected,
                        observed=data.get(field),
                        provenance="independent_group_comparison_v1 schema",
                        owner_approval_required=True,
                    )
                )
        return StudyDesignCompletionPatch(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            issues=issues,
        )

    def validate_contract(self, contract: Any) -> list[str]:
        try:
            plan = _as_plan(contract)
        except Exception as exc:
            return [f"invalid independent-group analysis plan: {exc}"]
        violations: list[str] = []
        if len(plan.arms) != 2:
            violations.append("independent-group comparison requires exactly two arms")
        if {arm.role for arm in plan.arms} != {"control", "treatment"}:
            violations.append("arms must contain one control and one treatment")
        if len({arm.arm_id for arm in plan.arms}) != len(plan.arms):
            violations.append("arm identifiers must be distinct")
        if plan.unit_structure.cluster_unit:
            violations.append("clustered data require another Study Design")
        if plan.unit_structure.repeated_measure_unit:
            violations.append("repeated measures require another Study Design")
        if plan.unit_structure.analysis_unit != plan.unit_structure.independent_unit:
            violations.append("analysis units must be independent in this design")
        primary = [outcome for outcome in plan.outcomes if outcome.role == "primary"]
        if len(primary) != 1:
            violations.append("exactly one primary outcome is required")
        outcome_ids = {outcome.outcome_id for outcome in plan.outcomes}
        if len(outcome_ids) != len(plan.outcomes):
            violations.append("outcome identifiers must be unique")
        if set(plan.estimator_plan) != outcome_ids:
            violations.append("each outcome requires exactly one estimator")
        if set(plan.decision_rules) != outcome_ids:
            violations.append("each outcome requires exactly one decision rule")
        arm_by_role = {arm.role: arm.arm_id for arm in plan.arms}
        for estimand in plan.estimands:
            if estimand.outcome_id not in outcome_ids:
                violations.append("each estimand must reference a registered outcome")
            if estimand.control_arm_id != arm_by_role.get("control"):
                violations.append("estimand control arm must reference the registered control")
            if estimand.treatment_arm_id != arm_by_role.get("treatment"):
                violations.append("estimand treatment arm must reference the registered treatment")
            if estimand.control_arm_id == estimand.treatment_arm_id:
                violations.append("baseline and treatment cannot reference the same arm")
        for outcome in plan.outcomes:
            estimator = plan.estimator_plan.get(outcome.outcome_id)
            if not estimator:
                continue
            if outcome.kind == "continuous" and estimator.estimator_id != "welch_mean_difference_v1":
                violations.append(f"continuous outcome {outcome.label} requires Welch estimation")
            if outcome.kind == "binary" and estimator.estimator_id == "welch_mean_difference_v1":
                violations.append(f"binary outcome {outcome.label} cannot use a continuous estimator")
            rule = plan.decision_rules.get(outcome.outcome_id)
            if rule and rule.beneficial_direction != outcome.beneficial_direction:
                violations.append(
                    f"decision direction for {outcome.label} contradicts the outcome direction"
                )
        if len(plan.multiplicity.hypothesis_ids) > 1 and plan.multiplicity.method == "no_correction":
            violations.append("multiple confirmatory hypotheses require a frozen multiplicity plan")
        confirmatory_ids = {
            item.outcome_id for item in plan.outcomes
            if item.role in {"primary", "secondary"}
        }
        if set(plan.multiplicity.hypothesis_ids) != confirmatory_ids:
            violations.append(
                "multiplicity family must equal the preregistered confirmatory outcomes"
            )
        return violations

    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan:
        violations = self.validate_contract(contract)
        if violations:
            raise ValueError("; ".join(violations))
        return _as_plan(contract)

    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]:
        errors: list[str] = []
        arm_ids = {arm.arm_id for arm in plan.arms}
        subject_arms: dict[str, set[str]] = defaultdict(set)
        subject_counts: Counter[str] = Counter()
        required = {"subject_id", "arm"}
        for index, row in enumerate(rows):
            missing = sorted(required - set(row))
            if missing:
                errors.append(f"row {index} is missing {', '.join(missing)}")
                continue
            subject = str(row["subject_id"])
            arm = str(row["arm"])
            if arm not in arm_ids:
                errors.append(f"row {index} uses an unregistered arm")
            subject_arms[subject].add(arm)
            subject_counts[subject] += 1
        if any(len(arms) > 1 for arms in subject_arms.values()):
            errors.append("the same subject appears in both independent arms")
        if any(count > 1 for count in subject_counts.values()):
            errors.append("repeated observations require a longitudinal Study Design")
        for outcome in plan.outcomes:
            if not any(outcome.field in row for row in rows):
                errors.append(f"outcome field is absent: {outcome.label}")
        return list(dict.fromkeys(errors))

    def _continuous(
        self, plan: AnalysisPlan, outcome: Any, rows: list[dict[str, Any]]
    ) -> OutcomeEvaluation:
        arms = {arm.role: arm.arm_id for arm in plan.arms}
        values: dict[str, list[float]] = {arm_id: [] for arm_id in arms.values()}
        missing = 0
        for row in rows:
            value = row.get(outcome.field)
            if value is None or value == "":
                missing += 1
                continue
            values[str(row["arm"])].append(float(value))
        control, treatment = values[arms["control"]], values[arms["treatment"]]
        if min(len(control), len(treatment)) < 2:
            return OutcomeEvaluation(
                outcome_id=outcome.outcome_id, kind="continuous", eligible=False,
                arm_statistics={}, effect_measure="mean_difference",
                missing_count=missing, denominator=len(control) + len(treatment),
                decision="unverifiable", details={"reason": "each arm requires at least two observations"},
            )
        mc, mt = mean(control), mean(treatment)
        vc, vt = variance(control), variance(treatment)
        effect = mt - mc
        se2 = vc / len(control) + vt / len(treatment)
        se = math.sqrt(se2)
        df = se2 * se2 / (
            (vc / len(control)) ** 2 / (len(control) - 1)
            + (vt / len(treatment)) ** 2 / (len(treatment) - 1)
        ) if se > 0 else math.inf
        confidence = plan.inference_plan[outcome.outcome_id].confidence_level
        critical = student_t_ppf(0.5 + confidence / 2, df) if math.isfinite(df) else 0.0
        ci = (effect - critical * se, effect + critical * se)
        p = 2 * (1 - student_t_cdf(abs(effect / se), df)) if se > 0 else (0.0 if effect else 1.0)
        pooled = math.sqrt(((len(control)-1)*vc + (len(treatment)-1)*vt) / (len(control)+len(treatment)-2))
        raw_d = effect / pooled if pooled else None
        correction = 1 - 3 / (4 * (len(control)+len(treatment)) - 9)
        hedges_g = raw_d * correction if raw_d is not None else None
        return OutcomeEvaluation(
            outcome_id=outcome.outcome_id, kind="continuous", eligible=True,
            arm_statistics={
                arms["control"]: {"n": len(control), "mean": mc, "sd": math.sqrt(vc)},
                arms["treatment"]: {"n": len(treatment), "mean": mt, "sd": math.sqrt(vt)},
            },
            effect_measure="mean_difference", effect=effect, standard_error=se,
            confidence_interval=ci, p_value=p, raw_p_value=p,
            degrees_of_freedom=df if math.isfinite(df) else None,
            missing_count=missing, denominator=len(control)+len(treatment),
            details={"hedges_g": hedges_g, "uncertainty": "Welch t interval"},
        )

    def _binary(
        self, plan: AnalysisPlan, outcome: Any, rows: list[dict[str, Any]]
    ) -> OutcomeEvaluation:
        arms = {arm.role: arm.arm_id for arm in plan.arms}
        counts = {arm_id: [0, 0] for arm_id in arms.values()}
        missing = 0
        for row in rows:
            value = row.get(outcome.field)
            if value is None or value == "":
                missing += 1
                continue
            counts[str(row["arm"])][1] += 1
            if value == outcome.event_value:
                counts[str(row["arm"])][0] += 1
        ec, nc = counts[arms["control"]]
        et, nt = counts[arms["treatment"]]
        if min(nc, nt) < 1:
            return OutcomeEvaluation(
                outcome_id=outcome.outcome_id, kind="binary", eligible=False,
                arm_statistics={}, effect_measure="risk_difference",
                missing_count=missing, denominator=nc+nt, decision="unverifiable",
                details={"reason": "both arms require observed outcomes"},
            )
        pc, pt = ec/nc, et/nt
        risk_difference = pt - pc
        risk_ratio = pt / pc if pc > 0 else None
        control_non_events = nc - ec
        treatment_non_events = nt - et
        odds_ratio = (
            (et / treatment_non_events) / (ec / control_non_events)
            if min(ec, et, control_non_events, treatment_non_events) > 0
            else None
        )
        measure = next(item.effect_measure for item in plan.estimands if item.outcome_id == outcome.outcome_id)
        if measure == "risk_difference":
            effect = pt-pc
            se = math.sqrt(pt*(1-pt)/nt + pc*(1-pc)/nc)
        elif measure == "risk_ratio":
            if min(ec, et) == 0:
                return OutcomeEvaluation(
                    outcome_id=outcome.outcome_id, kind="binary", eligible=False,
                    arm_statistics={}, effect_measure=measure, missing_count=missing,
                    denominator=nc+nt, decision="unverifiable",
                    details={"reason": "risk-ratio log interval requires nonzero events"},
                )
            effect = pt/pc
            se = math.sqrt(1/et - 1/nt + 1/ec - 1/nc)
        else:
            if min(ec, et, nc-ec, nt-et) == 0:
                return OutcomeEvaluation(
                    outcome_id=outcome.outcome_id, kind="binary", eligible=False,
                    arm_statistics={}, effect_measure=measure, missing_count=missing,
                    denominator=nc+nt, decision="unverifiable",
                    details={"reason": "odds-ratio log interval requires nonzero cells"},
                )
            effect = (et/(nt-et))/(ec/(nc-ec))
            se = math.sqrt(1/et + 1/(nt-et) + 1/ec + 1/(nc-ec))
        confidence = plan.inference_plan[outcome.outcome_id].confidence_level
        z = 1.959963984540054 if confidence == 0.95 else 1.959963984540054
        if measure == "risk_difference":
            ci = (effect-z*se, effect+z*se)
            statistic = effect/se if se else math.inf
        else:
            log_effect = math.log(effect)
            ci = (math.exp(log_effect-z*se), math.exp(log_effect+z*se))
            statistic = log_effect/se if se else math.inf
        p = 2*(1-0.5*(1+math.erf(abs(statistic)/math.sqrt(2))))
        return OutcomeEvaluation(
            outcome_id=outcome.outcome_id, kind="binary", eligible=True,
            arm_statistics={
                arms["control"]: {"n": nc, "events": ec, "proportion": pc},
                arms["treatment"]: {"n": nt, "events": et, "proportion": pt},
            }, effect_measure=measure, effect=effect, standard_error=se,
            confidence_interval=ci, p_value=p, raw_p_value=p,
            missing_count=missing, denominator=nc+nt,
            details={
                "uncertainty": "large-sample Wald interval",
                "risk_difference": risk_difference,
                "risk_ratio": risk_ratio,
                "odds_ratio": odds_ratio,
            },
        )

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
                outcomes=[], primary_decision="unverifiable", limitations=errors,
            )
        outcomes = [
            self._continuous(plan, outcome, rows)
            if outcome.kind == "continuous" else self._binary(plan, outcome, rows)
            for outcome in plan.outcomes
        ]
        from ..inference.multiplicity import adjust_p_values

        raw_values = {
            item.outcome_id: item.raw_p_value
            for item in outcomes
            if item.raw_p_value is not None
            and item.outcome_id in plan.multiplicity.hypothesis_ids
        }
        adjusted = adjust_p_values(raw_values, plan.multiplicity.method)
        for item in outcomes:
            item.adjusted_p_value = adjusted.get(item.outcome_id)
        result = StudyDesignEvaluation(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            eligible=all(item.eligible for item in outcomes),
            qualification_checks={
                "realized_data_valid": True,
                "two_non_overlapping_arms": True,
                "denominators_explicit": True,
            }, outcomes=outcomes, primary_decision="inconclusive",
        )
        return self.adjudicate(plan, result)

    def adjudicate(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> StudyDesignEvaluation:
        by_id = {outcome.outcome_id: outcome for outcome in plan.outcomes}
        for result in evaluation.outcomes:
            if not result.eligible or not result.confidence_interval:
                result.decision = "unverifiable"
                continue
            rule = plan.decision_rules[result.outcome_id]
            low, high = result.confidence_interval
            null_value = (
                1.0
                if result.effect_measure in {"risk_ratio", "odds_ratio"}
                else 0.0
            )
            if rule.mode == "superiority":
                result.decision = "supported" if (
                    (rule.beneficial_direction == "higher" and low > null_value)
                    or (rule.beneficial_direction == "lower" and high < null_value)
                ) else "inconclusive"
            elif rule.mode == "noninferiority":
                boundary = -rule.margin if rule.beneficial_direction == "higher" else rule.margin
                result.decision = "supported" if (
                    low > boundary if rule.beneficial_direction == "higher" else high < boundary
                ) else "inconclusive"
            else:
                result.decision = "supported" if low > rule.lower_margin and high < rule.upper_margin else "inconclusive"
        primary = [item for item in evaluation.outcomes if by_id[item.outcome_id].role == "primary"]
        evaluation.primary_decision = primary[0].decision if primary else "unverifiable"
        evaluation.eligible = bool(primary and primary[0].eligible)
        return evaluation

    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope:
        by_id = {outcome.outcome_id: outcome for outcome in plan.outcomes}
        effects = []
        for item in evaluation.outcomes:
            label = by_id[item.outcome_id].label
            if item.effect is None:
                effects.append(f"{label} could not be estimated from eligible observations.")
            else:
                interval = item.confidence_interval
                effects.append(
                    f"For {label}, the estimated {item.effect_measure.replace('_', ' ')} was "
                    f"{item.effect:.4g}" + (f" with an interval from {interval[0]:.4g} to {interval[1]:.4g}." if interval else ".")
                )
        causal_forbidden = plan.allocation.mechanism != "randomized"
        permitted = [
            "Describe the observed between-group association and its frozen uncertainty interval.",
            "Report missing observations and arm-specific denominators explicitly.",
        ]
        prohibited = ["Do not claim causality because random allocation was not verified."] if causal_forbidden else []
        return ClaimEnvelope(
            study_design_name="Two independent groups",
            arm_definitions=[f"{arm.label}: {arm.definition}" for arm in plan.arms],
            allocation_verified_randomized=not causal_forbidden,
            observation_unit=plan.unit_structure.observation_unit,
            analysis_unit=plan.unit_structure.analysis_unit,
            independent_unit=plan.unit_structure.independent_unit,
            primary_outcomes=[item.label for item in plan.outcomes if item.role == "primary"],
            secondary_outcomes=[item.label for item in plan.outcomes if item.role == "secondary"],
            denominator=sum(item.denominator for item in evaluation.outcomes if by_id[item.outcome_id].role == "primary"),
            missing_count=sum(item.missing_count for item in evaluation.outcomes),
            excluded_count=sum(item.excluded_count for item in evaluation.outcomes),
            effect_estimates=effects,
            uncertainty_method="Welch intervals for continuous outcomes and registered large-sample intervals for binary outcomes",
            multiplicity_method=plan.multiplicity.method.replace("_", " "),
            scientific_verdict=evaluation.primary_decision,
            permitted_claims=permitted,
            prohibited_claims=prohibited,
            generalization_boundary=plan.claim_boundary.get("generalization", "Only the frozen eligible population is represented."),
            reproduction_materials=list(plan.claim_boundary.get("reproduction_materials", [])),
        )


__all__ = ["IndependentGroupComparison"]
