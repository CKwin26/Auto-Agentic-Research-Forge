from __future__ import annotations

import json
import subprocess
from pathlib import Path

from research_forge.benchmark import _grid_proposal, audit_project, load_task, materialize_task
from research_forge.runner import execute_run
from research_forge.runtime import DockerRuntime, RuntimeOptions
from research_forge.storage import read_json


def test_docker_runtime_enforces_two_container_boundary_without_live_daemon(
    tmp_path: Path, monkeypatch
) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    runtime = DockerRuntime(RuntimeOptions(kind="docker"), preflight=False)
    runtime.image_id = "sha256:test-image"
    runtime.server_version = "test-daemon"

    def fake_invoke(command, *, name, environment, timeout):
        del environment, timeout
        run_dir = next((project / "runs").glob("baseline-*"))
        if name.startswith("rf-candidate-"):
            trial = next(
                path
                for path in sorted(run_dir.glob("trial-*"))
                if (path / "candidate-output").is_dir()
                and not (path / "candidate-output" / "submission").exists()
            )
            parameters = read_json(run_dir / "parameters.json")
            (trial / "candidate-output" / "submission").write_text(
                json.dumps(parameters), encoding="utf-8"
            )
        else:
            trial = next(
                path
                for path in sorted(run_dir.glob("trial-*"))
                if (path / "submission").is_file()
                and not (path / "evaluator-output" / "metrics.json").exists()
            )
            submission = json.loads((trial / "submission").read_text(encoding="utf-8"))
            x = float(submission.get("x", 0.0))
            score = 1.0 - ((x - 3.0) / 3.0) ** 2
            (trial / "evaluator-output" / "metrics.json").write_text(
                json.dumps({"score": score}), encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(runtime, "_invoke", fake_invoke)
    baseline = execute_run(project, runtime=runtime)
    assert baseline.valid
    assert baseline.runtime == "docker"
    assert baseline.isolation_verified
    assert baseline.aggregate_metrics["score"] == 0.0

    command = read_json(project / "runs" / baseline.run_id / "trial-001" / "command.json")
    candidate_text = "\n".join(command["experiment_argv"])
    evaluator_text = "\n".join(command["evaluator_argv"])
    assert "--network\nnone" in candidate_text
    assert "--read-only" in candidate_text
    assert str(project / "evaluator") not in candidate_text
    assert "/workspace/evaluator" not in candidate_text
    assert "/workspace/input/submission" in evaluator_text
    assert ",readonly" in evaluator_text
    audit = audit_project(project)
    assert audit.passed
    assert audit.isolation_verified


def test_docker_audit_accepts_isolated_candidate_failure_before_evaluator(
    tmp_path: Path, monkeypatch
) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    runtime = DockerRuntime(RuntimeOptions(kind="docker"), preflight=False)
    runtime.image_id = "sha256:test-image"
    runtime.server_version = "test-daemon"

    def valid_baseline(command, *, name, environment, timeout):
        del environment, timeout
        run_dir = next((project / "runs").glob("baseline-*"))
        if name.startswith("rf-candidate-"):
            trial = next(
                path
                for path in sorted(run_dir.glob("trial-*"))
                if not (path / "candidate-output" / "submission").exists()
            )
            parameters = read_json(run_dir / "parameters.json")
            (trial / "candidate-output" / "submission").write_text(
                json.dumps(parameters), encoding="utf-8"
            )
        else:
            trial = next(
                path
                for path in sorted(run_dir.glob("trial-*"))
                if (path / "submission").is_file()
                and not (path / "evaluator-output" / "metrics.json").exists()
            )
            (trial / "evaluator-output" / "metrics.json").write_text(
                json.dumps({"score": 0.0}), encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(runtime, "_invoke", valid_baseline)
    assert execute_run(project, runtime=runtime).valid
    proposal = _grid_proposal(project, spec, {"x": 1.0}, 1)

    def fail_candidate(command, *, name, environment, timeout):
        del name, environment, timeout
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="import failed")

    monkeypatch.setattr(runtime, "_invoke", fail_candidate)
    failed = execute_run(project, envelope=proposal, runtime=runtime)
    assert not failed.valid
    assert all(trial.evaluator_exit_code is None for trial in failed.trials)
    audit = audit_project(project)
    assert audit.passed
    assert audit.isolation_verified


def test_docker_candidate_command_rejects_evaluator_placeholder(tmp_path: Path) -> None:
    task_dir, spec = load_task("rf-quadratic-max")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    contract = read_json(project / "execution_contract.json")
    contract["command"].append("{evaluator_dir}/labels.json")
    # This direct runtime-level test avoids mutating the frozen project contract.
    from research_forge.models import ExecutionContract

    runtime = DockerRuntime(RuntimeOptions(kind="docker"), preflight=False)
    runtime.image_id = "sha256:test-image"
    run_dir = tmp_path / "trial-fixture"
    run_dir.mkdir()
    params = run_dir / "params.json"
    params.write_text("{}", encoding="utf-8")
    try:
        runtime.run_trial(
            contract=ExecutionContract.model_validate(contract),
            project=project,
            experiment_dir=project / "experiment",
            evaluator_dir=project / "evaluator",
            trial_dir=run_dir,
            params_file=params,
            environment={},
        )
    except ValueError as exc:
        assert "cannot reference evaluator_dir" in str(exc)
    else:
        raise AssertionError("Docker candidate unexpectedly received evaluator access")


def test_controlled_runtime_attestation_carries_capability_evidence() -> None:
    runtime = DockerRuntime(RuntimeOptions(kind="docker", image="rf-airs-cpu:v1"), preflight=False)
    runtime.image_id = "sha256:controlled-image"
    runtime.server_version = "test-daemon"
    runtime.capability_manifest = {
        "controlled": True,
        "verified": True,
        "environment": "rf-airs-cpu",
        "version": "v1",
        "packages": {"scikit-learn": "1.9.0"},
        "capabilities": ["scikit-learn-text-classification"],
    }
    runtime.capability_manifest_sha256 = "abc123"

    attestation = runtime.attestation(evaluator_separated=True)
    assert attestation["controlled_environment"] is True
    assert attestation["capability_verified"] is True
    assert attestation["capability_manifest_sha256"] == "abc123"
    assert attestation["capabilities"]["packages"]["scikit-learn"] == "1.9.0"
