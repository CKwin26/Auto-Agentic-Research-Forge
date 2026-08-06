from __future__ import annotations

"""Typed, evidence-bound scientific figures for canonical Stage 4.

The generic paper visual plan answers where a visual belongs.  This module
answers the scientific question that the generic layer intentionally cannot:
which encodings are valid for a registered experiment Profile and whether the
rendered values preserve the frozen Evaluation/Statistics artifacts.

No value is parsed from manuscript prose.  Callers must provide a hash-bound
``FigureDataBinding`` created from a formal Stage 3 artifact.
"""

import hashlib
import json
import math
import re
from html import escape
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .storage import sha256_file, write_json_atomic


FigureType = Literal[
    "arm_distribution",
    "effect_interval",
    "sample_flow",
    "factorial_interaction",
    "factorial_effect_forest",
    "longitudinal_trajectory",
    "slope_distribution",
    "kaplan_meier",
    "propensity_overlap",
    "covariate_balance",
    "equivalence_interval",
    "posterior_density",
    "online_effect_forest",
    "paired_item_difference",
    "rater_agreement",
    "multi_outcome_forest",
    "adjusted_p_value_table",
]


class ScientificFigureIntent(StrictModel):
    intent_id: str = Field(pattern=r"^figure-intent-[a-z0-9-]{2,100}$")
    study_id: str
    profile_id: str
    estimand_id: str
    figure_type: FigureType
    reader_question: str = Field(min_length=10, max_length=2_000)
    source_artifact_ids: list[str] = Field(min_length=1)
    caption_claim_ids: list[str] = Field(min_length=1)


class FigureDataBinding(StrictModel):
    binding_id: str = Field(pattern=r"^figure-data-[a-z0-9-]{2,100}$")
    source_artifact_ids: list[str] = Field(min_length=1)
    source_paths: list[str] = Field(min_length=1)
    source_hashes: dict[str, str] = Field(min_length=1)
    data_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_columns: list[str] = Field(min_length=1)
    rows: list[dict[str, Any]] = Field(min_length=1)
    frozen: Literal[True] = True

    @model_validator(mode="after")
    def validate_binding(self) -> "FigureDataBinding":
        if len(self.data_columns) != len(set(self.data_columns)):
            raise ValueError("figure data columns must be unique")
        missing = sorted(
            column
            for column in self.data_columns
            if any(column not in row for row in self.rows)
        )
        if missing:
            raise ValueError(
                "figure rows do not contain every declared column: "
                + ", ".join(missing)
            )
        payload = json.dumps(
            self.rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if hashlib.sha256(payload).hexdigest() != self.data_hash:
            raise ValueError("figure data hash does not match the bound rows")
        if not set(self.source_paths) <= set(self.source_hashes):
            raise ValueError("every figure source path requires a source hash")
        if any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in self.source_hashes.values()):
            raise ValueError("figure source hashes must be SHA-256 digests")
        return self


