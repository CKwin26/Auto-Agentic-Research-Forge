from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from research_forge.study_design.designs.online_ab import OnlineABTest
from research_forge.study_design.online_ab_case import (
    online_ab_acceptance_plan,
    online_ab_acceptance_rows,
)
from research_forge.study_design.reference import recalculate_independent_group
from research_forge.study_design.online_ab_workflow_acceptance import (
    prepare_online_ab_workflow_acceptance,
)
from research_forge.study_design.workflow_package import _artifact_path
from research_forge.storage import sha256_file
from research_forge.workflow_domain import WorkflowRepository


def _contract(payload: dict | None = None) -> SimpleNamespace:
    plan = payload or online_ab_acceptance_plan().model_dump(mode="json")
    return SimpleNamespace(study_design_spec=plan)


def test_online_ab_fixed_horizon_evaluates_exposed_units_and_srm() -> None:
    design = OnlineABTest()
    plan = online_ab_acceptance_plan()
    rows = online_ab_acceptance_rows()
    assert design.validate_contract(_contract()) == []
    evaluation = design.evaluate(plan, rows)
    primary = next(item for item in evaluation.outcomes if item.outcome_id == "conversion")
    assert evaluation.eligible is True
    assert evaluation.primary_decision == "supported"
    assert primary.denominator == 400
    assert primary.arm_statistics["control"]["events"] == 60
    assert primary.arm_statistics["treatment"]["events"] == 90
    assert evaluation.qualification_checks["sample_ratio_match_passed"] is True
    assert primary.details["srm_p_value"] == 1.0


def test_online_ab_rejects_early_peeking_duplicate_exposure_and_late_row() -> None:
    design = OnlineABTest()
    plan = online_ab_acceptance_plan()
    rows = online_ab_acceptance_rows()
    assert any("complete planned exposure count" in item for item in design.validate_realized_data(plan, rows[:-1]))

    duplicate = deepcopy(rows)
    duplicate[1]["exposure_id"] = duplicate[0]["exposure_id"]
    assert any("exposure identifiers must be unique" in item for item in design.validate_realized_data(plan, duplicate))

    late = deepcopy(rows)
    late[0]["exposure_timestamp"] = "2026-08-02T00:00:00+00:00"
    assert any("outside the frozen exposure window" in item for item in design.validate_realized_data(plan, late))


def test_online_ab_rejects_sample_ratio_mismatch_and_unfrozen_stopping() -> None:
    design = OnlineABTest()
    plan = online_ab_acceptance_plan()
    rows = online_ab_acceptance_rows()
    for index, row in enumerate(rows):
        row["arm"] = "control" if index < 350 else "treatment"
    assert any("sample-ratio mismatch" in item for item in design.validate_realized_data(plan, rows))

    payload = plan.model_dump(mode="json")
    payload["online_ab"]["stopping_rule"] = "Stop when desired"
    assert any("prohibit early" in item for item in design.validate_contract(_contract(payload)))


def test_online_ab_claims_preserve_exposure_and_no_peeking_boundary() -> None:
    design = OnlineABTest()
    plan = online_ab_acceptance_plan()
    evaluation = design.evaluate(plan, online_ab_acceptance_rows())
    envelope = design.produce_claim_envelope(plan, evaluation)
    assert envelope.design_details["analysis_mode"] == "fixed_horizon_no_peeking"
    assert any("interim monitoring" in item for item in envelope.prohibited_claims)
    assert any("first valid exposures" in item for item in envelope.permitted_claims)


def test_online_ab_independent_recalculator_matches_registered_effects() -> None:
    design = OnlineABTest()
    plan = online_ab_acceptance_plan()
    rows = online_ab_acceptance_rows()
    recorded = design.evaluate(plan, rows)
    reference = recalculate_independent_group(plan, rows)
    assert reference["primary_decision"] == recorded.primary_decision
    for result in recorded.outcomes:
        expected = reference["outcomes"][result.outcome_id]
        assert expected["effect"] == result.effect
        assert expected["confidence_interval"] == list(
            result.confidence_interval or []
        )


def test_online_ab_prepares_real_stage3_handoff_for_canonical_stage4(tmp_path) -> None:
    prepared = prepare_online_ab_workflow_acceptance(tmp_path)
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    assert prepared["scientific_verdict"] == "supported"
    assert prepared["stage_four_queued"] is True
    assert len(repository.list_evaluation_records(study_id)) == 2
    assert len(repository.list_result_envelopes(study_id)) == 2
    assert len(repository.list_stage3_completions(study_id)) == 1


def test_workflow_package_selects_latest_registered_artifact_version(tmp_path) -> None:
    prepared = prepare_online_ab_workflow_acceptance(tmp_path)
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    first = tmp_path / "reader-facing-v1.json"
    second = tmp_path / "reader-facing-v2.json"
    first.write_text('{"revision": 1}', encoding="utf-8")
    second.write_text('{"revision": 2}', encoding="utf-8")
    first_record = repository.register_artifact(
        study_id,
        str(first),
        sha256_file(first),
        kind="reader_facing_revision_probe",
        version=1,
    )
    repository.register_artifact(
        study_id,
        str(second),
        sha256_file(second),
        kind="reader_facing_revision_probe",
        version=2,
        predecessor_artifact_id=first_record.artifact_id,
    )

    assert _artifact_path(
        repository, study_id, "reader_facing_revision_probe"
    ) == second.resolve()
