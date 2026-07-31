from __future__ import annotations

"""Evidence-bound visual argument plans compiled into paper figure/table slots."""

from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .paper_authoring import (
    EvidenceClaimMap,
    FigureSlot,
    TableSlot,
)
from .paper_venue_policy import VenuePolicyProfile


class FigureSelectionRecord(StrictModel):
    selection_id: str = Field(pattern=r"^selection-[a-z0-9-]{2,100}$")
    rule: Literal[
        "all_registered",
        "random_registered",
        "median_case",
        "largest_improvement",
        "largest_regression",
        "representative_by_cluster",
    ]
    eligible_sample_ids: list[str]
    selected_sample_ids: list[str]
    seed: int | None = None
    cluster_artifact_id: str | None = None
    rationale: str = Field(min_length=10, max_length=2000)

    @model_validator(mode="after")
    def selected_samples_are_eligible(self) -> "FigureSelectionRecord":
        if not set(self.selected_sample_ids) <= set(self.eligible_sample_ids):
            raise ValueError("qualitative figure selected samples outside its frozen pool")
        if self.rule == "random_registered" and self.seed is None:
            raise ValueError("registered random selection requires a frozen seed")
        if self.rule == "representative_by_cluster" and not self.cluster_artifact_id:
            raise ValueError("cluster-based selection requires a frozen cluster artifact")
        return self


class ImageTransformationRecord(StrictModel):
    transformation_id: str = Field(pattern=r"^transform-[a-z0-9-]{2,100}$")
    source_artifact_id: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operations: list[
        Literal[
            "crop",
            "resize",
            "uniform_brightness",
            "uniform_contrast",
            "uniform_color_conversion",
            "annotation_overlay",
            "panel_assembly",
        ]
    ]
    parameters: dict[str, str | int | float | bool]
    applied_uniformly_across_groups: bool
    content_added_or_removed: Literal[False] = False
    generative_tool_used: Literal[False] = False
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CaptionClaimBinding(StrictModel):
    caption_id: str
    figure_id: str
    reader_question: str
    comparison: str
    denominator_claim_ids: list[str]
    uncertainty_claim_ids: list[str]
    observation_claim_ids: list[str]
    limitation_claim_ids: list[str]
    caption_text: str


class FigureSpec(StrictModel):
    figure_id: str = Field(pattern=r"^fig-[a-z0-9-]{2,80}$")
    role: Literal[
        "problem_and_method_overview",
        "primary_result",
        "mechanism_or_ablation",
        "robustness_or_error",
        "cost_or_tradeoff",
        "qualitative_examples",
        "graphical_abstract",
    ]
    reader_question: str = Field(min_length=10, max_length=1500)
    claim_ids: list[str] = Field(min_length=1)
    source_artifact_ids: list[str] = Field(min_length=1)
    source_paths: list[str] = Field(min_length=1)
    visual_type: Literal[
        "editable_process_diagram",
        "paired_point",
        "effect_interval",
        "bar",
        "line",
        "scatter",
        "forest",
        "heatmap",
        "pareto",
        "calibration",
        "qualitative_panel",
        "graphical_abstract",
    ]
    main_or_supplement: Literal["main", "supplement"]
    evidence_status: Literal["evidence", "explanatory_only"] = "evidence"
    must_show: list[str]
    must_not_imply: list[str]
    alt_text: str = Field(min_length=10, max_length=2000)
    minimum_font_points: float = Field(default=8.0, ge=6.0, le=72.0)
    color_vision_safe: bool = True
    output_format: Literal["svg", "pdf", "png", "tiff"] = "svg"
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    units: str | None = None
    truncated_axis: bool = False
    dual_axis: bool = False
    three_dimensional_encoding: bool = False
    duplicates_visual_id: str | None = None
    selection_record: FigureSelectionRecord | None = None
    transformation_records: list[ImageTransformationRecord] = Field(
        default_factory=list
    )
    caption_binding: CaptionClaimBinding

    @model_validator(mode="after")
    def validate_figure_type(self) -> "FigureSpec":
        if self.visual_type == "graphical_abstract" and self.evidence_status != "explanatory_only":
            raise ValueError("a graphical abstract cannot be scientific evidence")
        if self.visual_type == "qualitative_panel" and self.selection_record is None:
            raise ValueError("qualitative panels require a frozen selection record")
        if self.caption_binding.figure_id != self.figure_id:
            raise ValueError("caption binding targets a different figure")
        return self


class TableSpec(StrictModel):
    table_id: str = Field(pattern=r"^tab-[a-z0-9-]{2,80}$")
    role: Literal[
        "experiment_setup",
        "primary_results",
        "ablation",
        "robustness",
        "resource_tradeoff",
    ]
    reader_question: str = Field(min_length=10, max_length=1500)
    claim_ids: list[str] = Field(min_length=1)
    source_artifact_ids: list[str] = Field(min_length=1)
    source_paths: list[str] = Field(min_length=1)
    columns: list[str] = Field(min_length=2)
    main_or_supplement: Literal["main", "supplement"]
    caption_binding: CaptionClaimBinding


