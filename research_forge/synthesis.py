from __future__ import annotations

import math
from pathlib import Path

from .contracts import transition, verify_frozen_contracts
from .manuscript_depth import audit_manuscript_depth
from .models import (
    ClaimKind,
    CompletionCertificate,
    ExecutionContract,
    LiteratureSource,
    LiteratureSourceType,
    PaperClaim,
    ProjectMeta,
    ResearchPlanDraft,
    RunRecord,
    Stage,
    SynthesisAudit,
)
from .storage import (
    load_jsonl,
    load_meta,
    load_state,
    read_json,
    save_state,
    sha256_file,
    write_json_atomic,
)


_REQUIRED_SECTIONS = (
    "# ",
    "## Abstract",
    "## Introduction",
    "## Related Work and Registered Sources",
    "## Methods",
    "## Results",
    "## Limitations",
    "## Conclusion",
    "## References",
    "## Reproducibility",
)


def _sources(project: Path) -> list[LiteratureSource]:
    return [
        LiteratureSource.model_validate(read_json(path))
        for path in sorted((project / "literature" / "sources").glob("*.json"))
    ]


def _run(project: Path, run_id: str) -> RunRecord:
    return RunRecord.model_validate(read_json(project / "runs" / run_id / "record.json"))


def _build_claims(project: Path) -> list[PaperClaim]:
    state = load_state(project)
    if not state.baseline_run_id:
        raise ValueError("synthesis requires a verified baseline")
    if not state.best_run_id or state.best_run_id == state.baseline_run_id:
        raise ValueError("synthesis requires at least one promoted improving experiment")

    research = ResearchPlanDraft.model_validate(read_json(project / "research_contract.json"))
    execution = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    baseline = _run(project, state.baseline_run_id)
    best = _run(project, state.best_run_id)
    if not baseline.valid or not best.valid:
        raise ValueError("synthesis cannot cite invalid baseline or best-run records")

    claims: list[PaperClaim] = []
    for index, source in enumerate(_sources(project), start=1):
        statement = source.notes.strip() or (
            f"{source.title} is a registered {source.source_type.value} source used to frame this study."
        )
        claims.append(
            PaperClaim(
                claim_id=f"background-{index:03d}",
                kind=ClaimKind.BACKGROUND,
                statement=statement,
                source_ids=[source.source_id],
            )
        )
    claims.extend(
        [
            PaperClaim(
                claim_id="method-frozen-protocol",
                kind=ClaimKind.METHOD,
                statement=(
                    f"The frozen protocol evaluates {research.baseline_definition} using "
                    f"{execution.primary_metric} as the primary metric ({execution.direction.value})."
                ),
            ),
            PaperClaim(
                claim_id="result-baseline",
                kind=ClaimKind.RESULT,
                statement=(
                    f"The verified baseline produced {execution.primary_metric}="
                    f"{baseline.aggregate_metrics[execution.primary_metric]:.12g}."
                ),
                evidence_run_ids=[baseline.run_id],
                reported_metrics={
                    execution.primary_metric: baseline.aggregate_metrics[execution.primary_metric]
                },
            ),
            PaperClaim(
                claim_id="result-best-promoted",
                kind=ClaimKind.RESULT,
                statement=(
                    f"The promoted best run produced {execution.primary_metric}="
                    f"{best.aggregate_metrics[execution.primary_metric]:.12g}, with registered improvement "
                    f"{(best.improvement or 0.0):.12g} over its reference."
                ),
                evidence_run_ids=[best.run_id],
                reported_metrics={execution.primary_metric: best.aggregate_metrics[execution.primary_metric]},
                reported_improvement=best.improvement,
            ),
            PaperClaim(
                claim_id="limitation-evidence-scope",
                kind=ClaimKind.LIMITATION,
                statement=(
                    "This draft supports only claims linked to the frozen source registry or run ledger; "
                    "it does not infer scientific novelty from orchestration success."
                ),
            ),
        ]
    )
    return claims