class ScientificFigureSpec(StrictModel):
    schema_version: int = 2
    figure_id: str = Field(pattern=r"^fig-[a-z0-9-]{2,100}$")
    profile_id: str
    estimand_id: str
    figure_type: FigureType
    source_artifact_ids: list[str] = Field(min_length=1)
    data_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_columns: list[str] = Field(min_length=1)
    point_estimate_field: str | None = None
    lower_interval_field: str | None = None
    upper_interval_field: str | None = None
    reference_value: float | None = None
    lower_margin: float | None = None
    upper_margin: float | None = None
    x_encoding: str | None = None
    y_encoding: str | None = None
    group_encoding: str | None = None
    facet_encoding: str | None = None
    time_field: str | None = None
    event_field: str | None = None
    censoring_field: str | None = None
    posterior_field: str | None = None
    unit: str
    denominator: str
    confidence_or_credible_level: float | None = Field(default=None, gt=0, lt=1)
    interval_kind: Literal[
        "confidence", "credible", "nominal", "multiplicity_adjusted", "none"
    ] = "none"
    effect_measure: Literal[
        "difference",
        "risk_difference",
        "odds_ratio",
        "risk_ratio",
        "hazard_ratio",
        "slope_difference",
        "rmst_difference",
        "posterior_difference",
        "descriptive",
    ]
    caption_claim_ids: list[str] = Field(min_length=1)
    caption: str = Field(min_length=10, max_length=5_000)
    alt_text: str = Field(min_length=10, max_length=5_000)
    renderer: str
    renderer_version: str
    output_formats: list[Literal["svg", "pdf", "png"]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_spec(self) -> "ScientificFigureSpec":
        interval_fields = (
            self.lower_interval_field,
            self.upper_interval_field,
        )
        if any(interval_fields) and not all(interval_fields):
            raise ValueError("an interval requires both lower and upper fields")
        if self.interval_kind != "none" and not all(interval_fields):
            raise ValueError("an interval label requires complete interval fields")
        ratio = self.effect_measure in {"odds_ratio", "risk_ratio", "hazard_ratio"}
        expected_null = 1.0 if ratio else 0.0
        if self.reference_value is not None and not math.isclose(
            self.reference_value, expected_null, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError(
                f"{self.effect_measure} requires null reference {expected_null:g}"
            )
        if (self.lower_margin is None) != (self.upper_margin is None):
            raise ValueError("equivalence margins require both lower and upper values")
        if (
            self.lower_margin is not None
            and self.upper_margin is not None
            and self.lower_margin >= self.upper_margin
        ):
            raise ValueError("lower equivalence margin must be below upper margin")
        if len(self.output_formats) != len(set(self.output_formats)):
            raise ValueError("figure output formats must be unique")
        return self


class FigureBlockingIssue(StrictModel):
    issue_id: str = Field(pattern=r"^figure-block-[a-z0-9-]{2,120}$")
    figure_id: str
    code: str
    message: str
    required_action: str
    critical: Literal[True] = True


class FigureSemanticValidationReport(StrictModel):
    schema_version: int = 2
    figure_id: str
    passed: bool
    checks: dict[str, bool]
    issues: list[FigureBlockingIssue]
    validated_at: str = Field(default_factory=utc_now)


class FigureVisualValidationReport(StrictModel):
    schema_version: int = 2
    figure_id: str
    passed: bool
    output_path: str
    output_sha256: str | None = None
    checks: dict[str, bool]
    issues: list[FigureBlockingIssue]
    validated_at: str = Field(default_factory=utc_now)


class FigureAcceptanceReport(StrictModel):
    schema_version: int = 2
    figure_id: str
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    renderer: str
    renderer_version: str
    output_hashes: dict[str, str]
    semantic_validation: FigureSemanticValidationReport
    visual_validation: list[FigureVisualValidationReport]
    accepted: bool
    signed_at: str = Field(default_factory=utc_now)


class ProfileFigureRequirement(StrictModel):
    figure_type: FigureType
    required_fields: list[str]
    minimum_caption_content: list[str]
    allowed_fallback: list[FigureType] = Field(default_factory=list)
    forbidden_chart_types: list[str] = Field(default_factory=list)
    semantic_rules: list[str] = Field(default_factory=list)


class ProfileFigureAdapter(StrictModel):
    profile_id: str
    natural_language_name: str
    requirements: list[ProfileFigureRequirement] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_figure_types(self) -> "ProfileFigureAdapter":
        values = [item.figure_type for item in self.requirements]
        if len(values) != len(set(values)):
            raise ValueError("Profile figure requirements must be unique")
        return self


_INTERNAL_TOKEN = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|(?:[A-Za-z]:\\|/Users/|/home/)|"
    r"\b(?:run|task|study)-[a-z0-9-]{6,}\b)",
    re.IGNORECASE,
)


class FigureSemanticValidator:
    """Fail-closed validator for figure data, semantics, and captions."""

    version = "scientific-figure-semantic-validator-v2"

    @classmethod
    def validate(
        cls,
        spec: ScientificFigureSpec,
        binding: FigureDataBinding,
        *,
        contract_direction: Literal["increase", "decrease", "two_sided"] | None = None,
    ) -> FigureSemanticValidationReport:
        checks: dict[str, bool] = {}
        issues: list[FigureBlockingIssue] = []

        def check(name: str, ok: bool, message: str, action: str) -> None:
            checks[name] = bool(ok)
            if not ok:
                issues.append(
                    FigureBlockingIssue(
                        issue_id=f"figure-block-{spec.figure_id.removeprefix('fig-')}-{name.replace('_', '-')}",
                        figure_id=spec.figure_id,
                        code=name.upper(),
                        message=message,
                        required_action=action,
                    )
                )

        check(
            "data_hash_bound",
            spec.data_hash == binding.data_hash,
            "figure spec and FigureData artifact hashes differ",
            "rebuild the figure spec from the frozen FigureData artifact",
        )
        check(
            "source_artifacts_bound",
            set(spec.source_artifact_ids) == set(binding.source_artifact_ids),
            "figure spec does not exactly bind the formal source artifacts",
            "bind the exact Evaluation/Statistics artifact IDs",
        )
        check(
            "data_columns_bound",
            set(spec.data_columns) == set(binding.data_columns),
            "figure spec columns differ from the frozen FigureData schema",
            "regenerate the spec without adding or dropping data columns",
        )
        required = {
            value
            for value in (
                spec.point_estimate_field,
                spec.lower_interval_field,
                spec.upper_interval_field,
                spec.x_encoding,
                spec.y_encoding,
                spec.group_encoding,
                spec.facet_encoding,
                spec.time_field,
                spec.event_field,
                spec.censoring_field,
                spec.posterior_field,
            )
            if value
        }
        check(
            "required_fields_present",
            required <= set(binding.data_columns),
            "one or more encoded fields are absent from FigureData",
            "supply a complete formal FigureData artifact or block the figure",
        )
        interval_valid = True
        if spec.point_estimate_field and spec.lower_interval_field and spec.upper_interval_field:
            for row in binding.rows:
                point = row.get(spec.point_estimate_field)
                lower = row.get(spec.lower_interval_field)
                upper = row.get(spec.upper_interval_field)
                if not all(isinstance(value, (int, float)) for value in (point, lower, upper)):
                    interval_valid = False
                    break
                if not float(lower) <= float(point) <= float(upper):
                    interval_valid = False
                    break
        check(
            "ordered_complete_intervals",
            interval_valid,
            "interval values are missing, nonnumeric, or do not satisfy lower <= point <= upper",
            "correct the formal Statistics artifact; do not repair values in Stage 4",
        )
        ratio = spec.effect_measure in {"odds_ratio", "risk_ratio", "hazard_ratio"}
        expected_null = 1.0 if ratio else 0.0
        check(
            "reference_measure_consistent",
            spec.reference_value is None
            or math.isclose(spec.reference_value, expected_null, rel_tol=0, abs_tol=1e-12),
            "the null reference is inconsistent with the effect measure",
            f"use {expected_null:g} as the registered null reference",
        )
        margin_valid = True
        if spec.lower_margin is not None and spec.upper_margin is not None:
            margin_valid = spec.lower_margin < spec.upper_margin
            if spec.effect_measure in {"odds_ratio", "risk_ratio", "hazard_ratio"}:
                margin_valid = margin_valid and spec.lower_margin > 0
        check(
            "margin_unit_direction_valid",
            margin_valid,
            "equivalence margins are invalid for the registered effect scale",
            "bind margins from the frozen Research Contract on the correct scale",
        )
        check(
            "interval_label_semantics",
            not (
                spec.interval_kind == "credible"
                and "confidence interval" in (spec.caption + " " + spec.alt_text).casefold()
            )
            and not (
                spec.interval_kind == "nominal"
                and "multiplicity-adjusted interval"
                in (spec.caption + " " + spec.alt_text).casefold()
            ),
            "caption mislabels the registered interval type",
            "use the interval terminology recorded in the Statistics artifact",
        )
        check(
            "denominator_present",
            bool(spec.denominator.strip()),
            "figure denominator is missing",
            "bind the denominator from the formal Evaluation artifact",
        )
        public_text = " ".join((spec.caption, spec.alt_text, spec.unit, spec.denominator))
        check(
            "reader_facing_labels",
            _INTERNAL_TOKEN.search(public_text) is None,
            "figure text exposes an internal field, local path, or run identifier",
            "replace internal tokens with natural scholarly labels",
        )
        check(
            "effect_direction_bound",
            contract_direction in {None, "increase", "decrease", "two_sided"},
            "figure direction is not bound to the Research Contract",
            "supply the frozen effect direction",
        )
        return FigureSemanticValidationReport(
            figure_id=spec.figure_id,
            passed=not issues,
            checks=checks,
            issues=issues,
        )


Renderer = Callable[[ScientificFigureSpec, FigureDataBinding, Path], Path]


class FigureRendererRegistry:
    def __init__(self) -> None:
        self._renderers: dict[str, tuple[str, Renderer]] = {}

    def register(self, name: str, version: str, renderer: Renderer) -> None:
        if name in self._renderers:
            raise ValueError(f"duplicate scientific figure renderer: {name}")
        self._renderers[name] = (version, renderer)

    def resolve(self, name: str) -> tuple[str, Renderer]:
        try:
            return self._renderers[name]
        except KeyError as exc:
            raise ValueError(f"unregistered scientific figure renderer: {name}") from exc

    def snapshot(self) -> dict[str, str]:
        return {name: value[0] for name, value in sorted(self._renderers.items())}


def _number(value: Any) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError("scientific figure renderer received a nonnumeric value")
    return float(value)


def _svg_text(x: float, y: float, text: str, *, size: int = 12, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, sans-serif" '
        f'font-size="{size}" text-anchor="{anchor}" fill="#051d25">{escape(text)}</text>'
    )


def _scale(values: list[float], low: float, high: float) -> Callable[[float], float]:
    minimum = min(values)
    maximum = max(values)
    if math.isclose(minimum, maximum):
        minimum -= 1.0
        maximum += 1.0
    return lambda value: low + (value - minimum) / (maximum - minimum) * (high - low)


def _render_interval_svg(
    spec: ScientificFigureSpec, binding: FigureDataBinding, output: Path
) -> Path:
    if not (
        spec.point_estimate_field
        and spec.lower_interval_field
        and spec.upper_interval_field
    ):
        raise ValueError("interval renderer requires point, lower, and upper fields")
    rows = binding.rows
    points = [_number(row[spec.point_estimate_field]) for row in rows]
    lowers = [_number(row[spec.lower_interval_field]) for row in rows]
    uppers = [_number(row[spec.upper_interval_field]) for row in rows]
    references = [value for value in (spec.reference_value, spec.lower_margin, spec.upper_margin) if value is not None]
    x = _scale(lowers + uppers + references, 180, 760)
    height = 110 + 42 * len(rows)
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" viewBox="0 0 900 {height}">',
        '<rect width="900" height="100%" fill="white"/>',
        _svg_text(450, 28, spec.caption, size=13, anchor="middle"),
    ]
    if spec.lower_margin is not None and spec.upper_margin is not None:
        elements.append(
            f'<rect x="{x(spec.lower_margin):.1f}" y="45" width="{x(spec.upper_margin)-x(spec.lower_margin):.1f}" '
            f'height="{height-75}" fill="#a9c7ce" opacity="0.35"/>'
        )
    if spec.reference_value is not None:
        elements.append(
            f'<line x1="{x(spec.reference_value):.1f}" y1="45" x2="{x(spec.reference_value):.1f}" '
            f'y2="{height-30}" stroke="#051d25" stroke-width="1.4" stroke-dasharray="5 4"/>'
        )
    for index, (row, point, lower, upper) in enumerate(zip(rows, points, lowers, uppers, strict=True)):
        y = 75 + index * 42
        label_field = spec.group_encoding or spec.y_encoding
        label = str(row.get(label_field, f"Outcome {index + 1}")) if label_field else f"Outcome {index + 1}"
        elements.extend(
            [
                _svg_text(165, y + 4, label, anchor="end"),
                f'<line x1="{x(lower):.1f}" y1="{y}" x2="{x(upper):.1f}" y2="{y}" stroke="#3a747d" stroke-width="3"/>',
                f'<line x1="{x(lower):.1f}" y1="{y-6}" x2="{x(lower):.1f}" y2="{y+6}" stroke="#3a747d"/>',
                f'<line x1="{x(upper):.1f}" y1="{y-6}" x2="{x(upper):.1f}" y2="{y+6}" stroke="#3a747d"/>',
                f'<circle cx="{x(point):.1f}" cy="{y}" r="5" fill="#0d454e"/>',
                _svg_text(775, y + 4, f"{point:.3g} [{lower:.3g}, {upper:.3g}]", size=11),
            ]
        )
    elements.extend([_svg_text(470, height - 10, f"Effect ({spec.unit})", anchor="middle"), "</svg>"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements), encoding="utf-8", newline="\n")
    return output


