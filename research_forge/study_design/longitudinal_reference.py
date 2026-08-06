"""Independent recalculation for the linear repeated-measures kernel.

This module deliberately does not import or invoke the production evaluator.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, variance
from typing import Any

from .math_utils import student_t_cdf, student_t_ppf
from .schemas import AnalysisPlan


def _linear_slope(points: list[tuple[float, float]]) -> float:
    x_bar = sum(item[0] for item in points) / len(points)
    y_bar = sum(item[1] for item in points) / len(points)
    denominator = sum((item[0] - x_bar) ** 2 for item in points)
    if denominator <= 0:
        raise ValueError("reference trajectory has no time variation")
    return sum((x - x_bar) * (y - y_bar) for x, y in points) / denominator


def recalculate_longitudinal(
    plan: AnalysisPlan, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    extension = plan.longitudinal
    if extension is None:
        raise ValueError("reference recalculation requires a longitudinal extension")
    outcome = plan.outcomes[0]
    trajectories: dict[str, dict[str, Any]] = {}
    missing = 0
    for row in rows:
        subject = str(row[extension.subject_id_field])
        entry = trajectories.setdefault(subject, {"arm": str(row["arm"]), "points": []})
        if entry["arm"] != str(row["arm"]):
            raise ValueError("reference recalculation detected an arm switch")
        value = row.get(outcome.field)
        if value is None or value == "":
            missing += 1
            continue
        entry["points"].append((float(row[extension.time_field]), float(value)))

    slopes: dict[str, list[float]] = defaultdict(list)
    excluded = 0
    for entry in trajectories.values():
        if len(entry["points"]) < extension.minimum_observed_time_points:
            excluded += 1
            continue
        slopes[entry["arm"]].append(_linear_slope(entry["points"]))

    role = {arm.role: arm.arm_id for arm in plan.arms}
    control = slopes[role["control"]]
    treatment = slopes[role["treatment"]]
    if min(len(control), len(treatment)) < extension.minimum_subjects_per_arm:
        return {
            "eligible": False,
            "primary_decision": "unverifiable",
            "denominator": len(control) + len(treatment),
            "missing_count": missing,
            "excluded_count": excluded,
        }

    effect = mean(treatment) - mean(control)
    control_variance = variance(control)
    treatment_variance = variance(treatment)
    standard_error_squared = control_variance / len(control) + treatment_variance / len(treatment)
    standard_error = math.sqrt(standard_error_squared)
    degrees_of_freedom = standard_error_squared**2 / (
        (control_variance / len(control)) ** 2 / (len(control) - 1)
        + (treatment_variance / len(treatment)) ** 2 / (len(treatment) - 1)
    )
    confidence = plan.inference_plan[outcome.outcome_id].confidence_level
    critical = student_t_ppf(0.5 + confidence / 2.0, degrees_of_freedom)
    interval = [
        effect - critical * standard_error,
        effect + critical * standard_error,
    ]
    p_value = 2 * (
        1 - student_t_cdf(abs(effect / standard_error), degrees_of_freedom)
    )
    direction = plan.decision_rules[outcome.outcome_id].beneficial_direction
    decision = (
        "supported"
        if (interval[0] > 0 if direction == "higher" else interval[1] < 0)
        else "inconclusive"
    )
    outcome_result = {
        "effect": effect,
        "standard_error": standard_error,
        "confidence_interval": interval,
        "p_value": p_value,
        "degrees_of_freedom": degrees_of_freedom,
        "denominator": len(control) + len(treatment),
        "missing_count": missing,
        "excluded_count": excluded,
        "decision": decision,
    }
    return {
        "eligible": True,
        "arm_slopes": {role["control"]: control, role["treatment"]: treatment},
        **outcome_result,
        "outcomes": {outcome.outcome_id: outcome_result},
        "primary_decision": decision,
    }


__all__ = ["recalculate_longitudinal"]
