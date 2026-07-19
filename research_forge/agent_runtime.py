from __future__ import annotations

import asyncio
import contextvars
import json
import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .models import (
    ExperimentProposal,
    LiteratureSearchPlan,
    LiteratureScreeningOutput,
    LiteratureSynthesis,
    LocalizedBlockDraft,
    ResearchPlanDraft,
    TermCandidateBatch,
    utc_now,
)
from .study_models import SemanticClaimJudgmentBatch, StudyFinalizerOutput


ROOT = Path(__file__).resolve().parents[1]
T = TypeVar("T", bound=BaseModel)
SUPPORTED_BACKENDS = {"codex", "api"}
DEFAULT_CODEX_MODEL = "gpt-5.4"
CODEX_TRANSIENT_MAX_ATTEMPTS = 3
_TRANSIENT_CODEX_MARKERS = (
    "stream disconnected before completion",
    "error sending request",
    "connection reset",
    "connection closed",
    "service unavailable",
    "bad gateway",
)
_TELEMETRY_PATH: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "research_forge_agent_telemetry_path", default=None
)
_TELEMETRY_SCOPE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "research_forge_agent_telemetry_scope", default=None
)


def configure_agent_telemetry(path: Path, *, scope: str) -> None:
    """Route subsequent task-local agent usage events to an append-only ledger."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    _TELEMETRY_PATH.set(path)
    _TELEMETRY_SCOPE.set(scope)


def _append_agent_telemetry(event: dict[str, Any]) -> None:
    path = _TELEMETRY_PATH.get()
    if path is None:
        return
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def agent_telemetry_summary(path: Path, *, scopes: set[str]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                event = json.loads(line)
                if str(event.get("scope")) in scopes:
                    events.append(event)
    completed = [item for item in events if item.get("status") == "completed"]
    missing = [item for item in completed if item.get("usage_available") is not True]
    if missing:
        raise ValueError("one or more completed model calls lack token telemetry")
    return {
        "schema_version": 1,
        "scopes": sorted(scopes),
        "model_call_count": len(completed),
        "token_count": sum(int(item["usage"]["total_tokens"]) for item in completed),
        "input_token_count": sum(int(item["usage"]["input_tokens"]) for item in completed),
        "cached_input_token_count": sum(
            int(item["usage"]["cached_input_tokens"]) for item in completed
        ),
        "output_token_count": sum(int(item["usage"]["output_tokens"]) for item in completed),
        "reasoning_output_token_count": sum(
            int(item["usage"]["reasoning_output_tokens"]) for item in completed
        ),
        "monetary_cost_usd": 0.0,
        "monetary_cost_basis": (
            "Codex ChatGPT subscription marginal API charge; subscription allocation and "
            "opportunity cost are not estimated"
        ),
        "event_count": len(events),
    }


def _is_transient_codex_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_CODEX_MARKERS)


def _instructions(name: str) -> str:
    configured = os.getenv("RESEARCH_FORGE_PROMPT_ROOT", "").strip()
    root = Path(configured).resolve() if configured else Path(__file__).resolve().parent / "prompts"
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ValueError(f"prompt path escapes configured root: {name}")
    return path.read_text(encoding="utf-8")


def backend_name() -> str:
    backend = os.getenv("RESEARCH_FORGE_BACKEND", "codex").strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        choices = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise ValueError(f"unsupported RESEARCH_FORGE_BACKEND={backend!r}; choose {choices}")
    return backend


def model_name() -> str:
    if backend_name() == "codex":
        return f"codex:{os.getenv('RESEARCH_FORGE_CODEX_MODEL', DEFAULT_CODEX_MODEL)}"
    return f"api:{os.getenv('AUTORESEARCH_MODEL', 'gpt-5.6-terra')}"


def _strict_output_schema(output_type: type[BaseModel]) -> dict[str, Any]:
    schema = output_type.model_json_schema()

    def normalize(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                normalize(item)
            return
        if not isinstance(value, dict):
            return
        value.pop("default", None)
        for item in value.values():
            normalize(item)
        properties = value.get("properties")
        if isinstance(properties, dict):
            value["additionalProperties"] = False
            value["required"] = list(properties)

    normalize(schema)
    return schema


def _parse_structured_output(output_type: type[T], value: Any) -> T:
    if isinstance(value, output_type):
        return value
    if isinstance(value, str):
        return output_type.model_validate_json(value)
    return output_type.model_validate(value)


async def _run_codex_structured(
    name: str,
    instructions: str,
    output_type: type[T],
    prompt: str,
    *,
    cwd: str | Path | None,
) -> T:
    from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

    working_dir = Path(cwd or ROOT).resolve()
    model = os.getenv("RESEARCH_FORGE_CODEX_MODEL", DEFAULT_CODEX_MODEL).strip()
    config = CodexConfig(
        cwd=str(working_dir),
        env={"OPENAI_API_KEY": "", "CODEX_API_KEY": ""},
        client_name="research_forge",
        client_title="Research Forge",
    )
    result = None
    for attempt in range(1, CODEX_TRANSIENT_MAX_ATTEMPTS + 1):
        try:
            async with AsyncCodex(config) as codex:
                thread = await codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    base_instructions=instructions,
                    cwd=str(working_dir),
                    ephemeral=True,
                    model=model,
                    sandbox=Sandbox.read_only,
                    service_name=name,
                )
                result = await thread.run(
                    prompt,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(working_dir),
                    output_schema=_strict_output_schema(output_type),
                    sandbox=Sandbox.read_only,
                )
            break
        except RuntimeError as exc:
            if not _is_transient_codex_error(exc) or attempt == CODEX_TRANSIENT_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(0.5 * attempt)
    assert result is not None
    usage = result.usage
    usage_payload = (
        usage.total.model_dump(mode="json", by_alias=False) if usage is not None else None
    )
    _append_agent_telemetry(
        {
            "schema_version": 1,
            "recorded_at": utc_now(),
            "scope": _TELEMETRY_SCOPE.get(),
            "service_name": name,
            "backend": "codex",
            "model": model,
            "status": "completed",
            "usage_available": usage_payload is not None,
            "usage": usage_payload,
            "turn_id": result.id,
            "duration_ms": result.duration_ms,
        }
    )
    if _TELEMETRY_PATH.get() is not None and usage_payload is None:
        raise RuntimeError("Codex completed without required token usage telemetry")
    if not result.final_response:
        raise RuntimeError("Codex completed without a final structured response")
    return _parse_structured_output(output_type, result.final_response)


async def _run_api_structured(
    name: str,
    instructions: str,
    output_type: type[T],
    prompt: str,
) -> T:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env.local")
    from agents import Agent, Runner

    agent = Agent(
        name=name,
        instructions=instructions,
        model=os.getenv("AUTORESEARCH_MODEL", "gpt-5.6-terra"),
        output_type=output_type,
    )
    result = await Runner.run(agent, prompt)
    return _parse_structured_output(output_type, result.final_output)


async def _run_structured(
    name: str,
    instructions: str,
    output_type: type[T],
    prompt: str,
    *,
    cwd: str | Path | None,
) -> T:
    if backend_name() == "codex":
        return await _run_codex_structured(
            name,
            instructions,
            output_type,
            prompt,
            cwd=cwd,
        )
    return await _run_api_structured(name, instructions, output_type, prompt)


async def generate_plan(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> ResearchPlanDraft:
    return await _run_structured(
        "Research contract planner",
        _instructions("planner.md"),
        ResearchPlanDraft,
        prompt,
        cwd=cwd,
    )


async def generate_literature_search_plan(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> LiteratureSearchPlan:
    return await _run_structured(
        "Scholarly search strategist",
        _instructions("literature_search.md"),
        LiteratureSearchPlan,
        prompt,
        cwd=cwd,
    )


async def generate_literature_synthesis(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> LiteratureSynthesis:
    return await _run_structured(
        "Evidence-bound literature synthesizer",
        _instructions("literature_synthesis.md"),
        LiteratureSynthesis,
        prompt,
        cwd=cwd,
    )


async def generate_bundle_paper_draft(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    # Imported lazily to keep the core agent runtime independent of the
    # project-bundle orchestration module during normal startup.
    from .paper_expansion import PaperDraftSections

    return await _run_structured(
        "Evidence-bound full manuscript writer",
        _instructions("bundle_paper_writer.md"),
        PaperDraftSections,
        prompt,
        cwd=cwd,
    )


async def screen_literature_candidates(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> LiteratureScreeningOutput:
    return await _run_structured(
        "Bounded scholarly relevance screener",
        _instructions("literature_screen.md"),
        LiteratureScreeningOutput,
        prompt,
        cwd=cwd,
    )


async def generate_proposal(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> ExperimentProposal:
    return await _run_structured(
        "Falsifiable experiment designer",
        _instructions("experimenter.md"),
        ExperimentProposal,
        prompt,
        cwd=cwd,
    )


async def generate_study_finalizer(
    prompt: str,
    *,
    instructions: str,
    cwd: str | Path | None = None,
) -> StudyFinalizerOutput:
    return await _run_structured(
        "Shared study conclusion finalizer",
        instructions,
        StudyFinalizerOutput,
        prompt,
        cwd=cwd,
    )


async def judge_study_claims(
    prompt: str,
    *,
    instructions: str,
    cwd: str | Path | None = None,
) -> SemanticClaimJudgmentBatch:
    return await _run_structured(
        "Arm-blinded protected claim evaluator",
        instructions,
        SemanticClaimJudgmentBatch,
        prompt,
        cwd=cwd,
    )


async def extract_terminology_candidates(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> TermCandidateBatch:
    return await _run_structured(
        "Academic terminology extractor",
        _instructions("terminology_extract.md"),
        TermCandidateBatch,
        prompt,
        cwd=cwd,
    )


async def localize_markdown_block(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> LocalizedBlockDraft:
    return await _run_structured(
        "Chinese academic manuscript localizer",
        _instructions("manuscript_localize.md"),
        LocalizedBlockDraft,
        prompt,
        cwd=cwd,
    )


async def repair_localized_markdown_block(
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> LocalizedBlockDraft:
    return await _run_structured(
        "Chinese terminology repairer",
        _instructions("localization_repair.md"),
        LocalizedBlockDraft,
        prompt,
        cwd=cwd,
    )


def backend_status() -> dict[str, Any]:
    backend = backend_name()
    status: dict[str, Any] = {"backend": backend, "model": model_name()}
    if backend == "api":
        try:
            import agents

            status["api_sdk_installed"] = True
            status["api_sdk_version"] = getattr(agents, "__version__", "unknown")
        except ImportError:
            status["api_sdk_installed"] = False
        return status

    try:
        import openai_codex
        from openai_codex import Codex, CodexConfig
    except ImportError:
        status["codex_sdk_installed"] = False
        status["codex_authenticated"] = False
        return status

    status["codex_sdk_installed"] = True
    status["codex_sdk_version"] = openai_codex.__version__
    try:
        config = CodexConfig(
            cwd=str(ROOT),
            env={"OPENAI_API_KEY": "", "CODEX_API_KEY": ""},
            client_name="research_forge_doctor",
            client_title="Research Forge Doctor",
        )
        with Codex(config) as codex:
            account = codex.account().model_dump(mode="json", exclude_none=True)
        safe_account = account.get("account") or {}
        status["codex_authenticated"] = bool(safe_account)
        status["codex_account_type"] = safe_account.get("type")
        status["codex_plan_type"] = safe_account.get("plan_type")
    except Exception as exc:
        status["codex_authenticated"] = False
        status["codex_error"] = f"{type(exc).__name__}: {exc}"
    return status
