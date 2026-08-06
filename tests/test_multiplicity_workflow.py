from __future__ import annotations

from research_forge.study_design import study_design
from research_forge.study_design.workflow_acceptance import (
    independent_group_acceptance_plan_rows,
    prepare_independent_group_workflow_acceptance,
)
from research_forge.workflow_domain import ArtifactStatus, WorkflowRepository


def test_multiplicity_fixture_freezes_and_reports_the_complete_family() -> None:
    plan, rows = independent_group_acceptance_plan_rows("multiplicity")
    evaluation = study_design("independent_group_comparison_v1").evaluate(
        plan, rows
    )

    assert plan.multiplicity.method == "holm"
    assert set(plan.multiplicity.hypothesis_ids) == {"quality", "completion"}
    assert {item.outcome_id for item in evaluation.outcomes} == {
        "quality",
        "completion",
    }
    assert all(item.adjusted_p_value is not None for item in evaluation.outcomes)


def test_multiplicity_workflow_is_a_distinct_study_and_contract(tmp_path) -> None:
    prepared = prepare_independent_group_workflow_acceptance(
        tmp_path / "multiplicity",
        acceptance_variant="multiplicity",
    )
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    study = repository.load_study(study_id)
    scope = repository.latest_scope_contract(study_id)
    contract = repository.latest_research_contract(study_id)

    assert prepared["acceptance_variant"] == "multiplicity"
    assert "Family-Wise Error Control" in study.title
    assert "Holm" in scope.research_question
    assert contract.status is ArtifactStatus.FROZEN
    assert {item["id"] for item in contract.inference_modules} == {
        "multiplicity_control_v1"
    }
    assert contract.statistical_rules["multiplicity"].startswith("Holm")
    assert prepared["scientific_verdict"] == "supported"
    assert prepared["stage_four_steps"] == 31

    claim = repository.list_claim_envelopes(study_id)[-1]
    assert "Holm adjustment" in claim.allowed_claim
    assert claim.maximum_claim_tier == (
        "controlled_familywise_adjusted_multi_outcome_result"
    )
