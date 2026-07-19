from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from research_forge.models import (
    Direction,
    LiteratureSource,
    LiteratureSourceType,
    ProjectState,
    Stage,
)
from research_forge.storage import load_state, read_json, write_json_atomic
from research_forge.cli import _project_status_with_study_overlay
from research_forge.study_models import (
    Stage2ArmDefinition,
    Stage2Cell,
    Stage2ManualAuditPlan,
    Stage2Protocol,
    Stage2TaskBinding,
    StudyArm,
)
from research_forge.study_synthesis import (
    ANALYSIS_STATUS,
    HUMAN_VALIDATION,
    audit_provisional_study_completion,
    audit_provisional_study_synthesis,
    complete_provisional_study,
    render_provisional_study_latex,
    synthesize_provisional_study,
)
from research_forge.synthesis import verify_completion_certificate


TASKS = ["task-alpha", "task-beta", "task-gamma"]


def _protocol() -> Stage2Protocol:
    tasks = [
        Stage2TaskBinding(
            task_id=task_id,
            snapshot_path=f"stage2/task_packs/{task_id}",
            task_hash=(str(index + 1) * 64)[:64],
            primary_metric="Accuracy",
            direction=Direction.MAXIMIZE,
            baseline_score=0.5,
            target_score=0.9,
            iteration_cap=1,
            timeout_seconds=120,
        )
        for index, task_id in enumerate(TASKS)
    ]
    cells = []
    sequence = 1
    for arm in (StudyArm.BASELINE, StudyArm.TREATMENT):
        for task_id in TASKS:
            for seed in (0, 1, 2):
                cells.append(
                    Stage2Cell(
                        sequence=sequence,
                        cell_id=f"{arm.value}--{task_id}--seed-{seed}",
                        arm=arm,
                        task_id=task_id,
                        seed=seed,
                    )
                )
                sequence += 1
    return Stage2Protocol(
        protocol_revision=4,
        protocol_id="stage2-123456789abc",
        title="Paired claim-evidence gate ablation",
        research_question=(
            "Does a pre-delivery claim-evidence gate reduce unsupported final research claims?"
        ),
        hypothesis=(
            "The gate will reduce unsupported claims while preserving bounded task-native performance."
        ),
        plan_id="plan-test",
        plan_hash="a" * 64,
        review_id="review-test",
        review_hash="b" * 64,
        selected_novelty_id="novelty-02",
        backbone_manifest_hash="c" * 64,
        arms=[
            Stage2ArmDefinition(
                arm=StudyArm.BASELINE,
                initial_finalizer=(
                    "Use the shared finalizer without verifier rejection or claim revision."
                ),
                verification_gate="disabled",
                revision_limit=0,
                removal_after_failed_recheck=False,
            ),
            Stage2ArmDefinition(
                arm=StudyArm.TREATMENT,
                initial_finalizer=(
                    "Use the shared finalizer before applying one verifier-guided revision cycle."
                ),
                verification_gate="reject_revise_recheck",
                revision_limit=1,
                removal_after_failed_recheck=True,
            ),
        ],
        tasks=tasks,
        seeds=[0, 1, 2],
        cells=cells,
        secondary_metrics=[
            "citation_correctness",
            "experiment_detail_error_rate",
            "evidence_coverage",
            "task_native_score",
            "wall_clock_runtime_seconds",
            "verifier_abstention_rate",
            "failure_mode_count",
            "audit_false_positive_rate",
        ],
        manual_audit=Stage2ManualAuditPlan(),
        stop_conditions=[
            "Stop after exactly 18 completed cells.",
            "Stop and fail closed on any integrity drift.",
        ],
    )