def _render_line_svg(
    spec: ScientificFigureSpec, binding: FigureDataBinding, output: Path
) -> Path:
    x_field = spec.time_field or spec.x_encoding
    y_field = spec.y_encoding or spec.point_estimate_field
    if not x_field or not y_field:
        raise ValueError("line renderer requires x/time and y encodings")
    groups: dict[str, list[tuple[Any, float]]] = {}
    for row in binding.rows:
        group = str(row.get(spec.group_encoding, "Series")) if spec.group_encoding else "Series"
        groups.setdefault(group, []).append((row[x_field], _number(row[y_field])))
    values_x = [point[0] for points in groups.values() for point in points]
    values_y = [point[1] for points in groups.values() for point in points]
    numeric_x = all(isinstance(value, (int, float)) for value in values_x)
    if numeric_x:
        sx = _scale([float(value) for value in values_x], 90, 780)
    else:
        categories = list(dict.fromkeys(str(value) for value in values_x))
        category_x = {
            value: 120 + index * (620 / max(1, len(categories) - 1))
            for index, value in enumerate(categories)
        }
        sx = lambda value: category_x[str(value)]
    sy0 = _scale(values_y, 500, 70)
    colors = ["#0d454e", "#3a747d", "#70a1a9", "#051d25"]
    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560">',
        '<rect width="900" height="560" fill="white"/>',
        _svg_text(450, 28, spec.caption, size=13, anchor="middle"),
        '<line x1="90" y1="500" x2="780" y2="500" stroke="#051d25"/>',
        '<line x1="90" y1="70" x2="90" y2="500" stroke="#051d25"/>',
    ]
    for index, (group, points) in enumerate(sorted(groups.items())):
        ordered = sorted(points)
        coords = " ".join(f"{sx(a):.1f},{sy0(b):.1f}" for a, b in ordered)
        color = colors[index % len(colors)]
        elements.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="3"/>')
        for a, b in ordered:
            elements.append(f'<circle cx="{sx(a):.1f}" cy="{sy0(b):.1f}" r="3.5" fill="{color}"/>')
        if spec.lower_interval_field and spec.upper_interval_field:
            group_rows = [
                row
                for row in binding.rows
                if (
                    str(row.get(spec.group_encoding, "Series"))
                    if spec.group_encoding
                    else "Series"
                )
                == group
            ]
            for row in group_rows:
                x_value = row[x_field]
                lower = _number(row[spec.lower_interval_field])
                upper = _number(row[spec.upper_interval_field])
                elements.extend(
                    [
                        f'<line x1="{sx(x_value):.1f}" y1="{sy0(lower):.1f}" '
                        f'x2="{sx(x_value):.1f}" y2="{sy0(upper):.1f}" '
                        f'stroke="{color}" stroke-width="1.2"/>',
                        f'<line x1="{sx(x_value)-4:.1f}" y1="{sy0(lower):.1f}" '
                        f'x2="{sx(x_value)+4:.1f}" y2="{sy0(lower):.1f}" stroke="{color}"/>',
                        f'<line x1="{sx(x_value)-4:.1f}" y1="{sy0(upper):.1f}" '
                        f'x2="{sx(x_value)+4:.1f}" y2="{sy0(upper):.1f}" stroke="{color}"/>',
                    ]
                )
        elements.append(_svg_text(800, 85 + index * 22, group, size=11))
    elements.extend(
        [
            _svg_text(440, 540, x_field.replace("_", " "), anchor="middle"),
            _svg_text(25, 285, f"{y_field.replace('_', ' ')} ({spec.unit})", anchor="middle"),
            "</svg>",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements), encoding="utf-8", newline="\n")
    return output


