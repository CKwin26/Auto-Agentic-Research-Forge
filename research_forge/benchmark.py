from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from .benchmark_models import (
    BenchmarkReport,
    BenchmarkSeedResult,
    BenchmarkTaskSpec,
    IntegrityAudit,
)
from .contracts import (
    metric_improvement,
    protect_artifacts,
    transition,
    validate_proposal,
    validate_metrics,
    verify_frozen_contracts,
)
from .models import (
    Direction,
    EventRecord,
    ExecutionContract,
    ExperimentProposal,
    LiteratureSourceType,
    MetricDefinition,
    ParameterOverride,
    ProposalEnvelope,
    ResearchPlanDraft,
    RunRecord,
    Stage,
)
from .runner import execute_run, promote_run
from .runtime import (
    DEFAULT_CONTROLLED_CPU_IMAGE,
    DockerRuntime,
    ExecutionRuntime,
    RuntimeKind,
    RuntimeOptions,
    build_runtime,
)
from .service import create_project, freeze_project, propose_experiment, register_literature_source
from .storage import (
    ROOT,
    append_jsonl,
    load_jsonl,
    load_state,
    read_json,
    save_state,
    sha256_tree,
    slugify,
    write_json_atomic,
)


BUILTIN_TASKS = Path(__file__).resolve().parent / "benchmark_tasks"
REGISTERED_TASKS = ROOT / "benchmarks"
DEFAULT_BENCHMARK_ROOT = ROOT / "benchmark_runs"


def _task_file(task_dir: Path) -> Path:
    path = task_dir / "task.json"
    if not path.is_file():
        raise FileNotFoundError(f"benchmark task is missing task.json: {task_dir}")
    return path


def _task_roots(
    extra_root: str | Path | None = None,
) -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = [("builtin", BUILTIN_TASKS)]
    if REGISTERED_TASKS.is_dir():
        roots.extend(
            (provider.name, provider.resolve())
            for provider in sorted(REGISTERED_TASKS.iterdir())
            if provider.is_dir()
        )
    if extra_root is not None:
        roots.append(("extra", Path(extra_root).resolve()))
    return roots


def _discover_tasks(
    extra_root: str | Path | None = None,
) -> list[tuple[str, Path, BenchmarkTaskSpec]]:
    discovered: list[tuple[str, Path, BenchmarkTaskSpec]] = []
    seen: dict[str, Path] = {}
    for registry, root in _task_roots(extra_root):
        if not root.is_dir():
            continue
        for task_file in sorted(root.glob("*/task.json")):
            task_dir = task_file.parent.resolve()
            spec = BenchmarkTaskSpec.model_validate(read_json(task_file))
            if task_dir.name != spec.task_id:
                raise ValueError(
                    f"task directory must match task_id: {task_dir.name} != {spec.task_id}"
                )
            previous = seen.get(spec.task_id)
            if previous is not None:
                raise ValueError(
                    f"duplicate registered task_id {spec.task_id!r}: {previous} and {task_dir}"
                )
            seen[spec.task_id] = task_dir
            discovered.append((registry, task_dir, spec))
    return sorted(discovered, key=lambda item: item[2].task_id)


def load_task(task: str | Path) -> tuple[Path, BenchmarkTaskSpec]:
    candidate = Path(task)
    if candidate.is_dir():
        task_dir = candidate.resolve()
        spec = BenchmarkTaskSpec.model_validate(read_json(_task_file(task_dir)))
        if task_dir.name != spec.task_id:
            raise ValueError(f"task directory must match task_id: {task_dir.name} != {spec.task_id}")
        return task_dir, spec

    task_id = str(task)
    matches = [
        (task_dir, spec)
        for _, task_dir, spec in _discover_tasks()
        if spec.task_id == task_id
    ]
    if not matches:
        raise FileNotFoundError(f"benchmark task is not registered: {task_id}")
    return matches[0]


def list_tasks(extra_root: str | Path | None = None) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    for registry, task_dir, spec in _discover_tasks(extra_root):
        tasks.append(
            {
                "task_id": spec.task_id,
                "title": spec.title,
                "source": spec.source,
                "registry": registry,
                "metric": spec.primary_metric,
                "direction": spec.direction.value,
                "compute_tier": spec.compute_tier,
                "runnable": spec.runnable,
                "path": str(task_dir),
            }
        )
    return tasks


def benchmark_doctor(
    dataset_python: str | Path | None = None,
    *,
    docker_image: str = DEFAULT_CONTROLLED_CPU_IMAGE,
) -> dict[str, object]:
    RuntimeOptions(kind="docker", image=docker_image)
    docker = shutil.which("docker")
    registered_tasks = list_tasks()
    status: dict[str, object] = {
        "builtin_tasks": sum(item["registry"] == "builtin" for item in registered_tasks),
        "registered_tasks": len(registered_tasks),
        "runnable_tasks": sum(bool(item["runnable"]) for item in registered_tasks),
        "docker_available": docker is not None,
        "docker_executable": docker,
        "docker_daemon_ready": False,
        "docker_image": docker_image,
        "docker_image_ready": False,
        "docker_isolation_ready": False,
        "public_anti_cheating_ready": False,
        "controlled_ml_environment": False,
        "ml_capabilities_verified": False,
        "ml_environment_ready": False,
        "runtime_capabilities": None,
        "airs_dataset_python": None,
        "airs_datasets_version": None,
        "airs_lite_ready": False,
    }
    if docker is not None:
        daemon = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        if daemon.returncode == 0:
            status["docker_daemon_ready"] = True
            status["docker_server_version"] = daemon.stdout.strip()
            image = subprocess.run(
                [docker, "image", "inspect", "--format", "{{.Id}}", docker_image],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                shell=False,
                check=False,
            )
            if image.returncode == 0:
                status["docker_image_ready"] = True
                status["docker_image_id"] = image.stdout.strip()
                try:
                    runtime = DockerRuntime(
                        RuntimeOptions(kind="docker", image=docker_image)
                    )
                    capabilities = runtime.capabilities()
                    status["runtime_capabilities"] = capabilities
                    status["controlled_ml_environment"] = (
                        capabilities.get("controlled") is True
                    )
                    status["ml_capabilities_verified"] = (
                        capabilities.get("verified") is True
                    )
                    status["ml_environment_ready"] = bool(
                        status["controlled_ml_environment"]
                        and status["ml_capabilities_verified"]
                    )
                    status["capability_manifest_sha256"] = (
                        runtime.capability_manifest_sha256
                    )
                except Exception as exc:
                    status["capability_error"] = f"{type(exc).__name__}: {exc}"
        else:
            status["docker_error"] = daemon.stderr.strip() or daemon.stdout.strip()
    ready = bool(status["docker_daemon_ready"] and status["docker_image_ready"])
    status["docker_isolation_ready"] = ready
    status["public_anti_cheating_ready"] = ready
    if dataset_python is None:
        return status
    python = Path(dataset_python).resolve()
    status["airs_dataset_python"] = str(python)
    if not python.is_file():
        status["airs_error"] = "dataset Python interpreter does not exist"
        return status
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as m; print(m.version('datasets'))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    version = completed.stdout.strip() if completed.returncode == 0 else None
    status["airs_datasets_version"] = version
    status["airs_lite_ready"] = version in {"3.6.0", "4.0.0"}
    if version not in {"3.6.0", "4.0.0"}:
        status["airs_error"] = (
            "AIRS-lite exporters require datasets==3.6.0 for legacy scripted "
            "datasets or datasets==4.0.0 for current Parquet schemas; "
            f"detected {version or 'unavailable'}"
        )
    return status


