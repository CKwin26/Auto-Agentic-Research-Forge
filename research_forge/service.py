from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from .contracts import freeze_contracts, transition, validate_proposal
from .models import (
    Direction,
    EventRecord,
    ExecutionContract,
    LiteratureManifest,
    LiteratureApprovalRecord,
    LiteratureSource,
    LiteratureSourceType,
    PlanEvidenceBinding,
    ProjectMeta,
    ProjectState,
    ProposalEnvelope,
    ResearchPlanDraft,
    Stage1Manifest,
    Stage,
    macro_stage_for,
    utc_now,
)
from .storage import (
    append_jsonl,
    load_jsonl,
    load_meta,
    load_state,
    project_dir,
    read_json,
    resolve_workspace_root,
    safe_relative,
    save_state,
    sha256_file,
    sha256_tree,
    slugify,
    write_json_atomic,
)


SAMPLE_EXPERIMENT = '''from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--metrics", required=True)
    args = parser.parse_args()

    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    # Replace this deterministic placeholder with the real training/evaluation call.
    score = float(params.get("score", 0.50))
    Path(args.metrics).write_text(
        json.dumps({"score": score}, indent=2) + "\\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
'''


def create_project(name: str, idea: str, *, slug: str | None = None, root: str | Path | None = None) -> Path:
    base = resolve_workspace_root(root)
    base.mkdir(parents=True, exist_ok=True)
    project_slug = slugify(slug or name)
    project = project_dir(project_slug, base, must_exist=False)
    if project.exists():
        raise FileExistsError(f"project already exists: {project}")
    project.mkdir()
    for directory in (
        "data",
        "experiment",
        "literature/sources",
        "literature/search_plans",
        "literature/discoveries",
        "plans",
        "proposals",
        "runs",
        "lineage",
        "synthesis",
    ):
        (project / directory).mkdir(parents=True)
    meta = ProjectMeta(slug=project_slug, name=name, idea=idea)
    state = ProjectState()
    write_json_atomic(project / "project.json", meta)
    write_json_atomic(project / "state.json", state)
    write_json_atomic(project / "current_parameters.json", {})
    (project / "experiment" / "run_experiment.py").write_text(
        SAMPLE_EXPERIMENT, encoding="utf-8", newline="\n"
    )
    append_jsonl(
        project / "events.jsonl",
        EventRecord(event="project_created", to_stage=Stage.SCOPING, details={"slug": project_slug}),
    )
    return project


def register_literature_source(
    project: Path,
    *,
    source_id: str,
    source_type: LiteratureSourceType,
    title: str,
    authors: list[str],
    year: int | None,
    locator: str,
    verification_method: str,
    notes: str = "",
    verified: bool = False,
) -> LiteratureSource:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("literature sources can only be registered before contracts are frozen")
    source = LiteratureSource(
        source_id=source_id,
        source_type=source_type,
        title=title,
        authors=authors,
        year=year,
        locator=locator,
        notes=notes,
        verified=verified,
        verification_method=verification_method,
    )
    path = project / "literature" / "sources" / f"{source.source_id}.json"
    if path.exists():
        raise FileExistsError(f"literature source already exists: {source.source_id}")
    write_json_atomic(path, source)
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="literature_source_registered",
            from_stage=state.stage,
            to_stage=state.stage,
            details={
                "source_id": source.source_id,
                "source_type": source.source_type.value,
                "verified": source.verified,
            },
        ),
    )
    return source


def list_literature_sources(project: Path) -> list[LiteratureSource]:
    source_dir = project / "literature" / "sources"
    if not source_dir.is_dir():
        return []
    return [
        LiteratureSource.model_validate(read_json(path))
        for path in sorted(source_dir.glob("*.json"))
    ]


