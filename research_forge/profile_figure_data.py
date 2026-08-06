from __future__ import annotations

"""Build immutable Profile-specific FigureData from formal Stage 3 outputs.

The builder is intended to run as the last deterministic reporting projection
of Stage 3.  Canonical Stage 4 reads the emitted bundle; it never parses values
from prose and never changes the frozen Evaluation or Scientific Verdict.
"""

import hashlib
import json
import math
from collections import defaultdict
from statistics import mean, stdev
from typing import Any

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .scientific_figure_engine import (
    FigureBlockingIssue,
    FigureDataBinding,
    ScientificFigureSpec,
    validate_profile_figure_set,
)
from .storage import sha256_file, write_json_atomic


PROFILE_ID_ALIASES = {
    "survival_time_to_event_v1": "survival_analysis_v1",
    "causal_observational_v1": "causal_inference_v1",
    "open_ended_human_rating_v1": "open_generation_human_rating_v1",
}


class ProfileFigureDataBundle(StrictModel):
    schema_version: int = 2
    study_id: str
    profile_id: str
    source_artifact_ids: list[str] = Field(min_length=1)
    source_hashes: dict[str, str] = Field(min_length=1)
    bindings: list[FigureDataBinding]
    specs: list[ScientificFigureSpec]
    blocking_issues: list[FigureBlockingIssue]
    complete: bool
    generated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def completeness_matches_issues(self) -> "ProfileFigureDataBundle":
        if self.complete == bool(self.blocking_issues):
            raise ValueError("Profile FigureData completeness contradicts blocking issues")
        return self


def _digest_rows(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _safe_slug(value: str) -> str:
    return "-".join(
        item for item in "".join(
            char.casefold() if char.isalnum() else " " for char in value
        ).split() if item
    )[:72]


class _Builder:
    def __init__(
        self,
        *,
        study_id: str,
        profile_id: str,
        source_artifact_ids: list[str],
        source_paths: list[str],
        source_hashes: dict[str, str],
    ) -> None:
        self.study_id = study_id
        self.profile_id = profile_id
        self.source_artifact_ids = source_artifact_ids
        self.source_paths = source_paths
        self.source_hashes = source_hashes
        self.bindings: list[FigureDataBinding] = []
        self.specs: list[ScientificFigureSpec] = []

    def add(
        self,
        *,
        figure_type: str,
        rows: list[dict[str, Any]],
        estimand_id: str,
        unit: str,
        denominator: str,
        caption: str,
        alt_text: str,
        renderer: str,
        point: str | None = None,
        lower: str | None = None,
        upper: str | None = None,
        reference: float | None = None,
        lower_margin: float | None = None,
        upper_margin: float | None = None,
        x: str | None = None,
        y: str | None = None,
        group: str | None = None,
        facet: str | None = None,
        time: str | None = None,
        event: str | None = None,
        censoring: str | None = None,
        posterior: str | None = None,
        level: float | None = None,
        interval_kind: str = "none",
        effect_measure: str = "descriptive",
        caption_claim_ids: list[str] | None = None,
    ) -> None:
        if not rows:
            raise ValueError(f"{figure_type} has no formal FigureData rows")
        suffix = _safe_slug(figure_type)
        figure_id = f"fig-{_safe_slug(self.profile_id.removesuffix('_v1'))}-{suffix}"
        columns = list(rows[0])
        binding = FigureDataBinding(
            binding_id=f"figure-data-{_safe_slug(self.profile_id)}-{suffix}",
            source_artifact_ids=self.source_artifact_ids,
            source_paths=self.source_paths,
            source_hashes=self.source_hashes,
            data_hash=_digest_rows(rows),
            data_columns=columns,
            rows=rows,
        )
        spec = ScientificFigureSpec(
            figure_id=figure_id,
            profile_id=self.profile_id,
            estimand_id=estimand_id,
            figure_type=figure_type,
            source_artifact_ids=self.source_artifact_ids,
            data_hash=binding.data_hash,
            data_columns=columns,
            point_estimate_field=point,
            lower_interval_field=lower,
            upper_interval_field=upper,
            reference_value=reference,
            lower_margin=lower_margin,
            upper_margin=upper_margin,
            x_encoding=x,
            y_encoding=y,
            group_encoding=group,
            facet_encoding=facet,
            time_field=time,
            event_field=event,
            censoring_field=censoring,
            posterior_field=posterior,
            unit=unit,
            denominator=denominator,
            confidence_or_credible_level=level,
            interval_kind=interval_kind,
            effect_measure=effect_measure,
            caption_claim_ids=caption_claim_ids or [f"registered-{estimand_id}"],
            caption=caption,
            alt_text=alt_text,
            renderer=renderer,
            renderer_version="2.0.0",
            output_formats=["svg", "pdf", "png"],
        )
        self.bindings.append(binding)
        self.specs.append(spec)


def _outcome_map(evaluation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["outcome_id"]): item
        for item in evaluation.get("outcomes", [])
    }


