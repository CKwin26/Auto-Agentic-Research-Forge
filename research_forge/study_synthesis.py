from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .contracts import transition
from .literature import audit_stage1
from .manual_audit import audit_manual_audit
from .manuscript_depth import audit_manuscript_depth
from .models import (
    ClaimKind,
    CompletionCertificate,
    FrozenManifest,
    LiteratureSource,
    Stage,
    StrictModel,
    SynthesisAudit,
    utc_now,
)
from .paper_pipeline import GENERIC_JOURNAL_ARTICLE, markdown_heading
from .storage import (
    load_state,
    read_json,
    safe_relative,
    save_state,
    sha256_file,
    write_json_atomic,
)
from .study import audit_stage2_protocol
from .study_models import Stage2Protocol
from .study_runner import audit_stage2_evaluation
from .synthesis import verify_completion_certificate


ANALYSIS_STATUS = "provisional_human_validation_deferred"
HUMAN_VALIDATION = "deferred"
SYNTHESIS_MODE = "provisional_stage2_paired_study"

_REQUIRED_SECTIONS = (
    "# ",
    "## Status",
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("abstract")),
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("introduction")),
    "## Related Work and Registered Sources",
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("methods")),
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("results")),
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("discussion")),
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("limitations")),
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("conclusion")),
    markdown_heading(GENERIC_JOURNAL_ARTICLE.section("references")),
    "## Reproducibility",
)

_REQUIRED_LATEX_MARKERS = (
    r"\documentclass",
    r"\begin{document}",
    r"\begin{abstract}",
    r"\section{Introduction}",
    r"\section{Related Work and Registered Sources}",
    r"\section{Methods}",
    r"\section{Results}",
    r"\section{Discussion}",
    r"\section{Limitations}",
    r"\section{Conclusion}",
    r"\begin{thebibliography}",
    r"\section{Reproducibility}",
    r"\end{document}",
)