def _summary() -> dict[str, object]:
    baseline = {
        "abstained_claims": 1,
        "citation_correctness": 2 / 3,
        "evidence_coverage": 1.0,
        "experiment_detail_error_rate": 1 / 3,
        "final_claims": 36,
        "initial_claims": 36,
        "mean_task_native_score": 0.60,
        "registries": 9,
        "retention_rate": 1.0,
        "total_wall_clock_seconds": 100.0,
        "unsupported_claim_rate": 11 / 36,
        "unsupported_claims": 11,
        "verifier_abstention_rate": 1 / 36,
    }
    treatment = {
        "abstained_claims": 0,
        "citation_correctness": 8 / 9,
        "evidence_coverage": 1.0,
        "experiment_detail_error_rate": 2 / 36,
        "final_claims": 36,
        "initial_claims": 36,
        "mean_task_native_score": 0.62,
        "registries": 9,
        "retention_rate": 1.0,
        "total_wall_clock_seconds": 165.0,
        "unsupported_claim_rate": 3 / 36,
        "unsupported_claims": 3,
        "verifier_abstention_rate": 0.0,
    }
    return {
        "schema_version": 1,
        "protocol_id": "stage2-123456789abc",
        "analysis_status": "protected_evaluator_complete_manual_audit_pending",
        "primary_analysis_interpretable": False,
        "arm_metrics": {"baseline": baseline, "treatment": treatment},
        "paired_analysis": {
            "effect_definition": "treatment_minus_baseline",
            "mean_paired_unsupported_claim_rate_effect": -2 / 9,
            "paired_cohen_dz": -0.84,
            "tasks_with_lower_unsupported_claim_rate": 3,
            "task_effects": {task_id: -0.2 for task_id in TASKS},
            "task_native_score_effects": {task_id: 0.0 for task_id in TASKS},
            "task_native_score_within_absolute_0_02": True,
            "pairs": [],
            "hierarchical_bootstrap": {
                "resamples": 10_000,
                "seed": 123,
                "ci95_low": -0.42,
                "ci95_high": -0.08,
                "median": -2 / 9,
            },
        },
    }


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "paired-study"
    (project / "literature" / "sources").mkdir(parents=True)
    (project / "stage2" / "evaluations").mkdir(parents=True)
    (project / "stage2" / "persona_panel" / "panel-v1-full").mkdir(parents=True)
    write_json_atomic(
        project / "state.json",
        ProjectState(
            stage=Stage.RESULT_REVIEW,
            revision=15,
            baseline_run_id="baseline-matrix-stage2-123456789abc",
            run_count=18,
        ),
    )
    (project / "events.jsonl").write_text("", encoding="utf-8")
    write_json_atomic(project / "stage2" / "frozen_manifest.json", {"hashes": {}})
    write_json_atomic(project / "literature" / "stage1_manifest.json", {"hashes": {}})
    write_json_atomic(project / "literature" / "review.json", {"review_id": "review-test"})
    source = LiteratureSource(
        source_id="paper-test",
        source_type=LiteratureSourceType.PAPER,
        title="Evidence verification for autonomous research agents",
        authors=["Test Author"],
        year=2025,
        locator="https://example.test/paper",
        notes="A bounded test source about evidence verification.",
        verified=True,
        verification_method="Verified fixture metadata.",
    )
    write_json_atomic(project / "literature" / "sources" / "paper-test.json", source)
    write_json_atomic(project / "stage2" / "protocol.json", _protocol())
    write_json_atomic(
        project / "stage2" / "backbone_manifest.json",
        {"model": "codex:test", "controller_hashes": {}},
    )
    write_json_atomic(project / "stage2" / "evaluations" / "summary.json", _summary())
    write_json_atomic(
        project / "stage2" / "manual_audit_manifest.json",
        {"status": "awaiting_two_independent_auditors", "actual_total": 48},
    )
    write_json_atomic(
        project / "stage2" / "operator_decisions.json",
        {
            "schema_version": 1,
            "protocol_id": "stage2-123456789abc",
            "decisions": [
                {
                    "decision_id": "operator-decision-defer-human-validation",
                    "decision": "defer_preregistered_human_validation",
                    "status": "active",
                    "scope": "current_stage_only",
                    "protocol_effect": "none",
                }
            ],
        },
    )
    write_json_atomic(
        project
        / "stage2"
        / "persona_panel"
        / "panel-v1-full"
        / "final.json",
        {
            "ensemble_label": "same-model persona ensemble",
            "item_count": 48,
            "verdict_counts": {"supported": 37, "unsupported": 11},
            "disagreement_count": 4,
            "human_adjudication_queue": ["a", "b", "c", "d"],
        },
    )
    return project


