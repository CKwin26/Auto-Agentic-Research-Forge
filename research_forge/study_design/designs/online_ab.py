"""Certified fixed-horizon online A/B test Study Design."""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Any

from ..schemas import (
    AnalysisPlan,
    ClaimEnvelope,
    CompletionIssue,
    RepairClass,
    StudyDesignCompletionPatch,
    StudyDesignEvaluation,
    StudyDesignMaturity,
)
from ..sdk import StudyDesignDescriptor
from .independent_group import IndependentGroupComparison


def _payload(contract: Any) -> dict[str, Any]:
    value = getattr(contract, "study_design_spec", None)
    if value is None and isinstance(contract, dict):
        value = contract.get("study_design_spec")
    return dict(value or {})


def _as_plan(contract: Any) -> AnalysisPlan:
    data = _payload(contract)
    data.setdefault("study_design_id", "online_ab_test_v1")
    data.setdefault("study_design_version", "1")
    return AnalysisPlan.model_validate(data)


def _timestamp(value: Any) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("online A/B timestamps must include a timezone")
    return parsed


def _srm_p_value(observed: dict[str, int], expected: dict[str, float]) -> float:
    total = sum(observed.values())
    statistic = sum(
        (observed[arm_id] - total * ratio) ** 2 / (total * ratio)
        for arm_id, ratio in expected.items()
    )
    # Two arms imply one degree of freedom: P(ChiSquare_1 >= x).
    return math.erfc(math.sqrt(statistic / 2.0))


