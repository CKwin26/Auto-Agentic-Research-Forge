"""Deterministic observational fixture for causal Profile acceptance."""

from __future__ import annotations

import math
import random
from typing import Any


CAUSAL_TITLE = (
    "Does Structured Guidance Improve Task Quality? "
    "A Prespecified Observational Causal Analysis"
)


def causal_acceptance_plan() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "study_design_id": "causal_inference_v1",
        "study_design_version": "1",
        "unit_structure": {
            "row_unit": "one independently observed task session",
            "observation_unit": "task session",
            "assignment_unit": "not randomized; observed session exposure",
            "analysis_unit": "task session",
            "variance_unit": "task session",
            "independent_unit": "task session",
        },
        "allocation": {
            "mechanism": "observational",
            "evidence": "The guidance indicator was observed rather than randomized.",
            "concealment": None,
        },
        "arms": [
            {
                "arm_id": "unguided",
                "label": "Unguided sessions",
                "role": "control",
                "definition": "Observed sessions without structured guidance.",
            },
            {
                "arm_id": "guided",
                "label": "Guided sessions",
                "role": "treatment",
                "definition": "Observed sessions with structured guidance enabled.",
            },
        ],
        "outcomes": [
            {
                "outcome_id": "task_quality",
                "label": "Task quality score",
                "kind": "continuous",
                "field": "quality_score",
                "role": "primary",
                "beneficial_direction": "higher",
            }
        ],
        "estimands": [
            {
                "estimand_id": "ate_guidance_quality",
                "outcome_id": "task_quality",
                "treatment_arm_id": "guided",
                "control_arm_id": "unguided",
                "effect_measure": "average_treatment_effect",
                "analysis_population": "all_observed",
            }
        ],
        "estimator_plan": {
            "task_quality": {
                "estimator_id": "cross_fitted_aipw_ate_v1",
                "equal_variance_assumed": False,
            }
        },
        "inference_plan": {
            "task_quality": {
                "method": "cross-fitted AIPW influence-function interval",
                "confidence_level": 0.95,
                "sidedness": "two_sided",
                "seed": 20260805,
            }
        },
        "missingness": {
            "policy": "fail_if_any",
            "denominator_rule": (
                "Include every frozen eligible session with observed treatment, "
                "outcome, baseline difficulty, and prior experience."
            ),
            "exclusion_reasons_required": True,
        },
        "multiplicity": {
            "method": "no_correction",
            "family_id": "causal_primary_family",
            "hypothesis_ids": ["task_quality"],
            "alpha_or_q": 0.05,
        },
        "decision_rules": {
            "task_quality": {
                "mode": "superiority",
                "effect_measure": "average_treatment_effect",
                "beneficial_direction": "higher",
                "confidence_level": 0.95,
            }
        },
        "claim_boundary": {
            "generalization": (
                "Only the frozen synthetic observational session population and "
                "the owner-declared measured backdoor assumptions are represented."
            ),
            "reproduction_materials": [
                "frozen causal DAG",
                "observational session table",
                "cross-fitting assignment",
                "AIPW implementation",
                "independent recalculation",
            ],
        },
        "causal": {
            "subject_id_field": "session_id",
            "treatment_field": "guidance_enabled",
            "treatment_value": 1,
            "control_value": 0,
            "treatment_definition": (
                "The recorded binary state structured-guidance-enabled before the task response; "
                "the fixture contains no further contents, intensity, fidelity, or version information."
            ),
            "control_definition": (
                "The recorded binary state structured-guidance-not-enabled for the task session."
            ),
            "outcome_field": "quality_score",
            "causal_graph_edges": [
                {"source": "baseline_difficulty", "target": "guidance_enabled"},
                {"source": "baseline_difficulty", "target": "quality_score"},
                {"source": "prior_experience", "target": "guidance_enabled"},
                {"source": "prior_experience", "target": "quality_score"},
                {"source": "guidance_enabled", "target": "quality_score"},
            ],
            "adjustment_set": ["baseline_difficulty", "prior_experience"],
            "forbidden_adjustment_fields": ["post_treatment_effort", "quality_score"],
            "identification_strategy": "backdoor_adjustment",
            "estimand": "ate",
            "estimator": "cross_fitted_aipw",
            "cross_fitting_folds": 5,
            "fold_seed": 20260805,
            "propensity_lower_bound": 0.05,
            "propensity_upper_bound": 0.95,
            "minimum_overlap_fraction": 0.95,
            "minimum_subjects_per_arm": 40,
            "exchangeability_assumption": (
                "Conditional on frozen baseline difficulty and prior experience, "
                "guidance exposure is exchangeable with both potential quality outcomes."
            ),
            "consistency_assumption": (
                "Each observed outcome corresponds to its recorded binary guidance state. "
                "Because treatment versions are not represented, substantive treatment-version "
                "consistency cannot be assessed and interpretation is restricted to that recorded state."
            ),
            "no_interference_assumption": (
                "One session's guidance condition does not change another session's outcome."
            ),
        },
    }


def causal_acceptance_rows() -> list[dict[str, Any]]:
    """Return 240 observational rows with measured confounding and overlap."""

    generator = random.Random(20260805)
    rows: list[dict[str, Any]] = []
    for index in range(240):
        difficulty = ((index % 24) - 11.5) / 7.0
        experience = float((index // 24) % 2)
        propensity = 1.0 / (1.0 + math.exp(-(-0.1 + 0.35 * difficulty + 0.3 * experience)))
        treatment = int(generator.random() < propensity)
        noise = generator.gauss(0.0, 0.9)
        quality = 50.0 + 2.4 * treatment - 1.6 * difficulty + 1.2 * experience + noise
        rows.append(
            {
                "session_id": f"session-{index:03d}",
                "arm": "guided" if treatment else "unguided",
                "guidance_enabled": treatment,
                "baseline_difficulty": difficulty,
                "prior_experience": experience,
                "quality_score": quality,
            }
        )
    return rows


__all__ = ["CAUSAL_TITLE", "causal_acceptance_plan", "causal_acceptance_rows"]
