from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from research_forge.benchmark import _grid_proposal, audit_project, load_task, materialize_task
from research_forge.benchmark_models import LoopConfig, PendingLoopCandidate, ResearchLoopState
from research_forge.contracts import verify_frozen_contracts
from research_forge.research_loop import (
    _candidate_target_history,
    _config_hash,
    _fill_candidate_pool,
    _information_score,
    _normalize_pending,
    _proposal_focus,
    _reject_duplicate,
    _visible_data_manifest,
    canonical_state_fingerprint,
    candidate_fingerprint,
    run_loop_benchmark,
    run_project_loop,
)
from research_forge.models import (
    ExperimentProposal,
    FileReplacement,
    ParameterOverride,
    ProposalEnvelope,
)
from research_forge.runner import _artifact_hash, execute_run, promote_run
from research_forge.runtime import build_runtime
from research_forge.storage import load_jsonl, load_state, read_json, save_state, write_json_atomic


def test_invalid_execution_prioritizes_a_code_repair_over_stale_parameter_work() -> None:
    common = {
        "hypothesis": "A concrete causal change may repair the observed invalid execution.",
        "rationale": "The controller must prioritize repairing execution before unrelated work.",
        "expected_observation": "The repaired candidate executes successfully.",
        "falsification_condition": "The candidate remains invalid.",
        "success_criteria": ["The candidate is valid."],
        "estimated_minutes": 10,
    }
    stale = ExperimentProposal(
        title="Try another old parameter value",
        parameters=[ParameterOverride(name="x", value=1, reason="Old parameter axis.")],
        **common,
    )
    repair = ExperimentProposal(
        title="Repair the failing experiment implementation",
        file_replacements=[
            FileReplacement(
                path="run_experiment.py",
                reason="Remove the concrete unsupported dependency.",
                content="print('repaired')\n",
            )
        ],
        **common,
    )
    explored = {"parameter:x", "file:run_experiment.py"}
    diagnosis = {"failure_mode": "invalid_execution"}
    assert _information_score(repair, explored, diagnosis) > _information_score(
        stale, explored, diagnosis
    )


def test_codex_focus_explains_duplicate_targets_semantically(tmp_path: Path) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    execute_run(project)
    first = _grid_proposal(project, spec, {"x": 0.0}, 1)
    duplicate = _grid_proposal(project, spec, {"x": 0.0}, 2)
    fingerprint = candidate_fingerprint(project, first.proposal)
    _reject_duplicate(project, duplicate, fingerprint)
    config = LoopConfig(
        strategy="codex",
        runtime="local",
        iterations=3,
        candidate_pool_size=2,
        proposal_attempts_per_iteration=4,
        patience=3,
        max_invalid_runs=3,
        runtime_options=build_runtime("local").options.public_config(),
    )
    state = ResearchLoopState(
        loop_id="loop-readable-history",
        task_id=spec.task_id,
        seed=0,
        config_hash=_config_hash(config),
        explored_axes=["parameter:x"],
        pending_candidates=[
            PendingLoopCandidate(
                proposal_id=first.proposal_id,
                fingerprint=fingerprint,
                information_score=1.0,
                generated_at_iteration=1,
            )
        ],
    )

    history = _candidate_target_history(project, state)
    assert len(history) == 1
    assert history[0]["parameters"] == {"x": 0.0}
    assert history[0]["attempt_count"] == 2
    assert set(history[0]["statuses"]) == {"pending", "rejected_duplicate"}

    focus = json.loads(
        _proposal_focus(
            task_id=spec.task_id,
            seed=0,
            iteration=1,
            config=config,
            diagnosis={"failure_mode": "continue_ablation"},
            explored_axes=state.explored_axes,
            candidate_history=history,
            parameter_guidance=spec.parameter_guidance,
            visible_data_manifest=_visible_data_manifest(project),
        )
    )
    assert focus["candidate_history"][0]["parameters"] == {"x": 0.0}
    assert focus["runtime_constraints"]["runtime"] == "local"
    assert "Do not assume" in focus["runtime_constraints"]["dependency_policy"]
    assert "At least one causal axis" in focus["novelty_requirement"]
    assert "forbidden_equivalent_candidate_fingerprint_prefixes" not in focus
    assert focus["controller_feature_flags"] == {
        "candidate_pool": True,
        "duplicate_detection": True,
        "failure_diagnosis": True,
    }
    assert focus["visible_data_manifest"]["complete"] is True
    assert focus["visible_data_manifest"]["files"] == []
    assert "do not assume" in focus["visible_data_manifest"]["policy"]


