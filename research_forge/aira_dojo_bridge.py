"""Thin aira-dojo task bridge for imported official AIRS RAD bundles.

This module deliberately owns no data transformation and no scoring logic.
The official AIRS scripts remain the only prepare/evaluate authority.
"""

from __future__ import annotations

import asyncio
import ast
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .official_airs import _validate_submission_structure, evaluate_official_airs_submission


@dataclass(frozen=True)
class AirsRadRun:
    task_dir: Path
    agent_data_dir: Path
    agent_log_dir: Path
    evaluator_data_dir: Path
    python: Path
    run_manifest_path: Path | None = None


@dataclass(frozen=True)
class AirsCandidateBudget:
    """Public, per-candidate limits for the aira-dojo bridge."""

    wall_seconds: int = 300
    allow_dependency_install: bool = False


class AirsSubmissionProgram(BaseModel):
    """The API candidate's entire executable contribution.

    The program is run in a network-disabled container that has only the
    public agent mount and writable agent log.  It is never given task source,
    raw shared data, or evaluator data.
    """

    program: str


def _public_data_summary(agent_data_dir: Path, *, max_bytes: int = 6000) -> str:
    """Bound the public context sent to the API without reading hidden data."""
    entries: list[str] = []
    remaining = max_bytes
    for path in sorted(agent_data_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(agent_data_dir).as_posix()
        line = f"- {relative} ({path.stat().st_size} bytes)"
        if path.suffix.lower() in {".csv", ".json", ".jsonl", ".txt"} and path.stat().st_size <= 200_000:
            sample = path.read_text(encoding="utf-8", errors="replace")[:600].replace("\x00", "")
            line += f"\n  sample: {sample!r}"
        if len(line) > remaining:
            entries.append(line[:remaining] + "…")
            break
        entries.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break
    return "\n".join(entries) or "(public agent-data directory is empty)"


def _validate_api_program(program: str) -> None:
    """Reject source that obviously escapes the tiny offline submission role."""
    if len(program) > 20_000:
        raise ValueError("API candidate program exceeds 20,000 characters")
    tree = ast.parse(program, filename="submission_program.py", mode="exec")
    blocked_modules = {"os", "subprocess", "socket", "requests", "urllib", "http", "shutil", "ctypes", "multiprocessing"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name.split(".")[0] for alias in node.names] if isinstance(node, ast.Import) else [str(node.module or "").split(".")[0]]
            if any(module in blocked_modules for module in modules):
                raise ValueError("API candidate program imports a blocked module")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "compile", "__import__", "breakpoint"}:
            raise ValueError("API candidate program uses a blocked dynamic-execution primitive")


