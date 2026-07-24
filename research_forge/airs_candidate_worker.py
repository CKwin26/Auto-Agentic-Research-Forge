"""One killable Codex candidate worker for the official AIRS bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .aira_dojo_bridge import AirsCandidateBudget, AirsRadRun, _run_codex_airs_candidate_in_process


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request_path = Path(args.request)
    data = json.loads(request_path.read_text(encoding="utf-8"))
    run = AirsRadRun(
        task_dir=Path(data["task_dir"]), agent_data_dir=Path(data["agent_data_dir"]),
        agent_log_dir=Path(data["agent_log_dir"]), evaluator_data_dir=Path(data["evaluator_data_dir"]),
        python=Path(data["python"]), run_manifest_path=Path(data["run_manifest_path"]) if data.get("run_manifest_path") else None,
    )
    result = asyncio.run(_run_codex_airs_candidate_in_process(run, budget=AirsCandidateBudget(
        wall_seconds=int(data["wall_seconds"]), allow_dependency_install=bool(data["allow_dependency_install"]),
    )))
    (run.agent_log_dir / "candidate_worker_result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The Codex client may retain app-server handles after a completed turn.
    # The parent has already received the durable result file, so exit directly.
    os._exit(0)


if __name__ == "__main__":
    main()