def test_visible_data_manifest_reports_only_materialized_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "train.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    (data / "test.jsonl").write_text('{"x": 2}\n', encoding="utf-8")

    manifest = _visible_data_manifest(tmp_path)

    assert manifest["complete"] is True
    assert {item["path"] for item in manifest["files"]} == {
        "data/test.jsonl",
        "data/train.jsonl",
    }
    assert "unlisted split" in manifest["policy"]


def test_codex_focus_exposes_only_verified_controlled_environment_packages() -> None:
    config = LoopConfig(
        strategy="codex",
        runtime="docker",
        iterations=1,
        candidate_pool_size=1,
        proposal_attempts_per_iteration=1,
        runtime_options={"kind": "docker", "image": "rf-airs-cpu:v1"},
        runtime_capabilities={
            "controlled": True,
            "verified": True,
            "environment": "rf-airs-cpu",
            "version": "v1",
            "packages": {"numpy": "2.5.1", "scikit-learn": "1.9.0"},
        },
    )
    focus = json.loads(
        _proposal_focus(
            task_id="example",
            seed=0,
            iteration=1,
            config=config,
            diagnosis={"failure_mode": "continue_ablation"},
            explored_axes=[],
            candidate_history=[],
            parameter_guidance={},
            visible_data_manifest={
                "root": "data",
                "files": [],
                "complete": True,
                "omitted_file_count": 0,
                "policy": "No data files are materialized.",
            },
        )
    )
    constraints = focus["runtime_constraints"]
    assert constraints["runtime_capabilities"]["packages"]["scikit-learn"] == "1.9.0"
    assert "capability manifest was verified" in constraints["dependency_policy"]
    assert "do not install" in constraints["dependency_policy"]


def test_run_loop_grid_selects_promotes_stops_and_persists(tmp_path: Path) -> None:
    output, report, states = asyncio.run(
        run_loop_benchmark(
            "rf-quadratic-max",
            strategy="grid",
            seeds=[0],
            iterations=3,
            output_root=tmp_path,
            runtime="local",
            candidate_pool_size=2,
            proposal_attempts_per_iteration=4,
            patience=3,
            output_id="short01",
            short_paths=True,
        )
    )
    state = states[0]
    assert state.status == "completed"
    assert state.stop_reason == "target_reached"
    assert state.completed_iterations == 3
    assert len(state.executed_fingerprints) == len(set(state.executed_fingerprints))
    assert state.best_score == 1.0
    assert report.controller == "run-loop"
    assert report.mean_normalized_gain == 1.0
    assert not report.publishable
    assert output.name == "short01"
    assert Path(report.seeds[0].project).name == "s0"
    assert (output / "loop_manifest.json").is_file()
    assert (output / "loop_summary.json").is_file()
    events = load_jsonl(Path(report.seeds[0].project) / "benchmark" / "loop_events.jsonl")
    assert any(item["event"] == "diagnosis_updated" for item in events)
    assert any(item["event"] == "candidate_selected" for item in events)
    assert any(item["event"] == "iteration_finished" for item in events)
    assert audit_project(Path(report.seeds[0].project)).passed