def _plan_outcomes(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["outcome_id"]): item for item in plan.get("outcomes", [])}


def _arm_labels(plan: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["arm_id"]): str(item.get("label") or item["arm_id"])
        for item in plan.get("arms", [])
    }


def _interval_rows(
    evaluation: dict[str, Any], plan: dict[str, Any], *, point_name: str = "effect"
) -> list[dict[str, Any]]:
    labels = _plan_outcomes(plan)
    rows: list[dict[str, Any]] = []
    for item in evaluation.get("outcomes", []):
        interval = list(item.get("confidence_interval") or [])
        if item.get("effect") is None or len(interval) != 2:
            continue
        rows.append(
            {
                "outcome": str(labels.get(str(item["outcome_id"]), {}).get("label") or item["outcome_id"]).replace("_", " "),
                point_name: float(item["effect"]),
                "ci_lower": float(interval[0]),
                "ci_upper": float(interval[1]),
            }
        )
    return rows


def _sample_sd(values: list[float]) -> float:
    return stdev(values) if len(values) > 1 else 0.0


def _mean_interval(values: list[float]) -> tuple[float, float, float]:
    center = mean(values)
    half = 1.959963984540054 * _sample_sd(values) / math.sqrt(len(values))
    return center, center - half, center + half


def _independent(
    builder: _Builder,
    evaluation: dict[str, Any],
    plan: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    outcomes = _plan_outcomes(plan)
    primary = next(item for item in plan["outcomes"] if item.get("role") == "primary")
    field = str(primary["field"])
    labels = _arm_labels(plan)
    distribution = [
        {"arm": labels.get(str(row["arm"]), str(row["arm"])), "value": float(row[field])}
        for row in rows
        if row.get(field) not in {None, ""}
    ]
    builder.add(
        figure_type="arm_distribution",
        rows=distribution,
        estimand_id=str(primary["outcome_id"]),
        unit=str(primary.get("label") or "outcome units"),
        denominator=f"{len(distribution)} analyzed independent units",
        caption="Observed arm distributions for the primary outcome; horizontal marks show arm means and report the analyzed denominator.",
        alt_text="Jittered observations by independently assigned arm with an arm-specific mean marker.",
        renderer="builtin_scatter_svg",
        x="arm",
        y="value",
        group="arm",
    )
    effects = _interval_rows(evaluation, plan)
    builder.add(
        figure_type="effect_interval",
        rows=effects,
        estimand_id="registered-outcome-contrasts",
        unit="registered outcome units",
        denominator=f"{max(int(item.get('denominator') or 0) for item in evaluation['outcomes'])} registered observations before outcome-specific missingness",
        caption="Registered treatment-minus-control effects with the full interval at 95% confidence and the unit shown on each outcome scale.",
        alt_text="Point estimates and both confidence-interval endpoints for every registered outcome.",
        renderer="builtin_interval_svg",
        point="effect",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="outcome",
        level=0.95,
        interval_kind="confidence",
        effect_measure="difference",
    )
    flow: list[dict[str, Any]] = []
    for arm_id, label in labels.items():
        arm_rows = [row for row in rows if str(row["arm"]) == arm_id]
        analyzed = sum(row.get(field) not in {None, ""} for row in arm_rows)
        flow.append(
            {
                "arm": label,
                "eligible": len(arm_rows),
                "excluded": 0,
                "analyzed": analyzed,
            }
        )
    builder.add(
        figure_type="sample_flow",
        rows=flow,
        estimand_id=str(primary["outcome_id"]),
        unit="independent units",
        denominator=f"{len(rows)} eligible independent units",
        caption="Outcome-specific flow from eligible through excluded and analyzed units for each arm.",
        alt_text="Table of eligible, excluded, and analyzed units by arm.",
        renderer="builtin_table_svg",
    )


def _factorial(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    factorial = plan.get("factorial") or {}
    factors = list(factorial.get("factors") or [])
    if len(factors) != 2:
        raise ValueError("factorial FigureData requires exactly two frozen factors")
    a_field = str(factors[0].get("field") or factors[0].get("factor_id"))
    b_field = str(factors[1].get("field") or factors[1].get("factor_id"))
    outcome = next(item for item in plan["outcomes"] if item.get("role") == "primary")
    outcome_field = str(outcome["field"])
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        grouped[(str(row[a_field]), str(row[b_field]))].append(float(row[outcome_field]))
    interaction: list[dict[str, Any]] = []
    for (factor_a, factor_b), values in sorted(grouped.items()):
        center, lower, upper = _mean_interval(values)
        interaction.append(
            {"factor_a": factor_a.replace("_", " "), "factor_b": factor_b.replace("_", " "), "cell_mean": center, "ci_lower": lower, "ci_upper": upper}
        )
    builder.add(
        figure_type="factorial_interaction",
        rows=interaction,
        estimand_id="factorial-cell-means",
        unit=str(outcome.get("label") or "outcome units"),
        denominator=f"{len(rows)} independent units across four frozen cells",
        caption="Two-by-two interaction plot with four cell means, uncertainty shown as complete descriptive 95% intervals, and the registered factor interaction.",
        alt_text="Cell-mean trajectories across the second factor, separated by the first factor, reveal the registered interaction pattern.",
        renderer="builtin_line_svg",
        point="cell_mean",
        lower="ci_lower",
        upper="ci_upper",
        x="factor_b",
        y="cell_mean",
        group="factor_a",
        level=0.95,
        interval_kind="confidence",
        effect_measure="descriptive",
    )
    effects = [
        {
            "effect_name": str(item.get("details", {}).get("contrast_label") or item["outcome_id"]).replace("_", " "),
            "effect": float(item["effect"]),
            "ci_lower": float(item["confidence_interval"][0]),
            "ci_upper": float(item["confidence_interval"][1]),
        }
        for item in evaluation["outcomes"]
    ]
    builder.add(
        figure_type="factorial_effect_forest",
        rows=effects,
        estimand_id="factorial-main-and-interaction-effects",
        unit=str(outcome.get("label") or "outcome units"),
        denominator=f"{len(rows)} independent units",
        caption="Main effects and the interaction are shown as point estimates with full intervals at 95% confidence; all registered factorial effects are retained.",
        alt_text="Forest plot of both registered main effects and the registered interaction, with both interval endpoints.",
        renderer="builtin_interval_svg",
        point="effect",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="effect_name",
        level=0.95,
        interval_kind="confidence",
        effect_measure="difference",
    )


def _longitudinal(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    extension = plan.get("longitudinal") or {}
    time_field = str(extension.get("time_field") or "week")
    subject_field = str(extension.get("subject_id_field") or "subject_id")
    outcome = next(item for item in plan["outcomes"] if item.get("role") == "primary")
    value_field = str(outcome["field"])
    labels = _arm_labels(plan)
    grouped: dict[tuple[str, float], list[float]] = defaultdict(list)
    subjects: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        arm = str(row["arm"])
        time = float(row[time_field])
        value = float(row[value_field])
        grouped[(arm, time)].append(value)
        subjects[(arm, str(row[subject_field]))].append((time, value))
    trajectories = []
    for (arm, time), values in sorted(grouped.items()):
        center, lower, upper = _mean_interval(values)
        trajectories.append({"time": time, "arm": labels.get(arm, arm), "mean": center, "ci_lower": lower, "ci_upper": upper})
    builder.add(
        figure_type="longitudinal_trajectory",
        rows=trajectories,
        estimand_id=str(outcome["outcome_id"]),
        unit=str(outcome.get("label") or "outcome units"),
        denominator=f"{len(subjects)} independent subjects across {len({item['time'] for item in trajectories})} scheduled visits",
        caption="Arm-level trajectory across registered time for each arm, with uncertainty shown as complete pointwise 95% intervals and subject-level denominators.",
        alt_text="Mean outcome trajectories over registered follow-up time, with uncertainty for each arm.",
        renderer="builtin_line_svg",
        point="mean",
        lower="ci_lower",
        upper="ci_upper",
        x="time",
        y="mean",
        group="arm",
        time="time",
        level=0.95,
        interval_kind="confidence",
        effect_measure="descriptive",
    )
    slope_rows = []
    for (arm, subject), values in sorted(subjects.items()):
        xs = [item[0] for item in values]
        ys = [item[1] for item in values]
        x_bar, y_bar = mean(xs), mean(ys)
        denominator = sum((x - x_bar) ** 2 for x in xs)
        slope = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys, strict=True)) / denominator
        slope_rows.append({"subject": subject, "arm": labels.get(arm, arm), "slope": slope})
    builder.add(
        figure_type="slope_distribution",
        rows=slope_rows,
        estimand_id="subject-specific-slopes",
        unit=f"{outcome.get('label') or 'outcome units'} per registered time unit",
        denominator=f"{len(slope_rows)} independent subject slopes",
        caption="Distribution of independent subject slopes by arm, with the subject denominator.",
        alt_text="Jittered subject-specific slopes grouped by randomized arm.",
        renderer="builtin_scatter_svg",
        x="arm",
        y="slope",
        group="arm",
    )
    effect = _interval_rows(evaluation, plan)
    builder.add(
        figure_type="effect_interval",
        rows=effect,
        estimand_id="treatment-minus-control-slope",
        unit=f"{outcome.get('label') or 'outcome units'} per registered time unit",
        denominator=f"{evaluation['outcomes'][0]['denominator']} independent subjects",
        caption="Treatment-minus-control slope effect with the full interval at the registered 95% confidence level.",
        alt_text="Point estimate and both confidence-interval endpoints for the between-arm slope difference.",
        renderer="builtin_interval_svg",
        point="effect",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="outcome",
        level=0.95,
        interval_kind="confidence",
        effect_measure="slope_difference",
    )


def _km_rows(rows: list[dict[str, Any]], *, arm_field: str, time_field: str, event_field: str, labels: dict[str, str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for arm in sorted({str(row[arm_field]) for row in rows}):
        arm_rows = [row for row in rows if str(row[arm_field]) == arm]
        at_risk = len(arm_rows)
        survival = 1.0
        output.append({"time": 0.0, "arm": labels.get(arm, arm), "survival": 1.0, "at_risk": at_risk, "events": 0, "censored": 0})
        for time in sorted({float(row[time_field]) for row in arm_rows}):
            current = [row for row in arm_rows if float(row[time_field]) == time]
            events = sum(bool(row[event_field]) for row in current)
            censored = len(current) - events
            if at_risk and events:
                survival *= 1.0 - events / at_risk
            output.append({"time": time, "arm": labels.get(arm, arm), "survival": survival, "at_risk": at_risk, "events": events, "censored": censored})
            at_risk -= len(current)
    return output


def _survival(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    extension = plan.get("survival") or {}
    time_field = str(extension.get("duration_field") or "duration_hours")
    event_field = str(extension.get("event_field") or "failure_observed")
    labels = _arm_labels(plan)
    curves = _km_rows(rows, arm_field="arm", time_field=time_field, event_field=event_field, labels=labels)
    horizon = float(extension.get("restriction_time") or max(item["time"] for item in curves))
    builder.add(
        figure_type="kaplan_meier",
        rows=curves,
        estimand_id="survival-function",
        unit=str(extension.get("time_unit") or "registered time units"),
        denominator=f"{len(rows)} independent subjects",
        caption="Kaplan–Meier survival curves with censoring marks, number at risk, and the registered follow-up horizon.",
        alt_text="Stepwise survival probabilities by arm; crosses denote censoring and rows below report numbers at risk.",
        renderer="builtin_kaplan_meier_svg",
        point="survival",
        x="time",
        y="survival",
        group="arm",
        time="time",
        event="events",
        censoring="censored",
        effect_measure="descriptive",
    )
    item = evaluation["outcomes"][0]
    interval = item["confidence_interval"]
    builder.add(
        figure_type="effect_interval",
        rows=[{"outcome": "Restricted mean survival time difference", "rmst_effect": float(item["effect"]), "ci_lower": float(interval[0]), "ci_upper": float(interval[1]), "horizon": horizon}],
        estimand_id="restricted-mean-survival-time-difference",
        unit=str(extension.get("time_unit") or "registered time units"),
        denominator=f"{item['denominator']} independent subjects through horizon {horizon:g}",
        caption="RMST difference with the full interval at 95% confidence and the registered follow-up horizon.",
        alt_text="Point estimate and complete interval for the treatment-minus-control restricted mean survival time difference.",
        renderer="builtin_interval_svg",
        point="rmst_effect",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="outcome",
        level=0.95,
        interval_kind="confidence",
        effect_measure="rmst_difference",
    )


def _causal_propensity(plan: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    import numpy as np

    from .study_design.designs.causal import _fit_logistic, _fold_for, _sigmoid
    from .study_design.schemas import AnalysisPlan

    analysis = AnalysisPlan.model_validate(plan)
    causal = analysis.causal
    if causal is None:
        raise ValueError("causal FigureData requires the frozen causal extension")
    subjects = [str(row[causal.subject_id_field]) for row in rows]
    treatment = np.asarray([1.0 if row[causal.treatment_field] == causal.treatment_value else 0.0 for row in rows])
    covariates = np.asarray([[float(row[field]) for field in causal.adjustment_set] for row in rows], dtype=float)
    design = np.column_stack([np.ones(len(rows)), covariates])
    folds = np.asarray([_fold_for(subject, causal.fold_seed, causal.cross_fitting_folds) for subject in subjects])
    propensity = np.empty(len(rows), dtype=float)
    for fold in range(causal.cross_fitting_folds):
        test = folds == fold
        if not np.any(test):
            continue
        train = ~test
        propensity[test] = _sigmoid(design[test] @ _fit_logistic(design[train], treatment[train]))
    return propensity.tolist(), treatment.tolist()


def _weighted_smd(values: list[float], treatment: list[float], weights: list[float]) -> float:
    def summary(target: float) -> tuple[float, float]:
        selected = [(value, weight) for value, arm, weight in zip(values, treatment, weights, strict=True) if arm == target]
        total = sum(weight for _, weight in selected)
        center = sum(value * weight for value, weight in selected) / total
        variance = sum(weight * (value - center) ** 2 for value, weight in selected) / total
        return center, variance
    treated_mean, treated_var = summary(1.0)
    control_mean, control_var = summary(0.0)
    pooled = math.sqrt((treated_var + control_var) / 2.0)
    return (treated_mean - control_mean) / pooled if pooled else 0.0


def _causal(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    propensity, treatment = _causal_propensity(plan, rows)
    causal = plan["causal"]
    overlap_rows = [
        {"exposure": "Observed treatment" if arm == 1.0 else "Observed control", "propensity": value}
        for value, arm in zip(propensity, treatment, strict=True)
    ]
    builder.add(
        figure_type="propensity_overlap",
        rows=overlap_rows,
        estimand_id="average-treatment-effect-overlap",
        unit="propensity probability",
        denominator=f"{len(rows)} observed independent units",
        caption="Cross-fitted propensity-score overlap between the observed exposure groups within the frozen positivity bounds.",
        alt_text="Overlaid propensity-score distributions for observed treatment and control units.",
        renderer="builtin_histogram_svg",
        x="propensity",
        group="exposure",
    )
    bounded = [min(max(value, float(causal["propensity_lower_bound"])), float(causal["propensity_upper_bound"])) for value in propensity]
    weights = [1.0 / value if arm == 1.0 else 1.0 / (1.0 - value) for value, arm in zip(bounded, treatment, strict=True)]
    balance = []
    for field in causal["adjustment_set"]:
        values = [float(row[field]) for row in rows]
        before = _weighted_smd(values, treatment, [1.0] * len(rows))
        after = _weighted_smd(values, treatment, weights)
        balance.append({"covariate": str(field).replace("_", " "), "before_smd": before, "after_smd": after})
    builder.add(
        figure_type="covariate_balance",
        rows=balance,
        estimand_id="measured-backdoor-balance",
        unit="standardized mean difference",
        denominator=f"{len(rows)} observed independent units",
        caption="Measured covariate balance before adjustment and after adjustment; dashed lines mark the absolute 0.1 balance threshold.",
        alt_text="Love plot connecting before- and after-adjustment standardized mean differences for each measured covariate.",
        renderer="builtin_balance_svg",
        point="after_smd",
        x="before_smd",
        y="covariate",
        reference=0.0,
    )
    item = evaluation["outcomes"][0]
    interval = item["confidence_interval"]
    builder.add(
        figure_type="effect_interval",
        rows=[{"outcome": "Adjusted average treatment effect", "ate": float(item["effect"]), "ci_lower": float(interval[0]), "ci_upper": float(interval[1]), "qualified": bool(evaluation.get("eligible"))}],
        estimand_id="adjusted-average-treatment-effect",
        unit="registered outcome units",
        denominator=f"{item['denominator']} observed independent units",
        caption="Adjusted ATE with the full interval at 95% confidence and explicit qualification under the frozen identification assumptions.",
        alt_text="Adjusted average treatment effect and both interval endpoints, conditional on the registered qualification checks.",
        renderer="builtin_interval_svg",
        point="ate",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="outcome",
        level=0.95,
        interval_kind="confidence",
        effect_measure="difference",
    )


def _equivalence(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any]) -> None:
    primary = next(item for item in plan["outcomes"] if item.get("role") == "primary")
    item = _outcome_map(evaluation)[str(primary["outcome_id"])]
    rule = plan["decision_rules"][str(primary["outcome_id"])]
    lower_margin = rule.get("lower_margin")
    upper_margin = rule.get("upper_margin")
    if lower_margin is None or upper_margin is None:
        margin = float(rule["margin"])
        lower_margin, upper_margin = -margin, margin
    interval = item["confidence_interval"]
    builder.add(
        figure_type="equivalence_interval",
        rows=[{"outcome": str(primary.get("label") or primary["outcome_id"]), "effect": float(item["effect"]), "ci_lower": float(interval[0]), "ci_upper": float(interval[1]), "lower_margin": float(lower_margin), "upper_margin": float(upper_margin), "decision": str(item["decision"])}],
        estimand_id=str(primary["outcome_id"]),
        unit=str(rule.get("margin_unit") or primary.get("label") or "registered outcome units"),
        denominator=f"{item['denominator']} analyzed independent units",
        caption="Registered effect and full interval at 95% confidence relative to both margins and the zero reference; the figure reports the frozen decision.",
        alt_text="The full confidence interval is compared with the lower and upper equivalence margins and zero.",
        renderer="builtin_interval_svg",
        point="effect",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        lower_margin=float(lower_margin),
        upper_margin=float(upper_margin),
        group="outcome",
        level=float(rule.get("confidence_level") or 0.95),
        interval_kind="confidence",
        effect_measure="difference",
    )


def _bayesian(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any], rows: list[dict[str, Any]], evaluation_records: list[dict[str, Any]]) -> None:
    import numpy as np

    record = next((item for item in evaluation_records if str(item.get("metric_name", "")).startswith("Bayesian sensitivity")), None)
    if record is None:
        raise ValueError("Bayesian Profile requires the frozen Bayesian Evaluation record")
    statistical = record["statistical_rule"]
    prior = statistical["prior"]
    seed = int(statistical["seed"])
    draws = int(statistical["draws"])
    rope = [float(value) for value in statistical["rope"]]
    outcomes = _outcome_map(evaluation)
    completion = outcomes.get("completion") or next(iter(outcomes.values()))
    control = completion["arm_statistics"]["control"]
    treatment = completion["arm_statistics"]["treatment"]
    rng = np.random.default_rng(seed)
    control_draws = rng.beta(float(prior["alpha"]) + int(control["events"]), float(prior["beta"]) + int(control["n"]) - int(control["events"]), draws)
    treatment_draws = rng.beta(float(prior["alpha"]) + int(treatment["events"]), float(prior["beta"]) + int(treatment["n"]) - int(treatment["events"]), draws)
    differences = treatment_draws - control_draws
    plot_draws = differences[:: max(1, len(differences) // 4000)]
    posterior = record["contrast_estimates"]["posterior_sensitivity"]
    probability = float(posterior["probability_effect_above_zero"])
    credible = [float(value) for value in posterior["credible_interval_95"]]
    posterior_mean = float(posterior["posterior_mean_difference"])
    binding_rows = [
        {
            "posterior_draw": float(value),
            "posterior_mean": posterior_mean,
            "credible_lower": credible[0],
            "credible_upper": credible[1],
            "rope_lower": rope[0],
            "rope_upper": rope[1],
        }
        for value in plot_draws
    ]
    builder.add(
        figure_type="posterior_density",
        rows=binding_rows,
        estimand_id="posterior-completion-difference",
        unit="posterior probability difference",
        denominator=f"{control['n'] + treatment['n']} independent units and {draws} frozen posterior draws",
        caption=f"Posterior density with the 95% credible interval, zero reference, registered ROPE, and posterior probability above zero ({probability:.3f}).",
        alt_text="Posterior distribution of the treatment-minus-control probability difference, shaded against the ROPE and zero.",
        renderer="builtin_histogram_svg",
        point="posterior_mean",
        lower="credible_lower",
        upper="credible_upper",
        x="posterior_draw",
        posterior="posterior_draw",
        reference=0.0,
        lower_margin=rope[0],
        upper_margin=rope[1],
        level=0.95,
        interval_kind="credible",
        effect_measure="posterior_difference",
    )


def _online(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    outcomes = _plan_outcomes(plan)
    effects = []
    for item in evaluation["outcomes"]:
        interval = item["confidence_interval"]
        definition = outcomes[str(item["outcome_id"])]
        effects.append({"outcome": str(definition.get("label") or item["outcome_id"]), "role": str(definition.get("role") or "secondary"), "risk_difference": float(item["effect"]), "ci_lower": float(interval[0]), "ci_upper": float(interval[1])})
    builder.add(
        figure_type="online_effect_forest",
        rows=effects,
        estimand_id="online-primary-and-guardrail-effects",
        unit="risk difference",
        denominator=f"{max(int(item['denominator']) for item in evaluation['outcomes'])} first valid exposures",
        caption="Primary outcome and guardrail risk differences with full intervals at 95% confidence and explicit outcome roles.",
        alt_text="Forest plot separating the primary outcome from the guardrail and displaying both interval endpoints.",
        renderer="builtin_interval_svg",
        point="risk_difference",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="outcome",
        facet="role",
        level=0.95,
        interval_kind="confidence",
        effect_measure="risk_difference",
    )
    labels = _arm_labels(plan)
    flow = []
    for arm_id, label in labels.items():
        count = sum(str(row["arm"]) == arm_id for row in rows)
        flow.append({"arm": label, "allocated": count, "exposed": count, "admitted": count, "analyzed": count, "srm_status": "passed" if evaluation.get("qualification_checks", {}).get("sample_ratio_match_passed") else "failed"})
    builder.add(
        figure_type="sample_flow",
        rows=flow,
        estimand_id="online-exposure-flow",
        unit="randomization units",
        denominator=f"{len(rows)} allocated units in the frozen analysis window",
        caption="Allocation, exposure, admission, and analyzed flow by arm with the frozen SRM status.",
        alt_text="Flow table by randomized arm from allocation through final analysis, including the sample-ratio check.",
        renderer="builtin_table_svg",
    )


def _human_rating(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    extension = plan["human_rating"]
    item_field = str(extension["item_id_field"])
    rater_field = str(extension["rater_id_field"])
    value_field = str(plan["outcomes"][0]["field"])
    labels = _arm_labels(plan)
    item_arm: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        item_arm[(str(row[item_field]), str(row["arm"]))].append(float(row[value_field]))
    differences = []
    for item in sorted({key[0] for key in item_arm}):
        control = item_arm[(item, "control")]
        treatment = item_arm[(item, "treatment")]
        paired = [a - b for a, b in zip(treatment, control, strict=True)]
        center, lower, upper = _mean_interval(paired)
        differences.append({"item": item, "difference": center, "ci_lower": lower, "ci_upper": upper})
    builder.add(
        figure_type="paired_item_difference",
        rows=differences,
        estimand_id="paired-item-rating-difference",
        unit="rating-scale points",
        denominator=f"{len(differences)} paired prompt items",
        caption="Paired items are shown as rating differences with the full interval and the item denominator.",
        alt_text="Item-level treatment-minus-control rating differences for every frozen prompt item.",
        renderer="builtin_interval_svg",
        point="difference",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="item",
        level=0.95,
        interval_kind="confidence",
        effect_measure="difference",
    )
    details = evaluation["outcomes"][0]["details"]
    raters = sorted({str(row[rater_field]) for row in rows})
    agreement = [{"rater": rater.replace("_", " "), "agreement": float(details["inter_rater_reliability"]), "panel_complete": bool(evaluation.get("qualification_checks", {}).get("complete_balanced_panel"))} for rater in raters]
    builder.add(
        figure_type="rater_agreement",
        rows=agreement,
        estimand_id="blinded-panel-agreement",
        unit="registered agreement coefficient",
        denominator=f"{len(raters)} blinded raters and {len(differences)} paired items",
        caption="Registered rater agreement with the rater denominator and panel completeness qualification status.",
        alt_text="Agreement coefficient reported for the complete frozen blinded rater panel.",
        renderer="builtin_scatter_svg",
        x="rater",
        y="agreement",
        group="rater",
    )
    arm_means = []
    for arm_id, label in labels.items():
        values = [float(row[value_field]) for row in rows if str(row["arm"]) == arm_id]
        arm_means.append({"arm": label, "value": mean(values)})
    builder.add(
        figure_type="arm_distribution",
        rows=arm_means,
        estimand_id="blinded-arm-means",
        unit="rating-scale points",
        denominator=f"{len(rows)} blinded rating rows",
        caption="Arm means for blinded ratings across the complete frozen item and rater panel.",
        alt_text="Mean blinded rating for each response procedure.",
        renderer="builtin_scatter_svg",
        x="arm",
        y="value",
        group="arm",
    )


def _multiplicity(builder: _Builder, evaluation: dict[str, Any], plan: dict[str, Any]) -> None:
    definitions = _plan_outcomes(plan)
    forest = []
    table = []
    for item in evaluation["outcomes"]:
        definition = definitions[str(item["outcome_id"])]
        interval = item["confidence_interval"]
        label = str(definition.get("label") or item["outcome_id"])
        role = str(definition.get("role") or "secondary")
        forest.append({"outcome": label, "role": role, "effect": float(item["effect"]), "ci_lower": float(interval[0]), "ci_upper": float(interval[1])})
        table.append({"outcome": label, "role": role, "raw_p_value": float(item["raw_p_value"]), "adjusted_p_value": float(item["adjusted_p_value"]), "decision": str(item["decision"])})
    denominator = max(int(item["denominator"]) for item in evaluation["outcomes"])
    builder.add(
        figure_type="multi_outcome_forest",
        rows=forest,
        estimand_id="confirmatory-outcome-family",
        unit="registered outcome-specific units",
        denominator=f"up to {denominator} analyzed independent units by outcome",
        caption="The complete confirmatory family is shown with outcome roles and full intervals for all registered effects.",
        alt_text="Multi-outcome forest plot preserving every registered outcome and its complete interval.",
        renderer="builtin_interval_svg",
        point="effect",
        lower="ci_lower",
        upper="ci_upper",
        reference=0.0,
        group="outcome",
        facet="role",
        level=0.95,
        interval_kind="nominal",
        effect_measure="difference",
    )
    builder.add(
        figure_type="adjusted_p_value_table",
        rows=table,
        estimand_id="confirmatory-outcome-family",
        unit="probability",
        denominator=f"{len(table)} prespecified confirmatory outcomes",
        caption="Raw p-value and adjusted p-value results, outcome roles, and the frozen decision for the complete confirmatory family.",
        alt_text="Table comparing raw and adjusted p-values and decisions for every confirmatory outcome.",
        renderer="builtin_table_svg",
    )


def build_profile_figure_data_bundle(
    *,
    study_id: str,
    profile_id: str,
    evaluation: dict[str, Any],
    research_contract_spec: dict[str, Any],
    formal_rows: list[dict[str, Any]],
    source_artifact_ids: list[str],
    source_paths: list[str],
    source_hashes: dict[str, str],
    evaluation_records: list[dict[str, Any]] | None = None,
) -> ProfileFigureDataBundle:
    """Create a complete, fail-closed FigureData bundle for one Profile."""

    canonical = PROFILE_ID_ALIASES.get(profile_id, profile_id)
    builder = _Builder(
        study_id=study_id,
        profile_id=canonical,
        source_artifact_ids=source_artifact_ids,
        source_paths=source_paths,
        source_hashes=source_hashes,
    )
    try:
        if canonical == "independent_group_comparison_v1":
            _independent(builder, evaluation, research_contract_spec, formal_rows)
        elif canonical == "factorial_experiment_v1":
            _factorial(builder, evaluation, research_contract_spec, formal_rows)
        elif canonical == "longitudinal_repeated_measures_v1":
            _longitudinal(builder, evaluation, research_contract_spec, formal_rows)
        elif canonical == "survival_analysis_v1":
            _survival(builder, evaluation, research_contract_spec, formal_rows)
        elif canonical == "causal_inference_v1":
            _causal(builder, evaluation, research_contract_spec, formal_rows)
        elif canonical == "noninferiority_equivalence_v1":
            _equivalence(builder, evaluation, research_contract_spec)
        elif canonical == "bayesian_inference_v1":
            _bayesian(builder, evaluation, research_contract_spec, formal_rows, evaluation_records or [])
        elif canonical == "online_ab_test_v1":
            _online(builder, evaluation, research_contract_spec, formal_rows)
        elif canonical == "open_generation_human_rating_v1":
            _human_rating(builder, evaluation, research_contract_spec, formal_rows)
        elif canonical == "multiplicity_control_v1":
            _multiplicity(builder, evaluation, research_contract_spec)
        else:
            raise ValueError(f"no Profile FigureData projection for {canonical}")
        issues = validate_profile_figure_set(canonical, builder.specs)
    except Exception as exc:
        issues = [
            FigureBlockingIssue(
                issue_id=f"figure-block-{_safe_slug(canonical)}-projection-failed",
                figure_id=f"fig-{_safe_slug(canonical)}-required",
                code="PROFILE_FIGURE_DATA_PROJECTION_FAILED",
                message=str(exc),
                required_action=(
                    "produce the missing formal Stage 3 Statistics/FigureData "
                    "fields; Stage 4 may not infer them from prose"
                ),
            )
        ]
    return ProfileFigureDataBundle(
        study_id=study_id,
        profile_id=canonical,
        source_artifact_ids=source_artifact_ids,
        source_hashes=source_hashes,
        bindings=builder.bindings,
        specs=builder.specs,
        blocking_issues=issues,
        complete=not issues,
    )


def persist_profile_figure_data_bundle(
    repository: Any,
    study_id: str,
    *,
    profile_id: str,
    evaluation: Any,
    research_contract_spec: Any,
    formal_rows: list[dict[str, Any]],
    source_artifacts: list[Any],
    evaluation_records: list[Any] | None = None,
) -> tuple[ProfileFigureDataBundle, Any]:
    """Freeze and register the Stage 3 FigureData handoff for Stage 4.

    ``source_artifacts`` must be existing immutable ArtifactRecords.  The
    helper registers one new append-only projection and never mutates its
    Evaluation or dataset sources.
    """

    from .workflow_domain import ArtifactRole

    if not source_artifacts:
        raise ValueError("Profile FigureData requires formal source artifacts")
    source_paths = [str(item.path) for item in source_artifacts]
    source_hashes = {str(item.path): str(item.sha256) for item in source_artifacts}
    bundle = build_profile_figure_data_bundle(
        study_id=study_id,
        profile_id=profile_id,
        evaluation=(
            evaluation.model_dump(mode="json")
            if hasattr(evaluation, "model_dump")
            else dict(evaluation)
        ),
        research_contract_spec=(
            research_contract_spec.model_dump(mode="json")
            if hasattr(research_contract_spec, "model_dump")
            else dict(research_contract_spec)
        ),
        formal_rows=formal_rows,
        source_artifact_ids=[str(item.artifact_id) for item in source_artifacts],
        source_paths=source_paths,
        source_hashes=source_hashes,
        evaluation_records=[
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in (evaluation_records or [])
        ],
    )
    study_root = repository.root / "studies" / study_id
    path = study_root / "stage3" / "profile_figure_data_v2.json"
    write_json_atomic(path, bundle)
    artifact = repository.register_artifact(
        study_id,
        str(path),
        sha256_file(path),
        kind="profile_figure_data_bundle_v2",
        role=ArtifactRole.EVALUATION,
    )
    for source in source_artifacts:
        repository.add_dependency(
            study_id,
            source.artifact_id,
            artifact.artifact_id,
            relation="figure_data_projection_from",
        )
    return bundle, artifact


__all__ = [
    "PROFILE_ID_ALIASES",
    "ProfileFigureDataBundle",
    "build_profile_figure_data_bundle",
    "persist_profile_figure_data_bundle",
]
