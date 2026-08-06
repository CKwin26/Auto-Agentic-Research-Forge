"""Deterministic right-censored fixture for survival Profile acceptance."""

from __future__ import annotations

from typing import Any


SURVIVAL_TITLE = (
    "Does a Resilience Intervention Extend Event-Free Operation? "
    "A Prespecified Randomized Time-to-Event Study"
)


def survival_acceptance_plan() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "study_design_id": "survival_analysis_v1",
        "study_design_version": "1",
        "unit_structure": {
            "row_unit": "one randomized subject follow-up record",
            "observation_unit": "subject",
            "assignment_unit": "subject",
            "analysis_unit": "subject",
            "variance_unit": "subject",
            "independent_unit": "subject",
        },
        "allocation": {
            "mechanism": "randomized",
            "evidence": "Frozen seeded subject-level allocation ledger.",
            "concealment": "Allocation was frozen before event times were generated.",
        },
        "arms": [
            {
                "arm_id": "control",
                "label": "Standard configuration",
                "role": "control",
                "definition": "The frozen standard resilience configuration.",
            },
            {
                "arm_id": "treatment",
                "label": "Resilience intervention",
                "role": "treatment",
                "definition": "The frozen intervention intended to delay first failure.",
            },
        ],
        "outcomes": [
            {
                "outcome_id": "time_to_first_failure",
                "label": "Time to first failure",
                "kind": "time_to_event",
                "field": "duration_hours",
                "role": "primary",
                "beneficial_direction": "higher",
            }
        ],
        "estimands": [
            {
                "estimand_id": "rmst_difference_12h",
                "outcome_id": "time_to_first_failure",
                "treatment_arm_id": "treatment",
                "control_arm_id": "control",
                "effect_measure": "rmst_difference",
                "analysis_population": "all_observed",
            }
        ],
        "estimator_plan": {
            "time_to_first_failure": {
                "estimator_id": "kaplan_meier_rmst_difference_v1",
                "equal_variance_assumed": False,
            }
        },
        "inference_plan": {
            "time_to_first_failure": {
                "method": "Kaplan-Meier RMST difference with arm-stratified subject bootstrap",
                "confidence_level": 0.95,
                "sidedness": "two_sided",
                "seed": 20260804,
            }
        },
        "missingness": {
            "policy": "right_censoring",
            "denominator_rule": (
                "Include every randomized subject with a positive observed follow-up "
                "duration and an explicit event or administrative-censor status."
            ),
            "exclusion_reasons_required": True,
        },
        "multiplicity": {
            "method": "no_correction",
            "family_id": "survival_primary_family",
            "hypothesis_ids": ["time_to_first_failure"],
            "alpha_or_q": 0.05,
        },
        "decision_rules": {
            "time_to_first_failure": {
                "mode": "superiority",
                "effect_measure": "rmst_difference",
                "beneficial_direction": "higher",
                "confidence_level": 0.95,
            }
        },
        "claim_boundary": {
            "generalization": (
                "Only the frozen randomized software fixture, first-failure event, "
                "administrative right censoring, and 12-hour horizon are represented."
            ),
            "reproduction_materials": [
                "subject allocation ledger",
                "time-to-event records",
                "frozen survival analysis plan",
                "independent Kaplan-Meier RMST recalculation",
            ],
        },
        "survival": {
            "subject_id_field": "subject_id",
            "duration_field": "duration_hours",
            "event_field": "failure_observed",
            "event_value": 1,
            "censor_value": 0,
            "time_origin": "start of the frozen workload",
            "time_unit": "hour",
            "restriction_time": 12.0,
            "minimum_subjects_per_arm": 10,
            "minimum_events_per_arm": 4,
            "censoring_rule": "Subjects without first failure are administratively censored at their last verified follow-up.",
            "estimator": "kaplan_meier_rmst",
            "bootstrap_repetitions": 1000,
            "bootstrap_seed": 20260804,
        },
    }


def survival_acceptance_rows() -> list[dict[str, Any]]:
    """Return 48 independent records with explicit right censoring."""

    control_times = [
        (2.0, 1), (2.5, 1), (3.0, 1), (3.5, 1), (4.0, 1), (4.5, 1),
        (5.0, 1), (5.5, 1), (6.0, 1), (6.5, 1), (7.0, 1), (7.5, 1),
        (8.0, 1), (8.5, 1), (9.0, 1), (9.5, 1), (10.0, 1), (10.5, 1),
        (11.0, 0), (11.5, 0), (12.0, 0), (12.0, 0), (12.0, 0), (12.0, 0),
    ]
    treatment_times = [
        (5.0, 1), (5.5, 1), (6.0, 1), (6.5, 1), (7.0, 1), (7.5, 1),
        (8.0, 1), (8.5, 1), (9.0, 1), (9.5, 1), (10.0, 1), (10.5, 1),
        (11.0, 1), (11.5, 1), (12.0, 0), (12.0, 0), (12.0, 0), (12.0, 0),
        (12.0, 0), (12.0, 0), (12.0, 0), (12.0, 0), (12.0, 0), (12.0, 0),
    ]
    rows: list[dict[str, Any]] = []
    for arm, records in (("control", control_times), ("treatment", treatment_times)):
        for index, (duration, event) in enumerate(records):
            rows.append({
                "subject_id": f"{arm}-{index:02d}",
                "arm": arm,
                "duration_hours": duration,
                "failure_observed": event,
            })
    return rows


__all__ = ["SURVIVAL_TITLE", "survival_acceptance_plan", "survival_acceptance_rows"]