class OnlineABTest(IndependentGroupComparison):
    descriptor = StudyDesignDescriptor(
        design_id="online_ab_test_v1",
        version="1",
        title="Fixed-horizon online A/B test",
        summary=(
            "Evaluates exposed randomization units in a frozen online window "
            "with SRM detection, a fixed-horizon no-peeking rule, one primary "
            "metric, and separately reported guardrails."
        ),
        maturity=StudyDesignMaturity.C2_DRY_RUN,
        formal_execution_supported=True,
        known_limits=(
            "two arms and one exposure per randomization unit",
            "fixed-horizon inference only; no always-valid or alpha-spending sequential estimator",
            "no cluster randomization, switchback, interference, or delayed-outcome correction",
        ),
    )

    def complete_contract(
        self, task_brief: Any, draft_contract: Any, resources: Any | None = None
    ) -> StudyDesignCompletionPatch:
        data = _payload(draft_contract)
        issues: list[CompletionIssue] = []
        required = {
            "unit_structure": "Freeze exposure, randomization, analysis, and variance units.",
            "allocation": "Freeze randomized assignment evidence and arm ratio.",
            "arms": "Define the control and treatment experiences.",
            "outcomes": "Register one primary metric and any guardrails.",
            "estimands": "Bind exposed-user estimands to both arms.",
            "online_ab": "Freeze exposure eligibility, window, SRM, horizon, and stopping rule.",
            "decision_rules": "Freeze primary and guardrail decision semantics before launch.",
        }
        for field, expected in required.items():
            if not data.get(field):
                issues.append(
                    CompletionIssue(
                        issue_id=f"online-ab-{field}-missing",
                        field_path=f"study_design_spec.{field}",
                        severity="error",
                        repair_class=RepairClass.OWNER_SCIENTIFIC_DECISION,
                        expected=expected,
                        observed=data.get(field),
                        provenance="online_ab_test_v1 schema",
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
            return [f"invalid online A/B analysis plan: {exc}"]
        violations = list(
            super().validate_contract(
                {"study_design_spec": plan.model_dump(mode="json")}
            )
        )
        if plan.online_ab is None:
            return [*violations, "online A/B requires the frozen exposure and stopping extension"]
        spec = plan.online_ab
        if plan.allocation.mechanism != "randomized":
            violations.append("online A/B requires randomized assignment")
        arm_ids = {item.arm_id for item in plan.arms}
        if set(spec.allocation_ratio) != arm_ids:
            violations.append("online A/B allocation ratios must name both registered arms")
        if plan.unit_structure.assignment_unit != plan.unit_structure.independent_unit:
            violations.append("online A/B v1 requires independent randomization units")
        if plan.unit_structure.cluster_unit or plan.unit_structure.repeated_measure_unit:
            violations.append("clustered or repeated online tests require another Profile")
        primary = [item for item in plan.outcomes if item.role == "primary"]
        if len(primary) != 1:
            violations.append("online A/B requires exactly one primary outcome")
        outcome_by_id = {item.outcome_id: item for item in plan.outcomes}
        for outcome_id in spec.guardrail_outcome_ids:
            if outcome_id not in outcome_by_id:
                violations.append("every online A/B guardrail must reference an outcome")
            elif outcome_by_id[outcome_id].role != "secondary":
                violations.append("online A/B guardrails must be registered secondary outcomes")
        try:
            if _timestamp(spec.window_end) <= _timestamp(spec.window_start):
                violations.append("online A/B exposure window end must follow its start")
        except ValueError as exc:
            violations.append(str(exc))
        if "no" not in spec.stopping_rule.lower() and "only" not in spec.stopping_rule.lower():
            violations.append("fixed-horizon stopping rule must explicitly prohibit early scientific decisions")
        if plan.missingness.policy != "fail_if_any":
            violations.append("online A/B v1 fails closed on missing exposure or outcome fields")
        return list(dict.fromkeys(violations))

    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan:
        violations = self.validate_contract(contract)
        if violations:
            raise ValueError("; ".join(violations))
        return _as_plan(contract)

    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]:
        spec = plan.online_ab
        if spec is None:
            return ["online A/B exposure and stopping extension is absent"]
        errors: list[str] = []
        start, end = _timestamp(spec.window_start), _timestamp(spec.window_end)
        required = {
            spec.randomization_unit_field,
            spec.exposure_id_field,
            spec.exposure_timestamp_field,
            "arm",
            *(item.field for item in plan.outcomes),
        }
        units: Counter[str] = Counter()
        exposures: Counter[str] = Counter()
        arms: Counter[str] = Counter()
        registered_arms = {item.arm_id for item in plan.arms}
        for index, row in enumerate(rows):
            missing = sorted(
                field for field in required if field not in row or row[field] in {None, ""}
            )
            if missing:
                errors.append(f"row {index} is missing required online A/B fields: {', '.join(missing)}")
                continue
            unit = str(row[spec.randomization_unit_field])
            exposure = str(row[spec.exposure_id_field])
            arm = str(row["arm"])
            units[unit] += 1
            exposures[exposure] += 1
            arms[arm] += 1
            if arm not in registered_arms:
                errors.append(f"row {index} uses an unregistered online A/B arm")
            try:
                observed_at = _timestamp(row[spec.exposure_timestamp_field])
            except ValueError as exc:
                errors.append(f"row {index}: {exc}")
                continue
            if not start <= observed_at < end:
                errors.append(f"row {index} falls outside the frozen exposure window")
        if any(count != 1 for count in units.values()):
            errors.append("each online A/B randomization unit must contribute one first valid exposure")
        if any(count != 1 for count in exposures.values()):
            errors.append("online A/B exposure identifiers must be unique")
        if len(rows) != spec.planned_total_exposures:
            errors.append("fixed-horizon online A/B evaluation requires the complete planned exposure count")
        if any(arms[arm_id] < spec.minimum_exposures_per_arm for arm_id in registered_arms):
            errors.append("online A/B arm count is below the frozen minimum")
        if set(arms) == registered_arms and rows:
            if _srm_p_value(dict(arms), spec.allocation_ratio) < spec.srm_alpha:
                errors.append("sample-ratio mismatch crossed the frozen SRM threshold")
        return list(dict.fromkeys(errors))

    def evaluate(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> StudyDesignEvaluation:
        result = super().evaluate(plan, rows)
        if not result.eligible or plan.online_ab is None:
            return result
        counts = Counter(str(row["arm"]) for row in rows)
        srm_p = _srm_p_value(dict(counts), plan.online_ab.allocation_ratio)
        result.qualification_checks.update(
            {
                "exposure_window_frozen": True,
                "fixed_horizon_reached": len(rows)
                == plan.online_ab.planned_total_exposures,
                "sample_ratio_match_passed": srm_p >= plan.online_ab.srm_alpha,
                "one_exposure_per_randomization_unit": True,
            }
        )
        for outcome in result.outcomes:
            outcome.details.update(
                {
                    "online_ab_analysis_mode": plan.online_ab.analysis_mode,
                    "planned_total_exposures": plan.online_ab.planned_total_exposures,
                    "srm_p_value": srm_p,
                    "srm_alpha": plan.online_ab.srm_alpha,
                }
            )
        return result

    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope:
        envelope = super().produce_claim_envelope(plan, evaluation)
        spec = plan.online_ab
        if spec is None:
            return envelope
        return envelope.model_copy(
            update={
                "study_design_name": "Fixed-horizon online A/B test",
                "permitted_claims": [
                    *envelope.permitted_claims,
                    "Interpret results only for first valid exposures in the frozen experiment window after the planned horizon and SRM check passed.",
                ],
                "prohibited_claims": [
                    *envelope.prohibited_claims,
                    "Do not present interim monitoring as confirmatory evidence or imply an always-valid sequential test.",
                    "Do not generalize from exposed units to unexposed eligible traffic without an explicit transport assumption.",
                ],
                "design_details": {
                    **envelope.design_details,
                    "experiment_id": spec.experiment_id,
                    "analysis_population": spec.analysis_population,
                    "analysis_mode": spec.analysis_mode,
                    "planned_total_exposures": spec.planned_total_exposures,
                    "srm_alpha": spec.srm_alpha,
                    "stopping_rule": spec.stopping_rule,
                    "guardrail_outcome_ids": spec.guardrail_outcome_ids,
                },
            }
        )


__all__ = ["OnlineABTest"]