def test_state_safe_pool_fills_three_distinct_current_state_candidates(
    tmp_path: Path,
) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    execute_run(project)
    canonical = canonical_state_fingerprint(project)
    config = LoopConfig(
        strategy="grid",
        runtime="local",
        iterations=3,
        candidate_pool_size=3,
        proposal_attempts_per_iteration=6,
        runtime_options=build_runtime("local").options.public_config(),
    )
    state = ResearchLoopState(
        loop_id="loop-pool-three",
        task_id=spec.task_id,
        seed=0,
        config_hash=_config_hash(config),
        status="running",
        seen_fingerprints=[canonical],
    )

    asyncio.run(
        _fill_candidate_pool(
            project,
            spec=spec,
            seed=0,
            config=config,
            state=state,
        )
    )

    assert len(state.pending_candidates) == 3
    assert len({item.fingerprint for item in state.pending_candidates}) == 3
    assert {
        item.generated_from_fingerprint for item in state.pending_candidates
    } == {canonical}
    events = load_jsonl(project / "benchmark" / "loop_events.jsonl")
    ready = [item for item in events if item["event"] == "candidate_pool_ready"]
    assert ready[-1]["details"] == {
        "pool_size": 3,
        "target_pool_size": 3,
        "full": True,
        "canonical_state_fingerprint": canonical,
    }


def test_pending_pool_revalidates_invariant_target_and_drops_drifted_target(
    tmp_path: Path,
) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    execute_run(project)
    generated_from = canonical_state_fingerprint(project)

    invariant = _grid_proposal(project, spec, {"x": 1.0}, 1)
    invariant_fingerprint = candidate_fingerprint(project, invariant.proposal)
    source = (project / "experiment" / "run_experiment.py").read_text(encoding="utf-8")
    stale_proposal = ExperimentProposal(
        title="A file candidate tied to the old canonical parameters",
        hypothesis="A deterministic code edit may improve the metric.",
        rationale="Exercise state-aware pending-candidate invalidation.",
        expected_observation="The protected evaluator returns a finite score.",
        falsification_condition="The edit fails or does not improve.",
        success_criteria=["The candidate executes."],
        file_replacements=[
            FileReplacement(
                path="run_experiment.py",
                reason="Create a target that inherits the generation-state parameters.",
                content=source + "\n# state-safe-pool test candidate\n",
            )
        ],
        estimated_minutes=1,
    )
    stale = ProposalEnvelope(
        proposal_id="proposal-stale-file",
        model="test",
        proposal=stale_proposal,
        valid=True,
    )
    write_json_atomic(project / "proposals" / f"{stale.proposal_id}.json", stale)
    stale_fingerprint = candidate_fingerprint(project, stale.proposal)

    improving = _grid_proposal(project, spec, {"x": 3.0}, 2)
    record = execute_run(project, envelope=improving)
    assert record.verdict == "candidate_improves"
    promote_run(project, record.run_id, record.run_id)
    assert canonical_state_fingerprint(project) != generated_from

    config = LoopConfig(
        strategy="grid",
        runtime="local",
        iterations=3,
        candidate_pool_size=3,
        proposal_attempts_per_iteration=6,
        runtime_options=build_runtime("local").options.public_config(),
    )
    state = ResearchLoopState(
        loop_id="loop-state-revalidation",
        task_id=spec.task_id,
        seed=0,
        config_hash=_config_hash(config),
        status="running",
        seen_fingerprints=[
            generated_from,
            invariant_fingerprint,
            stale_fingerprint,
        ],
        pending_candidates=[
            PendingLoopCandidate(
                proposal_id=invariant.proposal_id,
                fingerprint=invariant_fingerprint,
                information_score=1.0,
                generated_at_iteration=1,
                generated_from_fingerprint=generated_from,
            ),
            PendingLoopCandidate(
                proposal_id=stale.proposal_id,
                fingerprint=stale_fingerprint,
                information_score=1.0,
                generated_at_iteration=1,
                generated_from_fingerprint=generated_from,
            ),
        ],
    )

    normalized = _normalize_pending(project, state, config)

    assert [item.proposal_id for item in normalized] == [invariant.proposal_id]
    assert normalized[0].generated_from_fingerprint == canonical_state_fingerprint(project)
    assert state.invalidated_proposal_ids == [stale.proposal_id]
    events = load_jsonl(project / "benchmark" / "loop_events.jsonl")
    assert any(
        item["event"] == "pending_candidate_revalidated_after_state_change"
        and item["details"]["proposal_id"] == invariant.proposal_id
        for item in events
    )
    assert any(
        item["event"] == "pending_candidate_invalidated_after_state_change"
        and item["details"]["proposal_id"] == stale.proposal_id
        for item in events
    )


