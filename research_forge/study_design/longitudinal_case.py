"""Deterministic repeated-measures fixture for Profile acceptance."""

from __future__ import annotations

from typing import Any


LONGITUDINAL_TITLE = (
    "Does Structured Practice Improve the Rate of Skill Development? "
    "A Prespecified Randomized Longitudinal Study"
)


def longitudinal_acceptance_plan() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "study_design_id": "longitudinal_repeated_measures_v1",
        "study_design_version": "1",
        "unit_structure": {
            "row_unit": "one subject visit",
            "observation_unit": "subject visit",
            "assignment_unit": "subject",
            "analysis_unit": "subject",
            "variance_unit": "subject",
            "independent_unit": "subject",
            "repeated_measure_unit": "subject",
        },
        "allocation": {
            "mechanism": "randomized",
            "evidence": "Frozen seeded subject-level allocation ledger.",
            "concealment": "Allocation was frozen before trajectory outcomes were generated.",
        },
        "arms": [
            {
                "arm_id": "control",
                "label": "Standard practice",
                "role": "control",
                "definition": "The frozen standard-practice schedule.",
            },
            {
                "arm_id": "treatment",
                "label": "Structured practice",
                "role": "treatment",
                "definition": "The frozen structured-practice schedule.",
            },
        ],
        "outcomes": [
            {
                "outcome_id": "skill_score",
                "label": "Skill score",
                "kind": "continuous",
                "field": "skill_score",
                "role": "primary",
                "beneficial_direction": "higher",
            }
        ],
        "estimands": [
            {
                "estimand_id": "mean_slope_difference",
                "outcome_id": "skill_score",
                "treatment_arm_id": "treatment",
                "control_arm_id": "control",
                "effect_measure": "slope_difference",
                "analysis_population": "all_observed",
            }
        ],
        "estimator_plan": {
            "skill_score": {
                "estimator_id": "mean_subject_slope_difference_v1",
                "equal_variance_assumed": False,
            }
        },
        "inference_plan": {
            "skill_score": {
                "method": "Welch comparison of independent subject-specific linear slopes",
                "confidence_level": 0.95,
                "sidedness": "two_sided",
            }
        },
        "missingness": {
            "policy": "minimum_observed_trajectory",
            "denominator_rule": (
                "Include each randomized subject with at least three distinct "
                "observed visits on the frozen four-visit time grid."
            ),
            "exclusion_reasons_required": True,
        },
        "multiplicity": {
            "method": "no_correction",
            "family_id": "longitudinal_primary_family",
            "hypothesis_ids": ["skill_score"],
            "alpha_or_q": 0.05,
        },
        "decision_rules": {
            "skill_score": {
                "mode": "superiority",
                "effect_measure": "slope_difference",
                "beneficial_direction": "higher",
                "confidence_level": 0.95,
            }
        },
        "claim_boundary": {
            "generalization": (
                "Only the frozen randomized software fixture, linear time scale, "
                "and eligible subject trajectories are represented."
            ),
            "reproduction_materials": [
                "subject allocation ledger",
                "visit-level observations",
                "frozen longitudinal analysis plan",
                "independent slope recalculation",
            ],
        },
        "longitudinal": {
            "subject_id_field": "subject_id",
            "time_field": "week",
            "time_unit": "week",
            "planned_time_points": [0.0, 1.0, 2.0, 3.0],
            "minimum_observed_time_points": 3,
            "minimum_subjects_per_arm": 8,
            "trajectory_model": "subject_specific_linear_slope",
            "covariance": "welch_between_subject_slopes",
        },
    }


def longitudinal_acceptance_rows() -> list[dict[str, Any]]:
    """Return 96 visit rows for 24 independently randomized subjects."""

    rows: list[dict[str, Any]] = []
    for arm, base_slope in (("control", 0.9), ("treatment", 2.1)):
        for subject_index in range(12):
            # The symmetric subject effects make the mean arm slopes exact while
            # retaining non-zero between-subject variance for Welch inference.
            subject_slope = base_slope + (subject_index - 5.5) * 0.04
            intercept = 40.0 + (subject_index % 4) * 0.3
            for week in range(4):
                rows.append(
                    {
                        "subject_id": f"{arm}-{subject_index:02d}",
                        "arm": arm,
                        "week": float(week),
                        "skill_score": intercept + subject_slope * week,
                    }
                )
    return rows


__all__ = [
    "LONGITUDINAL_TITLE",
    "longitudinal_acceptance_plan",
    "longitudinal_acceptance_rows",
]