def freeze_literature_manifest(
    project: Path, *, source_ids: list[str] | None = None
) -> LiteratureManifest:
    sources = list_literature_sources(project)
    if source_ids is not None:
        requested = set(source_ids)
        sources = [source for source in sources if source.source_id in requested]
        missing = sorted(requested - {source.source_id for source in sources})
        if missing:
            raise FileNotFoundError(
                "approved literature sources are missing: " + ", ".join(missing)
            )
    if not sources:
        raise ValueError("at least one registered literature or research source is required before freezing")
    unverified = [source.source_id for source in sources if not source.verified]
    if unverified:
        raise ValueError("all frozen sources must be explicitly verified: " + ", ".join(unverified))
    source_hashes = {
        source.source_id: sha256_file(project / "literature" / "sources" / f"{source.source_id}.json")
        for source in sources
    }
    manifest = LiteratureManifest(source_hashes=source_hashes)
    write_json_atomic(project / "literature_manifest.json", manifest)
    return manifest


def configure_project(
    project: Path,
    *,
    primary_metric: str,
    direction: Direction,
    entrypoint: str,
    timeout_seconds: int,
    max_runs: int,
    required_repeats: int,
    max_repeats: int,
    min_delta: float,
    required_metrics: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> ExecutionContract:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("execution contract can only be configured before freezing")
    entrypoint_path = safe_relative(project / "experiment", entrypoint.replace("\\", "/"))
    if entrypoint_path.suffix.lower() != ".py":
        raise ValueError("entrypoint must be a Python file inside experiment/")
    if not entrypoint_path.is_file():
        raise FileNotFoundError(f"entrypoint does not exist: {entrypoint_path}")
    command = [
        "{python}",
        "{experiment_dir}/" + entrypoint_path.relative_to(project / "experiment").as_posix(),
        "--params",
        "{params_file}",
        "--metrics",
        "{metrics_file}",
        *(extra_args or []),
    ]
    contract = ExecutionContract(
        configured_by_user=True,
        command=command,
        primary_metric=primary_metric,
        direction=direction,
        required_metrics=required_metrics or [],
        min_delta=min_delta,
        timeout_seconds=timeout_seconds,
        max_runs=max_runs,
        required_repeats=required_repeats,
        max_repeats=max_repeats,
    )
    write_json_atomic(project / "execution_contract.json", contract)
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="execution_contract_configured",
            from_stage=state.stage,
            to_stage=state.stage,
            details={"primary_metric": primary_metric, "direction": direction.value},
        ),
    )
    return contract


def _source_snapshot(project: Path, limit: int = 80_000) -> str:
    chunks: list[str] = []
    size = 0
    for path in sorted(p for p in (project / "experiment").rglob("*") if p.is_file()):
        if path.suffix.lower() not in {".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md"}:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        block = f"\n--- FILE {path.relative_to(project / 'experiment').as_posix()} ---\n{content}"
        if size + len(block) > limit:
            chunks.append("\n[remaining source omitted by deterministic context limit]")
            break
        chunks.append(block)
        size += len(block)
    return "".join(chunks)


async def plan_project(project: Path, message: str) -> tuple[str, ResearchPlanDraft]:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("planning is closed after contracts are frozen")
    from .literature import audit_stage1, included_source_ids, review_context

    stage1 = audit_stage1(project, require_plan=False)
    if not stage1.passed:
        raise ValueError(
            "Stage 1 literature gate must pass before research planning: "
            + "; ".join(stage1.violations)
        )
    literature_review = review_context(project) if stage1.mode == "scientific" else None
    approved_source_ids = (
        set(included_source_ids(project)) if stage1.mode == "scientific" else None
    )
    planning_sources = [
        source
        for source in list_literature_sources(project)
        if approved_source_ids is None or source.source_id in approved_source_ids
    ]
    meta = load_meta(project)
    prior_turns = load_jsonl(project / "planning_turns.jsonl")[-8:]
    latest = None
    if state.latest_plan_draft:
        latest = read_json(project / "plans" / f"{state.latest_plan_draft}.json")
    prompt = json.dumps(
        {
            "research_idea": meta.idea,
            "verified_research_sources": [
                source.model_dump(mode="json") for source in planning_sources
            ],
            "literature_review_and_novelty_map": literature_review,
            "user_message": message,
            "latest_draft": latest,
            "recent_turns": prior_turns,
            "instruction": "Return a concrete research contract draft. Ask only blocking questions.",
        },
        ensure_ascii=False,
        indent=2,
    )
    from .agent_runtime import generate_plan, model_name

    draft = await generate_plan(prompt, cwd=project)
    draft_id = f"plan-{utc_now().replace(':', '').replace('+00:00', 'Z')}-{uuid.uuid4().hex[:6]}"
    write_json_atomic(project / "plans" / f"{draft_id}.json", draft)
    if stage1.mode == "scientific":
        bind_plan_to_approved_evidence(project, draft_id)
    append_jsonl(
        project / "planning_turns.jsonl",
        {"recorded_at": utc_now(), "user": message, "draft_id": draft_id, "model": model_name()},
    )
    if state.stage == Stage.SCOPING:
        transition(project, state, Stage.PLAN_REVIEW, "first_plan_drafted", draft_id=draft_id)
    else:
        transition(project, state, Stage.PLAN_REVIEW, "plan_revised", draft_id=draft_id)
    state.latest_plan_draft = draft_id
    save_state(project, state)
    if stage1.mode == "scientific":
        audit_stage1(project, persist=True)
    return draft_id, draft