def _benchmark_plan(spec: BenchmarkTaskSpec) -> ResearchPlanDraft:
    guidance = [f"{name}: {text}" for name, text in spec.parameter_guidance.items()]
    research_question = spec.description
    if len(research_question) > 2000:
        research_question = research_question[:1997].rstrip() + "..."
    return ResearchPlanDraft(
        title=spec.title,
        research_question=research_question,
        hypothesis="A bounded, evidence-driven change can improve the frozen benchmark metric.",
        novelty_claim="The benchmark measures closed-loop experimental improvement, not publication novelty.",
        scope_in=["frozen evaluator", "bounded experiment proposals", "evidence-preserving runs"],
        scope_out=["unbounded package installation", "unverified self-reported metrics", "paper acceptance"],
        method_outline=[
            "verify the registered baseline",
            "propose one falsifiable change at a time",
            "score only evaluator-produced metrics",
            "retain invalid and negative attempts",
        ],
        datasets=[f"{spec.source} benchmark task: {spec.task_id}"],
        metrics=[
            MetricDefinition(
                name=spec.primary_metric,
                description=spec.metric_description,
                direction=spec.direction,
            )
        ],
        baseline_definition=f"Registered benchmark baseline score: {spec.baseline_score}",
        ablation_axes=guidance or ["candidate implementation"],
        confounders=["seed variance", "evaluator tampering", "invalid or stale submissions"],
        stop_conditions=[
            f"target score {spec.target_score} is reached",
            f"iteration budget {spec.max_iterations} is exhausted",
        ],
        risks=[
            "Local execution is tamper-evident but not an OS-level anti-cheating sandbox.",
            "Built-in tasks diagnose the harness and do not establish scientific novelty.",
        ],
        clarifying_questions=[],
        readiness_summary="The task, metric, evaluator, baseline, target, seeds, and budget are fixed.",
        ready_to_freeze=True,
    )


def materialize_task(
    task_dir: Path,
    spec: BenchmarkTaskSpec,
    *,
    seed: int,
    project_root: Path,
    project_slug: str | None = None,
) -> Path:
    if not spec.runnable:
        raise ValueError(spec.setup_instructions or f"benchmark task is not runnable: {spec.task_id}")
    starter = task_dir / "starter"
    evaluator = task_dir / "evaluator"
    if not starter.is_dir() or not evaluator.is_dir():
        raise FileNotFoundError("runnable task requires starter/ and evaluator/ directories")
    if not (starter / spec.starter_entrypoint).is_file():
        raise FileNotFoundError(f"starter entrypoint is missing: {spec.starter_entrypoint}")
    if not (evaluator / spec.evaluator_entrypoint).is_file():
        raise FileNotFoundError(f"evaluator entrypoint is missing: {spec.evaluator_entrypoint}")

    slug = project_slug or f"{spec.task_id}-seed-{seed}"
    project = create_project(
        f"{spec.title} (seed {seed})",
        spec.description,
        slug=slug,
        root=project_root,
    )
    cache_ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(starter, project / "experiment", dirs_exist_ok=True, ignore=cache_ignore)
    shutil.copytree(evaluator, project / "evaluator", ignore=cache_ignore)
    if (task_dir / "data").is_dir():
        shutil.copytree(task_dir / "data", project / "data", dirs_exist_ok=True, ignore=cache_ignore)
    (project / "benchmark").mkdir()
    write_json_atomic(project / "benchmark" / "task.json", spec)
    write_json_atomic(
        project / "benchmark" / "run_config.json",
        {"task_id": spec.task_id, "seed": seed, "source_path": str(task_dir.resolve())},
    )
    write_json_atomic(project / "current_parameters.json", spec.baseline_parameters)
    register_literature_source(
        project,
        source_id=f"rf-bench-{spec.task_id}",
        source_type=LiteratureSourceType.SPECIFICATION,
        title=f"RF-Bench task specification: {spec.title}",
        authors=["Research Forge"],
        year=None,
        locator=str((task_dir / "task.json").resolve()),
        notes=(
            "This registered task specification defines the calibration objective, evaluator, "
            "candidate space, and stopping budget; it is not evidence of scientific novelty."
        ),
        verified=True,
        verification_method="Loaded from the packaged task.json and frozen with the protected evaluator.",
    )

    plan_id = "benchmark-plan"
    write_json_atomic(project / "plans" / f"{plan_id}.json", _benchmark_plan(spec))
    state = load_state(project)
    state.stage = Stage.PLAN_REVIEW
    state.latest_plan_draft = plan_id
    save_state(project, state)

    experiment_command = [
        "{python}",
        "{experiment_dir}/" + spec.starter_entrypoint.replace("\\", "/"),
        "--params",
        "{params_file}",
        "--submission",
        "{submission_file}",
        "--seed",
        str(seed),
    ]
    evaluator_command = [
        "{python}",
        "{evaluator_dir}/" + spec.evaluator_entrypoint.replace("\\", "/"),
        "--submission",
        "{submission_file}",
        "--metrics",
        "{metrics_file}",
        "--seed",
        str(seed),
    ]
    contract = ExecutionContract(
        configured_by_user=True,
        command=experiment_command,
        evaluator_command=evaluator_command,
        primary_metric=spec.primary_metric,
        direction=spec.direction,
        required_metrics=[spec.primary_metric],
        min_delta=spec.min_delta,
        timeout_seconds=spec.timeout_seconds,
        max_runs=spec.max_iterations + 1,
        required_repeats=spec.required_repeats,
        max_repeats=spec.required_repeats,
    )
    write_json_atomic(project / "execution_contract.json", contract)
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="benchmark_execution_contract_configured",
            from_stage=Stage.PLAN_REVIEW,
            to_stage=Stage.PLAN_REVIEW,
            details={"task_id": spec.task_id, "seed": seed},
        ),
    )
    protected = [
        path.relative_to(project).as_posix()
        for path in sorted((project / "evaluator").rglob("*"))
        if path.is_file()
    ]
    protected.extend(["benchmark/task.json", "benchmark/run_config.json"])
    protect_artifacts(project, protected)
    freeze_project(project)
    return project


