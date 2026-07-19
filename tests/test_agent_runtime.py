from __future__ import annotations

from typing import Any

import pytest

from research_forge.agent_runtime import (
    _append_agent_telemetry,
    _is_transient_codex_error,
    _parse_structured_output,
    _strict_output_schema,
    agent_telemetry_summary,
    backend_name,
    configure_agent_telemetry,
    model_name,
)
from research_forge.models import ExperimentProposal, ParameterOverride


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def test_codex_is_the_default_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_FORGE_BACKEND", raising=False)
    monkeypatch.delenv("RESEARCH_FORGE_CODEX_MODEL", raising=False)
    assert backend_name() == "codex"
    assert model_name() == "codex:gpt-5.4"


def test_backend_must_be_explicitly_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_FORGE_BACKEND", "mystery")
    with pytest.raises(ValueError, match="unsupported"):
        backend_name()


def test_codex_schema_is_strict_and_has_no_defaults() -> None:
    schema = _strict_output_schema(ExperimentProposal)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert not _contains_key(schema, "default")


def test_codex_structured_response_is_revalidated() -> None:
    proposal = ExperimentProposal(
        title="Parameter-only test",
        hypothesis="A controlled scalar override will improve the fixed metric.",
        rationale="The experiment changes one variable and leaves the evaluator fixed.",
        expected_observation="The score increases over the verified baseline.",
        falsification_condition="The score does not improve or the run is invalid.",
        success_criteria=["The fixed metric improves."],
        parameters=[
            ParameterOverride(
                name="score",
                value=0.7,
                reason="Test one bounded scalar value.",
            )
        ],
        estimated_minutes=1,
    )
    parsed = _parse_structured_output(ExperimentProposal, proposal.model_dump_json())
    assert parsed == proposal


def test_only_transport_failures_are_retryable() -> None:
    assert _is_transient_codex_error(
        RuntimeError("stream disconnected before completion: error sending request")
    )
    assert not _is_transient_codex_error(
        RuntimeError("duplicate metric names carry conflicting values")
    )


def test_agent_telemetry_summary_preserves_token_breakdown(tmp_path) -> None:
    path = tmp_path / "usage.jsonl"
    configure_agent_telemetry(path, scope="shared_upstream")
    _append_agent_telemetry(
        {
            "scope": "shared_upstream",
            "status": "completed",
            "usage_available": True,
            "usage": {
                "cached_input_tokens": 2,
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_output_tokens": 3,
                "total_tokens": 18,
            },
        }
    )
    summary = agent_telemetry_summary(path, scopes={"shared_upstream"})
    assert summary["model_call_count"] == 1
    assert summary["token_count"] == 18
    assert summary["monetary_cost_usd"] == 0.0
    assert "subscription" in summary["monetary_cost_basis"]


def test_agent_telemetry_fails_closed_when_usage_is_missing(tmp_path) -> None:
    path = tmp_path / "usage.jsonl"
    configure_agent_telemetry(path, scope="treatment")
    _append_agent_telemetry(
        {
            "scope": "treatment",
            "status": "completed",
            "usage_available": False,
            "usage": None,
        }
    )
    with pytest.raises(ValueError, match="lack token telemetry"):
        agent_telemetry_summary(path, scopes={"treatment"})
