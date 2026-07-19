from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from research_forge.benchmark import audit_project, load_task, materialize_task
from research_forge.contracts import transition
from research_forge.models import ProposalEnvelope, Stage
from research_forge.runner import execute_run
from research_forge.runtime import DEFAULT_CONTROLLED_CPU_IMAGE, DockerRuntime, RuntimeOptions
from research_forge.storage import load_state, read_json, save_state, write_json_atomic


def _render_report(result: dict[str, object]) -> str:
    baseline = dict(result["baseline"])
    candidate = dict(result["candidate"])
    attestation = dict(candidate["runtime_attestation"])
    metric = next(iter(dict(candidate["aggregate_metrics"])))
    baseline_value = dict(baseline["aggregate_metrics"])[metric]
    candidate_value = dict(candidate["aggregate_metrics"])[metric]
    candidate_stddev = dict(candidate["metric_stddev"])[metric]
    integrity = dict(result["integrity"])
    return "\n".join(
        [
            "# Controlled environment validation",
            "",
            f"- Task: `{result['task_id']}`",
            f"- Environment: `{result['image']}`",
            f"- Image ID: `{attestation['image_id']}`",
            f"- Capability manifest: `{attestation['capability_manifest_sha256']}`",
            f"- Integrity audit: `{'pass' if integrity['passed'] else 'fail'}`",
            "",
            "| Run | Metric | Value | Repeats | Stddev | Verdict |",
            "|---|---|---:|---:|---:|---|",
            (
                f"| Baseline | {metric} | {baseline_value:.10f} | "
                f"{len(baseline['trials'])} | {dict(baseline['metric_stddev'])[metric]:.10f} | "
                f"{baseline['verdict']} |"
            ),
            (
                f"| Candidate | {metric} | {candidate_value:.10f} | "
                f"{len(candidate['trials'])} | {candidate_stddev:.10f} | "
                f"{candidate['verdict']} |"
            ),
            "",
            "The complete run records, capability evidence, and audit checks are in `validation.json`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a proposal in a fresh project using a controlled Docker environment."
    )
    parser.add_argument("task", help="Benchmark task ID or task-pack directory")
    parser.add_argument("proposal", type=Path, help="Validated proposal envelope JSON")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--docker-image", default=DEFAULT_CONTROLLED_CPU_IMAGE)
    parser.add_argument("--output-root", type=Path, default=Path("benchmark_runs"))
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output_root / f"controlled-env-validation-{stamp}-{uuid.uuid4().hex[:6]}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    task_dir, spec = load_task(args.task)
    project = materialize_task(
        task_dir,
        spec,
        seed=args.seed,
        project_root=output / "projects",
        project_slug=f"seed-{args.seed}",
    )
    runtime = DockerRuntime(RuntimeOptions(kind="docker", image=args.docker_image))
    baseline = execute_run(project, repeats=args.repeats, runtime=runtime)

    envelope = ProposalEnvelope.model_validate(read_json(args.proposal.resolve()))
    write_json_atomic(
        project / "proposals" / f"{envelope.proposal_id}.json",
        envelope.model_dump(mode="json"),
    )
    state = load_state(project)
    transition(
        project,
        state,
        Stage.EXPERIMENT_DESIGN,
        "controlled_environment_validation_proposal",
        proposal_id=envelope.proposal_id,
    )
    state.latest_proposal_id = envelope.proposal_id
    save_state(project, state)
    candidate = execute_run(
        project,
        envelope=envelope,
        repeats=args.repeats,
        runtime=runtime,
    )
    audit = audit_project(project)
    result = {
        "schema_version": 1,
        "output": str(output),
        "project": str(project),
        "task_id": spec.task_id,
        "seed": args.seed,
        "image": args.docker_image,
        "baseline": baseline.model_dump(mode="json"),
        "candidate": candidate.model_dump(mode="json"),
        "integrity": audit.model_dump(mode="json"),
        "controlled_environment_verified": (
            candidate.runtime_attestation.get("controlled_environment") is True
            and candidate.runtime_attestation.get("capability_verified") is True
        ),
    }
    write_json_atomic(output / "validation.json", result)
    (output / "report.md").write_text(_render_report(result), encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if baseline.valid and candidate.valid and audit.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
