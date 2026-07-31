from __future__ import annotations

"""Compile a VisualArgumentPlan through the existing frozen-evidence renderer."""

from pathlib import Path

from .paper_authoring import (
    EvidenceClaimMap,
    HierarchicalPaperOutline,
    PaperArtifactManifest,
    build_paper_artifacts,
)
from .paper_visual_strategy import VisualArgumentPlan, compile_visual_slots


def apply_visual_plan_to_outline(
    outline: HierarchicalPaperOutline,
    plan: VisualArgumentPlan,
) -> HierarchicalPaperOutline:
    figures, tables = compile_visual_slots(plan)
    return outline.model_copy(
        update={"figure_slots": figures, "table_slots": tables}
    )


def compile_visual_argument_plan(
    run_dir: str | Path,
    *,
    plan: VisualArgumentPlan,
    outline: HierarchicalPaperOutline,
    evidence_claim_map: EvidenceClaimMap,
) -> tuple[HierarchicalPaperOutline, PaperArtifactManifest]:
    compiled_outline = apply_visual_plan_to_outline(outline, plan)
    manifest = build_paper_artifacts(
        Path(run_dir).resolve(),
        outline=compiled_outline,
        evidence_claim_map=evidence_claim_map,
    )
    return compiled_outline, manifest


__all__ = ["apply_visual_plan_to_outline", "compile_visual_argument_plan"]
