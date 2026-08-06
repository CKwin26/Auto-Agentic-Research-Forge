"""Contract completion orchestration for Study Designs and modules."""

from __future__ import annotations

from typing import Any

from .registry import inference_module, study_design
from .schemas import StudyDesignCompletionPatch


def complete_study_design_contract(
    task_brief: Any,
    contract: Any,
    resources: Any | None = None,
) -> list[StudyDesignCompletionPatch]:
    binding = getattr(contract, "study_design", None) or {}
    design_id = (
        str(binding.get("id") or "")
        if isinstance(binding, dict)
        else str(binding.id)
    )
    if not design_id:
        return []
    patches = [
        study_design(design_id).complete_contract(
            task_brief, contract, resources
        )
    ]
    for raw in getattr(contract, "inference_modules", []) or []:
        module_id = (
            str(raw.get("id") or "")
            if isinstance(raw, dict)
            else str(raw.id)
        )
        patches.append(inference_module(module_id).complete_contract(contract))
    return patches


__all__ = ["complete_study_design_contract"]