def _render_scatter_svg(
    spec: ScientificFigureSpec, binding: FigureDataBinding, output: Path
) -> Path:
    category_field = spec.x_encoding or spec.group_encoding
    value_field = spec.y_encoding or spec.point_estimate_field
    if not category_field or not value_field:
        raise ValueError("scatter renderer requires category and value encodings")
    categories = list(dict.fromkeys(str(row[category_field]) for row in binding.rows))
    values = [_number(row[value_field]) for row in binding.rows]
    sy = _scale(values, 480, 65)
    spacing = 650 / max(1, len(categories))
    centers = {
        category: 120 + spacing * (index + 0.5)
        for index, category in enumerate(categories)
    }
    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560">',
        '<rect width="900" height="560" fill="white"/>',
        _svg_text(450, 28, spec.caption, size=13, anchor="middle"),
        '<line x1="90" y1="500" x2="810" y2="500" stroke="#051d25"/>',
        '<line x1="90" y1="55" x2="90" y2="500" stroke="#051d25"/>',
    ]
    for index, row in enumerate(binding.rows):
        category = str(row[category_field])
        jitter = ((index * 37) % 17 - 8) * 1.2
        elements.append(
            f'<circle cx="{centers[category] + jitter:.1f}" cy="{sy(_number(row[value_field])):.1f}" '
            'r="3.2" fill="#0d454e" opacity="0.72"/>'
        )
    for category in categories:
        category_values = [
            _number(row[value_field])
            for row in binding.rows
            if str(row[category_field]) == category
        ]
        mean = sum(category_values) / len(category_values)
        center = centers[category]
        elements.extend(
            [
                f'<line x1="{center-24:.1f}" y1="{sy(mean):.1f}" x2="{center+24:.1f}" '
                'y2="{:.1f}" stroke="#051d25" stroke-width="3"/>'.format(sy(mean)),
                _svg_text(center, 523, category, size=11, anchor="middle"),
            ]
        )
    elements.extend([_svg_text(28, 285, f"{value_field.replace('_', ' ')} ({spec.unit})", anchor="middle"), "</svg>"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements), encoding="utf-8", newline="\n")
    return output