def _render_manuscript(project: Path, claims: list[PaperClaim]) -> str:
    meta: ProjectMeta = load_meta(project)
    research = ResearchPlanDraft.model_validate(read_json(project / "research_contract.json"))
    execution = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    sources = _sources(project)
    result_claims = [claim for claim in claims if claim.kind == ClaimKind.RESULT]
    background_claims = [claim for claim in claims if claim.kind == ClaimKind.BACKGROUND]
    method_claim = next(claim for claim in claims if claim.kind == ClaimKind.METHOD)
    limitation_claim = next(claim for claim in claims if claim.kind == ClaimKind.LIMITATION)

    lines = [
        f"# {research.title or meta.name}",
        "",
        "## Abstract",
        "",
        f"We study the question: {research.research_question} The frozen hypothesis is: {research.hypothesis}",
        (
            f"A deterministic evidence ledger records the baseline and promoted result for "
            f"`{execution.primary_metric}`. All numerical statements below are bound to run records."
        ),
        "",
        "## Introduction",
        "",
        f"Research question: {research.research_question}",
        "",
    ]
    lines.extend(f"- {claim.statement} [{claim.source_ids[0]}]" for claim in background_claims)
    lines.extend(
        [
            "",
            "## Related Work and Registered Sources",
            "",
        ]
    )
    lines.extend(
        f"- [{source.source_id}] {source.title} ({source.source_type.value}; verified by: "
        f"{source.verification_method})"
        for source in sources
    )
    lines.extend(
        [
            "",
            "## Methods",
            "",
            method_claim.statement,
            "",
            "The frozen method outline was:",
            "",
        ]
    )
    lines.extend(f"1. {item}" for item in research.method_outline)
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Claim | Run | Metric evidence |",
            "|---|---|---|",
        ]
    )
    for claim in result_claims:
        metrics = ", ".join(f"{name}={value:.12g}" for name, value in claim.reported_metrics.items())
        lines.append(f"| {claim.statement} | `{claim.evidence_run_ids[0]}` | {metrics} |")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            limitation_claim.statement,
            "",
            "Additional frozen risks:",
            "",
        ]
    )
    lines.extend(f"- {risk}" for risk in research.risks)
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            (
                "The four-stage loop produced a frozen research specification, a verified baseline, "
                "an evidence-linked promoted experiment, and this audited manuscript draft."
            ),
            "",
            "## References",
            "",
        ]
    )
    for source in sources:
        author_text = ", ".join(source.authors)
        year_text = str(source.year) if source.year is not None else "n.d."
        lines.append(
            f"- [{source.source_id}] {author_text} ({year_text}). *{source.title}*. {source.locator}"
        )
    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            f"- Frozen contract digest: `{verify_frozen_contracts(project)}`",
            f"- Baseline run: `{load_state(project).baseline_run_id}`",
            f"- Promoted best run: `{load_state(project).best_run_id}`",
            "- Claim registry: `synthesis/claims.json`",
            "- Synthesis audit: `synthesis/audit.json`",
            "",
        ]
    )
    return "\n".join(lines)


