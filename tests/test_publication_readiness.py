from __future__ import annotations

from pathlib import Path

import pytest

from research_forge.publication_readiness import (
    audit_publication_experiment_design,
    audit_publication_readiness,
    build_system_design_repair_report,
    create_publication_target,
    freeze_publication_experiment_target,
    persist_publication_readiness,
    persist_system_design_repair,
    stage2_design_repair_violations,
)


ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPT = ROOT / "output" / "pdf" / "research-agent-evidence-v4-paper-en.tex"
PROJECT = ROOT / "stage1_runs" / "research-agent-evidence-v2"
VENUE = "research-integrity-and-peer-review"
VENUE_REPORT = PROJECT / "synthesis" / "venue_recommendation.json"


def _target(tmp_path: Path, *, threshold: float = 0.60):
    return create_publication_target(
        MANUSCRIPT,
        project=PROJECT,
        venue_id=VENUE,
        readiness_threshold=threshold,
        contract_path=tmp_path / "target.json",
    )


def test_current_v4_passes_depth_but_fails_scientific_submission_gate(tmp_path: Path) -> None:
    target, target_path = _target(tmp_path)
    report = audit_publication_readiness(
        target,
        manuscript=MANUSCRIPT,
        project=PROJECT,
        contract_path=target_path,
    )
    dimensions = {item.id: item for item in report.dimensions}

    assert 0.45 < report.readiness_score < report.readiness_threshold
    assert report.publication_submission_ready is False
    assert report.hard_gate_passed is False
    assert dimensions["reporting_integrity"].status == "meets_target"
    assert dimensions["scientific_identification"].status == "hard_fail"
    assert dimensions["independent_validation"].status == "hard_fail"
    assert report.estimated_acceptance_probability.center == pytest.approx(0.0544)
    assert report.projected_readiness_after_plan > report.readiness_threshold
    assert report.system_design_repair_required is True
    assert report.failure_fingerprint
    assert {item.earliest_repair_stage for item in report.system_design_repairs} >= {
        "stage_1_discovery",
        "stage_2_protocol",
    }
    assert "VENUE-QUALITY-BAR-NOT-MET" in {
        item.code for item in report.hard_blockers
    }


def test_readiness_threshold_never_changes_acceptance_probability(tmp_path: Path) -> None:
    low_target, low_path = _target(tmp_path / "low", threshold=0.50)
    high_target, high_path = _target(tmp_path / "high", threshold=0.80)
    low = audit_publication_readiness(low_target, project=PROJECT, contract_path=low_path)
    high = audit_publication_readiness(high_target, project=PROJECT, contract_path=high_path)

    assert low.readiness_score == high.readiness_score
    assert low.estimated_acceptance_probability == high.estimated_acceptance_probability
    assert low.score_threshold_passed is True
    assert low.hard_gate_passed is False
    assert low.publication_submission_ready is False


def test_frozen_target_requires_explicit_replacement(tmp_path: Path) -> None:
    _, target_path = _target(tmp_path)

    with pytest.raises(FileExistsError, match="different publication target"):
        create_publication_target(
            MANUSCRIPT,
            project=PROJECT,
            venue_id="tmlr",
            contract_path=target_path,
        )


def test_report_persists_readiness_and_acceptance_as_separate_quantities(tmp_path: Path) -> None:
    target, target_path = _target(tmp_path)
    report = audit_publication_readiness(target, project=PROJECT, contract_path=target_path)
    json_path, markdown_path = persist_publication_readiness(
        report,
        json_path=tmp_path / "readiness.json",
        markdown_path=tmp_path / "readiness.md",
    )
    markdown = markdown_path.read_text(encoding="utf-8")

    assert json_path.is_file()
    assert "60% 是 Research Forge 可控制的投稿就绪门，不是期刊录用率" in markdown
    assert "文字深度门=通过" not in markdown
    assert "累计情景就绪度" in markdown


