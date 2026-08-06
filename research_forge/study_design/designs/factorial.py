"""Certified two-by-two independent-unit factorial Study Design."""

from __future__ import annotations

import itertools
import math
from collections import Counter
from statistics import mean, stdev
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
    data.setdefault("study_design_id", "factorial_experiment_v1")
    data.setdefault("study_design_version", "1")
    return AnalysisPlan.model_validate(data)


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*matrix, strict=True)]


def _matmul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a * b for a, b in zip(row, column, strict=True)) for column in _transpose(right)]
        for row in left
    ]


def _inverse(matrix: list[list[float]]) -> list[list[float]]:
    size = len(matrix)
    augmented = [
        [*map(float, row), *[1.0 if i == j else 0.0 for j in range(size)]]
        for i, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("factorial design matrix is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(
                    augmented[row], augmented[column], strict=True
                )
            ]
    return [row[size:] for row in augmented]


def _quadratic(row: list[float], matrix: list[list[float]]) -> float:
    return sum(
        row[i] * matrix[i][j] * row[j]
        for i in range(len(row))
        for j in range(len(row))
    )


def _fit_hc2(
    matrix: list[list[float]], values: list[float]
) -> tuple[list[float], list[list[float]], int]:
    transpose = _transpose(matrix)
    inverse = _inverse(_matmul(transpose, matrix))
    beta = [
        row[0]
        for row in _matmul(
            _matmul(inverse, transpose), [[value] for value in values]
        )
    ]
    residuals = [
        value - sum(coefficient * feature for coefficient, feature in zip(beta, row, strict=True))
        for row, value in zip(matrix, values, strict=True)
    ]
    dimension = len(beta)
    meat = [[0.0 for _ in range(dimension)] for _ in range(dimension)]
    for row, residual in zip(matrix, residuals, strict=True):
        leverage = _quadratic(row, inverse)
        if leverage >= 1.0 - 1e-12:
            raise ValueError("factorial HC2 covariance has unit leverage")
        weight = residual * residual / (1.0 - leverage)
        for i in range(dimension):
            for j in range(dimension):
                meat[i][j] += weight * row[i] * row[j]
    covariance = _matmul(_matmul(inverse, meat), inverse)
    return beta, covariance, len(values) - dimension


