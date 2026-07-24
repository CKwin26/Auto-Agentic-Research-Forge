from __future__ import annotations

import pytest

from research_forge.controller_ablation import (
    DEFAULT_CELL_STORAGE_ROOT,
    DEFAULT_TASKS,
    DEFAULT_VARIANTS,
    _aggregate_results,
    _execution_cells,
    _manifest_sha256,
    _task_entries,
    _validate_cell_completion,
)


def test_default_ablation_is_three_tasks_by_four_variants() -> None:
    tasks = [dict(item) for item in DEFAULT_TASKS]
    variants = [dict(item) for item in DEFAULT_VARIANTS]
    cells = _execution_cells(tasks, variants, [0])

    assert len(tasks) == 3
    assert len(variants) == 4
    assert len(cells) == 12
    assert len({(item["task_id"], item["variant_id"]) for item in cells}) == 12
    assert sum(int(item["iterations"]) for item in cells) == 28
    full_orders = [item["order"] for item in cells if item["variant_id"] == "full"]
    assert full_orders == [1, 8, 11]
    # The checkout directory is controlled by the runner and may itself be
    # long. Verify the storage suffix we control rather than the absolute path.
    storage_suffix = DEFAULT_CELL_STORAGE_ROOT.name + "/" + ("a" * 12) + "/12"
    assert len(storage_suffix) < 32
    by_id = {item["variant_id"]: item for item in variants}
    assert by_id["full"]["candidate_pool_size"] == 3
    assert by_id["no-candidate-pool"]["candidate_pool_size"] == 1
    assert by_id["no-duplicate-detection"]["candidate_pool_size"] == 3
    assert by_id["no-failure-diagnosis"]["candidate_pool_size"] == 3
    assert {item["proposal_attempts_per_iteration"] for item in variants} == {6}


def test_registered_ablation_tasks_match_frozen_budget_design() -> None:
    tasks = _task_entries()

    assert [item["task_id"] for item in tasks] == [
        "coreferenceresolutionsupergluewscaccuracy",
        "textualclassificationsickaccuracy",
        "textualsimilaritysickspearmancorrelation",
    ]
    assert [item["iterations"] for item in tasks] == [1, 3, 3]
    assert all(item["required_repeats"] == 2 for item in tasks)
    assert all(len(str(item["task_hash"])) == 64 for item in tasks)


def test_frozen_manifest_hash_detects_any_protocol_mutation() -> None:
    manifest = {
        "schema_version": 1,
        "matrix_id": "matrix-test",
        "status": "frozen",
        "seeds": [0],
        "variants": [dict(item) for item in DEFAULT_VARIANTS],
    }
    manifest["manifest_sha256"] = _manifest_sha256(manifest)

    assert _manifest_sha256(manifest) == manifest["manifest_sha256"]
    manifest["seeds"] = [1]
    assert _manifest_sha256(manifest) != manifest["manifest_sha256"]


def test_ablation_aggregation_is_paired_to_full_by_task() -> None:
    completed = []
    task_ids = ["a", "b", "c"]
    gains = {
        "full": [0.5, 0.4, 0.3],
        "no-candidate-pool": [0.4, 0.2, 0.3],
        "no-duplicate-detection": [0.5, 0.5, 0.4],
        "no-failure-diagnosis": [0.2, 0.4, 0.3],
    }
    for variant_id, values in gains.items():
        for task_id, gain in zip(task_ids, values, strict=True):
            completed.append(
                {
                    "variant_id": variant_id,
                    "task_id": task_id,
                    "normalized_gain": gain,
                    "anytime_auc": gain / 2,
                    "valid_submission_rate": 1.0,
                    "target_reached": False,
                    "proposal_attempts": 2,
                    "candidate_runs": 1,
                    "invalid_runs": 0,
                    "duplicate_rejections": 0,
                    "wall_time_seconds": 1.0,
                    "integrity_passed": True,
                }
            )

    aggregates = _aggregate_results(completed, [dict(item) for item in DEFAULT_VARIANTS])
    by_id = {item["variant_id"]: item for item in aggregates}

    assert by_id["full"]["mean_paired_delta_vs_full"] == 0.0
    assert abs(by_id["no-candidate-pool"]["mean_paired_delta_vs_full"] + 0.1) < 1e-12
    assert abs(by_id["no-duplicate-detection"]["mean_paired_delta_vs_full"] - (0.2 / 3)) < 1e-12
    assert by_id["no-failure-diagnosis"]["all_integrity_passed"] is True


def _clean_cell_journal() -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    report: dict[str, object] = {
        "publishable": True,
        "seeds": [{"errors": [], "proposal_attempts": 1}],
    }
    summary: dict[str, object] = {
        "config": {"candidate_pool_size": 1},
        "loops": [
            {
                "status": "completed",
                "stop_reason": "iteration_budget_exhausted",
                "errors": [],
                "proposal_attempts": 1,
                "completed_iterations": 1,
            }
        ],
    }
    events: list[dict[str, object]] = [
        {
            "event": "proposal_admitted_to_pool",
            "details": {
                "proposal_id": "proposal-1",
                "generated_from_fingerprint": "state-1",
            },
        },
        {
            "event": "candidate_pool_ready",
            "details": {
                "full": True,
                "pool_size": 1,
                "target_pool_size": 1,
                "canonical_state_fingerprint": "state-1",
            },
        },
        {
            "event": "candidate_selected",
            "details": {"proposal_id": "proposal-1"},
        },
    ]
    return report, summary, events


def test_cell_completion_guard_accepts_clean_state_safe_journal() -> None:
    report, summary, events = _clean_cell_journal()

    seed, loop, pool_events = _validate_cell_completion(
        report=report,
        summary=summary,
        proposal_ids={"proposal-1"},
        events=events,
    )

    assert seed["proposal_attempts"] == 1
    assert loop["status"] == "completed"
    assert len(pool_events) == 1


def test_cell_completion_guard_rejects_incomplete_or_contaminated_cells() -> None:
    report, summary, events = _clean_cell_journal()
    summary["loops"][0]["status"] = "running"  # type: ignore[index]
    summary["loops"][0]["stop_reason"] = None  # type: ignore[index]
    with pytest.raises(ValueError, match="terminal state"):
        _validate_cell_completion(
            report=report,
            summary=summary,
            proposal_ids={"proposal-1"},
            events=events,
        )

    report, summary, events = _clean_cell_journal()
    with pytest.raises(ValueError, match="proposal journal"):
        _validate_cell_completion(
            report=report,
            summary=summary,
            proposal_ids={"proposal-1", "orphan-proposal"},
            events=events,
        )

    report, summary, events = _clean_cell_journal()
    events[0]["details"]["generated_from_fingerprint"] = "stale-state"  # type: ignore[index]
    with pytest.raises(ValueError, match="stale state"):
        _validate_cell_completion(
            report=report,
            summary=summary,
            proposal_ids={"proposal-1"},
            events=events,
        )

    report, summary, events = _clean_cell_journal()
    events[1]["details"]["full"] = False  # type: ignore[index]
    with pytest.raises(ValueError, match="partial candidate pool"):
        _validate_cell_completion(
            report=report,
            summary=summary,
            proposal_ids={"proposal-1"},
            events=events,
        )
