from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import tomllib
from decimal import Decimal
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
from .models import MacroStage
from .pipeline_contracts import (
    MAX_PROMPT_FRAGMENT_CHARACTERS,
    MAX_PROMPT_TOTAL_CHARACTERS,
    PromptEnvelope,
    PromptFragment,
    relevant_failure_memory,
)
from .study_models import SemanticClaimJudgmentBatch, StudyFinalizerOutput


ROOT = Path(__file__).resolve().parents[1]
T = TypeVar("T", bound=BaseModel)
SUPPORTED_BACKENDS = {"codex", "api"}
DEFAULT_CODEX_MODEL = "gpt-5.6-terra"
CODEX_TRANSIENT_MAX_ATTEMPTS = 8
CODEX_TURN_TIMEOUT_SECONDS = 20 * 60
CODEX_TRANSIENT_BACKOFF_SECONDS = (15, 30, 60, 120, 240, 480, 600)
_TRANSIENT_CODEX_MARKERS = (
    "stream disconnected before completion",
    "error sending request",
    "connection reset",
    "connection closed",
    "codex process closed stdout",
    "transportclosederror",
    "timeouterror",
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
    input_tokens = sum(int(item["usage"]["input_tokens"]) for item in completed)
    cached_input_tokens = sum(
        int(item["usage"]["cached_input_tokens"]) for item in completed
    )
    output_tokens = sum(int(item["usage"]["output_tokens"]) for item in completed)
    provider_urls = {
        str(item.get("provider_base_url") or "managed") for item in completed
    }
    if provider_urls == {"managed"} or not completed:
        monetary_cost_usd = 0.0
        standard_cost_usd = 0.0
        cost_basis = (
            "Codex ChatGPT subscription marginal API charge; subscription allocation and "
            "opportunity cost are not estimated"
        )
        billing = provider_billing_binding()
    else:
        if len(provider_urls) != 1:
            raise ValueError("completed model calls mix provider billing domains")
        configured = _configured_billing_contract()
        if configured is None:
            raise ValueError("third-party Codex calls require a frozen billing contract")
        contract_path, contract = configured
        provider_url = next(iter(provider_urls)).rstrip("/")
        if provider_url != str(contract["provider_base_url"]).rstrip("/"):
            raise ValueError("telemetry provider differs from the frozen billing contract")
        models = {str(item.get("model") or "") for item in completed}
        if models != {str(contract["model"])}:
            raise ValueError("telemetry model differs from the frozen billing contract")
        if cached_input_tokens > input_tokens:
            raise ValueError("cached input tokens exceed total input tokens")
        rates = contract["rates_per_million_tokens"]
        standard = (
            Decimal(input_tokens - cached_input_tokens)
            * Decimal(str(rates["noncached_input"]))
            + Decimal(cached_input_tokens) * Decimal(str(rates["cached_input"]))
            + Decimal(output_tokens) * Decimal(str(rates["output"]))
        ) / Decimal(1_000_000)
        actual = standard * Decimal(str(contract["rate_multiplier"]))
        standard_cost_usd = float(standard)
        monetary_cost_usd = float(actual)
        billing = {
            "provider_billing_contract_hash": hashlib.sha256(
                contract_path.read_bytes()
            ).hexdigest(),
            "provider_billing_group": str(contract["provider_group"]),
        }
        cost_basis = (
            "Frozen provider token rates and multiplier, verified against an authenticated "
            "provider usage record before formal execution"
        )
    return {
        "schema_version": 1,
        "scopes": sorted(scopes),
        "model_call_count": len(completed),
        "token_count": sum(int(item["usage"]["total_tokens"]) for item in completed),
        "input_token_count": input_tokens,
        "cached_input_token_count": cached_input_tokens,
        "output_token_count": output_tokens,
        "reasoning_output_token_count": sum(
            int(item["usage"]["reasoning_output_tokens"]) for item in completed
        ),
        "standard_cost_usd": standard_cost_usd,
        "monetary_cost_usd": monetary_cost_usd,
        "monetary_cost_basis": cost_basis,
        **billing,
        "event_count": len(events),
    }


def _is_transient_codex_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in message for marker in _TRANSIENT_CODEX_MARKERS)