def bind_plan_to_approved_evidence(project: Path, draft_id: str) -> PlanEvidenceBinding:
    """Bind one existing plan draft to the currently approved scientific review."""
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("plan evidence binding is closed after contracts are frozen")
    from .literature import audit_stage1, included_source_ids, review_context

    literature_gate = audit_stage1(project, require_plan=False)
    if not literature_gate.passed or literature_gate.mode != "scientific":
        raise ValueError(
            "approved scientific literature gate must pass before plan binding: "
            + "; ".join(literature_gate.violations)
        )
    plan_path = project / "plans" / f"{draft_id}.json"
    ResearchPlanDraft.model_validate(read_json(plan_path))
    review = review_context(project)
    approval = LiteratureApprovalRecord.model_validate(
        read_json(project / "literature" / "approval.json")
    )
    binding = PlanEvidenceBinding(
        plan_id=draft_id,
        plan_hash=sha256_file(plan_path),
        review_id=str(review["review_id"]),
        review_hash=sha256_file(project / "literature" / "review.json"),
        selected_novelty_id=approval.selected_novelty_id,
        source_ids=sorted(included_source_ids(project)),
    )
    per_plan_path = project / "plans" / f"{draft_id}-evidence.json"
    root_binding_path = project / "plan_evidence_binding.json"
    write_json_atomic(per_plan_path, binding)
    write_json_atomic(root_binding_path, binding)

    manifest_path = project / "literature" / "stage1_manifest.json"
    manifest = Stage1Manifest.model_validate(read_json(manifest_path))
    if manifest.review_id != binding.review_id:
        raise ValueError("Stage 1 manifest is not bound to the approved review")
    for relative in list(manifest.hashes):
        if relative.startswith("plans/") or relative == "plan_evidence_binding.json":
            del manifest.hashes[relative]
    manifest.hashes[f"plans/{draft_id}.json"] = binding.plan_hash
    manifest.hashes[f"plans/{draft_id}-evidence.json"] = sha256_file(per_plan_path)
    manifest.hashes["plan_evidence_binding.json"] = sha256_file(root_binding_path)
    write_json_atomic(manifest_path, manifest)
    return binding


