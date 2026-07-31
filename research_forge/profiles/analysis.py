"""Deterministic scientific analysis primitives used by Profile Bundles.

These functions do not inspect repository state and never choose an inference
method after seeing results.  Callers must pass the frozen Profile parameters.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class PairedObservation:
    pair_id: str
    baseline: float
    treatment: float
    cluster_id: str | None = None


@dataclass(frozen=True)
class AnalysisResult:
    baseline_estimate: float
    treatment_estimate: float
    effect: float
    confidence_interval: tuple[float, float]
    p_value: float | None
    pair_count: int
    independent_unit_count: int
    variance_unit: str
    details: dict[str, int | float | str]


def _validate_pairs(
    rows: Sequence[PairedObservation],
    *,
    require_binary: bool,
    require_cluster: bool,
) -> None:
    if not rows:
        raise ValueError("analysis requires at least one paired observation")
    ids = [row.pair_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate pair_id")
    if require_cluster and any(not row.cluster_id for row in rows):
        raise ValueError("cluster_id is required for every pair")
    for row in rows:
        if not all(math.isfinite(value) for value in (row.baseline, row.treatment)):
            raise ValueError("outcomes must be finite")
        if require_binary and (
            row.baseline not in {0, 1} or row.treatment not in {0, 1}
        ):
            raise ValueError("binary outcomes must be exactly 0 or 1")


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires values")
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _cluster_bootstrap_effects(
    rows: Sequence[PairedObservation],
    *,
    resamples: int,
    seed: int,
) -> list[float]:
    clusters: dict[str, list[PairedObservation]] = {}
    for row in rows:
        assert row.cluster_id is not None
        clusters.setdefault(row.cluster_id, []).append(row)
    cluster_ids = sorted(clusters)
    rng = random.Random(seed)
    effects: list[float] = []
    for _ in range(resamples):
        sampled = [rng.choice(cluster_ids) for _ in cluster_ids]
        expanded = [row for cluster in sampled for row in clusters[cluster]]
        effects.append(
            sum(row.treatment - row.baseline for row in expanded)
            / len(expanded)
        )
    return effects


def cluster_bootstrap_paired_continuous(
    rows: Sequence[PairedObservation],
    *,
    resamples: int,
    seed: int,
    confidence_level: float = 0.95,
) -> AnalysisResult:
    _validate_pairs(rows, require_binary=False, require_cluster=True)
    effects = _cluster_bootstrap_effects(rows, resamples=resamples, seed=seed)
    alpha = 1 - confidence_level
    baseline = statistics.fmean(row.baseline for row in rows)
    treatment = statistics.fmean(row.treatment for row in rows)
    effect = treatment - baseline
    centered_extreme = sum(
        abs(sample - effect) >= abs(effect) for sample in effects
    )
    p_value = (centered_extreme + 1) / (len(effects) + 1)
    return AnalysisResult(
        baseline_estimate=baseline,
        treatment_estimate=treatment,
        effect=effect,
        confidence_interval=(
            _percentile(effects, alpha / 2),
            _percentile(effects, 1 - alpha / 2),
        ),
        p_value=p_value,
        pair_count=len(rows),
        independent_unit_count=len({row.cluster_id for row in rows}),
        variance_unit="cluster",
        details={"resamples": resamples, "seed": seed},
    )


def exact_mcnemar_p_value(n01: int, n10: int) -> float:
    if n01 < 0 or n10 < 0:
        raise ValueError("discordant counts cannot be negative")
    discordant = n01 + n10
    if discordant == 0:
        return 1.0
    smaller = min(n01, n10)
    lower_tail = sum(
        math.comb(discordant, k) for k in range(smaller + 1)
    ) / (2**discordant)
    return min(1.0, 2 * lower_tail)


def _binary_counts(
    rows: Sequence[PairedObservation],
) -> tuple[int, int, int, int]:
    n00 = sum(row.baseline == 0 and row.treatment == 0 for row in rows)
    n01 = sum(row.baseline == 0 and row.treatment == 1 for row in rows)
    n10 = sum(row.baseline == 1 and row.treatment == 0 for row in rows)
    n11 = sum(row.baseline == 1 and row.treatment == 1 for row in rows)
    return n00, n01, n10, n11


def paired_binary_independent(
    rows: Sequence[PairedObservation],
    *,
    confidence_level: float = 0.95,
) -> AnalysisResult:
    _validate_pairs(rows, require_binary=True, require_cluster=False)
    n00, n01, n10, n11 = _binary_counts(rows)
    total = len(rows)
    effect = (n01 - n10) / total
    # A fixed Wald interval is reported as an effect interval; the exact
    # McNemar test remains the registered hypothesis test.
    variance = (
        (n01 + n10) / total - ((n01 - n10) / total) ** 2
    ) / total
    z = statistics.NormalDist().inv_cdf(0.5 + confidence_level / 2)
    margin = z * math.sqrt(max(0.0, variance))
    return AnalysisResult(
        baseline_estimate=(n10 + n11) / total,
        treatment_estimate=(n01 + n11) / total,
        effect=effect,
        confidence_interval=(max(-1.0, effect - margin), min(1.0, effect + margin)),
        p_value=exact_mcnemar_p_value(n01, n10),
        pair_count=total,
        independent_unit_count=total,
        variance_unit="pair",
        details={"n00": n00, "n01": n01, "n10": n10, "n11": n11},
    )


def paired_binary_clustered(
    rows: Sequence[PairedObservation],
    *,
    resamples: int,
    seed: int,
    confidence_level: float = 0.95,
) -> AnalysisResult:
    _validate_pairs(rows, require_binary=True, require_cluster=True)
    continuous = cluster_bootstrap_paired_continuous(
        rows,
        resamples=resamples,
        seed=seed,
        confidence_level=confidence_level,
    )
    n00, n01, n10, n11 = _binary_counts(rows)
    return AnalysisResult(
        **{
            **continuous.__dict__,
            "p_value": None,
            "details": {
                **continuous.details,
                "n00": n00,
                "n01": n01,
                "n10": n10,
                "n11": n11,
            },
        }
    )


def effect_cross_check(rows: Iterable[PairedObservation]) -> float:
    """Independent implementation used by assurance and offline checking."""
    materialized = tuple(rows)
    if not materialized:
        raise ValueError("cross-check requires observations")
    deltas = [row.treatment - row.baseline for row in materialized]
    return math.fsum(deltas) / len(deltas)


__all__ = [
    "AnalysisResult",
    "PairedObservation",
    "cluster_bootstrap_paired_continuous",
    "effect_cross_check",
    "exact_mcnemar_p_value",
    "paired_binary_clustered",
    "paired_binary_independent",
]