class StudySynthesisClaim(StrictModel):
    schema_version: int = 1
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    kind: ClaimKind
    statement: str = Field(min_length=5, max_length=3000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_paths: list[str] = Field(min_length=1, max_length=100)
    evidence_sha256: dict[str, str] = Field(min_length=1, max_length=100)
    reported_metrics: dict[str, float] = Field(default_factory=dict, max_length=100)
    provisional: bool = False

    @model_validator(mode="after")
    def support_is_complete(self) -> "StudySynthesisClaim":
        if len(self.evidence_paths) != len(set(self.evidence_paths)):
            raise ValueError("claim evidence paths must be unique")
        if set(self.evidence_paths) != set(self.evidence_sha256):
            raise ValueError("claim evidence hashes must exactly cover evidence paths")
        if self.kind == ClaimKind.BACKGROUND and not self.source_ids:
            raise ValueError("background claims require registered source IDs")
        if self.kind == ClaimKind.RESULT and not self.reported_metrics:
            raise ValueError("result claims require structured metrics")
        if self.kind == ClaimKind.RESULT and not self.provisional:
            raise ValueError("automated Stage 2 result claims must remain provisional")
        if any(not math.isfinite(value) for value in self.reported_metrics.values()):
            raise ValueError("reported metrics must be finite")
        return self


def _stage2(project: Path) -> Path:
    return project / "stage2"


def _sources(project: Path) -> list[LiteratureSource]:
    return [
        LiteratureSource.model_validate(read_json(path))
        for path in sorted((project / "literature" / "sources").glob("*.json"))
    ]


def _relative(project: Path, path: Path) -> str:
    return path.resolve().relative_to(project.resolve()).as_posix()


def _active_deferral(project: Path, protocol_id: str) -> dict[str, Any]:
    path = _stage2(project) / "operator_decisions.json"
    payload = read_json(path)
    if payload.get("protocol_id") != protocol_id:
        raise ValueError("operator decision is not bound to the frozen protocol")
    matching = [
        item
        for item in payload.get("decisions", [])
        if item.get("decision") == "defer_preregistered_human_validation"
        and item.get("status") == "active"
        and item.get("protocol_effect") == "none"
    ]
    if len(matching) != 1:
        raise ValueError("exactly one active human-validation deferral is required")
    return dict(matching[0])


def _persona_panel_path(project: Path) -> Path | None:
    preferred = _stage2(project) / "persona_panel" / "panel-v1-full" / "final.json"
    if preferred.is_file():
        return preferred
    candidates = sorted((_stage2(project) / "persona_panel").glob("*/final.json"))
    return candidates[-1] if candidates else None


def _input_hashes(project: Path) -> dict[str, str]:
    stage2 = _stage2(project)
    required = [
        stage2 / "protocol.json",
        stage2 / "backbone_manifest.json",
        stage2 / "evaluations" / "summary.json",
        stage2 / "manual_audit_manifest.json",
        stage2 / "operator_decisions.json",
        project / "literature" / "stage1_manifest.json",
    ]
    panel = _persona_panel_path(project)
    if panel is not None:
        required.append(panel)
    return {_relative(project, path): sha256_file(path) for path in required}


def _verify_stage2_frozen_contracts(project: Path) -> str:
    """Verify the study-specific frozen manifest and return its content digest."""
    manifest_path = _stage2(project) / "frozen_manifest.json"
    manifest = FrozenManifest.model_validate(read_json(manifest_path))
    for relative, expected in manifest.hashes.items():
        path = safe_relative(project, relative)
        if not path.is_file():
            raise FileNotFoundError(f"frozen Stage 2 artifact is missing: {relative}")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(
                f"frozen Stage 2 artifact changed: {relative}: {actual} != {expected}"
            )
    return sha256_file(manifest_path)


def _expected_analysis(project: Path, *, created_at: str) -> dict[str, Any]:
    stage2 = _stage2(project)
    protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
    summary = read_json(stage2 / "evaluations" / "summary.json")
    decision = _active_deferral(project, protocol.protocol_id)
    panel_path = _persona_panel_path(project)
    panel_summary: dict[str, Any] | None = None
    if panel_path is not None:
        panel = read_json(panel_path)
        panel_summary = {
            "path": _relative(project, panel_path),
            "sha256": sha256_file(panel_path),
            "ensemble_label": panel.get("ensemble_label"),
            "item_count": panel.get("item_count"),
            "verdict_counts": panel.get("verdict_counts"),
            "disagreement_count": panel.get("disagreement_count"),
            "human_adjudication_queue_count": len(
                panel.get("human_adjudication_queue", [])
            ),
        }
    return {
        "schema_version": 1,
        "created_at": created_at,
        "protocol_id": protocol.protocol_id,
        "synthesis_mode": SYNTHESIS_MODE,
        "analysis_status": ANALYSIS_STATUS,
        "primary_analysis_interpretable": False,
        "human_validation": HUMAN_VALIDATION,
        "operator_decision_id": decision["decision_id"],
        "input_hashes": _input_hashes(project),
        "primary_metric": protocol.primary_metric,
        "metric_scope": "protected automated evaluator proxy",
        "arm_metrics": summary["arm_metrics"],
        "paired_analysis": summary["paired_analysis"],
        "scientist_persona_panel": panel_summary,
        "limitations": [
            "The preregistered two-human blinded audit has been deferred by the operator.",
            "Protected-evaluator effects are provisional and do not unlock the preregistered primary analysis.",
            "The scientist-persona panel is a same-model ensemble, not independent human or cross-model validation.",
            f"The experiment covers {len(protocol.tasks)} bounded task packs and does not establish universal generality.",
        ],
    }


def _claim(
    project: Path,
    *,
    claim_id: str,
    kind: ClaimKind,
    statement: str,
    evidence_paths: list[str],
    source_ids: list[str] | None = None,
    reported_metrics: dict[str, float] | None = None,
    provisional: bool = False,
) -> StudySynthesisClaim:
    hashes: dict[str, str] = {}
    for relative in evidence_paths:
        path = safe_relative(project, relative)
        if not path.is_file():
            raise FileNotFoundError(f"claim evidence is missing: {relative}")
        hashes[relative] = sha256_file(path)
    return StudySynthesisClaim(
        claim_id=claim_id,
        kind=kind,
        statement=statement,
        source_ids=source_ids or [],
        evidence_paths=evidence_paths,
        evidence_sha256=hashes,
        reported_metrics=reported_metrics or {},
        provisional=provisional,
    )


def _build_claims(project: Path, analysis: dict[str, Any]) -> list[StudySynthesisClaim]:
    protocol = Stage2Protocol.model_validate(read_json(_stage2(project) / "protocol.json"))
    sources = _sources(project)
    source_ids = [source.source_id for source in sources]
    source_paths = [f"literature/sources/{source_id}.json" for source_id in source_ids]
    baseline = analysis["arm_metrics"]["baseline"]
    treatment = analysis["arm_metrics"]["treatment"]
    paired = analysis["paired_analysis"]
    bootstrap = paired["hierarchical_bootstrap"]
    rate_delta = (
        float(treatment["unsupported_claim_rate"])
        - float(baseline["unsupported_claim_rate"])
    )
    runtime_delta = (
        float(treatment["total_wall_clock_seconds"])
        / float(baseline["total_wall_clock_seconds"])
        - 1.0
    )
    analysis_evidence = [
        "stage2/evaluations/summary.json",
        "stage2/provisional_analysis.json",
    ]
    claims = [
        _claim(
            project,
            claim_id="background-bounded-literature",
            kind=ClaimKind.BACKGROUND,
            statement=(
                f"The frozen Stage 1 review registered {len(sources)} verified sources spanning "
                "autonomous research agents, evidence verification, and research-agent reliability."
            ),
            evidence_paths=["literature/review.json", *source_paths],
            source_ids=source_ids,
        ),
        _claim(
            project,
            claim_id="method-frozen-paired-design",
            kind=ClaimKind.METHOD,
            statement=(
                f"The preregistered study used a frozen 2-arm by {len(protocol.tasks)}-task "
                f"by {len(protocol.seeds)}-seed paired design, for exactly {len(protocol.cells)} cells."
            ),
            evidence_paths=["stage2/protocol.json", "stage2/backbone_manifest.json"],
        ),
        _claim(
            project,
            claim_id="method-claim-evidence-gate",
            kind=ClaimKind.METHOD,
            statement=(
                "The treatment changed only the pre-delivery claim-evidence path by applying one "
                "reject-revise-recheck cycle; the baseline used the shared finalizer without verifier revision."
            ),
            evidence_paths=["stage2/protocol.json"],
        ),
        _claim(
            project,
            claim_id="result-unsupported-claim-rate",
            kind=ClaimKind.RESULT,
            statement=(
                "The protected automated evaluator estimated unsupported-claim rates of "
                f"{float(baseline['unsupported_claim_rate']):.4%} for baseline and "
                f"{float(treatment['unsupported_claim_rate']):.4%} for treatment, a treatment-minus-baseline "
                f"difference of {rate_delta * 100:.2f} percentage points."
            ),
            evidence_paths=analysis_evidence,
            reported_metrics={
                "baseline_unsupported_claim_rate": float(baseline["unsupported_claim_rate"]),
                "treatment_unsupported_claim_rate": float(treatment["unsupported_claim_rate"]),
                "treatment_minus_baseline": rate_delta,
            },
            provisional=True,
        ),
        _claim(
            project,
            claim_id="result-paired-effect",
            kind=ClaimKind.RESULT,
            statement=(
                "Across nine paired task-seed cells, the mean unsupported-claim-rate effect was "
                f"{float(paired['mean_paired_unsupported_claim_rate_effect']):.4f}; the preregistered "
                f"10,000-resample hierarchical bootstrap interval was [{float(bootstrap['ci95_low']):.4f}, "
                f"{float(bootstrap['ci95_high']):.4f}]."
            ),
            evidence_paths=analysis_evidence,
            reported_metrics={
                "mean_paired_effect": float(
                    paired["mean_paired_unsupported_claim_rate_effect"]
                ),
                "bootstrap_ci95_low": float(bootstrap["ci95_low"]),
                "bootstrap_ci95_high": float(bootstrap["ci95_high"]),
                "bootstrap_resamples": float(bootstrap["resamples"]),
                "paired_cohen_dz": float(paired["paired_cohen_dz"]),
            },
            provisional=True,
        ),
        _claim(
            project,
            claim_id="result-experiment-detail-error",
            kind=ClaimKind.RESULT,
            statement=(
                "The automated experiment-detail error rate was "
                f"{float(baseline['experiment_detail_error_rate']):.4%} for baseline and "
                f"{float(treatment['experiment_detail_error_rate']):.4%} for treatment."
            ),
            evidence_paths=analysis_evidence,
            reported_metrics={
                "baseline_experiment_detail_error_rate": float(
                    baseline["experiment_detail_error_rate"]
                ),
                "treatment_experiment_detail_error_rate": float(
                    treatment["experiment_detail_error_rate"]
                ),
            },
            provisional=True,
        ),
        _claim(
            project,
            claim_id="result-citation-correctness",
            kind=ClaimKind.RESULT,
            statement=(
                "Automated citation correctness was "
                f"{float(baseline['citation_correctness']):.4%} for baseline and "
                f"{float(treatment['citation_correctness']):.4%} for treatment."
            ),
            evidence_paths=analysis_evidence,
            reported_metrics={
                "baseline_citation_correctness": float(baseline["citation_correctness"]),
                "treatment_citation_correctness": float(treatment["citation_correctness"]),
            },
            provisional=True,
        ),
        _claim(
            project,
            claim_id="result-task-native-score",
            kind=ClaimKind.RESULT,
            statement=(
                "Mean task-native score was "
                f"{float(baseline['mean_task_native_score']):.5f} for baseline and "
                f"{float(treatment['mean_task_native_score']):.5f} for treatment in this bounded matrix."
            ),
            evidence_paths=analysis_evidence,
            reported_metrics={
                "baseline_mean_task_native_score": float(
                    baseline["mean_task_native_score"]
                ),
                "treatment_mean_task_native_score": float(
                    treatment["mean_task_native_score"]
                ),
            },
            provisional=True,
        ),
        _claim(
            project,
            claim_id="result-runtime-cost",
            kind=ClaimKind.RESULT,
            statement=(
                "Total measured wall-clock time was "
                f"{float(baseline['total_wall_clock_seconds']):.2f} seconds for baseline and "
                f"{float(treatment['total_wall_clock_seconds']):.2f} seconds for treatment, "
                f"a relative increase of {runtime_delta:.2%}."
            ),
            evidence_paths=analysis_evidence,
            reported_metrics={
                "baseline_wall_clock_seconds": float(
                    baseline["total_wall_clock_seconds"]
                ),
                "treatment_wall_clock_seconds": float(
                    treatment["total_wall_clock_seconds"]
                ),
                "relative_runtime_increase": runtime_delta,
            },
            provisional=True,
        ),
        _claim(
            project,
            claim_id="result-claim-retention",
            kind=ClaimKind.RESULT,
            statement=(
                "Both arms retained all 36 final claims in the protected evaluation, so the observed "
                "automated reliability difference was not produced by lower final claim count."
            ),
            evidence_paths=analysis_evidence,
            reported_metrics={
                "baseline_final_claims": float(baseline["final_claims"]),
                "treatment_final_claims": float(treatment["final_claims"]),
                "baseline_retention_rate": float(baseline["retention_rate"]),
                "treatment_retention_rate": float(treatment["retention_rate"]),
            },
            provisional=True,
        ),
        _claim(
            project,
            claim_id="limitation-human-validation-deferred",
            kind=ClaimKind.LIMITATION,
            statement=(
                "The preregistered two-human blinded claim audit is deferred; consequently the primary "
                "analysis remains uninterpretable under the frozen protocol and every effect reported here is provisional."
            ),
            evidence_paths=[
                "stage2/operator_decisions.json",
                "stage2/manual_audit_manifest.json",
                "stage2/provisional_analysis.json",
            ],
        ),
        _claim(
            project,
            claim_id="limitation-bounded-task-suite",
            kind=ClaimKind.LIMITATION,
            statement=(
                f"The evidence comes from {len(protocol.tasks)} bounded task packs, "
                f"{len(protocol.seeds)} seeds, one frozen Codex backbone, and one claim-gate "
                "implementation; it does not establish universal generality."
            ),
            evidence_paths=["stage2/protocol.json"],
        ),
    ]
    panel_info = analysis.get("scientist_persona_panel")
    if panel_info:
        counts = panel_info["verdict_counts"]
        claims.insert(
            -2,
            _claim(
                project,
                claim_id="result-same-model-persona-panel",
                kind=ClaimKind.RESULT,
                statement=(
                    "A supplementary same-model scientist-persona panel reviewed "
                    f"{int(panel_info['item_count'])} blinded claims and returned "
                    f"{int(counts.get('supported', 0))} supported, "
                    f"{int(counts.get('unsupported', 0))} unsupported, and "
                    f"{int(panel_info['disagreement_count'])} mixed-vote cases."
                ),
                evidence_paths=[str(panel_info["path"])],
                reported_metrics={
                    "panel_items": float(panel_info["item_count"]),
                    "panel_supported": float(counts.get("supported", 0)),
                    "panel_unsupported": float(counts.get("unsupported", 0)),
                    "panel_disagreements": float(panel_info["disagreement_count"]),
                },
                provisional=True,
            ),
        )
    expected_cells = 2 * len(protocol.tasks) * len(protocol.seeds)
    if len(protocol.cells) != expected_cells:
        raise ValueError("paired-study synthesis requires a complete frozen factorial design")
    return claims


def _render_manuscript(
    project: Path, analysis: dict[str, Any], claims: list[StudySynthesisClaim]
) -> str:
    protocol = Stage2Protocol.model_validate(read_json(_stage2(project) / "protocol.json"))
    sources = _sources(project)
    by_id = {claim.claim_id: claim for claim in claims}
    baseline = analysis["arm_metrics"]["baseline"]
    treatment = analysis["arm_metrics"]["treatment"]
    paired = analysis["paired_analysis"]
    bootstrap = paired["hierarchical_bootstrap"]
    lines = [
        f"# {protocol.title}",
        "",
        "## Status",
        "",
        "**Provisional automated-evaluation manuscript. Human validation is deferred. This artifact is pipeline-complete but not publication-ready.**",
        "",
        f"- Analysis status: `{analysis['analysis_status']}`",
        f"- Human validation: `{analysis['human_validation']}`",
        "- Preregistered primary analysis interpretable: `false`",
        "",
        "## Abstract",
        "",
        (
            "End-to-end autonomous research agents can execute experiments and draft scientific conclusions, "
            "but their final claims may overstate source support or experimental evidence. We evaluated a "
            "pre-delivery claim-evidence gate in a frozen same-backbone paired ablation. The study used two arms, "
            f"{len(protocol.tasks)} bounded task packs, {len(protocol.seeds)} seeds, "
            f"and exactly {len(protocol.cells)} cells. "
            + by_id["result-unsupported-claim-rate"].statement
            + " "
            + by_id["result-paired-effect"].statement
            + " "
            + by_id["result-runtime-cost"].statement
            + " Because the preregistered two-human audit is deferred, these effects remain provisional."
        ),
        "",
        "## Introduction",
        "",
        (
            "Autonomous research systems combine literature retrieval, experiment proposal, code execution, "
            "evaluation, and scientific writing. A central reliability problem is that a valid experiment run does "
            "not guarantee that every final prose claim is supported by the cited source or linked artifact."
        ),
        "",
        f"Research question: {protocol.research_question}",
        "",
        f"Frozen hypothesis: {protocol.hypothesis}",
        "",
        by_id["background-bounded-literature"].statement,
        "",
        "## Related Work and Registered Sources",
        "",
        (
            "The Stage 1 evidence boundary is a verified metadata/abstract review rather than a full-text systematic "
            "review. Sources below are the exact frozen registry used to frame the experiment."
        ),
        "",
    ]
    for source in sources:
        lines.append(
            f"- [{source.source_id}] {source.title} ({source.year or 'n.d.'}); "
            f"verified by {source.verification_method}"
        )
    lines.extend(
        [
            "",
            "## Methods",
            "",
            "### Study design",
            "",
            by_id["method-frozen-paired-design"].statement,
            "",
            by_id["method-claim-evidence-gate"].statement,
            "",
            "The three task packs were:",
            "",
        ]
    )
    for task in protocol.tasks:
        lines.append(
            f"- `{task.task_id}`: {task.primary_metric}, baseline {task.baseline_score:.6f}, "
            f"one candidate iteration, {task.timeout_seconds}-second task timeout."
        )
    lines.extend(
        [
            "",
            "### Evidence and evaluation",
            "",
            (
                "Every final output was represented as a structured claim registry. Experiment claims carried run "
                "IDs, exact metric values, and artifact paths; literature and novelty claims carried exact source IDs. "
                "All 18 registries were assigned random blind IDs before hybrid structural and Codex semantic evaluation."
            ),
            "",
            "### Analysis",
            "",
            (
                "The primary proxy metric was unsupported-claim rate. Treatment-minus-baseline effects were paired "
                "by task and seed. The frozen analysis used a 10,000-resample hierarchical bootstrap and paired "
                "Cohen's dz. These statistics are reported as automated-evaluator estimates, not as human-validated truth."
            ),
            "",
            "## Results",
            "",
            "### Arm-level automated-evaluator metrics",
            "",
            "| Metric | Baseline | Treatment |",
            "|---|---:|---:|",
            f"| Unsupported claim rate | {float(baseline['unsupported_claim_rate']):.2%} | {float(treatment['unsupported_claim_rate']):.2%} |",
            f"| Experiment-detail error rate | {float(baseline['experiment_detail_error_rate']):.2%} | {float(treatment['experiment_detail_error_rate']):.2%} |",
            f"| Citation correctness | {float(baseline['citation_correctness']):.2%} | {float(treatment['citation_correctness']):.2%} |",
            f"| Evidence coverage | {float(baseline['evidence_coverage']):.2%} | {float(treatment['evidence_coverage']):.2%} |",
            f"| Mean task-native score | {float(baseline['mean_task_native_score']):.5f} | {float(treatment['mean_task_native_score']):.5f} |",
            f"| Total wall-clock seconds | {float(baseline['total_wall_clock_seconds']):.2f} | {float(treatment['total_wall_clock_seconds']):.2f} |",
            "",
            by_id["result-unsupported-claim-rate"].statement,
            "",
            by_id["result-paired-effect"].statement,
            "",
            by_id["result-experiment-detail-error"].statement,
            "",
            by_id["result-citation-correctness"].statement,
            "",
            by_id["result-task-native-score"].statement,
            "",
            by_id["result-runtime-cost"].statement,
            "",
            by_id["result-claim-retention"].statement,
            "",
            "### Task-level paired effects",
            "",
            "| Task | Unsupported-claim-rate effect | Task-native-score effect |",
            "|---|---:|---:|",
        ]
    )
    for task in protocol.tasks:
        task_id = task.task_id
        lines.append(
            f"| `{task_id}` | {float(paired['task_effects'][task_id]):.4f} | "
            f"{float(paired['task_native_score_effects'][task_id]):.4f} |"
        )
    if "result-same-model-persona-panel" in by_id:
        lines.extend(
            [
                "",
                "### Supplementary same-model adversarial review",
                "",
                by_id["result-same-model-persona-panel"].statement,
                "",
                (
                    "This panel is retained as an internal error-finding aid. Shared-model correlation prevents it "
                    "from serving as independent validation."
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## Discussion",
            "",
            (
                "Within the automated evaluation boundary, the gate was associated with fewer unsupported claims, "
                "fewer erroneous experimental details, and higher citation correctness. The retained claim count was "
                "unchanged, which argues against simple claim suppression as the sole explanation in this matrix. "
                "The main observed trade-off was runtime: verifier and revision work increased total wall-clock cost."
            ),
            "",
            (
                f"The paired mean effect was {float(paired['mean_paired_unsupported_claim_rate_effect']):.4f}, "
                f"with bootstrap interval [{float(bootstrap['ci95_low']):.4f}, {float(bootstrap['ci95_high']):.4f}]. "
                "This numerical result is descriptive until the frozen human audit gate is resumed and completed."
            ),
            "",
            "## Limitations",
            "",
            by_id["limitation-human-validation-deferred"].statement,
            "",
            by_id["limitation-bounded-task-suite"].statement,
            "",
            "Additional limitations:",
            "",
            "- The protected semantic evaluator and experiment backbone both use Codex-family reasoning, so correlated model error is possible.",
            "- Stage 1 is a bounded metadata/abstract review; full-text novelty assessment remains outstanding.",
            "- The absolute 0.02 task-quality check treats positive improvements as deviations, so its frozen boolean is false even though no mean task-level degradation was observed.",
            "- The study evaluates one implementation of a pre-delivery gate and does not isolate every verifier component.",
            "",
            "## Conclusion",
            "",
            (
                "Research Forge completed a frozen 18-cell paired ablation and an evidence-bound provisional paper "
                "without converting automated judgments into a claim of human validation. The automated evidence is "
                "consistent with a reliability benefit from pre-delivery claim-evidence gating, accompanied by a "
                "substantial runtime cost. The next scientific step is external human review of this manuscript and, "
                "if desired, later resumption of the preregistered blinded claim audit."
            ),
            "",
            "## References",
            "",
        ]
    )
    for source in sources:
        authors = ", ".join(source.authors)
        lines.append(
            f"- [{source.source_id}] {authors} ({source.year or 'n.d.'}). *{source.title}*. {source.locator}"
        )
    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            f"- Protocol: `{protocol.protocol_id}` revision {protocol.protocol_revision}",
            f"- Model/backend: `{read_json(_stage2(project) / 'backbone_manifest.json')['model']}`",
            f"- Frozen contract digest: `{_verify_stage2_frozen_contracts(project)}`",
            f"- Protected evaluation summary: `stage2/evaluations/summary.json`",
            f"- Provisional analysis: `stage2/provisional_analysis.json`",
            f"- Claim registry: `synthesis/claims.json`",
            f"- Synthesis audit: `synthesis/audit.json`",
            "- Input hashes: `"
            + json.dumps(
                analysis["input_hashes"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "`",
            "",
        ]
    )
    return "\n".join(lines)


def _tex_escape(value: object) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in str(value))


def _render_latex(
    project: Path, analysis: dict[str, Any], claims: list[StudySynthesisClaim]
) -> str:
    protocol = Stage2Protocol.model_validate(read_json(_stage2(project) / "protocol.json"))
    sources = _sources(project)
    by_id = {claim.claim_id: claim for claim in claims}
    baseline = analysis["arm_metrics"]["baseline"]
    treatment = analysis["arm_metrics"]["treatment"]
    paired = analysis["paired_analysis"]
    bootstrap = paired["hierarchical_bootstrap"]
    backbone = read_json(_stage2(project) / "backbone_manifest.json")
    frozen_digest = _verify_stage2_frozen_contracts(project)
    created_date = str(analysis.get("created_at", ""))[:10]

    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{lmodern}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage{microtype}",
        r"\usepackage{booktabs}",
        r"\usepackage{amsmath}",
        r"\usepackage{enumitem}",
        r"\usepackage{xurl}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\hypersetup{pdftitle={"
        + _tex_escape(protocol.title)
        + r"},pdfauthor={Research Forge automated pipeline}}",
        r"\title{" + _tex_escape(protocol.title) + "}",
        r"\author{Research Forge automated pipeline}",
        r"\date{" + _tex_escape(created_date) + "}",
        "",
        r"\begin{document}",
        r"\maketitle",
        "",
        r"\section*{Status}",
        r"\begin{quote}",
        r"\textbf{Provisional automated-evaluation manuscript. Human validation is deferred. "
        r"This artifact is pipeline-complete but not publication-ready.}",
        r"\end{quote}",
        r"\begin{itemize}[leftmargin=*]",
        r"\item Analysis status: \texttt{" + _tex_escape(analysis["analysis_status"]) + "}",
        r"\item Human validation: \texttt{" + _tex_escape(analysis["human_validation"]) + "}",
        r"\item Preregistered primary analysis interpretable: \texttt{false}",
        r"\end{itemize}",
        "",
        r"\begin{abstract}",
        _tex_escape(
            "End-to-end autonomous research agents can execute experiments and draft scientific "
            "conclusions, but their final claims may overstate source support or experimental evidence. "
            "We evaluated a pre-delivery claim-evidence gate in a frozen same-backbone paired ablation. "
            f"The study used two arms, {len(protocol.tasks)} bounded task packs, "
            f"{len(protocol.seeds)} seeds, and exactly {len(protocol.cells)} cells. "
            + by_id["result-unsupported-claim-rate"].statement
            + " "
            + by_id["result-paired-effect"].statement
            + " "
            + by_id["result-runtime-cost"].statement
            + " Because the preregistered two-human audit is deferred, these effects remain provisional."
        ),
        r"\end{abstract}",
        "",
        r"\section{Introduction}",
        (
            "Autonomous research systems combine literature retrieval, experiment proposal, code "
            "execution, evaluation, and scientific writing. A central reliability problem is that a "
            "valid experiment run does not guarantee that every final prose claim is supported by the "
            "cited source or linked artifact."
        ),
        "",
        r"\paragraph{Research question.} " + _tex_escape(protocol.research_question),
        "",
        r"\paragraph{Frozen hypothesis.} " + _tex_escape(protocol.hypothesis),
        "",
        _tex_escape(by_id["background-bounded-literature"].statement),
        "",
        r"\section{Related Work and Registered Sources}",
        (
            "The Stage 1 evidence boundary is a verified metadata/abstract review rather than a "
            "full-text systematic review. The exact frozen registry is listed below."
        ),
        r"\begin{itemize}[leftmargin=*]",
    ]
    for source in sources:
        lines.append(
            r"\item \cite{" + source.source_id + "} " + _tex_escape(source.title) + "."
        )
    lines.extend(
        [
            r"\end{itemize}",
            "",
            r"\section{Methods}",
            r"\subsection{Study design}",
            _tex_escape(by_id["method-frozen-paired-design"].statement),
            "",
            _tex_escape(by_id["method-claim-evidence-gate"].statement),
            "",
            r"\begin{table}[htbp]",
            r"\centering\small",
            r"\caption{Frozen CPU AIRS-lite task packs.}",
            r"\begin{tabular}{p{0.49\linewidth}rr}",
            r"\toprule",
            r"Task & Baseline & Timeout (s) \\",
            r"\midrule",
        ]
    )
    for task in protocol.tasks:
        lines.append(
            _tex_escape(task.task_id)
            + f" & {task.baseline_score:.6f} & {task.timeout_seconds} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
            r"\subsection{Evidence and evaluation}",
            (
                "Every final output was represented as a structured claim registry. Experiment claims "
                "carried run IDs, exact metric values, and artifact paths; literature and novelty claims "
                "carried exact source IDs. All 18 registries were assigned random blind IDs before hybrid "
                "structural and Codex semantic evaluation."
            ),
            r"\subsection{Analysis}",
            (
                "The primary proxy metric was unsupported-claim rate. Treatment-minus-baseline effects "
                "were paired by task and seed. The frozen analysis used a 10,000-resample hierarchical "
                "bootstrap and paired Cohen's $d_z$. These statistics are automated-evaluator estimates, "
                "not human-validated truth."
            ),
            "",
            r"\section{Results}",
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Arm-level protected automated-evaluator metrics.}",
            r"\begin{tabular}{lrr}",
            r"\toprule",
            r"Metric & Baseline & Treatment \\",
            r"\midrule",
            f"Unsupported claim rate & {100 * float(baseline['unsupported_claim_rate']):.2f}\\% & {100 * float(treatment['unsupported_claim_rate']):.2f}\\% \\\\ ",
            f"Experiment-detail error rate & {100 * float(baseline['experiment_detail_error_rate']):.2f}\\% & {100 * float(treatment['experiment_detail_error_rate']):.2f}\\% \\\\ ",
            f"Citation correctness & {100 * float(baseline['citation_correctness']):.2f}\\% & {100 * float(treatment['citation_correctness']):.2f}\\% \\\\ ",
            f"Evidence coverage & {100 * float(baseline['evidence_coverage']):.2f}\\% & {100 * float(treatment['evidence_coverage']):.2f}\\% \\\\ ",
            f"Mean task-native score & {float(baseline['mean_task_native_score']):.5f} & {float(treatment['mean_task_native_score']):.5f} \\\\ ",
            f"Total wall-clock seconds & {float(baseline['total_wall_clock_seconds']):.2f} & {float(treatment['total_wall_clock_seconds']):.2f} \\\\ ",
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
            _tex_escape(by_id["result-unsupported-claim-rate"].statement),
            "",
            _tex_escape(by_id["result-paired-effect"].statement),
            "",
            _tex_escape(by_id["result-experiment-detail-error"].statement),
            "",
            _tex_escape(by_id["result-citation-correctness"].statement),
            "",
            _tex_escape(by_id["result-task-native-score"].statement),
            "",
            _tex_escape(by_id["result-runtime-cost"].statement),
            "",
            _tex_escape(by_id["result-claim-retention"].statement),
            "",
            r"\begin{table}[htbp]",
            r"\centering\small",
            r"\caption{Task-level paired treatment-minus-baseline effects.}",
            r"\begin{tabular}{lrr}",
            r"\toprule",
            r"Task & Unsupported-claim effect & Task-score effect \\",
            r"\midrule",
        ]
    )
    for task in protocol.tasks:
        task_id = task.task_id
        lines.append(
            _tex_escape(task_id)
            + f" & {float(paired['task_effects'][task_id]):.4f} & "
            + f"{float(paired['task_native_score_effects'][task_id]):.4f} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])
    if "result-same-model-persona-panel" in by_id:
        lines.extend(
            [
                r"\subsection{Supplementary same-model adversarial review}",
                _tex_escape(by_id["result-same-model-persona-panel"].statement),
                "",
                (
                    "This panel is retained as an internal error-finding aid. Shared-model correlation "
                    "prevents it from serving as independent validation."
                ),
            ]
        )
    lines.extend(
        [
            "",
            r"\section{Discussion}",
            (
                "Within the automated evaluation boundary, the gate was associated with fewer unsupported "
                "claims, fewer erroneous experimental details, and higher citation correctness. The retained "
                "claim count was unchanged, which argues against simple claim suppression as the sole "
                "explanation in this matrix. The main observed trade-off was runtime."
            ),
            "",
            (
                f"The paired mean effect was {float(paired['mean_paired_unsupported_claim_rate_effect']):.4f}, "
                f"with bootstrap interval [{float(bootstrap['ci95_low']):.4f}, "
                f"{float(bootstrap['ci95_high']):.4f}]. This result is descriptive until the frozen human "
                "audit gate is resumed and completed."
            ),
            "",
            r"\section{Limitations}",
            r"\begin{itemize}[leftmargin=*]",
            r"\item " + _tex_escape(by_id["limitation-human-validation-deferred"].statement),
            r"\item " + _tex_escape(by_id["limitation-bounded-task-suite"].statement),
            r"\item The protected semantic evaluator and experiment backbone both use Codex-family reasoning, so correlated model error is possible.",
            r"\item Stage 1 is a bounded metadata/abstract review; full-text novelty assessment remains outstanding.",
            r"\item The absolute 0.02 task-quality check treats positive improvements as deviations, so its frozen boolean is false even though no mean task-level degradation was observed.",
            r"\item The study evaluates one implementation of a pre-delivery gate and does not isolate every verifier component.",
            r"\end{itemize}",
            "",
            r"\section{Conclusion}",
            (
                "Research Forge completed a frozen 18-cell paired ablation and an evidence-bound provisional "
                "paper without converting automated judgments into a claim of human validation. The automated "
                "evidence is consistent with a reliability benefit from pre-delivery claim-evidence gating, "
                "accompanied by a substantial runtime cost. The next scientific step is external human review "
                "of this manuscript and, if desired, later resumption of the preregistered blinded claim audit."
            ),
            "",
            r"\begin{thebibliography}{99}",
        ]
    )
    for source in sources:
        authors = ", ".join(source.authors)
        lines.extend(
            [
                r"\bibitem{" + source.source_id + "}",
                _tex_escape(authors)
                + f" ({_tex_escape(source.year or 'n.d.')}). "
                + r"\newblock \emph{" + _tex_escape(source.title) + "}."
                + r" \newblock \url{" + source.locator + "}.",
            ]
        )
    lines.extend(
        [
            r"\end{thebibliography}",
            "",
            r"\section{Reproducibility}",
            r"\begin{itemize}[leftmargin=*]",
            r"\item Protocol: \path{" + protocol.protocol_id + "}, revision " + str(protocol.protocol_revision) + ".",
            r"\item Model/backend: \path{" + str(backbone["model"]) + "}.",
            r"\item Frozen contract digest: \path{" + frozen_digest + "}.",
            r"\item Protected evaluation summary: \path{stage2/evaluations/summary.json}.",
            r"\item Provisional analysis: \path{stage2/provisional_analysis.json}.",
            r"\item Claim registry: \path{synthesis/claims.json}.",
            r"\item Synthesis audit: \path{synthesis/audit.json}.",
            r"\end{itemize}",
            "",
            r"\end{document}",
            "",
        ]
    )
    return "\n".join(lines)


def _preflight(project: Path) -> None:
    state = load_state(project)
    if state.stage not in {Stage.RESULT_REVIEW, Stage.SYNTHESIS}:
        raise ValueError("provisional study synthesis requires result_review or synthesis stage")
    if state.active_run_id:
        raise ValueError("cannot synthesize while a run is active")
    _verify_stage2_frozen_contracts(project)
    stage1 = audit_stage1(project)
    if not stage1.passed:
        raise ValueError("Stage 1 audit failed: " + "; ".join(stage1.violations))
    protocol_audit = audit_stage2_protocol(project)
    if not protocol_audit.passed:
        raise ValueError("Stage 2 protocol audit failed: " + "; ".join(protocol_audit.violations))
    evaluation = audit_stage2_evaluation(project)
    if not evaluation.passed or not evaluation.complete:
        raise ValueError("protected evaluation is incomplete: " + "; ".join(evaluation.violations))
    manual = audit_manual_audit(project)
    if not manual.get("passed") or manual.get("complete"):
        raise ValueError("manual audit must be valid, incomplete, and explicitly deferred")
    protocol = Stage2Protocol.model_validate(read_json(_stage2(project) / "protocol.json"))
    _active_deferral(project, protocol.protocol_id)
    if (_stage2(project) / "final_analysis.json").exists():
        raise ValueError("official final analysis already exists; provisional synthesis is not allowed")


def synthesize_provisional_study(project: Path) -> SynthesisAudit:
    project = project.resolve()
    _preflight(project)
    analysis_path = _stage2(project) / "provisional_analysis.json"
    analysis = _expected_analysis(project, created_at=utc_now())
    write_json_atomic(analysis_path, analysis)
    claims = _build_claims(project, analysis)
    write_json_atomic(
        project / "synthesis" / "claims.json",
        {
            "schema_version": 1,
            "protocol_id": analysis["protocol_id"],
            "synthesis_mode": SYNTHESIS_MODE,
            "analysis_status": ANALYSIS_STATUS,
            "human_validation": HUMAN_VALIDATION,
            "claims": [claim.model_dump(mode="json") for claim in claims],
        },
    )
    manuscript = _render_manuscript(project, analysis, claims)
    manuscript_path = project / "synthesis" / "manuscript.md"
    manuscript_path.parent.mkdir(parents=True, exist_ok=True)
    manuscript_path.write_text(manuscript, encoding="utf-8", newline="\n")
    latex = _render_latex(project, analysis, claims)
    (project / "synthesis" / "manuscript.tex").write_text(
        latex, encoding="utf-8", newline="\n"
    )
    state = load_state(project)
    if state.stage != Stage.SYNTHESIS:
        transition(
            project,
            state,
            Stage.SYNTHESIS,
            "provisional_study_manuscript_synthesized",
            claim_count=len(claims),
            human_validation=HUMAN_VALIDATION,
        )
        save_state(project, state)
    return audit_provisional_study_synthesis(project, persist=True)


def audit_provisional_study_synthesis(
    project: Path, *, persist: bool = True
) -> SynthesisAudit:
    project = project.resolve()
    stage2 = _stage2(project)
    state = load_state(project)
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    check(
        "stage_allows_synthesis",
        state.stage in {Stage.SYNTHESIS, Stage.COMPLETED},
        "project is not in synthesis or completed stage",
    )
    check("no_active_run", state.active_run_id is None, "an experiment run is still active")
    try:
        _verify_stage2_frozen_contracts(project)
        check("frozen_contracts_intact", True, "")
    except Exception as exc:
        check("frozen_contracts_intact", False, f"frozen contracts failed verification: {exc}")
    stage1 = audit_stage1(project)
    check("stage1_gate_valid", stage1.passed, "Stage 1 evidence gate failed")
    protocol_audit = audit_stage2_protocol(project)
    check("stage2_protocol_valid", protocol_audit.passed, "Stage 2 protocol audit failed")
    evaluation = audit_stage2_evaluation(project)
    check(
        "protected_evaluation_complete",
        evaluation.passed
        and evaluation.complete
        and evaluation.evaluated_registries
        == getattr(evaluation, "expected_registries", 18),
        "protected evaluation is not complete and integral",
    )
    manual = audit_manual_audit(project)
    check(
        "human_validation_deferred_not_fabricated",
        bool(manual.get("passed"))
        and not bool(manual.get("complete"))
        and manual.get("status") == "awaiting_two_independent_auditors",
        "manual audit is not in the valid deferred state",
    )
    try:
        protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
        decision = _active_deferral(project, protocol.protocol_id)
        check(
            "operator_deferral_active",
            decision.get("scope") == "current_stage_only",
            "operator human-validation deferral is absent or malformed",
        )
    except Exception as exc:
        protocol = None
        check("operator_deferral_active", False, f"cannot validate operator deferral: {exc}")
    check(
        "official_final_analysis_withheld",
        not (stage2 / "final_analysis.json").exists(),
        "official final analysis exists despite deferred human validation",
    )

    analysis_path = stage2 / "provisional_analysis.json"
    analysis: dict[str, Any] = {}
    try:
        analysis = read_json(analysis_path)
        expected_analysis = _expected_analysis(
            project, created_at=str(analysis.get("created_at", ""))
        )
        check(
            "provisional_analysis_exact",
            analysis == expected_analysis
            and analysis.get("primary_analysis_interpretable") is False
            and analysis.get("human_validation") == HUMAN_VALIDATION,
            "provisional analysis does not match frozen automated evidence",
        )
    except Exception as exc:
        check("provisional_analysis_exact", False, f"cannot validate provisional analysis: {exc}")

    claims_path = project / "synthesis" / "claims.json"
    claims: list[StudySynthesisClaim] = []
    try:
        payload = read_json(claims_path)
        claims = [StudySynthesisClaim.model_validate(item) for item in payload["claims"]]
        check(
            "claim_registry_schema_valid",
            payload.get("protocol_id") == (protocol.protocol_id if protocol else None)
            and payload.get("synthesis_mode") == SYNTHESIS_MODE
            and payload.get("analysis_status") == ANALYSIS_STATUS
            and payload.get("human_validation") == HUMAN_VALIDATION
            and len({claim.claim_id for claim in claims}) == len(claims),
            "study claim registry metadata or IDs are invalid",
        )
        expected_claims = _build_claims(project, analysis)
        check(
            "claims_match_frozen_evidence",
            [claim.model_dump(mode="json") for claim in claims]
            == [claim.model_dump(mode="json") for claim in expected_claims],
            "study claims do not match reconstructed frozen evidence",
        )
    except Exception as exc:
        check("claim_registry_schema_valid", False, f"cannot validate claim registry: {exc}")
        check("claims_match_frozen_evidence", False, "claims could not be reconstructed")

    source_map = {source.source_id: source for source in _sources(project)}
    evidence_integral = bool(claims)
    result_claims_provisional = bool(claims)
    for claim in claims:
        if claim.kind == ClaimKind.RESULT and not claim.provisional:
            result_claims_provisional = False
        for source_id in claim.source_ids:
            source = source_map.get(source_id)
            if source is None or not source.verified:
                evidence_integral = False
                violations.append(
                    f"claim {claim.claim_id} cites missing or unverified source {source_id}"
                )
        for relative, expected in claim.evidence_sha256.items():
            try:
                path = safe_relative(project, relative)
                if not path.is_file() or sha256_file(path) != expected:
                    evidence_integral = False
                    violations.append(
                        f"claim {claim.claim_id} evidence hash mismatch: {relative}"
                    )
            except Exception as exc:
                evidence_integral = False
                violations.append(
                    f"claim {claim.claim_id} evidence path is invalid: {relative}: {exc}"
                )
    check("all_claim_evidence_integral", evidence_integral, "claim evidence is incomplete")
    check(
        "all_result_claims_marked_provisional",
        result_claims_provisional,
        "one or more automated result claims is not marked provisional",
    )

    manuscript_path = project / "synthesis" / "manuscript.md"
    manuscript = manuscript_path.read_text(encoding="utf-8") if manuscript_path.is_file() else ""
    check("manuscript_present", bool(manuscript.strip()), "manuscript is missing")
    check(
        "manuscript_sections_complete",
        all(section in manuscript for section in _REQUIRED_SECTIONS),
        "manuscript is missing required sections",
    )
    try:
        expected_manuscript = _render_manuscript(project, analysis, claims)
        check(
            "manuscript_matches_claim_registry",
            manuscript == expected_manuscript,
            "manuscript does not match the frozen claim registry",
        )
    except Exception as exc:
        check(
            "manuscript_matches_claim_registry",
            False,
            f"cannot reconstruct manuscript: {exc}",
        )

    depth_blocker: str | None = None
    try:
        depth = audit_manuscript_depth(
            manuscript_path,
            profile="journal-article",
            report_path=(
                project / "synthesis" / "manuscript_depth.json" if persist else None
            ),
        )
        check("manuscript_depth_audit_completed", True, "")
        if not depth.passed:
            depth_blocker = (
                "journal-article manuscript depth gate failed: "
                + "; ".join(depth.violations)
            )
    except Exception as exc:
        check(
            "manuscript_depth_audit_completed",
            False,
            f"manuscript depth audit failed to run: {exc}",
        )

    latex_path = project / "synthesis" / "manuscript.tex"
    latex = latex_path.read_text(encoding="utf-8") if latex_path.is_file() else ""
    check("latex_manuscript_present", bool(latex.strip()), "LaTeX manuscript is missing")
    check(
        "latex_manuscript_sections_complete",
        all(marker in latex for marker in _REQUIRED_LATEX_MARKERS),
        "LaTeX manuscript is missing required document sections",
    )
    try:
        expected_latex = _render_latex(project, analysis, claims)
        check(
            "latex_manuscript_matches_claim_registry",
            latex == expected_latex,
            "LaTeX manuscript does not match the frozen claim registry",
        )
    except Exception as exc:
        check(
            "latex_manuscript_matches_claim_registry",
            False,
            f"cannot reconstruct LaTeX manuscript: {exc}",
        )

    publication_blockers = [
        "human validation is deferred",
        "the preregistered primary analysis remains uninterpretable",
        "protected semantic effects are automated-evaluator estimates",
        "the scientist-persona panel is a same-model ensemble",
        "the experiment is bounded to three CPU AIRS-lite task packs",
        "Stage 1 novelty evidence is metadata/abstract level rather than full-text review",
    ]
    if depth_blocker:
        publication_blockers.append(depth_blocker)
    audit = SynthesisAudit(
        passed=bool(checks) and all(checks.values()),
        publication_ready=False,
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        publication_blockers=publication_blockers,
        claim_count=len(claims),
        evidence_run_ids=[cell.cell_id for cell in protocol.cells] if protocol else [],
        source_ids=list(source_map),
        manuscript_hash=sha256_file(manuscript_path) if manuscript_path.is_file() else None,
        claims_hash=sha256_file(claims_path) if claims_path.is_file() else None,
    )
    if persist and state.stage != Stage.COMPLETED:
        write_json_atomic(project / "synthesis" / "audit.json", audit)
    return audit


def _closed_loop_status(project: Path, audit: SynthesisAudit) -> dict[str, Any]:
    protocol = Stage2Protocol.model_validate(read_json(_stage2(project) / "protocol.json"))
    return {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "pipeline_complete": True,
        "project_stage": Stage.COMPLETED.value,
        "publication_ready": False,
        "analysis_status": ANALYSIS_STATUS,
        "primary_analysis_interpretable": False,
        "human_validation": HUMAN_VALIDATION,
        "synthesis_mode": SYNTHESIS_MODE,
        "four_stage_gates": {
            "stage_1_discovery": True,
            "stage_2_protocol": True,
            "stage_3_experimentation": True,
            "stage_4_synthesis": audit.passed,
        },
        "study_evidence": {
            "completed_cells": len(protocol.cells),
            "protected_evaluations": 18,
            "claim_count": audit.claim_count,
            "manuscript_markdown": "synthesis/manuscript.md",
            "manuscript_latex": "synthesis/manuscript.tex",
        },
        "boundary": (
            "The engineering pipeline is complete, but the scientific primary analysis remains "
            "provisional until the preregistered human-validation gate is resumed and completed."
        ),
    }


def _completion_artifact_paths(project: Path) -> list[str]:
    paths = [
        "stage2/frozen_manifest.json",
        "stage2/protocol.json",
        "stage2/evaluations/summary.json",
        "stage2/manual_audit_manifest.json",
        "stage2/operator_decisions.json",
        "stage2/provisional_analysis.json",
        "stage2/closed_loop_status.json",
        "synthesis/claims.json",
        "synthesis/manuscript.md",
        "synthesis/manuscript.tex",
        "synthesis/manuscript_depth.json",
        "synthesis/audit.json",
    ]
    panel = _persona_panel_path(project)
    if panel is not None:
        paths.append(_relative(project, panel))
    return paths


def _issue_completion_certificate(
    project: Path, audit: SynthesisAudit
) -> CompletionCertificate:
    if not audit.passed:
        raise ValueError("cannot certify a failed study synthesis audit")
    artifacts = {
        relative: sha256_file(safe_relative(project, relative))
        for relative in _completion_artifact_paths(project)
    }
    certificate = CompletionCertificate(
        publication_ready=False,
        audit_hash=artifacts["synthesis/audit.json"],
        artifact_hashes=artifacts,
    )
    write_json_atomic(project / "completion_certificate.json", certificate)
    return certificate


def complete_provisional_study(project: Path) -> dict[str, Any]:
    project = project.resolve()
    state = load_state(project)
    if state.stage != Stage.SYNTHESIS:
        raise ValueError("provisional study completion requires synthesis stage")
    audit = audit_provisional_study_synthesis(project, persist=True)
    if not audit.passed:
        raise ValueError("study synthesis audit failed: " + "; ".join(audit.violations))
    status_path = _stage2(project) / "closed_loop_status.json"
    write_json_atomic(status_path, _closed_loop_status(project, audit))
    certificate = _issue_completion_certificate(project, audit)
    transition(
        project,
        state,
        Stage.COMPLETED,
        "provisional_study_four_stage_loop_completed",
        publication_ready=False,
        human_validation=HUMAN_VALIDATION,
        pipeline_complete=True,
    )
    save_state(project, state)
    completion_audit = audit_provisional_study_completion(project)
    if not completion_audit["passed"]:
        raise ValueError(
            "completion certificate failed verification: "
            + "; ".join(completion_audit["violations"])
        )
    return {
        "certificate": certificate.model_dump(mode="json"),
        "completion_audit": completion_audit,
    }


def render_provisional_study_latex(project: Path) -> dict[str, Any]:
    """Add or refresh the audited LaTeX paper, including completed projects."""
    project = project.resolve()
    state = load_state(project)
    if state.stage not in {Stage.SYNTHESIS, Stage.COMPLETED}:
        raise ValueError("LaTeX rendering requires synthesis or completed stage")
    analysis = read_json(_stage2(project) / "provisional_analysis.json")
    claims_payload = read_json(project / "synthesis" / "claims.json")
    claims = [
        StudySynthesisClaim.model_validate(item)
        for item in claims_payload.get("claims", [])
    ]
    latex_path = project / "synthesis" / "manuscript.tex"
    latex_path.write_text(
        _render_latex(project, analysis, claims),
        encoding="utf-8",
        newline="\n",
    )
    # Rendering is a finalization path. Persist the deterministic manuscript-depth
    # report so older projects cannot receive a completion certificate that names
    # a missing gate artifact.
    audit = audit_provisional_study_synthesis(project, persist=True)
    if not audit.passed:
        raise ValueError("LaTeX synthesis audit failed: " + "; ".join(audit.violations))
    write_json_atomic(project / "synthesis" / "audit.json", audit)
    result: dict[str, Any] = {
        "latex_path": _relative(project, latex_path),
        "latex_sha256": sha256_file(latex_path),
        "synthesis_audit": audit.model_dump(mode="json"),
    }
    if state.stage == Stage.COMPLETED:
        write_json_atomic(
            _stage2(project) / "closed_loop_status.json",
            _closed_loop_status(project, audit),
        )
        certificate = _issue_completion_certificate(project, audit)
        completion_audit = audit_provisional_study_completion(project)
        if not completion_audit["passed"]:
            raise ValueError(
                "LaTeX completion recertification failed: "
                + "; ".join(completion_audit["violations"])
            )
        result["certificate"] = certificate.model_dump(mode="json")
        result["completion_audit"] = completion_audit
    return result


def audit_provisional_study_completion(project: Path) -> dict[str, Any]:
    project = project.resolve()
    state = load_state(project)
    violations: list[str] = []
    checks: dict[str, bool] = {}

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    completion = verify_completion_certificate(project)
    check(
        "completion_certificate_valid",
        bool(completion.get("passed")),
        "generic completion certificate verification failed",
    )
    try:
        certificate = CompletionCertificate.model_validate(
            read_json(project / "completion_certificate.json")
        )
        check(
            "latex_manuscript_certified",
            "synthesis/manuscript.tex" in certificate.artifact_hashes,
            "completion certificate does not bind the LaTeX manuscript",
        )
    except Exception as exc:
        check(
            "latex_manuscript_certified",
            False,
            f"cannot verify LaTeX certificate binding: {exc}",
        )
    check("project_completed", state.stage == Stage.COMPLETED, "project state is not completed")
    try:
        status = read_json(_stage2(project) / "closed_loop_status.json")
        check(
            "closed_loop_status_valid",
            status.get("pipeline_complete") is True
            and status.get("publication_ready") is False
            and status.get("human_validation") == HUMAN_VALIDATION
            and status.get("analysis_status") == ANALYSIS_STATUS
            and all(status.get("four_stage_gates", {}).values()),
            "closed-loop status does not preserve the provisional completion boundary",
        )
    except Exception as exc:
        check("closed_loop_status_valid", False, f"cannot load closed-loop status: {exc}")
    try:
        analysis = read_json(_stage2(project) / "provisional_analysis.json")
        check(
            "analysis_remains_provisional",
            analysis.get("primary_analysis_interpretable") is False
            and analysis.get("human_validation") == HUMAN_VALIDATION
            and not (_stage2(project) / "final_analysis.json").exists(),
            "completion incorrectly promoted the provisional analysis",
        )
    except Exception as exc:
        check("analysis_remains_provisional", False, f"cannot load provisional analysis: {exc}")
    try:
        audit = SynthesisAudit.model_validate(read_json(project / "synthesis" / "audit.json"))
        check(
            "synthesis_audit_passed_not_publishable",
            audit.passed and audit.publication_ready is False,
            "synthesis audit is missing, failed, or incorrectly publication-ready",
        )
    except Exception as exc:
        check("synthesis_audit_passed_not_publishable", False, f"cannot load synthesis audit: {exc}")
    return {
        "schema_version": 1,
        "passed": bool(checks) and all(checks.values()),
        "pipeline_complete": bool(checks) and all(checks.values()),
        "publication_ready": False,
        "human_validation": HUMAN_VALIDATION,
        "analysis_status": ANALYSIS_STATUS,
        "checks": checks,
        "violations": list(dict.fromkeys(violations)),
    }