def audit_synthesis(project: Path) -> SynthesisAudit:
    state = load_state(project)
    violations: list[str] = []
    publication_blockers: list[str] = []
    checks: dict[str, bool] = {}

    checks["stage_allows_synthesis"] = state.stage in {
        Stage.SYNTHESIS,
        Stage.COMPLETED,
    }
    checks["no_active_run"] = state.active_run_id is None
    try:
        verify_frozen_contracts(project)
        checks["frozen_contracts_intact"] = True
    except Exception as exc:
        checks["frozen_contracts_intact"] = False
        violations.append(f"frozen contracts failed verification: {exc}")

    sources = _sources(project)
    source_map = {source.source_id: source for source in sources}
    checks["verified_source_registry"] = bool(sources) and all(source.verified for source in sources)

    claims_path = project / "synthesis" / "claims.json"
    manuscript_path = project / "synthesis" / "manuscript.md"
    claims: list[PaperClaim] = []
    try:
        payload = read_json(claims_path)
        claims = [PaperClaim.model_validate(item) for item in payload.get("claims", [])]
        checks["claim_registry_valid"] = bool(claims) and len({claim.claim_id for claim in claims}) == len(claims)
    except Exception as exc:
        checks["claim_registry_valid"] = False
        violations.append(f"claim registry is invalid: {exc}")

    try:
        expected_claims = _build_claims(project)
        checks["claims_match_frozen_evidence"] = [
            claim.model_dump(mode="json") for claim in claims
        ] == [claim.model_dump(mode="json") for claim in expected_claims]
    except Exception as exc:
        checks["claims_match_frozen_evidence"] = False
        violations.append(f"could not reconstruct claims from frozen evidence: {exc}")

    result_run_ids: list[str] = []
    source_ids: list[str] = []
    claims_supported = bool(claims)
    for claim in claims:
        if claim.kind == ClaimKind.BACKGROUND:
            for source_id in claim.source_ids:
                source_ids.append(source_id)
                source = source_map.get(source_id)
                if source is None or not source.verified:
                    claims_supported = False
                    violations.append(f"background claim {claim.claim_id} has unverified source {source_id}")
        if claim.kind == ClaimKind.RESULT:
            if len(claim.evidence_run_ids) != 1:
                claims_supported = False
                violations.append(f"result claim {claim.claim_id} must cite exactly one run")
                continue
            run_id = claim.evidence_run_ids[0]
            result_run_ids.append(run_id)
            try:
                run = _run(project, run_id)
            except Exception as exc:
                claims_supported = False
                violations.append(f"result claim {claim.claim_id} cannot load run {run_id}: {exc}")
                continue
            evidence = next((item for item in load_jsonl(project / "evidence.jsonl") if item.get("run_id") == run_id), None)
            if not run.valid or evidence is None or not evidence.get("valid"):
                claims_supported = False
                violations.append(f"result claim {claim.claim_id} cites invalid or absent evidence {run_id}")
            for metric, reported in claim.reported_metrics.items():
                actual = run.aggregate_metrics.get(metric)
                if actual is None or not math.isclose(actual, reported, rel_tol=1e-12, abs_tol=1e-12):
                    claims_supported = False
                    violations.append(f"result claim {claim.claim_id} misreports {metric}")
            if claim.reported_improvement is not None and (
                run.improvement is None
                or not math.isclose(run.improvement, claim.reported_improvement, rel_tol=1e-12, abs_tol=1e-12)
            ):
                claims_supported = False
                violations.append(f"result claim {claim.claim_id} misreports improvement")
            if not run.isolation_verified:
                publication_blockers.append(f"run {run_id} was not isolation-verified")
    checks["all_claims_supported"] = claims_supported

    promoted_ids = {str(item.get("run_id")) for item in load_jsonl(project / "lineage.jsonl")}
    checks["experiment_stage_completed"] = bool(
        state.baseline_run_id
        and state.best_run_id
        and state.best_run_id != state.baseline_run_id
        and state.best_run_id in promoted_ids
    )

    manuscript = manuscript_path.read_text(encoding="utf-8") if manuscript_path.is_file() else ""
    checks["manuscript_sections_complete"] = all(section in manuscript for section in _REQUIRED_SECTIONS)
    checks["manuscript_present"] = bool(manuscript.strip())
    try:
        checks["manuscript_matches_claim_registry"] = manuscript == _render_manuscript(project, claims)
    except Exception as exc:
        checks["manuscript_matches_claim_registry"] = False
        violations.append(f"could not reconstruct manuscript from claim registry: {exc}")

    try:
        depth = audit_manuscript_depth(
            manuscript_path,
            profile="journal-article",
            report_path=project / "synthesis" / "manuscript_depth.json",
        )
        checks["manuscript_depth_audit_completed"] = True
        if not depth.passed:
            publication_blockers.append(
                "journal-article manuscript depth gate failed: "
                + "; ".join(depth.violations)
            )
    except Exception as exc:
        checks["manuscript_depth_audit_completed"] = False
        violations.append(f"manuscript depth audit failed to run: {exc}")

    if not any(source.source_type == LiteratureSourceType.PAPER for source in sources):
        publication_blockers.append("no verified paper source is registered")
    if (project / "benchmark").is_dir():
        publication_blockers.append("RF-Bench calibration does not establish scientific novelty")

    for name, passed in checks.items():
        if not passed and not any(name in item for item in violations):
            violations.append(f"failed synthesis check: {name}")
    audit = SynthesisAudit(
        passed=all(checks.values()),
        publication_ready=all(checks.values()) and not publication_blockers,
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        publication_blockers=list(dict.fromkeys(publication_blockers)),
        claim_count=len(claims),
        evidence_run_ids=list(dict.fromkeys(result_run_ids)),
        source_ids=list(dict.fromkeys(source_ids)),
        manuscript_hash=sha256_file(manuscript_path) if manuscript_path.is_file() else None,
        claims_hash=sha256_file(claims_path) if claims_path.is_file() else None,
    )
    if state.stage != Stage.COMPLETED:
        write_json_atomic(project / "synthesis" / "audit.json", audit)
    return audit


