"""Independent black-box recalculator for acceptance comparisons.

This module intentionally does not import or call the certified Study Design
evaluator. It consumes a frozen plan plus flat rows and implements its own
continuous primary-effect calculation.
"""

from __future__ import annotations

import math
from statistics import mean, variance
from typing import Any

from .math_utils import student_t_cdf, student_t_ppf
from .schemas import AnalysisPlan


def recalculate_continuous_primary(
    plan: AnalysisPlan, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    primary = next(item for item in plan.outcomes if item.role == "primary")
    if primary.kind != "continuous":
        raise ValueError("reference recalculator supports a continuous primary outcome")
    arm_by_role = {arm.role: arm.arm_id for arm in plan.arms}
    control = [
        float(row[primary.field]) for row in rows
        if str(row.get("arm")) == arm_by_role["control"]
        and row.get(primary.field) not in {None, ""}
    ]
    treatment = [
        float(row[primary.field]) for row in rows
        if str(row.get("arm")) == arm_by_role["treatment"]
        and row.get(primary.field) not in {None, ""}
    ]
    vc, vt = variance(control), variance(treatment)
    effect = mean(treatment) - mean(control)
    se2 = vc / len(control) + vt / len(treatment)
    se = math.sqrt(se2)
    df = se2**2 / (
        (vc / len(control)) ** 2 / (len(control) - 1)
        + (vt / len(treatment)) ** 2 / (len(treatment) - 1)
    )
    confidence = plan.inference_plan[primary.outcome_id].confidence_level
    critical = student_t_ppf(0.5 + confidence / 2, df)
    return {
        "control_n": len(control), "treatment_n": len(treatment),
        "control_mean": mean(control), "treatment_mean": mean(treatment),
        "effect": effect, "standard_error": se,
        "degrees_of_freedom": df,
        "confidence_interval": [effect-critical*se, effect+critical*se],
        "missing_count": len(rows) - len(control) - len(treatment),
        "denominator": len(control) + len(treatment),
    }


def _adjust_p_values_reference(
    values: dict[str, float], method: str
) -> dict[str, float]:
    """Independent multiplicity implementation used only for acceptance."""

    if method == "no_correction":
        return dict(values)
    count = len(values)
    if method == "bonferroni":
        return {key: min(1.0, value * count) for key, value in values.items()}
    ordered = sorted(values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    if method == "holm":
        running = 0.0
        for index, (key, value) in enumerate(ordered):
            running = max(running, (count - index) * value)
            adjusted[key] = min(1.0, running)
    elif method == "benjamini_hochberg":
        running = 1.0
        for rank in range(count, 0, -1):
            key, value = ordered[rank - 1]
            running = min(running, value * count / rank)
            adjusted[key] = min(1.0, running)
    else:
        raise ValueError(f"unsupported multiplicity method: {method}")
    return {key: adjusted[key] for key in values}


def _reference_decision(
    interval: tuple[float, float], *, measure: str, rule: Any
) -> str:
    low, high = interval
    null = 1.0 if measure in {"risk_ratio", "odds_ratio"} else 0.0
    if rule.mode == "superiority":
        passed = (
            low > null if rule.beneficial_direction == "higher" else high < null
        )
    elif rule.mode == "noninferiority":
        if rule.margin is None:
            raise ValueError("reference noninferiority decision requires a margin")
        passed = (
            low > -rule.margin
            if rule.beneficial_direction == "higher"
            else high < rule.margin
        )
    elif rule.mode == "equivalence":
        if rule.lower_margin is None or rule.upper_margin is None:
            raise ValueError("reference equivalence decision requires two margins")
        passed = low > rule.lower_margin and high < rule.upper_margin
    else:
        raise ValueError(f"unsupported decision mode: {rule.mode}")
    return "supported" if passed else "inconclusive"


def recalculate_independent_group(
    plan: AnalysisPlan, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Rebuild all registered summaries without calling the production evaluator."""

    arm_by_role = {arm.role: arm.arm_id for arm in plan.arms}
    results: dict[str, dict[str, Any]] = {}
    raw_p: dict[str, float] = {}
    for outcome in plan.outcomes:
        observed = {
            arm_id: [
                row[outcome.field]
                for row in rows
                if str(row.get("arm")) == arm_id
                and row.get(outcome.field) not in {None, ""}
            ]
            for arm_id in arm_by_role.values()
        }
        control = observed[arm_by_role["control"]]
        treatment = observed[arm_by_role["treatment"]]
        denominator = len(control) + len(treatment)
        missing = len(rows) - denominator
        estimand = next(
            item for item in plan.estimands if item.outcome_id == outcome.outcome_id
        )
        confidence = plan.inference_plan[outcome.outcome_id].confidence_level
        if outcome.kind == "continuous":
            control_values = [float(value) for value in control]
            treatment_values = [float(value) for value in treatment]
            vc, vt = variance(control_values), variance(treatment_values)
            effect = mean(treatment_values) - mean(control_values)
            se2 = vc / len(control_values) + vt / len(treatment_values)
            se = math.sqrt(se2)
            df = se2**2 / (
                (vc / len(control_values)) ** 2 / (len(control_values) - 1)
                + (vt / len(treatment_values)) ** 2 / (len(treatment_values) - 1)
            )
            critical = student_t_ppf(0.5 + confidence / 2, df)
            interval = (effect - critical * se, effect + critical * se)
            p_value = 2 * (1 - student_t_cdf(abs(effect / se), df))
            arm_statistics = {
                arm_by_role["control"]: {
                    "n": len(control_values),
                    "mean": mean(control_values),
                    "sd": math.sqrt(vc),
                },
                arm_by_role["treatment"]: {
                    "n": len(treatment_values),
                    "mean": mean(treatment_values),
                    "sd": math.sqrt(vt),
                },
            }
        else:
            ec = sum(value == outcome.event_value for value in control)
            et = sum(value == outcome.event_value for value in treatment)
            nc, nt = len(control), len(treatment)
            pc, pt = ec / nc, et / nt
            alternate_binary_effects = {
                "risk_difference": pt - pc,
                "risk_ratio": pt / pc if pc > 0 else None,
                "odds_ratio": (
                    (et / (nt - et)) / (ec / (nc - ec))
                    if min(ec, et, nc - ec, nt - et) > 0
                    else None
                ),
            }
            if estimand.effect_measure == "risk_difference":
                effect = pt - pc
                se = math.sqrt(pt * (1 - pt) / nt + pc * (1 - pc) / nc)
                interval = (effect - 1.959963984540054 * se, effect + 1.959963984540054 * se)
                statistic = effect / se if se else math.inf
            elif estimand.effect_measure == "risk_ratio":
                effect = pt / pc
                se = math.sqrt(1 / et - 1 / nt + 1 / ec - 1 / nc)
                log_effect = math.log(effect)
                interval = (
                    math.exp(log_effect - 1.959963984540054 * se),
                    math.exp(log_effect + 1.959963984540054 * se),
                )
                statistic = log_effect / se
            else:
                effect = (et / (nt - et)) / (ec / (nc - ec))
                se = math.sqrt(1 / et + 1 / (nt - et) + 1 / ec + 1 / (nc - ec))
                log_effect = math.log(effect)
                interval = (
                    math.exp(log_effect - 1.959963984540054 * se),
                    math.exp(log_effect + 1.959963984540054 * se),
                )
                statistic = log_effect / se
            p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(statistic) / math.sqrt(2))))
            arm_statistics = {
                arm_by_role["control"]: {"n": nc, "events": ec, "proportion": pc},
                arm_by_role["treatment"]: {"n": nt, "events": et, "proportion": pt},
            }
        raw_p[outcome.outcome_id] = p_value
        results[outcome.outcome_id] = {
            "kind": outcome.kind,
            "arm_statistics": arm_statistics,
            "effect_measure": estimand.effect_measure,
            "effect": effect,
            "confidence_interval": list(interval),
            "raw_p_value": p_value,
            "denominator": denominator,
            "missing_count": missing,
            "decision": _reference_decision(
                interval,
                measure=estimand.effect_measure,
                rule=plan.decision_rules[outcome.outcome_id],
            ),
        }
        if outcome.kind == "binary":
            results[outcome.outcome_id]["alternate_effects"] = (
                alternate_binary_effects
            )
    adjusted = _adjust_p_values_reference(
        {
            key: value
            for key, value in raw_p.items()
            if key in plan.multiplicity.hypothesis_ids
        },
        plan.multiplicity.method,
    )
    for outcome_id, value in adjusted.items():
        results[outcome_id]["adjusted_p_value"] = value
    primary_id = next(item.outcome_id for item in plan.outcomes if item.role == "primary")
    return {
        "outcomes": results,
        "multiplicity_method": plan.multiplicity.method,
        "primary_decision": results[primary_id]["decision"],
    }


__all__ = ["recalculate_continuous_primary", "recalculate_independent_group"]