def test_run_loop_rejects_equivalent_candidates_before_execution(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="rfdup-") as temporary:
        root = Path(temporary)
        source, _ = load_task("rf-quadratic-max")
        task = root / "rf-duplicate-max"
        shutil.copytree(source, task)
        spec = read_json(task / "task.json")
        spec["task_id"] = "rf-duplicate-max"
        spec["candidate_parameters"] = [{"x": 0.0}, {"x": 0.0}, {"x": 3.0}]
        spec["max_iterations"] = 3
        write_json_atomic(task / "task.json", spec)

        output, report, states = asyncio.run(
            run_loop_benchmark(
                task,
                strategy="grid",
                seeds=[0],
                iterations=3,
                output_root=root / "runs",
                runtime="local",
                candidate_pool_size=1,
                proposal_attempts_per_iteration=3,
            )
        )
        del output
        state = states[0]
        assert state.proposal_attempts == 3
        assert state.completed_iterations == 1
        assert state.stop_reason == "target_reached"
        project = Path(report.seeds[0].project)
        proposals = [read_json(path) for path in sorted((project / "proposals").glob("*.json"))]
        assert sum(not item["valid"] for item in proposals) == 2
        assert report.seeds[0].valid_submission_rate == 1 / 3
        events = load_jsonl(project / "benchmark" / "loop_events.jsonl")
        assert sum(item["event"] == "proposal_rejected_as_duplicate" for item in events) == 2


def test_no_duplicate_detection_executes_repeated_targets(tmp_path: Path) -> None:
    source, _ = load_task("rf-quadratic-max")
    task = tmp_path / "rf-repeat-max"
    shutil.copytree(source, task)
    spec = read_json(task / "task.json")
    spec["task_id"] = "rf-repeat-max"
    spec["candidate_parameters"] = [{"x": 1.0}, {"x": 1.0}]
    spec["max_iterations"] = 2
    write_json_atomic(task / "task.json", spec)

    output, report, states = asyncio.run(
        run_loop_benchmark(
            task,
            strategy="grid",
            seeds=[0],
            iterations=2,
            output_root=tmp_path / "runs",
            runtime="local",
            candidate_pool_size=1,
            proposal_attempts_per_iteration=1,
            deduplicate_candidates=False,
        )
    )
    del output
    state = states[0]
    assert state.completed_iterations == 2
    assert state.stop_reason == "iteration_budget_exhausted"
    assert len(state.executed_proposal_ids) == 2
    assert len(set(state.executed_proposal_ids)) == 2
    assert len(state.executed_fingerprints) == 2
    assert len(set(state.executed_fingerprints)) == 1
    assert report.seeds[0].candidate_runs == 2
    events = load_jsonl(Path(report.seeds[0].project) / "benchmark" / "loop_events.jsonl")
    assert not any(item["event"] == "proposal_rejected_as_duplicate" for item in events)
    assert audit_project(Path(report.seeds[0].project)).passed


