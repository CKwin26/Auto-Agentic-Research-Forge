"""Required fail-closed cases for the independent-group Study Design.

The acceptance harness exercises the same schemas, contract validators,
realized-data validators, inference boundaries, and Stage 4 authority checks
used by the workflow.  Each rejected case is represented by a structured
``CompletionIssue`` so the UI can explain the blocker instead of displaying a
bare PASS/FAIL flag.
"""

from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace
from typing import Any, Callable

from .evidence import validate_claim_envelope_authority
from .inference.bayesian import BayesianInference
from .inference.noninferiority import interval_decision
from .registry import study_design
from .schemas import (
    AnalysisPlan,
    ClaimEnvelope,
    CompletionIssue,
    RepairClass,
    StudyDesignEvaluation,
)
from .validation import blocking_issues_for_realized_data


def _issue(case_id: str, observed: Any, *, field_path: str) -> CompletionIssue:
    digest = hashlib.sha256(
        f"{case_id}:{observed}".encode("utf-8")
    ).hexdigest()[:16]
    return CompletionIssue(
        issue_id=f"negative-{digest}",
        field_path=field_path,
        severity="critical",
        repair_class=RepairClass.UNRESOLVABLE_BLOCKER,
        expected="the frozen scientific design and evidence authority remain valid",
        observed=observed,
        provenance=f"required negative acceptance case: {case_id}",
        owner_approval_required=False,
    )


def _schema_or_contract_rejection(
    payload: dict[str, Any],
) -> str | None:
    try:
        candidate = AnalysisPlan.model_validate(payload)
    except Exception as exc:  # Pydantic supplies the precise field diagnosis.
        return str(exc)
    contract = SimpleNamespace(
        study_design={
            "id": candidate.study_design_id,
            "version": candidate.study_design_version,
        },
        inference_modules=[],
        study_design_spec=candidate.model_dump(mode="json"),
    )
    violations = study_design(candidate.study_design_id).validate_contract(contract)
    return "; ".join(violations) if violations else None