def _codex_retry_delay(name: str, prompt: str, attempt: int) -> int:
    if not 1 <= attempt < CODEX_TRANSIENT_MAX_ATTEMPTS:
        raise ValueError("retry delay requires a non-final failed attempt")
    base = CODEX_TRANSIENT_BACKOFF_SECONDS[attempt - 1]
    digest = hashlib.sha256(
        f"{name}\0{prompt}\0{attempt}".encode("utf-8")
    ).digest()
    return base + (int.from_bytes(digest[:2], "big") % 17)


def _instructions(name: str) -> str:
    configured = os.getenv("RESEARCH_FORGE_PROMPT_ROOT", "").strip()
    root = Path(configured).resolve() if configured else Path(__file__).resolve().parent / "prompts"
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ValueError(f"prompt path escapes configured root: {name}")
    return path.read_text(encoding="utf-8")


def _load_local_runtime_env() -> None:
    """Load the ignored local provider config before backend selection.

    Previously ``.env.local`` was loaded only *after* ``backend_name()`` had
    already selected the default Codex backend.  That made a correctly written
    API configuration silently use the managed subscription instead.
    """
    if os.getenv("RESEARCH_FORGE_SKIP_LOCAL_ENV", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env.local", override=False)


def backend_name() -> str:
    backend = os.getenv("RESEARCH_FORGE_BACKEND", "codex").strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        choices = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise ValueError(f"unsupported RESEARCH_FORGE_BACKEND={backend!r}; choose {choices}")
    return backend


def _configured_codex_home() -> Path | None:
    raw = (
        os.getenv("RESEARCH_FORGE_CODEX_HOME", "").strip()
        or os.getenv("CODEX_HOME", "").strip()
    )
    if not raw:
        return None
    home = Path(raw).expanduser().resolve()
    if not (home / "config.toml").is_file():
        raise FileNotFoundError(f"Codex provider config is missing: {home / 'config.toml'}")
    if not (home / "auth.json").is_file():
        raise FileNotFoundError(f"Codex provider authentication is missing: {home / 'auth.json'}")
    return home


def _configured_billing_contract() -> tuple[Path, dict[str, Any]] | None:
    raw = os.getenv("RESEARCH_FORGE_PROVIDER_BILLING_CONTRACT", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"provider billing contract is missing: {path}")
    contract = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "provider_name",
        "provider_base_url",
        "model",
        "provider_group",
        "rates_per_million_tokens",
        "rate_multiplier",
    }
    missing = sorted(required.difference(contract))
    if missing:
        raise ValueError(f"provider billing contract is incomplete: {', '.join(missing)}")
    rates = contract["rates_per_million_tokens"]
    if not isinstance(rates, dict) or not {
        "noncached_input",
        "cached_input",
        "output",
    }.issubset(rates):
        raise ValueError("provider billing contract lacks the frozen token rates")
    return path, contract


def provider_billing_binding() -> dict[str, str]:
    configured = _configured_billing_contract()
    if configured is None:
        return {
            "provider_billing_contract_hash": "0" * 64,
            "provider_billing_group": "managed-subscription",
        }
    path, contract = configured
    return {
        "provider_billing_contract_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "provider_billing_group": str(contract["provider_group"]),
    }


def codex_provider_binding() -> dict[str, str]:
    home = _configured_codex_home()
    if home is None:
        marker = b"openai-managed-codex-default"
        return {
            "provider_name": "openai-managed",
            "provider_base_url": "managed",
            "provider_config_hash": hashlib.sha256(marker).hexdigest(),
            "provider_model": os.getenv(
                "RESEARCH_FORGE_CODEX_MODEL", DEFAULT_CODEX_MODEL
            ).strip(),
            "provider_home_source": "managed-default",
        }
    config_path = home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    provider_key = str(config.get("model_provider") or "").strip()
    providers = config.get("model_providers") or {}
    provider = providers.get(provider_key) if isinstance(providers, dict) else None
    if not provider_key or not isinstance(provider, dict):
        raise ValueError("Codex provider config does not define the selected model provider")
    base_url = str(provider.get("base_url") or "").strip()
    if not base_url.startswith("https://"):
        raise ValueError("Codex provider base_url must use HTTPS")
    provider_model = (
        os.getenv("RESEARCH_FORGE_CODEX_MODEL", "").strip()
        or str(config.get("model") or "").strip()
    )
    if not provider_model:
        raise ValueError("Codex provider model is not configured")
    return {
        "provider_name": provider_key,
        "provider_base_url": base_url.rstrip("/"),
        "provider_config_hash": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "provider_model": provider_model,
        "provider_home_source": "explicit-isolated-home",
    }


