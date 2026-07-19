from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .models import (
    ExperimentProposal,
    LiteratureSearchPlan,
    LiteratureScreeningOutput,
    LiteratureSynthesis,
    ResearchPlanDraft,
)
from .study_models import SemanticClaimJudgmentBatch, StudyFinalizerOutput


ROOT = Path(__file__).resolve().parents[1]
T = TypeVar("T", bound=BaseModel)
SUPPORTED_BACKENDS = {"codex", "api"}
DEFAULT_CODEX_MODEL = "gpt-5.4"


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
