from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

from research_forge.benchmark import audit_project, load_task, materialize_task
from research_forge.contracts import transition, validate_proposal
from research_forge.models import (
    ExecutionContract,
    ExperimentProposal,
    FileReplacement,
    ParameterOverride,
    ProposalEnvelope,
    RunRecord,
    Stage,
)
from research_forge.runner import execute_run
from research_forge.runtime import DEFAULT_CONTROLLED_CPU_IMAGE, DockerRuntime, RuntimeOptions
from research_forge.storage import (
    load_state,
    read_json,
    save_state,
    sha256_tree,
    write_json_atomic,
)


def _artifact_hash(experiment_dir: Path, parameters: dict[str, object]) -> str:
    digest = hashlib.sha256()
    digest.update(sha256_tree(experiment_dir).encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(parameters, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def _replay_proposal(source_project: Path, source_record: RunRecord) -> ExperimentProposal:
    experiment_dir = source_project / "experiment"
    replacements = [
        FileReplacement(
            path=path.relative_to(experiment_dir).as_posix(),
            reason=(
                "Replay the complete promoted seed-search artifact without asking the model to "
                "regenerate or reinterpret any code."
            ),
            content=path.read_text(encoding="utf-8"),
        )
        for path in sorted(experiment_dir.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    parameters = [
        ParameterOverride(
            name=name,
            value=value,
            reason="Replay the exact promoted parameter state from the source seed.",
        )
        for name, value in sorted(source_record.parameters.items())
    ]
    return ExperimentProposal(
        title="Replay the complete promoted candidate on an independent benchmark seed",
        hypothesis=(
            "The promoted candidate is implementation- and metric-reproducible when rebuilt from "
            "the frozen task baseline and evaluated with an independent seed."
        ),
        rationale=(
            "This is a deterministic replication, not a new search step: copy the complete canonical "
            "experiment and exact parameter state produced by the source-seed loop."
        ),
        expected_observation=(
            "The candidate executes successfully, retains the source code hash, and improves over "
            "the independently evaluated baseline."
        ),
        falsification_condition=(
            "The run is invalid, the code hash differs, the integrity audit fails, or the candidate "
            "does not improve over the independent baseline."
        ),
        success_criteria=[
            "All required repeats produce finite evaluator metrics.",
            "The replay code hash exactly matches the source best-run code hash.",
            "The independent project passes every integrity and isolation check.",
        ],
        file_replacements=replacements,
        parameters=parameters,
        estimated_minutes=10,
        risks=["A stochastic algorithm may produce seed-dependent metric variation."],
        tags=["replication", "independent-seed", "controlled-environment"],
    )


def _render_report(result: dict[str, object]) -> str:
    rows = []
    for item in result["seeds"]:
        rows.append(
            f"| {item['seed']} | {item['role']} | {item['baseline_score']:.10f} | "
            f"{item['candidate_score']:.10f} | {item['improvement']:+.10f} | "
            f"{item['within_run_stddev']:.10f} | {item['audit_passed']} |"
        )
    return "\n".join(
        [
            "# Independent-seed replication",
            "",
            f"- Task: `{result['task_id']}`",
            f"- Source best run: `{result['source_best_run_id']}`",
            f"- Source code hash: `{result['source_code_hash']}`",
            f"- Image: `{result['image']}`",
            f"- Across-seed candidate stddev: `{result['candidate_score_stddev_across_seeds']:.10f}`",
            f"- All audits passed: `{result['all_audits_passed']}`",
            f"- All code hashes match: `{result['all_code_hashes_match']}`",
            "",
            "| Seed | Role | Baseline | Candidate | Improvement | Within-run stddev | Audit |",
            "|---:|---|---:|---:|---:|---:|:---:|",
            *rows,
            "",
            "Seed 0 is the bounded Codex search result. Other seeds reconstruct the complete final "
            "candidate from the frozen task baseline; they do not call Codex or continue search.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a source project's complete best candidate on independent seeds."
    )
    parser.add_argument("task", help="Registered benchmark task ID or task-pack directory")
    parser.add_argument("source_project", type=Path)
    parser.add_argument("--seeds", default="1,2", help="Comma-separated independent seeds")
    parser.add_argument("--docker-image", default=DEFAULT_CONTROLLED_CPU_IMAGE)
    parser.add_argument("--output-root", type=Path, default=Path("benchmark_runs"))
    args = parser.parse_args()

    seeds = [int(item.strip()) for item in args.seeds.split(",") if item.strip()]
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("--seeds must contain unique integers")
    source_project = args.source_project.resolve()
    source_state = load_state(source_project)
    if not source_state.best_run_id or not source_state.baseline_run_id:
        raise ValueError("source project has no verified baseline and best run")
    source_record = RunRecord.model_validate(
        read_json(source_project / "runs" / source_state.best_run_id / "record.json")
    )
    source_baseline = RunRecord.model_validate(
        read_json(source_project / "runs" / source_state.baseline_run_id / "record.json")
    )
    source_hash = _artifact_hash(source_project / "experiment", source_record.parameters)
    if source_hash != source_record.code_hash:
        raise ValueError("source canonical experiment does not match its best run")
    source_audit = audit_project(source_project)
    if not source_audit.passed:
        raise ValueError("source project integrity audit failed")

    task_dir, spec = load_task(args.task)
    run_config = read_json(source_project / "benchmark" / "run_config.json")
    source_seed = int(run_config["seed"])
    if source_seed in seeds:
        raise ValueError("replication seeds must not include the source seed")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output_root
        / f"{spec.task_id[:24]}-replication-{stamp}-{uuid.uuid4().hex[:6]}"
    ).resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = DockerRuntime(RuntimeOptions(kind="docker", image=args.docker_image))
    proposal = _replay_proposal(source_project, source_record)
    seed_results: list[dict[str, object]] = [
        {
            "seed": source_seed,
            "role": "search",
            "project": str(source_project),
            "baseline_run_id": source_baseline.run_id,
            "candidate_run_id": source_record.run_id,
            "baseline_score": source_baseline.aggregate_metrics[spec.primary_metric],
            "candidate_score": source_record.aggregate_metrics[spec.primary_metric],
            "improvement": (
                source_record.aggregate_metrics[spec.primary_metric]
                - source_baseline.aggregate_metrics[spec.primary_metric]
            ),
            "within_run_stddev": source_record.metric_stddev[spec.primary_metric],
            "code_hash": source_record.code_hash,
            "valid": source_record.valid,
            "audit_passed": source_audit.passed,
            "isolation_verified": source_record.isolation_verified,
        }
    ]

    for seed in seeds:
        project = materialize_task(
            task_dir,
            spec,
            seed=seed,
            project_root=output / "projects",
            project_slug=f"seed-{seed}",
        )
        baseline = execute_run(project, runtime=runtime)
        contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
        validation_errors = validate_proposal(project, proposal, contract)
        envelope = ProposalEnvelope(
            proposal_id=f"proposal-replay-{source_record.run_id}-seed-{seed}",
            model="research-forge-deterministic-replay",
            proposal=proposal,
            validation_errors=validation_errors,
            valid=not validation_errors,
        )
        write_json_atomic(
            project / "proposals" / f"{envelope.proposal_id}.json",
            envelope.model_dump(mode="json"),
        )
        if validation_errors:
            raise ValueError(f"replay proposal validation failed: {validation_errors}")
        state = load_state(project)
        transition(
            project,
            state,
            Stage.EXPERIMENT_DESIGN,
            "independent_seed_replay_proposal",
            proposal_id=envelope.proposal_id,
        )
        state.latest_proposal_id = envelope.proposal_id
        save_state(project, state)
        candidate = execute_run(project, envelope=envelope, runtime=runtime)
        audit = audit_project(project)
        seed_results.append(
            {
                "seed": seed,
                "role": "replay",
                "project": str(project),
                "baseline_run_id": baseline.run_id,
                "candidate_run_id": candidate.run_id,
                "baseline_score": baseline.aggregate_metrics.get(spec.primary_metric),
                "candidate_score": candidate.aggregate_metrics.get(spec.primary_metric),
                "improvement": candidate.improvement,
                "within_run_stddev": candidate.metric_stddev.get(spec.primary_metric),
                "code_hash": candidate.code_hash,
                "valid": candidate.valid,
                "audit_passed": audit.passed,
                "isolation_verified": candidate.isolation_verified,
                "errors": audit.violations,
            }
        )

    scores = [float(item["candidate_score"]) for item in seed_results]
    result: dict[str, object] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task_id": spec.task_id,
        "metric": spec.primary_metric,
        "image": args.docker_image,
        "source_project": str(source_project),
        "source_best_run_id": source_record.run_id,
        "source_code_hash": source_record.code_hash,
        "seeds": seed_results,
        "mean_candidate_score": statistics.fmean(scores),
        "candidate_score_stddev_across_seeds": statistics.pstdev(scores),
        "all_valid": all(bool(item["valid"]) for item in seed_results),
        "all_audits_passed": all(bool(item["audit_passed"]) for item in seed_results),
        "all_isolation_verified": all(
            bool(item["isolation_verified"]) for item in seed_results
        ),
        "all_code_hashes_match": all(
            item["code_hash"] == source_record.code_hash for item in seed_results
        ),
        "official_airs_leaderboard_submission": False,
    }
    write_json_atomic(output / "replication_report.json", result)
    (output / "report.md").write_text(_render_report(result), encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
    return 0 if all(
        bool(result[name])
        for name in (
            "all_valid",
            "all_audits_passed",
            "all_isolation_verified",
            "all_code_hashes_match",
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
