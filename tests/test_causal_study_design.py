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
from research_forge.study_design.causal_case import (
    causal_acceptance_plan,
    causal_acceptance_rows,
)
from research_forge.study_design.causal_reference import recalculate_causal
from research_forge.study_design.causal_workflow_acceptance import (
    prepare_causal_workflow_acceptance,
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


def causal_contract(payload: dict | None = None):
    return SimpleNamespace(
        study_design={"id": "causal_inference_v1", "version": "1"},
        inference_modules=[],
        study_design_spec=causal_acceptance_plan() if payload is None else payload,
    )


def test_causal_profile_is_registered_with_verified_c3_acceptance() -> None:
    catalog = {item["design_id"]: item for item in study_design_catalog()}
    profile = catalog["causal_inference_v1"]
    assert profile["maturity"] == "c2_dry_run"
    assert profile["verified_maturity"] == "c3_real_fixture"
    assert profile["acceptance_report_present"] is True
    assert profile["acceptance_study_id"] == "study-56d8027d9fa47e5a"
    assert profile["acceptance_hash"] == (
        "597c0a307c86b44195efa5200f89b43621daaa7b48a3d982bfe3901a254f902e"
    )
    assert profile["formal_execution_supported"] is True
    limits = " ".join(profile["known_limits"])
    assert "backdoor" in limits
    assert "no IV" in limits


def test_causal_kernel_estimates_frozen_cross_fitted_aipw_ate() -> None:
    plan = compile_analysis_plan(causal_contract())
    rows = causal_acceptance_rows()
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    result = evaluation.outcomes[0]

    assert evaluation.eligible is True
    assert evaluation.primary_decision == "supported"
    assert result.effect == pytest.approx(2.421993279071317, abs=1e-10)
    assert result.denominator == 240
    assert result.details["overlap_fraction"] == 1.0
    assert result.details["adjustment_set"] == [
        "baseline_difficulty",
        "prior_experience",
    ]

    envelope = produce_claim_envelope(plan, evaluation)
    assert envelope.allocation_verified_randomized is False
    assert "conditional exchangeability" in " ".join(envelope.permitted_claims)
    assert "hidden confounding" in " ".join(envelope.prohibited_claims)
    assert envelope.design_details["identification_strategy"] == "backdoor_adjustment"


def test_independent_causal_recalculator_matches_production() -> None:
    plan = compile_analysis_plan(causal_contract())
    rows = causal_acceptance_rows()
    production = study_design(plan.study_design_id).evaluate(plan, rows).outcomes[0]
    reference = recalculate_causal(plan, rows)

    assert reference["eligible"] is True
    assert reference["effect"] == pytest.approx(production.effect, abs=1e-10)
    assert reference["standard_error"] == pytest.approx(
        production.standard_error, abs=1e-10
    )
    assert reference["confidence_interval"] == pytest.approx(
        production.confidence_interval, abs=1e-10
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("causal", None),
        lambda value: value["allocation"].__setitem__("mechanism", "randomized"),
        lambda value: value["outcomes"][0].__setitem__("kind", "binary"),
        lambda value: value["estimands"][0].__setitem__(
            "effect_measure", "mean_difference"
        ),
        lambda value: value["estimator_plan"]["task_quality"].__setitem__(
            "estimator_id", "welch_mean_difference_v1"
        ),
        lambda value: value["missingness"].__setitem__("policy", "complete_case"),
    ],
)
def test_causal_contract_mutations_fail_closed(mutation) -> None:
    payload = deepcopy(causal_acceptance_plan())
    mutation(payload)
    assert validate_composable_contract(causal_contract(payload))


def test_causal_graph_cycle_and_post_treatment_adjustment_fail_schema() -> None:
    cycle = deepcopy(causal_acceptance_plan())
    cycle["causal"]["causal_graph_edges"].append(
        {"source": "quality_score", "target": "baseline_difficulty"}
    )
    assert validate_composable_contract(causal_contract(cycle))

    forbidden = deepcopy(causal_acceptance_plan())
    forbidden["causal"]["adjustment_set"].append("post_treatment_effort")
    assert validate_composable_contract(causal_contract(forbidden))


def test_missing_required_causal_value_is_unverifiable() -> None:
    plan = compile_analysis_plan(causal_contract())
    rows = causal_acceptance_rows()
    rows[0]["baseline_difficulty"] = None
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    assert evaluation.eligible is False
    assert evaluation.primary_decision == "unverifiable"
    assert "missing required causal fields" in " ".join(evaluation.limitations)


def test_empirical_positivity_failure_is_unverifiable() -> None:
    plan = compile_analysis_plan(causal_contract())
    rows = causal_acceptance_rows()
    for row in rows:
        exposed = int(float(row["baseline_difficulty"]) > 0)
        row["guidance_enabled"] = exposed
        row["arm"] = "guided" if exposed else "unguided"
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    assert evaluation.eligible is False
    assert evaluation.primary_decision == "unverifiable"
    assert evaluation.outcomes[0].details["overlap_fraction"] < 0.95


def test_duplicate_subject_is_rejected_before_estimation() -> None:
    plan = compile_analysis_plan(causal_contract())
    rows = causal_acceptance_rows()
    rows[1]["session_id"] = rows[0]["session_id"]
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    assert evaluation.eligible is False
    assert "exactly once" in " ".join(evaluation.limitations)


def test_causal_formal_acceptance_uses_canonical_four_phase_workflow(
    tmp_path,
) -> None:
    prepared = prepare_causal_workflow_acceptance(tmp_path / "formal-causal")
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    contract = repository.latest_research_contract(study_id)

    assert repository.latest_scope_contract(study_id).status is ArtifactStatus.FROZEN
    assert contract.status is ArtifactStatus.FROZEN
    assert contract.study_design["id"] == "causal_inference_v1"
    spec = contract.study_design_spec
    assert spec["allocation"]["mechanism"] == "observational"
    assert spec["causal"]["adjustment_set"] == [
        "baseline_difficulty",
        "prior_experience",
    ]
    assert any(
        gate.gate_type is GateType.RESEARCH_CONTRACT
        and gate.status is GateStatus.APPROVED
        for gate in repository.list_gates(study_id)
    )
    assert len(repository.list_result_envelopes(study_id)) == 2
    evaluations = repository.list_evaluation_records(study_id)
    assert len(evaluations) == 1
    assert evaluations[0].variance_unit == "task session"
    assert evaluations[0].independent_unit_count == 240
    assert evaluations[0].paired_effect == pytest.approx(2.421993279071317)
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
    assert "cross-fitted AIPW" in method_statement
    assert "baseline difficulty" in method_statement
    assert "prior experience" in method_statement
    assert "overlap fraction: 1" in method_statement
    protocol_statement = next(
        item["statement"]
        for item in map_payload["bindings"]
        if item["claim_id"].endswith(":registered-protocol")
    )
    assert "ridge-stabilized IRLS" in protocol_statement
    assert "arm-specific ordinary least squares" in protocol_statement
    assert "not a mathematically exact zero" in protocol_statement
    assert "treatment-version consistency cannot be assessed" in protocol_statement

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
