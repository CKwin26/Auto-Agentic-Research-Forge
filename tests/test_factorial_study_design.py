from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from research_forge.study_design import (
    compile_analysis_plan,
    produce_claim_envelope,
    study_design,
    study_design_catalog,
    validate_composable_contract,
)
from research_forge.study_design.factorial_reference import recalculate_factorial
from research_forge.study_design.factorial_workflow_acceptance import (
    prepare_factorial_workflow_acceptance,
)
from research_forge.study_design.workflow_evidence import derive_workflow_receipts
from research_forge.workflow_domain import (
    ArtifactStatus,
    EntryMode,
    GateStatus,
    GateType,
    WorkflowRepository,
)


def factorial_payload() -> dict:
    factors = [
        {"factor_id": "assistance", "label": "Assistance", "field": "assistance", "levels": ["off", "on"]},
        {"factor_id": "feedback", "label": "Feedback", "field": "feedback", "levels": ["brief", "detailed"]},
    ]
    cells = [
        {"arm_id": "off-brief", "levels": {"assistance": "off", "feedback": "brief"}},
        {"arm_id": "off-detailed", "levels": {"assistance": "off", "feedback": "detailed"}},
        {"arm_id": "on-brief", "levels": {"assistance": "on", "feedback": "brief"}},
        {"arm_id": "on-detailed", "levels": {"assistance": "on", "feedback": "detailed"}},
    ]
    contrasts = [
        {"contrast_id": "assistance-main", "label": "Average assistance effect", "outcome_id": "quality", "kind": "main_effect", "factor_ids": ["assistance"], "role": "secondary", "beneficial_direction": "higher"},
        {"contrast_id": "feedback-main", "label": "Average feedback effect", "outcome_id": "quality", "kind": "main_effect", "factor_ids": ["feedback"], "role": "secondary", "beneficial_direction": "higher"},
        {"contrast_id": "assistance-feedback", "label": "Assistance by feedback interaction", "outcome_id": "quality", "kind": "interaction", "factor_ids": ["assistance", "feedback"], "role": "primary", "beneficial_direction": "higher"},
    ]
    return {
        "schema_version": 1,
        "study_design_id": "factorial_experiment_v1",
        "study_design_version": "1",
        "unit_structure": {
            "row_unit": "one task record", "observation_unit": "task",
            "assignment_unit": "task", "analysis_unit": "task",
            "variance_unit": "task", "independent_unit": "task",
        },
        "allocation": {"mechanism": "randomized", "evidence": "Frozen seeded allocation ledger."},
        "arms": [
            {"arm_id": cell["arm_id"], "label": cell["arm_id"].replace("-", " ").title(), "role": "factorial_cell", "definition": str(cell["levels"])}
            for cell in cells
        ],
        "outcomes": [
            {"outcome_id": "quality", "label": "Task quality", "kind": "continuous", "field": "quality", "role": "primary", "beneficial_direction": "higher"}
        ],
        "estimands": [],
        "estimator_plan": {"quality": {"estimator_id": "factorial_ols_hc2_v1", "equal_variance_assumed": False}},
        "inference_plan": {"quality": {"method": "effect-coded OLS with HC2 covariance", "confidence_level": 0.95}},
        "missingness": {"policy": "complete_case", "denominator_rule": "Observed task records in all four cells", "exclusion_reasons_required": True},
        "multiplicity": {"method": "holm", "family_id": "factorial-confirmatory", "hypothesis_ids": [item["contrast_id"] for item in contrasts], "alpha_or_q": 0.05},
        "decision_rules": {
            item["contrast_id"]: {"mode": "superiority", "effect_measure": item["kind"], "beneficial_direction": "higher", "confidence_level": 0.95}
            for item in contrasts
        },
        "claim_boundary": {"generalization": "The frozen randomized software-task fixture.", "reproduction_materials": ["allocation ledger", "factorial rows", "analysis plan"]},
        "factorial": {"factors": factors, "cells": cells, "contrasts": contrasts, "coding": "effect_coding", "covariance": "hc2_robust", "minimum_observed_per_cell": 2},
    }


def factorial_contract(payload: dict | None = None):
    return SimpleNamespace(
        study_design={"id": "factorial_experiment_v1", "version": "1"},
        inference_modules=[{"id": "multiplicity_control_v1", "version": "1"}],
        study_design_spec=payload or factorial_payload(),
    )


def factorial_rows() -> list[dict]:
    rows: list[dict] = []
    for assistance, a_code in (("off", -0.5), ("on", 0.5)):
        for feedback, b_code in (("brief", -0.5), ("detailed", 0.5)):
            arm = f"{assistance}-{feedback}"
            for index in range(20):
                noise = ((index % 5) - 2) * 0.2
                rows.append({
                    "subject_id": f"{arm}-{index:02d}", "arm": arm,
                    "assistance": assistance, "feedback": feedback,
                    "quality": 50 + 2 * a_code + 3 * b_code + 4 * a_code * b_code + noise,
                })
    return rows


def test_factorial_profile_exposes_static_floor_and_verified_c3() -> None:
    catalog = {item["design_id"]: item for item in study_design_catalog()}
    profile = catalog["factorial_experiment_v1"]
    assert profile["maturity"] == "c2_dry_run"
    assert profile["verified_maturity"] == "c3_real_fixture"
    assert profile["acceptance_report_present"] is True
    assert profile["acceptance_hash"]
    assert profile["acceptance_study_id"] == "study-4d3e36fbed425a5a"
    assert profile["formal_execution_supported"] is True
    assert "2x2" in " ".join(profile["known_limits"])


