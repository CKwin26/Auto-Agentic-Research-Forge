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
from research_forge.study_design.survival_case import (
    survival_acceptance_plan,
    survival_acceptance_rows,
)
from research_forge.study_design.survival_reference import recalculate_survival
from research_forge.study_design.survival_workflow_acceptance import (
    prepare_survival_workflow_acceptance,
)
from research_forge.study_design.workflow_evidence import derive_workflow_receipts
from research_forge.workflow_domain import (
    ArtifactStatus,
    GateStatus,
    GateType,
    WorkflowRepository,
)
from research_forge.profiles.stage_four_evidence import (
    build_stage_four_evidence_from_repository,
)


def survival_contract(payload: dict | None = None):
    return SimpleNamespace(
        study_design={"id": "survival_analysis_v1", "version": "1"},
        inference_modules=[],
        study_design_spec=(
            survival_acceptance_plan() if payload is None else payload
        ),
    )


def test_survival_profile_is_registered_as_executable() -> None:
    catalog = {item["design_id"]: item for item in study_design_catalog()}
    profile = catalog["survival_analysis_v1"]
    assert profile["maturity"] == "c2_dry_run"
    assert profile["verified_maturity"] == "c3_real_fixture"
    assert profile["acceptance_report_present"] is True
    assert profile["acceptance_hash"]
    assert profile["acceptance_study_id"] == "study-00230dda935fa413"
    assert profile["formal_execution_supported"] is True
    assert "right-censored" in " ".join(profile["known_limits"])


def test_survival_kernel_estimates_registered_rmst_difference() -> None:
    plan = compile_analysis_plan(survival_contract())
    evaluation = study_design(plan.study_design_id).evaluate(
        plan, survival_acceptance_rows()
    )
    result = evaluation.outcomes[0]

    assert evaluation.eligible is True
    assert evaluation.primary_decision == "supported"
    assert result.effect == pytest.approx(2.125, abs=1e-12)
    assert result.denominator == 48
    assert result.arm_statistics["control"]["events"] == 18
    assert result.arm_statistics["control"]["censored"] == 6
    assert result.arm_statistics["treatment"]["events"] == 14
    assert result.arm_statistics["treatment"]["censored"] == 10

    envelope = produce_claim_envelope(plan, evaluation)
    assert envelope.analysis_unit == "subject"
    assert "12 hour" in envelope.effect_estimates[0]
    assert any("censored" in item for item in envelope.prohibited_claims)
    assert any("proportional hazards" in item for item in envelope.prohibited_claims)


def test_independent_survival_recalculator_matches_production() -> None:
    plan = compile_analysis_plan(survival_contract())
    rows = survival_acceptance_rows()
    production = study_design(plan.study_design_id).evaluate(plan, rows).outcomes[0]
    reference = recalculate_survival(plan, rows)

    assert reference["effect"] == pytest.approx(production.effect, abs=1e-12)
    assert reference["standard_error"] == pytest.approx(
        production.standard_error, abs=1e-12
    )
    assert reference["confidence_interval"] == pytest.approx(
        production.confidence_interval, abs=1e-12
    )
    assert reference["primary_decision"] == production.decision


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p.__setitem__("survival", None), "survival"),
        (
            lambda p: p["allocation"].__setitem__("mechanism", "observational"),
            "randomized",
        ),
        (
            lambda p: p["outcomes"][0].__setitem__("kind", "continuous"),
            "time-to-event",
        ),
        (
            lambda p: p["estimands"][0].__setitem__(
                "effect_measure", "mean_difference"
            ),
            "RMST",
        ),
        (
            lambda p: p["missingness"].__setitem__("policy", "complete_case"),
            "right-censoring",
        ),
    ],
)
def test_survival_contract_mutations_fail_closed(mutate, expected: str) -> None:
    payload = deepcopy(survival_acceptance_plan())
    mutate(payload)
    errors = validate_composable_contract(survival_contract(payload))
    assert errors
    assert expected.lower() in " ".join(errors).lower()


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (
            lambda rows: rows[1].__setitem__("subject_id", rows[0]["subject_id"]),
            "more than once",
        ),
        (
            lambda rows: rows[0].__setitem__("failure_observed", "unknown"),
            "event status",
        ),
        (
            lambda rows: rows[0].__setitem__("duration_hours", 0),
            "nonpositive",
        ),
        (
            lambda rows: rows[0].__setitem__("arm", "unregistered"),
            "unregistered arm",
        ),
    ],
)
def test_survival_realized_data_corruption_is_unverifiable(mutate, fragment: str) -> None:
    plan = compile_analysis_plan(survival_contract())
    rows = survival_acceptance_rows()
    mutate(rows)
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    assert evaluation.eligible is False
    assert evaluation.primary_decision == "unverifiable"
    assert fragment in " ".join(evaluation.limitations)


def test_too_few_observed_events_is_unverifiable_not_null_effect() -> None:
    plan = compile_analysis_plan(survival_contract())
    rows = survival_acceptance_rows()
    for row in rows:
        if row["arm"] == "treatment":
            row["failure_observed"] = 0
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    assert evaluation.eligible is False
    assert evaluation.primary_decision == "unverifiable"
    assert evaluation.outcomes[0].details["reason"] == (
        "too few subjects or observed events per arm"
    )


def test_survival_contract_completion_requires_owner_design_decisions() -> None:
    profile = study_design("survival_analysis_v1")
    patch = profile.complete_contract({}, survival_contract({}))
    fields = {item.field_path for item in patch.owner_confirmation_bundle}
    assert "study_design_spec.survival" in fields
    assert "study_design_spec.missingness" in fields
    assert not patch.deterministic_updates


def test_survival_formal_acceptance_uses_canonical_four_phase_workflow(
    tmp_path,
) -> None:
    prepared = prepare_survival_workflow_acceptance(tmp_path / "formal-survival")
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    contract = repository.latest_research_contract(study_id)

    assert repository.latest_scope_contract(study_id).status is ArtifactStatus.FROZEN
    assert contract.status is ArtifactStatus.FROZEN
    assert contract.study_design["id"] == "survival_analysis_v1"
    assert contract.study_design_spec["unit_structure"]["independent_unit"] == "subject"
    assert contract.study_design_spec["survival"]["restriction_time"] == 12.0
    assert any(
        gate.gate_type is GateType.RESEARCH_CONTRACT
        and gate.status is GateStatus.APPROVED
        for gate in repository.list_gates(study_id)
    )
    assert len(repository.list_result_envelopes(study_id)) == 2
    evaluations = repository.list_evaluation_records(study_id)
    assert len(evaluations) == 1
    assert evaluations[0].variance_unit == "subject"
    assert evaluations[0].independent_unit_count == 48
    assert evaluations[0].paired_effect == pytest.approx(2.125)
    assert len(repository.list_stage3_completions(study_id)) == 1
    assert prepared["scientific_verdict"] == "supported"
    assert prepared["stage_four_steps"] == 31

    map_payload = build_stage_four_evidence_from_repository(
        repository, study_id
    ).evidence_claim_map.model_dump(mode="json")
    method_statement = next(
        item["statement"]
        for item in map_payload["bindings"]
        if item["claim_id"].endswith(":method")
    )
    assert "bootstrap repetitions: 1000" in method_statement
    assert "bootstrap seed: 20260804" in method_statement
    assert "control events: 18" in method_statement
    assert "control censored: 6" in method_statement
    assert "treatment events: 14" in method_statement
    assert "treatment censored: 10" in method_statement

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
