from __future__ import annotations

import pytest

from research_forge.stage_three_evaluator import evaluate_sample_predictions


def test_platform_evaluator_recomputes_accuracy_from_sample_rows() -> None:
    result = evaluate_sample_predictions(
        [
            {
                "sample_id": "a",
                "prediction": 1,
                "target_reference": "target-a",
            },
            {
                "sample_id": "b",
                "prediction": 0,
                "target_reference": "target-b",
                "abstention": True,
            },
        ],
        plugin="exact_match_rate_v1",
        targets={"target-a": 1, "target-b": 1},
    )

    assert result == {
        "accuracy": 0.5,
        "denominator": 2,
        "sample_ids": ["a", "b"],
        "abstentions": 1,
    }


def test_platform_evaluator_rejects_duplicate_sample_ids() -> None:
    with pytest.raises(ValueError, match="duplicate sample IDs"):
        evaluate_sample_predictions(
            [
                {
                    "sample_id": "a",
                    "prediction": 1,
                    "target_reference": "target-a",
                },
                {
                    "sample_id": "a",
                    "prediction": 1,
                    "target_reference": "target-b",
                },
            ],
            plugin="exact_match_rate_v1",
            targets={"target-a": 1, "target-b": 1},
        )
