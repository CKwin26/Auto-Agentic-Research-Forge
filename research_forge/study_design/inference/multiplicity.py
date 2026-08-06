"""Named-family multiplicity adjustment without result deletion."""

from __future__ import annotations

from typing import Any

from ..schemas import StudyDesignCompletionPatch, StudyDesignMaturity
from ..sdk import InferenceModuleDescriptor


def adjust_p_values(values: dict[str, float], method: str) -> dict[str, float]:
    if not values:
        return {}
    if any(not 0 <= value <= 1 for value in values.values()):
        raise ValueError("p-values must be between zero and one")
    names = list(values)
    count = len(names)
    if method == "no_correction":
        return dict(values)
    if method == "bonferroni":
        return {name: min(1.0, value * count) for name, value in values.items()}
    ordered = sorted(values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    if method == "holm":
        running = 0.0
        for rank, (name, value) in enumerate(ordered):
            running = max(running, (count - rank) * value)
            adjusted[name] = min(1.0, running)
    elif method == "benjamini_hochberg":
        running = 1.0
        for rank in range(count, 0, -1):
            name, value = ordered[rank - 1]
            running = min(running, value * count / rank)
            adjusted[name] = min(1.0, running)
    else:
        raise ValueError(f"unsupported multiplicity method: {method}")
    return {name: adjusted[name] for name in names}


class MultiplicityControl:
    descriptor = InferenceModuleDescriptor(
        module_id="multiplicity_control_v1", version="1",
        title="Multiplicity control",
        summary="Preserves raw results and adds Bonferroni, Holm, or Benjamini-Hochberg adjustments.",
        # Formal maturity is evidence-derived from an acceptance report.  The
        # reusable module itself has schema, validation, and dry-run coverage.
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
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
        spec = getattr(contract, "study_design_spec", {}) or {}
        plan = spec.get("multiplicity", {})
        hypotheses = plan.get("hypothesis_ids", [])
        if len(hypotheses) > 1 and plan.get("method") == "no_correction":
            return ["multiple confirmatory hypotheses require a multiplicity method"]
        return []

    def compute(self, payload: Any) -> dict[str, Any]:
        values = dict(payload["p_values"])
        adjusted = adjust_p_values(values, str(payload["method"]))
        return {"raw_p_values": values, "adjusted_p_values": adjusted}

    def adjudicate(self, payload: Any) -> Any:
        return payload

    def produce_claim_constraints(self, payload: Any) -> list[str]:
        return ["Report every registered hypothesis, including non-significant results."]


__all__ = ["MultiplicityControl", "adjust_p_values"]