def test_scientific_failure_revises_future_stage2_design_and_tracks_recurrence(tmp_path: Path) -> None:
    target, target_path = _target(tmp_path)
    readiness = audit_publication_readiness(target, project=PROJECT, contract_path=target_path)
    repair = build_system_design_repair_report(readiness)
    ledger = tmp_path / "failure-ledger.jsonl"
    json_path = tmp_path / "repair.json"
    markdown_path = tmp_path / "repair.md"

    persist_system_design_repair(
        repair,
        json_path=json_path,
        markdown_path=markdown_path,
        ledger_path=ledger,
    )
    persist_system_design_repair(
        repair,
        json_path=json_path,
        markdown_path=markdown_path,
        ledger_path=ledger,
    )

    violations = stage2_design_repair_violations(
        repair.model_dump(mode="json"),
        open_root_cause_codes={
            "RC-MEASUREMENT-CIRCULARITY",
            "RC-COUNTERFACTUAL-NONISOLATION",
            "RC-CONSTRUCT-UNDERCOVERAGE",
        },
        task_count=3,
        seed_count=3,
        novelty_refresh_present=False,
    )
    repaired = stage2_design_repair_violations(
        repair.model_dump(mode="json"),
        open_root_cause_codes=set(),
        task_count=8,
        seed_count=5,
        novelty_refresh_present=True,
    )

    assert any("RC-MEASUREMENT-CIRCULARITY" in item for item in violations)
    assert any("8 tasks x 5 seeds" in item for item in violations)
    assert any("novelty_refresh.json" in item for item in violations)
    assert repaired == []
    events = ledger.read_text(encoding="utf-8").splitlines()
    assert len(events) == 2
    assert '"occurrence_index": 2' in events[1]
    assert "历史冻结产物保持不可变" in markdown_path.read_text(encoding="utf-8")


def test_strict_venue_recommendation_freezes_pre_experiment_hard_gates(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target, target_path = freeze_publication_experiment_target(
        project=project,
        venue_id=VENUE,
        recommendation_report_path=VENUE_REPORT,
    )
    protocol = {
        "protocol_id": "stage2-current",
        "tasks": [{"task_id": f"task-{index}"} for index in range(3)],
        "seeds": [0, 1, 2],
        "primary_metric": "unsupported_claim_rate",
        "secondary_metrics": ["claim_retention"],
    }
    roots = {
        "findings": [
            {"code": "RC-MEASUREMENT-CIRCULARITY", "resolved": False},
            {"code": "RC-COUNTERFACTUAL-NONISOLATION", "resolved": False},
            {"code": "RC-CONSTRUCT-UNDERCOVERAGE", "resolved": False},
        ]
    }
    gate = audit_publication_experiment_design(
        target,
        protocol=protocol,
        root_cause_report=roots,
        novelty_refresh_present=False,
    )

    assert target_path == project / "publication_experiment_contract.json"
    assert target.venue_target_locked is True
    assert target.venue_quality_bar == pytest.approx(0.68)
    assert target.recommendation_quality_score == pytest.approx(0.4767)
    assert target.minimum_tasks == 8
    assert target.minimum_seeds_per_task == 5
    assert gate.pre_experiment_gate_passed is False
    assert any("publication intent" in item for item in gate.violations)
    assert any("locked publication contract" in item for item in gate.violations)
    assert any("blocking scientific root causes" in item for item in gate.violations)
    assert any("at least 8" in item for item in gate.violations)
    assert any("at least 5" in item for item in gate.violations)
    assert any("construct metrics" in item for item in gate.violations)
    assert any("novelty_refresh.json" in item for item in gate.violations)

    repaired_gate = audit_publication_experiment_design(
        target,
        protocol={
            "protocol_id": "stage2-repaired",
            "study_intent": "publication",
            "publication_contract_id": target.contract_id,
            "tasks": [{"task_id": f"task-{index}"} for index in range(8)],
            "seeds": [0, 1, 2, 3, 4],
            "primary_metric": "unsupported_claim_rate",
            "secondary_metrics": [
                "claim_retention",
                "semantic_change_type",
                "scientific_informativeness",
            ],
        },
        root_cause_report={"findings": []},
        novelty_refresh_present=True,
    )
    assert repaired_gate.pre_experiment_gate_passed is True
    assert repaired_gate.violations == []

    with pytest.raises(FileExistsError, match="different publication experiment target"):
        freeze_publication_experiment_target(
            project=project,
            venue_id="tmlr",
            recommendation_report_path=VENUE_REPORT,
            output_path=target_path,
        )
