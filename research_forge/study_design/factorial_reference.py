"""Independent reference recalculator for the 2x2 factorial kernel.

This module intentionally does not import or call the production factorial
evaluator.  It reconstructs the design matrix, OLS coefficients, HC2
covariance, intervals, multiplicity adjustment, and decisions from frozen rows.
"""

from __future__ import annotations

import math
from typing import Any

from .inference.multiplicity import adjust_p_values
from .math_utils import student_t_cdf, student_t_ppf
from .schemas import AnalysisPlan


def _transpose(value: list[list[float]]) -> list[list[float]]:
    return [list(item) for item in zip(*value, strict=True)]


def _multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    columns = _transpose(right)
    return [[sum(x * y for x, y in zip(row, column, strict=True)) for column in columns] for row in left]


def _invert(value: list[list[float]]) -> list[list[float]]:
    size = len(value)
    work = [[*map(float, row), *[float(i == j) for j in range(size)]] for i, row in enumerate(value)]
    for position in range(size):
        selected = max(range(position, size), key=lambda row: abs(work[row][position]))
        if abs(work[selected][position]) < 1e-12:
            raise ValueError("reference factorial matrix is singular")
        work[position], work[selected] = work[selected], work[position]
        divisor = work[position][position]
        work[position] = [entry / divisor for entry in work[position]]
        for row in range(size):
            if row == position:
                continue
            multiplier = work[row][position]
            work[row] = [entry - multiplier * pivot for entry, pivot in zip(work[row], work[position], strict=True)]
    return [row[size:] for row in work]


def recalculate_factorial(plan: AnalysisPlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
    factorial = plan.factorial
    if factorial is None or len(factorial.factors) != 2:
        raise ValueError("reference recalculation requires a frozen 2x2 factorial plan")
    outcome = plan.outcomes[0]
    first, second = factorial.factors
    coding = {
        first.factor_id: {first.levels[0]: -0.5, first.levels[1]: 0.5},
        second.factor_id: {second.levels[0]: -0.5, second.levels[1]: 0.5},
    }
    matrix: list[list[float]] = []
    observed: list[float] = []
    missing = 0
    cell_counts = {cell.arm_id: 0 for cell in factorial.cells}
    for row in rows:
        raw = row.get(outcome.field)
        if raw in {None, ""}:
            missing += 1
            continue
        a = coding[first.factor_id][str(row[first.field])]
        b = coding[second.factor_id][str(row[second.field])]
        matrix.append([1.0, a, b, a * b])
        observed.append(float(raw))
        cell_counts[str(row["arm"])] += 1
    transpose = _transpose(matrix)
    inverse = _invert(_multiply(transpose, matrix))
    coefficients = [row[0] for row in _multiply(_multiply(inverse, transpose), [[value] for value in observed])]
    residuals = [
        value - sum(coefficient * field for coefficient, field in zip(coefficients, row, strict=True))
        for row, value in zip(matrix, observed, strict=True)
    ]
    dimension = len(coefficients)
    meat = [[0.0] * dimension for _ in range(dimension)]
    for row, residual in zip(matrix, residuals, strict=True):
        leverage = sum(row[i] * inverse[i][j] * row[j] for i in range(dimension) for j in range(dimension))
        if leverage >= 1.0 - 1e-12:
            raise ValueError("reference HC2 covariance has unit leverage")
        weight = residual * residual / (1.0 - leverage)
        for i in range(dimension):
            for j in range(dimension):
                meat[i][j] += weight * row[i] * row[j]
    covariance = _multiply(_multiply(inverse, meat), inverse)
    degrees_of_freedom = len(observed) - dimension
    index = {
        (first.factor_id,): 1,
        (second.factor_id,): 2,
        (first.factor_id, second.factor_id): 3,
        (second.factor_id, first.factor_id): 3,
    }
    raw: dict[str, float] = {}
    outputs: dict[str, dict[str, Any]] = {}
    for contrast in factorial.contrasts:
        position = index[tuple(contrast.factor_ids)]
        effect = coefficients[position]
        standard_error = math.sqrt(max(0.0, covariance[position][position]))
        confidence = plan.inference_plan[outcome.outcome_id].confidence_level
        critical = student_t_ppf(0.5 + confidence / 2.0, degrees_of_freedom)
        statistic = effect / standard_error if standard_error else math.inf
        p_value = 2 * (1 - student_t_cdf(abs(statistic), degrees_of_freedom))
        raw[contrast.contrast_id] = p_value
        outputs[contrast.contrast_id] = {
            "effect": effect,
            "standard_error": standard_error,
            "confidence_interval": [effect - critical * standard_error, effect + critical * standard_error],
            "raw_p_value": p_value,
            "denominator": len(observed),
            "missing_count": missing,
        }
    adjusted = adjust_p_values(
        {key: value for key, value in raw.items() if key in plan.multiplicity.hypothesis_ids},
        plan.multiplicity.method,
    )
    contrast_lookup = {item.contrast_id: item for item in factorial.contrasts}
    for contrast_id, output in outputs.items():
        contrast = contrast_lookup[contrast_id]
        low, high = output["confidence_interval"]
        direction = low > 0 if contrast.beneficial_direction == "higher" else high < 0
        output["adjusted_p_value"] = adjusted.get(contrast_id)
        output["decision"] = "supported" if direction and (output["adjusted_p_value"] is None or output["adjusted_p_value"] <= plan.multiplicity.alpha_or_q) else "inconclusive"
    primary = next(item for item in factorial.contrasts if item.role == "primary")
    return {
        "coefficients": {
            "intercept": coefficients[0],
            first.factor_id: coefficients[1],
            second.factor_id: coefficients[2],
            f"{first.factor_id}:{second.factor_id}": coefficients[3],
        },
        "cell_counts": cell_counts,
        "contrasts": outputs,
        # Generic package verification consumes the same independently
        # reconstructed outcome map across Study Design Profiles.  Keep the
        # factorial name as well for human inspection.
        "outcomes": outputs,
        "primary_decision": outputs[primary.contrast_id]["decision"],
        "multiplicity_method": plan.multiplicity.method,
    }


__all__ = ["recalculate_factorial"]