def _codex_process_env() -> dict[str, str]:
    child_env = {
        "OPENAI_API_KEY": "",
        "CODEX_API_KEY": "",
        # API-backend configuration loaded from .env.local must not redirect a
        # managed Codex/OAuth subprocess. Explicit custom providers are routed
        # by their isolated CODEX_HOME below.
        "OPENAI_BASE_URL": "",
    }
    home = _configured_codex_home()
    if home is not None:
        child_env["CODEX_HOME"] = str(home)
        # CC-Switch may intentionally own the endpoint/model configuration but
        # leave Codex's OAuth-shaped auth.json without a provider key.  For an
        # explicit custom provider only, forward the already configured local
        # key to the child process.  Managed Codex retains the deliberate blank
        # variables above, so subscription credentials never leak into a
        # provider subprocess.
        if codex_provider_binding()["provider_base_url"] != "managed":
            provider_key = os.getenv("OPENAI_API_KEY", "").strip()
            if provider_key:
                child_env["OPENAI_API_KEY"] = provider_key
    return child_env


def _configured_codex_model() -> str:
    return codex_provider_binding()["provider_model"]


def model_name() -> str:
    if backend_name() == "codex":
        return f"codex:{_configured_codex_model()}"
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
    model = _configured_codex_model()
    provider = codex_provider_binding()
    config = CodexConfig(
        cwd=str(working_dir),
        env=_codex_process_env(),
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
                result = await asyncio.wait_for(
                    thread.run(
                        prompt,
                        approval_mode=ApprovalMode.deny_all,
                        cwd=str(working_dir),
                        output_schema=_strict_output_schema(output_type),
                        sandbox=Sandbox.read_only,
                    ),
                    timeout=CODEX_TURN_TIMEOUT_SECONDS,
                )
            break
        except Exception as exc:
            if not _is_transient_codex_error(exc):
                raise
            exhausted = attempt == CODEX_TRANSIENT_MAX_ATTEMPTS
            _append_agent_telemetry(
                {
                    "schema_version": 1,
                    "recorded_at": utc_now(),
                    "scope": _TELEMETRY_SCOPE.get(),
                    "service_name": name,
                    "backend": "codex",
                    "model": model,
                    "provider_name": provider["provider_name"],
                    "provider_base_url": provider["provider_base_url"],
                    "provider_config_hash": provider["provider_config_hash"],
                    "status": "transport_exhausted" if exhausted else "transport_retry",
                    "attempt": attempt,
                    "max_attempts": CODEX_TRANSIENT_MAX_ATTEMPTS,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:2000],
                    "usage_available": False,
                }
            )
            if exhausted:
                raise
            await asyncio.sleep(_codex_retry_delay(name, prompt, attempt))
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
            "provider_name": provider["provider_name"],
            "provider_base_url": provider["provider_base_url"],
            "provider_config_hash": provider["provider_config_hash"],
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
    stage: MacroStage,
    skill_id: str,
) -> T:
    _load_local_runtime_env()
    # Every existing agent entry point now receives its untrusted project
    # material through the same bounded envelope.  This is deliberately done
    # before backend selection so Codex and API paths have identical limits.
    prompt_root = Path(cwd or ROOT).resolve()
    request_fragments = _runtime_request_fragments(prompt)
    failure_fragments = relevant_failure_memory(
        prompt_root, stage=stage, skill_id=skill_id
    )
    remaining = MAX_PROMPT_TOTAL_CHARACTERS - sum(
        len(item.text) for item in request_fragments
    )
    admitted_failure_fragments: list[PromptFragment] = []
    for fragment in failure_fragments:
        if len(fragment.text) > remaining:
            break
        admitted_failure_fragments.append(fragment)
        remaining -= len(fragment.text)
    bounded_prompt = PromptEnvelope(
        stage=stage,
        skill_id=skill_id,
        fragments=[
            *request_fragments,
            *admitted_failure_fragments,
        ],
    ).render()
    if backend_name() == "codex":
        return await _run_codex_structured(
            name,
            instructions,
            output_type,
            bounded_prompt,
            cwd=cwd,
        )
    return await _run_api_structured(name, instructions, output_type, bounded_prompt)