def _patch_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "research_forge.study_synthesis.audit_stage1",
        lambda _project: SimpleNamespace(passed=True, violations=[]),
    )
    monkeypatch.setattr(
        "research_forge.study_synthesis.audit_stage2_protocol",
        lambda _project: SimpleNamespace(passed=True, violations=[]),
    )
    monkeypatch.setattr(
        "research_forge.study_synthesis.audit_stage2_evaluation",
        lambda _project: SimpleNamespace(
            passed=True, complete=True, evaluated_registries=18, violations=[]
        ),
    )
    monkeypatch.setattr(
        "research_forge.study_synthesis.audit_manual_audit",
        lambda _project: {
            "passed": True,
            "complete": False,
            "status": "awaiting_two_independent_auditors",
        },
    )


def test_provisional_paired_study_closes_four_stage_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _patch_audits(monkeypatch)

    synthesis = synthesize_provisional_study(project)
    assert synthesis.passed
    assert synthesis.publication_ready is False
    assert synthesis.claim_count >= 10
    assert load_state(project).stage == Stage.SYNTHESIS

    analysis = read_json(project / "stage2" / "provisional_analysis.json")
    assert analysis["analysis_status"] == ANALYSIS_STATUS
    assert analysis["human_validation"] == HUMAN_VALIDATION
    assert analysis["primary_analysis_interpretable"] is False
    assert not (project / "stage2" / "final_analysis.json").exists()
    manuscript = (project / "synthesis" / "manuscript.md").read_text(encoding="utf-8")
    assert "pipeline-complete but not publication-ready" in manuscript
    assert "## Results" in manuscript
    latex = (project / "synthesis" / "manuscript.tex").read_text(encoding="utf-8")
    assert r"\documentclass" in latex
    assert r"\section{Results}" in latex
    assert r"30.56\%" in latex

    completed = complete_provisional_study(project)
    assert completed["completion_audit"]["passed"]
    assert load_state(project).stage == Stage.COMPLETED
    assert verify_completion_certificate(project)["passed"]
    assert verify_completion_certificate(project)["publication_ready"] is False
    certificate = read_json(project / "completion_certificate.json")
    assert "synthesis/manuscript.tex" in certificate["artifact_hashes"]
    status = read_json(project / "stage2" / "closed_loop_status.json")
    assert status["pipeline_complete"] is True
    assert status["human_validation"] == HUMAN_VALIDATION
    assert all(status["four_stage_gates"].values())
    assert audit_provisional_study_completion(project)["passed"]
    refreshed = render_provisional_study_latex(project)
    assert refreshed["completion_audit"]["passed"]

    monkeypatch.setattr(
        "research_forge.cli.project_status",
        lambda _project: {
            "macro_stage": "stage_4_synthesis",
            "four_stage_gates": {
                "stage_3_experimentation": {
                    "passed": False,
                    "best_run_id": None,
                }
            },
        },
    )
    overlaid = _project_status_with_study_overlay(project)
    assert overlaid["four_stage_gates"]["stage_3_experimentation"]["passed"]
    assert overlaid["study_closed_loop"]["verification"]["passed"]


def test_provisional_synthesis_fails_closed_on_missing_deferral_or_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _patch_audits(monkeypatch)
    decisions = read_json(project / "stage2" / "operator_decisions.json")
    decisions["decisions"][0]["status"] = "revoked"
    write_json_atomic(project / "stage2" / "operator_decisions.json", decisions)
    with pytest.raises(ValueError, match="active human-validation deferral"):
        synthesize_provisional_study(project)

    project = _project(tmp_path / "second")
    _patch_audits(monkeypatch)
    assert synthesize_provisional_study(project).passed
    analysis = read_json(project / "stage2" / "provisional_analysis.json")
    analysis["arm_metrics"]["treatment"]["unsupported_claim_rate"] = 0.99
    write_json_atomic(project / "stage2" / "provisional_analysis.json", analysis)
    audit = audit_provisional_study_synthesis(project)
    assert not audit.passed
    assert not audit.checks["provisional_analysis_exact"]
    assert not audit.checks["claims_match_frozen_evidence"]
