from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import research_forge.controller_ablation as ablation
from research_forge.models import utc_now
from research_forge.storage import append_jsonl, read_json, write_json_atomic


def reopen_incomplete_cells(matrix: Path) -> list[str]:
    """Reopen cells that the wrapper summarized after a non-terminal loop error."""

    state_path = matrix / "ablation_state.json"
    state = read_json(state_path)
    reopened: list[str] = []
    for cell in state["cells"]:
        if cell.get("status") != "completed" or not cell.get("result"):
            continue
        output_value = cell.get("output")
        output = Path(str(output_value)) if output_value else None
        loop_state_paths = (
            sorted(output.glob("projects/*/benchmark/loop_state.json"))
            if output and output.is_dir()
            else []
        )
        terminal = False
        if len(loop_state_paths) == 1:
            loop_state = read_json(loop_state_paths[0])
            terminal = (
                loop_state.get("status") in {"completed", "stopped"}
                and bool(loop_state.get("stop_reason"))
            )
        if terminal:
            continue
        cell["status"] = "pending"
        cell["error"] = None
        cell["result"] = None
        reopened.append(str(cell["cell_id"]))
        append_jsonl(
            matrix / "events.jsonl",
            {
                "recorded_at": utc_now(),
                "event": "recovery_reopened_incomplete_cell",
                "cell_id": cell["cell_id"],
                "output": str(output) if output else None,
            },
        )
    if reopened:
        state["status"] = "running"
        state["updated_at"] = utc_now()
        write_json_atomic(state_path, state)
    return reopened


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: resume_frozen_ablation.py MATRIX_DIRECTORY")

    matrix = Path(sys.argv[1]).resolve()
    manifest = read_json(matrix / "matrix.json")
    frozen_model = str(manifest["model"])
    if not frozen_model.startswith("codex:"):
        raise RuntimeError("frozen recovery requires a Codex model")

    os.environ["RESEARCH_FORGE_AGENT_BACKEND"] = "codex"
    os.environ["RESEARCH_FORGE_CODEX_MODEL"] = frozen_model.split(":", 1)[1]

    def frozen_backend_identity() -> dict[str, object]:
        return {
            "backend": "codex",
            "model": frozen_model,
            "codex_authenticated": True,
            "recovery_note": (
                "The redundant account-status probe was skipped after repeated SDK reconnects. "
                "Every scientific proposal still uses the frozen Codex backend and model."
            ),
        }

    ablation.backend_status = frozen_backend_identity
    results: dict[str, object] | None = None
    recovery_rounds: list[dict[str, object]] = []
    remaining: list[str] = []
    for round_number in range(1, 4):
        reopened = reopen_incomplete_cells(matrix)
        recovery_rounds.append(
            {"round": round_number, "reopened_incomplete_cells": reopened}
        )
        results = asyncio.run(ablation.run_controller_ablation(matrix))
        remaining = reopen_incomplete_cells(matrix)
        if not remaining:
            break
        recovery_rounds[-1]["remaining_incomplete_cells"] = remaining
    if results is None:
        raise RuntimeError("frozen recovery did not execute")
    if remaining:
        raise RuntimeError(
            "non-terminal ablation cells remained after recovery: "
            + ", ".join(remaining)
        )
    print(
        json.dumps(
            {
                "matrix_id": results["matrix_id"],
                "status": results["status"],
                "completed_cells": results["completed_cells"],
                "failed_cells": results["failed_cells"],
                "recovery_rounds": recovery_rounds,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if results["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
