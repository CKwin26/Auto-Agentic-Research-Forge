from __future__ import annotations

import asyncio
import threading

from research_forge.models import MacroStage
from research_forge.orchestration import (
    OperationDefinition,
    RequirementKind,
    TaskOrchestrator,
    TaskRequirement,
    TaskRequirementError,
    TaskRequest,
    TaskStatus,
)
from research_forge.storage import load_jsonl
from research_forge.workflow_tasks import built_in_operations


def test_task_is_persisted_before_execution_and_idempotent(tmp_path) -> None:
    seen = []

    def handler(payload, task):
        seen.append((payload["value"], task.status.value))
        return {"answer": payload["value"] + 1}

    orchestrator = TaskOrchestrator(
        tmp_path,
        [OperationDefinition("test.increment", handler, stage=MacroStage.EXPERIMENTATION)],
    )
    request = TaskRequest(
        operation="test.increment", payload={"value": 2}, idempotency_key="same-input"
    )
    first = orchestrator.submit(request)
    second = orchestrator.submit(request)
    assert first.task_id == second.task_id
    assert orchestrator.load(first.task_id).status is TaskStatus.PENDING

    completed = asyncio.run(orchestrator.run(first.task_id))

    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.result == {"answer": 3}
    assert seen == [(2, "running")]
    assert [item["event"] for item in load_jsonl(tmp_path / "task_events.jsonl")] == [
        "submitted",
        "started",
        "succeeded",
    ]


def test_operation_can_publish_concrete_progress_events(tmp_path) -> None:
    def handler(payload, task, report_progress):
        report_progress(
            stage="experimentation",
            title="正在核对冻结指标",
            detail=f"检查 {payload['count']} 项实验输出。",
        )
        return {"checked": payload["count"], "attempt": task.attempt}

    orchestrator = TaskOrchestrator(
        tmp_path,
        [OperationDefinition("test.progress", handler, stage=MacroStage.EXPERIMENTATION)],
    )
    task = orchestrator.submit(
        TaskRequest(operation="test.progress", payload={"count": 3})
    )

    completed = asyncio.run(orchestrator.run(task.task_id))
    events = load_jsonl(tmp_path / "task_events.jsonl")

    assert completed.status is TaskStatus.SUCCEEDED
    assert [item["event"] for item in events] == [
        "submitted",
        "started",
        "progress",
        "succeeded",
    ]
    assert events[2]["stage"] == "experimentation"
    assert events[2]["title"] == "正在核对冻结指标"
    assert events[2]["detail"] == "检查 3 项实验输出。"


def test_failed_resumable_task_preserves_error_and_can_resume(tmp_path) -> None:
    attempts = 0

    def handler(payload, task):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient failure")
        return {"attempt": task.attempt}

    orchestrator = TaskOrchestrator(
        tmp_path,
        [OperationDefinition("test.retry", handler, resumable=True)],
    )
    task = orchestrator.submit(TaskRequest(operation="test.retry"))
    failed = asyncio.run(orchestrator.run(task.task_id))
    assert failed.status is TaskStatus.FAILED
    assert failed.error is not None
    assert failed.error.error_type == "RuntimeError"

    completed = asyncio.run(orchestrator.run(task.task_id, resume=True))

    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.attempt == 2
    assert completed.result == {"attempt": 2}


def test_unknown_operation_is_durably_blocked(tmp_path) -> None:
    orchestrator = TaskOrchestrator(tmp_path)
    task = orchestrator.submit(TaskRequest(operation="unknown.operation"))

    blocked = asyncio.run(orchestrator.run(task.task_id))

    assert blocked.status is TaskStatus.BLOCKED
    assert blocked.error is not None
    assert blocked.error.error_type == "UnknownOperation"


def test_publication_adapter_actions_are_exposed_as_durable_operations() -> None:
    operations = {item.operation: item for item in built_in_operations()}

    assert operations["publication.audit-synthesis"].resumable is True
    assert operations["publication.manual-submit"].resumable is False
    assert operations["publication.audit-synthesis"].stage is MacroStage.SYNTHESIS


def test_running_task_pauses_at_completed_checkpoint_and_resumes_without_rerun(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def handler(payload, task):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return {"checkpoint": payload["value"]}

    orchestrator = TaskOrchestrator(
        tmp_path,
        [OperationDefinition("test.checkpoint", handler, resumable=True)],
    )
    task = orchestrator.submit(
        TaskRequest(operation="test.checkpoint", payload={"value": 7})
    )
    result = {}

    def execute() -> None:
        result["record"] = asyncio.run(orchestrator.run(task.task_id))

    worker = threading.Thread(target=execute)
    worker.start()
    assert entered.wait(timeout=2)
    requested = orchestrator.request_pause(task.task_id)
    assert requested.status is TaskStatus.PAUSE_REQUESTED
    release.set()
    worker.join(timeout=2)

    paused = result["record"]
    assert paused.status is TaskStatus.PAUSED
    assert paused.result == {"checkpoint": 7}

    completed = asyncio.run(orchestrator.run(task.task_id, resume=True))
    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.result == {"checkpoint": 7}
    assert calls == 1


def test_structured_requirement_waits_for_user_and_can_be_resolved(tmp_path) -> None:
    requirement = TaskRequirement.create(
        kind=RequirementKind.CREDENTIAL,
        title="Configure literature API",
        reason="A credential is required for the frozen literature provider.",
        stage=MacroStage.DISCOVERY,
        external_data_disclosure=["search keywords"],
        accepted_inputs=[{"type": "credential_status", "secret": False}],
        alternatives=[{"id": "offline", "label": "Use offline sources"}],
    )

    def handler(payload, task):
        if not task.requirements:
            raise TaskRequirementError(requirement)
        return {"continued": True}

    orchestrator = TaskOrchestrator(
        tmp_path,
        [OperationDefinition("test.requirement", handler, resumable=True)],
    )
    task = orchestrator.submit(TaskRequest(operation="test.requirement"))
    waiting = asyncio.run(orchestrator.run(task.task_id))
    assert waiting.status is TaskStatus.WAITING_FOR_USER
    assert waiting.requirements[0].kind is RequirementKind.CREDENTIAL

    paused = orchestrator.resolve_requirement(
        task.task_id,
        requirement.requirement_id,
        {"configured": True},
    )
    assert paused.status is TaskStatus.PAUSED
    assert paused.requirements[0].resolved is True

    completed = asyncio.run(orchestrator.run(task.task_id, resume=True))
    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.result == {"continued": True}
