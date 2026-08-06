"""Deterministic blinded open-generation human-rating acceptance fixture."""

from __future__ import annotations

import hashlib
from typing import Any

from .schemas import AnalysisPlan


HUMAN_RATING_TITLE = (
    "Does Structured Self-Critique Improve the Usefulness of Open-Ended "
    "Responses? A Blinded Paired Human-Rating Study"
)


def human_rating_acceptance_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "study_design_id": "open_generation_human_rating_v1",
            "study_design_version": "1",
            "unit_structure": {
                "row_unit": "one blinded rater score for one frozen output",
                "observation_unit": "blinded output rating",
                "assignment_unit": "prompt item",
                "analysis_unit": "prompt item",
                "variance_unit": "prompt item",
                "independent_unit": "prompt item",
                "repeated_measure_unit": "prompt item",
            },
            "allocation": {
                "mechanism": "randomized",
                "evidence": "Every frozen prompt is evaluated under both response procedures; condition labels and display order are independently permuted for each rater with seed 20260805.",
            },
            "arms": [
                {
                    "arm_id": "control",
                    "label": "Direct response",
                    "role": "control",
                    "definition": "One frozen response generated directly from the registered prompt without a self-critique pass.",
                },
                {
                    "arm_id": "treatment",
                    "label": "Structured self-critique response",
                    "role": "treatment",
                    "definition": "One frozen response generated from the same prompt after the registered structured self-critique pass.",
                },
            ],
            "outcomes": [
                {
                    "outcome_id": "usefulness_rating",
                    "label": "Blinded usefulness rating",
                    "kind": "continuous",
                    "field": "rating",
                    "role": "primary",
                    "beneficial_direction": "higher",
                }
            ],
            "estimands": [
                {
                    "estimand_id": "paired_item_usefulness_difference",
                    "outcome_id": "usefulness_rating",
                    "treatment_arm_id": "treatment",
                    "control_arm_id": "control",
                    "effect_measure": "paired_mean_difference",
                    "analysis_population": "all_observed",
                }
            ],
            "estimator_plan": {
                "usefulness_rating": {
                    "estimator_id": "paired_item_mean_difference_v1",
                    "equal_variance_assumed": False,
                }
            },
            "inference_plan": {
                "usefulness_rating": {
                    "method": "paired t interval over prompt-level mean-rating differences",
                    "confidence_level": 0.95,
                }
            },
            "missingness": {
                "policy": "fail_if_any",
                "denominator_rule": "Every prompt-arm output must receive one score from every frozen panel member; incomplete panels are ineligible rather than silently averaged.",
                "exclusion_reasons_required": True,
            },
            "multiplicity": {
                "method": "no_correction",
                "family_id": "single confirmatory human-rated outcome",
                "hypothesis_ids": ["usefulness_rating"],
                "alpha_or_q": 0.05,
            },
            "decision_rules": {
                "usefulness_rating": {
                    "mode": "superiority",
                    "effect_measure": "paired_mean_difference",
                    "beneficial_direction": "higher",
                }
            },
            "claim_boundary": {
                "generalization": "Only the 40 frozen prompts, two frozen output procedures, registered rubric, and three-rater blinded panel are represented.",
                "reproduction_materials": [
                    "prompt manifest",
                    "frozen output hashes",
                    "blind-label ledger",
                    "rubric",
                    "rating ledger",
                    "independent paired recalculator",
                ],
            },
            "human_rating": {
                "item_id_field": "item_id",
                "output_id_field": "output_id",
                "output_hash_field": "output_sha256",
                "rater_id_field": "rater_id",
                "blind_label_field": "blind_label",
                "presentation_order_field": "presentation_order",
                "rubric_id": "usefulness-rubric-v1",
                "rating_scale_min": 1,
                "rating_scale_max": 5,
                "rater_panel_ids": ["rater-01", "rater-02", "rater-03"],
                "raters_per_output": 3,
                "condition_label_map": {"control": "condition-X", "treatment": "condition-Y"},
                "blindness": "condition_blinded",
                "presentation_randomization": "randomized_per_rater",
                "aggregation": "mean_rating_per_item_and_arm",
                "reliability_method": "icc_2_1_absolute_agreement",
                "minimum_reliability": 0.75,
                "adjudication_rule": "Do not replace frozen ratings. Record rubric deviations separately; invalidate the affected panel only when a prespecified integrity rule is met.",
            },
        }
    )


def human_rating_acceptance_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    panel = ("rater-01", "rater-02", "rater-03")
    for item_index in range(40):
        control_score = 2 + item_index % 3
        treatment_score = min(5, control_score + 1)
        for arm, score in (
            ("control", control_score),
            ("treatment", treatment_score),
        ):
            output_id = f"item-{item_index:03d}-{arm}"
            output_hash = hashlib.sha256(
                f"frozen-output-v1:{output_id}".encode("utf-8")
            ).hexdigest()
            blind_label = "condition-X" if arm == "control" else "condition-Y"
            for rater_index, rater_id in enumerate(panel):
                rows.append(
                    {
                        "item_id": f"item-{item_index:03d}",
                        "output_id": output_id,
                        "output_sha256": output_hash,
                        "rater_id": rater_id,
                        "blind_label": blind_label,
                        "presentation_order": (
                            1
                            if (item_index + rater_index + (arm == "treatment")) % 2 == 0
                            else 2
                        ),
                        "arm": arm,
                        "rating": score,
                    }
                )
    return rows


__all__ = [
    "HUMAN_RATING_TITLE",
    "human_rating_acceptance_plan",
    "human_rating_acceptance_rows",
]
