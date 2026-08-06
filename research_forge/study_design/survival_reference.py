"""Independent recalculation for the right-censored RMST kernel.

This module deliberately does not import or call the production evaluator.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import stdev
from typing import Any

from .schemas import AnalysisPlan


def _area_under_product_limit(
    observations: list[tuple[float, bool]], horizon: float
) -> float:
    probability = 1.0
    total = 0.0
    left = 0.0
    observed_times = sorted(
        {time for time, _ in observations if time <= horizon}
    )
    for current in observed_times:
        total += probability * (current - left)
        risk_set = [item for item in observations if item[0] >= current]
        failures = sum(
            time == current and event for time, event in observations
        )
        if failures:
            probability *= (len(risk_set) - failures) / len(risk_set)
        left = current
    return total + probability * (horizon - left)


def _quantile(values: list[float], q: float) -> float:
    values = sorted(values)
    location = q * (len(values) - 1)
    lo, hi = math.floor(location), math.ceil(location)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (location - lo)


def recalculate_survival(
    plan: AnalysisPlan, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    extension = plan.survival
    if extension is None:
        raise ValueError("reference recalculation requires a survival extension")
    grouped: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["arm"])].append((
            float(row[extension.duration_field]),
            row[extension.event_field] == extension.event_value,
        ))
    role = {arm.role: arm.arm_id for arm in plan.arms}
    control = grouped[role["control"]]
    treatment = grouped[role["treatment"]]
    event_counts = {
        arm: sum(event for _, event in observations)
        for arm, observations in grouped.items()
    }
    eligible = (
        min(len(control), len(treatment)) >= extension.minimum_subjects_per_arm
        and min(event_counts.values()) >= extension.minimum_events_per_arm
    )
    if not eligible:
        return {
            "eligible": False,
            "primary_decision": "unverifiable",
            "denominator": len(control) + len(treatment),
            "missing_count": 0,
            "excluded_count": 0,
        }
    horizon = extension.restriction_time
    arm_rmst = {
        role["control"]: _area_under_product_limit(control, horizon),
        role["treatment"]: _area_under_product_limit(treatment, horizon),
    }
    effect = arm_rmst[role["treatment"]] - arm_rmst[role["control"]]
    rng = random.Random(extension.bootstrap_seed)
    draws: list[float] = []
    for _ in range(extension.bootstrap_repetitions):
        c = [control[rng.randrange(len(control))] for _ in control]
        t = [treatment[rng.randrange(len(treatment))] for _ in treatment]
        draws.append(
            _area_under_product_limit(t, horizon)
            - _area_under_product_limit(c, horizon)
        )
    confidence = plan.inference_plan[plan.outcomes[0].outcome_id].confidence_level
    tail = (1 - confidence) / 2
    interval = [_quantile(draws, tail), _quantile(draws, 1 - tail)]
    p_value = min(
        1.0,
        2 * min(
            sum(value <= 0 for value in draws) / len(draws),
            sum(value >= 0 for value in draws) / len(draws),
        ),
    )
    direction = plan.decision_rules[plan.outcomes[0].outcome_id].beneficial_direction
    if direction == "higher":
        decision = "supported" if interval[0] > 0 else "refuted" if interval[1] < 0 else "inconclusive"
    else:
        decision = "supported" if interval[1] < 0 else "refuted" if interval[0] > 0 else "inconclusive"
    outcome = {
        "effect": effect,
        "standard_error": stdev(draws),
        "confidence_interval": interval,
        "p_value": p_value,
        "denominator": len(control) + len(treatment),
        "missing_count": 0,
        "excluded_count": 0,
        "decision": decision,
    }
    return {
        "eligible": True,
        "arm_rmst": arm_rmst,
        **outcome,
        "outcomes": {plan.outcomes[0].outcome_id: outcome},
        "primary_decision": decision,
    }


__all__ = ["recalculate_survival"]