def _render_histogram_svg(
    spec: ScientificFigureSpec, binding: FigureDataBinding, output: Path
) -> Path:
    value_field = spec.x_encoding or spec.posterior_field
    if not value_field:
        raise ValueError("histogram renderer requires a numeric x encoding")
    group_field = spec.group_encoding
    groups: dict[str, list[float]] = {}
    for row in binding.rows:
        label = str(row.get(group_field, "Distribution")) if group_field else "Distribution"
        groups.setdefault(label, []).append(_number(row[value_field]))
    all_values = [value for values in groups.values() for value in values]
    minimum, maximum = min(all_values), max(all_values)
    if math.isclose(minimum, maximum):
        minimum -= 0.5
        maximum += 0.5
    bins = 24
    width = (maximum - minimum) / bins
    counts: dict[str, list[int]] = {label: [0] * bins for label in groups}
    for label, values in groups.items():
        for value in values:
            index = min(bins - 1, max(0, int((value - minimum) / width)))
            counts[label][index] += 1
    max_count = max(max(values) for values in counts.values())
    sx = _scale([minimum, maximum], 90, 790)
    sy = _scale([0.0, float(max_count)], 500, 80)
    colors = ["#0d454e", "#70a1a9", "#3a747d"]
    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560">',
        '<rect width="900" height="560" fill="white"/>',
        _svg_text(450, 28, spec.caption, size=13, anchor="middle"),
        '<line x1="90" y1="500" x2="790" y2="500" stroke="#051d25"/>',
        '<line x1="90" y1="80" x2="90" y2="500" stroke="#051d25"/>',
    ]
    for group_index, (label, values) in enumerate(sorted(counts.items())):
        color = colors[group_index % len(colors)]
        for bin_index, count in enumerate(values):
            left = minimum + bin_index * width
            right = left + width
            elements.append(
                f'<rect x="{sx(left):.1f}" y="{sy(float(count)):.1f}" '
                f'width="{max(1.0, sx(right)-sx(left)):.1f}" height="{500-sy(float(count)):.1f}" '
                f'fill="{color}" opacity="0.42" stroke="{color}"/>'
            )
        elements.append(_svg_text(805, 90 + group_index * 22, label, size=11))
    if spec.lower_margin is not None and spec.upper_margin is not None:
        elements.append(
            f'<rect x="{sx(spec.lower_margin):.1f}" y="80" '
            f'width="{sx(spec.upper_margin)-sx(spec.lower_margin):.1f}" height="420" '
            'fill="#a9c7ce" opacity="0.22"/>'
        )
    if spec.reference_value is not None:
        elements.append(
            f'<line x1="{sx(spec.reference_value):.1f}" y1="80" '
            f'x2="{sx(spec.reference_value):.1f}" y2="500" stroke="#051d25" '
            'stroke-dasharray="5 4"/>'
        )
    elements.extend([_svg_text(440, 540, f"{value_field.replace('_', ' ')} ({spec.unit})", anchor="middle"), "</svg>"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements), encoding="utf-8", newline="\n")
    return output


def _render_kaplan_meier_svg(
    spec: ScientificFigureSpec, binding: FigureDataBinding, output: Path
) -> Path:
    time_field = spec.time_field or spec.x_encoding
    survival_field = spec.y_encoding or spec.point_estimate_field
    group_field = spec.group_encoding
    if not time_field or not survival_field or not group_field:
        raise ValueError("Kaplan-Meier renderer requires time, survival, and arm fields")
    times = [_number(row[time_field]) for row in binding.rows]
    sx = _scale(times, 90, 760)
    sy = lambda value: 480 - value * 390
    colors = ["#0d454e", "#70a1a9", "#3a747d"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in binding.rows:
        groups.setdefault(str(row[group_field]), []).append(row)
    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="620" viewBox="0 0 900 620">',
        '<rect width="900" height="620" fill="white"/>',
        _svg_text(450, 28, spec.caption, size=13, anchor="middle"),
        '<line x1="90" y1="480" x2="760" y2="480" stroke="#051d25"/>',
        '<line x1="90" y1="90" x2="90" y2="480" stroke="#051d25"/>',
        _svg_text(25, 280, "Survival probability", anchor="middle"),
    ]
    for index, (group, rows) in enumerate(sorted(groups.items())):
        color = colors[index % len(colors)]
        ordered = sorted(rows, key=lambda row: _number(row[time_field]))
        path_points: list[str] = []
        previous_y: float | None = None
        for row in ordered:
            x = sx(_number(row[time_field]))
            y = sy(_number(row[survival_field]))
            if previous_y is not None:
                path_points.append(f"{x:.1f},{previous_y:.1f}")
            path_points.append(f"{x:.1f},{y:.1f}")
            previous_y = y
            censored = int(row.get(spec.censoring_field or "censored", 0) or 0)
            if censored:
                elements.append(f'<path d="M{x-4:.1f},{y-4:.1f} L{x+4:.1f},{y+4:.1f} M{x-4:.1f},{y+4:.1f} L{x+4:.1f},{y-4:.1f}" stroke="{color}"/>')
        elements.append(f'<polyline points="{" ".join(path_points)}" fill="none" stroke="{color}" stroke-width="3"/>')
        elements.append(_svg_text(780, 105 + index * 22, group, size=11))
        risk_summary = ", ".join(
            f"{row[time_field]}: {row.get('at_risk', '')}"
            for row in ordered[:: max(1, len(ordered) // 4)]
        )
        elements.append(_svg_text(100, 525 + index * 24, f"Number at risk — {group}: {risk_summary}", size=10))
    elements.extend([_svg_text(420, 600, f"Time ({spec.unit})", anchor="middle"), "</svg>"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements), encoding="utf-8", newline="\n")
    return output


def _render_balance_svg(
    spec: ScientificFigureSpec, binding: FigureDataBinding, output: Path
) -> Path:
    covariate = spec.y_encoding or "covariate"
    before_field = spec.x_encoding or "before_smd"
    after_field = spec.point_estimate_field or "after_smd"
    values = [
        _number(row[field])
        for row in binding.rows
        for field in (before_field, after_field)
    ] + [-0.1, 0.0, 0.1]
    sx = _scale(values, 200, 760)
    height = 120 + 48 * len(binding.rows)
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" viewBox="0 0 900 {height}">',
        f'<rect width="900" height="{height}" fill="white"/>',
        _svg_text(450, 28, spec.caption, size=13, anchor="middle"),
        f'<line x1="{sx(0):.1f}" y1="50" x2="{sx(0):.1f}" y2="{height-35}" stroke="#051d25"/>',
    ]
    for threshold in (-0.1, 0.1):
        elements.append(f'<line x1="{sx(threshold):.1f}" y1="50" x2="{sx(threshold):.1f}" y2="{height-35}" stroke="#70a1a9" stroke-dasharray="4 4"/>')
    for index, row in enumerate(binding.rows):
        y = 75 + index * 48
        before = _number(row[before_field])
        after = _number(row[after_field])
        elements.extend(
            [
                _svg_text(185, y + 4, str(row[covariate]), anchor="end"),
                f'<line x1="{sx(before):.1f}" y1="{y}" x2="{sx(after):.1f}" y2="{y}" stroke="#a9c7ce" stroke-width="2"/>',
                f'<circle cx="{sx(before):.1f}" cy="{y}" r="5" fill="#70a1a9"/>',
                f'<circle cx="{sx(after):.1f}" cy="{y}" r="5" fill="#0d454e"/>',
            ]
        )
    elements.extend([_svg_text(470, height - 10, "Standardized mean difference", anchor="middle"), "</svg>"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements), encoding="utf-8", newline="\n")
    return output


def _render_table_svg(
    spec: ScientificFigureSpec, binding: FigureDataBinding, output: Path
) -> Path:
    columns = binding.data_columns
    width = 1100
    row_height = 34
    height = 80 + row_height * (len(binding.rows) + 1)
    col_width = (width - 40) / len(columns)
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        _svg_text(width / 2, 25, spec.caption, size=13, anchor="middle"),
    ]
    for column_index, column in enumerate(columns):
        x = 20 + column_index * col_width
        elements.append(f'<rect x="{x:.1f}" y="45" width="{col_width:.1f}" height="{row_height}" fill="#a9c7ce"/>')
        elements.append(_svg_text(x + 6, 67, column.replace("_", " "), size=10))
    for row_index, row in enumerate(binding.rows):
        y = 45 + (row_index + 1) * row_height
        for column_index, column in enumerate(columns):
            x = 20 + column_index * col_width
            elements.append(f'<rect x="{x:.1f}" y="{y}" width="{col_width:.1f}" height="{row_height}" fill="white" stroke="#70a1a9"/>')
            elements.append(_svg_text(x + 6, y + 22, str(row[column]), size=10))
    elements.append("</svg>")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements), encoding="utf-8", newline="\n")
    return output


