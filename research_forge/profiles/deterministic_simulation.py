"""Deterministic paired simulation used by the capability acceptance suite."""

from __future__ import annotations

import hashlib
import math
from typing import Literal

from pydantic import Field, model_validator

from ..models import StrictModel
from .contracts import DeterministicSimulationParameters


class SimulationRow(StrictModel):
    pair_id: str
    value: float


class DeterministicSimulationResult(StrictModel):
    schema_version: int = 1
    profile_id: Literal["deterministic_simulation_v1"] = (
        "deterministic_simulation_v1"
    )
    arm: Literal["baseline", "treatment"]
    seed: int
    rows: list[SimulationRow] = Field(min_length=1)
    denominator: int = Field(ge=1)
    mean_value: float

    @model_validator(mode="after")
    def result_is_self_consistent(self) -> "DeterministicSimulationResult":
        if len(self.rows) != self.denominator:
            raise ValueError("row count must equal denominator")
        if len({item.pair_id for item in self.rows}) != len(self.rows):
            raise ValueError("pair IDs must be unique")
        recomputed = sum(item.value for item in self.rows) / len(self.rows)
        if not math.isclose(
            recomputed, self.mean_value, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("mean_value does not match rows")
        return self


class SimulationEvaluation(StrictModel):
    metric: Literal["mean_paired_difference"] = "mean_paired_difference"
    effect: float
    threshold: float
    direction: Literal["higher_is_better", "lower_is_better"]
    verdict: Literal["supported", "refuted", "inconclusive"]
    denominator: int = Field(ge=1)


def _common_noise(seed: int, index: int, scale: float) -> float:
    digest = hashlib.sha256(f"{seed}:{index}".encode("utf-8")).digest()
    unit = int.from_bytes(digest[:8], "big") / (2**64 - 1)
    return (2.0 * unit - 1.0) * scale


def run_deterministic_simulation(
    parameters: DeterministicSimulationParameters,
    *,
    arm: Literal["baseline", "treatment"],
    seed: int,
) -> DeterministicSimulationResult:
    shift = parameters.treatment_effect if arm == "treatment" else 0.0
    rows = [
        SimulationRow(
            pair_id=f"pair-{index:06d}",
            value=(
                parameters.baseline_location
                + _common_noise(seed, index, parameters.noise_scale)
                + shift
            ),
        )
        for index in range(parameters.sample_count)
    ]
    return DeterministicSimulationResult(
        arm=arm,
        seed=seed,
        rows=rows,
        denominator=len(rows),
        mean_value=sum(item.value for item in rows) / len(rows),
    )


def evaluate_deterministic_simulation(
    baseline: DeterministicSimulationResult,
    treatment: DeterministicSimulationResult,
    parameters: DeterministicSimulationParameters,
) -> SimulationEvaluation:
    if baseline.seed != treatment.seed:
        raise ValueError("paired simulation arms must use the same seed")
    baseline_by_id = {item.pair_id: item.value for item in baseline.rows}
    treatment_by_id = {item.pair_id: item.value for item in treatment.rows}
    if baseline_by_id.keys() != treatment_by_id.keys():
        raise ValueError("paired simulation arms must contain identical pair IDs")
    differences = [
        treatment_by_id[pair_id] - baseline_by_id[pair_id]
        for pair_id in sorted(baseline_by_id)
    ]
    effect = sum(differences) / len(differences)
    tolerance = 1e-12
    if parameters.direction == "higher_is_better":
        verdict = (
            "supported"
            if effect > parameters.effect_threshold + tolerance
            else "refuted"
            if effect < parameters.effect_threshold - tolerance
            else "inconclusive"
        )
    else:
        verdict = (
            "supported"
            if effect < parameters.effect_threshold - tolerance
            else "refuted"
            if effect > parameters.effect_threshold + tolerance
            else "inconclusive"
        )
    return SimulationEvaluation(
        effect=effect,
        threshold=parameters.effect_threshold,
        direction=parameters.direction,
        verdict=verdict,
        denominator=len(differences),
    )


__all__ = [
    "DeterministicSimulationResult",
    "SimulationEvaluation",
    "SimulationRow",
    "evaluate_deterministic_simulation",
    "run_deterministic_simulation",
]
