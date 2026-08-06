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
from research_forge.study_design.longitudinal_case import (
    longitudinal_acceptance_plan,
    longitudinal_acceptance_rows,
)
from research_forge.study_design.longitudinal_reference import (
    recalculate_longitudinal,
)
from research_forge.study_design.longitudinal_workflow_acceptance import (
    prepare_longitudinal_workflow_acceptance,
)
from research_forge.study_design.workflow_evidence import derive_workflow_receipts
from research_forge.workflow_domain import (
    ArtifactStatus,
    GateStatus,
    GateType,
    WorkflowRepository,
)


def longitudinal_contract(payload: dict | None = None):
    return SimpleNamespace(
        study_design={"id": "longitudinal_repeated_measures_v1", "version": "1"},
        inference_modules=[],
        study_design_spec=payload or longitudinal_acceptance_plan(),
    )


def test_longitudinal_profile_is_registered_as_executable() -> None:
    catalog = {item["design_id"]: item for item in study_design_catalog()}
    profile = catalog["longitudinal_repeated_measures_v1"]
    assert profile["maturity"] == "c2_dry_run"
    assert profile["verified_maturity"] == "c3_real_fixture"
    assert profile["acceptance_report_present"] is True
    assert profile["acceptance_hash"]
    assert profile["acceptance_study_id"] == "study-7880b7e6a9e26597"
    assert profile["formal_execution_supported"] is True
    assert "subject slopes" in " ".join(profile["known_limits"])


def test_longitudinal_kernel_estimates_between_arm_slope_difference() -> None:
    plan = compile_analysis_plan(longitudinal_contract())
    rows = longitudinal_acceptance_rows()
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    result = evaluation.outcomes[0]

    assert evaluation.eligible is True
    assert evaluation.primary_decision == "supported"
    assert result.effect == pytest.approx(1.2, abs=1e-12)
    assert result.denominator == 24
    assert result.missing_count == 0
    assert result.excluded_count == 0
    assert result.arm_statistics["control"]["n_subjects"] == 12
    assert result.arm_statistics["treatment"]["n_subjects"] == 12

    envelope = produce_claim_envelope(plan, evaluation)
    assert envelope.analysis_unit == "subject"
    assert envelope.independent_unit == "subject"
    assert "slope" in " ".join(envelope.effect_estimates).lower()
    assert "longitudinal_repeated_measures_v1" not in " ".join(envelope.effect_estimates)
    assert any("repeated rows" in item for item in envelope.prohibited_claims)


def test_independent_recalculator_matches_production_longitudinal_result() -> None:
    plan = compile_analysis_plan(longitudinal_contract())
    rows = longitudinal_acceptance_rows()
    production = study_design(plan.study_design_id).evaluate(plan, rows).outcomes[0]
    reference = recalculate_longitudinal(plan, rows)

    assert production.effect == pytest.approx(reference["effect"], abs=1e-12)
    assert production.standard_error == pytest.approx(
        reference["standard_error"], abs=1e-12
    )
    assert production.confidence_interval == pytest.approx(
        reference["confidence_interval"], abs=1e-12
    )
    assert production.p_value == pytest.approx(reference["p_value"], abs=1e-12)
    assert production.degrees_of_freedom == pytest.approx(
        reference["degrees_of_freedom"], abs=1e-12
    )
    assert production.decision == reference["primary_decision"]
    assert reference["outcomes"]["skill_score"]["decision"] == "supported"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda value: value.__setitem__("longitudinal", None), "longitudinal extension"),
        (lambda value: value["allocation"].__setitem__("mechanism", "observational"), "randomized"),
        (lambda value: value["unit_structure"].__setitem__("independent_unit", "visit"), "must all be the subject"),
        (lambda value: value["unit_structure"].__setitem__("cluster_unit", "clinic"), "clustered"),
        (lambda value: value["estimator_plan"]["skill_score"].__setitem__("estimator_id", "welch_mean_difference_v1"), "subject-slope estimator"),
        (lambda value: value["missingness"].__setitem__("policy", "complete_case"), "minimum-observed-trajectory"),
        (lambda value: value["longitudinal"].__setitem__("planned_time_points", [0, 1, 1, 2]), "must be distinct"),
    ],
)
def test_longitudinal_contract_mutations_fail_closed(mutation, expected) -> None:
    payload = deepcopy(longitudinal_acceptance_plan())
    mutation(payload)
    violations = validate_composable_contract(longitudinal_contract(payload))
    assert any(expected in item for item in violations), violations


def test_longitudinal_realized_data_rejects_duplicate_time_arm_switch_and_unknown_time() -> None:
    plan = compile_analysis_plan(longitudinal_contract())
    rows = longitudinal_acceptance_rows()
    rows.append(dict(rows[0]))
    rows[1]["arm"] = "treatment"
    rows[2]["week"] = 9.0
    errors = study_design(plan.study_design_id).validate_realized_data(plan, rows)
    assert any("duplicate" in item for item in errors)
    assert any("switch" in item for item in errors)
    assert any("outside the frozen grid" in item for item in errors)


def test_longitudinal_missing_visits_use_subject_level_denominator() -> None:
    plan = compile_analysis_plan(longitudinal_contract())
    rows = longitudinal_acceptance_rows()
    for row in rows:
        if row["subject_id"] == "control-00" and row["week"] in {1.0, 2.0}:
            row["skill_score"] = None
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    result = evaluation.outcomes[0]
    assert evaluation.eligible is True
    assert result.denominator == 23
    assert result.excluded_count == 1
    assert result.missing_count == 2


def test_longitudinal_too_few_eligible_subjects_is_unverifiable() -> None:
    plan = compile_analysis_plan(longitudinal_contract())
    rows = [
        row
        for row in longitudinal_acceptance_rows()
        if row["arm"] == "control" or int(row["subject_id"].split("-")[1]) < 7
    ]
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    assert evaluation.eligible is False
    assert evaluation.primary_decision == "unverifiable"
    assert evaluation.outcomes[0].decision == "unverifiable"


def test_longitudinal_completion_exposes_owner_design_decisions() -> None:
    patch = study_design("longitudinal_repeated_measures_v1").complete_contract(
        {}, SimpleNamespace(study_design_spec={})
    )
    assert patch.owner_confirmation_bundle
    assert {item.field_path for item in patch.issues} >= {
        "study_design_spec.longitudinal",
        "study_design_spec.decision_rules",
        "study_design_spec.missingness",
    }


def test_longitudinal_formal_acceptance_uses_canonical_four_phase_workflow(
    tmp_path,
) -> None:
    prepared = prepare_longitudinal_workflow_acceptance(
        tmp_path / "formal-longitudinal"
    )
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    contract = repository.latest_research_contract(study_id)

    assert repository.latest_scope_contract(study_id).status is ArtifactStatus.FROZEN
    assert contract.status is ArtifactStatus.FROZEN
    assert contract.study_design["id"] == "longitudinal_repeated_measures_v1"
    assert contract.study_design_spec["unit_structure"]["independent_unit"] == "subject"
    assert any(
        gate.gate_type is GateType.RESEARCH_CONTRACT
        and gate.status is GateStatus.APPROVED
        for gate in repository.list_gates(study_id)
    )
    assert len(repository.list_result_envelopes(study_id)) == 2
    evaluations = repository.list_evaluation_records(study_id)
    assert len(evaluations) == 1
    assert evaluations[0].variance_unit == "subject"
    assert evaluations[0].independent_unit_count == 24
    assert evaluations[0].paired_effect == pytest.approx(1.2)
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
