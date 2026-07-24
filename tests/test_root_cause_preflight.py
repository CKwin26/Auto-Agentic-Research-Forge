from __future__ import annotations

from research_forge.root_cause_preflight import (
    ReviewTarget,
    analyze_root_causes,
)


def _current_design() -> tuple[dict, dict, dict, dict]:
    plan = {
        "research_question": "Does the gate reduce unsupported claims?",
        "hypothesis": "The treatment will lower unsupported_claim_rate.",
        "novelty_claim": "A bounded engineering study.",
        "baseline_definition": "The same backbone is used in two separate arms.",
        "readiness_summary": "Ready to freeze.",
        "method_outline": [
            "Run nine matched task-seed pairs.",
            "Report paired per-task comparisons.",
        ],
        "confounders": ["The evaluator may share blind spots with the gate."],
        "risks": [],
        "scope_in": [],
        "scope_out": [],
        "metrics": [
            {"name": "unsupported_claim_rate"},
            {"name": "wall_clock_runtime_seconds"},
        ],
    }
    protocol = {
        "primary_metric": "unsupported_claim_rate",
        "secondary_metrics": ["wall_clock_runtime_seconds"],
        "protected_evaluator": "hybrid_structural_plus_arm_blinded_codex",
        "treatment_gate": "structural_plus_frozen_codex_gate",
        "manual_audit": {"independent_auditors": 2},
        "cells": [
            {"arm": arm, "task_id": "task", "seed": seed}
            for arm in ("baseline", "treatment")
            for seed in (0, 1, 2)
        ],
    }
    backbone = {"model": "codex:gpt-test"}
    analysis = {
        "human_validation": "deferred",
        "primary_analysis_interpretable": False,
    }
    return plan, protocol, backbone, analysis


def test_current_design_is_traced_to_upstream_roots_and_blocks_publication() -> None:
    plan, protocol, backbone, analysis = _current_design()
    report = analyze_root_causes(
        project_name="fixture",
        plan=plan,
        protocol=protocol,
        backbone=backbone,
        analysis=analysis,
        review_corpus=(
            "Major Revision. Same-family evaluator preference, separate stochastic runs, "
            "fixed order, semantic contraction, equal claim counts, and deferred human audit."
        ),
        target=ReviewTarget.PUBLICATION,
    )

    codes = {item.code for item in report.findings}
    assert {
        "RC-MEASUREMENT-CIRCULARITY",
        "RC-COUNTERFACTUAL-NONISOLATION",
        "RC-CONSTRUCT-UNDERCOVERAGE",
        "RC-MATURITY-TARGET-MISMATCH",
        "RC-REPORTING-CONTRACT-GAP",
        "RC-TELEMETRY-SCHEMA-GAP",
    } <= codes
    assert report.maximum_claim_tier == "internal_proxy_association"
    assert not report.publication_submission_ready
    assert not report.gate_passed
    assert report.predicted_editorial_outcome == "major_revision_or_reject"


def test_same_evidence_can_be_routed_to_developmental_review() -> None:
    plan, protocol, backbone, analysis = _current_design()
    report = analyze_root_causes(
        project_name="fixture",
        plan=plan,
        protocol=protocol,
        backbone=backbone,
        analysis=analysis,
        target=ReviewTarget.DEVELOPMENTAL_REVIEW,
    )
    assert report.gate_passed
    assert report.developmental_review_allowed
    assert not report.publication_submission_ready
    assert report.predicted_editorial_outcome == "developmental_major_revision_expected"


