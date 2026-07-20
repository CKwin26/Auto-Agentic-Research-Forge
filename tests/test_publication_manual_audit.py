from research_forge.publication_manual_audit import _select_blinded_sample


def test_publication_manual_audit_includes_every_rare_unsupported_claim() -> None:
    candidates = [
        {"task_id": "task", "arm": "baseline", "sort_key": "1", "evaluator_class": "unsupported"},
        {"task_id": "task", "arm": "baseline", "sort_key": "2", "evaluator_class": "non_unsupported"},
        {"task_id": "task", "arm": "treatment", "sort_key": "3", "evaluator_class": "non_unsupported"},
        {"task_id": "task", "arm": "treatment", "sort_key": "4", "evaluator_class": "non_unsupported"},
    ]
    selected, audit = _select_blinded_sample(
        candidates,
        {
            "tasks": [{"task_id": "task"}],
            "manual_audit": {
                "claims_per_task_arm": 2,
                "target_unsupported_per_task_arm": 1,
                "target_non_unsupported_per_task_arm": 1,
                "total_claims": 4,
            },
        },
    )
    assert len(selected) == 4
    assert audit["all_available_unsupported_selected"] is True
    assert audit["available_evaluator_unsupported"] == 1
