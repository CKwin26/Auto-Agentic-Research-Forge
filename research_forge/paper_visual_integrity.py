from __future__ import annotations

"""Deterministic visual-integrity checks for evidence and explanatory figures."""

from typing import Literal

from pydantic import Field

from .models import StrictModel, utc_now
from .paper_authoring import PaperArtifactManifest
from .paper_venue_policy import VenuePolicyProfile
from .paper_visual_strategy import VisualArgumentPlan


class VisualIntegrityFinding(StrictModel):
    finding_id: str = Field(pattern=r"^visual-finding-[a-z0-9-]{2,100}$")
    severity: Literal["blocking", "major", "minor"]
    visual_id: str | None = None
    category: Literal[
        "provenance",
        "selection",
        "transformation",
        "data_encoding",
        "caption",
        "layout",
        "accessibility",
        "duplication",
    ]
    message: str
    required_action: str


class VisualIntegrityReport(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    checks: dict[str, bool]
    findings: list[VisualIntegrityFinding]
    audited_visual_ids: list[str]


def audit_visual_integrity(
    plan: VisualArgumentPlan,
    manifest: PaperArtifactManifest,
    *,
    venue_policy: VenuePolicyProfile,
) -> VisualIntegrityReport:
    findings: list[VisualIntegrityFinding] = []
    records = {item.slot_id: item for item in manifest.records}
    plan_ids = [item.figure_id for item in plan.figures] + [
        item.table_id for item in plan.tables
    ]

    def hash_bound(record: object) -> bool:
        artifact_hash = bool(getattr(record, "artifact_sha256", None))
        deterministic_table = (
            getattr(record, "kind", None) == "table"
            and bool(getattr(record, "data_sha256", None))
            and bool(getattr(record, "rendered_markdown", None))
        )
        return bool(
            (artifact_hash or deterministic_table)
            and getattr(record, "source_paths", None)
            and getattr(record, "rendering_code_path", None)
            and getattr(record, "rendering_code_sha256", None)
        )

    for visual_id in plan_ids:
        record = records.get(visual_id)
        if record is None:
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-missing-{visual_id}",
                    severity="blocking",
                    visual_id=visual_id,
                    category="provenance",
                    message="planned visual has no artifact-manifest record",
                    required_action="render the visual or mark its evidence requirement unresolved",
                )
            )
        elif record.status != "rendered":
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-unresolved-{visual_id}",
                    severity="blocking",
                    visual_id=visual_id,
                    category="provenance",
                    message=f"planned visual is {record.status}",
                    required_action=record.reason,
                )
            )
        elif not hash_bound(record):
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-unbound-{visual_id}",
                    severity="blocking",
                    visual_id=visual_id,
                    category="provenance",
                    message=(
                        "rendered visual lacks a source path, rendering-code "
                        "hash, or hashed rendered/data representation"
                    ),
                    required_action=(
                        "bind and hash the source data, rendering code, and "
                        "rendered figure or deterministic table representation"
                    ),
                )
            )
    for figure in plan.figures:
        record = records.get(figure.figure_id)
        if figure.visual_type in {
            "editable_process_diagram",
            "qualitative_panel",
            "graphical_abstract",
        } and (
            record is None
            or record.renderer != "next_ai_drawio"
            or record.editor_backend != "next_ai_drawio"
            or not record.editable_source_sha256
            or not record.editor_config_sha256
            or not record.editor_audit_sha256
            or record.editor_audit_passed is not True
        ):
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-next-ai-drawio-{figure.figure_id}",
                    severity="blocking",
                    visual_id=figure.figure_id,
                    category="provenance",
                    message=(
                        "conceptual figure did not complete the mandatory "
                        "next-ai-draw-io source and audit path"
                    ),
                    required_action=(
                        "create and audit the editable next-ai-draw-io source, "
                        "then export the approved vector artifact"
                    ),
                )
            )
        if (
            record is not None
            and record.status == "rendered"
            and record.artifact_path
            and not record.artifact_path.casefold().endswith(
                f".{figure.output_format}"
            )
        ):
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-format-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="layout",
                    message="rendered artifact format differs from the approved visual plan",
                    required_action="rerender the approved output format from frozen evidence",
                )
            )
        if venue_policy.visual_policy.alt_text_required and not figure.alt_text.strip():
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-alt-text-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="accessibility",
                    message="figure lacks required alternative text",
                    required_action="add evidence-calibrated alternative text",
                )
            )
        if (
            venue_policy.visual_policy.color_vision_safe_required
            and not figure.color_vision_safe
        ):
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-color-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="accessibility",
                    message="figure is not declared color-vision safe",
                    required_action="use a color-vision-safe palette and redundant encodings",
                )
            )
        if figure.minimum_font_points < venue_policy.visual_policy.minimum_font_points:
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-font-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="layout",
                    message="figure text is smaller than the venue minimum",
                    required_action=(
                        f"use at least {venue_policy.visual_policy.minimum_font_points:g} pt"
                    ),
                )
            )
        if (
            venue_policy.visual_policy.vector_output_required
            and figure.output_format not in {"svg", "pdf"}
        ):
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-vector-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="layout",
                    message="venue requires a vector figure output",
                    required_action="render the final figure as SVG or PDF",
                )
            )
        if figure.truncated_axis:
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-axis-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="data_encoding",
                    message="figure uses a truncated axis without an approved exception",
                    required_action="restore the full axis or register and disclose the truncation",
                )
            )
        if figure.dual_axis or figure.three_dimensional_encoding:
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-encoding-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="data_encoding",
                    message="dual-axis or three-dimensional encoding is prohibited",
                    required_action="use a direct two-dimensional single-axis comparison",
                )
            )
        if figure.duplicates_visual_id:
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-duplicate-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="duplication",
                    message=f"figure duplicates {figure.duplicates_visual_id}",
                    required_action="remove the duplicate or make the distinct analytical role explicit",
                )
            )
        if figure.visual_type == "qualitative_panel":
            if figure.selection_record is None:
                findings.append(
                    VisualIntegrityFinding(
                        finding_id=f"visual-finding-selection-{figure.figure_id}",
                        severity="blocking",
                        visual_id=figure.figure_id,
                        category="selection",
                        message="qualitative panel lacks a frozen selection rule",
                        required_action="freeze the eligible pool, rule, seed, and selected IDs",
                    )
                )
            if not figure.transformation_records:
                findings.append(
                    VisualIntegrityFinding(
                        finding_id=f"visual-finding-transform-log-{figure.figure_id}",
                        severity="blocking",
                        visual_id=figure.figure_id,
                        category="transformation",
                        message="qualitative panel lacks an image transformation log",
                        required_action=(
                            "record source hashes, crops, resize, annotations, "
                            "panel assembly, and output hashes"
                        ),
                    )
                )
            for transformation in figure.transformation_records:
                if (
                    transformation.generative_tool_used
                    or transformation.content_added_or_removed
                ):
                    findings.append(
                        VisualIntegrityFinding(
                            finding_id=(
                                f"visual-finding-transform-{transformation.transformation_id}"
                            ),
                            severity="blocking",
                            visual_id=figure.figure_id,
                            category="transformation",
                            message="scientific image content was generated, added, or removed",
                            required_action="return to the unmodified source image",
                        )
                    )
        if (
            figure.evidence_status == "evidence"
            and figure.visual_type == "graphical_abstract"
        ):
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-status-{figure.figure_id}",
                    severity="blocking",
                    visual_id=figure.figure_id,
                    category="data_encoding",
                    message="graphical abstract is incorrectly classified as evidence",
                    required_action="mark it explanatory_only",
                )
            )
        caption = figure.caption_binding
        if not caption.denominator_claim_ids and figure.role in {
            "primary_result",
            "mechanism_or_ablation",
            "robustness_or_error",
            "cost_or_tradeoff",
        }:
            findings.append(
                VisualIntegrityFinding(
                    finding_id=f"visual-finding-denominator-{figure.figure_id}",
                    severity="major",
                    visual_id=figure.figure_id,
                    category="caption",
                    message="result figure caption does not bind a denominator claim",
                    required_action="bind the sample size or eligible denominator",
                )
            )
    checks = {
        "all_planned_visuals_manifested": all(item in records for item in plan_ids),
        "all_visuals_rendered": all(
            records.get(item) is not None and records[item].status == "rendered"
            for item in plan_ids
        ),
        "all_visuals_hash_bound": all(
            records.get(item) is not None
            and hash_bound(records[item])
            for item in plan_ids
        ),
        "scientific_images_not_generative": not any(
            finding.category == "transformation" for finding in findings
        ),
        "graphical_abstracts_explanatory_only": not any(
            finding.category == "data_encoding" for finding in findings
        ),
        "caption_claims_complete": not any(
            finding.category == "caption"
            and finding.severity in {"blocking", "major"}
            for finding in findings
        ),
        "venue_vector_policy_satisfied": not any(
            item.finding_id.startswith(
                ("visual-finding-vector-", "visual-finding-format-")
            )
            for item in findings
        ),
        "venue_accessibility_policy_satisfied": not any(
            item.category == "accessibility" for item in findings
        ),
        "font_size_policy_satisfied": not any(
            item.finding_id.startswith("visual-finding-font-")
            for item in findings
        ),
        "axes_and_encoding_safe": not any(
            item.category == "data_encoding" for item in findings
        ),
        "no_visual_duplication": not any(
            item.category == "duplication" for item in findings
        ),
    }
    return VisualIntegrityReport(
        passed=all(checks.values())
        and not any(item.severity in {"blocking", "major"} for item in findings),
        checks=checks,
        findings=findings,
        audited_visual_ids=sorted(plan_ids),
    )


__all__ = [
    "VisualIntegrityFinding",
    "VisualIntegrityReport",
    "audit_visual_integrity",
]
