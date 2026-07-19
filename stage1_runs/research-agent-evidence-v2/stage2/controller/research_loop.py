from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .benchmark import (
    DEFAULT_BENCHMARK_ROOT,
    _grid_proposal,
    _render_report,
    _score_project,
    load_task,
    materialize_task,
    seed_project_slug,
)
from .benchmark_models import (
    BenchmarkReport,
    BenchmarkSeedResult,
    LoopConfig,
    PendingLoopCandidate,
    ResearchLoopState,
)
from .contracts import metric_improvement
from .models import Direction, ExperimentProposal, ProposalEnvelope, RunRecord, Stage, utc_now
from .runner import execute_run, promote_run, recover_run
from .runtime import (
    DEFAULT_CONTROLLED_CPU_IMAGE,
    ExecutionRuntime,
    RuntimeKind,
    RuntimeOptions,
    build_runtime,
)
from .service import propose_experiment
from .storage import (
    append_jsonl,
    load_jsonl,
    load_state,
    read_json,
    safe_relative,
    sha256_tree,
    write_json_atomic,
)


def _config_hash(config: LoopConfig) -> str:
    payload = json.dumps(config.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _state_path(project: Path) -> Path:
    return project / "benchmark" / "loop_state.json"


def _events_path(project: Path) -> Path:
    return project / "benchmark" / "loop_events.jsonl"


def _save_loop_state(project: Path, state: ResearchLoopState) -> None:
    state.updated_at = utc_now()
    write_json_atomic(_state_path(project), state)


def _loop_event(project: Path, state: ResearchLoopState, event: str, **details: object) -> None:
    append_jsonl(
        _events_path(project),
        {
            "recorded_at": utc_now(),
            "loop_id": state.loop_id,
            "iteration": state.completed_iterations,
            "event": event,
            "details": details,
        },
    )


def _candidate_tree_hash(project: Path, proposal: ExperimentProposal | None) -> str:
    experiment = project / "experiment"
    replacements = {
        replacement.path.replace("\\", "/"): replacement.content.encode("utf-8")
        for replacement in (proposal.file_replacements if proposal else [])
    }
    for relative in replacements:
        safe_relative(experiment, relative)
    existing = {
        path.relative_to(experiment).as_posix(): path
        for path in experiment.rglob("*")
        if path.is_file()
    }
    digest = hashlib.sha256()
    for relative in sorted(set(existing) | set(replacements)):
        content = replacements.get(relative)
        if content is None:
            content = existing[relative].read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def candidate_fingerprint(project: Path, proposal: ExperimentProposal | None = None) -> str:
    parameters = read_json(project / "current_parameters.json")
    if proposal is not None:
        parameters.update(proposal.parameter_map())
    canonical_parameters = {
        name: float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else value
        for name, value in parameters.items()
    }
    digest = hashlib.sha256()
    digest.update(_candidate_tree_hash(project, proposal).encode("ascii"))
    digest.update(b"\0")
    digest.update(
        json.dumps(canonical_parameters, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    return digest.hexdigest()


def canonical_state_fingerprint(project: Path) -> str:
    """Hash the exact canonical code-plus-parameter state proposals must target."""

    return candidate_fingerprint(project)


def _proposal_axes(proposal: ExperimentProposal) -> list[str]:
    axes = [f"parameter:{item.name}" for item in proposal.parameters]
    axes.extend(f"file:{item.path.replace('\\', '/')}" for item in proposal.file_replacements)
    return sorted(set(axes))


def _information_score(
    proposal: ExperimentProposal,
    explored_axes: set[str],
    diagnosis: dict[str, object] | None = None,
) -> float:
    axes = _proposal_axes(proposal)
    new_axes = sum(axis not in explored_axes for axis in axes)
    change_count = max(len(axes), 1)
    focused_bonus = 3.0 / change_count
    novelty = 10.0 * new_axes
    repair_bonus = (
        20.0
        if (diagnosis or {}).get("failure_mode") == "invalid_execution"
        and proposal.file_replacements
        else 0.0
    )
    complexity_penalty = max(change_count - 1, 0) * 0.5
    time_penalty = proposal.estimated_minutes / 10_080.0
    return novelty + repair_bonus + focused_bonus - complexity_penalty - time_penalty


def diagnose_project(project: Path, spec_metric: str, direction: Direction) -> dict[str, object]:
    evidence = load_jsonl(project / "evidence.jsonl")
    candidates = [item for item in evidence if not item.get("is_baseline")]
    valid = [item for item in candidates if item.get("valid")]
    improving = [item for item in valid if item.get("verdict") == "candidate_improves"]
    invalid = [item for item in candidates if not item.get("valid")]
    values = [float(item["primary_value"]) for item in valid if item.get("primary_value") is not None]
    best_value = None
    if values:
        best_value = max(values) if direction == Direction.MAXIMIZE else min(values)
    recent = [
        {
            "run_id": item.get("run_id"),
            "verdict": item.get("verdict"),
            "primary_value": item.get("primary_value"),
            "improvement": item.get("improvement"),
            "error": item.get("error"),
        }
        for item in candidates[-8:]
    ]
    return {
        "primary_metric": spec_metric,
        "candidate_runs": len(candidates),
        "valid_runs": len(valid),
        "invalid_runs": len(invalid),
        "improving_runs": len(improving),
        "best_candidate_value": best_value,
        "recent_outcomes": recent,
        "failure_mode": (
            "invalid_execution"
            if invalid and len(invalid) >= len(valid)
            else "metric_plateau"
            if len(valid) >= 2 and not improving[-2:]
            else "continue_ablation"
        ),
    }


def _visible_data_manifest(project: Path, *, limit: int = 200) -> dict[str, object]:
    """Describe only the data files actually materialized for the experiment agent."""

    data_root = project / "data"
    files = [path for path in sorted(data_root.rglob("*")) if path.is_file()]
    visible = files[:limit]
    return {
        "root": "data",
        "files": [
            {
                "path": path.relative_to(project).as_posix(),
                "bytes": path.stat().st_size,
            }
            for path in visible
        ],
        "complete": len(files) <= limit,
        "omitted_file_count": max(len(files) - limit, 0),
        "policy": (
            "This manifest is the authoritative view of materialized agent-visible data. "
            "When complete is true, do not assume that an unlisted split, file, or directory "
            "exists even if the imported task description mentions it. Derive any validation "
            "split deterministically from listed labeled training data when scientifically needed."
        ),
    }


def _proposal_focus(
    *,
    task_id: str,
    seed: int,
    iteration: int,
    config: LoopConfig,
    diagnosis: dict[str, object],
    explored_axes: list[str],
    candidate_history: list[dict[str, object]],
    parameter_guidance: dict[str, str],
    visible_data_manifest: dict[str, object],
) -> str:
    capabilities = config.runtime_capabilities
    if (
        capabilities.get("controlled") is True
        and capabilities.get("verified") is True
    ):
        dependency_policy = (
            "The controlled image capability manifest was verified by an isolated import and "
            "smoke-test probe. You may use only the exact packages listed in "
            "runtime_capabilities.packages; do not install, download, or assume any unlisted "
            "dependency. Invalid-run stderr in candidate_history remains authoritative diagnostic "
            "data."
        )
    else:
        dependency_policy = (
            "Do not assume a third-party Python package is installed. Treat a slim Python "
            "image as standard-library-only unless canonical code or a prior successful run "
            "proves otherwise. Invalid-run stderr in candidate_history is authoritative "
            "diagnostic data; repair that concrete failure without changing the evaluator."
        )
    if not config.deduplicate_candidates:
        novelty_requirement = (
            "Novel target states are preferred but not enforced in this ablation; an exact "
            "repeat may consume another candidate-run budget."
        )
    elif explored_axes:
        novelty_requirement = (
            "At least one causal axis must be absent from explored_axes unless the diagnosis is "
            "invalid_execution and the proposal is a non-equivalent repair. Do not spend another "
            "measurement on an explored-only target when the contract permits a new code or "
            "parameter axis."
        )
    else:
        novelty_requirement = (
            "Choose the smallest informative causal axis; exact targets in candidate_history "
            "are forbidden."
        )
    if config.failure_diagnosis:
        diagnosis_policy = (
            "Use the deterministic diagnosis and address its failure mode. Invalid execution "
            "receives an explicit repair priority in candidate ranking."
        )
    else:
        diagnosis_policy = (
            "Deterministic failure diagnosis is disabled for this controller ablation. Raw "
            "candidate history remains evidence, but the controller supplies no failure-mode "
            "classification or repair priority."
        )
    duplicate_policy = (
        "Treat every readable target in candidate_history as forbidden even if it was rejected, "
        "is still pending, or can be described with different prose."
        if config.deduplicate_candidates
        else (
            "Deterministic duplicate detection is disabled for this controller ablation. The "
            "controller may execute a repeated code-plus-parameter target and will count it as "
            "a separate budgeted candidate."
        )
    )
    return json.dumps(
        {
            "controller": "Research Forge deterministic run-loop v1",
            "task_id": task_id,
            "seed": seed,
            "iteration": iteration,
            "iteration_budget": config.iterations,
            "diagnosis": diagnosis,
            "parameter_guidance": parameter_guidance,
            "visible_data_manifest": visible_data_manifest,
            "explored_axes": explored_axes,
            "candidate_history": candidate_history,
            "controller_feature_flags": {
                "candidate_pool": config.candidate_pool_size > 1,
                "duplicate_detection": config.deduplicate_candidates,
                "failure_diagnosis": config.failure_diagnosis,
            },
            "runtime_constraints": {
                "runtime": config.runtime,
                "runtime_options": config.runtime_options,
                "runtime_capabilities": capabilities,
                "network_access": "disabled" if config.runtime == "docker" else "host-dependent",
                "dependency_policy": dependency_policy,
            },
            "novelty_requirement": novelty_requirement,
            "diagnosis_policy": diagnosis_policy,
            "selection_policy": (
                "Propose exactly one minimal falsifiable experiment. Prefer one previously unexplored "
                "axis and avoid accidental no-op changes. "
                f"{duplicate_policy} {diagnosis_policy} "
                "The controller, not the agent, decides whether to execute, promote, retry, or stop."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def _run_stderr_excerpt(project: Path, run_id: object, *, limit: int = 2_400) -> str | None:
    if not run_id:
        return None
    run_dir = project / "runs" / str(run_id)
    excerpts: list[str] = []
    seen: set[str] = set()
    for path in sorted(run_dir.glob("trial-*/stderr.log")):
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            continue
        tail = content[-1_200:]
        if tail in seen:
            continue
        seen.add(tail)
        excerpts.append(f"{path.parent.name}: {tail}")
    if not excerpts:
        return None
    return "\n---\n".join(excerpts)[-limit:]


def _candidate_target_history(
    project: Path,
    state: ResearchLoopState,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Return compact, semantic target summaries for the proposal model.

    Fingerprints remain the deterministic equality authority, but a model cannot infer which
    experiment a hexadecimal digest represents. Grouping proposal envelopes by their concrete
    parameter values and replacement hashes gives the model useful duplicate feedback without
    exposing protected evaluator data or adding full replacement bodies to the prompt.
    """

    evidence_by_proposal = {
        str(item["proposal_id"]): item
        for item in load_jsonl(project / "evidence.jsonl")
        if item.get("proposal_id")
    }
    pending_ids = {item.proposal_id for item in state.pending_candidates}
    invalidated_ids = set(state.invalidated_proposal_ids)
    groups: dict[str, dict[str, object]] = {}
    proposal_dir = project / "proposals"
    for path in sorted(proposal_dir.glob("*.json")):
        envelope = ProposalEnvelope.model_validate(read_json(path))
        proposal = envelope.proposal
        parameters = proposal.parameter_map()
        replacements = [
            {
                "path": replacement.path.replace("\\", "/"),
                "reason": replacement.reason,
                "content_sha256_prefix": hashlib.sha256(
                    replacement.content.encode("utf-8")
                ).hexdigest()[:16],
            }
            for replacement in sorted(proposal.file_replacements, key=lambda item: item.path)
        ]
        target_key = json.dumps(
            {"parameters": parameters, "file_replacements": replacements},
            ensure_ascii=False,
            sort_keys=True,
        )
        evidence = evidence_by_proposal.get(envelope.proposal_id)
        if evidence is not None:
            status = "executed"
        elif state.active_proposal_id == envelope.proposal_id:
            status = "active"
        elif envelope.proposal_id in pending_ids:
            status = "pending"
        elif envelope.proposal_id in invalidated_ids:
            status = "stale_invalidated"
        elif not envelope.valid and any(
            "equivalent candidate" in error for error in envelope.validation_errors
        ):
            status = "rejected_duplicate"
        elif not envelope.valid:
            status = "rejected_contract"
        else:
            status = "generated"

        group = groups.setdefault(
            target_key,
            {
                "title": proposal.title,
                "hypothesis": proposal.hypothesis[:320],
                "parameters": parameters,
                "file_replacements": replacements,
                "axes": _proposal_axes(proposal),
                "attempt_count": 0,
                "statuses": [],
                "last_created_at": envelope.created_at,
            },
        )
        group["attempt_count"] = int(group["attempt_count"]) + 1
        statuses = list(group["statuses"])
        if status not in statuses:
            statuses.append(status)
            group["statuses"] = statuses
        group["last_created_at"] = max(str(group["last_created_at"]), envelope.created_at)
        if evidence is not None:
            group["outcome"] = {
                "run_id": evidence.get("run_id"),
                "valid": evidence.get("valid"),
                "verdict": evidence.get("verdict"),
                "primary_value": evidence.get("primary_value"),
                "improvement": evidence.get("improvement"),
                "error": evidence.get("error"),
                "stderr_excerpt": _run_stderr_excerpt(project, evidence.get("run_id")),
            }

    ordered = sorted(groups.values(), key=lambda item: str(item["last_created_at"]))
    return ordered[-limit:]


def _reject_duplicate(project: Path, envelope: ProposalEnvelope, fingerprint: str) -> None:
    envelope.valid = False
    envelope.validation_errors.append(
        f"equivalent candidate already generated or executed: {fingerprint}"
    )
    write_json_atomic(project / "proposals" / f"{envelope.proposal_id}.json", envelope)


def _load_envelope(project: Path, proposal_id: str) -> ProposalEnvelope:
    return ProposalEnvelope.model_validate(read_json(project / "proposals" / f"{proposal_id}.json"))


def _normalize_pending(
    project: Path,
    state: ResearchLoopState,
    config: LoopConfig,
) -> list[PendingLoopCandidate]:
    normalized: list[PendingLoopCandidate] = []
    executed = set(state.executed_fingerprints)
    seen = set(state.seen_fingerprints)
    current_state_fingerprint = canonical_state_fingerprint(project)
    for candidate in state.pending_candidates:
        envelope = _load_envelope(project, candidate.proposal_id)
        if not envelope.valid:
            continue
        fingerprint = candidate_fingerprint(project, envelope.proposal)
        if candidate.generated_from_fingerprint != current_state_fingerprint:
            if fingerprint != candidate.fingerprint:
                if candidate.proposal_id not in state.invalidated_proposal_ids:
                    state.invalidated_proposal_ids.append(candidate.proposal_id)
                _loop_event(
                    project,
                    state,
                    "pending_candidate_invalidated_after_state_change",
                    proposal_id=envelope.proposal_id,
                    previous_target_fingerprint=candidate.fingerprint,
                    rebased_target_fingerprint=fingerprint,
                    generated_from_fingerprint=candidate.generated_from_fingerprint,
                    current_state_fingerprint=current_state_fingerprint,
                )
                continue
            _loop_event(
                project,
                state,
                "pending_candidate_revalidated_after_state_change",
                proposal_id=envelope.proposal_id,
                target_fingerprint=fingerprint,
                generated_from_fingerprint=candidate.generated_from_fingerprint,
                current_state_fingerprint=current_state_fingerprint,
            )
        if config.deduplicate_candidates and fingerprint in executed:
            _reject_duplicate(project, envelope, fingerprint)
            _loop_event(
                project,
                state,
                "pending_candidate_rejected_as_noop",
                proposal_id=envelope.proposal_id,
                fingerprint=fingerprint,
            )
            continue
        if fingerprint not in seen:
            state.seen_fingerprints.append(fingerprint)
            seen.add(fingerprint)
        normalized.append(
            PendingLoopCandidate(
                proposal_id=candidate.proposal_id,
                fingerprint=fingerprint,
                information_score=_information_score(
                    envelope.proposal,
                    set(state.explored_axes),
                    state.last_diagnosis,
                ),
                generated_at_iteration=candidate.generated_at_iteration,
                generated_from_fingerprint=current_state_fingerprint,
            )
        )
    return normalized


async def _fill_candidate_pool(
    project: Path,
    *,
    spec: object,
    seed: int,
    config: LoopConfig,
    state: ResearchLoopState,
) -> None:
    state.pending_candidates = _normalize_pending(project, state, config)
    generation_state_fingerprint = canonical_state_fingerprint(project)
    attempts_this_iteration = 0
    while (
        len(state.pending_candidates) < config.candidate_pool_size
        and attempts_this_iteration < config.proposal_attempts_per_iteration
    ):
        if config.strategy == "grid":
            candidate_parameters = getattr(spec, "candidate_parameters")
            if state.proposal_attempts >= len(candidate_parameters):
                break
            envelope = _grid_proposal(
                project,
                spec,
                candidate_parameters[state.proposal_attempts],
                state.proposal_attempts + 1,
            )
        else:
            focus = _proposal_focus(
                task_id=getattr(spec, "task_id"),
                seed=seed,
                iteration=state.completed_iterations + 1,
                config=config,
                diagnosis=state.last_diagnosis,
                explored_axes=state.explored_axes,
                candidate_history=_candidate_target_history(project, state),
                parameter_guidance=getattr(spec, "parameter_guidance"),
                visible_data_manifest=_visible_data_manifest(project),
            )
            envelope = await propose_experiment(project, focus)
        if canonical_state_fingerprint(project) != generation_state_fingerprint:
            raise RuntimeError("canonical experiment state changed during proposal generation")
        state.proposal_attempts += 1
        attempts_this_iteration += 1
        if not envelope.valid:
            _loop_event(
                project,
                state,
                "proposal_rejected_by_contract",
                proposal_id=envelope.proposal_id,
                errors=envelope.validation_errors,
            )
            continue
        fingerprint = candidate_fingerprint(project, envelope.proposal)
        if config.deduplicate_candidates and fingerprint in set(state.seen_fingerprints):
            _reject_duplicate(project, envelope, fingerprint)
            _loop_event(
                project,
                state,
                "proposal_rejected_as_duplicate",
                proposal_id=envelope.proposal_id,
                fingerprint=fingerprint,
            )
            continue
        state.seen_fingerprints.append(fingerprint)
        score = _information_score(
            envelope.proposal,
            set(state.explored_axes),
            state.last_diagnosis,
        )
        state.pending_candidates.append(
            PendingLoopCandidate(
                proposal_id=envelope.proposal_id,
                fingerprint=fingerprint,
                information_score=score,
                generated_at_iteration=state.completed_iterations + 1,
                generated_from_fingerprint=generation_state_fingerprint,
            )
        )
        _loop_event(
            project,
            state,
            "proposal_admitted_to_pool",
            proposal_id=envelope.proposal_id,
            fingerprint=fingerprint,
            information_score=score,
            generated_from_fingerprint=generation_state_fingerprint,
        )
        _save_loop_state(project, state)
    _loop_event(
        project,
        state,
        "candidate_pool_ready",
        pool_size=len(state.pending_candidates),
        target_pool_size=config.candidate_pool_size,
        full=len(state.pending_candidates) == config.candidate_pool_size,
        canonical_state_fingerprint=generation_state_fingerprint,
    )
    _save_loop_state(project, state)


def _select_candidate(
    project: Path, state: ResearchLoopState, config: LoopConfig
) -> tuple[PendingLoopCandidate, ProposalEnvelope] | None:
    state.pending_candidates = _normalize_pending(project, state, config)
    if not state.pending_candidates:
        return None
    selected = sorted(
        state.pending_candidates,
        key=lambda item: (-item.information_score, item.generated_at_iteration, item.proposal_id),
    )[0]
    state.pending_candidates = [
        item for item in state.pending_candidates if item.proposal_id != selected.proposal_id
    ]
    return selected, _load_envelope(project, selected.proposal_id)


def _target_reached(value: float, baseline: float, target: float, direction: Direction) -> bool:
    gain = metric_improvement(value, baseline, direction)
    target_gain = metric_improvement(target, baseline, direction)
    return gain >= target_gain


def _best_record(project: Path) -> RunRecord | None:
    project_state = load_state(project)
    run_id = project_state.best_run_id or project_state.baseline_run_id
    if not run_id:
        return None
    return RunRecord.model_validate(read_json(project / "runs" / run_id / "record.json"))


async def _account_candidate_record(
    project: Path,
    state: ResearchLoopState,
    *,
    config: LoopConfig,
    envelope: ProposalEnvelope,
    fingerprint: str,
    record: RunRecord,
    metric: str,
) -> bool:
    already_accounted = (
        fingerprint in set(state.executed_fingerprints)
        if config.deduplicate_candidates
        else envelope.proposal_id in set(state.executed_proposal_ids)
    )
    if not already_accounted:
        state.completed_iterations += 1
        state.executed_fingerprints.append(fingerprint)
        state.executed_proposal_ids.append(envelope.proposal_id)
        state.explored_axes = sorted(
            set(state.explored_axes) | set(_proposal_axes(envelope.proposal))
        )
    promoted = any(
        item.get("run_id") == record.run_id for item in load_jsonl(project / "lineage.jsonl")
    )
    if not already_accounted:
        if record.verdict == "candidate_improves":
            state.consecutive_non_improving = 0
            if config.auto_promote and not promoted:
                await asyncio.to_thread(promote_run, project, record.run_id, record.run_id)
                promoted = True
        elif record.valid:
            state.consecutive_non_improving += 1
        else:
            state.invalid_runs += 1
            state.consecutive_non_improving += 1
    best = _best_record(project)
    if best and best.valid:
        state.best_score = best.aggregate_metrics[metric]
    if promoted and state.pending_candidates:
        state.pending_candidates = _normalize_pending(project, state, config)
    state.active_proposal_id = None
    state.active_fingerprint = None
    state.active_information_score = None
    state.active_generated_from_fingerprint = None
    return promoted


def _find_proposal_run(project: Path, proposal_id: str) -> RunRecord | None:
    matches: list[RunRecord] = []
    for path in sorted((project / "runs").glob("*/record.json")):
        record = RunRecord.model_validate(read_json(path))
        if record.proposal_id == proposal_id:
            matches.append(record)
    return matches[-1] if matches else None


async def run_project_loop(
    project: Path,
    spec: object,
    *,
    seed: int,
    config: LoopConfig,
    runtime: ExecutionRuntime,
) -> ResearchLoopState:
    config_digest = _config_hash(config)
    path = _state_path(project)
    if path.is_file():
        state = ResearchLoopState.model_validate(read_json(path))
        if state.config_hash != config_digest:
            raise ValueError("resume config differs from the persisted run-loop config")
        if state.status in {"completed", "stopped"}:
            return state
        state.resumed_count += 1
        state.status = "running"
        state.stop_reason = None
        _loop_event(project, state, "loop_resumed", resumed_count=state.resumed_count)
    else:
        state = ResearchLoopState(
            loop_id=f"loop-{uuid.uuid4().hex[:12]}",
            task_id=getattr(spec, "task_id"),
            seed=seed,
            config_hash=config_digest,
            status="running",
        )
        _loop_event(project, state, "loop_started", config=config.model_dump(mode="json"))
    _save_loop_state(project, state)

    metric = getattr(spec, "primary_metric")
    project_state = load_state(project)
    if project_state.active_run_id:
        abandoned = await asyncio.to_thread(
            recover_run, project, project_state.active_run_id
        )
        if not abandoned.is_baseline:
            if not state.active_proposal_id or not abandoned.proposal_id:
                raise ValueError("interrupted candidate cannot be reconciled to the loop journal")
            envelope = _load_envelope(project, state.active_proposal_id)
            fingerprint = state.active_fingerprint or candidate_fingerprint(
                project, envelope.proposal
            )
            await _account_candidate_record(
                project,
                state,
                config=config,
                envelope=envelope,
                fingerprint=fingerprint,
                record=abandoned,
                metric=metric,
            )
        _loop_event(
            project,
            state,
            "interrupted_run_recovered",
            run_id=abandoned.run_id,
            is_baseline=abandoned.is_baseline,
        )
        _save_loop_state(project, state)

    if state.active_proposal_id:
        envelope = _load_envelope(project, state.active_proposal_id)
        fingerprint = state.active_fingerprint or candidate_fingerprint(project, envelope.proposal)
        completed = _find_proposal_run(project, state.active_proposal_id)
        if completed is not None:
            promoted = await _account_candidate_record(
                project,
                state,
                config=config,
                envelope=envelope,
                fingerprint=fingerprint,
                record=completed,
                metric=metric,
            )
            _loop_event(
                project,
                state,
                "completed_run_reconciled",
                run_id=completed.run_id,
                proposal_id=envelope.proposal_id,
                promoted=promoted,
            )
        else:
            state.pending_candidates.append(
                PendingLoopCandidate(
                    proposal_id=envelope.proposal_id,
                    fingerprint=fingerprint,
                    information_score=state.active_information_score
                    if state.active_information_score is not None
                    else _information_score(
                        envelope.proposal,
                        set(state.explored_axes),
                        state.last_diagnosis,
                    ),
                    generated_at_iteration=state.completed_iterations + 1,
                    generated_from_fingerprint=state.active_generated_from_fingerprint,
                )
            )
            state.active_proposal_id = None
            state.active_fingerprint = None
            state.active_information_score = None
            state.active_generated_from_fingerprint = None
            _loop_event(
                project,
                state,
                "selected_candidate_requeued",
                proposal_id=envelope.proposal_id,
                fingerprint=fingerprint,
            )
        _save_loop_state(project, state)

    project_state = load_state(project)
    if project_state.stage == Stage.BASELINE_PENDING:
        baseline = await asyncio.to_thread(execute_run, project, runtime=runtime)
        _loop_event(
            project,
            state,
            "baseline_finished",
            run_id=baseline.run_id,
            valid=baseline.valid,
            score=baseline.aggregate_metrics.get(getattr(spec, "primary_metric")),
        )
        if not baseline.valid:
            state.status = "stopped"
            state.stop_reason = "baseline_invalid"
            state.errors.append(baseline.error or "baseline invalid")
            _save_loop_state(project, state)
            return state
    baseline_record = RunRecord.model_validate(
        read_json(
            project
            / "runs"
            / str(load_state(project).baseline_run_id)
            / "record.json"
        )
    )
    baseline_score = baseline_record.aggregate_metrics[metric]
    if not state.seen_fingerprints:
        state.seen_fingerprints.append(candidate_fingerprint(project))
    best = _best_record(project)
    state.best_score = best.aggregate_metrics[metric] if best and best.valid else baseline_score
    _save_loop_state(project, state)

    if (
        config.stop_at_target
        and state.best_score is not None
        and _target_reached(
            state.best_score,
            baseline_score,
            getattr(spec, "target_score"),
            getattr(spec, "direction"),
        )
    ):
        state.status = "completed"
        state.stop_reason = "target_reached"
        _loop_event(project, state, "loop_finished", status=state.status, reason=state.stop_reason)
        _save_loop_state(project, state)
        return state

    while state.completed_iterations < config.iterations:
        if config.failure_diagnosis:
            state.last_diagnosis = diagnose_project(project, metric, getattr(spec, "direction"))
            _loop_event(project, state, "diagnosis_updated", diagnosis=state.last_diagnosis)
        else:
            state.last_diagnosis = {
                "primary_metric": metric,
                "failure_mode": "diagnosis_disabled",
            }
            _loop_event(project, state, "diagnosis_disabled")
        await _fill_candidate_pool(
            project,
            spec=spec,
            seed=seed,
            config=config,
            state=state,
        )
        selected = _select_candidate(project, state, config)
        if selected is None:
            state.status = "stopped"
            state.stop_reason = "proposal_space_exhausted"
            _loop_event(project, state, "loop_stopped", reason=state.stop_reason)
            break
        candidate, envelope = selected
        _loop_event(
            project,
            state,
            "candidate_selected",
            proposal_id=envelope.proposal_id,
            fingerprint=candidate.fingerprint,
            information_score=candidate.information_score,
        )
        state.active_proposal_id = envelope.proposal_id
        state.active_fingerprint = candidate.fingerprint
        state.active_information_score = candidate.information_score
        state.active_generated_from_fingerprint = candidate.generated_from_fingerprint
        _save_loop_state(project, state)
        try:
            record = await asyncio.to_thread(
                execute_run,
                project,
                envelope=envelope,
                runtime=runtime,
            )
        except Exception as exc:
            state.status = "error"
            state.stop_reason = "execution_exception"
            state.errors.append(f"{type(exc).__name__}: {exc}")
            _loop_event(
                project,
                state,
                "loop_error",
                proposal_id=envelope.proposal_id,
                error=state.errors[-1],
            )
            _save_loop_state(project, state)
            raise

        promoted = await _account_candidate_record(
            project,
            state,
            config=config,
            envelope=envelope,
            fingerprint=candidate.fingerprint,
            record=record,
            metric=metric,
        )
        _loop_event(
            project,
            state,
            "iteration_finished",
            run_id=record.run_id,
            proposal_id=envelope.proposal_id,
            fingerprint=candidate.fingerprint,
            valid=record.valid,
            verdict=record.verdict,
            score=record.aggregate_metrics.get(metric),
            improvement=record.improvement,
            promoted=promoted,
        )
        _save_loop_state(project, state)

        if (
            config.stop_at_target
            and state.best_score is not None
            and _target_reached(
                state.best_score,
                baseline_score,
                getattr(spec, "target_score"),
                getattr(spec, "direction"),
            )
        ):
            state.status = "completed"
            state.stop_reason = "target_reached"
            break
        if state.invalid_runs >= config.max_invalid_runs:
            state.status = "stopped"
            state.stop_reason = "invalid_run_budget_exhausted"
            break
        if state.consecutive_non_improving >= config.patience:
            state.status = "stopped"
            state.stop_reason = "non_improvement_patience_exhausted"
            break

    if state.status == "running":
        state.status = "completed"
        state.stop_reason = "iteration_budget_exhausted"
    _loop_event(project, state, "loop_finished", status=state.status, reason=state.stop_reason)
    _save_loop_state(project, state)
    return state


def _runtime_options_for_manifest(runtime: ExecutionRuntime) -> dict[str, object]:
    return runtime.options.public_config()


async def run_loop_benchmark(
    task: str | Path,
    *,
    strategy: str = "codex",
    seeds: Iterable[int] | None = None,
    iterations: int | None = None,
    output_root: str | Path | None = None,
    runtime: RuntimeKind | RuntimeOptions | ExecutionRuntime = "docker",
    candidate_pool_size: int = 3,
    proposal_attempts_per_iteration: int = 6,
    patience: int = 5,
    max_invalid_runs: int = 3,
    deduplicate_candidates: bool = True,
    failure_diagnosis: bool = True,
    resume: str | Path | None = None,
    output_id: str | None = None,
    short_paths: bool = False,
) -> tuple[Path, BenchmarkReport, list[ResearchLoopState]]:
    if strategy not in {"codex", "grid"}:
        raise ValueError("run-loop strategy must be codex or grid")
    if output_id is not None and (
        len(output_id) < 2
        or len(output_id) > 24
        or output_id.strip("abcdefghijklmnopqrstuvwxyz0123456789-")
        or output_id.startswith("-")
        or output_id.endswith("-")
    ):
        raise ValueError("output_id must be 2-24 lowercase letters, digits, or hyphens")
    task_dir, spec = load_task(task)
    selected_seeds = list(seeds) if seeds is not None else list(spec.seeds)
    if not selected_seeds or len(selected_seeds) != len(set(selected_seeds)):
        raise ValueError("run-loop seeds must be non-empty and unique")
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
        raise ValueError("grid run-loop requires registered candidate parameters")
    runtime_engine = build_runtime(
        RuntimeOptions(kind="docker", image=DEFAULT_CONTROLLED_CPU_IMAGE)
        if runtime == "docker"
        else runtime
    )
    config = LoopConfig(
        strategy=strategy,
        runtime=runtime_engine.name,
        iterations=requested_iterations,
        candidate_pool_size=candidate_pool_size,
        proposal_attempts_per_iteration=proposal_attempts_per_iteration,
        patience=patience,
        max_invalid_runs=max_invalid_runs,
        deduplicate_candidates=deduplicate_candidates,
        failure_diagnosis=failure_diagnosis,
        runtime_options=_runtime_options_for_manifest(runtime_engine),
        runtime_capabilities=runtime_engine.capabilities(),
    )

    if resume is not None:
        output = Path(resume).resolve()
        manifest = read_json(output / "loop_manifest.json")
        if manifest.get("task_id") != spec.task_id:
            raise ValueError("resume task differs from loop manifest")
        if manifest.get("task_hash") != sha256_tree(task_dir):
            raise ValueError("source task pack changed since the loop was created")
        if manifest.get("config") != config.model_dump(mode="json"):
            raise ValueError("resume options differ from loop manifest")
        if manifest.get("seeds") != selected_seeds:
            raise ValueError("resume seeds differ from loop manifest")
        if bool(manifest.get("short_paths", False)) != short_paths:
            raise ValueError("resume short-path mode differs from loop manifest")
        if output_id is not None and manifest.get("benchmark_id") != output_id:
            raise ValueError("resume output_id differs from loop manifest")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        benchmark_id = output_id or (
            f"{spec.task_id[:24]}-loop-{strategy}-{stamp}-{uuid.uuid4().hex[:6]}"
        )
        root = Path(output_root or DEFAULT_BENCHMARK_ROOT).resolve()
        output = root / benchmark_id
        output.mkdir(parents=True)
        write_json_atomic(
            output / "loop_manifest.json",
            {
                "schema_version": 1,
                "benchmark_id": benchmark_id,
                "task_id": spec.task_id,
                "task_source": str(task_dir),
                "task_hash": sha256_tree(task_dir),
                "seeds": selected_seeds,
                "short_paths": short_paths,
                "config": config.model_dump(mode="json"),
                "created_at": utc_now(),
            },
        )
    manifest = read_json(output / "loop_manifest.json")
    benchmark_id = str(manifest["benchmark_id"])
    projects_root = output / "projects"
    project_results: list[BenchmarkSeedResult] = []
    loop_states: list[ResearchLoopState] = []

    for seed in selected_seeds:
        started = time.monotonic()
        errors: list[str] = []
        project_slug = f"s{seed}" if short_paths else seed_project_slug(spec.task_id, seed)
        project = projects_root / project_slug
        if not project.is_dir():
            project = materialize_task(
                task_dir,
                spec,
                seed=seed,
                project_root=projects_root,
                project_slug=project_slug,
            )
        try:
            loop_state = await run_project_loop(
                project,
                spec,
                seed=seed,
                config=config,
                runtime=runtime_engine,
            )
            errors.extend(loop_state.errors)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if _state_path(project).is_file():
                loop_state = ResearchLoopState.model_validate(read_json(_state_path(project)))
            else:
                raise
        loop_states.append(loop_state)
        project_results.append(
            _score_project(
                project,
                spec,
                seed=seed,
                requested_iterations=requested_iterations,
                wall_time_seconds=time.monotonic() - started,
                errors=errors,
            )
        )

    gains = [item.normalized_gain for item in project_results]
    from .agent_runtime import model_name

    model = model_name() if strategy == "codex" else "rfbench-grid"
    limitations = [
        "The controller automates bounded experiments; it does not establish scientific novelty or manuscript quality."
    ]
    if spec.source == "rfbench":
        limitations.insert(0, "Built-in CPU tasks are controller diagnostics, not scientific results.")
    elif spec.source == "airs":
        limitations.insert(
            0,
            "AIRS-lite preserves the official split and metric semantics but is not an official leaderboard submission.",
        )
    if runtime_engine.name != "docker":
        limitations.append("Local runtime was selected, so hidden-label isolation is not OS-enforced.")
    isolation_verified = bool(project_results) and all(
        item.integrity.isolation_verified for item in project_results
    )
    loops_finished_cleanly = len(loop_states) == len(project_results) and all(
        state.status in {"completed", "stopped"}
        and bool(state.stop_reason)
        and not state.errors
        for state in loop_states
    )
    seed_reports_clean = all(not item.errors for item in project_results)
    report = BenchmarkReport(
        benchmark_id=benchmark_id,
        task_id=spec.task_id,
        strategy=strategy,
        model=model,
        runtime=runtime_engine.name,
        controller="run-loop",
        requested_iterations=requested_iterations,
        seeds=project_results,
        integrity_pass_rate=statistics.fmean(
            1.0 if item.integrity.passed else 0.0 for item in project_results
        ),
        success_at_n=statistics.fmean(
            1.0 if item.target_reached else 0.0 for item in project_results
        ),
        mean_normalized_gain=statistics.fmean(gains),
        median_normalized_gain=statistics.median(gains),
        mean_valid_submission_rate=statistics.fmean(
            item.valid_submission_rate for item in project_results
        ),
        mean_anytime_auc=statistics.fmean(item.anytime_auc for item in project_results),
        total_wall_time_seconds=sum(item.wall_time_seconds for item in project_results),
        publishable=isolation_verified
        and all(item.integrity.passed for item in project_results)
        and loops_finished_cleanly
        and seed_reports_clean,
        limitations=limitations,
    )
    write_json_atomic(output / "report.json", report)
    write_json_atomic(
        output / "loop_summary.json",
        {
            "schema_version": 1,
            "benchmark_id": benchmark_id,
            "config": config.model_dump(mode="json"),
            "loops": [item.model_dump(mode="json") for item in loop_states],
        },
    )
    _render_report(report, output)
    return output, report, loop_states
