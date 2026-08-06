"""Independent recalculator for the blinded human-rating acceptance fixture."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, variance
from typing import Any

from .math_utils import student_t_cdf, student_t_ppf
from .schemas import AnalysisPlan


def _reference_icc(matrix: list[list[float]]) -> float:
    n, k = len(matrix), len(matrix[0])
    rows = [mean(item) for item in matrix]
    columns = [mean(matrix[i][j] for i in range(n)) for j in range(k)]
    grand = mean(rows)
    ms_rows = k * sum((value - grand) ** 2 for value in rows) / (n - 1)
    ms_columns = n * sum((value - grand) ** 2 for value in columns) / (k - 1)
    ms_error = sum(
        (matrix[i][j] - rows[i] - columns[j] + grand) ** 2
        for i in range(n)
        for j in range(k)
    ) / ((n - 1) * (k - 1))
    denominator = ms_rows + (k - 1) * ms_error + (k / n) * (
        ms_columns - ms_error
    )
    return (ms_rows - ms_error) / denominator if denominator else 1.0


def recalculate_human_rating(
    plan: AnalysisPlan, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    spec = plan.human_rating
    if spec is None:
        return {"eligible": False, "reason": "human-rating extension missing"}
    outcome = plan.outcomes[0]
    by_item_arm: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_output_rater: dict[str, dict[str, float]] = defaultdict(dict)
    output_order: list[str] = []
    for row in rows:
        item = str(row[spec.item_id_field])
        arm = str(row["arm"])
        output = str(row[spec.output_id_field])
        rater = str(row[spec.rater_id_field])
        score = float(row[outcome.field])
        by_item_arm[(item, arm)].append(score)
        if output not in by_output_rater:
            output_order.append(output)
        by_output_rater[output][rater] = score
    items = sorted({item for item, _ in by_item_arm})
    differences = [
        mean(by_item_arm[(item, "treatment")])
        - mean(by_item_arm[(item, "control")])
        for item in items
    ]
    effect = mean(differences)
    df = len(differences) - 1
    standard_error = math.sqrt(variance(differences) / len(differences))
    confidence = plan.inference_plan[outcome.outcome_id].confidence_level
    critical = student_t_ppf(0.5 + confidence / 2, df)
    interval = (
        effect - critical * standard_error,
        effect + critical * standard_error,
    )
    p_value = (
        2 * (1 - student_t_cdf(abs(effect / standard_error), df))
        if standard_error
        else (0.0 if effect else 1.0)
    )
    reliability = _reference_icc(
        [
            [by_output_rater[output][rater] for rater in spec.rater_panel_ids]
            for output in output_order
        ]
    )
    decision = (
        "supported"
        if reliability >= spec.minimum_reliability and interval[0] > 0
        else "inconclusive"
    )
    return {
        "eligible": reliability >= spec.minimum_reliability,
        "primary_decision": decision,
        "inter_rater_reliability": reliability,
        "outcomes": {
            outcome.outcome_id: {
                "effect": effect,
                "standard_error": standard_error,
                "confidence_interval": list(interval),
                "p_value": p_value,
                "denominator": len(items),
                "missing_count": 0,
                "decision": decision,
            }
        },
    }


__all__ = ["recalculate_human_rating"]
