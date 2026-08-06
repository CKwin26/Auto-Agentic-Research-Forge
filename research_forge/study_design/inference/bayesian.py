"""Narrow, recomputable Bayesian sensitivity analysis."""

from __future__ import annotations

import random
from typing import Any

from ..schemas import StudyDesignCompletionPatch, StudyDesignMaturity
from ..sdk import InferenceModuleDescriptor


def beta_binomial_difference(
    *, control_events: int, control_n: int, treatment_events: int,
    treatment_n: int, prior_alpha: float, prior_beta: float,
    seed: int, draws: int = 20_000, rope: tuple[float, float] = (-0.01, 0.01),
) -> dict[str, Any]:
    if not 0 <= control_events <= control_n or not 0 <= treatment_events <= treatment_n:
        raise ValueError("event counts must be within arm denominators")
    if prior_alpha <= 0 or prior_beta <= 0:
        raise ValueError("Beta prior parameters must be positive")
    if draws < 1_000:
        raise ValueError("at least 1000 posterior draws are required")
    rng = random.Random(seed)
    differences = sorted(
        rng.betavariate(prior_alpha+treatment_events, prior_beta+treatment_n-treatment_events)
        - rng.betavariate(prior_alpha+control_events, prior_beta+control_n-control_events)
        for _ in range(draws)
    )
    low = differences[int(0.025*draws)]
    high = differences[min(draws-1, int(0.975*draws))]
    return {
        "posterior_mean_difference": sum(differences)/draws,
        "credible_interval_95": [low, high],
        "probability_effect_above_zero": sum(value > 0 for value in differences)/draws,
        "probability_in_rope": sum(rope[0] <= value <= rope[1] for value in differences)/draws,
        "seed": seed, "draws": draws,
        "prior": {"family": "Beta", "alpha": prior_alpha, "beta": prior_beta},
        "rope": list(rope),
    }


class BayesianInference:
    descriptor = InferenceModuleDescriptor(
        module_id="bayesian_inference_v1", version="1",
        title="Bayesian sensitivity analysis",
        summary="Recomputable Beta-Binomial sensitivity analysis for binary two-arm outcomes.",
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        # The static descriptor remains C2 until a sealed acceptance report is
        # present, but the canonical Workflow v2 execution path is connected.
        formal_execution_supported=True,
        sensitivity_only_by_default=True,
        known_limits=("binary Beta-Binomial only", "cannot override the frozen primary verdict"),
    )

    def qualify(self, contract: Any) -> dict[str, Any]:
        errors = self.validate_contract(contract)
        return {"qualified": not errors, "violations": errors}

    def complete_contract(self, contract: Any) -> StudyDesignCompletionPatch:
        binding = getattr(contract, "study_design", {}) or {}
        return StudyDesignCompletionPatch(
            study_design_id=binding.get("id", "unknown") if isinstance(binding, dict) else binding.id,
            study_design_version=binding.get("version", "1") if isinstance(binding, dict) else binding.version,
        )

    def validate_contract(self, contract: Any) -> list[str]:
        modules = getattr(contract, "inference_modules", []) or []
        binding = next((item for item in modules if (item.get("id") if isinstance(item, dict) else item.id) == self.descriptor.module_id), None)
        extension = (binding.get("extension", {}) if isinstance(binding, dict) else binding.extension) if binding else {}
        errors = []
        prior = extension.get("prior") or {}
        if not prior or not extension.get("prior_provenance"):
            errors.append("Bayesian sensitivity requires an approved prior and provenance")
        elif (
            prior.get("family") != "Beta"
            or float(prior.get("alpha", 0.0)) <= 0
            or float(prior.get("beta", 0.0)) <= 0
        ):
            errors.append("Beta-Binomial sensitivity requires positive frozen Beta prior parameters")
        if extension.get("informative") and not extension.get("owner_approved"):
            errors.append("informative Bayesian prior requires owner approval")
        if extension.get("seed") is None:
            errors.append("Bayesian sensitivity requires a frozen seed")
        draws = extension.get("draws")
        if draws is None or int(draws) < 1_000:
            errors.append("Bayesian sensitivity requires at least 1000 frozen posterior draws")
        rope = extension.get("rope")
        if (
            not isinstance(rope, (list, tuple))
            or len(rope) != 2
            or float(rope[0]) >= float(rope[1])
        ):
            errors.append("Bayesian sensitivity requires an ordered two-value ROPE")
        threshold = extension.get("decision_threshold")
        if threshold is None or not 0.0 < float(threshold) < 1.0:
            errors.append("Bayesian sensitivity requires a frozen probability threshold between zero and one")
        if extension.get("role") != "prespecified_sensitivity_only":
            errors.append("Bayesian analysis must be frozen as prespecified sensitivity only")
        return errors

    def compute(self, payload: Any) -> dict[str, Any]:
        return beta_binomial_difference(**payload)

    def adjudicate(self, payload: Any) -> Any:
        return payload

    def produce_claim_constraints(self, payload: Any) -> list[str]:
        return ["Present Bayesian output as sensitivity analysis; it cannot replace the frozen primary verdict."]


__all__ = ["BayesianInference", "beta_binomial_difference"]