class FactorialExperiment:
    descriptor = StudyDesignDescriptor(
        design_id="factorial_experiment_v1",
        version="1",
        title="Two-factor experiment",
        summary=(
            "Estimates two prespecified main effects and their interaction in "
            "a randomized 2x2 design with independent experimental units."
        ),
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
        known_limits=(
            "exactly one 2x2 design with two factors and two levels each",
            "one continuous outcome and independent units only",
            "fixed-effect OLS with HC2 robust covariance; no blocks or clusters",
        ),
    )

    def qualify(self, contract: Any, resources: Any | None = None) -> dict[str, Any]:
        violations = self.validate_contract(contract)
        return {"qualified": not violations, "violations": violations}

    def complete_contract(
        self, task_brief: Any, draft_contract: Any, resources: Any | None = None
    ) -> StudyDesignCompletionPatch:
        data = _payload(draft_contract)
        required = {
            "unit_structure": "Define independent observation, assignment, analysis, and variance units.",
            "allocation": "Freeze randomized assignment evidence for all four cells.",
            "arms": "Define the four factorial cells.",
            "outcomes": "Register one continuous outcome.",
            "factorial": "Freeze two factors, their levels, cells, main effects, and interaction.",
            "missingness": "Freeze the missing-data and denominator rule.",
            "decision_rules": "Freeze a decision rule for every confirmatory contrast.",
        }
        issues = [
            CompletionIssue(
                issue_id=f"factorial-{field}-missing",
                field_path=f"study_design_spec.{field}",
                severity="error",
                repair_class=RepairClass.OWNER_SCIENTIFIC_DECISION,
                expected=expected,
                observed=data.get(field),
                provenance="factorial_experiment_v1 schema",
                owner_approval_required=True,
            )
            for field, expected in required.items()
            if not data.get(field)
        ]
        return StudyDesignCompletionPatch(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            issues=issues,
        )

    def validate_contract(self, contract: Any) -> list[str]:
        try:
            plan = _as_plan(contract)
        except Exception as exc:
            return [f"invalid factorial analysis plan: {exc}"]
        violations: list[str] = []
        factorial = plan.factorial
        if factorial is None:
            return ["factorial experiment requires a frozen factorial extension"]
        if plan.unit_structure.cluster_unit:
            violations.append("clustered factorial experiments require another Study Design")
        if plan.unit_structure.repeated_measure_unit:
            violations.append("repeated-measure factorial experiments require another Study Design")
        if len({
            plan.unit_structure.assignment_unit,
            plan.unit_structure.analysis_unit,
            plan.unit_structure.variance_unit,
            plan.unit_structure.independent_unit,
        }) != 1:
            violations.append("assignment, analysis, variance, and independent units must coincide")
        if plan.allocation.mechanism != "randomized":
            violations.append("factorial_experiment_v1 requires verified randomized allocation")
        if len(plan.arms) != 4 or {arm.role for arm in plan.arms} != {"factorial_cell"}:
            violations.append("a 2x2 factorial experiment requires four factorial-cell arms")
        arm_ids = [arm.arm_id for arm in plan.arms]
        if len(set(arm_ids)) != len(arm_ids):
            violations.append("factorial cell arm identifiers must be unique")

        factors = factorial.factors
        factor_ids = [factor.factor_id for factor in factors]
        factor_fields = [factor.field for factor in factors]
        if len(set(factor_ids)) != 2 or len(set(factor_fields)) != 2:
            violations.append("factor identifiers and data fields must be unique")
        expected_combinations = set(itertools.product(*(factor.levels for factor in factors)))
        observed_combinations: set[tuple[str, ...]] = set()
        cell_arm_ids: list[str] = []
        for cell in factorial.cells:
            cell_arm_ids.append(cell.arm_id)
            if set(cell.levels) != set(factor_ids):
                violations.append("every factorial cell must bind every registered factor exactly once")
                continue
            combination = tuple(cell.levels.get(factor_id, "") for factor_id in factor_ids)
            observed_combinations.add(combination)
            for factor, level in zip(factors, combination, strict=True):
                if level not in factor.levels:
                    violations.append(f"factorial cell uses an unknown level for {factor.label}")
        if observed_combinations != expected_combinations:
            violations.append("factorial cells must cover the complete 2x2 Cartesian product")
        if set(cell_arm_ids) != set(arm_ids) or len(set(cell_arm_ids)) != 4:
            violations.append("factorial cells must map one-to-one to the four registered arms")

        if len(plan.outcomes) != 1 or plan.outcomes[0].kind != "continuous":
            violations.append("factorial_experiment_v1 currently supports exactly one continuous outcome")
        outcome_ids = {outcome.outcome_id for outcome in plan.outcomes}
        if set(plan.estimator_plan) != outcome_ids or any(
            estimator.estimator_id != "factorial_ols_hc2_v1"
            for estimator in plan.estimator_plan.values()
        ):
            violations.append("the continuous factorial outcome requires factorial OLS with HC2 covariance")
        contrast_ids = [contrast.contrast_id for contrast in factorial.contrasts]
        if len(set(contrast_ids)) != len(contrast_ids):
            violations.append("factorial contrast identifiers must be unique")
        if len([contrast for contrast in factorial.contrasts if contrast.role == "primary"]) != 1:
            violations.append("exactly one factorial contrast must be primary")
        for contrast in factorial.contrasts:
            if contrast.outcome_id not in outcome_ids:
                violations.append("every factorial contrast must reference the registered outcome")
            if not set(contrast.factor_ids) <= set(factor_ids):
                violations.append("factorial contrast references an unknown factor")
        if set(plan.decision_rules) != set(contrast_ids):
            violations.append("every factorial contrast requires exactly one decision rule")
        for contrast in factorial.contrasts:
            rule = plan.decision_rules.get(contrast.contrast_id)
            if rule and rule.beneficial_direction != contrast.beneficial_direction:
                violations.append(f"decision direction contradicts contrast {contrast.label}")
            if rule and rule.mode != "superiority":
                violations.append("factorial_experiment_v1 currently supports superiority contrasts only")
        confirmatory = {
            contrast.contrast_id
            for contrast in factorial.contrasts
            if contrast.role in {"primary", "secondary"}
        }
        if set(plan.multiplicity.hypothesis_ids) != confirmatory:
            violations.append("multiplicity family must equal the confirmatory factorial contrasts")
        if len(confirmatory) > 1 and plan.multiplicity.method == "no_correction":
            violations.append("multiple factorial contrasts require multiplicity control")
        return list(dict.fromkeys(violations))

    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan:
        violations = self.validate_contract(contract)
        if violations:
            raise ValueError("; ".join(violations))
        return _as_plan(contract)

    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]:
        factorial = plan.factorial
        assert factorial is not None
        errors: list[str] = []
        required = {"subject_id", "arm", *(factor.field for factor in factorial.factors)}
        counts: Counter[str] = Counter()
        cell_lookup = {cell.arm_id: cell for cell in factorial.cells}
        observed_per_cell: Counter[str] = Counter()
        outcome = plan.outcomes[0]
        for index, row in enumerate(rows):
            missing = sorted(required - set(row))
            if missing:
                errors.append(f"row {index} is missing {', '.join(missing)}")
                continue
            subject = str(row["subject_id"])
            counts[subject] += 1
            arm = str(row["arm"])
            cell = cell_lookup.get(arm)
            if cell is None:
                errors.append(f"row {index} uses an unregistered factorial cell")
                continue
            for factor in factorial.factors:
                if str(row[factor.field]) != cell.levels[factor.factor_id]:
                    errors.append(f"row {index} factor levels do not match its frozen cell")
            if row.get(outcome.field) not in {None, ""}:
                observed_per_cell[arm] += 1
        if any(count > 1 for count in counts.values()):
            errors.append("repeated observations require a repeated-measures Study Design")
        for arm_id in cell_lookup:
            if observed_per_cell[arm_id] < factorial.minimum_observed_per_cell:
                errors.append(f"factorial cell {arm_id} has too few observed outcomes")
        if not any(outcome.field in row for row in rows):
            errors.append(f"outcome field is absent: {outcome.label}")
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
        factorial = plan.factorial
        assert factorial is not None
        outcome = plan.outcomes[0]
        factor_a, factor_b = factorial.factors
        code_a = {factor_a.levels[0]: -0.5, factor_a.levels[1]: 0.5}
        code_b = {factor_b.levels[0]: -0.5, factor_b.levels[1]: 0.5}
        matrix: list[list[float]] = []
        values: list[float] = []
        cell_values: dict[str, list[float]] = {cell.arm_id: [] for cell in factorial.cells}
        missing = 0
        for row in rows:
            value = row.get(outcome.field)
            if value in {None, ""}:
                missing += 1
                continue
            a = code_a[str(row[factor_a.field])]
            b = code_b[str(row[factor_b.field])]
            matrix.append([1.0, a, b, a * b])
            numeric = float(value)
            values.append(numeric)
            cell_values[str(row["arm"])].append(numeric)
        try:
            beta, covariance, degrees_of_freedom = _fit_hc2(matrix, values)
        except ValueError as exc:
            return StudyDesignEvaluation(
                study_design_id=self.descriptor.design_id,
                study_design_version=self.descriptor.version,
                eligible=False,
                qualification_checks={"realized_data_valid": True, "estimable": False},
                outcomes=[],
                primary_decision="unverifiable",
                limitations=[str(exc)],
            )
        coefficient_index = {
            (factor_a.factor_id,): 1,
            (factor_b.factor_id,): 2,
            (factor_a.factor_id, factor_b.factor_id): 3,
            (factor_b.factor_id, factor_a.factor_id): 3,
        }
        arm_statistics = {
            arm_id: {
                "n": len(observations),
                "mean": mean(observations),
                "sd": stdev(observations),
            }
            for arm_id, observations in cell_values.items()
        }
        evaluations: list[OutcomeEvaluation] = []
        for contrast in factorial.contrasts:
            index = coefficient_index[tuple(contrast.factor_ids)]
            effect = beta[index]
            standard_error = math.sqrt(max(0.0, covariance[index][index]))
            confidence = plan.inference_plan[outcome.outcome_id].confidence_level
            critical = student_t_ppf(0.5 + confidence / 2.0, degrees_of_freedom)
            interval = (
                effect - critical * standard_error,
                effect + critical * standard_error,
            )
            statistic = effect / standard_error if standard_error else math.inf
            p_value = 2 * (1 - student_t_cdf(abs(statistic), degrees_of_freedom))
            evaluations.append(
                OutcomeEvaluation(
                    outcome_id=contrast.contrast_id,
                    kind="continuous",
                    eligible=True,
                    arm_statistics=arm_statistics,
                    effect_measure=contrast.kind,
                    effect=effect,
                    standard_error=standard_error,
                    confidence_interval=interval,
                    p_value=p_value,
                    raw_p_value=p_value,
                    degrees_of_freedom=float(degrees_of_freedom),
                    missing_count=missing,
                    denominator=len(values),
                    details={
                        "contrast_label": contrast.label,
                        "factor_ids": contrast.factor_ids,
                        "outcome_id": outcome.outcome_id,
                        "estimator": "effect-coded OLS with HC2 robust covariance",
                    },
                )
            )
        from ..inference.multiplicity import adjust_p_values

        adjusted = adjust_p_values(
            {
                item.outcome_id: item.raw_p_value
                for item in evaluations
                if item.outcome_id in plan.multiplicity.hypothesis_ids
                and item.raw_p_value is not None
            },
            plan.multiplicity.method,
        )
        for item in evaluations:
            item.adjusted_p_value = adjusted.get(item.outcome_id)
        result = StudyDesignEvaluation(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            eligible=True,
            qualification_checks={
                "realized_data_valid": True,
                "complete_factorial_cells": True,
                "independent_units": True,
                "denominators_explicit": True,
            },
            outcomes=evaluations,
            primary_decision="inconclusive",
        )
        return self.adjudicate(plan, result)

    def adjudicate(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> StudyDesignEvaluation:
        factorial = plan.factorial
        assert factorial is not None
        contrast_by_id = {contrast.contrast_id: contrast for contrast in factorial.contrasts}
        alpha = plan.multiplicity.alpha_or_q
        for result in evaluation.outcomes:
            contrast = contrast_by_id[result.outcome_id]
            low, high = result.confidence_interval or (math.nan, math.nan)
            direction_supported = (
                low > 0
                if contrast.beneficial_direction == "higher"
                else high < 0
            )
            multiplicity_supported = (
                result.adjusted_p_value is None
                or result.adjusted_p_value <= alpha
            )
            result.decision = (
                "supported"
                if direction_supported and multiplicity_supported
                else "inconclusive"
            )
        primary = [
            item
            for item in evaluation.outcomes
            if contrast_by_id[item.outcome_id].role == "primary"
        ]
        evaluation.primary_decision = primary[0].decision if primary else "unverifiable"
        evaluation.eligible = bool(primary and primary[0].eligible)
        return evaluation

    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope:
        factorial = plan.factorial
        assert factorial is not None
        contrast_by_id = {contrast.contrast_id: contrast for contrast in factorial.contrasts}
        effects = []
        for item in evaluation.outcomes:
            contrast = contrast_by_id[item.outcome_id]
            interval = item.confidence_interval
            effects.append(
                f"For {contrast.label}, the estimated {contrast.kind.replace('_', ' ')} "
                f"was {item.effect:.4g}"
                + (
                    f" with an interval from {interval[0]:.4g} to {interval[1]:.4g}."
                    if interval else "."
                )
            )
        primary_ids = {
            contrast.outcome_id
            for contrast in factorial.contrasts
            if contrast.role == "primary"
        }
        return ClaimEnvelope(
            study_design_name="Randomized two-by-two factorial experiment",
            arm_definitions=[f"{arm.label}: {arm.definition}" for arm in plan.arms],
            allocation_verified_randomized=True,
            observation_unit=plan.unit_structure.observation_unit,
            analysis_unit=plan.unit_structure.analysis_unit,
            independent_unit=plan.unit_structure.independent_unit,
            primary_outcomes=[item.label for item in plan.outcomes if item.outcome_id in primary_ids],
            secondary_outcomes=[item.label for item in plan.outcomes if item.outcome_id not in primary_ids],
            denominator=max((item.denominator for item in evaluation.outcomes), default=0),
            missing_count=max((item.missing_count for item in evaluation.outcomes), default=0),
            excluded_count=max((item.excluded_count for item in evaluation.outcomes), default=0),
            effect_estimates=effects,
            uncertainty_method="Effect-coded factorial OLS with HC2 robust covariance and t intervals",
            multiplicity_method=plan.multiplicity.method.replace("_", " "),
            scientific_verdict=evaluation.primary_decision,
            permitted_claims=[
                "Report the registered main effects and interaction with their uncertainty.",
                "Interpret a main effect as averaged over the other factor in the frozen 2x2 design.",
            ],
            prohibited_claims=[
                "Do not reduce an interaction to four unrelated pairwise comparisons.",
                "Do not generalize beyond the frozen randomized factorial population.",
            ],
            generalization_boundary=plan.claim_boundary.get(
                "generalization", "Only the frozen randomized 2x2 population is represented."
            ),
            reproduction_materials=list(plan.claim_boundary.get("reproduction_materials", [])),
            design_details={
                "factors": [
                    {"label": factor.label, "levels": factor.levels}
                    for factor in factorial.factors
                ],
                "contrasts": [
                    {"label": contrast.label, "kind": contrast.kind}
                    for contrast in factorial.contrasts
                ],
            },
        )


__all__ = ["FactorialExperiment"]
