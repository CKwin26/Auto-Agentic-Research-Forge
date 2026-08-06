"""Frozen software fixture for the formal 2x2 factorial Profile acceptance.

The fixture is intentionally small and deterministic.  It exercises the
scientific contract, execution, evaluation, independent recalculation, and
paper handoff without making a claim about people or an external population.
"""

from __future__ import annotations

from typing import Any


FACTORIAL_TITLE = (
    "How Do Structured Assistance and Explanatory Feedback Jointly Affect "
    "Task Quality? A Prespecified Randomized Factorial Study"
)


def factorial_acceptance_plan() -> dict[str, Any]:
    factors = [
        {
            "factor_id": "assistance",
            "label": "Structured assistance",
            "field": "assistance",
            "levels": ["absent", "present"],
        },
        {
            "factor_id": "feedback",
            "label": "Explanatory feedback",
            "field": "feedback",
            "levels": ["brief", "detailed"],
        },
    ]
    cells = [
        {
            "arm_id": "absent_brief",
            "levels": {"assistance": "absent", "feedback": "brief"},
        },
        {
            "arm_id": "absent_detailed",
            "levels": {"assistance": "absent", "feedback": "detailed"},
        },
        {
            "arm_id": "present_brief",
            "levels": {"assistance": "present", "feedback": "brief"},
        },
        {
            "arm_id": "present_detailed",
            "levels": {"assistance": "present", "feedback": "detailed"},
        },
    ]
    contrasts = [
        {
            "contrast_id": "assistance_main",
            "label": "Average effect of structured assistance",
            "outcome_id": "quality",
            "kind": "main_effect",
            "factor_ids": ["assistance"],
            "role": "secondary",
            "beneficial_direction": "higher",
        },
        {
            "contrast_id": "feedback_main",
            "label": "Average effect of explanatory feedback",
            "outcome_id": "quality",
            "kind": "main_effect",
            "factor_ids": ["feedback"],
            "role": "secondary",
            "beneficial_direction": "higher",
        },
        {
            "contrast_id": "assistance_feedback_interaction",
            "label": "Interaction between structured assistance and explanatory feedback",
            "outcome_id": "quality",
            "kind": "interaction",
            "factor_ids": ["assistance", "feedback"],
            "role": "primary",
            "beneficial_direction": "higher",
        },
    ]
    return {
        "schema_version": 1,
        "study_design_id": "factorial_experiment_v1",
        "study_design_version": "1",
        "unit_structure": {
            "row_unit": "one independently allocated software task",
            "observation_unit": "software task",
            "assignment_unit": "software task",
            "analysis_unit": "software task",
            "variance_unit": "software task",
            "independent_unit": "software task",
        },
        "allocation": {
            "mechanism": "randomized",
            "evidence": "Frozen seeded allocation ledger with twenty tasks per cell.",
        },
        "arms": [
            {
                "arm_id": cell["arm_id"],
                "label": cell["arm_id"].replace("_", " ").title(),
                "role": "factorial_cell",
                "definition": (
                    f"structured assistance={cell['levels']['assistance']}; "
                    f"explanatory feedback={cell['levels']['feedback']}"
                ),
            }
            for cell in cells
        ],
        "outcomes": [
            {
                "outcome_id": "quality",
                "label": "Task quality",
                "kind": "continuous",
                "field": "quality",
                "role": "primary",
                "beneficial_direction": "higher",
            }
        ],
        "estimands": [],
        "estimator_plan": {
            "quality": {
                "estimator_id": "factorial_ols_hc2_v1",
                "equal_variance_assumed": False,
            }
        },
        "inference_plan": {
            "quality": {
                "method": "effect-coded OLS with HC2 robust covariance",
                "confidence_level": 0.95,
            }
        },
        "missingness": {
            "policy": "complete_case",
            "denominator_rule": "All observed task-quality records across the four cells",
            "exclusion_reasons_required": True,
        },
        "multiplicity": {
            "method": "holm",
            "family_id": "factorial_confirmatory_family",
            "hypothesis_ids": [item["contrast_id"] for item in contrasts],
            "alpha_or_q": 0.05,
        },
        "decision_rules": {
            item["contrast_id"]: {
                "mode": "superiority",
                "effect_measure": item["kind"],
                "beneficial_direction": "higher",
                "confidence_level": 0.95,
            }
            for item in contrasts
        },
        "claim_boundary": {
            "generalization": (
                "Only the frozen randomized deterministic software-task fixture "
                "with the registered factor levels is represented."
            ),
            "reproduction_materials": [
                "factorial allocation ledger",
                "task and scoring specification",
                "factorial observations",
                "frozen analysis plan",
            ],
        },
        "factorial": {
            "factors": factors,
            "cells": cells,
            "contrasts": contrasts,
            "coding": "effect_coding",
            "covariance": "hc2_robust",
            "minimum_observed_per_cell": 20,
        },
    }


def factorial_acceptance_rows() -> list[dict[str, Any]]:
    """Return eighty independently allocated, reproducible task records."""

    rows: list[dict[str, Any]] = []
    for assistance, assistance_code in (("absent", -0.5), ("present", 0.5)):
        for feedback, feedback_code in (("brief", -0.5), ("detailed", 0.5)):
            arm_id = f"{assistance}_{feedback}"
            for index in range(20):
                # Balanced deterministic residuals keep the registered effects
                # exact while leaving non-zero within-cell variance.
                residual = ((index % 5) - 2) * 0.2
                quality = (
                    50.0
                    + 2.0 * assistance_code
                    + 3.0 * feedback_code
                    + 4.0 * assistance_code * feedback_code
                    + residual
                )
                rows.append(
                    {
                        "subject_id": f"{arm_id}-{index:02d}",
                        "arm": arm_id,
                        "assistance": assistance,
                        "feedback": feedback,
                        "quality": quality,
                    }
                )
    return rows


__all__ = [
    "FACTORIAL_TITLE",
    "factorial_acceptance_plan",
    "factorial_acceptance_rows",
]
