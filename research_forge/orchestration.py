from __future__ import annotations

"""Durable, operation-neutral task orchestration for Research Forge.

The orchestrator deliberately knows nothing about literature, experiments, or
papers.  Domain operations are registered as handlers.  Every task is written
before a handler is called, and every transition is appended to an event log,
so a process interruption never turns into an invisible repeated side effect.
"""

import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .models import MacroStage, StrictModel, utc_now
from .storage import append_jsonl, read_json, write_json_atomic


TASK_DIRECTORY = "tasks"
TASK_EVENT_FILENAME = "task_events.jsonl"
_OPERATION_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class RequirementKind(StrEnum):
    DATASET = "dataset"
    CREDENTIAL = "credential"
    PERMISSION = "permission"
    ENVIRONMENT = "environment"
    CONFIGURATION = "configuration"
    PROTOCOL_CHANGE = "protocol_change"


class TaskRequirement(StrictModel):
    requirement_id: str = Field(pattern=r"^req-[a-f0-9]{12}$")
    kind: RequirementKind
    title: str = Field(max_length=240)
    reason: str = Field(max_length=4_000)
    stage: MacroStage | None = None
    required: bool = True
    changes_protocol: bool = False
    external_data_disclosure: list[str] = Field(default_factory=list)
    accepted_inputs: list[dict[str, Any]] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    resolved: bool = False
    resolution: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        *,
        kind: RequirementKind,
        title: str,
        reason: str,
        stage: MacroStage | None = None,
        required: bool = True,
        changes_protocol: bool = False,
        external_data_disclosure: list[str] | None = None,
        accepted_inputs: list[dict[str, Any]] | None = None,
        alternatives: list[dict[str, Any]] | None = None,
    ) -> "TaskRequirement":
        material = f"{kind.value}\0{title}\0{reason}\0{uuid.uuid4().hex}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
        return cls(
            requirement_id=f"req-{digest}",
            kind=kind,
            title=title,
            reason=reason,
            stage=stage,
            required=required,
            changes_protocol=changes_protocol,
            external_data_disclosure=external_data_disclosure or [],
            accepted_inputs=accepted_inputs or [],
            alternatives=alternatives or [],
        )