def seed_project_slug(task_id: str, seed: int) -> str:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:10]
    return f"seed-{seed}-{digest}"


def _grid_proposal(
    project: Path,
    spec: BenchmarkTaskSpec,
    parameters: dict[str, object],
    iteration: int,
) -> ProposalEnvelope:
    proposal = ExperimentProposal(
        title=f"Registered grid candidate {iteration}",
        hypothesis="This registered candidate may improve the frozen benchmark metric.",
        rationale="A deterministic baseline strategy supplies the candidate for harness calibration.",
        expected_observation="The protected evaluator returns a finite benchmark metric.",
        falsification_condition="The candidate is invalid or does not improve on the current reference.",
        success_criteria=["The evaluator-produced primary metric improves."],
        parameters=[
            ParameterOverride(
                name=name,
                value=value,
                reason="Registered RF-Bench calibration candidate.",
            )
            for name, value in parameters.items()
        ],
        estimated_minutes=1,
        tags=["rfbench", "grid"],
    )
    contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    errors = validate_proposal(project, proposal, contract)
    proposal_id = f"proposal-grid-{iteration:03d}-{uuid.uuid4().hex[:6]}"
    envelope = ProposalEnvelope(
        proposal_id=proposal_id,
        model="rfbench-grid",
        proposal=proposal,
        validation_errors=errors,
        valid=not errors,
    )
    write_json_atomic(project / "proposals" / f"{proposal_id}.json", envelope)
    state = load_state(project)
    if state.stage != Stage.EXPERIMENT_DESIGN:
        transition(project, state, Stage.EXPERIMENT_DESIGN, "benchmark_grid_proposed", proposal_id=proposal_id)
    else:
        transition(project, state, Stage.EXPERIMENT_DESIGN, "benchmark_grid_reproposed", proposal_id=proposal_id)
    state.latest_proposal_id = proposal_id
    save_state(project, state)
    return envelope


def _docker_phase_command_valid(
    argv: object,
    *,
    forbidden_host_path: Path,
    forbidden_container_path: str,
    allowed_mount_destinations: set[str],
    required_mount_destinations: set[str],
    workdir: str,
    limits: dict[str, object],
    image: str,
) -> bool:
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        return False
    required_pairs = {
        "--network": "none",
        "--cap-drop": "ALL",
        "--security-opt": "no-new-privileges",
        "--user": "65534:65534",
        "--workdir": workdir,
        "--pids-limit": str(limits.get("pids_limit")),
        "--memory": f"{limits.get('memory_mb')}m",
        "--cpus": str(limits.get("cpus")),
    }
    for flag, expected in required_pairs.items():
        if argv.count(flag) != 1:
            return False
        index = argv.index(flag)
        if index + 1 >= len(argv) or argv[index + 1] != expected:
            return False
    for flag in ("--read-only", "--tmpfs"):
        if flag not in argv:
            return False
    tmpfs_index = argv.index("--tmpfs")
    if (
        tmpfs_index + 1 >= len(argv)
        or argv[tmpfs_index + 1]
        != f"/tmp:rw,noexec,nosuid,size={limits.get('tmpfs_mb')}m"
    ):
        return False
    if "--privileged" in argv:
        return False
    if "--rm" not in argv or argv.count("--name") != 1:
        return False
    expected_environment = {
        "PYTHONUNBUFFERED=1",
        "PYTHONDONTWRITEBYTECODE=1",
        "AUTORESEARCH_PROJECT_DIR=/workspace/project",
        "AUTORESEARCH_RUN_DIR=/workspace/output",
    }
    environment_values = {
        argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "--env"
    }
    if environment_values != expected_environment:
        return False
    if "python3" not in argv or argv[argv.index("python3") - 1] != image:
        return False
    lowered = "\n".join(argv).lower()
    if str(forbidden_host_path.resolve()).lower() in lowered:
        return False
    if forbidden_container_path.lower() in lowered:
        return False
    mounts = [argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "--mount"]
    if not mounts:
        return False
    destinations: list[str] = []
    for mount in mounts:
        match = re.search(r"(?:^|,)dst=([^,]+)", mount)
        if match is None:
            return False
        destination = match.group(1)
        destinations.append(destination)
        if destination not in allowed_mount_destinations:
            return False
        if destination != "/workspace/output" and ",readonly" not in mount:
            return False
    if not required_mount_destinations.issubset(set(destinations)):
        return False
    writable = [mount for mount in mounts if ",readonly" not in mount]
    if len(writable) != 1 or "dst=/workspace/output" not in writable[0]:
        return False
    if not any("dst=/workspace/input/params.json" in mount and ",readonly" in mount for mount in mounts):
        return False
    return True


def _docker_trial_isolation_valid(
    command_record: dict[str, object],
    *,
    project: Path,
    evaluator_required: bool,
    attestation: dict[str, object],
) -> bool:
    limits = attestation.get("limits")
    if not isinstance(limits, dict):
        return False
    candidate = command_record.get("experiment_argv")
    if not _docker_phase_command_valid(
        candidate,
        forbidden_host_path=project / "evaluator",
        forbidden_container_path="/workspace/evaluator",
        allowed_mount_destinations={
            "/workspace/experiment",
            "/workspace/input/params.json",
            "/workspace/output",
            "/workspace/project/data",
        },
        required_mount_destinations={
            "/workspace/experiment",
            "/workspace/input/params.json",
            "/workspace/output",
        },
        workdir="/workspace/experiment",
        limits=limits,
        image=str(attestation.get("image")),
    ):
        return False
    evaluator = command_record.get("evaluator_argv")
    if not evaluator_required:
        return evaluator is None
    if not _docker_phase_command_valid(
        evaluator,
        forbidden_host_path=project / "experiment",
        forbidden_container_path="/workspace/experiment",
        allowed_mount_destinations={
            "/workspace/evaluator",
            "/workspace/input/params.json",
            "/workspace/input/submission",
            "/workspace/output",
            "/workspace/project/data",
        },
        required_mount_destinations={
            "/workspace/evaluator",
            "/workspace/input/params.json",
            "/workspace/input/submission",
            "/workspace/output",
        },
        workdir="/workspace/evaluator",
        limits=limits,
        image=str(attestation.get("image")),
    ):
        return False
    assert isinstance(evaluator, list)
    mounts = [
        evaluator[index + 1]
        for index, token in enumerate(evaluator[:-1])
        if token == "--mount"
    ]
    return any(
        "dst=/workspace/input/submission" in mount and ",readonly" in mount
        for mount in mounts
    )


