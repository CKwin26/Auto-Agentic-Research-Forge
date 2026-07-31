"""Runtime adapters from frozen evaluator rows to Profile analysis."""

from __future__ import annotations

from typing import Any, Sequence

from ..workflow_domain import Stage3Profile
from .analysis import (
    AnalysisResult,
    PairedObservation,
    cluster_bootstrap_paired_continuous,
    paired_binary_clustered,
    paired_binary_independent,
)
from .contracts import (
    PairedBinaryClusteredParameters,
    PairedBinaryIndependentParameters,
    PairedContinuousV2Parameters,
)


def _paired_rows(
    arm_pairs: Sequence[
        tuple[list[dict[str, Any]], list[dict[str, Any]]]
    ],
    *,
    pairing_key: str,
    outcome_field: str,
    cluster_id_field: str | None,
) -> list[PairedObservation]:
    observations: list[PairedObservation] = []
    seen: set[str] = set()
    for block_index, (baseline_rows, treatment_rows) in enumerate(arm_pairs):
        baseline = {str(row[pairing_key]): row for row in baseline_rows}
        treatment = {str(row[pairing_key]): row for row in treatment_rows}
        if len(baseline) != len(baseline_rows):
            raise ValueError("duplicate pairing key in baseline rows")
        if len(treatment) != len(treatment_rows):
            raise ValueError("duplicate pairing key in treatment rows")
        if set(baseline) != set(treatment):
            raise ValueError("baseline and treatment analysis rows do not pair")
        for pair_id in sorted(baseline):
            scoped_pair_id = f"{block_index}:{pair_id}"
            if scoped_pair_id in seen:
                raise ValueError("pairing key repeats across run cells")
            seen.add(scoped_pair_id)
            baseline_row = baseline[pair_id]
            treatment_row = treatment[pair_id]
            cluster_id: str | None = None
            if cluster_id_field:
                left = str(baseline_row.get(cluster_id_field) or "")
                right = str(treatment_row.get(cluster_id_field) or "")
                if not left or left != right:
                    raise ValueError(
                        "cluster identity is missing or differs between arms"
                    )
                cluster_id = left
            observations.append(
                PairedObservation(
                    pair_id=scoped_pair_id,
                    baseline=float(baseline_row[outcome_field]),
                    treatment=float(treatment_row[outcome_field]),
                    cluster_id=cluster_id,
                )
            )
    return observations


def analyze_profile_rows(
    profile: Stage3Profile,
    arm_pairs: Sequence[
        tuple[list[dict[str, Any]], list[dict[str, Any]]]
    ],
    parameters: dict[str, Any],
) -> AnalysisResult:
    if profile is Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2:
        spec = PairedContinuousV2Parameters.model_validate(parameters)
        rows = _paired_rows(
            arm_pairs,
            pairing_key=spec.pairing_key,
            outcome_field="value",
            cluster_id_field=spec.cluster_id_field,
        )
        return cluster_bootstrap_paired_continuous(
            rows,
            resamples=spec.inference_spec.resamples,
            seed=spec.inference_spec.seed,
            confidence_level=spec.inference_spec.confidence_level,
        )
    if profile is Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1:
        spec = PairedBinaryIndependentParameters.model_validate(parameters)
        rows = _paired_rows(
            arm_pairs,
            pairing_key=spec.pairing_key,
            outcome_field="value",
            cluster_id_field=None,
        )
        return paired_binary_independent(
            rows, confidence_level=spec.confidence_level
        )
    if profile is Stage3Profile.PAIRED_BINARY_CLUSTERED_V1:
        spec = PairedBinaryClusteredParameters.model_validate(parameters)
        rows = _paired_rows(
            arm_pairs,
            pairing_key=spec.pairing_key,
            outcome_field="value",
            cluster_id_field=spec.cluster_id_field,
        )
        return paired_binary_clustered(
            rows,
            resamples=spec.inference_spec.resamples,
            seed=spec.inference_spec.seed,
            confidence_level=spec.inference_spec.confidence_level,
        )
    raise ValueError(f"Profile {profile.value} has no modern row analyzer")


__all__ = ["analyze_profile_rows"]
