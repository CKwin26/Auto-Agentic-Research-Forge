"""Run an unrelated ML working-paper case through the unchanged Research Forge core.

This deliberately creates a *working-paper* case, not a publishability claim.
It must fail rather than overwrite an existing case directory.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from research_forge.benchmark import load_task, materialize_task
from research_forge.models import ExperimentProposal, FileReplacement, ParameterOverride, ProposalEnvelope, Stage
from research_forge.pipeline_contracts import initialize_pipeline_project, load_project_spec
from research_forge.runner import execute_run, promote_run
from research_forge.storage import load_state, save_state, write_json_atomic
from research_forge.synthesis import complete_project, synthesize_project, verify_completion_certificate


ROOT = Path(__file__).resolve().parents[1]


def run(output_root: Path) -> dict[str, object]:
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"second-case output already exists: {output_root}")
    task_dir, task = load_task(ROOT / "benchmarks" / "airs" / "textualclassificationsickaccuracy")
    project = materialize_task(task_dir, task, seed=0, project_root=output_root.parent, project_slug=output_root.name)
    spec_path = ROOT / "examples" / "project-specs" / "sick-lexical-replication.json"
    manifest_path = initialize_pipeline_project(project, load_project_spec(spec_path))
    baseline = execute_run(project)
    state = load_state(project)
    state.stage = Stage.EXPERIMENT_DESIGN
    save_state(project, state)
    candidate_source = (ROOT / "examples" / "second-case" / "sick_lexical_naive_bayes.py").read_text(encoding="utf-8")
    proposal = ExperimentProposal(
        title="Transparent lexical-overlap stratified baseline",
        hypothesis="Train-derived lexical-overlap and negation strata improve protected accuracy over the majority-label baseline.",
        rationale="The candidate learns only per-stratum label frequencies from the frozen training split and emits predictions for the protected test evaluator.",
        expected_observation="The protected accuracy exceeds the majority-label baseline.",
        falsification_condition="The candidate is invalid or its protected accuracy does not exceed baseline.",
        success_criteria=["The protected evaluator reports a valid Accuracy value above baseline."],
        file_replacements=[
            FileReplacement(
                path="run_experiment.py",
                reason="Replace the constant-label baseline with a transparent lexical-overlap classifier.",
                content=candidate_source,
            )
        ],
        parameters=[ParameterOverride(name="model", value="lexical-overlap-stratified", reason="Records the declared fixed candidate family.")],
        estimated_minutes=2,
        tags=["second-case", "transparent-baseline", "no-core-change"],
    )
    envelope = ProposalEnvelope(proposal_id="proposal-lexical-overlap", model="deterministic-case-adapter", proposal=proposal, valid=True)
    write_json_atomic(project / "proposals" / f"{envelope.proposal_id}.json", envelope)
    candidate = execute_run(project, envelope=envelope)
    if candidate.verdict == "candidate_improves":
        promotion = promote_run(project, candidate.run_id, candidate.run_id)
    else:
        promotion = None
    audit = synthesize_project(project)
    certificate = complete_project(project)
    verification = verify_completion_certificate(project)
    report = {
        "schema_version": 1,
        "case": "sick-lexical-replication",
        "project": str(project),
        "pipeline_manifest": str(manifest_path),
        "baseline": baseline.model_dump(mode="json"),
        "candidate": candidate.model_dump(mode="json"),
        "promotion": promotion.model_dump(mode="json") if promotion else None,
        "synthesis_audit": audit.model_dump(mode="json"),
        "completion_certificate": certificate.model_dump(mode="json"),
        "completion_verification": verification,
        "interpretation": "A second unrelated computational working-paper case. It validates portability, not external scientific novelty or publication readiness.",
    }
    write_json_atomic(project / "second_case_validation.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="New project directory for this validation case")
    args = parser.parse_args()
    import json

    print(json.dumps(run(Path(args.output)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