DEFAULT_FIGURE_RENDERERS = FigureRendererRegistry()
DEFAULT_FIGURE_RENDERERS.register("builtin_interval_svg", "2.0.0", _render_interval_svg)
DEFAULT_FIGURE_RENDERERS.register("builtin_line_svg", "2.0.0", _render_line_svg)
DEFAULT_FIGURE_RENDERERS.register("builtin_table_svg", "2.0.0", _render_table_svg)
DEFAULT_FIGURE_RENDERERS.register("builtin_scatter_svg", "2.0.0", _render_scatter_svg)
DEFAULT_FIGURE_RENDERERS.register("builtin_histogram_svg", "2.0.0", _render_histogram_svg)
DEFAULT_FIGURE_RENDERERS.register("builtin_kaplan_meier_svg", "2.0.0", _render_kaplan_meier_svg)
DEFAULT_FIGURE_RENDERERS.register("builtin_balance_svg", "2.0.0", _render_balance_svg)


class FigureVisualValidator:
    version = "scientific-figure-visual-validator-v2"

    @classmethod
    def validate(
        cls, spec: ScientificFigureSpec, output: str | Path
    ) -> FigureVisualValidationReport:
        path = Path(output).resolve()
        checks = {
            "file_present": path.is_file(),
            "file_nonempty": path.is_file() and path.stat().st_size > 100,
            "format_readable": False,
            "reader_facing_text": True,
            "interval_marks_present": True,
        }
        text = ""
        if path.is_file() and path.suffix.casefold() == ".svg":
            text = path.read_text(encoding="utf-8", errors="replace")
            checks["format_readable"] = "<svg" in text and "</svg>" in text
            checks["reader_facing_text"] = _INTERNAL_TOKEN.search(text) is None
            if spec.lower_interval_field and spec.upper_interval_field:
                checks["interval_marks_present"] = "<line" in text and "<circle" in text
        elif path.is_file() and path.suffix.casefold() == ".pdf":
            try:
                from pypdf import PdfReader

                checks["format_readable"] = len(PdfReader(str(path)).pages) == 1
            except Exception:
                checks["format_readable"] = False
        elif path.is_file() and path.suffix.casefold() == ".png":
            try:
                from PIL import Image

                with Image.open(path) as image:
                    checks["format_readable"] = (
                        image.width >= 300 and image.height >= 150
                    )
            except Exception:
                checks["format_readable"] = False
        issues = [
            FigureBlockingIssue(
                issue_id=f"figure-block-{spec.figure_id.removeprefix('fig-')}-visual-{name.replace('_', '-')}",
                figure_id=spec.figure_id,
                code=name.upper(),
                message=f"rendered figure failed visual check: {name}",
                required_action="rerender from the accepted Figure Spec and inspect the vector output",
            )
            for name, passed in checks.items()
            if not passed
        ]
        return FigureVisualValidationReport(
            figure_id=spec.figure_id,
            passed=not issues,
            output_path=str(path),
            output_sha256=sha256_file(path) if path.is_file() else None,
            checks=checks,
            issues=issues,
        )


