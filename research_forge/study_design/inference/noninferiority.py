"""Deterministic superiority, noninferiority, and equivalence decisions."""

from __future__ import annotations

from typing import Any

from ..schemas import StudyDesignCompletionPatch, StudyDesignMaturity
from ..sdk import InferenceModuleDescriptor


def interval_decision(
    interval: tuple[float, float], *, mode: str, direction: str,
    margin: float | None = None, lower_margin: float | None = None,
    upper_margin: float | None = None,
) -> str:
    low, high = interval
    if mode == "superiority":
        return "supported" if ((direction == "higher" and low > 0) or (direction == "lower" and high < 0)) else "inconclusive"
    if mode == "noninferiority":
        if margin is None or margin <= 0:
            raise ValueError("noninferiority requires a positive frozen margin")
        return "supported" if ((direction == "higher" and low > -margin) or (direction == "lower" and high < margin)) else "inconclusive"
    if mode == "equivalence":
        if lower_margin is None or upper_margin is None or lower_margin >= upper_margin:
            raise ValueError("equivalence requires ordered frozen margins")
        return "supported" if low > lower_margin and high < upper_margin else "inconclusive"
    raise ValueError(f"unsupported decision mode: {mode}")


class NoninferiorityEquivalence:
    descriptor = InferenceModuleDescriptor(
        module_id="noninferiority_equivalence_v1", version="1",
        title="Noninferiority and equivalence",
        summary="Applies direction-aware frozen margins to confidence intervals; absence of significance never proves equivalence.",
        # Static descriptors state the shipped implementation floor.  C3 is
        # derived only from a sealed end-to-end acceptance package; importing
        # a module must never promote it by declaration.
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
        errors: list[str] = []
        for name, rule in (spec.get("decision_rules") or {}).items():
            mode = rule.get("mode")
            if mode in {"noninferiority", "equivalence"}:
                if not rule.get("margin_provenance") or not rule.get("owner_approved"):
                    errors.append(f"{name} requires approved margin provenance")
        return errors

    def compute(self, payload: Any) -> dict[str, Any]:
        return {"decision": interval_decision(tuple(payload["interval"]), **payload["rule"])}

    def adjudicate(self, payload: Any) -> Any:
        return payload

    def produce_claim_constraints(self, payload: Any) -> list[str]:
        return ["Do not infer equivalence merely because a superiority test is not significant."]


__all__ = ["NoninferiorityEquivalence", "interval_decision"]