def audit_project(project: Path) -> IntegrityAudit:
    violations: list[str] = []
    checks: dict[str, bool] = {}
    try:
        expected_contract_hash = verify_frozen_contracts(project)
        checks["frozen_and_protected_artifacts"] = True
    except Exception as exc:
        expected_contract_hash = None
        checks["frozen_and_protected_artifacts"] = False
        violations.append(str(exc))

    state = load_state(project)
    checks["no_active_run"] = state.active_run_id is None
    if state.active_run_id is not None:
        violations.append(f"active run was not closed: {state.active_run_id}")

    evidence = load_jsonl(project / "evidence.jsonl")
    evidence_ids = [str(item.get("run_id")) for item in evidence]
    checks["unique_evidence_ids"] = len(evidence_ids) == len(set(evidence_ids))
    if not checks["unique_evidence_ids"]:
        violations.append("evidence ledger contains duplicate run IDs")

    records: dict[str, RunRecord] = {}
    for record_file in sorted((project / "runs").glob("*/record.json")):
        record = RunRecord.model_validate(read_json(record_file))
        records[record.run_id] = record
    checks["run_records_present"] = bool(records)
    if not records:
        violations.append("benchmark project has no completed run records")
    checks["verified_baseline_present"] = any(
        record.is_baseline and record.valid and record.verdict == "baseline_verified"
        for record in records.values()
    )
    if not checks["verified_baseline_present"]:
        violations.append("benchmark project has no verified baseline")
    evidence_map = {str(item.get("run_id")): item for item in evidence}
    checks["evidence_run_bijection"] = set(evidence_map) == set(records)
    for missing in sorted(set(records) - set(evidence_map)):
        violations.append(f"run is missing from evidence ledger: {missing}")
    for missing in sorted(set(evidence_map) - set(records)):
        violations.append(f"evidence points to a missing run record: {missing}")

    matches = True
    finite = True
    contract_matches = True
    for run_id in sorted(set(records) & set(evidence_map)):
        record = records[run_id]
        item = evidence_map[run_id]
        primary_value = record.aggregate_metrics.get(str(item.get("primary_metric")))
        fields_match = (
            item.get("valid") == record.valid
            and item.get("verdict") == record.verdict
            and item.get("proposal_id") == record.proposal_id
            and item.get("aggregate_metrics") == record.aggregate_metrics
            and item.get("primary_value") == primary_value
            and item.get("code_hash") == record.code_hash
            and item.get("contract_hash") == record.contract_hash
            and item.get("runtime", "local") == record.runtime
            and bool(item.get("isolation_verified", False)) == record.isolation_verified
        )
        if not fields_match:
            matches = False
            violations.append(f"evidence does not match run record: {run_id}")
        if expected_contract_hash is not None and record.contract_hash != expected_contract_hash:
            contract_matches = False
            violations.append(f"run used a different frozen contract: {run_id}")
        for name, value in record.aggregate_metrics.items():
            if not math.isfinite(value):
                finite = False
                violations.append(f"non-finite aggregate metric in {run_id}: {name}")
    checks["evidence_matches_records"] = matches
    checks["finite_metrics"] = finite
    checks["run_contract_hashes_match"] = contract_matches

    artifacts_match = True
    runtime_claims_match = True
    contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    for run_id, record in records.items():
        run_dir = project / "runs" / run_id
        try:
            manifest = read_json(run_dir / "manifest.json")
            parameters = read_json(run_dir / "parameters.json")
            digest = hashlib.sha256()
            digest.update(sha256_tree(run_dir / "workspace" / "experiment").encode("ascii"))
            digest.update(b"\0")
            digest.update(
                json.dumps(parameters, ensure_ascii=False, sort_keys=True).encode("utf-8")
            )
            expected_code_hash = digest.hexdigest()
            if (
                manifest.get("run_id") != record.run_id
                or manifest.get("proposal_id") != record.proposal_id
                or manifest.get("is_baseline") != record.is_baseline
                or manifest.get("contract_hash") != record.contract_hash
                or manifest.get("code_hash") != record.code_hash
                or manifest.get("runtime", "local") != record.runtime
                or bool(manifest.get("isolation_verified", False)) != record.isolation_verified
                or dict(manifest.get("runtime_attestation") or {}) != record.runtime_attestation
                or parameters != record.parameters
                or expected_code_hash != record.code_hash
            ):
                raise ValueError("manifest, parameters, or workspace hash differs")
            for trial in record.trials:
                trial_dir = run_dir / f"trial-{trial.index:03d}"
                metrics_path = trial_dir / "metrics.json"
                if trial.metrics:
                    normalized, metric_errors = validate_metrics(read_json(metrics_path), contract)
                    if metric_errors or normalized != trial.metrics:
                        raise ValueError(f"trial metrics differ: {trial.index}")
                elif metrics_path.is_file() and trial.valid:
                    raise ValueError(f"valid trial has no recorded metrics: {trial.index}")
                command_record = read_json(trial_dir / "command.json")
                if command_record.get("runtime", "local") != record.runtime:
                    raise ValueError(f"trial runtime differs: {trial.index}")
                if record.runtime == "docker" and not _docker_trial_isolation_valid(
                    command_record,
                    project=project,
                    evaluator_required=(
                        contract.evaluator_command is not None
                        and trial.evaluator_exit_code is not None
                    ),
                    attestation=record.runtime_attestation,
                ):
                    runtime_claims_match = False
                    raise ValueError(f"Docker isolation command invalid: {trial.index}")
            if record.valid:
                rebuilt: dict[str, float] = {}
                metric_names = sorted(
                    set.intersection(*(set(trial.metrics) for trial in record.trials))
                )
                for name in metric_names:
                    rebuilt[name] = statistics.fmean(
                        trial.metrics[name] for trial in record.trials
                    )
                if rebuilt != record.aggregate_metrics:
                    raise ValueError("aggregate metrics differ from trial metrics")
            if record.proposal_id is not None:
                proposal_path = project / "proposals" / f"{record.proposal_id}.json"
                if not proposal_path.is_file() or not read_json(proposal_path).get("valid"):
                    raise ValueError("candidate run lacks a valid proposal envelope")
        except Exception as exc:
            artifacts_match = False
            violations.append(f"run artifacts do not match record {run_id}: {exc}")
    checks["run_artifacts_match_records"] = artifacts_match

    for run_id, record in records.items():
        attestation = record.runtime_attestation
        if record.runtime == "local":
            valid_claim = not record.isolation_verified and not bool(
                attestation.get("isolation_verified", False)
            )
        else:
            security_claim = (
                attestation.get("network") == "none"
                and attestation.get("read_only_rootfs") is True
                and attestation.get("capabilities_dropped") is True
                and attestation.get("no_new_privileges") is True
                and bool(attestation.get("image_id"))
            )
            controlled_claim = True
            if attestation.get("controlled_environment") is True:
                capability_payload = attestation.get("capabilities")
                controlled_claim = (
                    attestation.get("capability_verified") is True
                    and bool(attestation.get("capability_manifest_sha256"))
                    and isinstance(capability_payload, dict)
                    and capability_payload.get("controlled") is True
                    and capability_payload.get("verified") is True
                    and bool(capability_payload.get("packages"))
                )
            if record.isolation_verified:
                valid_claim = (
                    bool(attestation.get("isolation_verified"))
                    and bool(attestation.get("candidate_evaluator_separated"))
                    and security_claim
                    and controlled_claim
                )
            else:
                valid_claim = (
                    not bool(attestation.get("isolation_verified"))
                    and not bool(attestation.get("candidate_evaluator_separated"))
                    and security_claim
                    and controlled_claim
                )
        if not valid_claim:
            runtime_claims_match = False
            violations.append(f"runtime isolation claim is inconsistent: {run_id}")
    checks["runtime_claims_consistent"] = runtime_claims_match

    promotion_ok = True
    for item in load_jsonl(project / "lineage.jsonl"):
        run_id = str(item.get("run_id"))
        record = records.get(run_id)
        if record is None or not record.valid or record.verdict != "candidate_improves":
            promotion_ok = False
            violations.append(f"promotion lacks a valid improving run: {run_id}")
    checks["promotion_lineage"] = promotion_ok
    passed = all(checks.values()) and not violations
    return IntegrityAudit(
        project=str(project.resolve()),
        passed=passed,
        checks=checks,
        violations=violations,
        evidence_records=len(evidence),
        run_records=len(records),
        isolation_verified=bool(records)
        and all(record.runtime == "docker" and record.isolation_verified for record in records.values()),
    )