def freeze_project(project: Path) -> str:
    state = load_state(project)
    if state.stage != Stage.PLAN_REVIEW or not state.latest_plan_draft:
        raise ValueError("a reviewed plan draft is required before freezing")
    from .literature import audit_stage1

    stage1 = audit_stage1(project, persist=True)
    if not stage1.passed:
        raise ValueError("Stage 1 gate failed: " + "; ".join(stage1.violations))
    draft = ResearchPlanDraft.model_validate(
        read_json(project / "plans" / f"{state.latest_plan_draft}.json")
    )
    if not draft.ready_to_freeze or draft.clarifying_questions:
        raise ValueError("plan still has blocking questions; revise it before freezing")
    contract_path = project / "execution_contract.json"
    if not contract_path.is_file():
        raise ValueError("configure the deterministic execution contract before freezing")
    execution = ExecutionContract.model_validate(read_json(contract_path))
    if not execution.configured_by_user:
        raise ValueError("execution contract has not been confirmed by the user")
    planned_metrics = {metric.name: metric.direction for metric in draft.metrics}
    if execution.primary_metric not in planned_metrics:
        raise ValueError("execution primary metric is not declared in the research plan")
    if planned_metrics[execution.primary_metric] != execution.direction:
        raise ValueError("execution metric direction conflicts with the research plan")
    if stage1.mode == "scientific":
        binding = PlanEvidenceBinding.model_validate(read_json(project / "plan_evidence_binding.json"))
        if binding.plan_id != state.latest_plan_draft:
            raise ValueError("latest research plan is not bound to the Stage 1 literature review")
        plan_path = project / "plans" / f"{state.latest_plan_draft}.json"
        if binding.plan_hash != sha256_file(plan_path):
            raise ValueError("research plan changed after Stage 1 evidence binding")
        if binding.review_id != stage1.review_id:
            raise ValueError("research plan is bound to a stale literature review")
        if binding.review_hash != sha256_file(project / "literature" / "review.json"):
            raise ValueError("literature review changed after research planning")
        from .literature import included_source_ids

        current_source_ids = sorted(included_source_ids(project))
        if binding.source_ids != current_source_ids:
            raise ValueError("registered source set changed after research planning")
    freeze_literature_manifest(
        project,
        source_ids=(current_source_ids if stage1.mode == "scientific" else None),
    )
    write_json_atomic(project / "research_contract.json", draft)
    transition(project, state, Stage.CONTRACT_FROZEN, "research_contract_frozen")
    manifest = freeze_contracts(project)
    transition(project, state, Stage.BASELINE_PENDING, "baseline_gate_opened")
    save_state(project, state)
    return manifest.frozen_at


async def propose_experiment(project: Path, focus: str) -> ProposalEnvelope:
    state = load_state(project)
    if state.stage not in {Stage.BASELINE_VERIFIED, Stage.EXPERIMENT_DESIGN, Stage.RESULT_REVIEW}:
        raise ValueError("a verified baseline is required before experiment design")
    research_contract = read_json(project / "research_contract.json")
    execution_contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    evidence = load_jsonl(project / "evidence.jsonl")[-30:]
    prompt = json.dumps(
        {
            "focus": focus,
            "research_contract": research_contract,
            "execution_contract": execution_contract.model_dump(mode="json"),
            "evidence_ledger_tail": evidence,
            "canonical_parameters": read_json(project / "current_parameters.json"),
            "canonical_experiment_code": _source_snapshot(project),
            "instruction": "Propose exactly one minimal, falsifiable next experiment.",
        },
        ensure_ascii=False,
        indent=2,
    )
    from .agent_runtime import generate_proposal, model_name

    proposal = await generate_proposal(prompt, cwd=project)
    errors = validate_proposal(project, proposal, execution_contract)
    proposal_id = f"proposal-{utc_now().replace(':', '').replace('+00:00', 'Z')}-{uuid.uuid4().hex[:6]}"
    envelope = ProposalEnvelope(
        proposal_id=proposal_id,
        model=model_name(),
        proposal=proposal,
        validation_errors=errors,
        valid=not errors,
    )
    write_json_atomic(project / "proposals" / f"{proposal_id}.json", envelope)
    if state.stage != Stage.EXPERIMENT_DESIGN:
        transition(project, state, Stage.EXPERIMENT_DESIGN, "experiment_proposed", proposal_id=proposal_id)
    else:
        transition(project, state, Stage.EXPERIMENT_DESIGN, "experiment_reproposed", proposal_id=proposal_id)
    state.latest_proposal_id = proposal_id
    save_state(project, state)
    return envelope