class TaskRequest(StrictModel):
    schema_version: int = 1
    operation: str = Field(pattern=_OPERATION_PATTERN, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=3, max_length=240)

    @model_validator(mode="after")
    def payload_must_be_json_serializable(self) -> "TaskRequest":
        try:
            json.dumps(self.payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("task payload must be JSON serializable") from exc
        return self


class TaskError(StrictModel):
    error_type: str
    message: str = Field(max_length=4_000)


class TaskRecord(StrictModel):
    schema_version: int = 1
    task_id: str = Field(pattern=r"^task-[a-f0-9]{16}$")
    request: TaskRequest
    stage: MacroStage | None = None
    status: TaskStatus = TaskStatus.PENDING
    attempt: int = Field(default=0, ge=0)
    resumable: bool = False
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: TaskError | None = None
    requirements: list[TaskRequirement] = Field(default_factory=list)
    pause_requested_at: str | None = None


class TaskRequirementError(RuntimeError):
    """Pause a durable workflow until the user resolves a structured requirement."""

    def __init__(self, requirement: TaskRequirement) -> None:
        super().__init__(requirement.reason)
        self.requirement = requirement


class TaskPauseError(RuntimeError):
    """Stop a running operation at a resumable checkpoint without succeeding."""

    def __init__(self, detail: dict[str, Any] | None = None) -> None:
        super().__init__("task paused at a resumable checkpoint")
        self.detail = detail or {}


@dataclass(frozen=True)
class OperationDefinition:
    operation: str
    handler: Callable[..., Any | Awaitable[Any]]
    stage: MacroStage | None = None
    resumable: bool = False
    max_transient_retries: int = 3
    retry_backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        if re.fullmatch(_OPERATION_PATTERN, self.operation) is None:
            raise ValueError(f"invalid task operation: {self.operation}")
        if self.max_transient_retries < 0:
            raise ValueError("max_transient_retries must be non-negative")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")


class TaskExecutionError(RuntimeError):
    def __init__(self, record: TaskRecord) -> None:
        message = record.error.message if record.error else "task execution failed"
        super().__init__(f"{record.task_id}: {message}")
        self.record = record


class TransientTaskError(RuntimeError):
    """A retryable network, rate-limit, or temporary process failure."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _json_result(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, Path):
        value = {"path": str(value.resolve())}
    if isinstance(value, dict):
        payload = value
    else:
        payload = {"value": value}
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise TypeError("task result must be JSON serializable") from exc


def _task_digest(request: TaskRequest) -> str:
    if request.idempotency_key:
        material = f"{request.operation}\0{request.idempotency_key}"
    else:
        material = f"{request.operation}\0{uuid.uuid4().hex}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class TaskOrchestrator:
    def __init__(self, root: str | Path, operations: Iterable[OperationDefinition] = ()) -> None:
        self.root = Path(root).expanduser().resolve()
        self.tasks_root = self.root / TASK_DIRECTORY
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self._operations: dict[str, OperationDefinition] = {}
        for definition in operations:
            self.register(definition)

    def register(self, definition: OperationDefinition) -> None:
        if definition.operation in self._operations:
            raise ValueError(f"task operation already registered: {definition.operation}")
        self._operations[definition.operation] = definition

    def _path(self, task_id: str) -> Path:
        if re.fullmatch(r"task-[a-f0-9]{16}", task_id) is None:
            raise ValueError("invalid task ID")
        return self.tasks_root / f"{task_id}.json"

    def _append_event(
        self,
        record: TaskRecord,
        event: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "schema_version": 1,
            "event": event,
            "task_id": record.task_id,
            "operation": record.request.operation,
            "status": record.status.value,
            "attempt": record.attempt,
            "recorded_at": utc_now(),
        }
        if detail:
            payload.update(_json_result(detail))
        append_jsonl(
            self.root / TASK_EVENT_FILENAME,
            payload,
        )

    def _persist(self, record: TaskRecord, event: str) -> TaskRecord:
        record = record.model_copy(update={"updated_at": utc_now()})
        write_json_atomic(self._path(record.task_id), record)
        self._append_event(record, event)
        return record

    def _progress_reporter(self, task_id: str) -> Callable[..., None]:
        def report(**detail: Any) -> None:
            self._append_event(self.load(task_id), "progress", detail)

        return report

    def submit(self, request: TaskRequest) -> TaskRecord:
        task_id = f"task-{_task_digest(request)}"
        path = self._path(task_id)
        if path.is_file():
            return self.load(task_id)
        definition = self._operations.get(request.operation)
        record = TaskRecord(
            task_id=task_id,
            request=request,
            stage=definition.stage if definition else None,
            resumable=definition.resumable if definition else False,
        )
        return self._persist(record, "submitted")

    def load(self, task_id: str) -> TaskRecord:
        return TaskRecord.model_validate(read_json(self._path(task_id)))

    def list(self, *, status: TaskStatus | None = None) -> list[TaskRecord]:
        records = [
            TaskRecord.model_validate(read_json(path))
            for path in sorted(self.tasks_root.glob("task-*.json"))
        ]
        if status is not None:
            records = [item for item in records if item.status is status]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    async def run(self, task_id: str, *, resume: bool = False) -> TaskRecord:
        record = self.load(task_id)
        if record.status is TaskStatus.SUCCEEDED:
            return record
        if record.status is TaskStatus.CANCELLED:
            return record
        if resume and record.status is TaskStatus.PAUSED and record.result is not None:
            completed = record.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "completed_at": utc_now(),
                    "pause_requested_at": None,
                }
            )
            return self._persist(completed, "resumed_from_checkpoint")
        if record.status is TaskStatus.RUNNING and not resume:
            raise ValueError("task was interrupted while running; use resume explicitly")
        if record.status in {
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.PAUSED,
            TaskStatus.RETRYING,
            TaskStatus.WAITING_FOR_USER,
            TaskStatus.PAUSE_REQUESTED,
        } and not resume:
            raise ValueError("failed task requires an explicit resume")
        if resume and not record.resumable:
            raise ValueError(f"task operation is not safely resumable: {record.request.operation}")
        unresolved = [item for item in record.requirements if item.required and not item.resolved]
        if unresolved:
            raise ValueError("task still has unresolved required user input")
        definition = self._operations.get(record.request.operation)
        if definition is None:
            blocked = record.model_copy(
                update={
                    "status": TaskStatus.BLOCKED,
                    "error": TaskError(
                        error_type="UnknownOperation",
                        message=f"no handler registered for {record.request.operation}",
                    ),
                    "completed_at": utc_now(),
                }
            )
            return self._persist(blocked, "blocked")
        running = self._persist(
            record.model_copy(
                update={
                    "status": TaskStatus.RUNNING,
                    "attempt": record.attempt + 1,
                    "started_at": utc_now(),
                    "completed_at": None,
                    "result": None,
                    "error": None,
                }
            ),
            "resumed" if resume else "started",
        )

        async def invoke(current: TaskRecord) -> Any:
            parameters = inspect.signature(definition.handler).parameters
            if len(parameters) >= 4:
                value = definition.handler(
                    current.request.payload,
                    current,
                    self._progress_reporter(task_id),
                    lambda: self.load(task_id).status.value,
                )
            elif len(parameters) >= 3:
                value = definition.handler(
                    current.request.payload,
                    current,
                    self._progress_reporter(task_id),
                )
            else:
                value = definition.handler(current.request.payload, current)
            if inspect.isawaitable(value):
                value = await value
            return value

        retries = 0
        while True:
            try:
                value = await invoke(running)
                break
            except TransientTaskError as exc:
                if retries >= definition.max_transient_retries:
                    failed = running.model_copy(
                        update={
                            "status": TaskStatus.FAILED,
                            "error": TaskError(
                                error_type=type(exc).__name__,
                                message=str(exc)[:4_000] or type(exc).__name__,
                            ),
                            "completed_at": utc_now(),
                        }
                    )
                    return self._persist(failed, "failed_after_retries")
                retries += 1
                retrying = self._persist(
                    running.model_copy(
                        update={
                            "status": TaskStatus.RETRYING,
                            "error": TaskError(
                                error_type=type(exc).__name__,
                                message=str(exc)[:4_000] or type(exc).__name__,
                            ),
                        }
                    ),
                    "retrying",
                )
                import asyncio

                delay = (
                    exc.retry_after_seconds
                    if exc.retry_after_seconds is not None
                    else definition.retry_backoff_seconds * (2 ** (retries - 1))
                )
                if delay:
                    await asyncio.sleep(delay)
                running = self._persist(
                    retrying.model_copy(
                        update={
                            "status": TaskStatus.RUNNING,
                            "attempt": retrying.attempt + 1,
                            "error": None,
                        }
                    ),
                    "retry_started",
                )
            except TaskPauseError as exc:
                latest = self.load(task_id)
                paused = latest.model_copy(
                    update={
                        "status": TaskStatus.PAUSED,
                        "result": exc.detail or None,
                        "error": None,
                        "completed_at": None,
                    }
                )
                return self._persist(paused, "paused_at_checkpoint")
            except TaskRequirementError as exc:
                requirement = exc.requirement
                waiting = running.model_copy(
                    update={
                        "status": TaskStatus.WAITING_FOR_USER,
                        "requirements": [*running.requirements, requirement],
                        "error": TaskError(
                            error_type="UserInputRequired",
                            message=requirement.reason,
                        ),
                        "completed_at": None,
                    }
                )
                return self._persist(waiting, "waiting_for_user")
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                failed = running.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "error": TaskError(
                            error_type=type(exc).__name__,
                            message=str(exc)[:4_000] or type(exc).__name__,
                        ),
                        "completed_at": utc_now(),
                    }
                )
                return self._persist(failed, "failed")

        try:
            payload = _json_result(value)
            latest = self.load(task_id)
            if latest.status is TaskStatus.PAUSE_REQUESTED:
                paused = latest.model_copy(
                    update={
                        "status": TaskStatus.PAUSED,
                        "result": payload,
                        "completed_at": None,
                    }
                )
                return self._persist(paused, "paused_at_checkpoint")
            succeeded = running.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "result": payload,
                    "completed_at": utc_now(),
                }
            )
            return self._persist(succeeded, "succeeded")
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            failed = running.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "error": TaskError(
                        error_type=type(exc).__name__,
                        message=str(exc)[:4_000] or type(exc).__name__,
                    ),
                    "completed_at": utc_now(),
                }
            )
            return self._persist(failed, "failed")

    def request_pause(self, task_id: str) -> TaskRecord:
        record = self.load(task_id)
        if record.status is TaskStatus.PENDING:
            paused = record.model_copy(update={"status": TaskStatus.PAUSED})
            return self._persist(paused, "paused")
        if record.status not in {TaskStatus.RUNNING, TaskStatus.RETRYING}:
            raise ValueError(f"task cannot be paused from {record.status.value}")
        requested = record.model_copy(
            update={
                "status": TaskStatus.PAUSE_REQUESTED,
                "pause_requested_at": utc_now(),
            }
        )
        return self._persist(requested, "pause_requested")

    def cancel(self, task_id: str) -> TaskRecord:
        record = self.load(task_id)
        if record.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            return record
        cancelled = record.model_copy(
            update={
                "status": TaskStatus.CANCELLED,
                "completed_at": utc_now(),
                "pause_requested_at": None,
            }
        )
        return self._persist(cancelled, "cancelled")

    def resolve_requirement(
        self,
        task_id: str,
        requirement_id: str,
        resolution: dict[str, Any],
    ) -> TaskRecord:
        record = self.load(task_id)
        if record.status is not TaskStatus.WAITING_FOR_USER:
            raise ValueError("task is not waiting for user input")
        found = False
        requirements: list[TaskRequirement] = []
        for item in record.requirements:
            if item.requirement_id == requirement_id:
                found = True
                requirements.append(
                    item.model_copy(update={"resolved": True, "resolution": resolution})
                )
            else:
                requirements.append(item)
        if not found:
            raise FileNotFoundError(f"task requirement not found: {requirement_id}")
        next_status = (
            TaskStatus.BLOCKED
            if any(item.changes_protocol and item.resolved for item in requirements)
            else TaskStatus.PAUSED
        )
        updated = record.model_copy(
            update={
                "status": next_status,
                "requirements": requirements,
                "error": None,
            }
        )
        event = "protocol_change_required" if next_status is TaskStatus.BLOCKED else "requirement_resolved"
        return self._persist(updated, event)

    def run_sync(self, task_id: str, *, resume: bool = False) -> TaskRecord:
        import asyncio

        return asyncio.run(self.run(task_id, resume=resume))

    def execute_sync(self, request: TaskRequest) -> TaskRecord:
        record = self.submit(request)
        if record.status is TaskStatus.SUCCEEDED:
            return record
        record = self.run_sync(record.task_id)
        if record.status is not TaskStatus.SUCCEEDED:
            raise TaskExecutionError(record)
        return record
