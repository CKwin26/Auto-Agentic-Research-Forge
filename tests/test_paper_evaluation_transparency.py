from __future__ import annotations

from pathlib import Path

from research_forge.paper_evaluation_transparency import (
    TransparencyStatus,
    assess_stage4_evidence_sufficiency,
    audit_evaluation_transparency_coverage,
    build_evaluation_transparency_register,
    infer_declared_transparency_item_ids,
    restore_required_transparency_disclosures,
)
from research_forge.workflow_domain import (
    EntryMode,
    ExecutionStatus,
    ExecutorType,
    Phase,
    WorkflowRepository,
)
from research_forge.workflow_scheduler import (
    BlockedStepError,
    PersistentDAGScheduler,
)


def _register(**overrides):
    values = {
        "study_id": "study-transparency-test",
        "source_claim_envelope_id": "claim-envelope-test",
        "evaluation_path": "stage3/evaluations/evaluation-test.json",
        "contract_path": "contracts/research-v1.json",
        "evaluator_policy": {
            "type": "DeBERTa NLI",
            "contradiction_threshold": 0.7,
            "threshold_selection": {
                "source": "locked_public_calibration",
            },
            "numeric_tolerance": 1e-12,
        },
        "total_records": 10,
        "eligible_records": 10,
        "excluded_record_ids": [],
        "exclusion_reasons": {},
        "abstention_count": 1,
        "arm_estimates": {"control": 0.5, "treatment": 0.5},
        "repair_present": False,
        "prospective_successor_present": False,
        "available_artifact_kinds": set(),
    }
    values.update(overrides)
    return build_evaluation_transparency_register(**values)


def test_transparency_register_preserves_operational_counts_and_gaps() -> None:
    register = _register()
    by_id = {item.item_id: item for item in register.items}
    assert register.frozen
    assert by_id["transparency-sample-flow"].status is TransparencyStatus.AVAILABLE
    assert "10 records" in by_id["transparency-sample-flow"].statement
    assert by_id["transparency-abstentions"].status is TransparencyStatus.AVAILABLE
    assert (
        by_id["transparency-threshold-provenance"].status
        is TransparencyStatus.AVAILABLE
    )
    assert (
        by_id["transparency-verifier-ablation"].status
        is TransparencyStatus.MISSING
    )
    assert by_id["transparency-verifier-ablation"].follow_up_action


def test_nli_risk_alert_does_not_activate_learned_evaluator_requirements() -> None:
    register = _register(
        evaluator_policy={
            "authority_order": [
                "deterministic_integrity_checks",
                "nli_risk_alert",
            ],
            "uses_learned_evaluator": False,
        }
    )
    categories = {item.category for item in register.items}
    assert "evaluator_sensitivity" not in categories
    assert "verifier_ablation" not in categories
    assert "packet_example" not in categories
    assert "semantic_preservation" not in categories


def test_numeric_precision_statement_does_not_call_different_arms_equal() -> None:
    register = _register(
        arm_estimates={"baseline": 0.5, "treatment": 1.0},
        evaluator_policy={},
    )
    item = next(
        item
        for item in register.items
        if item.item_id == "transparency-numeric-precision"
    )

    assert item.status is TransparencyStatus.AVAILABLE
    assert item.statement.startswith("Frozen arm estimates differ:")
    assert "exactly equal" not in item.statement


def test_stage4_missing_evidence_routes_to_stage3_successor() -> None:
    register = _register(
        evaluator_policy={
            "type": "DeBERTa NLI",
            "contradiction_threshold": 0.7,
        },
        total_records=10,
        eligible_records=8,
        excluded_record_ids=["cell-1", "cell-2"],
        exclusion_reasons={},
        repair_present=True,
        prospective_successor_present=False,
    )
    decision = assess_stage4_evidence_sufficiency(register)
    assert decision.status == "stage3_backfill_required"
    assert {
        "transparency-eligibility-breakdown",
        "transparency-threshold-provenance",
        "transparency-prospective-validation",
    } <= set(decision.required_item_ids)
    assert {"eligibility_rules", "evaluator_policy", "tasks", "seeds"} <= set(
        decision.changed_contract_fields
    )
    assert decision.historical_verdict_must_be_preserved is True