async def _run_api_airs_candidate_in_process(
    run: AirsRadRun,
    *,
    budget: AirsCandidateBudget,
) -> dict[str, Any]:
    """Use the configured OpenAI-compatible API for a bounded public candidate."""
    from dotenv import load_dotenv
    from agents import Agent, Runner
    from .agent_runtime import _load_local_runtime_env

    _load_local_runtime_env()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env.local", override=False)
    metadata = yaml.safe_load((run.task_dir / "metadata.yaml").read_text(encoding="utf-8")) or {}
    info = metadata.get("logging_info", {})
    description = (run.task_dir / "project_description.md").read_text(encoding="utf-8")
    audit_path = run.agent_log_dir / "candidate_attempt.json"
    prompt = f"""Produce one deterministic Python 3 submission program for an AIRS task.

Public task description:\n{description}

Public metadata: output_type={info.get('output_type')}; scoring_column={info.get('scoring_column')}; expected shape={info.get('shape')}.
Public agent-data manifest and small text samples:\n{_public_data_summary(run.agent_data_dir)}

Your returned program will execute offline in a Linux container. It can read only `/agent-data` and write only `/agent-log/submission.csv`. `datasets.load_from_disk` and `pyarrow` are preinstalled for public HuggingFace Arrow data; standard-library modules (csv, json, pathlib, math, statistics, re) are also fine. Do not install packages, download data, use subprocesses, train a model, or access an evaluator. Inspect the actual available public files at runtime, write exactly the required CSV header and row count, and finish without prose. Return only the structured `program` field."""
    record: dict[str, Any] = {
        "schema_version": 1, "backend": "api", "model": os.getenv("AUTORESEARCH_MODEL", "gpt-5.6-terra"),
        "budget_seconds": budget.wall_seconds, "dependency_install_allowed": False, "status": "started",
    }
    try:
        agent = Agent(
            name="AIRS public submission programmer",
            instructions="Return a safe, deterministic Python program that uses only public agent data and preinstalled libraries.",
            model=record["model"], output_type=AirsSubmissionProgram,
        )
        result = await asyncio.wait_for(Runner.run(agent, prompt), timeout=budget.wall_seconds)
        draft = result.final_output
        if not isinstance(draft, AirsSubmissionProgram):
            draft = AirsSubmissionProgram.model_validate(draft)
        _validate_api_program(draft.program)
        program_path = run.agent_log_dir / "submission_program.py"
        program_path.write_text(draft.program, encoding="utf-8")
        command = [
            "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
            "--volume", f"{run.agent_data_dir.resolve()}:/agent-data:ro",
            "--volume", f"{run.agent_log_dir.resolve()}:/agent-log",
            "--workdir", "/agent-log", "research-forge/airs-official-cpu:v1", "python", "/agent-log/submission_program.py",
        ]
        completed = await asyncio.to_thread(
            subprocess.run, command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=min(budget.wall_seconds, 120), check=False,
        )
        record["program_execution"] = {"command": command, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
        if completed.returncode != 0:
            raise RuntimeError("API candidate program failed: " + completed.stderr[-1000:])
        record["submission_validation"] = _validate_submission_structure(run.agent_log_dir / "submission.csv", run.task_dir)
        record["status"] = "valid_submission"
    except TimeoutError:
        record.update({"api_status": "timeout", "api_error": f"candidate exceeded {budget.wall_seconds} seconds"})
    except Exception as exc:
        record.update({"api_status": "invalid_submission", "api_error": f"{type(exc).__name__}: {exc}"[:2000]})
    if record.get("status") != "valid_submission":
        # A program proposed from a bounded API context can still mishandle an
        # unfamiliar public storage format.  Preserve that failure, then use a
        # deterministic public-only fallback so protocol validation is not
        # held hostage by agent code generation.  This is a controller-owned
        # safety net, never a hidden-label optimization.
        fallback = run_public_airs_baseline(run)
        record["fallback"] = fallback
        if fallback.get("status") == "valid_submission":
            record["backend"] = "api_with_deterministic_public_fallback"
            record["status"] = "valid_submission"
            record["submission_validation"] = fallback["submission_validation"]
        else:
            record["status"] = "invalid_submission"
            record["error"] = "API candidate and deterministic public fallback both failed"
    audit_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


async def _run_codex_airs_candidate_in_process(
    run: AirsRadRun,
    *,
    budget: AirsCandidateBudget = AirsCandidateBudget(),
) -> dict[str, Any]:
    """Ask the configured Codex provider for one bounded AIRS submission.

    This is deliberately a candidate generator, not an evaluator: it receives
    only the public task description and agent-data mount.  The resulting CSV
    is structurally checked before the caller can invoke official scoring.
    """
    if budget.wall_seconds < 1:
        raise ValueError("candidate wall_seconds must be positive")
    from .agent_runtime import _load_local_runtime_env, backend_name

    _load_local_runtime_env()
    if backend_name() == "api":
        return await _run_api_airs_candidate_in_process(run, budget=budget)
    from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
    from .agent_runtime import _codex_process_env, _configured_codex_model

    metadata = yaml.safe_load((run.task_dir / "metadata.yaml").read_text(encoding="utf-8")) or {}
    info = metadata.get("logging_info", {})
    description = (run.task_dir / "project_description.md").read_text(encoding="utf-8")
    run_root = run.agent_log_dir.parent
    audit_path = run.agent_log_dir / "candidate_attempt.json"
    prompt = f"""You are producing exactly one AIRS benchmark submission.

Public task description:\n{description}

Public metadata: output_type={info.get('output_type')}; scoring_column={info.get('scoring_column')}; expected shape={info.get('shape')}.
Your working directory contains only `agent-data` (read) and `agent-log` (write).
Use the already available local runtime and public data only. Do NOT install packages, download data, access any evaluator data, access parent directories, or run long hyperparameter/nearest-neighbor sweeps. Start by inspecting the public `agent-data` files and their schema, then immediately implement one deterministic, bounded baseline (for example: a train-label mode or mean, a direct text overlap score, or a last-value forecast as appropriate). Do not train a model. Write only the final `agent-log/submission.csv`, with a non-empty header and exactly the public expected number of data rows. Before finishing, verify that file exists. Do not claim a score or explain a plan instead of writing the file.
"""
    instructions = """You are the candidate side of an AIRS/a-ira-dojo run. The evaluator is protected. Obey the file boundary and time budget exactly; a submission is useful only if it is written to agent-log/submission.csv."""
    config = CodexConfig(cwd=str(run_root), env=_codex_process_env(), client_name="research_forge_airs", client_title="Research Forge AIRS")
    record: dict[str, Any] = {
        "schema_version": 1,
        "backend": "codex",
        "model": _configured_codex_model(),
        "budget_seconds": budget.wall_seconds,
        "dependency_install_allowed": budget.allow_dependency_install,
        "status": "started",
    }
    try:
        async with AsyncCodex(config) as codex:
            thread = await codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                base_instructions=instructions,
                cwd=str(run_root),
                ephemeral=True,
                model=_configured_codex_model(),
                sandbox=Sandbox.workspace_write,
                service_name="airs-rad-candidate",
            )
            await asyncio.wait_for(
                thread.run(prompt, approval_mode=ApprovalMode.deny_all, cwd=str(run_root), sandbox=Sandbox.workspace_write),
                timeout=budget.wall_seconds,
            )
        record["submission_validation"] = _validate_submission_structure(run.agent_log_dir / "submission.csv", run.task_dir)
        record["status"] = "valid_submission"
    except TimeoutError:
        record["status"] = "timeout"
        record["error"] = f"candidate exceeded {budget.wall_seconds} seconds"
    except Exception as exc:
        record["status"] = "invalid_submission"
        record["error"] = f"{type(exc).__name__}: {exc}"[:2000]
    audit_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def _terminate_process_tree(pid: int) -> None:
    """Hard-stop a timed-out candidate and every helper it spawned."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


async def run_codex_airs_candidate(
    run: AirsRadRun,
    *,
    budget: AirsCandidateBudget = AirsCandidateBudget(),
) -> dict[str, Any]:
    """Run a candidate in a killable worker process.

    Codex's app-server can remain alive while an async client is closing.  The
    outer process boundary makes the declared budget enforceable and prevents a
    stale candidate from consuming a later task's resources.
    """
    if budget.wall_seconds < 1:
        raise ValueError("candidate wall_seconds must be positive")
    from .agent_runtime import _configured_codex_home

    request = run.agent_log_dir / "candidate_worker_request.json"
    response = run.agent_log_dir / "candidate_worker_result.json"
    request.write_text(
        json.dumps(
            {
                "task_dir": str(run.task_dir), "agent_data_dir": str(run.agent_data_dir),
                "agent_log_dir": str(run.agent_log_dir), "evaluator_data_dir": str(run.evaluator_data_dir),
                "python": str(run.python), "run_manifest_path": str(run.run_manifest_path) if run.run_manifest_path else None,
                "wall_seconds": budget.wall_seconds, "allow_dependency_install": budget.allow_dependency_install,
            },
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    home = _configured_codex_home()
    if home is not None:
        env["RESEARCH_FORGE_CODEX_HOME"] = str(home)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "research_forge.airs_candidate_worker", "--request", str(request),
        cwd=str(run.agent_log_dir.parent), env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=budget.wall_seconds + 15)
    except TimeoutError:
        _terminate_process_tree(process.pid)
        await process.wait()
        record = {"schema_version": 1, "status": "timeout", "error": f"candidate exceeded {budget.wall_seconds} seconds", "budget_seconds": budget.wall_seconds}
        (run.agent_log_dir / "candidate_attempt.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return record
    if response.is_file():
        return json.loads(response.read_text(encoding="utf-8"))
    record = {"schema_version": 1, "status": "invalid_submission", "error": f"candidate worker exited {process.returncode} without result"}
    (run.agent_log_dir / "candidate_attempt.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def run_public_airs_baseline(run: AirsRadRun) -> dict[str, Any]:
    """Generate a bounded deterministic baseline from the agent-visible splits."""
    root = Path(__file__).resolve().parents[1]
    image = "research-forge/airs-official-cpu:v1"
    docker_ready = shutil.which("docker") is not None and subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, check=False
    ).returncode == 0
    if docker_ready:
        command = [
            "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
            "--env", "PYTHONPATH=/research-forge", "--volume", f"{root.resolve()}:/research-forge:ro",
            "--volume", f"{run.task_dir.resolve()}:/task:ro",
            "--volume", f"{run.agent_data_dir.resolve()}:/agent-data:ro",
            "--volume", f"{run.agent_log_dir.resolve()}:/agent-log",
            "--workdir", "/agent-log", image, "python", "-m", "research_forge.airs_public_baseline",
            "--task-dir", "/task", "--agent-data", "/agent-data", "--agent-log", "/agent-log",
        ]
        completed = subprocess.run(command, cwd=run.agent_log_dir.parent, capture_output=True, text=True, timeout=120, check=False)
        runtime = "docker"
    else:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        command = [
            str(run.python), "-m", "research_forge.airs_public_baseline", "--task-dir", str(run.task_dir),
            "--agent-data", str(run.agent_data_dir), "--agent-log", str(run.agent_log_dir),
        ]
        completed = subprocess.run(command, cwd=run.agent_log_dir.parent, env=env, capture_output=True, text=True, timeout=120, check=False)
        runtime = "local"
    record: dict[str, Any] = {"schema_version": 1, "backend": "deterministic_public_baseline", "runtime": runtime, "command": command, "returncode": completed.returncode}
    if completed.returncode == 0:
        record["status"] = "valid_submission"
        record["submission_validation"] = _validate_submission_structure(run.agent_log_dir / "submission.csv", run.task_dir)
    else:
        record["status"] = "invalid_submission"
        record["error"] = completed.stderr[-2000:]
    (run.agent_log_dir / "baseline_attempt.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def aira_task_class() -> type[Any]:
    """Return an aira-dojo ``Task`` bound to an already prepared AIRS run."""
    from dojo.core.tasks.base import Task

    class AirsRadTask(Task):
        def __init__(self, cfg: Any, *, run: AirsRadRun) -> None:
            super().__init__(cfg)
            self.run = run

        def prepare(self, **_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            return (
                {"agent_data_dir": str(self.run.agent_data_dir), "agent_log_dir": str(self.run.agent_log_dir)},
                {"task_dir": str(self.run.task_dir), "submission_path": str(self.run.agent_log_dir / "submission.csv")},
            )

        def step_task(self, state: dict[str, Any], action: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            if not (self.run.agent_log_dir / "submission.csv").is_file():
                return state, {"valid_solution": False, "reason": "submission.csv missing"}
            return state, {"valid_solution": True, "action": action}

        def evaluate_fitness(self, **_: Any) -> dict[str, Any]:
            return evaluate_official_airs_submission(
                self.run.task_dir,
                python=self.run.python,
                run_manifest_path=self.run.run_manifest_path,
            )

        def close(self, state: dict[str, Any]) -> None:
            return None

    return AirsRadTask
