"""Certified paired open-generation comparison with blinded human ratings."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, variance
from typing import Any

from ..math_utils import student_t_cdf, student_t_ppf
from ..schemas import (
    AnalysisPlan,
    ClaimEnvelope,
    CompletionIssue,
    OutcomeEvaluation,
    RepairClass,
    StudyDesignCompletionPatch,
    StudyDesignEvaluation,
    StudyDesignMaturity,
)
from ..sdk import StudyDesignDescriptor


def _payload(contract: Any) -> dict[str, Any]:
    value = getattr(contract, "study_design_spec", None)
    if value is None and isinstance(contract, dict):
        value = contract.get("study_design_spec")
    return dict(value or {})


def _as_plan(contract: Any) -> AnalysisPlan:
    data = _payload(contract)
    data.setdefault("study_design_id", "open_generation_human_rating_v1")
    data.setdefault("study_design_version", "1")
    return AnalysisPlan.model_validate(data)


def _icc_2_1(matrix: list[list[float]]) -> float:
    """Two-way random-effects, absolute-agreement, single-rating ICC."""

    n = len(matrix)
    k = len(matrix[0]) if matrix else 0
    if n < 2 or k < 2 or any(len(row) != k for row in matrix):
        raise ValueError("ICC(2,1) requires a complete targets-by-raters matrix")
    row_means = [mean(row) for row in matrix]
    column_means = [mean(matrix[i][j] for i in range(n)) for j in range(k)]
    grand = mean(row_means)
    ms_rows = k * sum((value - grand) ** 2 for value in row_means) / (n - 1)
    ms_columns = n * sum((value - grand) ** 2 for value in column_means) / (k - 1)
    residual = sum(
        (matrix[i][j] - row_means[i] - column_means[j] + grand) ** 2
        for i in range(n)
        for j in range(k)
    )
    ms_error = residual / ((n - 1) * (k - 1))
    denominator = (
        ms_rows + (k - 1) * ms_error + (k / n) * (ms_columns - ms_error)
    )
    if denominator == 0:
        return 1.0 if ms_error == 0 else 0.0
    return (ms_rows - ms_error) / denominator


class OpenGenerationHumanRating:
    descriptor = StudyDesignDescriptor(
        design_id="open_generation_human_rating_v1",
        version="1",
        title="Open generation with blinded human ratings",
        summary=(
            "Compares two frozen outputs per independent item using a blinded, "
            "balanced rater panel, prompt-level paired inference, and registered "
            "inter-rater reliability."
        ),
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
        known_limits=(
            "two response procedures and one continuous ordinal-style rating outcome",
            "complete balanced panel with the same raters for every output",
            "paired prompt-level mean-rating inference only",
            "no preference-ranking, free-form qualitative coding, or adaptive adjudication",
        ),
    )

    def qualify(self, contract: Any, resources: Any | None = None) -> dict[str, Any]:
        violations = self.validate_contract(contract)
        return {"qualified": not violations, "violations": violations}

    def complete_contract(
        self, task_brief: Any, draft_contract: Any, resources: Any | None = None
    ) -> StudyDesignCompletionPatch:
        data = _payload(draft_contract)
        issues: list[CompletionIssue] = []
        required = {
            "unit_structure": "Freeze prompt, output, rating, and independent analysis units.",
            "arms": "Define both frozen response procedures.",
            "outcomes": "Freeze the human-rating construct and scale.",
            "estimands": "Bind the paired prompt-level contrast.",
            "human_rating": "Freeze rubric, rater panel, blindness, ordering, reliability, and adjudication.",
            "decision_rules": "Freeze the confirmatory decision before ratings are revealed.",
        }
        for field, expected in required.items():
            if not data.get(field):
                issues.append(
                    CompletionIssue(
                        issue_id=f"human-rating-{field}-missing",
                        field_path=f"study_design_spec.{field}",
                        severity="error",
                        repair_class=RepairClass.OWNER_SCIENTIFIC_DECISION,
                        expected=expected,
                        observed=data.get(field),
                        provenance="open_generation_human_rating_v1 schema",
                        owner_approval_required=True,
                    )
                )
        return StudyDesignCompletionPatch(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            issues=issues,
        )

    def validate_contract(self, contract: Any) -> list[str]:
        try:
            plan = _as_plan(contract)
        except Exception as exc:
            return [f"invalid human-rating analysis plan: {exc}"]
        violations: list[str] = []
        spec = plan.human_rating
        if spec is None:
            return ["open generation requires the frozen human-rating extension"]
        if len(plan.arms) != 2 or {item.role for item in plan.arms} != {
            "control",
            "treatment",
        }:
            violations.append("human-rating v1 requires one control and one treatment procedure")
        if set(spec.condition_label_map) != {item.arm_id for item in plan.arms}:
            violations.append("blind condition map must name both registered procedures")
        if plan.unit_structure.analysis_unit != plan.unit_structure.independent_unit:
            violations.append("prompt item must be the independent analysis unit")
        if not plan.unit_structure.repeated_measure_unit:
            violations.append("paired outputs and ratings must be bound to a repeated-measure item")
        primary = [item for item in plan.outcomes if item.role == "primary"]
        if len(primary) != 1 or primary[0].kind != "continuous":
            violations.append("human-rating v1 requires one continuous primary rating outcome")
        if len(plan.estimands) != 1 or plan.estimands[0].effect_measure != "paired_mean_difference":
            violations.append("human-rating v1 requires one paired prompt-level mean difference")
        if set(plan.estimator_plan) != {item.outcome_id for item in plan.outcomes}:
            violations.append("every human-rating outcome requires one estimator")
        elif any(
            item.estimator_id != "paired_item_mean_difference_v1"
            for item in plan.estimator_plan.values()
        ):
            violations.append("human-rating v1 requires the paired item-mean estimator")
        if plan.missingness.policy != "fail_if_any":
            violations.append("human-rating v1 fails closed on incomplete rating panels")
        if plan.multiplicity.hypothesis_ids != [primary[0].outcome_id] if primary else True:
            violations.append("the single human-rating outcome must be the confirmatory family")
        return list(dict.fromkeys(violations))

    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan:
        violations = self.validate_contract(contract)
        if violations:
            raise ValueError("; ".join(violations))
        return _as_plan(contract)

    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]:
        spec = plan.human_rating
        if spec is None:
            return ["human-rating extension is absent"]
        outcome = plan.outcomes[0]
        required = {
            spec.item_id_field,
            spec.output_id_field,
            spec.output_hash_field,
            spec.rater_id_field,
            spec.blind_label_field,
            spec.presentation_order_field,
            "arm",
            outcome.field,
        }
        errors: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        output_meta: dict[str, tuple[str, str, str]] = {}
        panel_by_output: dict[str, set[str]] = defaultdict(set)
        outputs_by_item: dict[str, dict[str, str]] = defaultdict(dict)
        order_by_item_rater: dict[tuple[str, str], set[int]] = defaultdict(set)
        arms = {item.arm_id for item in plan.arms}
        panel = set(spec.rater_panel_ids)
        for index, row in enumerate(rows):
            missing = sorted(
                field for field in required if field not in row or row[field] in {None, ""}
            )
            if missing:
                errors.append(f"row {index} is missing human-rating fields: {', '.join(missing)}")
                continue
            item_id = str(row[spec.item_id_field])
            output_id = str(row[spec.output_id_field])
            output_hash = str(row[spec.output_hash_field])
            rater_id = str(row[spec.rater_id_field])
            blind_label = str(row[spec.blind_label_field])
            arm = str(row["arm"])
            key = (item_id, output_id, rater_id)
            if key in seen:
                errors.append("a rater cannot score the same frozen output twice")
            seen.add(key)
            if arm not in arms:
                errors.append(f"row {index} uses an unregistered response procedure")
            if rater_id not in panel:
                errors.append(f"row {index} uses a rater outside the frozen panel")
            if blind_label != spec.condition_label_map.get(arm):
                errors.append(f"row {index} violates the frozen blind-label ledger")
            if len(output_hash) != 64 or any(ch not in "0123456789abcdef" for ch in output_hash):
                errors.append(f"row {index} has an invalid frozen output hash")
            rating = float(row[outcome.field])
            if not spec.rating_scale_min <= rating <= spec.rating_scale_max:
                errors.append(f"row {index} rating falls outside the frozen rubric scale")
            meta = (item_id, arm, output_hash)
            if output_id in output_meta and output_meta[output_id] != meta:
                errors.append("a frozen output identifier changed item, arm, or hash")
            output_meta[output_id] = meta
            panel_by_output[output_id].add(rater_id)
            if arm in outputs_by_item[item_id] and outputs_by_item[item_id][arm] != output_id:
                errors.append("each item may contain only one frozen output per procedure")
            outputs_by_item[item_id][arm] = output_id
            try:
                order = int(row[spec.presentation_order_field])
            except (TypeError, ValueError):
                errors.append(f"row {index} has an invalid presentation order")
            else:
                order_by_item_rater[(item_id, rater_id)].add(order)
        if any(set(value) != arms for value in outputs_by_item.values()):
            errors.append("every prompt item requires one frozen output from each procedure")
        if any(value != panel for value in panel_by_output.values()):
            errors.append("every frozen output requires the complete registered rater panel")
        if any(value != {1, 2} for value in order_by_item_rater.values()):
            errors.append("each rater must receive both item outputs in a complete randomized order")
        return list(dict.fromkeys(errors))

    def evaluate(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> StudyDesignEvaluation:
        errors = self.validate_realized_data(plan, rows)
        if errors:
            return StudyDesignEvaluation(
                study_design_id=self.descriptor.design_id,
                study_design_version=self.descriptor.version,
                eligible=False,
                qualification_checks={"realized_data_valid": False},
                outcomes=[],
                primary_decision="unverifiable",
                limitations=errors,
            )
        spec = plan.human_rating
        assert spec is not None
        outcome = plan.outcomes[0]
        ratings: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        output_ratings: dict[str, dict[str, float]] = defaultdict(dict)
        output_order: list[str] = []
        output_seen: set[str] = set()
        for row in rows:
            item_id = str(row[spec.item_id_field])
            arm = str(row["arm"])
            rater_id = str(row[spec.rater_id_field])
            output_id = str(row[spec.output_id_field])
            score = float(row[outcome.field])
            ratings[(item_id, arm)][rater_id] = score
            output_ratings[output_id][rater_id] = score
            if output_id not in output_seen:
                output_seen.add(output_id)
                output_order.append(output_id)
        arm_means: dict[str, list[float]] = defaultdict(list)
        paired_differences: list[float] = []
        item_ids = sorted({item_id for item_id, _ in ratings})
        for item_id in item_ids:
            control = mean(ratings[(item_id, "control")].values())
            treatment = mean(ratings[(item_id, "treatment")].values())
            arm_means["control"].append(control)
            arm_means["treatment"].append(treatment)
            paired_differences.append(treatment - control)
        effect = mean(paired_differences)
        if len(paired_differences) > 1:
            difference_variance = variance(paired_differences)
            standard_error = math.sqrt(difference_variance / len(paired_differences))
            degrees = len(paired_differences) - 1
        else:
            standard_error, degrees = 0.0, 0
        confidence = plan.inference_plan[outcome.outcome_id].confidence_level
        critical = student_t_ppf(0.5 + confidence / 2, degrees) if degrees else 0.0
        interval = (
            effect - critical * standard_error,
            effect + critical * standard_error,
        )
        p_value = (
            2 * (1 - student_t_cdf(abs(effect / standard_error), degrees))
            if standard_error > 0 and degrees
            else (0.0 if effect else 1.0)
        )
        matrix = [
            [output_ratings[output_id][rater] for rater in spec.rater_panel_ids]
            for output_id in output_order
        ]
        reliability = _icc_2_1(matrix)
        result = OutcomeEvaluation(
            outcome_id=outcome.outcome_id,
            kind="continuous",
            eligible=reliability >= spec.minimum_reliability,
            arm_statistics={
                arm_id: {
                    "n_items": len(values),
                    "mean_item_rating": mean(values),
                    "sd_item_rating": math.sqrt(variance(values)),
                }
                for arm_id, values in arm_means.items()
            },
            effect_measure="paired_mean_difference",
            effect=effect,
            standard_error=standard_error,
            confidence_interval=interval,
            p_value=p_value,
            raw_p_value=p_value,
            adjusted_p_value=p_value,
            degrees_of_freedom=float(degrees) if degrees else None,
            denominator=len(item_ids),
            decision="inconclusive",
            details={
                "rating_rows": len(rows),
                "frozen_outputs": len(output_order),
                "raters_per_output": spec.raters_per_output,
                "reliability_method": spec.reliability_method,
                "inter_rater_reliability": reliability,
                "minimum_reliability": spec.minimum_reliability,
                "aggregation": spec.aggregation,
            },
        )
        evaluation = StudyDesignEvaluation(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            eligible=result.eligible,
            qualification_checks={
                "realized_data_valid": True,
                "condition_blinded": True,
                "complete_balanced_panel": True,
                "prompt_is_independent_unit": True,
                "reliability_threshold_passed": result.eligible,
            },
            outcomes=[result],
            primary_decision="inconclusive" if result.eligible else "unverifiable",
            limitations=(
                []
                if result.eligible
                else ["inter-rater reliability did not reach the frozen minimum"]
            ),
        )
        return self.adjudicate(plan, evaluation)

    def adjudicate(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> StudyDesignEvaluation:
        if not evaluation.outcomes:
            return evaluation
        result = evaluation.outcomes[0]
        if not result.eligible or result.confidence_interval is None:
            result.decision = "unverifiable"
            evaluation.primary_decision = "unverifiable"
            evaluation.eligible = False
            return evaluation
        low, high = result.confidence_interval
        direction = plan.decision_rules[result.outcome_id].beneficial_direction
        result.decision = (
            "supported"
            if (direction == "higher" and low > 0)
            or (direction == "lower" and high < 0)
            else "inconclusive"
        )
        evaluation.primary_decision = result.decision
        return evaluation

    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope:
        spec = plan.human_rating
        assert spec is not None
        result = evaluation.outcomes[0] if evaluation.outcomes else None
        interval = result.confidence_interval if result else None
        effect = (
            f"The treatment-minus-control prompt-level mean rating difference was {result.effect:.4g}, "
            f"with an interval from {interval[0]:.4g} to {interval[1]:.4g}."
            if result and result.effect is not None and interval
            else "The registered paired human-rating contrast was not estimable."
        )
        return ClaimEnvelope(
            study_design_name="Blinded paired open-generation human-rating study",
            arm_definitions=[f"{arm.label}: {arm.definition}" for arm in plan.arms],
            allocation_verified_randomized=True,
            observation_unit=plan.unit_structure.observation_unit,
            analysis_unit=plan.unit_structure.analysis_unit,
            independent_unit=plan.unit_structure.independent_unit,
            primary_outcomes=[plan.outcomes[0].label],
            secondary_outcomes=[],
            denominator=result.denominator if result else 0,
            missing_count=result.missing_count if result else 0,
            excluded_count=result.excluded_count if result else 0,
            effect_estimates=[effect],
            uncertainty_method="paired t interval over independent prompt-level mean-rating differences",
            multiplicity_method="no correction; one confirmatory rating outcome",
            scientific_verdict=evaluation.primary_decision,
            permitted_claims=[
                "Report the paired difference in prompt-level mean blinded ratings.",
                "Report inter-rater reliability as a measurement qualification check.",
            ],
            prohibited_claims=[
                "Do not treat individual rating rows as independent experimental units.",
                "Do not reveal condition identities to raters or rewrite frozen ratings during adjudication.",
                "Do not interpret a usefulness rating as factual accuracy, safety, or preference outside the registered rubric.",
            ],
            generalization_boundary=plan.claim_boundary.get(
                "generalization",
                "Only the frozen prompts, outputs, rubric, and rater panel are represented.",
            ),
            reproduction_materials=list(
                plan.claim_boundary.get("reproduction_materials", [])
            ),
            design_details={
                "rubric_id": spec.rubric_id,
                "rating_scale": [spec.rating_scale_min, spec.rating_scale_max],
                "rater_panel_size": len(spec.rater_panel_ids),
                "blindness": spec.blindness,
                "presentation_randomization": spec.presentation_randomization,
                "reliability_method": spec.reliability_method,
                "minimum_reliability": spec.minimum_reliability,
            },
        )


__all__ = ["OpenGenerationHumanRating"]