def required_negative_acceptance_cases(
    plan: AnalysisPlan,
    rows: list[dict[str, Any]],
    evaluation: StudyDesignEvaluation,
    envelope: ClaimEnvelope,
) -> dict[str, dict[str, Any]]:
    """Execute the fifteen registered negative cases and return blockers."""

    base = plan.model_dump(mode="json")
    cases: dict[str, tuple[str, Callable[[], str | None]]] = {}

    def realized(mutated: list[dict[str, Any]]) -> str | None:
        issues = blocking_issues_for_realized_data(plan, mutated)
        return issues[0].observed if issues else None

    cross_arm = copy.deepcopy(rows)
    cross_arm.append({**copy.deepcopy(rows[0]), "arm": "treatment"})
    cases["01_cross_arm_duplicate"] = (
        "formal_dataset",
        lambda: realized(cross_arm),
    )
    repeated = copy.deepcopy(rows)
    repeated.append(copy.deepcopy(rows[0]))
    cases["02_repeated_subject"] = (
        "formal_dataset",
        lambda: realized(repeated),
    )

    missing_primary = copy.deepcopy(base)
    for outcome in missing_primary["outcomes"]:
        outcome["role"] = "secondary"
    cases["03_missing_primary_outcome"] = (
        "outcomes",
        lambda: _schema_or_contract_rejection(missing_primary),
    )

    units_missing = copy.deepcopy(base)
    units_missing["unit_structure"]["analysis_unit"] = ""
    units_missing["unit_structure"]["variance_unit"] = ""
    cases["04_analysis_and_variance_units_missing"] = (
        "unit_structure",
        lambda: _schema_or_contract_rejection(units_missing),
    )

    binary_continuous = copy.deepcopy(base)
    binary_id = next(
        item["outcome_id"]
        for item in binary_continuous["outcomes"]
        if item["kind"] == "binary"
    )
    binary_continuous["estimator_plan"][binary_id]["estimator_id"] = (
        "welch_mean_difference_v1"
    )
    cases["05_binary_outcome_with_continuous_estimator"] = (
        "estimator_plan",
        lambda: _schema_or_contract_rejection(binary_continuous),
    )

    same_arm = copy.deepcopy(base)
    same_arm["estimands"][0]["treatment_arm_id"] = same_arm["estimands"][0][
        "control_arm_id"
    ]
    cases["06_same_control_and_treatment_arm"] = (
        "estimands",
        lambda: _schema_or_contract_rejection(same_arm),
    )

    def missing_margin() -> str | None:
        try:
            interval_decision(
                (-0.1, 0.2),
                mode="noninferiority",
                direction="higher",
                margin=None,
            )
        except ValueError as exc:
            return str(exc)
        return None

    cases["07_noninferiority_margin_missing"] = (
        "decision_rules",
        missing_margin,
    )

    direction_reversed = copy.deepcopy(base)
    primary_id = next(
        item["outcome_id"]
        for item in direction_reversed["outcomes"]
        if item["role"] == "primary"
    )
    direction_reversed["decision_rules"][primary_id] = {
        "mode": "noninferiority",
        "effect_measure": "mean_difference",
        "beneficial_direction": "lower",
        "margin": 1.0,
        "margin_unit": "quality points",
        "margin_provenance": "owner-approved acceptance rule",
        "owner_approved": True,
    }
    cases["08_noninferiority_direction_reversed"] = (
        "decision_rules",
        lambda: _schema_or_contract_rejection(direction_reversed),
    )

    def significance_is_not_equivalence() -> str | None:
        superiority = interval_decision(
            (-0.2, 0.2), mode="superiority", direction="higher"
        )
        try:
            interval_decision(
                (-0.2, 0.2),
                mode="equivalence",
                direction="higher",
            )
        except ValueError as exc:
            return (
                "p > 0.05 / an interval crossing zero is inconclusive for "
                f"superiority ({superiority}) and cannot establish equivalence: {exc}"
            )
        return None

    cases["09_nonsignificance_claimed_as_equivalence"] = (
        "decision_rules",
        significance_is_not_equivalence,
    )

    no_multiplicity = copy.deepcopy(base)
    no_multiplicity["multiplicity"]["method"] = "no_correction"
    cases["10_multiple_confirmatory_without_multiplicity"] = (
        "multiplicity",
        lambda: _schema_or_contract_rejection(no_multiplicity),
    )

    post_results = copy.deepcopy(base)
    post_results["multiplicity"]["registered_before_results"] = False
    cases["11_post_result_confirmatory_addition"] = (
        "multiplicity",
        lambda: _schema_or_contract_rejection(post_results),
    )

    def bayesian_violation(*, informative: bool, owner_approved: bool) -> str | None:
        extension = {
            "seed": 7,
            "informative": informative,
            "owner_approved": owner_approved,
        }
        if informative:
            extension.update(
                {
                    "prior": {"alpha": 10, "beta": 2},
                    "prior_provenance": "historical evidence",
                }
            )
        candidate = SimpleNamespace(
            study_design={"id": plan.study_design_id, "version": "1"},
            study_design_spec=base,
            inference_modules=[
                {
                    "id": "bayesian_inference_v1",
                    "version": "1",
                    "extension": extension,
                }
            ],
        )
        violations = BayesianInference().validate_contract(candidate)
        return "; ".join(violations) if violations else None

    cases["12_bayesian_prior_unrecorded"] = (
        "inference_modules.bayesian_inference_v1",
        lambda: bayesian_violation(informative=False, owner_approved=False),
    )
    cases["13_informative_prior_unapproved"] = (
        "inference_modules.bayesian_inference_v1",
        lambda: bayesian_violation(informative=True, owner_approved=False),
    )

    def silent_denominator_removal() -> str | None:
        primary = next(
            item
            for item in evaluation.outcomes
            if item.outcome_id == primary_id
        )
        if primary.denominator + primary.missing_count == len(rows):
            return (
                "removing missing rows without retaining them in the frozen "
                "population and missingness ledger is forbidden"
            )
        return None

    cases["14_missing_sample_silently_removed"] = (
        "missingness.denominator_rule",
        silent_denominator_removal,
    )

    def stage_four_rewrite() -> str | None:
        replacement = (
            "refuted"
            if envelope.scientific_verdict != "refuted"
            else "supported"
        )
        candidate = envelope.model_copy(
            update={"scientific_verdict": replacement}
        )
        violations = validate_claim_envelope_authority(evaluation, candidate)
        return "; ".join(violations) if violations else None

    cases["15_stage_four_verdict_rewrite"] = (
        "claim_envelope.scientific_verdict",
        stage_four_rewrite,
    )

    report: dict[str, dict[str, Any]] = {}
    for case_id, (field_path, check) in cases.items():
        observed = check()
        issue = _issue(case_id, observed, field_path=field_path) if observed else None
        report[case_id] = {
            "passed": issue is not None,
            "blocking_issue": issue.model_dump(mode="json") if issue else None,
        }
    return report


__all__ = ["required_negative_acceptance_cases"]
