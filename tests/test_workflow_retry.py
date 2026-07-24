from __future__ import annotations

import asyncio

from research_forge.orchestration import (
    OperationDefinition,
    TaskOrchestrator,
    TaskRequest,
    TaskStatus,
    TransientTaskError,
)
from research_forge.storage import load_jsonl


def test_transient_failure_retries_three_times_automatically(tmp_path) -> None:
    attempts = 0

    def handler(payload, task):
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise TransientTaskError("temporary provider timeout", retry_after_seconds=0)
        return {"attempt": task.attempt}

    orchestrator = TaskOrchestrator(
        tmp_path,
        [
            OperationDefinition(
                "test.transient",
                handler,
                max_transient_retries=3,
                retry_backoff_seconds=0,
            )
        ],
    )
    task = orchestrator.submit(TaskRequest(operation="test.transient"))
    completed = asyncio.run(orchestrator.run(task.task_id))

    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.attempt == 4
    assert attempts == 4
    assert [item["event"] for item in load_jsonl(tmp_path / "task_events.jsonl")].count(
        "retrying"
    ) == 3


def test_cancel_preserves_task_record(tmp_path) -> None:
    orchestrator = TaskOrchestrator(
        tmp_path, [OperationDefinition("test.cancel", lambda payload, task: {})]
    )
    task = orchestrator.submit(TaskRequest(operation="test.cancel"))
    cancelled = orchestrator.cancel(task.task_id)

    assert cancelled.status is TaskStatus.CANCELLED
    assert orchestrator.load(task.task_id).status is TaskStatus.CANCELLED
