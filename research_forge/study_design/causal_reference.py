"""Independent recalculation for the causal acceptance fixture."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np

from .schemas import AnalysisPlan


def _reference_fold(subject: str, seed: int, folds: int) -> int:
    raw = hashlib.sha256(f"{seed}:{subject}".encode("utf-8")).digest()
    return int.from_bytes(raw[:8], byteorder="big") % folds


def _reference_logistic(x: np.ndarray, a: np.ndarray) -> np.ndarray:
    beta = np.zeros(x.shape[1], dtype=float)
    penalty = np.eye(x.shape[1]) * 1e-8
    penalty[0, 0] = 0.0
    for _ in range(100):
        score_input = np.clip(x @ beta, -35.0, 35.0)
        probability = 1.0 / (1.0 + np.exp(-score_input))
        weight = np.clip(probability * (1.0 - probability), 1e-8, None)
        score = x.T @ (a - probability) - penalty @ beta
        information = x.T @ (x * weight[:, None]) + penalty
        step = np.linalg.solve(information, score)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return beta


def _reference_ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.pinv(x.T @ x) @ x.T @ y


def recalculate_causal(
    plan: AnalysisPlan, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Recompute the registered cross-fitted AIPW ATE without production code."""

    causal = plan.causal
    if causal is None:
        return {"eligible": False, "reason": "causal extension missing"}
    subjects = [str(row[causal.subject_id_field]) for row in rows]
    a = np.asarray(
        [1.0 if row[causal.treatment_field] == causal.treatment_value else 0.0 for row in rows]
    )
    y = np.asarray([float(row[causal.outcome_field]) for row in rows])
    x = np.column_stack(
        [
            np.ones(len(rows)),
            np.asarray(
                [[float(row[field]) for field in causal.adjustment_set] for row in rows],
                dtype=float,
            ),
        ]
    )
    assignment = np.asarray(
        [_reference_fold(subject, causal.fold_seed, causal.cross_fitting_folds) for subject in subjects]
    )
    e = np.empty(len(rows), dtype=float)
    m0 = np.empty(len(rows), dtype=float)
    m1 = np.empty(len(rows), dtype=float)
    for fold in range(causal.cross_fitting_folds):
        held_out = assignment == fold
        if not np.any(held_out):
            continue
        training = ~held_out
        propensity_beta = _reference_logistic(x[training], a[training])
        e[held_out] = 1.0 / (
            1.0 + np.exp(-np.clip(x[held_out] @ propensity_beta, -35.0, 35.0))
        )
        control = training & (a == 0.0)
        treatment = training & (a == 1.0)
        m0[held_out] = x[held_out] @ _reference_ols(x[control], y[control])
        m1[held_out] = x[held_out] @ _reference_ols(x[treatment], y[treatment])
    overlap = float(
        np.mean(
            (e >= causal.propensity_lower_bound)
            & (e <= causal.propensity_upper_bound)
        )
    )
    if overlap < causal.minimum_overlap_fraction:
        return {"eligible": False, "overlap_fraction": overlap}
    bounded = np.clip(e, causal.propensity_lower_bound, causal.propensity_upper_bound)
    pseudo = m1 - m0 + a * (y - m1) / bounded - (1.0 - a) * (y - m0) / (1.0 - bounded)
    effect = float(np.mean(pseudo))
    standard_error = float(np.std(pseudo, ddof=1) / math.sqrt(len(rows)))
    critical = 1.959963984540054
    interval = (effect - critical * standard_error, effect + critical * standard_error)
    statistic = effect / standard_error if standard_error else math.inf
    p_value = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(statistic) / math.sqrt(2.0))))
    decision = "supported" if interval[0] > 0 else (
        "refuted" if interval[1] < 0 else "inconclusive"
    )
    outcome_id = plan.outcomes[0].outcome_id
    return {
        "eligible": True,
        "effect": effect,
        "standard_error": standard_error,
        "confidence_interval": interval,
        "p_value": p_value,
        "denominator": len(rows),
        "missing_count": 0,
        "excluded_count": 0,
        "overlap_fraction": overlap,
        "decision": decision,
        "outcomes": {
            outcome_id: {
                "effect": effect,
                "standard_error": standard_error,
                "confidence_interval": interval,
                "p_value": p_value,
                "denominator": len(rows),
                "missing_count": 0,
                "excluded_count": 0,
                "decision": decision,
            }
        },
        "primary_decision": decision,
    }


__all__ = ["recalculate_causal"]
