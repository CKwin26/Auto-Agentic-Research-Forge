from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import metric_improvement, transition, validate_metrics, verify_frozen_contracts
from .models import (
    Direction,
    EvidenceRecord,
    ExecutionContract,
    ExperimentProposal,
    PromotionRecord,
    ProposalEnvelope,
    RunRecord,
    Stage,
    TrialResult,
    utc_now,
)
from .storage import (
    append_jsonl,
    load_jsonl,
    load_state,
    read_json,
    safe_relative,
    save_state,
    sha256_tree,
    write_json_atomic,
)
from .runtime import (
    ExecutionRuntime,
    RuntimeKind,
    RuntimeOptions,
    TrialRuntimeResult,
    build_runtime,
)


_SECRET_NAME = re.compile(r"(API.?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE.?KEY)", re.IGNORECASE)


def _run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _sanitized_environment(project: Path, run_dir: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not _SECRET_NAME.search(name)}
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "AUTORESEARCH_PROJECT_DIR": str(project),
            "AUTORESEARCH_RUN_DIR": str(run_dir),
        }
    )
    return env


def _apply_proposal(experiment_dir: Path, proposal: ExperimentProposal) -> list[str]:
    changed: list[str] = []
    for replacement in proposal.file_replacements:
        destination = safe_relative(experiment_dir, replacement.path.replace("\\", "/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(replacement.content, encoding="utf-8", newline="\n")
        changed.append(destination.relative_to(experiment_dir).as_posix())
    return changed


def _load_reference(project: Path, run_id: str) -> RunRecord:
    return RunRecord.model_validate(read_json(project / "runs" / run_id / "record.json"))


def _artifact_hash(experiment_dir: Path, parameters: dict[str, object]) -> str:
    digest = hashlib.sha256()
    digest.update(sha256_tree(experiment_dir).encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(parameters, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def execute_run(
    project: Path,
    *,
    envelope: ProposalEnvelope | None = None,
    repeats: int | None = None,
    runtime: RuntimeKind | RuntimeOptions | ExecutionRuntime = "local",
) -> RunRecord:
    state = load_state(project)
    is_baseline = envelope is None
    if state.active_run_id:
        raise ValueError(f"another run is active: {state.active_run_id}; use recover if it was interrupted")
    if is_baseline and state.stage != Stage.BASELINE_PENDING:
        raise ValueError("baseline can only run while stage is baseline_pending")
    if not is_baseline and state.stage not in {Stage.EXPERIMENT_DESIGN, Stage.RESULT_REVIEW}:
        raise ValueError("candidate run requires experiment_design or result_review stage")
    if envelope is not None and not envelope.valid:
        raise ValueError("proposal failed deterministic validation")

    contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    runtime_engine = build_runtime(runtime)
    runtime_attestation = runtime_engine.attestation(
        evaluator_separated=contract.evaluator_command is not None
    )
    isolation_verified = bool(runtime_attestation.get("isolation_verified"))
    contract_hash = verify_frozen_contracts(project)
    if state.run_count >= contract.max_runs:
        raise ValueError(f"run budget exhausted ({contract.max_runs})")
    repeat_count = repeats if repeats is not None else contract.required_repeats
    if repeat_count < contract.required_repeats or repeat_count > contract.max_repeats:
        raise ValueError(
            f"repeats must be between {contract.required_repeats} and {contract.max_repeats}"
        )

    run_id = _run_id("baseline" if is_baseline else "run")
    run_dir = project / "runs" / run_id
    workspace = run_dir / "workspace"
    experiment_dir = workspace / "experiment"
    evaluator_dir = project / "evaluator"
    run_dir.mkdir(parents=True)
    shutil.copytree(
        project / "experiment",
        experiment_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    proposal = envelope.proposal if envelope else None
    canonical_parameters = read_json(project / "current_parameters.json")
    parameters = {**canonical_parameters, **(proposal.parameter_map() if proposal else {})}
    changed_files = _apply_proposal(experiment_dir, proposal) if proposal else []
    code_hash = _artifact_hash(experiment_dir, parameters)

    write_json_atomic(run_dir / "parameters.json", parameters)
    write_json_atomic(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "proposal_id": envelope.proposal_id if envelope else None,
            "is_baseline": is_baseline,
            "contract_hash": contract_hash,
            "code_hash": code_hash,
            "changed_files": changed_files,
            "repeats": repeat_count,
            "runtime": runtime_engine.name,
            "isolation_verified": isolation_verified,
            "runtime_attestation": runtime_attestation,
        },
    )

    if not is_baseline:
        transition(project, state, Stage.EXPERIMENT_RUNNING, "candidate_run_started", run_id=run_id)
    state.active_run_id = run_id
    save_state(project, state)

    started_at = utc_now()
    trials: list[TrialResult] = []
    for index in range(1, repeat_count + 1):
        trial_dir = run_dir / f"trial-{index:03d}"
        trial_dir.mkdir(parents=True)
        metrics_file = trial_dir / "metrics.json"
        params_file = run_dir / "parameters.json"
        started = time.monotonic()
        runtime_result: TrialRuntimeResult
        metrics: dict[str, float] = {}
        try:
            runtime_result = runtime_engine.run_trial(
                contract=contract,
                project=project,
                experiment_dir=experiment_dir,
                evaluator_dir=evaluator_dir,
                trial_dir=trial_dir,
                params_file=params_file,
                environment=_sanitized_environment(project, trial_dir),
            )
            if runtime_result.error is None:
                raw_metrics = read_json(metrics_file)
                metrics, metric_errors = validate_metrics(raw_metrics, contract)
                if metric_errors:
                    runtime_result.error = "; ".join(metric_errors)
        except Exception as exc:  # The attempt must still enter the evidence ledger.
            runtime_result = TrialRuntimeResult(
                experiment_argv=[],
                evaluator_argv=None,
                error=f"runner error: {type(exc).__name__}: {exc}",
            )
        write_json_atomic(
            trial_dir / "command.json",
            {
                "runtime": runtime_engine.name,
                "experiment_argv": runtime_result.experiment_argv,
                "evaluator_argv": runtime_result.evaluator_argv,
            },
        )
        duration = time.monotonic() - started
        (trial_dir / "stdout.log").write_text(
            runtime_result.stdout, encoding="utf-8", newline="\n"
        )
        (trial_dir / "stderr.log").write_text(
            runtime_result.stderr, encoding="utf-8", newline="\n"
        )
        if contract.evaluator_command is not None:
            (trial_dir / "evaluator.stdout.log").write_text(
                runtime_result.evaluator_stdout, encoding="utf-8", newline="\n"
            )
            (trial_dir / "evaluator.stderr.log").write_text(
                runtime_result.evaluator_stderr, encoding="utf-8", newline="\n"
            )
        trials.append(
            TrialResult(
                index=index,
                exit_code=runtime_result.exit_code,
                evaluator_exit_code=runtime_result.evaluator_exit_code,
                duration_seconds=duration,
                metrics=metrics,
                valid=runtime_result.error is None,
                error=runtime_result.error,
            )
        )

    aggregate: dict[str, float] = {}
    stddev: dict[str, float] = {}
    if trials and all(trial.valid for trial in trials):
        metric_names = sorted(set.intersection(*(set(trial.metrics) for trial in trials)))
        for name in metric_names:
            values = [trial.metrics[name] for trial in trials]
            aggregate[name] = statistics.fmean(values)
            stddev[name] = statistics.pstdev(values) if len(values) > 1 else 0.0

    valid = bool(trials) and all(trial.valid for trial in trials) and contract.primary_metric in aggregate
    error = None if valid else "; ".join(filter(None, (trial.error for trial in trials))) or "invalid run"
    improvement: float | None = None
    if is_baseline:
        verdict = "baseline_verified" if valid else "invalid"
    elif valid:
        reference_id = state.best_run_id or state.baseline_run_id
        if not reference_id:
            raise ValueError("candidate run has no verified baseline reference")
        reference = _load_reference(project, reference_id)
        improvement = metric_improvement(
            aggregate[contract.primary_metric],
            reference.aggregate_metrics[contract.primary_metric],
            contract.direction,
        )
        verdict = "candidate_improves" if improvement > contract.min_delta else "valid_non_improving"
    else:
        verdict = "invalid"

    try:
        post_hash = verify_frozen_contracts(project)
        if post_hash != contract_hash:
            raise ValueError("frozen contract digest changed during run")
    except Exception as exc:
        valid = False
        verdict = "invalid"
        error = f"{error + '; ' if error else ''}{exc}"

    finished_at = utc_now()
    record = RunRecord(
        run_id=run_id,
        proposal_id=envelope.proposal_id if envelope else None,
        is_baseline=is_baseline,
        started_at=started_at,
        finished_at=finished_at,
        contract_hash=contract_hash,
        code_hash=code_hash,
        runtime=runtime_engine.name,
        isolation_verified=isolation_verified,
        runtime_attestation=runtime_attestation,
        parameters=parameters,
        trials=trials,
        aggregate_metrics=aggregate,
        metric_stddev=stddev,
        valid=valid,
        verdict=verdict,
        improvement=improvement,
        error=error,
    )
    write_json_atomic(run_dir / "record.json", record)
    append_jsonl(
        project / "evidence.jsonl",
        EvidenceRecord(
            run_id=run_id,
            proposal_id=record.proposal_id,
            is_baseline=is_baseline,
            valid=valid,
            verdict=verdict,
            primary_metric=contract.primary_metric,
            primary_value=aggregate.get(contract.primary_metric),
            improvement=improvement,
            aggregate_metrics=aggregate,
            code_hash=code_hash,
            contract_hash=contract_hash,
            runtime=runtime_engine.name,
            isolation_verified=isolation_verified,
            error=error,
        ),
    )

    state = load_state(project)
    state.run_count += 1
    state.active_run_id = None
    if is_baseline and valid:
        state.baseline_run_id = run_id
        state.best_run_id = run_id
        transition(project, state, Stage.BASELINE_VERIFIED, "baseline_verified", run_id=run_id)
    elif is_baseline:
        transition(project, state, Stage.BASELINE_PENDING, "baseline_rejected", run_id=run_id)
    else:
        transition(
            project,
            state,
            Stage.RESULT_REVIEW,
            "candidate_run_finished",
            run_id=run_id,
            valid=valid,
            verdict=verdict,
        )
    save_state(project, state)
    return record


def promote_run(project: Path, run_id: str, confirmation: str) -> PromotionRecord:
    if confirmation != run_id:
        raise ValueError("promotion confirmation must exactly match the run id")
    state = load_state(project)
    if state.stage != Stage.RESULT_REVIEW:
        raise ValueError("promotion is only allowed during result_review")
    record = _load_reference(project, run_id)
    if not record.valid or record.verdict != "candidate_improves":
        raise ValueError("only a valid improving candidate can be promoted")
    source = project / "runs" / run_id / "workspace" / "experiment"
    if _artifact_hash(source, record.parameters) != record.code_hash:
        raise ValueError("run workspace changed after validation")
    target = project / "experiment"
    previous_parameters = read_json(project / "current_parameters.json")
    previous_hash = _artifact_hash(target, previous_parameters)
    promotion_id = f"promotion-{run_id}"
    backup = project / "lineage" / promotion_id / "before"
    shutil.copytree(target, backup)

    changed: list[str] = []
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source).as_posix()
        destination = safe_relative(target, relative)
        if not destination.exists() or source_file.read_bytes() != destination.read_bytes():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            changed.append(relative)
    write_json_atomic(project / "current_parameters.json", record.parameters)
    if record.parameters != previous_parameters:
        changed.append("current_parameters.json")
    promoted_hash = _artifact_hash(target, record.parameters)
    promotion = PromotionRecord(
        run_id=run_id,
        proposal_id=record.proposal_id,
        previous_code_hash=previous_hash,
        promoted_code_hash=promoted_hash,
        files=changed,
    )
    write_json_atomic(project / "lineage" / promotion_id / "record.json", promotion)
    append_jsonl(project / "lineage.jsonl", promotion)
    state.best_run_id = run_id
    transition(project, state, Stage.EXPERIMENT_DESIGN, "candidate_promoted", run_id=run_id)
    save_state(project, state)
    return promotion


def recover_run(project: Path, confirmation: str) -> RunRecord:
    state = load_state(project)
    run_id = state.active_run_id
    if not run_id:
        raise ValueError("there is no active run to recover")
    if confirmation != run_id:
        raise ValueError("recovery confirmation must exactly match the active run id")
    run_dir = project / "runs" / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("active run has no manifest; manual inspection is required")
    manifest = read_json(manifest_path)
    is_baseline = bool(manifest.get("is_baseline"))
    record_path = run_dir / "record.json"
    if record_path.is_file():
        record = RunRecord.model_validate(read_json(record_path))
    else:
        now = utc_now()
        record = RunRecord(
            run_id=run_id,
            proposal_id=manifest.get("proposal_id"),
            is_baseline=is_baseline,
            started_at=now,
            finished_at=now,
            contract_hash=str(manifest.get("contract_hash") or "unknown"),
            code_hash=str(manifest.get("code_hash") or "unknown"),
            runtime=str(manifest.get("runtime") or "local"),
            isolation_verified=bool(manifest.get("isolation_verified")),
            runtime_attestation=dict(manifest.get("runtime_attestation") or {}),
            parameters=read_json(run_dir / "parameters.json") if (run_dir / "parameters.json").is_file() else {},
            trials=[],
            valid=False,
            verdict="invalid",
            error="run was marked abandoned by explicit recovery",
        )
        write_json_atomic(record_path, record)

    contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    existing_evidence = load_jsonl(project / "evidence.jsonl")
    if not any(item.get("run_id") == run_id for item in existing_evidence):
        append_jsonl(
            project / "evidence.jsonl",
            EvidenceRecord(
                run_id=run_id,
                proposal_id=record.proposal_id,
                is_baseline=is_baseline,
                valid=record.valid,
                verdict=record.verdict,
                primary_metric=contract.primary_metric,
                primary_value=record.aggregate_metrics.get(contract.primary_metric),
                improvement=record.improvement,
                aggregate_metrics=record.aggregate_metrics,
                code_hash=record.code_hash,
                contract_hash=record.contract_hash,
                runtime=record.runtime,
                isolation_verified=record.isolation_verified,
                error=record.error,
            ),
        )

    state.run_count += 1
    state.active_run_id = None
    if is_baseline and record.valid:
        state.baseline_run_id = run_id
        state.best_run_id = run_id
        transition(project, state, Stage.BASELINE_VERIFIED, "baseline_recovered", run_id=run_id)
    elif is_baseline:
        transition(project, state, Stage.BASELINE_PENDING, "baseline_abandoned", run_id=run_id)
    else:
        transition(
            project,
            state,
            Stage.RESULT_REVIEW,
            "candidate_recovered",
            run_id=run_id,
            valid=record.valid,
            verdict=record.verdict,
        )
    save_state(project, state)
    return record
