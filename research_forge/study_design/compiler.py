"""Compile the Study Design portion of one executable Research Contract."""

from __future__ import annotations

from typing import Any

from .registry import study_design
from .schemas import AnalysisPlan


def compile_analysis_plan(contract: Any) -> AnalysisPlan:
    binding = getattr(contract, "study_design", None) or {}
    design_id = (
        str(binding.get("id") or "")
        if isinstance(binding, dict)
        else str(binding.id)
    )
    if not design_id:
        raise ValueError("Research Contract does not select a Study Design")
    return study_design(design_id).compile_analysis_plan(contract)


__all__ = ["compile_analysis_plan"]