def _runtime_request_fragments(prompt: str) -> list[PromptFragment]:
    """Split a large structured request without dropping current evidence.

    The envelope has both a per-fragment and a total budget.  Stage 4 prompts
    can legitimately exceed the former after combining an outline, claim map,
    reporting register, and literature manifest.  Chunking avoids treating
    that valid aggregate as one oversized fragment; failure memory is admitted
    only from the remaining total budget.
    """

    if not prompt:
        raise ValueError("runtime request cannot be empty")
    if len(prompt) > MAX_PROMPT_TOTAL_CHARACTERS:
        raise ValueError(
            "runtime request exceeds the total prompt budget; compact the "
            "task-specific evidence view before invoking the model"
        )
    fragments: list[PromptFragment] = []
    for index, start in enumerate(
        range(0, len(prompt), MAX_PROMPT_FRAGMENT_CHARACTERS),
        start=1,
    ):
        fragments.append(
            PromptFragment(
                source_id=f"runtime-request-part-{index:02d}",
                kind="operator_request",
                text=prompt[
                    start : start + MAX_PROMPT_FRAGMENT_CHARACTERS
                ],
            )
        )
    return fragments


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
        stage=MacroStage.DISCOVERY,
        skill_id="research-question",
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
        stage=MacroStage.DISCOVERY,
        skill_id="literature-discovery",
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
        stage=MacroStage.DISCOVERY,
        skill_id="literature-synthesis",
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
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-writing",
    )


async def generate_bundle_paper_outline(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .paper_authoring import HierarchicalPaperOutline

    return await _run_structured(
        "Evidence-bound manuscript architect",
        _instructions("bundle_paper_outline.md"),
        HierarchicalPaperOutline,
        prompt,
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-outline",
    )


async def generate_bundle_publication_title(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .paper_authoring import PublicationTitleCandidate

    return await _run_structured(
        "Evidence-bound academic title editor",
        _instructions("bundle_publication_title.md"),
        PublicationTitleCandidate,
        prompt,
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="publication-title",
    )


async def review_bundle_paper_artifact(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .paper_authoring import RoleReview

    return await _run_structured(
        "Blinded manuscript panel reviewer",
        _instructions("bundle_paper_review.md"),
        RoleReview,
        prompt,
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-review",
    )


async def review_nuwa_claim_packet(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .nuwa_panel import NuwaPersonaReview

    return await _run_structured(
        "Blinded Nuwa claim reviewer",
        _instructions("nuwa_claim_review.md"),
        NuwaPersonaReview,
        prompt,
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="nuwa-claim-review",
    )


async def revise_bundle_paper_outline(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .paper_authoring import HierarchicalPaperOutline

    return await _run_structured(
        "Evidence-bound manuscript outline reviser",
        _instructions("bundle_paper_outline_revision.md"),
        HierarchicalPaperOutline,
        prompt,
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-outline-revision",
    )


async def revise_bundle_paper_draft(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .paper_expansion import PaperDraftSections

    return await _run_structured(
        "Evidence-bound manuscript draft reviser",
        _instructions("bundle_paper_draft_revision.md"),
        PaperDraftSections,
        prompt,
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-draft-revision",
    )


async def humanize_bundle_paper_draft(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .paper_expansion import PaperDraftSections

    return await _run_structured(
        "Claim-preserving academic prose editor",
        _instructions("bundle_paper_humanizer.md"),
        PaperDraftSections,
        prompt,
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-humanizer",
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
        stage=MacroStage.DISCOVERY,
        skill_id="literature-screening",
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
        stage=MacroStage.EXPERIMENTATION,
        skill_id="experiment-design",
    )


async def generate_stage3_profile_v1_package(
    prompt: str,
    *,
    cwd: str | Path | None = None,
):
    from .stage_three_generation import GeneratedProfileV1Package

    return await _run_structured(
        "Stage 3 Profile v1 implementation builder",
        _instructions("stage3_profile_v1_builder.md"),
        GeneratedProfileV1Package,
        prompt,
        cwd=cwd,
        stage=MacroStage.EXPERIMENTATION,
        skill_id="stage3-profile-v1-builder",
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
        stage=MacroStage.SYNTHESIS,
        skill_id="study-finalizer",
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
        stage=MacroStage.SYNTHESIS,
        skill_id="claim-audit",
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
        stage=MacroStage.SYNTHESIS,
        skill_id="terminology-extraction",
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
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-localization",
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
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-localization-repair",
    )


def backend_status() -> dict[str, Any]:
    _load_local_runtime_env()
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
    status.update(codex_provider_binding())
    status.update(provider_billing_binding())
    try:
        config = CodexConfig(
            cwd=str(ROOT),
            env=_codex_process_env(),
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