def test_no_failure_diagnosis_records_disabled_control_condition(tmp_path: Path) -> None:
    output, report, states = asyncio.run(
        run_loop_benchmark(
            "rf-quadratic-max",
            strategy="grid",
            seeds=[0],
            iterations=1,
            output_root=tmp_path,
            runtime="local",
            candidate_pool_size=1,
            proposal_attempts_per_iteration=1,
            failure_diagnosis=False,
        )
    )
    del output
    state = states[0]
    assert state.last_diagnosis == {
        "primary_metric": "score",
        "failure_mode": "diagnosis_disabled",
    }
    events = load_jsonl(Path(report.seeds[0].project) / "benchmark" / "loop_events.jsonl")
    assert any(item["event"] == "diagnosis_disabled" for item in events)
    assert not any(item["event"] == "diagnosis_updated" for item in events)


def test_run_loop_recovers_interrupted_baseline_and_continues(tmp_path: Path) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    active_id = "baseline-interrupted"
    run_dir = project / "runs" / active_id
    workspace = run_dir / "workspace" / "experiment"
    shutil.copytree(project / "experiment", workspace)
    parameters = read_json(project / "current_parameters.json")
    write_json_atomic(run_dir / "parameters.json", parameters)
    write_json_atomic(
        run_dir / "manifest.json",
        {
            "run_id": active_id,
            "proposal_id": None,
            "is_baseline": True,
            "contract_hash": verify_frozen_contracts(project),
            "code_hash": _artifact_hash(workspace, parameters),
            "changed_files": [],
            "repeats": 1,
            "runtime": "local",
            "isolation_verified": False,
            "runtime_attestation": {},
        },
    )
    project_state = load_state(project)
    project_state.active_run_id = active_id
    save_state(project, project_state)
    config = LoopConfig(
        strategy="grid",
        runtime="local",
        iterations=1,
        candidate_pool_size=1,
        proposal_attempts_per_iteration=1,
        patience=1,
        max_invalid_runs=2,
        runtime_options=build_runtime("local").options.public_config(),
    )
    state = asyncio.run(
        run_project_loop(
            project,
            spec,
            seed=0,
            config=config,
            runtime=build_runtime("local"),
        )
    )
    assert state.completed_iterations == 1
    assert load_state(project).active_run_id is None
    events = load_jsonl(project / "benchmark" / "loop_events.jsonl")
    assert any(item["event"] == "interrupted_run_recovered" for item in events)
    assert audit_project(project).passed


def test_run_loop_reconciles_completed_run_after_controller_crash(tmp_path: Path) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    execute_run(project)
    envelope = _grid_proposal(project, spec, {"x": 3.0}, 1)
    fingerprint = candidate_fingerprint(project, envelope.proposal)
    record = execute_run(project, envelope=envelope)
    assert record.verdict == "candidate_improves"

    config = LoopConfig(
        strategy="grid",
        runtime="local",
        iterations=1,
        candidate_pool_size=1,
        proposal_attempts_per_iteration=1,
        patience=1,
        max_invalid_runs=1,
        runtime_options=build_runtime("local").options.public_config(),
    )
    loop_state = ResearchLoopState(
        loop_id="loop-reconcile",
        task_id=spec.task_id,
        seed=0,
        config_hash=_config_hash(config),
        status="error",
        proposal_attempts=1,
        seen_fingerprints=[candidate_fingerprint(project), fingerprint],
        active_proposal_id=envelope.proposal_id,
        active_fingerprint=fingerprint,
        active_information_score=1.0,
    )
    write_json_atomic(project / "benchmark" / "loop_state.json", loop_state)
    resumed = asyncio.run(
        run_project_loop(
            project,
            spec,
            seed=0,
            config=config,
            runtime=build_runtime("local"),
        )
    )
    assert resumed.completed_iterations == 1
    assert resumed.stop_reason == "target_reached"
    assert resumed.active_proposal_id is None
    assert load_state(project).best_run_id == record.run_id
    events = load_jsonl(project / "benchmark" / "loop_events.jsonl")
    assert any(item["event"] == "completed_run_reconciled" for item in events)
    assert audit_project(project).passed
