from pathlib import Path

import research_forge.publication_context_review as context_review
from research_forge.storage import sha256_file, write_json_atomic


def test_successor_plan_binds_repair_and_keeps_execution_closed(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "study-v1"
    protocol_path = project / "stage2" / "protocol.json"
    context_path = (
        project
        / "stage2"
        / "protected_nli_evaluation"
        / "manual-audit"
        / "context-restored-review"
        / "result.json"
    )
    repair_path = project / "design_revisions" / "publication_context_repair_verification.json"
    write_json_atomic(
        project / "project.json",
        {"schema_version": 1, "slug": "study-v1", "name": "Study", "idea": "Test"},
    )
    write_json_atomic(
        protocol_path,
        {
            "protocol_id": "stage2-predecessor",
            "primary_metric": "unsupported_claim_rate",
            "secondary_metrics": ["task_native_score"],
            "seeds": [11, 22],
            "tasks": [
                {
                    "task_id": "task-a",
                    "task_hash": "a" * 64,
                    "primary_metric": "Accuracy",
                    "direction": "maximize",
                    "baseline_score": 0.5,
                    "target_score": 0.7,
                }
            ],
            "manual_audit": {"independent_auditors": 2, "total_claims": 16},
        },
    )
    write_json_atomic(context_path, {"review_type": "post_unblinding_context_restored_diagnostic"})
    source_root = Path(context_review.__file__).resolve().parent
    source_hashes = {
        name: sha256_file(source_root / name)
        for name in (
            "study_runner.py",
            "counterfactual_rebranch.py",
            "publication_nli_evaluation.py",
        )
    }
    write_json_atomic(
        repair_path,
        {
            "passed": True,
            "successor_evaluator_implementation_version": (
                "publication-nli-v4-context-bound-deterministic-metrics"
            ),
            "source_code_sha256": source_hashes,
            "replayed_cases": 5,
            "supported_cases": 5,
        },
    )
    write_json_atomic(project / "design_revisions" / "independent_calibration_contract.json", {"passed": True})
    write_json_atomic(project / "design_revisions" / "novelty_refresh.json", {"passed": True})
    monkeypatch.setattr(
        context_review,
        "audit_publication_context_review",
        lambda _project: {
            "passed": True,
            "review_sha256": sha256_file(context_path),
        },
    )

    plan = context_review.prepare_publication_successor_protocol_plan(project)

    assert plan["status"] == "prefreeze_prerequisites_open"
    assert plan["successor"]["predecessor_outcome_reuse_allowed"] is False
    assert plan["confirmatory_boundary"]["post_unblinding_AAAAA_may_not_be_used_as_successor_labels"] is True
    assert plan["repair_binding"]["historical_diagnostic_replay"]["confirmatory_evidence"] is False
    assert plan["successor_protocol_ready_to_freeze"] is False
    audit = context_review.audit_publication_successor_protocol_plan(project)
    assert audit["passed"] is True
    assert audit["successor_protocol_ready_to_freeze"] is False
    assert "secondary_evaluator_contract_available" in audit["open_prerequisites"]
    assert "successor_specific_publication_contract_required" in audit["open_prerequisites"]
