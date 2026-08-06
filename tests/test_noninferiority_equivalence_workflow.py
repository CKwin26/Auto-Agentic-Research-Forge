from __future__ import annotations

from research_forge.study_design import study_design
from research_forge.study_design.inference.noninferiority import interval_decision
from research_forge.study_design.mutations import build_authority_mutation_report
from research_forge.study_design.workflow_acceptance import (
    independent_group_acceptance_plan_rows,
    prepare_independent_group_workflow_acceptance,
)
from research_forge.workflow_domain import ArtifactStatus, WorkflowRepository


def test_equivalence_fixture_is_not_a_disguised_superiority_result() -> None:
    plan, rows = independent_group_acceptance_plan_rows("equivalence")
    arm_definitions = {arm.arm_id: arm.definition for arm in plan.arms}
    assert "within-arm rank modulo 10" in arm_definitions["control"]
    assert "within-arm rank modulo 10" in arm_definitions["treatment"]
    assert "case index" not in arm_definitions["control"].lower()
    assert "case index" not in arm_definitions["treatment"].lower()
    for arm_id, quality_base, completion_divisor in (
        ("control", 50.0, 5),
        ("treatment", 49.8, 8),
    ):
        arm_rows = sorted(
            (row for row in rows if row["arm"] == arm_id),
            key=lambda row: row["subject_id"],
        )
        for arm_rank, row in enumerate(arm_rows):
            if row["quality"] not in {None, ""}:
                assert row["quality"] == quality_base + 0.7 * (arm_rank % 10)
            assert row["completed"] is (arm_rank % completion_divisor != 0)

    evaluation = study_design("independent_group_comparison_v1").evaluate(plan, rows)
    primary = next(item for item in evaluation.outcomes if item.outcome_id == "quality")
    interval = tuple(primary.confidence_interval or ())

    assert evaluation.primary_decision == "supported"
    assert interval[0] < 0 < interval[1]
    assert interval_decision(
        interval,
        mode="equivalence",
        direction="higher",
        lower_margin=-1.0,
        upper_margin=1.0,
    ) == "supported"
    assert interval_decision(
        interval,
        mode="noninferiority",
        direction="higher",
        margin=1.0,
    ) == "supported"
    assert interval_decision(
        interval,
        mode="superiority",
        direction="higher",
    ) == "inconclusive"
    mutation_report = build_authority_mutation_report(plan, rows)
    assert mutation_report["change_outcome_value"]["passed"] is True


def test_equivalence_workflow_freezes_margins_before_formal_execution(
    tmp_path,
) -> None:
    prepared = prepare_independent_group_workflow_acceptance(
        tmp_path / "equivalence",
        acceptance_variant="equivalence",
    )
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    contract = repository.latest_research_contract(study_id)
    rule = contract.study_design_spec["decision_rules"]["quality"]

    assert contract.status is ArtifactStatus.FROZEN
    assert {item["id"] for item in contract.inference_modules} >= {
        "noninferiority_equivalence_v1"
    }
    assert rule["mode"] == "equivalence"
    assert rule["lower_margin"] == -1.0
    assert rule["upper_margin"] == 1.0
    assert rule["owner_approved"] is True
    assert "non-significance" in contract.statistical_rules["success_rule"]
    assert prepared["acceptance_variant"] == "equivalence"
    assert prepared["scientific_verdict"] == "supported"
    assert prepared["stage_four_steps"] == 31

    claim = repository.list_claim_envelopes(study_id)[-1]
    assert "equivalence margins" in claim.allowed_claim
    assert "does not establish superiority" in claim.allowed_claim
    assert "superiority" in claim.prohibited_generalizations