def test_independent_content_matched_design_can_clear_publication_gate() -> None:
    plan, protocol, backbone, analysis = _current_design()
    plan["method_outline"] = [
        "Branch both conditions from identical run artifacts.",
        "Report all nine raw pairs and leave-one-task-out sensitivity.",
    ]
    plan["metrics"] = [
        {"name": "unsupported_claim_rate"},
        {"name": "semantic_change_type"},
        {"name": "informativeness"},
        {"name": "wall_clock_runtime_seconds"},
        {"name": "model_call_count"},
        {"name": "token_usage"},
    ]
    protocol.update(
        {
            "protected_evaluator": "external_evaluator",
            "treatment_gate": "codex_gate",
            "counterfactual_source": "shared_run_artifact",
            "secondary_metrics": [
                "semantic_change_type",
                "informativeness",
                "wall_clock_runtime_seconds",
                "model_call_count",
                "token_usage",
            ],
            "cells": [
                {"arm": arm, "task_id": "task", "seed": seed}
                for seed in (0, 1, 2)
                for arm in ("baseline", "treatment")
            ],
        }
    )
    analysis = {
        "human_validation": "completed",
        "primary_analysis_interpretable": True,
    }
    report = analyze_root_causes(
        project_name="fixture",
        plan=plan,
        protocol=protocol,
        backbone=backbone,
        analysis=analysis,
        target=ReviewTarget.PUBLICATION,
    )

    assert report.gate_passed
    assert report.publication_submission_ready
    assert report.maximum_claim_tier == "bounded_causal_effect"
    assert not [item for item in report.findings if item.severity.value == "critical"]


def test_deferred_human_audit_can_pass_the_automated_publication_gate_only() -> None:
    """Human pending must not be relabelled as submission ready or block automation."""
    plan, protocol, _backbone, _analysis = _current_design()
    plan["method_outline"] = [
        "Branch both conditions from identical run artifacts.",
        "Report all nine raw pairs and leave-one-task-out sensitivity.",
    ]
    plan["metrics"] = [
        {"name": "unsupported_claim_rate"},
        {"name": "semantic_change_type"},
        {"name": "informativeness"},
        {"name": "wall_clock_runtime_seconds"},
        {"name": "model_call_count"},
        {"name": "token_usage"},
    ]
    protocol.update(
        {
            "protected_evaluator": "external_evaluator",
            "treatment_gate": "codex_gate",
            "counterfactual_source": "shared_run_artifact",
            "secondary_metrics": [
                "semantic_change_type",
                "informativeness",
                "wall_clock_runtime_seconds",
                "model_call_count",
                "token_usage",
            ],
            "cells": [
                {"arm": arm, "task_id": "task", "seed": seed}
                for seed in (0, 1, 2)
                for arm in ("baseline", "treatment")
            ],
        }
    )
    report = analyze_root_causes(
        project_name="fixture",
        plan=plan,
        protocol=protocol,
        backbone={"model": "codex:gpt-test"},
        analysis={
            "analysis_status": "protected_independent_nli_complete_human_audit_pending",
            "human_validation": "deferred",
            "primary_analysis_interpretable": False,
        },
        target=ReviewTarget.PUBLICATION,
    )

    assert report.gate_passed
    assert report.automated_publication_gate_passed
    assert report.human_gate_pending
    assert not report.publication_submission_ready
    assert report.predicted_editorial_outcome == "automated_gate_passed_human_validation_pending"
    maturity = next(item for item in report.findings if item.code == "RC-MATURITY-TARGET-MISMATCH")
    assert maturity.severity.value == "medium"


def test_verified_context_repair_closes_implementation_faults_not_historical_endpoint() -> None:
    plan, protocol, backbone, _analysis = _current_design()
    report = analyze_root_causes(
        project_name="fixture",
        plan=plan,
        protocol=protocol,
        backbone=backbone,
        analysis={
            "human_validation": "complete",
            "primary_analysis_interpretable": False,
            "analysis_status": "publication_human_audit_complete_primary_analysis_invalid",
        },
        context_review={
            "reviewed_evaluator_unsupported": 5,
            "contextual_verdict_letters": "AAAAA",
            "contextual_verdict_counts": {"supported": 5},
        },
        context_repair_verification={
            "passed": True,
            "does_not_recompute_historical_primary_analysis": True,
            "replayed_cases": 5,
            "supported_cases": 5,
        },
        successor_protocol_plan={
            "successor": {"predecessor_outcome_reuse_allowed": False}
        },
        target=ReviewTarget.PUBLICATION,
    )

    by_code = {item.code: item for item in report.findings}
    assert by_code["RC-EVIDENCE-PACKAGING-CONTEXT-LOSS"].resolved is True
    assert by_code["RC-DETERMINISTIC-METRIC-PRECEDENCE-GAP"].resolved is True
    assert by_code["RC-PRIMARY-ENDPOINT-INVALID"].resolved is False
    assert "fault-localization and implementation-repair loop is complete" in report.root_conclusion
    assert "rather than evidence that the diagnostic workflow failed" in report.root_conclusion