def _score_project(
    project: Path,
    spec: BenchmarkTaskSpec,
    *,
    seed: int,
    requested_iterations: int,
    wall_time_seconds: float,
    errors: list[str],
) -> BenchmarkSeedResult:
    audit = audit_project(project)
    records = [
        RunRecord.model_validate(read_json(path))
        for path in sorted((project / "runs").glob("*/record.json"))
    ]
    baseline_records = [record for record in records if record.is_baseline and record.valid]
    candidate_records = [record for record in records if not record.is_baseline]
    valid_candidates = [record for record in candidate_records if record.valid]
    proposal_files = list((project / "proposals").glob("*.json"))
    valid_proposals = sum(
        1 for path in proposal_files if bool(read_json(path).get("valid"))
    )
    baseline_score = None
    best_score = None
    normalized_gain = 0.0
    target_reached = False
    anytime_values: list[float] = []
    reproducible = False
    if baseline_records:
        baseline_score = baseline_records[-1].aggregate_metrics[spec.primary_metric]
        if not math.isclose(
            baseline_score,
            spec.baseline_score,
            rel_tol=1e-9,
            abs_tol=max(1e-12, spec.reproducibility_tolerance),
        ):
            audit.checks["registered_baseline_matches"] = False
            audit.violations.append(
                f"registered baseline {spec.baseline_score} differs from evaluated baseline {baseline_score}"
            )
            audit.passed = False
        else:
            audit.checks["registered_baseline_matches"] = True
        best_score = baseline_score
        denominator = metric_improvement(spec.target_score, baseline_score, spec.direction)
        if denominator <= 0:
            audit.checks["target_improves_on_evaluated_baseline"] = False
            audit.violations.append("target does not improve on the evaluated baseline")
            audit.passed = False
            errors.append("target does not improve on the evaluated baseline")
            denominator = math.inf
        else:
            audit.checks["target_improves_on_evaluated_baseline"] = True
        running_best = baseline_score
        ordered = sorted(candidate_records, key=lambda record: record.started_at)
        for record in ordered:
            if record.valid:
                value = record.aggregate_metrics[spec.primary_metric]
                if metric_improvement(value, running_best, spec.direction) > 0:
                    running_best = value
            gain = metric_improvement(running_best, baseline_score, spec.direction) / denominator
            anytime_values.append(max(0.0, gain))
        if ordered:
            best_record = max(
                (record for record in [*baseline_records, *valid_candidates]),
                key=lambda record: (
                    record.aggregate_metrics[spec.primary_metric]
                    if spec.direction == Direction.MAXIMIZE
                    else -record.aggregate_metrics[spec.primary_metric]
                ),
            )
            best_score = best_record.aggregate_metrics[spec.primary_metric]
            normalized_gain = metric_improvement(best_score, baseline_score, spec.direction) / denominator
            reproducible = (
                best_record.metric_stddev.get(spec.primary_metric, math.inf)
                <= spec.reproducibility_tolerance
                and len(best_record.trials) >= spec.required_repeats
            )
        target_reached = normalized_gain >= 1.0
    while len(anytime_values) < requested_iterations:
        anytime_values.append(max(0.0, normalized_gain))
    if len(anytime_values) > requested_iterations:
        anytime_values = anytime_values[:requested_iterations]
    denominator_attempts = max(len(proposal_files), len(candidate_records), 1)
    valid_submission_rate = len(valid_candidates) / denominator_attempts
    return BenchmarkSeedResult(
        seed=seed,
        project=str(project.resolve()),
        baseline_score=baseline_score,
        best_score=best_score,
        normalized_gain=normalized_gain,
        target_reached=target_reached,
        proposal_attempts=len(proposal_files),
        valid_proposals=valid_proposals,
        candidate_runs=len(candidate_records),
        valid_candidate_runs=len(valid_candidates),
        valid_submission_rate=valid_submission_rate,
        reproducible=reproducible,
        anytime_auc=statistics.fmean(anytime_values) if anytime_values else 0.0,
        wall_time_seconds=wall_time_seconds,
        integrity=audit,
        errors=errors,
    )