def test_noncritical_missing_analyses_are_disclosed_without_fake_data() -> None:
    register = _register()
    decision = assess_stage4_evidence_sufficiency(register)
    assert decision.status == "disclosure_only"
    assert "transparency-verifier-ablation" in decision.disclosure_item_ids
    assert decision.required_item_ids == []


def test_contract_can_make_all_missing_items_disclosure_only() -> None:
    register = _register(repair_present=True)
    decision = assess_stage4_evidence_sufficiency(
        register,
        required_backfill_categories=set(),
    )
    assert decision.status == "disclosure_only"
    assert decision.required_item_ids == []
    assert "transparency-prospective-validation" in (
        decision.disclosure_item_ids
    )


def test_transparency_coverage_requires_every_material_item() -> None:
    register = _register()
    material_ids = [item.item_id for item in register.material_items()]
    passed = audit_evaluation_transparency_coverage(
        register,
        declared_item_ids=material_ids,
    )
    assert passed.passed
    failed = audit_evaluation_transparency_coverage(
        register,
        declared_item_ids=material_ids[:-1],
    )
    assert not failed.passed
    assert failed.missing_item_ids == [material_ids[-1]]


def test_reader_facing_disclosures_do_not_require_internal_item_ids() -> None:
    register = _register()
    declared = infer_declared_transparency_item_ids(
        register,
        sections={
            "methods": (
                "All ten records were assigned and retained for analysis; "
                "one evaluator abstention was recorded."
            ),
            "results": "Values are reported at frozen decimal precision.",
            "limitations": (
                "The public packet schema and release assets remain unavailable."
            ),
        },
    )

    assert "transparency-sample-flow" in declared
    assert "transparency-abstentions" in declared
    assert "transparency-numeric-precision" in declared
    assert "transparency-release-assets" in declared


def test_missing_reader_disclosures_are_restored_from_frozen_register() -> None:
    register = _register()
    repaired, restored = restore_required_transparency_disclosures(
        register,
        sections={
            "methods": "The evaluation followed the frozen protocol.",
            "results": "The registered comparison was evaluated.",
            "limitations": "The study has bounded scope.",
        },
    )

    assert restored
    material_ids = {item.item_id for item in register.material_items()}
    initially_declared = set(
        infer_declared_transparency_item_ids(
            register,
            sections={
                "methods": "The evaluation followed the frozen protocol.",
                "results": "The registered comparison was evaluated.",
                "limitations": "The study has bounded scope.",
            },
        )
    )
    assert material_ids <= initially_declared | set(restored)
    assert repaired["limitations"] != "The study has bounded scope."


def test_scheduler_redirects_evidence_backfill_to_stage3(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Stage 4 backfill")
    study = repository.create_study(
        project.project_id,
        "Missing prospective evidence",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    step = repository.add_step(
        study.study_id,
        "stage4_evidence_sufficiency_gate",
        Phase.PAPER,
        ExecutorType.DETERMINISTIC_EVALUATOR,
    )

    def require_backfill(_context):
        raise BlockedStepError(
            "fresh evidence is required",
            kind="stage3_evidence_backfill_required",
            redirect_phase=Phase.EXPERIMENT,
        )

    PersistentDAGScheduler(
        repository,
        {"stage4_evidence_sufficiency_gate": require_backfill},
    ).run(study.study_id)

    updated_step = repository.load_step(
        study.study_id,
        step.step_instance_id,
    )
    updated_study = repository.load_study(study.study_id)
    assert updated_step.status is ExecutionStatus.BLOCKED
    assert updated_step.blocker == {
        "kind": "stage3_evidence_backfill_required",
        "message": "fresh evidence is required",
        "redirect_phase": "experiment",
    }
    assert updated_study.phase is Phase.EXPERIMENT