def render_and_accept_figure(
    spec: ScientificFigureSpec,
    binding: FigureDataBinding,
    output_dir: str | Path,
    *,
    registry: FigureRendererRegistry = DEFAULT_FIGURE_RENDERERS,
    contract_direction: Literal["increase", "decrease", "two_sided"] | None = None,
) -> FigureAcceptanceReport:
    semantic = FigureSemanticValidator.validate(
        spec, binding, contract_direction=contract_direction
    )
    output_root = Path(output_dir).resolve()
    spec_path = output_root / f"{spec.figure_id}.spec.json"
    data_path = output_root / f"{spec.figure_id}.data.json"
    output_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(spec_path, spec)
    write_json_atomic(data_path, binding)
    visual_reports: list[FigureVisualValidationReport] = []
    output_hashes: dict[str, str] = {}
    if semantic.passed:
        version, renderer = registry.resolve(spec.renderer)
        if version != spec.renderer_version:
            raise ValueError("frozen renderer version does not match registry")
        svg_output = renderer(
            spec, binding, output_root / f"{spec.figure_id}.svg"
        )
        generated: dict[str, Path] = {"svg": svg_output}
        requested = set(spec.output_formats)
        if requested & {"pdf", "png"}:
            try:
                import fitz
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise RuntimeError(
                    "PDF/PNG scientific figure export requires the pinned "
                    "publication extra (PyMuPDF)"
                ) from exc
            svg_document = fitz.open("svg", svg_output.read_bytes())
            pdf_bytes = svg_document.convert_to_pdf()
            if "pdf" in requested:
                pdf_path = output_root / f"{spec.figure_id}.pdf"
                pdf_path.write_bytes(pdf_bytes)
                generated["pdf"] = pdf_path
            if "png" in requested:
                pdf_document = fitz.open("pdf", pdf_bytes)
                pixmap = pdf_document.load_page(0).get_pixmap(
                    matrix=fitz.Matrix(2, 2), alpha=False
                )
                png_path = output_root / f"{spec.figure_id}.png"
                pixmap.save(str(png_path))
                generated["png"] = png_path
        for output_format in spec.output_formats:
            output = generated[output_format]
            visual = FigureVisualValidator.validate(spec, output)
            visual_reports.append(visual)
            if visual.output_sha256:
                output_hashes[output.name] = visual.output_sha256
    report = FigureAcceptanceReport(
        figure_id=spec.figure_id,
        spec_sha256=sha256_file(spec_path),
        data_sha256=sha256_file(data_path),
        renderer=spec.renderer,
        renderer_version=spec.renderer_version,
        output_hashes=output_hashes,
        semantic_validation=semantic,
        visual_validation=visual_reports,
        accepted=semantic.passed
        and bool(visual_reports)
        and all(item.passed for item in visual_reports),
    )
    write_json_atomic(output_root / f"{spec.figure_id}.acceptance.json", report)
    return report


def _req(
    figure_type: FigureType,
    fields: list[str],
    caption: list[str],
    *,
    fallback: list[FigureType] | None = None,
    forbidden: list[str] | None = None,
    rules: list[str] | None = None,
) -> ProfileFigureRequirement:
    return ProfileFigureRequirement(
        figure_type=figure_type,
        required_fields=fields,
        minimum_caption_content=caption,
        allowed_fallback=fallback or [],
        forbidden_chart_types=forbidden or [],
        semantic_rules=rules or [],
    )


