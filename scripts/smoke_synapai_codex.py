from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from research_forge.agent_runtime import (
    _run_codex_structured,
    agent_telemetry_summary,
    configure_agent_telemetry,
)


class SmokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    integer: int


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault(
        "RESEARCH_FORGE_PROVIDER_BILLING_CONTRACT",
        str(
            root
            / "stage1_runs"
            / "research-agent-evidence-publication-v1"
            / "design_revisions"
            / "synapai_gpt56_billing_contract.json"
        ),
    )
    telemetry = root / ".private" / "synapai-codex" / "smoke-telemetry.jsonl"
    telemetry.unlink(missing_ok=True)
    configure_agent_telemetry(telemetry, scope="synapai-smoke")
    result = await _run_codex_structured(
        "SynapAI compatibility smoke",
        "Return only the requested structured response. Do not inspect files or use tools.",
        SmokeResponse,
        "Set status to ok and integer to 7.",
        cwd=root,
    )
    summary = agent_telemetry_summary(telemetry, scopes={"synapai-smoke"})
    print(
        json.dumps(
            {
                "structured_output": result.model_dump(mode="json"),
                "model_call_count": summary["model_call_count"],
                "token_count": summary["token_count"],
                "standard_cost_usd": summary["standard_cost_usd"],
                "monetary_cost_usd": summary["monetary_cost_usd"],
                "provider_billing_group": summary["provider_billing_group"],
                "usage_complete": summary["event_count"] == 1,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