def load_proposal(project: Path, proposal_id: str) -> ProposalEnvelope:
    state = load_state(project)
    resolved = state.latest_proposal_id if proposal_id == "latest" else proposal_id
    if not resolved:
        raise ValueError("no experiment proposal exists")
    return ProposalEnvelope.model_validate(read_json(project / "proposals" / f"{resolved}.json"))


def render_report(project: Path) -> Path:
    meta = load_meta(project)
    state = load_state(project)
    evidence = load_jsonl(project / "evidence.jsonl")
    research = read_json(project / "research_contract.json") if (project / "research_contract.json").exists() else {}
    execution = read_json(project / "execution_contract.json") if (project / "execution_contract.json").exists() else {}
    primary = execution.get("primary_metric", "unknown")
    lines = [
        f"# {meta.name}",
        "",
        f"- Stage: `{state.stage.value}`",
        f"- Research question: {research.get('research_question', meta.idea)}",
        f"- Hypothesis: {research.get('hypothesis', 'not frozen')}",
        f"- Primary metric: `{primary}` ({execution.get('direction', 'unknown')})",
        f"- Baseline run: `{state.baseline_run_id or 'none'}`",
        f"- Best promoted run: `{state.best_run_id or 'none'}`",
        "",
        "## Evidence ledger",
        "",
        "| Run | Valid | Verdict | Primary value | Improvement | Error |",
        "|---|---:|---|---:|---:|---|",
    ]
    for item in evidence:
        value = item.get("primary_value")
        improvement = item.get("improvement")
        error = str(item.get("error") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{item.get('run_id')}` | {item.get('valid')} | {item.get('verdict')} | "
            f"{value if value is not None else ''} | {improvement if improvement is not None else ''} | {error} |"
        )
    if not evidence:
        lines.append("| — | — | No runs recorded | — | — | — |")
    lines.extend(
        [
            "",
            "## Reproducibility notes",
            "",
            "This report is rendered from the append-only evidence ledger. Invalid and negative runs are retained.",
            "Frozen research and execution contracts are hash-checked before and after every run.",
            "",
        ]
    )
    output = project / "report.md"
    output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return output


def project_status(project: Path) -> dict[str, object]:
    meta = load_meta(project)
    state = load_state(project)
    evidence = load_jsonl(project / "evidence.jsonl")
    from .synthesis import verify_completion_certificate

    completion = verify_completion_certificate(project)
    from .literature import audit_stage1

    stage1 = audit_stage1(project)
    return {
        "project": meta.model_dump(mode="json"),
        "state": state.model_dump(mode="json"),
        "macro_stage": macro_stage_for(state.stage).value,
        "four_stage_gates": {
            "stage_1_discovery": {
                "passed": stage1.passed,
                "registered_sources": len(list_literature_sources(project)),
                "candidate_count": stage1.candidate_count,
                "provider_count": stage1.provider_count,
                "review_id": stage1.review_id,
                "violations": stage1.violations,
            },
            "stage_2_protocol": {
                "passed": bool(state.baseline_run_id),
                "baseline_run_id": state.baseline_run_id,
            },
            "stage_3_experimentation": {
                "passed": bool(state.best_run_id and state.best_run_id != state.baseline_run_id),
                "best_run_id": state.best_run_id,
            },
            "stage_4_synthesis": {
                "passed": completion["passed"],
                "audit_present": (project / "synthesis" / "audit.json").is_file(),
                "publication_ready": completion["publication_ready"],
                "violations": completion["violations"],
            },
        },
        "evidence_count": len(evidence),
        "valid_runs": sum(1 for item in evidence if item.get("valid")),
        "invalid_runs": sum(1 for item in evidence if not item.get("valid")),
        "canonical_source_hash": sha256_tree(project / "experiment"),
        "canonical_parameters": read_json(project / "current_parameters.json"),
    }


def key_is_present() -> bool:
    if os.getenv("OPENAI_API_KEY", "").strip():
        return True
    env_path = Path(__file__).resolve().parents[1] / ".env.local"
    if not env_path.is_file():
        return False
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith("OPENAI_API_KEY=") and stripped.partition("=")[2].strip():
            return True
    return False
