from __future__ import annotations

from pathlib import Path

from research_forge.storage import read_json
from research_forge.study_design.workflow_acceptance import (
    prepare_independent_group_workflow_acceptance,
)
from research_forge.workflow_domain import WorkflowRepository


def test_bayesian_sensitivity_is_frozen_bound_and_non_authoritative(tmp_path) -> None:
    prepared = prepare_independent_group_workflow_acceptance(
        tmp_path / "bayesian",
        acceptance_variant="bayesian",
    )
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    contract = repository.latest_research_contract(study_id)
    module = next(
        item
        for item in contract.inference_modules
        if item["id"] == "bayesian_inference_v1"
    )
    extension = module["extension"]

    assert extension["prior"] == {"family": "Beta", "alpha": 1.0, "beta": 1.0}
    assert extension["seed"] == 20260804
    assert extension["draws"] == 20_000
    assert extension["decision_threshold"] == 0.90
    assert extension["role"] == "prespecified_sensitivity_only"

    records = repository.list_evaluation_records(study_id)
    bayesian = next(
        item for item in records if item.metric_name.startswith("Bayesian sensitivity")
    )
    assert len(records) == 3
    assert bayesian.decision.value == "inconclusive"
    assert bayesian.statistical_rule["interval_kind"] == "95% posterior credible interval"
    assert bayesian.statistical_rule["sensitivity_only"] is True
    assert bayesian.contrast_estimates["posterior_sensitivity"][
        "probability_effect_above_zero"
    ] < 0.90

    claim = repository.list_claim_envelopes(study_id)[-1]
    assert "did not change the primary verdict" in claim.allowed_claim
    assert any("credible interval" in item for item in claim.prohibited_generalizations)
    assert prepared["scientific_verdict"] == "supported"

    handoff = read_json(
        Path(prepared["workflow_repository"])
        / "studies"
        / study_id
        / "stage4"
        / "stage_four_evidence_handoff_v1.json"
    )
    result_statement = next(
        item["statement"]
        for item in handoff["evidence_claim_map"]["bindings"]
        if item["claim_id"] == f"evaluation:{bayesian.evaluation_id}:result"
    )
    assert "posterior credible interval" in result_statement
    assert "confidence interval" not in result_statement