class VisualArgumentPlan(StrictModel):
    schema_version: int = 1
    study_id: str
    version: int = Field(default=1, ge=1)
    narrative_contract_id: str
    visual_thesis: str = Field(min_length=20, max_length=3000)
    main_figure_budget: int = Field(ge=0, le=30)
    supplementary_figure_budget: int = Field(ge=0, le=100)
    figures: list[FigureSpec]
    tables: list[TableSpec] = Field(default_factory=list)
    status: Literal["draft", "frozen", "superseded"] = "draft"
    approved_by: str | None = None
    approved_at: str | None = None
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_plan(self) -> "VisualArgumentPlan":
        ids = [item.figure_id for item in self.figures] + [
            item.table_id for item in self.tables
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("visual IDs must be unique")
        main = sum(item.main_or_supplement == "main" for item in self.figures)
        supplement = sum(
            item.main_or_supplement == "supplement" for item in self.figures
        )
        if main > self.main_figure_budget:
            raise ValueError("main figure budget exceeded")
        if supplement > self.supplementary_figure_budget:
            raise ValueError("supplementary figure budget exceeded")
        if self.status == "frozen" and (not self.approved_by or not self.approved_at):
            raise ValueError("a frozen visual plan requires owner approval")
        return self


_CHART_TYPE_MAP = {
    "editable_process_diagram": "flow",
    "paired_point": "scatter",
    "effect_interval": "forest",
    "bar": "bar",
    "line": "line",
    "scatter": "scatter",
    "forest": "forest",
    "heatmap": "heatmap",
    "pareto": "scatter",
    "calibration": "line",
    "qualitative_panel": "diagram",
    "graphical_abstract": "diagram",
}


def validate_visual_argument_plan(
    plan: VisualArgumentPlan,
    *,
    evidence_claim_map: EvidenceClaimMap,
    venue_policy: VenuePolicyProfile,
) -> list[str]:
    violations: list[str] = []
    allowed_claims = {item.claim_id for item in evidence_claim_map.bindings}
    allowed_paths = {
        item.claim_id: {pointer.path for pointer in item.evidence}
        for item in evidence_claim_map.bindings
    }
    if plan.main_figure_budget > venue_policy.visual_policy.main_figure_budget:
        violations.append("visual plan exceeds the venue main-figure budget")
    if (
        plan.supplementary_figure_budget
        > venue_policy.visual_policy.supplementary_figure_budget
    ):
        violations.append("visual plan exceeds the venue supplementary-figure budget")
    for figure in plan.figures:
        unknown = sorted(set(figure.claim_ids) - allowed_claims)
        if unknown:
            violations.append(
                f"{figure.figure_id} references unknown claims: {', '.join(unknown)}"
            )
        expected_paths = {
            path for claim_id in figure.claim_ids for path in allowed_paths.get(claim_id, set())
        }
        if set(figure.source_paths) != expected_paths:
            violations.append(
                f"{figure.figure_id} source paths do not exactly match its claim bindings"
            )
        if (
            figure.visual_type == "graphical_abstract"
            and not venue_policy.visual_policy.graphical_abstract_generation_allowed
        ):
            violations.append(
                f"{figure.figure_id} graphical abstract is not allowed by the venue policy"
            )
    for table in plan.tables:
        unknown = sorted(set(table.claim_ids) - allowed_claims)
        if unknown:
            violations.append(
                f"{table.table_id} references unknown claims: {', '.join(unknown)}"
            )
        expected_paths = {
            path for claim_id in table.claim_ids for path in allowed_paths.get(claim_id, set())
        }
        if set(table.source_paths) != expected_paths:
            violations.append(
                f"{table.table_id} source paths do not exactly match its claim bindings"
            )
    return list(dict.fromkeys(violations))


def compile_visual_slots(
    plan: VisualArgumentPlan,
) -> tuple[list[FigureSlot], list[TableSlot]]:
    figures = [
        FigureSlot(
            slot_id=item.figure_id,
            purpose=item.reader_question,
            evidence_claim_ids=item.claim_ids,
            source_paths=item.source_paths,
            chart_type=_CHART_TYPE_MAP[item.visual_type],  # type: ignore[arg-type]
            renderer=(
                "next_ai_drawio"
                if item.visual_type
                in {
                    "editable_process_diagram",
                    "qualitative_panel",
                    "graphical_abstract",
                }
                else "native_svg"
            ),
            caption_contract=item.caption_binding.caption_text,
        )
        for item in plan.figures
    ]
    tables = [
        TableSlot(
            slot_id=item.table_id,
            purpose=item.reader_question,
            evidence_claim_ids=item.claim_ids,
            source_paths=item.source_paths,
            columns=item.columns,
            caption_contract=item.caption_binding.caption_text,
        )
        for item in plan.tables
    ]
    return figures, tables


__all__ = [
    "CaptionClaimBinding",
    "FigureSelectionRecord",
    "FigureSpec",
    "ImageTransformationRecord",
    "TableSpec",
    "VisualArgumentPlan",
    "compile_visual_slots",
    "validate_visual_argument_plan",
]