def verify_completion_certificate(project: Path) -> dict[str, object]:
    state = load_state(project)
    violations: list[str] = []
    certificate_path = project / "completion_certificate.json"
    try:
        certificate = CompletionCertificate.model_validate(read_json(certificate_path))
    except Exception as exc:
        return {"passed": False, "publication_ready": False, "violations": [str(exc)]}
    if state.stage != Stage.COMPLETED:
        violations.append("project state is not completed")
    for relative, expected in certificate.artifact_hashes.items():
        path = project / relative
        if not path.is_file():
            violations.append(f"completed artifact is missing: {relative}")
        elif sha256_file(path) != expected:
            violations.append(f"completed artifact changed: {relative}")
    if certificate.artifact_hashes.get("synthesis/audit.json") != certificate.audit_hash:
        violations.append("certificate audit hash is inconsistent")
    return {
        "passed": not violations,
        "publication_ready": certificate.publication_ready and not violations,
        "violations": violations,
    }


def synthesize_project(project: Path) -> SynthesisAudit:
    state = load_state(project)
    if state.stage not in {
        Stage.BASELINE_VERIFIED,
        Stage.EXPERIMENT_DESIGN,
        Stage.RESULT_REVIEW,
        Stage.SYNTHESIS,
    }:
        raise ValueError("synthesis requires verified experiment evidence")
    if state.active_run_id:
        raise ValueError("cannot synthesize while a run is active")
    verify_frozen_contracts(project)
    claims = _build_claims(project)
    write_json_atomic(
        project / "synthesis" / "claims.json",
        {"schema_version": 1, "claims": [claim.model_dump(mode="json") for claim in claims]},
    )
    manuscript = _render_manuscript(project, claims)
    (project / "synthesis" / "manuscript.md").write_text(
        manuscript, encoding="utf-8", newline="\n"
    )
    if state.stage != Stage.SYNTHESIS:
        transition(project, state, Stage.SYNTHESIS, "manuscript_synthesized", claim_count=len(claims))
        save_state(project, state)
    return audit_synthesis(project)


def complete_project(project: Path) -> CompletionCertificate:
    state = load_state(project)
    if state.stage != Stage.SYNTHESIS:
        raise ValueError("project completion requires synthesis stage")
    audit = audit_synthesis(project)
    if not audit.passed:
        raise ValueError("synthesis audit failed: " + "; ".join(audit.violations))
    artifacts = {
        "frozen_manifest.json": sha256_file(project / "frozen_manifest.json"),
        "synthesis/claims.json": sha256_file(project / "synthesis" / "claims.json"),
        "synthesis/manuscript.md": sha256_file(project / "synthesis" / "manuscript.md"),
        "synthesis/manuscript_depth.json": sha256_file(
            project / "synthesis" / "manuscript_depth.json"
        ),
        "synthesis/audit.json": sha256_file(project / "synthesis" / "audit.json"),
    }
    certificate = CompletionCertificate(
        publication_ready=audit.publication_ready,
        audit_hash=artifacts["synthesis/audit.json"],
        artifact_hashes=artifacts,
    )
    write_json_atomic(project / "completion_certificate.json", certificate)
    transition(
        project,
        state,
        Stage.COMPLETED,
        "four_stage_loop_completed",
        publication_ready=audit.publication_ready,
    )
    save_state(project, state)
    return certificate
