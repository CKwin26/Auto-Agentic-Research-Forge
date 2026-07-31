from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from research_forge.profiles.analysis import (
    PairedObservation,
    cluster_bootstrap_paired_continuous,
    exact_mcnemar_p_value,
    paired_binary_clustered,
    paired_binary_independent,
)
from research_forge.profiles.assurance import run_profile_assurance
from research_forge.profiles.contracts import (
    PairedBinaryClusteredParameters,
    PairedBinaryIndependentParameters,
    PairedContinuousV2Parameters,
)
from research_forge.workflow_domain import Stage3Profile


def test_continuous_cluster_bootstrap_is_seeded_and_cluster_aware() -> None:
    rows = [
        PairedObservation("a", 1.0, 2.0, "task-a"),
        PairedObservation("b", 1.0, 3.0, "task-a"),
        PairedObservation("c", 4.0, 3.0, "task-b"),
    ]
    first = cluster_bootstrap_paired_continuous(
        rows, resamples=1_000, seed=17
    )
    second = cluster_bootstrap_paired_continuous(
        rows, resamples=1_000, seed=17
    )

    assert first.effect == pytest.approx(2 / 3)
    assert first.independent_unit_count == 2
    assert first.confidence_interval == second.confidence_interval


def test_independent_binary_builds_registered_2x2_table() -> None:
    rows = [
        PairedObservation("a", 0, 0),
        PairedObservation("b", 0, 1),
        PairedObservation("c", 0, 1),
        PairedObservation("d", 1, 0),
        PairedObservation("e", 1, 1),
    ]
    result = paired_binary_independent(rows)

    assert result.details == {"n00": 1, "n01": 2, "n10": 1, "n11": 1}
    assert result.effect == pytest.approx(0.2)
    assert result.p_value == exact_mcnemar_p_value(2, 1)


def test_clustered_binary_resamples_clusters_not_pairs() -> None:
    rows = [
        PairedObservation("a", 0, 1, "drawing-a"),
        PairedObservation("b", 0, 1, "drawing-a"),
        PairedObservation("c", 1, 0, "drawing-b"),
    ]
    result = paired_binary_clustered(
        rows, resamples=1_000, seed=23
    )

    assert result.p_value is None
    assert result.independent_unit_count == 2
    assert result.effect == pytest.approx(1 / 3)


@pytest.mark.parametrize(
    "rows,message",
    [
        (
            [
                PairedObservation("same", 0, 1),
                PairedObservation("same", 1, 0),
            ],
            "duplicate pair_id",
        ),
        ([PairedObservation("a", 0, 2)], "exactly 0 or 1"),
    ],
)
def test_binary_qualification_blocks_invalid_units(
    rows: list[PairedObservation], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        paired_binary_independent(rows)


def test_profile_contracts_freeze_resampling_unit() -> None:
    parameters = PairedContinuousV2Parameters.model_validate(
        {
            "pairing_key": "sample_id",
            "cluster_id_field": "task_id",
            "variance_unit": "cluster",
            "inference_spec": {
                "method": "cluster_bootstrap",
                "resample_unit": "task_id",
                "resamples": 2_000,
                "seed": 19,
            },
        }
    )
    assert parameters.inference_spec.seed == 19

    with pytest.raises(ValidationError, match="resample_unit"):
        PairedBinaryClusteredParameters.model_validate(
            {
                "pairing_key": "circle_id",
                "cluster_id_field": "drawing_id",
                "success_definition": "matched",
                "variance_unit": "cluster",
                "inference_spec": {
                    "method": "cluster_bootstrap",
                    "resample_unit": "circle_id",
                    "resamples": 1_000,
                    "seed": 7,
                },
            }
        )


def test_independent_profile_forbids_cluster_semantics() -> None:
    with pytest.raises(ValidationError):
        PairedBinaryIndependentParameters.model_validate(
            {
                "pairing_key": "sample_id",
                "success_definition": "correct",
                "variance_unit": "cluster",
            }
        )


@pytest.mark.parametrize(
    "profile",
    [
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
    ],
)
def test_profile_statistical_assurance_golden_vectors_pass(
    profile: Stage3Profile,
) -> None:
    report = run_profile_assurance(profile)
    assert report.passed, report.checks
    assert all(report.checks.values())


def test_exact_mcnemar_matches_known_value() -> None:
    # Two-sided exact binomial p for discordant counts 1 and 9 is 22/1024.
    assert math.isclose(exact_mcnemar_p_value(1, 9), 22 / 1024)
