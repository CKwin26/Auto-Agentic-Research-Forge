"""Persistent, stateless Codex worker for evidence-bounded synthesis.

The worker keeps only the Codex transport/client warm.  Every submitted batch
starts a new ephemeral thread and receives its complete evidence bundle in the
request, so no answer can depend on conversational memory from an earlier job.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from ...agent_runtime import (
    CODEX_TRANSIENT_MAX_ATTEMPTS,
    CODEX_TURN_TIMEOUT_SECONDS,
    _append_agent_telemetry,
    _codex_process_env,
    _codex_retry_delay,
    _configured_codex_model,
    _is_transient_codex_error,
    _parse_structured_output,
    _strict_output_schema,
    codex_provider_binding,
)
from ...models import utc_now


T = TypeVar("T", bound=BaseModel)


class PersistentCodexSynthesisWorker:
    """Reuse one Codex client while keeping synthesis jobs semantically isolated."""

    def __init__(self, *, cwd: str | Path) -> None:
        self.cwd = Path(cwd).resolve()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="research-forge-synthesis-worker",
            daemon=True,
        )
        self._ready = threading.Event()
        self._submission_lock = threading.Lock()
        self._closed = False
        self._client: Any = None
        self._client_context: Any = None
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Codex synthesis worker failed to start")
        atexit.register(self.close)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        try:
            self._loop.run_until_complete(self._close_client())
        except RuntimeError as exc:
            # During interpreter shutdown Python may close its default executor
            # before atexit reaches this daemon worker. Codex is already
            # process-scoped at that point; suppress only that shutdown race.
            if "cannot schedule new futures after shutdown" not in str(exc):
                raise
        self._loop.close()

    async def _ensure_client(self) -> tuple[Any, int]:
        if self._client is not None:
            return self._client, 0
        from openai_codex import AsyncCodex, CodexConfig

        started = time.perf_counter()
        config = CodexConfig(
            cwd=str(self.cwd),
            env=_codex_process_env(),
            client_name="research_forge",
            client_title="Research Forge Synthesis",
        )
        self._client_context = AsyncCodex(config)
        self._client = await self._client_context.__aenter__()
        elapsed = round((time.perf_counter() - started) * 1000)
        return self._client, elapsed

    async def _close_client(self) -> None:
        if self._client_context is not None:
            await self._client_context.__aexit__(None, None, None)
        self._client = None
        self._client_context = None

    async def _execute(
        self,
        *,
        submitted_at: float,
        name: str,
        instructions: str,
        output_type: type[T],
        prompt: str,
    ) -> tuple[T, dict[str, int | None]]:
        from openai_codex import ApprovalMode, Sandbox

        dequeued_at = time.perf_counter()
        client_init_ms = 0
        session_start_ms = 0
        model_elapsed_ms = 0
        result = None
        provider = codex_provider_binding()
        for attempt in range(1, CODEX_TRANSIENT_MAX_ATTEMPTS + 1):
            try:
                client, init_ms = await self._ensure_client()
                client_init_ms += init_ms
                session_started = time.perf_counter()
                thread = await client.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    base_instructions=instructions,
                    cwd=str(self.cwd),
                    ephemeral=True,
                    model=_configured_codex_model(),
                    sandbox=Sandbox.read_only,
                    service_name=name,
                )
                session_start_ms += round(
                    (time.perf_counter() - session_started) * 1000
                )
                model_started = time.perf_counter()
                result = await asyncio.wait_for(
                    thread.run(
                        prompt,
                        approval_mode=ApprovalMode.deny_all,
                        cwd=str(self.cwd),
                        output_schema=_strict_output_schema(output_type),
                        sandbox=Sandbox.read_only,
                    ),
                    timeout=CODEX_TURN_TIMEOUT_SECONDS,
                )
                model_elapsed_ms += round(
                    (time.perf_counter() - model_started) * 1000
                )
                break
            except Exception as exc:
                exhausted = attempt == CODEX_TRANSIENT_MAX_ATTEMPTS
                if not _is_transient_codex_error(exc):
                    raise
                _append_agent_telemetry(
                    {
                        "schema_version": 1,
                        "recorded_at": utc_now(),
                        "service_name": name,
                        "backend": "codex",
                        "provider_name": provider["provider_name"],
                        "provider_base_url": provider["provider_base_url"],
                        "provider_config_hash": provider["provider_config_hash"],
                        "status": (
                            "transport_exhausted"
                            if exhausted
                            else "transport_retry"
                        ),
                        "attempt": attempt,
                        "max_attempts": CODEX_TRANSIENT_MAX_ATTEMPTS,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:2000],
                    }
                )
                await self._close_client()
                if exhausted:
                    raise
                await asyncio.sleep(_codex_retry_delay(name, prompt, attempt))
        if result is None:
            raise RuntimeError("Codex synthesis did not return a result")
        if not result.final_response:
            raise RuntimeError("Codex completed without a structured response")
        usage = getattr(result, "usage", None)
        usage_payload = (
            usage.total.model_dump(mode="json", by_alias=False)
            if usage is not None
            else None
        )
        _append_agent_telemetry(
            {
                "schema_version": 1,
                "recorded_at": utc_now(),
                "service_name": name,
                "backend": "codex",
                "provider_name": provider["provider_name"],
                "provider_base_url": provider["provider_base_url"],
                "provider_config_hash": provider["provider_config_hash"],
                "status": "completed",
                "usage_available": usage_payload is not None,
                "usage": usage_payload,
                "turn_id": getattr(result, "id", None),
                "duration_ms": getattr(result, "duration_ms", None),
            }
        )
        validation_started = time.perf_counter()
        parsed = _parse_structured_output(output_type, result.final_response)
        schema_validation_ms = round(
            (time.perf_counter() - validation_started) * 1000
        )
        return parsed, {
            "queue_wait_ms": round((dequeued_at - submitted_at) * 1000),
            "worker_client_init_ms": client_init_ms,
            "session_start_ms": session_start_ms,
            "request_upload_ms": None,
            "model_time_to_first_token_ms": None,
            "model_generation_ms": (
                int(result.duration_ms)
                if getattr(result, "duration_ms", None) is not None
                else model_elapsed_ms
            ),
            "model_round_trip_ms": model_elapsed_ms,
            "schema_validation_ms": schema_validation_ms,
        }

    def run_structured(
        self,
        *,
        name: str,
        instructions: str,
        output_type: type[T],
        prompt: str,
    ) -> tuple[T, dict[str, int | None]]:
        if self._closed:
            raise RuntimeError("Codex synthesis worker is closed")
        submitted_at = time.perf_counter()
        with self._submission_lock:
            future: Future[tuple[T, dict[str, int | None]]] = (
                asyncio.run_coroutine_threadsafe(
                    self._execute(
                        submitted_at=submitted_at,
                        name=name,
                        instructions=instructions,
                        output_type=output_type,
                        prompt=prompt,
                    ),
                    self._loop,
                )
            )
            return future.result(timeout=CODEX_TURN_TIMEOUT_SECONDS + 30)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if (
            self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=10)