def _render_report(report: BenchmarkReport, output: Path) -> Path:
    lines = [
        f"# RF-Bench: {report.task_id}",
        "",
        f"- Benchmark ID: `{report.benchmark_id}`",
        f"- Strategy: `{report.strategy}`",
        f"- Controller: `{report.controller}`",
        f"- Runtime: `{report.runtime}`",
        f"- Model: `{report.model}`",
        f"- Publishable: `{report.publishable}`",
        f"- Integrity pass rate: `{report.integrity_pass_rate:.3f}`",
        f"- Success@N: `{report.success_at_n:.3f}`",
        f"- Mean normalized gain: `{report.mean_normalized_gain:.3f}`",
        f"- Mean valid submission rate: `{report.mean_valid_submission_rate:.3f}`",
        f"- Mean anytime AUC: `{report.mean_anytime_auc:.3f}`",
        "",
        "| Seed | Baseline | Best | Norm. gain | Target | Valid rate | Reproducible | Integrity |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.seeds:
        lines.append(
            f"| {item.seed} | {item.baseline_score} | {item.best_score} | "
            f"{item.normalized_gain:.3f} | {item.target_reached} | "
            f"{item.valid_submission_rate:.3f} | {item.reproducible} | {item.integrity.passed} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report.limitations)
    lines.extend(["", "Integrity violations invalidate a run; prose quality cannot compensate for them.", ""])
    path = output / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


async def run_benchmark(
    task: str | Path,
    *,
    strategy: str = "codex",
    seeds: Iterable[int] | None = None,
    iterations: int | None = None,
    output_root: str | Path | None = None,
    runtime: RuntimeKind | RuntimeOptions | ExecutionRuntime = "local",
) -> tuple[Path, BenchmarkReport]:
    if strategy not in {"codex", "grid"}:
        raise ValueError("benchmark strategy must be codex or grid")
    task_dir, spec = load_task(task)
    selected_seeds = list(seeds) if seeds is not None else list(spec.seeds)
    if not selected_seeds or len(selected_seeds) != len(set(selected_seeds)):
        raise ValueError("benchmark seeds must be non-empty and unique")
    requested_iterations = (
        iterations
        if iterations is not None
        else min(spec.max_iterations, len(spec.candidate_parameters))
        if strategy == "grid"
        else spec.max_iterations
    )
    if requested_iterations < 1 or requested_iterations > spec.max_iterations:
        raise ValueError(f"iterations must be between 1 and {spec.max_iterations}")
    if strategy == "grid" and not spec.candidate_parameters:
        raise ValueError("grid strategy requires candidate_parameters in the task spec")
    if strategy == "grid" and requested_iterations > len(spec.candidate_parameters):
        raise ValueError(
            "grid iterations exceed the number of registered candidate parameter sets"
        )
    runtime_engine = build_runtime(runtime)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    benchmark_id = (
        f"{slugify(spec.task_id)[:24]}-{strategy}-{stamp}-{uuid.uuid4().hex[:6]}"
    )
    root = Path(output_root or DEFAULT_BENCHMARK_ROOT).resolve()
    output = root / benchmark_id
    projects_root = output / "projects"
    output.mkdir(parents=True)
    project_results: list[BenchmarkSeedResult] = []

    for seed in selected_seeds:
        started = time.monotonic()
        errors: list[str] = []
        project = materialize_task(
            task_dir,
            spec,
            seed=seed,
            project_root=projects_root,
            project_slug=seed_project_slug(spec.task_id, seed),
        )
        try:
            baseline = await asyncio.to_thread(execute_run, project, runtime=runtime_engine)
            if not baseline.valid:
                errors.append(f"baseline invalid: {baseline.error}")
            else:
                for iteration in range(1, requested_iterations + 1):
                    if strategy == "grid":
                        parameters = spec.candidate_parameters[iteration - 1]
                        envelope = _grid_proposal(project, spec, parameters, iteration)
                    else:
                        focus = (
                            f"RF-Bench task {spec.task_id}, seed {seed}, iteration {iteration}/"
                            f"{requested_iterations}. Parameter guidance: {spec.parameter_guidance}. "
                            "Choose one bounded experiment. Metrics are produced only by the protected evaluator."
                        )
                        envelope = await propose_experiment(project, focus)
                    if not envelope.valid:
                        errors.append(
                            f"proposal {envelope.proposal_id} invalid: "
                            + "; ".join(envelope.validation_errors)
                        )
                        continue
                    record = await asyncio.to_thread(
                        execute_run,
                        project,
                        envelope=envelope,
                        runtime=runtime_engine,
                    )
                    if record.verdict == "candidate_improves":
                        await asyncio.to_thread(promote_run, project, record.run_id, record.run_id)
                    if record.valid:
                        baseline_value = baseline.aggregate_metrics[spec.primary_metric]
                        gain = metric_improvement(
                            record.aggregate_metrics[spec.primary_metric],
                            baseline_value,
                            spec.direction,
                        )
                        target_gain = metric_improvement(
                            spec.target_score, baseline_value, spec.direction
                        )
                        if gain >= target_gain:
                            break
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        elapsed = time.monotonic() - started
        project_results.append(
            _score_project(
                project,
                spec,
                seed=seed,
                requested_iterations=requested_iterations,
                wall_time_seconds=elapsed,
                errors=errors,
            )
        )

    gains = [item.normalized_gain for item in project_results]
    from .agent_runtime import model_name

    model = model_name() if strategy == "codex" else "rfbench-grid"
    limitations = [
        "RF-Bench v1 scores stages 2-3; project-level source and manuscript audits are reported separately."
    ]
    if spec.source == "rfbench":
        limitations.insert(
            0,
            "The built-in CPU tasks are development diagnostics, not evidence of scientific novelty.",
        )
    elif spec.source == "airs":
        limitations.insert(
            0,
            "AIRS-lite uses the official dataset split and metric semantics, but not the official container runtime or leaderboard protocol.",
        )
    if runtime_engine.name != "docker":
        limitations.append(
            "This run used the local runtime: evaluator hashes are checked, but the OS does not enforce anti-cheating isolation."
        )
    isolation_verified = bool(project_results) and all(
        item.integrity.isolation_verified for item in project_results
    )
    report = BenchmarkReport(
        benchmark_id=benchmark_id,
        task_id=spec.task_id,
        strategy=strategy,
        model=model,
        runtime=runtime_engine.name,
        controller="bounded",
        requested_iterations=requested_iterations,
        seeds=project_results,
        integrity_pass_rate=statistics.fmean(
            1.0 if item.integrity.passed else 0.0 for item in project_results
        ),
        success_at_n=statistics.fmean(1.0 if item.target_reached else 0.0 for item in project_results),
        mean_normalized_gain=statistics.fmean(gains),
        median_normalized_gain=statistics.median(gains),
        mean_valid_submission_rate=statistics.fmean(
            item.valid_submission_rate for item in project_results
        ),
        mean_anytime_auc=statistics.fmean(item.anytime_auc for item in project_results),
        total_wall_time_seconds=sum(item.wall_time_seconds for item in project_results),
        publishable=isolation_verified
        and all(item.integrity.passed for item in project_results),
        limitations=limitations,
    )
    write_json_atomic(output / "report.json", report)
    _render_report(report, output)
    return output, report


def import_airs_task(source: str | Path, output_root: str | Path) -> Path:
    source_dir = Path(source).resolve()
    metadata_path = source_dir / "metadata.yaml"
    description_path = source_dir / "project_description.md"
    if not metadata_path.is_file() or not description_path.is_file():
        raise FileNotFoundError("AIRS task requires metadata.yaml and project_description.md")
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("logging_info"), dict):
        raise ValueError("invalid AIRS metadata: logging_info is required")
    info = metadata["logging_info"]
    sota = info.get("sota")
    if not isinstance(sota, list) or not sota or "sota_score" not in sota[0]:
        raise ValueError("invalid AIRS metadata: at least one SOTA score is required")
    name = str(info.get("name") or source_dir.name)
    task_id = slugify(name)
    lower_is_better = bool(metadata.get("metric_lower_is_better"))
    direction = Direction.MINIMIZE if lower_is_better else Direction.MAXIMIZE
    spec = BenchmarkTaskSpec(
        task_id=task_id,
        title=f"AIRS-Bench: {name}",
        description=description_path.read_text(encoding="utf-8"),
        source="airs",
        primary_metric=str(info.get("metric") or "score"),
        metric_description=f"AIRS-Bench metric for {info.get('research_problem') or name}",
        direction=direction,
        baseline_score=float(info["estimated_worst_score"]),
        target_score=float(sota[0]["sota_score"]),
        optimal_score=float(info["optimal_score"]) if info.get("optimal_score") is not None else None,
        baseline_parameters={},
        parameter_guidance={},
        candidate_parameters=[],
        seeds=list(range(10)),
        max_iterations=10,
        timeout_seconds=86_400,
        required_repeats=1,
        compute_tier="gpu",
        runnable=False,
        setup_instructions=(
            "Imported AIRS task metadata is ready, but execution requires AIRS dataset preparation, "
            "a candidate starter entrypoint, and a Docker/isolated evaluator runtime."
        ),
    )
    destination = Path(output_root).resolve() / task_id
    if destination.exists():
        raise FileExistsError(f"benchmark task already exists: {destination}")
    destination.mkdir(parents=True)
    write_json_atomic(destination / "task.json", spec)
    (destination / "source").mkdir()
    for name in ("metadata.yaml", "project_description.md", "prepare.py", "evaluate.py", "evaluate_prepare.py", "utils.py"):
        path = source_dir / name
        if path.is_file():
            shutil.copy2(path, destination / "source" / name)
    return destination


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"expected JSON object in {path}")
                rows.append(value)
    return rows


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + end - 1) / 2.0 + 1.0
        for index in ordered[start:end]:
            result[index] = rank
        start = end
    return result


