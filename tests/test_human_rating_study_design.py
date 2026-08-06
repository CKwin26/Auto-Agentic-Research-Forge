from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from research_forge.study_design.designs.human_rating import (
    OpenGenerationHumanRating,
)
from research_forge.study_design.human_rating_case import (
    human_rating_acceptance_plan,
    human_rating_acceptance_rows,
)
from research_forge.study_design.human_rating_reference import (
    recalculate_human_rating,
)
from research_forge.study_design.human_rating_workflow_acceptance import (
    prepare_human_rating_workflow_acceptance,
)
from research_forge.workflow_domain import WorkflowRepository


def _contract(payload: dict | None = None) -> SimpleNamespace:
    plan = payload or human_rating_acceptance_plan().model_dump(mode="json")
    return SimpleNamespace(study_design_spec=plan)


def test_blinded_human_rating_evaluates_prompt_level_pairs() -> None:
    design = OpenGenerationHumanRating()
    plan = human_rating_acceptance_plan()
    rows = human_rating_acceptance_rows()
    assert design.validate_contract(_contract()) == []
    evaluation = design.evaluate(plan, rows)
    result = evaluation.outcomes[0]
    assert evaluation.eligible is True
    assert evaluation.primary_decision == "supported"
    assert result.denominator == 40
    assert result.details["rating_rows"] == 240
    assert result.details["frozen_outputs"] == 80
    assert result.details["inter_rater_reliability"] >= 0.75


def test_human_rating_rejects_incomplete_panel_duplicate_and_unblinding() -> None:
    design = OpenGenerationHumanRating()
    plan = human_rating_acceptance_plan()
    rows = human_rating_acceptance_rows()

    incomplete = deepcopy(rows)
    incomplete.pop()
    assert any(
        "complete registered rater panel" in item
        for item in design.validate_realized_data(plan, incomplete)
    )

    duplicate = deepcopy(rows)
    duplicate.append(deepcopy(duplicate[0]))
    assert any(
        "cannot score the same frozen output twice" in item
        for item in design.validate_realized_data(plan, duplicate)
    )

    unblinded = deepcopy(rows)
    unblinded[0]["blind_label"] = "treatment"
    assert any(
        "blind-label ledger" in item
        for item in design.validate_realized_data(plan, unblinded)
    )


def test_human_rating_rejects_pseudoreplication_and_unfrozen_panel() -> None:
    design = OpenGenerationHumanRating()
    payload = human_rating_acceptance_plan().model_dump(mode="json")
    payload["unit_structure"]["analysis_unit"] = "rating row"
    assert any(
        "independent analysis unit" in item
        for item in design.validate_contract(_contract(payload))
    )

    payload = human_rating_acceptance_plan().model_dump(mode="json")
    payload["human_rating"]["rater_panel_ids"] = ["rater-01", "rater-02"]
    assert any(
        "raters_per_output must equal" in item
        for item in design.validate_contract(_contract(payload))
    )


def test_human_rating_claims_preserve_rubric_and_independence_boundary() -> None:
    design = OpenGenerationHumanRating()
    plan = human_rating_acceptance_plan()
    evaluation = design.evaluate(plan, human_rating_acceptance_rows())
    envelope = design.produce_claim_envelope(plan, evaluation)
    assert envelope.design_details["rubric_id"] == "usefulness-rubric-v1"
    assert any("rating rows" in item for item in envelope.prohibited_claims)
    assert any("factual accuracy" in item for item in envelope.prohibited_claims)
    assert any("inter-rater reliability" in item for item in envelope.permitted_claims)


def test_human_rating_independent_recalculator_matches_registered_effect() -> None:
    design = OpenGenerationHumanRating()
    plan = human_rating_acceptance_plan()
    rows = human_rating_acceptance_rows()
    recorded = design.evaluate(plan, rows)
    reference = recalculate_human_rating(plan, rows)
    result = recorded.outcomes[0]
    expected = reference["outcomes"]["usefulness_rating"]
    assert reference["primary_decision"] == recorded.primary_decision
    assert expected["effect"] == result.effect
    assert expected["confidence_interval"] == list(result.confidence_interval or [])
    assert reference["inter_rater_reliability"] == result.details[
        "inter_rater_reliability"
    ]


def test_human_rating_prepares_real_stage3_handoff_for_canonical_stage4(tmp_path) -> None:
    prepared = prepare_human_rating_workflow_acceptance(tmp_path)
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    assert prepared["scientific_verdict"] == "supported"
    assert prepared["stage_four_queued"] is True
    assert len(repository.list_evaluation_records(study_id)) == 1
    assert len(repository.list_result_envelopes(study_id)) == 2
    assert len(repository.list_stage3_completions(study_id)) == 1
