"""Resumable official AIRS RAD matrix runner.

The runner never substitutes a local metric for official evaluation.  It only
prepares official bundles, invokes a candidate through the aira-dojo bridge,
and then invokes the original official evaluator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .aira_dojo_bridge import AirsCandidateBudget, AirsRadRun, run_codex_airs_candidate, run_public_airs_baseline
from .official_airs import evaluate_official_airs_submission, import_official_airs_task, prepare_official_airs_task


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def run_matrix(
    matrix_path: Path,
    *,
    source_root: Path,
    import_root: Path,
    shared_data: Path,
    run_root: Path,
    evaluator_python: Path,
    candidate: str,
    candidate_seconds: int,
    require_cuda: bool = False,
    runtime: str = "local",
    docker_image: str | None = None,
) -> dict[str, int]:
    matrix_path, source_root, import_root = matrix_path.resolve(), source_root.resolve(), import_root.resolve()
    shared_data, run_root, evaluator_python = shared_data.resolve(), run_root.resolve(), evaluator_python.resolve()
    if runtime not in {"local", "docker"}:
        raise ValueError("runtime must be 'local' or 'docker'")
    if runtime == "docker" and not docker_image:
        raise ValueError("docker runtime requires --docker-image")
    if require_cuda and runtime == "local":
        probe = subprocess.run(
            [str(evaluator_python), "-c", "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)"],
            capture_output=True, text=True, check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError("GPU matrix requires torch.cuda.is_available() in evaluator-python")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    tasks, seeds = list(matrix["tasks"]), list(matrix["seeds"])
    if len(tasks) * len(seeds) != int(matrix["cell_count"]):
        raise ValueError("matrix cell_count does not match tasks times seeds")
    ledger, counts = run_root / "official-matrix-ledger.jsonl", {"completed": 0, "failed": 0, "skipped": 0}
    for task_name in tasks:
        source, task = source_root / task_name, import_root / task_name
        if not task.exists():
            import_official_airs_task(source, import_root)
        for seed in seeds:
            cell = run_root / task_name / f"seed-{seed}"
            manifest = cell / "run-manifest.json"
            if manifest.is_file():
                saved = json.loads(manifest.read_text(encoding="utf-8"))
                if saved.get("evaluation_prepared"):
                    counts["skipped"] += 1
                    continue
                # A candidate may have completed while an evaluator dependency
                # was unavailable.  Preserve that bounded API result and retry
                # only the protected scoring stage on a later matrix resume.
                existing_submission = cell / "agent-log" / "submission.csv"
                if existing_submission.is_file():
                    record: dict[str, Any] = {"at": _utc(), "task": task_name, "seed": seed, "candidate": candidate, "status": "started_evaluation_resume"}
                    try:
                        evaluator_cell = Path(saved["evaluator_data_mount"])
                        run = AirsRadRun(task, cell / "agent-data", cell / "agent-log", evaluator_cell, evaluator_python, manifest)
                        evaluated = evaluate_official_airs_submission(task, python=evaluator_python, run_manifest_path=manifest, execution=runtime, docker_image=docker_image)
                        record.update({"status": "completed", "candidate_status": "resumed_existing_submission", "metric_stdout": evaluated["evaluate"]["stdout"]})
                        counts["completed"] += 1
                    except Exception as exc:
                        record.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000]})
                        counts["failed"] += 1
                    with ledger.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, sort_keys=True) + "\n")
                    continue
            record: dict[str, Any] = {"at": _utc(), "task": task_name, "seed": seed, "candidate": candidate, "status": "started"}
            try:
                # Keep evaluator-only data outside the candidate's workspace.
                # The candidate process is rooted at ``cell`` and receives only
                # the public agent-data mount plus its log directory.
                evaluator_cell = run_root / "_protected_evaluator_data" / task_name / f"seed-{seed}"
                prepare_official_airs_task(
                    task, global_shared_data_dir=shared_data, agent_data_mount_dir=cell / "agent-data",
                    agent_log_dir=cell / "agent-log", evaluator_data_mount_dir=evaluator_cell,
                    python=evaluator_python, run_manifest_path=manifest, execution=runtime, docker_image=docker_image,
                )
                run = AirsRadRun(task, cell / "agent-data", cell / "agent-log", evaluator_cell, evaluator_python, manifest)
                outcome = run_public_airs_baseline(run) if candidate == "baseline" else asyncio.run(
                    run_codex_airs_candidate(run, budget=AirsCandidateBudget(wall_seconds=candidate_seconds))
                )
                record["candidate_status"] = outcome["status"]
                if outcome["status"] != "valid_submission":
                    raise RuntimeError("candidate did not produce a valid public submission")
                evaluated = evaluate_official_airs_submission(task, python=evaluator_python, run_manifest_path=manifest, execution=runtime, docker_image=docker_image)
                record.update({"status": "completed", "metric_stdout": evaluated["evaluate"]["stdout"]})
                counts["completed"] += 1
            except Exception as exc:
                record.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000]})
                counts["failed"] += 1
            _append(ledger, record)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--import-root", type=Path, required=True)
    parser.add_argument("--shared-data", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--evaluator-python", type=Path, required=True)
    parser.add_argument("--candidate", choices=("baseline", "codex"), default="codex")
    parser.add_argument("--candidate-seconds", type=int, default=900)
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--runtime", choices=("local", "docker"), default="local")
    parser.add_argument("--docker-image")
    args = parser.parse_args()
    print(json.dumps(run_matrix(args.matrix, source_root=args.source_root, import_root=args.import_root, shared_data=args.shared_data, run_root=args.run_root, evaluator_python=args.evaluator_python, candidate=args.candidate, candidate_seconds=args.candidate_seconds, require_cuda=args.require_cuda, runtime=args.runtime, docker_image=args.docker_image), sort_keys=True))


if __name__ == "__main__":
    main()
