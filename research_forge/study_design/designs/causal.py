"""Certified observational point-treatment causal Study Design."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from statistics import mean, stdev
from typing import Any

import numpy as np

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
    data.setdefault("study_design_id", "causal_inference_v1")
    data.setdefault("study_design_version", "1")
    return AnalysisPlan.model_validate(data)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _fit_logistic(x: np.ndarray, treatment: np.ndarray) -> np.ndarray:
    """Deterministic ridge-stabilized IRLS for the propensity nuisance model."""

    coefficients = np.zeros(x.shape[1], dtype=float)
    ridge = np.eye(x.shape[1], dtype=float) * 1e-8
    ridge[0, 0] = 0.0
    for _ in range(100):
        probability = _sigmoid(x @ coefficients)
        weights = np.clip(probability * (1.0 - probability), 1e-8, None)
        working = x @ coefficients + (treatment - probability) / weights
        weighted_x = x * np.sqrt(weights)[:, None]
        weighted_y = working * np.sqrt(weights)
        updated = np.linalg.solve(weighted_x.T @ weighted_x + ridge, weighted_x.T @ weighted_y)
        if np.max(np.abs(updated - coefficients)) < 1e-10:
            coefficients = updated
            break
        coefficients = updated
    return coefficients


def _fit_linear(x: np.ndarray, outcome: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(x, outcome, rcond=None)[0]


def _fold_for(subject_id: str, seed: int, folds: int) -> int:
    digest = hashlib.sha256(f"{seed}:{subject_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % folds


class CausalInference:
    descriptor = StudyDesignDescriptor(
        design_id="causal_inference_v1",
        version="1",
        title="Observational causal inference with backdoor adjustment",
        summary=(
            "Estimates a frozen average treatment effect for one binary "
            "point treatment and continuous outcome using a declared DAG, "
            "backdoor adjustment set, overlap checks, and cross-fitted AIPW."
        ),
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
        known_limits=(
            "one observational binary point treatment and one continuous outcome",
            "owner-frozen measured backdoor adjustment set only",
            "cross-fitted linear outcome and logistic propensity nuisance models",
            "no IV, DiD, RDD, mediation, time-varying treatment, interference, or hidden-confounding identification",
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
            "unit_structure": "Freeze one independent subject row as the analysis unit.",
            "allocation": "Declare observational allocation and its provenance.",
            "arms": "Define the observed control and treatment conditions.",
            "outcomes": "Register one continuous primary outcome.",
            "estimands": "Freeze the population average treatment effect.",
            "causal": "Freeze the DAG, adjustment set, identification assumptions, overlap bounds, and cross-fitting plan.",
            "decision_rules": "Freeze the causal-effect decision rule before formal estimation.",
        }
        for field, expected in required.items():
            if not data.get(field):
                issues.append(
                    CompletionIssue(
                        issue_id=f"causal-inference-{field}-missing",
                        field_path=f"study_design_spec.{field}",
                        severity="error",
                        repair_class=RepairClass.OWNER_SCIENTIFIC_DECISION,
                        expected=expected,
                        observed=data.get(field),
                        provenance="causal_inference_v1 schema",
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
            return [f"invalid causal analysis plan: {exc}"]
        violations: list[str] = []
        if plan.causal is None:
            return ["causal inference requires the frozen causal design extension"]
        causal = plan.causal
        if plan.allocation.mechanism not in {"observational", "nonrandomized"}:
            violations.append("causal profile v1 requires observational or nonrandomized allocation")
        if len(plan.arms) != 2 or {arm.role for arm in plan.arms} != {"control", "treatment"}:
            violations.append("causal profile requires one control and one treatment arm")
        if plan.unit_structure.analysis_unit != plan.unit_structure.independent_unit:
            violations.append("causal profile v1 requires independent analysis units")
        if plan.unit_structure.cluster_unit or plan.unit_structure.repeated_measure_unit:
            violations.append("clustered or repeated causal data require another profile")
        primary = [item for item in plan.outcomes if item.role == "primary"]
        if len(plan.outcomes) != 1 or len(primary) != 1 or primary[0].kind != "continuous":
            violations.append("causal profile v1 requires exactly one continuous primary outcome")
        elif primary[0].field != causal.outcome_field:
            violations.append("causal outcome field must match the registered primary outcome")
        if len(plan.estimands) != 1:
            violations.append("causal profile v1 requires exactly one ATE estimand")
        else:
            estimand = plan.estimands[0]
            if estimand.effect_measure != "average_treatment_effect":
                violations.append("causal profile v1 requires the average treatment effect measure")
            if estimand.analysis_population != "all_observed":
                violations.append("causal ATE requires the frozen all-observed target population")
        outcome_id = primary[0].outcome_id if primary else ""
        estimator = plan.estimator_plan.get(outcome_id)
        if not estimator or estimator.estimator_id != "cross_fitted_aipw_ate_v1":
            violations.append("causal profile v1 requires cross-fitted AIPW estimation")
        if set(plan.estimator_plan) != ({outcome_id} if outcome_id else set()):
            violations.append("causal profile requires exactly one estimator binding")
        if set(plan.decision_rules) != ({outcome_id} if outcome_id else set()):
            violations.append("causal profile requires exactly one decision rule")
        if plan.missingness.policy != "fail_if_any":
            violations.append("causal profile v1 fails closed when required causal fields are missing")
        if plan.multiplicity.hypothesis_ids != [outcome_id]:
            violations.append("causal confirmatory family must contain only the primary outcome")
        return list(dict.fromkeys(violations))

    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan:
        violations = self.validate_contract(contract)
        if violations:
            raise ValueError("; ".join(violations))
        return _as_plan(contract)

    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]:
        causal = plan.causal
        if causal is None:
            return ["causal design extension is absent"]
        errors: list[str] = []
        required = {
            causal.subject_id_field,
            causal.treatment_field,
            causal.outcome_field,
            *causal.adjustment_set,
        }
        subject_counts: Counter[str] = Counter()
        arm_counts: Counter[str] = Counter()
        for index, row in enumerate(rows):
            missing = sorted(field for field in required if field not in row or row[field] in {None, ""})
            if missing:
                errors.append(f"row {index} is missing required causal fields: {', '.join(missing)}")
                continue
            subject_counts[str(row[causal.subject_id_field])] += 1
            treatment = row[causal.treatment_field]
            if treatment == causal.treatment_value:
                arm_counts["treatment"] += 1
            elif treatment == causal.control_value:
                arm_counts["control"] += 1
            else:
                errors.append(f"row {index} uses an unregistered treatment value")
            for field in [causal.outcome_field, *causal.adjustment_set]:
                try:
                    value = float(row[field])
                except (TypeError, ValueError):
                    errors.append(f"row {index} has a nonnumeric causal field: {field}")
                    continue
                if not math.isfinite(value):
                    errors.append(f"row {index} has a nonfinite causal field: {field}")
        if any(count > 1 for count in subject_counts.values()):
            errors.append("each causal subject must appear exactly once")
        if arm_counts["control"] < causal.minimum_subjects_per_arm:
            errors.append("control arm is below the frozen minimum subject count")
        if arm_counts["treatment"] < causal.minimum_subjects_per_arm:
            errors.append("treatment arm is below the frozen minimum subject count")
        return list(dict.fromkeys(errors))

    def _estimate(self, plan: AnalysisPlan, rows: list[dict[str, Any]]) -> OutcomeEvaluation:
        causal = plan.causal
        assert causal is not None
        outcome = plan.outcomes[0]
        subjects = [str(row[causal.subject_id_field]) for row in rows]
        treatment = np.asarray(
            [1.0 if row[causal.treatment_field] == causal.treatment_value else 0.0 for row in rows]
        )
        observed = np.asarray([float(row[causal.outcome_field]) for row in rows])
        covariates = np.asarray(
            [[float(row[field]) for field in causal.adjustment_set] for row in rows],
            dtype=float,
        )
        design = np.column_stack([np.ones(len(rows)), covariates])
        folds = np.asarray(
            [_fold_for(subject, causal.fold_seed, causal.cross_fitting_folds) for subject in subjects]
        )
        propensity = np.empty(len(rows), dtype=float)
        predicted_control = np.empty(len(rows), dtype=float)
        predicted_treatment = np.empty(len(rows), dtype=float)
        for fold in range(causal.cross_fitting_folds):
            test = folds == fold
            if not np.any(test):
                continue
            train = ~test
            if len(set(treatment[train])) != 2:
                return OutcomeEvaluation(
                    outcome_id=outcome.outcome_id,
                    kind="continuous",
                    eligible=False,
                    arm_statistics={},
                    effect_measure="average_treatment_effect",
                    denominator=len(rows),
                    decision="unverifiable",
                    details={"reason": "a cross-fitting training fold lacks one treatment condition"},
                )
            propensity[test] = _sigmoid(design[test] @ _fit_logistic(design[train], treatment[train]))
            for arm, destination in ((0.0, predicted_control), (1.0, predicted_treatment)):
                arm_train = train & (treatment == arm)
                if np.sum(arm_train) <= design.shape[1]:
                    return OutcomeEvaluation(
                        outcome_id=outcome.outcome_id,
                        kind="continuous",
                        eligible=False,
                        arm_statistics={},
                        effect_measure="average_treatment_effect",
                        denominator=len(rows),
                        decision="unverifiable",
                        details={"reason": "a nuisance outcome model is underidentified"},
                    )
                destination[test] = design[test] @ _fit_linear(design[arm_train], observed[arm_train])

        inside_overlap = (propensity >= causal.propensity_lower_bound) & (
            propensity <= causal.propensity_upper_bound
        )
        overlap_fraction = float(np.mean(inside_overlap))
        if overlap_fraction < causal.minimum_overlap_fraction:
            return OutcomeEvaluation(
                outcome_id=outcome.outcome_id,
                kind="continuous",
                eligible=False,
                arm_statistics={},
                effect_measure="average_treatment_effect",
                denominator=len(rows),
                decision="unverifiable",
                details={
                    "reason": "the frozen empirical overlap requirement was not met",
                    "overlap_fraction": overlap_fraction,
                },
            )
        bounded_propensity = np.clip(
            propensity, causal.propensity_lower_bound, causal.propensity_upper_bound
        )
        pseudo_outcome = (
            predicted_treatment
            - predicted_control
            + treatment * (observed - predicted_treatment) / bounded_propensity
            - (1.0 - treatment) * (observed - predicted_control) / (1.0 - bounded_propensity)
        )
        effect = float(np.mean(pseudo_outcome))
        se = float(np.std(pseudo_outcome, ddof=1) / math.sqrt(len(rows)))
        confidence = plan.inference_plan[outcome.outcome_id].confidence_level
        critical = 1.959963984540054 if confidence == 0.95 else 1.959963984540054
        interval = (effect - critical * se, effect + critical * se)
        statistic = effect / se if se else math.inf
        p_value = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(statistic) / math.sqrt(2.0))))
        by_arm = {
            "control": observed[treatment == 0.0],
            "treatment": observed[treatment == 1.0],
        }
        return OutcomeEvaluation(
            outcome_id=outcome.outcome_id,
            kind="continuous",
            eligible=True,
            arm_statistics={
                arm: {"n": len(values), "observed_mean": float(np.mean(values))}
                for arm, values in by_arm.items()
            },
            effect_measure="average_treatment_effect",
            effect=effect,
            standard_error=se,
            confidence_interval=interval,
            p_value=p_value,
            raw_p_value=p_value,
            denominator=len(rows),
            decision="inconclusive",
            details={
                "estimand": "ATE",
                "identification": "backdoor adjustment under the frozen DAG and assumptions",
                "estimator": "cross-fitted AIPW",
                "cross_fitting_folds": causal.cross_fitting_folds,
                "fold_seed": causal.fold_seed,
                "adjustment_set": list(causal.adjustment_set),
                "overlap_fraction": overlap_fraction,
                "propensity_min": float(np.min(propensity)),
                "propensity_max": float(np.max(propensity)),
                "propensity_bounds": [
                    causal.propensity_lower_bound,
                    causal.propensity_upper_bound,
                ],
            },
        )

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
        result = self._estimate(plan, rows)
        evaluation = StudyDesignEvaluation(
            study_design_id=self.descriptor.design_id,
            study_design_version=self.descriptor.version,
            eligible=result.eligible,
            qualification_checks={
                "realized_data_valid": True,
                "dag_and_backdoor_set_frozen": True,
                "positivity_checked": bool(result.eligible),
                "cross_fitting_completed": bool(result.eligible),
            },
            outcomes=[result],
            primary_decision="inconclusive",
            limitations=[
                "Exchangeability given the measured adjustment set is an identifying assumption, not a test result.",
                "The analysis cannot exclude bias from unmeasured confounding or measurement error.",
            ],
        )
        return self.adjudicate(plan, evaluation)

    def adjudicate(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> StudyDesignEvaluation:
        if not evaluation.outcomes:
            evaluation.primary_decision = "unverifiable"
            evaluation.eligible = False
            return evaluation
        result = evaluation.outcomes[0]
        if not result.eligible or not result.confidence_interval:
            result.decision = "unverifiable"
        else:
            rule = plan.decision_rules[result.outcome_id]
            low, high = result.confidence_interval
            if rule.mode != "superiority":
                result.decision = "unverifiable"
            elif rule.beneficial_direction == "higher":
                result.decision = "supported" if low > 0 else (
                    "refuted" if high < 0 else "inconclusive"
                )
            else:
                result.decision = "supported" if high < 0 else (
                    "refuted" if low > 0 else "inconclusive"
                )
        evaluation.primary_decision = result.decision
        evaluation.eligible = result.eligible
        return evaluation

    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope:
        causal = plan.causal
        assert causal is not None
        result = evaluation.outcomes[0] if evaluation.outcomes else None
        if result and result.effect is not None and result.confidence_interval:
            low, high = result.confidence_interval
            effects = [
                f"The cross-fitted AIPW estimate of the population average treatment effect was "
                f"{result.effect:.4g}, with a {plan.inference_plan[result.outcome_id].confidence_level:.0%} "
                f"interval from {low:.4g} to {high:.4g}."
            ]
        else:
            effects = ["The registered average treatment effect was not estimable under the frozen eligibility rules."]
        return ClaimEnvelope(
            study_design_name="Observational causal inference with backdoor adjustment",
            arm_definitions=[f"{arm.label}: {arm.definition}" for arm in plan.arms],
            allocation_verified_randomized=False,
            observation_unit=plan.unit_structure.observation_unit,
            analysis_unit=plan.unit_structure.analysis_unit,
            independent_unit=plan.unit_structure.independent_unit,
            primary_outcomes=[plan.outcomes[0].label],
            secondary_outcomes=[],
            denominator=result.denominator if result else 0,
            missing_count=result.missing_count if result else 0,
            excluded_count=result.excluded_count if result else 0,
            effect_estimates=effects,
            uncertainty_method="cross-fitted AIPW influence-function normal interval",
            multiplicity_method=plan.multiplicity.method.replace("_", " "),
            scientific_verdict=evaluation.primary_decision,
            permitted_claims=[
                "Report the frozen ATE estimate conditional on consistency, conditional exchangeability, positivity, and no interference.",
                "Report measured overlap and the exact owner-frozen adjustment set.",
            ],
            prohibited_claims=[
                "Do not present conditional exchangeability or absence of hidden confounding as empirically proven.",
                "Do not generalize the ATE to populations outside the frozen data boundary.",
                "Do not reinterpret the ATE as ATT, CATE, mediation, IV, DiD, RDD, or longitudinal treatment effect.",
                "Do not attribute the estimate to a mechanism not identified by the frozen graph.",
            ],
            generalization_boundary=plan.claim_boundary.get(
                "generalization", "Only the frozen eligible observational population is represented."
            ),
            reproduction_materials=list(plan.claim_boundary.get("reproduction_materials", [])),
            design_details={
                "identification_strategy": causal.identification_strategy,
                "estimand": causal.estimand,
                "adjustment_set": list(causal.adjustment_set),
                "exchangeability_assumption": causal.exchangeability_assumption,
                "consistency_assumption": causal.consistency_assumption,
                "no_interference_assumption": causal.no_interference_assumption,
            },
        )


__all__ = ["CausalInference"]
