"""Deterministic golden-vector assurance for implemented Profile statistics."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...workflow_domain import Stage3Profile
from ..analysis import (
    PairedObservation,
    cluster_bootstrap_paired_continuous,
    effect_cross_check,
    exact_mcnemar_p_value,
    paired_binary_clustered,
    paired_binary_independent,
)


@dataclass(frozen=True)
class ProfileAssuranceResult:
    profile: Stage3Profile
    passed: bool
    checks: dict[str, bool]


def _continuous_checks() -> dict[str, bool]:
    rows = [
        PairedObservation("a", 0.1, 0.2, "c1"),
        PairedObservation("b", 0.2, 0.4, "c1"),
        PairedObservation("c", 0.4, 0.5, "c2"),
        PairedObservation("d", 0.8, 0.9, "c3"),
    ]
    result = cluster_bootstrap_paired_continuous(
        rows, resamples=1_000, seed=20260726
    )
    replay = cluster_bootstrap_paired_continuous(
        rows, resamples=1_000, seed=20260726
    )
    return {
        "direction": result.effect > 0,
        "cluster_is_independent_unit": result.independent_unit_count == 3,
        "cross_implementation": math.isclose(
            result.effect, effect_cross_check(rows), abs_tol=1e-15
        ),
        "fixed_seed_replay": result.confidence_interval
        == replay.confidence_interval,
        "interval_ordered": (
            result.confidence_interval[0]
            <= result.effect
            <= result.confidence_interval[1]
        ),
    }


def _binary_independent_checks() -> dict[str, bool]:
    rows = [
        PairedObservation("a", 0, 1),
        PairedObservation("b", 0, 1),
        PairedObservation("c", 1, 0),
        PairedObservation("d", 1, 1),
    ]
    result = paired_binary_independent(rows)
    return {
        "table_counts": result.details
        == {"n00": 0, "n01": 2, "n10": 1, "n11": 1},
        "risk_difference": math.isclose(result.effect, 0.25),
        "cross_implementation": math.isclose(
            result.effect, effect_cross_check(rows)
        ),
        "exact_null_boundary": exact_mcnemar_p_value(0, 0) == 1.0,
        "exact_direction_symmetry": (
            exact_mcnemar_p_value(1, 5)
            == exact_mcnemar_p_value(5, 1)
        ),
    }


def _binary_clustered_checks() -> dict[str, bool]:
    rows = [
        PairedObservation("a", 0, 1, "drawing-1"),
        PairedObservation("b", 0, 1, "drawing-1"),
        PairedObservation("c", 1, 1, "drawing-2"),
        PairedObservation("d", 1, 0, "drawing-3"),
    ]
    result = paired_binary_clustered(
        rows, resamples=1_000, seed=20260726
    )
    return {
        "cluster_is_independent_unit": result.independent_unit_count == 3,
        "binary_table_retained": result.details["n01"] == 2,
        "cross_implementation": math.isclose(
            result.effect, effect_cross_check(rows)
        ),
        "direction": result.effect > 0,
    }


def _multi_arm_checks() -> dict[str, bool]:
    backbone = [
        PairedObservation("a", 0.2, 0.5, "c1"),
        PairedObservation("b", 0.3, 0.6, "c2"),
        PairedObservation("c", 0.4, 0.7, "c3"),
    ]
    neutral = [
        PairedObservation("a", 0.3, 0.5, "c1"),
        PairedObservation("b", 0.4, 0.6, "c2"),
        PairedObservation("c", 0.5, 0.7, "c3"),
    ]
    left = cluster_bootstrap_paired_continuous(
        backbone, resamples=1_000, seed=20260726
    )
    right = cluster_bootstrap_paired_continuous(
        neutral, resamples=1_000, seed=20260726
    )
    threshold = 0.1
    return {
        "all_registered_contrasts_positive": (
            left.effect > 0 and right.effect > 0
        ),
        "conjunction_requires_every_contrast": (
            left.effect >= threshold and right.effect >= threshold
        ),
        "weakest_contrast_is_conservative": (
            min(left.effect, right.effect) == right.effect
        ),
        "fixed_seed_replay": (
            left.confidence_interval
            == cluster_bootstrap_paired_continuous(
                backbone, resamples=1_000, seed=20260726
            ).confidence_interval
        ),
    }


def run_profile_assurance(
    profile: Stage3Profile,
) -> ProfileAssuranceResult:
    if profile is Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2:
        checks = _continuous_checks()
    elif profile is Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1:
        checks = _binary_independent_checks()
    elif profile is Stage3Profile.PAIRED_BINARY_CLUSTERED_V1:
        checks = _binary_clustered_checks()
    elif profile is Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1:
        checks = _multi_arm_checks()
    else:
        raise ValueError(f"no assurance suite for {profile.value}")
    return ProfileAssuranceResult(
        profile=profile,
        passed=all(checks.values()),
        checks=checks,
    )


__all__ = ["ProfileAssuranceResult", "run_profile_assurance"]