PROFILE_FIGURE_ADAPTERS: dict[str, ProfileFigureAdapter] = {
    "independent_group_comparison_v1": ProfileFigureAdapter(
        profile_id="independent_group_comparison_v1",
        natural_language_name="independent-group comparison",
        requirements=[
            _req("arm_distribution", ["arm", "value"], ["arm", "analyzed denominator"]),
            _req("effect_interval", ["effect", "ci_lower", "ci_upper"], ["effect", "full interval", "unit"], forbidden=["generic_ci_bar"]),
            _req("sample_flow", ["arm", "eligible", "analyzed"], ["eligible", "excluded", "analyzed"]),
        ],
    ),
    "factorial_experiment_v1": ProfileFigureAdapter(
        profile_id="factorial_experiment_v1",
        natural_language_name="two-by-two factorial experiment",
        requirements=[
            _req("factorial_interaction", ["factor_a", "factor_b", "cell_mean", "ci_lower", "ci_upper"], ["cell means", "uncertainty", "interaction"], forbidden=["six_bar_effect_endpoint"]),
            _req("factorial_effect_forest", ["effect_name", "effect", "ci_lower", "ci_upper"], ["main effects", "interaction", "full intervals"]),
        ],
    ),
    "longitudinal_repeated_measures_v1": ProfileFigureAdapter(
        profile_id="longitudinal_repeated_measures_v1",
        natural_language_name="longitudinal repeated-measures study",
        requirements=[
            _req("longitudinal_trajectory", ["time", "arm", "mean", "ci_lower", "ci_upper"], ["time", "arm", "trajectory", "uncertainty"], forbidden=["effect_endpoint_only"]),
            _req("slope_distribution", ["subject", "arm", "slope"], ["subject slopes", "arm", "denominator"]),
            _req("effect_interval", ["effect", "ci_lower", "ci_upper"], ["treatment-minus-control slope", "full interval"]),
        ],
    ),
    "survival_analysis_v1": ProfileFigureAdapter(
        profile_id="survival_analysis_v1",
        natural_language_name="time-to-event study",
        requirements=[
            _req("kaplan_meier", ["time", "arm", "survival", "at_risk", "censored"], ["number at risk", "censoring", "follow-up horizon"], forbidden=["ordinary_bar_chart"]),
            _req("effect_interval", ["rmst_effect", "ci_lower", "ci_upper"], ["RMST difference", "full interval", "horizon"]),
        ],
    ),
    "causal_inference_v1": ProfileFigureAdapter(
        profile_id="causal_inference_v1",
        natural_language_name="causal observational analysis",
        requirements=[
            _req("propensity_overlap", ["exposure", "propensity"], ["overlap", "exposure groups"]),
            _req("covariate_balance", ["covariate", "before_smd", "after_smd"], ["before adjustment", "after adjustment", "balance threshold"]),
            _req("effect_interval", ["ate", "ci_lower", "ci_upper"], ["adjusted ATE", "full interval", "qualification"]),
        ],
    ),
    "noninferiority_equivalence_v1": ProfileFigureAdapter(
        profile_id="noninferiority_equivalence_v1",
        natural_language_name="noninferiority or equivalence experiment",
        requirements=[
            _req("equivalence_interval", ["effect", "ci_lower", "ci_upper", "lower_margin", "upper_margin"], ["full interval", "both margins", "zero reference", "decision"], forbidden=["generic_ci_bar"]),
        ],
    ),
    "bayesian_inference_v1": ProfileFigureAdapter(
        profile_id="bayesian_inference_v1",
        natural_language_name="Bayesian analysis",
        requirements=[
            _req("posterior_density", ["posterior_draw", "rope_lower", "rope_upper"], ["credible interval", "ROPE", "zero", "posterior probability"], forbidden=["confidence_interval_label"]),
        ],
    ),
    "online_ab_test_v1": ProfileFigureAdapter(
        profile_id="online_ab_test_v1",
        natural_language_name="online randomized A/B test",
        requirements=[
            _req("online_effect_forest", ["outcome", "role", "risk_difference", "ci_lower", "ci_upper"], ["primary outcome", "guardrail", "full intervals"]),
            _req("sample_flow", ["arm", "allocated", "exposed", "admitted", "analyzed"], ["allocation", "exposure", "admission", "analyzed", "SRM status"]),
        ],
    ),
    "open_generation_human_rating_v1": ProfileFigureAdapter(
        profile_id="open_generation_human_rating_v1",
        natural_language_name="open-ended generation with blinded human ratings",
        requirements=[
            _req("paired_item_difference", ["item", "difference", "ci_lower", "ci_upper"], ["paired items", "full interval", "item denominator"]),
            _req("rater_agreement", ["rater", "agreement"], ["rater agreement", "rater denominator", "panel completeness"]),
            _req("arm_distribution", ["arm", "value"], ["arm means", "item", "rater"]),
        ],
    ),
    "multiplicity_control_v1": ProfileFigureAdapter(
        profile_id="multiplicity_control_v1",
        natural_language_name="multi-outcome confirmatory analysis with multiplicity control",
        requirements=[
            _req("multi_outcome_forest", ["outcome", "role", "effect", "ci_lower", "ci_upper"], ["confirmatory family", "outcome roles", "full intervals"], forbidden=["generic_ci_bar"]),
            _req("adjusted_p_value_table", ["outcome", "role", "raw_p_value", "adjusted_p_value", "decision"], ["raw p-value", "adjusted p-value", "decision"]),
        ],
    ),
}


def profile_figure_adapter(profile_id: str) -> ProfileFigureAdapter:
    try:
        return PROFILE_FIGURE_ADAPTERS[profile_id]
    except KeyError as exc:
        raise ValueError(f"no Stage 4 Figure Adapter for Profile {profile_id}") from exc


def validate_profile_figure_set(
    profile_id: str,
    specs: list[ScientificFigureSpec],
) -> list[FigureBlockingIssue]:
    adapter = profile_figure_adapter(profile_id)
    available = {item.figure_type: item for item in specs if item.profile_id == profile_id}
    issues: list[FigureBlockingIssue] = []
    for requirement in adapter.requirements:
        spec = available.get(requirement.figure_type)
        if spec is None and not any(item in available for item in requirement.allowed_fallback):
            issues.append(
                FigureBlockingIssue(
                    issue_id=f"figure-block-profile-{profile_id.replace('_', '-')}-{requirement.figure_type.replace('_', '-')}",
                    figure_id=f"fig-required-{requirement.figure_type.replace('_', '-')}",
                    code="PROFILE_REQUIRED_FIGURE_MISSING",
                    message=(
                        f"{adapter.natural_language_name} requires "
                        f"{requirement.figure_type}"
                    ),
                    required_action="build the required figure from formal Profile output fields",
                )
            )
            continue
        if spec is None:
            continue
        missing = sorted(set(requirement.required_fields) - set(spec.data_columns))
        if missing:
            issues.append(
                FigureBlockingIssue(
                    issue_id=f"figure-block-{spec.figure_id.removeprefix('fig-')}-profile-fields",
                    figure_id=spec.figure_id,
                    code="PROFILE_REQUIRED_FIELDS_MISSING",
                    message="required Profile figure fields are missing: " + ", ".join(missing),
                    required_action="extend the formal FigureData artifact; do not infer fields from prose",
                )
            )
        caption = spec.caption.casefold()
        absent_caption = [item for item in requirement.minimum_caption_content if item.casefold() not in caption]
        if absent_caption:
            issues.append(
                FigureBlockingIssue(
                    issue_id=f"figure-block-{spec.figure_id.removeprefix('fig-')}-caption",
                    figure_id=spec.figure_id,
                    code="PROFILE_CAPTION_INCOMPLETE",
                    message="caption omits Profile-required content: " + ", ".join(absent_caption),
                    required_action="bind the required interpretation fields in the caption",
                )
            )
    return issues


__all__ = [
    "DEFAULT_FIGURE_RENDERERS",
    "FigureAcceptanceReport",
    "FigureBlockingIssue",
    "FigureDataBinding",
    "FigureRendererRegistry",
    "FigureSemanticValidationReport",
    "FigureSemanticValidator",
    "FigureVisualValidationReport",
    "FigureVisualValidator",
    "PROFILE_FIGURE_ADAPTERS",
    "ProfileFigureAdapter",
    "ProfileFigureRequirement",
    "ScientificFigureIntent",
    "ScientificFigureSpec",
    "profile_figure_adapter",
    "render_and_accept_figure",
    "validate_profile_figure_set",
]