def test_factorial_kernel_estimates_main_effects_and_interaction() -> None:
    plan = compile_analysis_plan(factorial_contract())
    evaluation = study_design(plan.study_design_id).evaluate(plan, factorial_rows())
    by_id = {item.outcome_id: item for item in evaluation.outcomes}

    assert evaluation.eligible is True
    assert evaluation.primary_decision == "supported"
    assert by_id["assistance-main"].effect == pytest.approx(2.0)
    assert by_id["feedback-main"].effect == pytest.approx(3.0)
    assert by_id["assistance-feedback"].effect == pytest.approx(4.0)
    assert all(item.denominator == 80 for item in evaluation.outcomes)
    assert all(item.adjusted_p_value is not None for item in evaluation.outcomes)
    assert all(item.details["estimator"].endswith("HC2 robust covariance") for item in evaluation.outcomes)

    envelope = produce_claim_envelope(plan, evaluation)
    rendered = " ".join(envelope.effect_estimates + envelope.permitted_claims)
    assert "interaction" in rendered.lower()
    assert "factorial_experiment_v1" not in rendered
    assert envelope.design_details["factors"][0]["label"] == "Assistance"


def test_factorial_independent_recalculator_agrees_with_production() -> None:
    plan = compile_analysis_plan(factorial_contract())
    rows = factorial_rows()
    production = study_design(plan.study_design_id).evaluate(plan, rows)
    reference = recalculate_factorial(plan, rows)
    produced = {item.outcome_id: item for item in production.outcomes}
    for contrast_id, expected in reference["contrasts"].items():
        actual = produced[contrast_id]
        assert actual.effect == pytest.approx(expected["effect"], abs=1e-12)
        assert actual.standard_error == pytest.approx(expected["standard_error"], abs=1e-12)
        assert actual.confidence_interval == pytest.approx(expected["confidence_interval"], abs=1e-12)
        assert actual.adjusted_p_value == pytest.approx(expected["adjusted_p_value"], abs=1e-12)
        assert actual.decision == expected["decision"]
    assert production.primary_decision == reference["primary_decision"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda value: value["factorial"]["cells"].pop(), "at least 4"),
        (lambda value: value["factorial"]["factors"][0].__setitem__("levels", ["off", "off"]), "levels must be distinct"),
        (lambda value: value["factorial"]["contrasts"][0].__setitem__("factor_ids", ["unknown"]), "unknown factor"),
        (lambda value: value["allocation"].__setitem__("mechanism", "observational"), "randomized"),
        (lambda value: value["multiplicity"].__setitem__("method", "no_correction"), "multiplicity control"),
        (lambda value: value["estimator_plan"]["quality"].__setitem__("estimator_id", "welch_mean_difference_v1"), "factorial OLS"),
        (lambda value: value["unit_structure"].__setitem__("repeated_measure_unit", "task"), "repeated-measure"),
    ],
)
def test_factorial_contract_mutations_fail_closed(mutation, expected) -> None:
    payload = deepcopy(factorial_payload())
    mutation(payload)
    assert any(expected in item for item in validate_composable_contract(factorial_contract(payload)))


def test_factorial_realized_data_rejects_repeats_and_cell_mismatch() -> None:
    plan = compile_analysis_plan(factorial_contract())
    rows = factorial_rows()
    rows.append(dict(rows[0]))
    rows[1]["assistance"] = "on"
    errors = study_design(plan.study_design_id).validate_realized_data(plan, rows)
    assert any("repeated observations" in item for item in errors)
    assert any("do not match" in item for item in errors)


def test_factorial_completion_exposes_owner_scientific_decisions() -> None:
    profile = study_design("factorial_experiment_v1")
    patch = profile.complete_contract({}, SimpleNamespace(study_design_spec={}))
    assert patch.owner_confirmation_bundle
    assert {item.field_path for item in patch.issues} >= {
        "study_design_spec.factorial",
        "study_design_spec.decision_rules",
    }


def test_factorial_formal_acceptance_uses_canonical_four_phase_workflow(tmp_path) -> None:
    prepared = prepare_factorial_workflow_acceptance(tmp_path / "formal-factorial")
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]

    study = repository.load_study(study_id)
    contract = repository.latest_research_contract(study_id)
    assert study.entry_mode is EntryMode.IDEA_TO_PAPER
    assert repository.latest_scope_contract(study_id).status is ArtifactStatus.FROZEN
    assert contract.status is ArtifactStatus.FROZEN
    assert contract.study_design["id"] == "factorial_experiment_v1"
    assert any(
        gate.gate_type is GateType.RESEARCH_CONTRACT
        and gate.status is GateStatus.APPROVED
        for gate in repository.list_gates(study_id)
    )
    assert len(repository.list_result_envelopes(study_id)) == 4
    evaluations = repository.list_evaluation_records(study_id)
    assert {item.metric_name for item in evaluations} == {
        "Average effect of structured assistance",
        "Average effect of explanatory feedback",
        "Interaction between structured assistance and explanatory feedback",
    }
    assert len(repository.list_stage3_completions(study_id)) == 1
    assert prepared["scientific_verdict"] == "supported"
    assert prepared["stage_four_steps"] == 31

    receipts = derive_workflow_receipts(repository, study_id)
    assert all(
        receipts[key]
        for key in (
            "idea_intake",
            "scope_frozen",
            "profile_qualified",
            "research_contract_completed",
            "owner_contract_approved",
            "research_contract_frozen",
            "resources_bound",
            "dry_run_completed",
            "formal_evaluation_completed",
            "independent_recalculation_completed",
            "scientific_verdict_frozen",
        )
    )
    assert not receipts["stage_four_manuscript_completed"]
