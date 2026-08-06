"""Deterministic acceptance plan and exposed-user rows for online A/B."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .schemas import AnalysisPlan


def online_ab_acceptance_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "study_design_id": "online_ab_test_v1",
            "study_design_version": "1",
            "unit_structure": {
                "row_unit": "one first valid experiment exposure",
                "observation_unit": "exposed account",
                "assignment_unit": "account",
                "analysis_unit": "account",
                "variance_unit": "account",
                "independent_unit": "account",
            },
            "allocation": {
                "mechanism": "randomized",
                "evidence": "A frozen seeded shuffle assigns exactly 200 of 400 account identifiers to each arm before exposure.",
            },
            "arms": [
                {"arm_id": "control", "label": "Current experience", "role": "control", "definition": "The registered current experience shown at first valid exposure."},
                {"arm_id": "treatment", "label": "Assisted experience", "role": "treatment", "definition": "The registered assisted experience shown at first valid exposure."},
            ],
            "outcomes": [
                {"outcome_id": "conversion", "label": "Task conversion", "kind": "binary", "field": "converted", "role": "primary", "beneficial_direction": "higher", "event_value": True},
                {"outcome_id": "error_guardrail", "label": "Error occurrence", "kind": "binary", "field": "error_occurred", "role": "secondary", "beneficial_direction": "lower", "event_value": True},
            ],
            "estimands": [
                {"estimand_id": "conversion_risk_difference", "outcome_id": "conversion", "treatment_arm_id": "treatment", "control_arm_id": "control", "effect_measure": "risk_difference", "analysis_population": "all_observed"},
                {"estimand_id": "error_risk_difference", "outcome_id": "error_guardrail", "treatment_arm_id": "treatment", "control_arm_id": "control", "effect_measure": "risk_difference", "analysis_population": "all_observed"},
            ],
            "estimator_plan": {
                "conversion": {"estimator_id": "wald_risk_difference_v1", "equal_variance_assumed": False},
                "error_guardrail": {"estimator_id": "wald_risk_difference_v1", "equal_variance_assumed": False},
            },
            "inference_plan": {
                "conversion": {"method": "Wald risk-difference interval", "confidence_level": 0.95},
                "error_guardrail": {"method": "Wald risk-difference interval", "confidence_level": 0.95},
            },
            "missingness": {"policy": "fail_if_any", "denominator_rule": "Analyze every first valid exposure after the full fixed horizon; missing exposure or outcome fields invalidate formal admission.", "exclusion_reasons_required": True},
            "multiplicity": {"method": "holm", "family_id": "online confirmatory primary and guardrail", "hypothesis_ids": ["conversion", "error_guardrail"], "alpha_or_q": 0.05},
            "decision_rules": {
                "conversion": {"mode": "superiority", "effect_measure": "risk_difference", "beneficial_direction": "higher"},
                "error_guardrail": {"mode": "superiority", "effect_measure": "risk_difference", "beneficial_direction": "lower"},
            },
            "claim_boundary": {
                "generalization": "Only first valid exposed accounts in the frozen synthetic online experiment window are represented.",
                "reproduction_materials": ["exposure log", "assignment ledger", "metric definitions", "fixed-horizon rule", "SRM calculation", "independent recalculator"],
            },
            "online_ab": {
                "experiment_id": "online-ab-fixed-horizon-acceptance-v1",
                "randomization_unit_field": "subject_id",
                "exposure_id_field": "exposure_id",
                "exposure_timestamp_field": "exposure_timestamp",
                "exposure_definition": "The account rendered its assigned experience and emitted one authenticated exposure event.",
                "analysis_population": "first_valid_exposure_only",
                "one_exposure_per_randomization_unit": True,
                "window_start": "2026-08-01T00:00:00+00:00",
                "window_end": "2026-08-02T00:00:00+00:00",
                "planned_total_exposures": 400,
                "minimum_exposures_per_arm": 180,
                "allocation_ratio": {"control": 0.5, "treatment": 0.5},
                "srm_alpha": 0.01,
                "analysis_mode": "fixed_horizon_no_peeking",
                "stopping_rule": "Make no scientific decision before all 400 first valid exposures are frozen; evaluate only once at the fixed horizon.",
                "guardrail_outcome_ids": ["error_guardrail"],
            },
        }
    )


def online_ab_acceptance_rows() -> list[dict[str, Any]]:
    identifiers = list(range(400))
    random.Random(20260804).shuffle(identifiers)
    control = set(identifiers[:200])
    arm_rank = {"control": 0, "treatment": 0}
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rows: list[dict[str, Any]] = []
    for index in range(400):
        arm = "control" if index in control else "treatment"
        rank = arm_rank[arm]
        arm_rank[arm] += 1
        # Frozen exact event counts: conversion 60/200 vs 90/200;
        # error guardrail 10/200 vs 12/200.
        conversion_events = 60 if arm == "control" else 90
        error_events = 10 if arm == "control" else 12
        rows.append(
            {
                "subject_id": f"account-{index:04d}",
                "exposure_id": f"exposure-{index:04d}",
                "exposure_timestamp": (
                    start + timedelta(seconds=index * 120)
                ).isoformat(),
                "arm": arm,
                "converted": rank < conversion_events,
                "error_occurred": rank < error_events,
            }
        )
    return rows


__all__ = ["online_ab_acceptance_plan", "online_ab_acceptance_rows"]