def _spearman(predictions: list[float], labels: list[float]) -> float:
    if len(predictions) != len(labels) or not predictions:
        raise ValueError("prediction and label lengths must match and be non-empty")
    left = _ranks(predictions)
    right = _ranks(labels)
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_norm = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_norm = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("Spearman correlation is undefined for constant values")
    return numerator / (left_norm * right_norm)


def _tokens(value: object) -> set[str]:
    return {token.strip(".,!?;:\"'()[]{}").lower() for token in str(value).split() if token.strip()}


def _similarity_predictions(
    rows: list[dict[str, object]], column_a: str, column_b: str, alpha: float
) -> list[float]:
    predictions: list[float] = []
    for row in rows:
        left = _tokens(row[column_a])
        right = _tokens(row[column_b])
        union = left | right
        jaccard = len(left & right) / len(union) if union else 0.0
        length_ratio = min(len(left), len(right)) / max(len(left), len(right), 1)
        predictions.append(5.0 * (alpha * jaccard + (1.0 - alpha) * length_ratio))
    return predictions


def _answer_texts(value: object) -> list[str]:
    if isinstance(value, dict):
        raw = value.get("text", [])
        return [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _classification_labels(value: object) -> list[object]:
    """Preserve scalar label types while flattening answer-style containers."""
    if isinstance(value, (dict, list)):
        return _answer_texts(value)
    return [value]


_ACCURACY_STARTER = '''from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    project = Path(os.environ["AUTORESEARCH_PROJECT_DIR"])
    rows = [json.loads(line) for line in (project / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if line]
    label = params.get("constant_label", __BASELINE_LABEL__)
    with Path(args.submission).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["prediction"])
        writer.writerows([[label] for _ in rows])


if __name__ == "__main__":
    main()
'''


_SPEARMAN_STARTER = '''from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path


def tokens(value: object) -> set[str]:
    return {token.strip(".,!?;:\\\"'()[]{}").lower() for token in str(value).split() if token.strip()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    alpha = float(params.get("alpha", 1.0))
    project = Path(os.environ["AUTORESEARCH_PROJECT_DIR"])
    rows = [json.loads(line) for line in (project / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if line]
    predictions = []
    for row in rows:
        left = tokens(row[__COLUMN_A__])
        right = tokens(row[__COLUMN_B__])
        union = left | right
        jaccard = len(left & right) / len(union) if union else 0.0
        length_ratio = min(len(left), len(right)) / max(len(left), len(right), 1)
        predictions.append(5.0 * (alpha * jaccard + (1.0 - alpha) * length_ratio))
    with Path(args.submission).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["prediction"])
        writer.writerows([[value] for value in predictions])


if __name__ == "__main__":
    main()
'''


_LITE_EVALUATOR = '''from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + end - 1) / 2.0 + 1.0
        for index in ordered[start:end]:
            result[index] = rank
        start = end
    return result


def spearman(predictions: list[float], labels: list[float]) -> float:
    left = ranks(predictions)
    right = ranks(labels)
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_norm = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_norm = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("Spearman correlation is undefined for constant values")
    return numerator / (left_norm * right_norm)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True)
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    labels = json.loads((Path(__file__).parent / "labels.json").read_text(encoding="utf-8"))
    with Path(args.submission).open("r", encoding="utf-8", newline="") as handle:
        predictions = [row[0] for row in list(csv.reader(handle))[1:]]
    if len(predictions) != len(labels):
        raise ValueError(f"submission row count {len(predictions)} != {len(labels)}")
    if __METRIC__ == "Accuracy":
        def answers(label: object) -> list[str]:
            if isinstance(label, dict):
                raw = label.get("text", [])
                return [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]
            if isinstance(label, list):
                return [str(item) for item in label]
            return [str(label)]
        value = sum(
            str(prediction) in set(answers(label))
            for prediction, label in zip(predictions, labels)
        ) / len(labels)
    elif __METRIC__ == "ExactMatch":
        def normalize(item: object) -> str:
            return " ".join(str(item).strip().lower().split())
        def answers(label: object) -> list[str]:
            if isinstance(label, dict):
                raw = label.get("text", [])
                return [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]
            if isinstance(label, list):
                return [str(item) for item in label]
            return [str(label)]
        value = sum(
            normalize(prediction) in {normalize(item) for item in answers(label)}
            for prediction, label in zip(predictions, labels)
        ) / len(labels)
    elif __METRIC__ == "SpearmanCorrelation":
        value = spearman([float(item) for item in predictions], [float(item) for item in labels])
    else:
        raise ValueError("unsupported AIRS-lite metric: " + __METRIC__)
    Path(args.metrics).write_text(json.dumps({__METRIC__: value}), encoding="utf-8")


if __name__ == "__main__":
    main()
'''


def activate_airs_lite(
    task_pack: str | Path,
    *,
    dataset_python: str | Path,
    cache_dir: str | Path,
) -> BenchmarkTaskSpec:
    task_dir = Path(task_pack).resolve()
    spec = BenchmarkTaskSpec.model_validate(read_json(_task_file(task_dir)))
    if spec.source != "airs":
        raise ValueError("activate-airs-lite requires an imported AIRS task pack")
    metadata_path = task_dir / "source" / "metadata.yaml"
    if not metadata_path.is_file():
        raise FileNotFoundError("imported AIRS metadata is missing")
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    info = metadata["logging_info"]
    metric = str(info["metric"])
    if metric not in {"Accuracy", "ExactMatch", "SpearmanCorrelation"}:
        raise ValueError(
            "AIRS-lite currently supports Accuracy, ExactMatch, and SpearmanCorrelation"
        )
    if metric == "Accuracy" and bool(info.get("custom_gold_labels")):
        raise ValueError(
            "AIRS-lite cannot activate custom-gold Accuracy tasks without a task-specific "
            "label and submission adapter"
        )
    python = Path(dataset_python).resolve()
    if not python.is_file():
        raise FileNotFoundError(f"dataset Python interpreter not found: {python}")
    readiness = benchmark_doctor(python)
    if not readiness["airs_lite_ready"]:
        raise ValueError(str(readiness.get("airs_error") or "AIRS-lite dataset runtime is not ready"))
    export_dir = task_dir / "activation"
    exporter = Path(__file__).resolve().parent / "export_airs_dataset.py"
    completed = subprocess.run(
        [
            str(python),
            str(exporter),
            "--metadata",
            str(metadata_path),
            "--output",
            str(export_dir),
            "--cache-dir",
            str(Path(cache_dir).resolve()),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        shell=False,
        check=False,
    )
    (task_dir / "activation.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (task_dir / "activation.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            "AIRS dataset export failed; inspect activation.stderr.log. "
            "Use datasets 3.6.0 for legacy scripted datasets or 4.0.0 for current Parquet schemas."
        )

    data_dir = task_dir / "data"
    evaluator_dir = task_dir / "evaluator"
    starter_dir = task_dir / "starter"
    data_dir.mkdir(exist_ok=True)
    evaluator_dir.mkdir(exist_ok=True)
    starter_dir.mkdir(exist_ok=True)
    shutil.copy2(export_dir / "agent" / "train.jsonl", data_dir / "train.jsonl")
    shutil.copy2(export_dir / "agent" / "test.jsonl", data_dir / "test.jsonl")
    shutil.copy2(export_dir / "hidden" / "labels.json", evaluator_dir / "labels.json")
    labels = json.loads((evaluator_dir / "labels.json").read_text(encoding="utf-8"))
    train_rows = _read_jsonl(data_dir / "train.jsonl")
    test_rows = _read_jsonl(data_dir / "test.jsonl")
    scoring_column = str(info["scoring_column"])

    if metric == "Accuracy":
        observed_labels = [
            label
            for row in train_rows
            for label in _classification_labels(row[scoring_column])
        ]
        label_counts = Counter(observed_labels)
        majority = label_counts.most_common(1)[0][0]
        baseline_score = sum(
            str(majority) in set(_answer_texts(label)) for label in labels
        ) / len(labels)
        values = sorted(set(observed_labels), key=str)
        candidates = [
            {"constant_label": value}
            for value, _ in label_counts.most_common(101)
            if value != majority
        ][:100]
        if not candidates:
            candidates = [{"constant_label": majority}]
        starter_source = _ACCURACY_STARTER.replace(
            "__BASELINE_LABEL__", repr(majority)
        )
        baseline_parameters = {"constant_label": majority}
        guidance = {"constant_label": f"one of the observed training labels: {values}"}
    elif metric == "ExactMatch":
        observed_answers = [
            answer
            for row in train_rows
            for answer in _answer_texts(row[scoring_column])
        ]
        majority = Counter(observed_answers).most_common(1)[0][0]
        baseline_score = sum(
            " ".join(str(majority).strip().lower().split())
            in {" ".join(item.strip().lower().split()) for item in _answer_texts(label)}
            for label in labels
        ) / len(labels)
        candidates = [{"constant_label": ""}]
        starter_source = _ACCURACY_STARTER.replace(
            "__BASELINE_LABEL__", repr(majority)
        )
        baseline_parameters = {"constant_label": majority}
        guidance = {
            "constant_label": "free-text answer; submissions use normalized exact match against any reference answer"
        }
    else:
        columns = list(info["input_columns"])
        if len(columns) < 2:
            raise ValueError("Spearman AIRS-lite task requires two input columns")
        predictions = _similarity_predictions(test_rows, columns[0], columns[1], 1.0)
        baseline_score = _spearman(predictions, [float(item) for item in labels])
        candidates = [{"alpha": value} for value in (0.75, 0.5, 0.25)]
        starter_source = (
            _SPEARMAN_STARTER.replace("__COLUMN_A__", repr(columns[0]))
            .replace("__COLUMN_B__", repr(columns[1]))
        )
        baseline_parameters = {"alpha": 1.0}
        guidance = {
            "alpha": "blend lexical Jaccard and token-length ratio; bounded real value in [0, 1]"
        }

    if metric_improvement(spec.target_score, baseline_score, spec.direction) <= 0:
        raise ValueError(
            f"AIRS target {spec.target_score} does not improve on activated baseline {baseline_score}"
        )
    (starter_dir / "run_experiment.py").write_text(
        starter_source, encoding="utf-8", newline="\n"
    )
    evaluator_source = _LITE_EVALUATOR.replace("__METRIC__", repr(metric))
    (evaluator_dir / "evaluate.py").write_text(
        evaluator_source, encoding="utf-8", newline="\n"
    )
    payload = spec.model_dump(mode="json")
    payload.update(
        {
            "baseline_score": baseline_score,
            "baseline_parameters": baseline_parameters,
            "parameter_guidance": guidance,
            "candidate_parameters": candidates,
            "max_iterations": max(spec.max_iterations, 10),
            "timeout_seconds": 120,
            "required_repeats": 2,
            "seeds": list(range(10)),
            "reproducibility_tolerance": 0.0,
            "starter_entrypoint": "run_experiment.py",
            "evaluator_entrypoint": "evaluate.py",
            "compute_tier": "cpu",
            "runnable": True,
            "setup_instructions": (
                "AIRS-lite uses the official dataset split and metric semantics with a pure-Python "
                "local evaluator. It remains non-publishable without container isolation."
            ),
        }
    )
    activated = BenchmarkTaskSpec.model_validate(payload)
    write_json_atomic(task_dir / "task.json", activated)
    return activated
