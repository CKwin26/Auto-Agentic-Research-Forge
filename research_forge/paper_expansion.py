from __future__ import annotations

import asyncio
from collections import Counter
import json
import hashlib
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .manuscript_depth import audit_manuscript_depth
from .models import LiteratureSource, LiteratureSourceType, StrictModel, utc_now
from .paper_authoring import (
    EvidenceClaimMap,
    HierarchicalPaperOutline,
    PaperArtifactManifest,
    PanelDecision,
    SubmissionGenreProfile,
    build_evidence_claim_map,
    build_paper_artifacts,
    decide_panel,
    load_or_create_submission_genre,
    materialize_artifact_callouts,
    validate_outline,
)
from .paper_pipeline import (
    GENERIC_JOURNAL_ARTICLE,
    markdown_heading,
    normalize_unstructured_abstract,
    validate_abstract_prose,
    validate_markdown_structure,
)
from .paper_typesetting import write_submission_latex
from .storage import read_json, sha256_file, write_json_atomic


MIN_VERIFIED_PAPERS = 15
MIN_NUMERIC_EVIDENCE = 15
_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]{1,79})\]")
_ARTIFACT_CALLOUT_RE = re.compile(r"\[(FIGURE|TABLE):([a-z0-9-]{3,84})\]")
_NUMBER_TOKEN_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?")
_REVIEW_ROLES = ("feynman", "tukey", "shannon", "popper")


class PaperDraftSections(StrictModel):
    """Structured prose returned by the paper-writing agent.

    References and the frozen numeric-evidence table are rendered locally so
    the model cannot invent either one.
    """

    title: str = Field(min_length=5, max_length=500)
    abstract: str = Field(min_length=100, max_length=5_000)
    introduction: str = Field(min_length=300, max_length=30_000)
    related_work: str = Field(min_length=300, max_length=35_000)
    methods: str = Field(min_length=500, max_length=45_000)
    results: str = Field(min_length=400, max_length=35_000)
    discussion: str = Field(min_length=500, max_length=40_000)
    limitations: str = Field(min_length=200, max_length=15_000)
    conclusion: str = Field(min_length=150, max_length=8_000)
    data_availability: str = Field(min_length=30, max_length=3_000)
    ethics_statement: str = Field(min_length=30, max_length=3_000)
    author_contributions: str = Field(min_length=30, max_length=3_000)
    conflict_of_interest: str = Field(min_length=10, max_length=2_000)
    funding: str = Field(min_length=10, max_length=2_000)
    ai_disclosure: str = Field(min_length=30, max_length=3_000)


class PaperExpansionPlan(StrictModel):
    schema_version: int = 1
    planned_at: str = Field(default_factory=utc_now)
    track_id: str
    ready: bool
    idea_gate_passed: bool
    evidence_gate_passed: bool
    literature_gate_passed: bool
    verified_paper_count: int = Field(ge=0)
    frozen_literature_manifest_valid: bool
    numeric_evidence_count: int = Field(ge=0)
    diagnostic_owner: Literal[
        "idea_validation",
        "evidence_packaging",
        "literature_grounding",
        "paper_writer",
        "none",
    ]
    blockers: list[str]
    next_action: str
    planned_artifacts: list[str]


class PaperExpansionAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    track_id: str
    passed: bool
    full_manuscript_generated: bool
    manuscript_depth_passed: bool
    genre_compliance_passed: bool
    citation_integrity_passed: bool
    conclusion_binding_passed: bool
    numeric_evidence_binding_passed: bool
    paper_draft_ready: bool
    publication_ready: bool
    diagnostic_owner: Literal[
        "idea_validation",
        "evidence_packaging",
        "literature_grounding",
        "paper_writer",
        "none",
    ]
    checks: dict[str, bool]
    violations: list[str]
    publication_blockers: list[str]


class PaperCompletionCertificate(StrictModel):
    schema_version: int = 1
    completed_at: str = Field(default_factory=utc_now)
    track_id: str
    paper_draft_ready: bool
    publication_ready: bool
    manuscript_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    genre_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_claim_map_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outline_review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    draft_review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision_trace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prose_polish_trace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    typeset_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    typesetting_report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    audit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PaperRevisionTrace(StrictModel):
    """Auditable deterministic edits between the model draft and rendered prose."""

    schema_version: int = 1
    revised_at: str = Field(default_factory=utc_now)
    stage: Literal["revise"] = "revise"
    policy: str = "deterministic_genre_normalization"
    changed_fields: list[str]
    operations: list[str]
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revised_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scientific_content_change_allowed: bool = False


class PaperProsePolishTrace(StrictModel):
    schema_version: int = 1
    polished_at: str = Field(default_factory=utc_now)
    policy: str = "academic_humanizer_claim_preserving"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    polished_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    number_multiset_preserved: bool
    citation_multiset_preserved: bool
    artifact_callout_multiset_preserved: bool
    frozen_conclusion_preserved: bool
    accepted: bool
    reverted: bool = False
    violations: list[str]


class PaperTypesettingReport(StrictModel):
    schema_version: int = 1
    rendered_at: str = Field(default_factory=utc_now)
    structure_profile_id: str
    language: Literal["zh", "en"]
    latex_path: str
    latex_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_generated: bool
    pdf_path: str | None = None
    pdf_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    compiler: str | None = None
    blocker: str | None = None
    author_metadata_required: bool = True


def _model_sha256(value: StrictModel) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_paper_draft(
    draft: PaperDraftSections,
) -> tuple[PaperDraftSections, PaperRevisionTrace]:
    """Apply only deterministic, claim-preserving genre repairs."""

    normalized_abstract, operations = normalize_unstructured_abstract(draft.abstract)
    revised = draft.model_copy(update={"abstract": normalized_abstract})
    changed_fields = ["abstract"] if normalized_abstract != draft.abstract.strip() else []
    return revised, PaperRevisionTrace(
        changed_fields=changed_fields,
        operations=list(operations),
        source_sha256=_model_sha256(draft),
        revised_sha256=_model_sha256(revised),
    )


def _json(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _literature_sources(run_dir: Path) -> tuple[list[LiteratureSource], bool, list[str]]:
    source_dir = run_dir / "source_snapshot" / "literature" / "sources"
    manifest_path = run_dir / "source_snapshot" / "literature_manifest.json"
    violations: list[str] = []
    sources: list[LiteratureSource] = []
    if source_dir.is_dir():
        for path in sorted(source_dir.glob("*.json")):
            try:
                source = LiteratureSource.model_validate(read_json(path))
            except Exception as exc:
                violations.append(f"文献来源 {path.name} 格式无效：{exc}")
                continue
            if source.source_type == LiteratureSourceType.PAPER and source.verified:
                sources.append(source)
            else:
                violations.append(
                    f"文献来源 {source.source_id} 不是已核验论文"
                )

    manifest_valid = False
    if not manifest_path.is_file():
        violations.append("缺少冻结文献清单")
    else:
        try:
            manifest = _json(manifest_path)
            expected = manifest.get("source_hashes", {})
            if not isinstance(expected, dict) or not expected:
                raise ValueError("source_hashes is empty")
            actual_ids = {source.source_id for source in sources}
            manifest_ids = {str(source_id) for source_id in expected}
            hashes_match = actual_ids == manifest_ids
            for source_id, digest in expected.items():
                source_path = source_dir / f"{source_id}.json"
                if not source_path.is_file() or sha256_file(source_path) != str(digest):
                    hashes_match = False
            manifest_valid = hashes_match
            if not manifest_valid:
                violations.append(
                    "冻结文献清单未与全部已核验来源文件精确绑定"
                )
        except Exception as exc:
            violations.append(f"冻结文献清单格式无效：{exc}")
    return sources, manifest_valid, violations


def prepare_project_bundle_paper(
    run_dir: str | Path, *, persist: bool = True
) -> PaperExpansionPlan:
    root = Path(run_dir).resolve()
    plan_path = root / "stage_4_synthesis" / "paper_expansion_plan.json"
    verdict = _json(root / "stage_3_experimentation" / "idea_verdict.json")
    lock = _json(root / "stage_2_protocol" / "protocol_lock.json")
    sources, manifest_valid, literature_violations = _literature_sources(root)
    numeric_count = len(verdict.get("numeric_evidence") or [])

    idea_gate = bool(verdict.get("idea_validated")) and verdict.get("status") in {
        "supported",
        "refuted",
    }
    evidence_gate = bool(
        lock.get("source_mode") == "declared_chain"
        and lock.get("protocol_bound_to_output")
        and verdict.get("protocol_bound_to_output")
        and verdict.get("evidence_maturity") == "prospective_blind"
        and numeric_count >= MIN_NUMERIC_EVIDENCE
    )
    literature_gate = manifest_valid and len(sources) >= MIN_VERIFIED_PAPERS
    blockers: list[str] = []
    if not idea_gate:
        blockers.append(
            "想法尚未得到干净的前瞻验证；完整论文写作保持关闭，避免把写作结果误当成想法成立"
        )
    if not evidence_gate:
        if not bool(lock.get("protocol_bound_to_output")):
            blockers.append("冻结协议与机器结果没有精确绑定")
        if verdict.get("evidence_maturity") != "prospective_blind":
            blockers.append("证据不是冻结后的前瞻盲测")
        if numeric_count < MIN_NUMERIC_EVIDENCE:
            blockers.append(
                f"可绑定数值证据只有 {numeric_count} 项；完整论文审计至少需要 {MIN_NUMERIC_EVIDENCE} 项"
            )
    if not literature_gate:
        blockers.append(
            f"冻结且核验过的论文文献只有 {len(sources)} 条；论文补全至少需要 {MIN_VERIFIED_PAPERS} 条"
        )
        blockers.extend(literature_violations)

    ready = idea_gate and evidence_gate and literature_gate
    if not idea_gate:
        owner = "idea_validation"
        next_action = "回到第三阶段，冻结窄化假设并取得新的独立前瞻证据"
    elif not evidence_gate:
        owner = "evidence_packaging"
        next_action = "补齐协议—结果绑定和可复核数值证据，不改写已经冻结的实验结论"
    elif not literature_gate:
        owner = "literature_grounding"
        next_action = "完成文献检索、来源核验与冻结清单后再启动论文写作代理"
    else:
        owner = "none"
        next_action = "前置门槛已通过，可以启动完整论文补写与独立写作审计"

    planned_at = utc_now()
    if plan_path.is_file():
        try:
            planned_at = PaperExpansionPlan.model_validate(
                read_json(plan_path)
            ).planned_at
        except Exception:
            pass
    plan = PaperExpansionPlan(
        planned_at=planned_at,
        track_id=str(verdict.get("track_id") or lock.get("track_id") or "unknown"),
        ready=ready,
        idea_gate_passed=idea_gate,
        evidence_gate_passed=evidence_gate,
        literature_gate_passed=literature_gate,
        verified_paper_count=len(sources),
        frozen_literature_manifest_valid=manifest_valid,
        numeric_evidence_count=numeric_count,
        diagnostic_owner=owner,  # type: ignore[arg-type]
        blockers=list(dict.fromkeys(blockers)),
        next_action=next_action,
        planned_artifacts=[
            "stage_4_synthesis/submission_genre.json",
            "stage_4_synthesis/evidence_claim_map.json",
            "stage_4_synthesis/paper_outline.initial.json",
            "stage_4_synthesis/paper_outline_review.json",
            "stage_4_synthesis/paper_outline.json",
            "stage_4_synthesis/paper_draft_sections.json",
            "stage_4_synthesis/paper_draft_review.json",
            "stage_4_synthesis/paper_prose_polish_trace.json",
            "stage_4_synthesis/paper_artifact_manifest.json",
            "stage_4_synthesis/full_manuscript.tex",
            "stage_4_synthesis/paper_typesetting_report.json",
            "stage_4_synthesis/paper_revision_trace.json",
            "stage_4_synthesis/full_manuscript.md",
            "stage_4_synthesis/full_manuscript_depth.json",
            "stage_4_synthesis/paper_expansion_audit.json",
            "stage_4_synthesis/paper_completion_certificate.json",
        ],
    )
    if persist:
        write_json_atomic(plan_path, plan)
    return plan


def _references(sources: list[LiteratureSource]) -> str:
    lines = []
    for source in sources:
        authors = ", ".join(source.authors)
        year = str(source.year) if source.year is not None else "n.d."
        lines.append(
            f"- [{source.source_id}] {authors} ({year}). *{source.title}*. {source.locator}"
        )
    return "\n".join(lines)


def _numeric_table(verdict: dict[str, Any]) -> str:
    lines = [
        "### 冻结数值证据",
        "",
        "下表由系统直接从第三阶段判定产物写入，写作代理不能修改字段路径或数值。",
        "",
        "Table: 第三阶段冻结数值证据",
        "| 证据路径 | 冻结值 |",
        "|---|---:|",
    ]
    for item in verdict.get("numeric_evidence") or []:
        lines.append(f"| `{item['path']}` | {item['value']} |")
    return "\n".join(lines)


def render_full_manuscript(
    draft: PaperDraftSections,
    *,
    verdict: dict[str, Any],
    sources: list[LiteratureSource],
    artifact_manifest: PaperArtifactManifest | None = None,
) -> str:
    draft, _ = normalize_paper_draft(draft)
    heading = lambda key: markdown_heading(
        GENERIC_JOURNAL_ARTICLE.section(key), "zh"
    )
    manuscript = (
        f"# {draft.title}\n\n"
        "> 状态：证据约束的完整论文候选稿。想法判定来自冻结实验产物，引用和数值表由本地审计器生成。\n\n"
        f"{heading('abstract')}\n\n{draft.abstract}\n\n"
        f"{heading('introduction')}\n\n{draft.introduction}\n\n"
        f"{heading('related_work')}\n\n{draft.related_work}\n\n"
        f"{heading('methods')}\n\n{draft.methods}\n\n"
        f"{heading('results')}\n\n{draft.results}\n\n{_numeric_table(verdict)}\n\n"
        f"{heading('discussion')}\n\n{draft.discussion}\n\n"
        f"{heading('limitations')}\n\n{draft.limitations}\n\n"
        f"{heading('conclusion')}\n\n{draft.conclusion}\n\n"
        f"{heading('data_availability')}\n\n{draft.data_availability}\n\n"
        f"{heading('ethics_statement')}\n\n{draft.ethics_statement}\n\n"
        f"{heading('author_contributions')}\n\n{draft.author_contributions}\n\n"
        f"{heading('conflict_of_interest')}\n\n{draft.conflict_of_interest}\n\n"
        f"{heading('funding')}\n\n{draft.funding}\n\n"
        f"{heading('ai_disclosure')}\n\n{draft.ai_disclosure}\n\n"
        f"{heading('references')}\n\n{_references(sources)}\n"
    )
    if artifact_manifest is not None:
        manuscript = materialize_artifact_callouts(manuscript, artifact_manifest)
    return manuscript


def _audit_full_manuscript(
    root: Path, plan: PaperExpansionPlan, *, persist: bool = True
) -> PaperExpansionAudit:
    manuscript_path = root / "stage_4_synthesis" / "full_manuscript.md"
    verdict = _json(root / "stage_3_experimentation" / "idea_verdict.json")
    sources, manifest_valid, literature_violations = _literature_sources(root)
    checks: dict[str, bool] = {
        "paper_expansion_plan_ready": plan.ready,
        "full_manuscript_present": manuscript_path.is_file(),
        "frozen_literature_manifest_valid": manifest_valid,
        "submission_genre_present": (root / "stage_4_synthesis" / "submission_genre.json").is_file(),
        "evidence_claim_map_present": (root / "stage_4_synthesis" / "evidence_claim_map.json").is_file(),
        "approved_outline_present": (root / "stage_4_synthesis" / "paper_outline.json").is_file(),
        "outline_panel_review_present": (root / "stage_4_synthesis" / "paper_outline_review.json").is_file(),
        "revision_trace_present": (root / "stage_4_synthesis" / "paper_revision_trace.json").is_file(),
        "draft_panel_review_present": (root / "stage_4_synthesis" / "paper_draft_review.json").is_file(),
        "prose_polish_trace_present": (root / "stage_4_synthesis" / "paper_prose_polish_trace.json").is_file(),
        "typeset_source_present": (root / "stage_4_synthesis" / "full_manuscript.tex").is_file(),
        "typesetting_report_present": (root / "stage_4_synthesis" / "paper_typesetting_report.json").is_file(),
    }
    artifact_manifest_path = root / "stage_4_synthesis" / "paper_artifact_manifest.json"
    try:
        artifact_manifest = PaperArtifactManifest.model_validate(read_json(artifact_manifest_path))
        checks["paper_artifacts_ready"] = artifact_manifest.ready
    except Exception:
        checks["paper_artifacts_ready"] = False
    violations: list[str] = []
    if not checks["paper_artifacts_ready"]:
        violations.append(
            "one or more approved figure/table slots could not be rendered from frozen evidence; repair evidence or revise the outline"
        )
    try:
        polish_trace = PaperProsePolishTrace.model_validate(
            read_json(root / "stage_4_synthesis" / "paper_prose_polish_trace.json")
        )
        checks["academic_humanizer_preserved_immutable_content"] = bool(
            polish_trace.accepted or polish_trace.reverted
        )
    except Exception:
        checks["academic_humanizer_preserved_immutable_content"] = False
    if not checks["academic_humanizer_preserved_immutable_content"]:
        violations.append("academic humanizer output was neither integrity-safe nor reverted")
    depth_passed = False
    genre_passed = False
    citation_passed = False
    conclusion_passed = False
    numeric_passed = False
    if manuscript_path.is_file():
        manuscript = manuscript_path.read_text(encoding="utf-8")
        checks["no_unresolved_artifact_callouts"] = not _ARTIFACT_CALLOUT_RE.search(manuscript)
        if not checks["no_unresolved_artifact_callouts"]:
            violations.append("full manuscript still contains unresolved figure/table callouts")
        structure_violations = validate_markdown_structure(
            manuscript, GENERIC_JOURNAL_ARTICLE
        )
        checks["paper_structure_contract_passed"] = not structure_violations
        genre_violations = [
            item
            for item in structure_violations
            if item.startswith("abstract ") or item.startswith("unstructured abstract")
        ]
        genre_passed = not genre_violations
        checks["abstract_genre_contract_passed"] = genre_passed
        violations.extend(structure_violations)
        allowed = {source.source_id for source in sources}
        cited = set(_CITATION_RE.findall(manuscript))
        related_heading = markdown_heading(
            GENERIC_JOURNAL_ARTICLE.section("related_work"), "zh"
        )
        related_match = re.search(
            rf"(?s)^{re.escape(related_heading)}\s*$\s*(.*?)(?=^##\s+)",
            manuscript,
            re.MULTILINE,
        )
        related = related_match.group(1) if related_match else ""
        related_cited = set(_CITATION_RE.findall(related))
        citation_passed = bool(allowed) and cited <= allowed and allowed <= related_cited
        checks["citations_resolve_to_frozen_sources"] = citation_passed
        if not citation_passed:
            violations.append(
                "稿件引用必须精确解析到冻结来源，且每条冻结论文都必须出现在相关工作中"
            )

        conclusion = str(verdict.get("conclusion") or "")
        conclusion_passed = bool(conclusion) and conclusion in manuscript
        checks["frozen_conclusion_present_verbatim"] = conclusion_passed
        if not conclusion_passed:
            violations.append("完整稿未逐字保留冻结实验结论")

        numeric_passed = all(
            f"| `{item['path']}` | {item['value']} |" in manuscript
            for item in verdict.get("numeric_evidence") or []
        )
        checks["all_frozen_numeric_evidence_present"] = numeric_passed
        if not numeric_passed:
            violations.append("完整稿修改或遗漏了冻结数值证据")
        try:
            depth = audit_manuscript_depth(
                manuscript_path,
                profile="journal-article",
                report_path=root
                / "stage_4_synthesis"
                / "full_manuscript_depth.json",
            )
            depth_passed = depth.passed
            checks["journal_article_depth_passed"] = depth_passed
            if not depth_passed:
                violations.extend(depth.violations)
        except Exception as exc:
            checks["journal_article_depth_passed"] = False
            violations.append(f"完整稿深度审计执行失败：{exc}")
    else:
        checks.update(
            {
                "citations_resolve_to_frozen_sources": False,
                "frozen_conclusion_present_verbatim": False,
                "all_frozen_numeric_evidence_present": False,
                "journal_article_depth_passed": False,
                "paper_structure_contract_passed": False,
                "abstract_genre_contract_passed": False,
                "no_unresolved_artifact_callouts": False,
            }
        )
        violations.append("缺少完整论文稿")

    violations.extend(literature_violations)
    paper_ready = bool(
        plan.ready
        and manuscript_path.is_file()
        and depth_passed
        and citation_passed
        and conclusion_passed
        and numeric_passed
        and all(checks.values())
    )
    owner: Literal[
        "idea_validation",
        "evidence_packaging",
        "literature_grounding",
        "paper_writer",
        "none",
    ] = "none" if paper_ready else (plan.diagnostic_owner if not plan.ready else "paper_writer")
    publication_blockers = [
        "尚未完成全文级新颖性复核与独立人工同行评审",
        "尚未完成期刊选择、格式适配、作者确认和投稿批准",
    ]
    audit = PaperExpansionAudit(
        track_id=plan.track_id,
        passed=paper_ready,
        full_manuscript_generated=manuscript_path.is_file(),
        manuscript_depth_passed=depth_passed,
        genre_compliance_passed=genre_passed,
        citation_integrity_passed=citation_passed,
        conclusion_binding_passed=conclusion_passed,
        numeric_evidence_binding_passed=numeric_passed,
        paper_draft_ready=paper_ready,
        publication_ready=False,
        diagnostic_owner=owner,
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        publication_blockers=publication_blockers,
    )
    if persist:
        write_json_atomic(root / "stage_4_synthesis" / "paper_expansion_audit.json", audit)
    return audit


def _agent_prompt(
    root: Path,
    sources: list[LiteratureSource],
    *,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
    outline: HierarchicalPaperOutline,
) -> str:
    payload = {
        "task": "Write a complete Chinese academic manuscript from the frozen bundle. Return only the requested structured sections.",
        "scope_contract": _json(root / "stage_1_discovery" / "scope_contract.json"),
        "protocol_lock": _json(root / "stage_2_protocol" / "protocol_lock.json"),
        "idea_verdict": _json(root / "stage_3_experimentation" / "idea_verdict.json"),
        "submission_genre": genre.model_dump(mode="json"),
        "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
        "approved_hierarchical_outline": outline.model_dump(mode="json"),
        "verified_literature": [source.model_dump(mode="json") for source in sources],
        "paper_structure_contract": GENERIC_JOURNAL_ARTICLE.prompt_contract("zh"),
        "requirements": {
            "language": "Chinese",
            "minimum_han_characters": 10000,
            "citation_syntax": "[source_id]",
            "citation_rule": "Use only exact source_id values supplied above. Cite every source in related_work.",
            "evidence_rule": "Do not invent, recompute, strengthen, or suppress any result. Preserve the exact frozen conclusion in the conclusion section.",
            "section_targets_han_chars": {
                "abstract": "250-600",
                "introduction": 1200,
                "related_work": 1600,
                "methods": 2400,
                "results": 1900,
                "discussion": 1900,
                "limitations": 600,
                "conclusion": 300,
            },
            "abstract_form": GENERIC_JOURNAL_ARTICLE.abstract.prompt_contract(),
            "abstract_semantic_moves": [
                "研究背景与窄问题",
                "研究目标",
                "方法与证据边界",
                "最重要的定量结果",
                "受证据约束的结论",
            ],
            "subsections": "Use at least four ### subsections in methods, three in results, and three in discussion.",
            "integrity": "Do not fabricate references, data, authors, venues, statistical tests, or causal claims.",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _outline_prompt(
    *,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
    sources: list[LiteratureSource],
) -> str:
    payload = {
        "task": "Create the evidence-bound hierarchical outline before any manuscript prose is drafted.",
        "submission_genre": genre.model_dump(mode="json"),
        "paper_structure_contract": GENERIC_JOURNAL_ARTICLE.prompt_contract("zh"),
        "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
        "verified_literature": [source.model_dump(mode="json") for source in sources],
        "slot_policy": {
            "draft_must_leave_slots_visible": True,
            "figures_and_tables_are_rendered_after_draft_revision": True,
            "slot_data_must_come_only_from_frozen_evidence": True,
            "missing_data_action": "rollback_to_evidence_packaging; never invent or estimate",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _review_prompt(
    *,
    role: str,
    artifact: Literal["outline", "draft"],
    content: dict[str, Any],
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
) -> str:
    return json.dumps(
        {
            "task": f"Review the {artifact} independently and return only your own verdict.",
            "required_role": role,
            "required_artifact": artifact,
            "submission_genre": genre.model_dump(mode="json"),
            "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
            "artifact": content,
        },
        ensure_ascii=False,
        indent=2,
    )


async def _review_outline(
    *,
    root: Path,
    outline: HierarchicalPaperOutline,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
) -> PanelDecision:
    from .agent_runtime import review_bundle_paper_artifact

    reviews = await asyncio.gather(
        *[
            review_bundle_paper_artifact(
                _review_prompt(
                    role=role,
                    artifact="outline",
                    content=outline.model_dump(mode="json"),
                    genre=genre,
                    evidence_claim_map=evidence_claim_map,
                ),
                cwd=root,
            )
            for role in _REVIEW_ROLES
        ]
    )
    for expected_role, review in zip(_REVIEW_ROLES, reviews, strict=True):
        if review.role != expected_role or review.artifact != "outline":
            raise ValueError(
                f"outline reviewer identity mismatch: expected {expected_role}, got {review.role}/{review.artifact}"
            )
    return decide_panel("outline", list(reviews))


async def _review_draft(
    *,
    root: Path,
    draft: PaperDraftSections,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
) -> PanelDecision:
    from .agent_runtime import review_bundle_paper_artifact

    reviews = await asyncio.gather(
        *[
            review_bundle_paper_artifact(
                _review_prompt(
                    role=role,
                    artifact="draft",
                    content=draft.model_dump(mode="json"),
                    genre=genre,
                    evidence_claim_map=evidence_claim_map,
                ),
                cwd=root,
            )
            for role in _REVIEW_ROLES
        ]
    )
    for expected_role, review in zip(_REVIEW_ROLES, reviews, strict=True):
        if review.role != expected_role or review.artifact != "draft":
            raise ValueError(
                f"draft reviewer identity mismatch: expected {expected_role}, got {review.role}/{review.artifact}"
            )
    return decide_panel("draft", list(reviews))


def _draft_text(draft: PaperDraftSections) -> str:
    return "\n".join(str(value) for value in draft.model_dump(mode="json").values())


def _draft_contract_violations(
    draft: PaperDraftSections,
    *,
    outline: HierarchicalPaperOutline,
    evidence_claim_map: EvidenceClaimMap,
) -> list[str]:
    text = _draft_text(draft)
    violations = validate_abstract_prose(
        draft.abstract, GENERIC_JOURNAL_ARTICLE.abstract
    )
    allowed_sources = set(evidence_claim_map.verified_source_ids)
    unknown_sources = sorted(set(_CITATION_RE.findall(text)) - allowed_sources)
    if unknown_sources:
        violations.append("draft cites unknown sources: " + ", ".join(unknown_sources))
    if evidence_claim_map.frozen_conclusion not in draft.conclusion:
        violations.append("draft conclusion does not preserve the frozen conclusion verbatim")
    expected_callouts = {
        *(f"FIGURE:{item.slot_id}" for item in outline.figure_slots),
        *(f"TABLE:{item.slot_id}" for item in outline.table_slots),
    }
    found_callouts = {f"{kind}:{slot_id}" for kind, slot_id in _ARTIFACT_CALLOUT_RE.findall(text)}
    if expected_callouts != found_callouts:
        missing = sorted(expected_callouts - found_callouts)
        unknown = sorted(found_callouts - expected_callouts)
        if missing:
            violations.append("draft is missing artifact callouts: " + ", ".join(missing))
        if unknown:
            violations.append("draft contains unknown artifact callouts: " + ", ".join(unknown))
    return list(dict.fromkeys(violations))


def _polish_trace(
    source: PaperDraftSections,
    polished: PaperDraftSections,
    *,
    frozen_conclusion: str,
) -> PaperProsePolishTrace:
    source_text = _draft_text(source)
    polished_text = _draft_text(polished)
    numbers_ok = Counter(_NUMBER_TOKEN_RE.findall(source_text)) == Counter(
        _NUMBER_TOKEN_RE.findall(polished_text)
    )
    citations_ok = Counter(_CITATION_RE.findall(source_text)) == Counter(
        _CITATION_RE.findall(polished_text)
    )
    callouts_ok = Counter(_ARTIFACT_CALLOUT_RE.findall(source_text)) == Counter(
        _ARTIFACT_CALLOUT_RE.findall(polished_text)
    )
    conclusion_ok = frozen_conclusion in polished.conclusion
    violations: list[str] = []
    if not numbers_ok:
        violations.append("academic humanizer changed the numeric-token multiset")
    if not citations_ok:
        violations.append("academic humanizer changed the citation-key multiset")
    if not callouts_ok:
        violations.append("academic humanizer changed the figure/table callout multiset")
    if not conclusion_ok:
        violations.append("academic humanizer changed or removed the frozen conclusion")
    return PaperProsePolishTrace(
        source_sha256=_model_sha256(source),
        polished_sha256=_model_sha256(polished),
        number_multiset_preserved=numbers_ok,
        citation_multiset_preserved=citations_ok,
        artifact_callout_multiset_preserved=callouts_ok,
        frozen_conclusion_preserved=conclusion_ok,
        accepted=not violations,
        reverted=False,
        violations=violations,
    )


async def _review_revise_and_polish_draft(
    *,
    root: Path,
    draft: PaperDraftSections,
    outline: HierarchicalPaperOutline,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
) -> tuple[
    PaperDraftSections,
    PanelDecision,
    PaperRevisionTrace,
    PaperProsePolishTrace,
    PaperArtifactManifest,
]:
    from .agent_runtime import humanize_bundle_paper_draft, revise_bundle_paper_draft

    synthesis_dir = root / "stage_4_synthesis"
    draft, normalization = normalize_paper_draft(draft)
    violations = _draft_contract_violations(
        draft, outline=outline, evidence_claim_map=evidence_claim_map
    )
    if violations:
        raise ValueError("initial paper draft violates contracts: " + "; ".join(violations))
    write_json_atomic(synthesis_dir / "paper_draft_sections.initial.json", draft)
    decision = await _review_draft(
        root=root,
        draft=draft,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
    )
    write_json_atomic(synthesis_dir / "paper_draft_review.json", decision)
    if decision.decision == "halt":
        raise ValueError("draft panel halted revision because review context was insufficient")
    revised = draft
    if decision.decision == "revise":
        revised = await revise_bundle_paper_draft(
            json.dumps(
                {
                    "submission_genre": genre.model_dump(mode="json"),
                    "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
                    "approved_outline": outline.model_dump(mode="json"),
                    "draft": draft.model_dump(mode="json"),
                    "panel_decision": decision.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            cwd=root,
        )
        revised, revision_normalization = normalize_paper_draft(revised)
        normalization = PaperRevisionTrace(
            changed_fields=list(dict.fromkeys(normalization.changed_fields + revision_normalization.changed_fields)),
            operations=list(dict.fromkeys(normalization.operations + revision_normalization.operations)),
            source_sha256=_model_sha256(draft),
            revised_sha256=_model_sha256(revised),
        )
        violations = _draft_contract_violations(
            revised, outline=outline, evidence_claim_map=evidence_claim_map
        )
        if violations:
            raise ValueError("revised paper draft violates contracts: " + "; ".join(violations))
    artifact_manifest = build_paper_artifacts(
        root,
        outline=outline,
        evidence_claim_map=evidence_claim_map,
    )
    polished = await humanize_bundle_paper_draft(
        json.dumps(
            {
                "submission_genre": genre.model_dump(mode="json"),
                "approved_outline": outline.model_dump(mode="json"),
                "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
                "paper_artifact_manifest": artifact_manifest.model_dump(mode="json"),
                "approved_draft": revised.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        cwd=root,
    )
    polished, polish_normalization = normalize_paper_draft(polished)
    polish = _polish_trace(
        revised,
        polished,
        frozen_conclusion=evidence_claim_map.frozen_conclusion,
    )
    if not polish.accepted:
        polished = revised
        polish = polish.model_copy(
            update={
                "accepted": False,
                "reverted": True,
                "polished_sha256": _model_sha256(revised),
                "violations": polish.violations + ["polished output was rejected and reverted"],
            }
        )
    final_violations = _draft_contract_violations(
        polished, outline=outline, evidence_claim_map=evidence_claim_map
    )
    if final_violations:
        raise ValueError("final polished draft violates contracts: " + "; ".join(final_violations))
    if polish_normalization.operations:
        normalization = PaperRevisionTrace(
            changed_fields=list(dict.fromkeys(normalization.changed_fields + polish_normalization.changed_fields)),
            operations=list(dict.fromkeys(normalization.operations + polish_normalization.operations)),
            source_sha256=normalization.source_sha256,
            revised_sha256=_model_sha256(polished),
        )
    return polished, decision, normalization, polish, artifact_manifest


async def _prepare_reviewed_outline(
    *,
    root: Path,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
    sources: list[LiteratureSource],
) -> tuple[HierarchicalPaperOutline, PanelDecision]:
    from .agent_runtime import (
        generate_bundle_paper_outline,
        revise_bundle_paper_outline,
    )

    initial = await generate_bundle_paper_outline(
        _outline_prompt(
            genre=genre,
            evidence_claim_map=evidence_claim_map,
            sources=sources,
        ),
        cwd=root,
    )
    violations = validate_outline(
        initial,
        contract=GENERIC_JOURNAL_ARTICLE,
        evidence_claim_map=evidence_claim_map,
    )
    if violations:
        raise ValueError("initial paper outline violates contracts: " + "; ".join(violations))
    synthesis_dir = root / "stage_4_synthesis"
    write_json_atomic(synthesis_dir / "paper_outline.initial.json", initial)
    decision = await _review_outline(
        root=root,
        outline=initial,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
    )
    write_json_atomic(synthesis_dir / "paper_outline_review.json", decision)
    if decision.decision == "halt":
        raise ValueError("outline panel halted drafting because review context was insufficient")
    final = initial
    if decision.decision == "revise":
        final = await revise_bundle_paper_outline(
            json.dumps(
                {
                    "submission_genre": genre.model_dump(mode="json"),
                    "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
                    "initial_outline": initial.model_dump(mode="json"),
                    "panel_decision": decision.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            cwd=root,
        )
        violations = validate_outline(
            final,
            contract=GENERIC_JOURNAL_ARTICLE,
            evidence_claim_map=evidence_claim_map,
        )
        if violations:
            raise ValueError("revised paper outline violates contracts: " + "; ".join(violations))
    write_json_atomic(synthesis_dir / "paper_outline.json", final)
    return final, decision


async def expand_project_bundle_paper(run_dir: str | Path) -> PaperExpansionAudit:
    root = Path(run_dir).resolve()
    plan = prepare_project_bundle_paper(root, persist=True)
    if not plan.ready:
        raise ValueError(
            "完整论文补写尚未解锁："
            + "；".join(item.rstrip("。；;") for item in plan.blockers)
        )
    sources, _, _ = _literature_sources(root)
    synthesis_dir = root / "stage_4_synthesis"
    genre = load_or_create_submission_genre(root, GENERIC_JOURNAL_ARTICLE)
    evidence_claim_map = build_evidence_claim_map(
        claims=_json(synthesis_dir / "claims.json"),
        verdict=_json(root / "stage_3_experimentation" / "idea_verdict.json"),
        sources=sources,
    )
    write_json_atomic(synthesis_dir / "evidence_claim_map.json", evidence_claim_map)
    outline, _ = await _prepare_reviewed_outline(
        root=root,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
        sources=sources,
    )
    from .agent_runtime import generate_bundle_paper_draft

    draft = await generate_bundle_paper_draft(
        _agent_prompt(
            root,
            sources,
            genre=genre,
            evidence_claim_map=evidence_claim_map,
            outline=outline,
        ),
        cwd=root,
    )
    draft, _, revision_trace, polish_trace, artifact_manifest = await _review_revise_and_polish_draft(
        root=root,
        draft=draft,
        outline=outline,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
    )
    write_json_atomic(synthesis_dir / "paper_draft_sections.json", draft)
    write_json_atomic(synthesis_dir / "paper_revision_trace.json", revision_trace)
    write_json_atomic(synthesis_dir / "paper_prose_polish_trace.json", polish_trace)
    verdict = _json(root / "stage_3_experimentation" / "idea_verdict.json")
    manuscript = render_full_manuscript(
        draft,
        verdict=verdict,
        sources=sources,
        artifact_manifest=artifact_manifest,
    )
    manuscript_path = root / "stage_4_synthesis" / "full_manuscript.md"
    manuscript_path.write_text(manuscript, encoding="utf-8", newline="\n")
    latex_path = write_submission_latex(
        manuscript_path,
        contract=GENERIC_JOURNAL_ARTICLE,
        language="zh",
    )
    typesetting_report = PaperTypesettingReport(
        structure_profile_id=GENERIC_JOURNAL_ARTICLE.profile_id,
        language="zh",
        latex_path=str(latex_path.relative_to(root)).replace("\\", "/"),
        latex_sha256=sha256_file(latex_path),
        pdf_generated=False,
        blocker="PDF compilation waits for the final manuscript integrity gate.",
    )
    write_json_atomic(synthesis_dir / "paper_typesetting_report.json", typesetting_report)
    audit = _audit_full_manuscript(root, plan, persist=True)
    if audit.passed:
        try:
            from .manuscript_compile import finalize_manuscript_pdf

            compiled = finalize_manuscript_pdf(
                latex_path,
                output_dir=synthesis_dir,
                profile="journal-article",
                language="zh",
                engine="auto",
                passes=2,
                report_path=synthesis_dir / "full_manuscript.typeset.depth.json",
                manifest_path=synthesis_dir / "full_manuscript.finalization.json",
            )
            pdf_path = Path(str(compiled["pdf"])).resolve()
            typesetting_report = PaperTypesettingReport(
                structure_profile_id=GENERIC_JOURNAL_ARTICLE.profile_id,
                language="zh",
                latex_path=str(latex_path.relative_to(root)).replace("\\", "/"),
                latex_sha256=sha256_file(latex_path),
                pdf_generated=True,
                pdf_path=str(pdf_path.relative_to(root)).replace("\\", "/"),
                pdf_sha256=sha256_file(pdf_path),
                compiler=str(compiled.get("compiler") or "unknown"),
                blocker=None,
            )
        except Exception as exc:
            typesetting_report = typesetting_report.model_copy(
                update={"blocker": f"{type(exc).__name__}: {exc}"[:2000]}
            )
        write_json_atomic(synthesis_dir / "paper_typesetting_report.json", typesetting_report)
    certificate = PaperCompletionCertificate(
        track_id=plan.track_id,
        paper_draft_ready=audit.paper_draft_ready,
        publication_ready=audit.publication_ready,
        manuscript_sha256=sha256_file(manuscript_path),
        genre_sha256=sha256_file(synthesis_dir / "submission_genre.json"),
        evidence_claim_map_sha256=sha256_file(synthesis_dir / "evidence_claim_map.json"),
        outline_sha256=sha256_file(synthesis_dir / "paper_outline.json"),
        outline_review_sha256=sha256_file(synthesis_dir / "paper_outline_review.json"),
        draft_review_sha256=sha256_file(synthesis_dir / "paper_draft_review.json"),
        revision_trace_sha256=sha256_file(synthesis_dir / "paper_revision_trace.json"),
        prose_polish_trace_sha256=sha256_file(synthesis_dir / "paper_prose_polish_trace.json"),
        artifact_manifest_sha256=sha256_file(synthesis_dir / "paper_artifact_manifest.json"),
        typeset_source_sha256=sha256_file(latex_path),
        typesetting_report_sha256=sha256_file(synthesis_dir / "paper_typesetting_report.json"),
        plan_sha256=sha256_file(root / "stage_4_synthesis" / "paper_expansion_plan.json"),
        audit_sha256=sha256_file(root / "stage_4_synthesis" / "paper_expansion_audit.json"),
    )
    write_json_atomic(
        root / "stage_4_synthesis" / "paper_completion_certificate.json", certificate
    )
    return audit


def verify_project_bundle_paper(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    try:
        certificate = PaperCompletionCertificate.model_validate(
            read_json(root / "stage_4_synthesis" / "paper_completion_certificate.json")
        )
        plan = PaperExpansionPlan.model_validate(
            read_json(root / "stage_4_synthesis" / "paper_expansion_plan.json")
        )
    except Exception as exc:
        return {"passed": False, "violations": [f"论文证书无效：{exc}"]}
    paths = {
        "manuscript": root / "stage_4_synthesis" / "full_manuscript.md",
        "genre": root / "stage_4_synthesis" / "submission_genre.json",
        "evidence_claim_map": root / "stage_4_synthesis" / "evidence_claim_map.json",
        "outline": root / "stage_4_synthesis" / "paper_outline.json",
        "outline_review": root / "stage_4_synthesis" / "paper_outline_review.json",
        "draft_review": root / "stage_4_synthesis" / "paper_draft_review.json",
        "revision_trace": root / "stage_4_synthesis" / "paper_revision_trace.json",
        "prose_polish_trace": root / "stage_4_synthesis" / "paper_prose_polish_trace.json",
        "artifact_manifest": root / "stage_4_synthesis" / "paper_artifact_manifest.json",
        "typeset_source": root / "stage_4_synthesis" / "full_manuscript.tex",
        "typesetting_report": root / "stage_4_synthesis" / "paper_typesetting_report.json",
        "plan": root / "stage_4_synthesis" / "paper_expansion_plan.json",
        "audit": root / "stage_4_synthesis" / "paper_expansion_audit.json",
    }
    expected = {
        "manuscript": certificate.manuscript_sha256,
        "genre": certificate.genre_sha256,
        "evidence_claim_map": certificate.evidence_claim_map_sha256,
        "outline": certificate.outline_sha256,
        "outline_review": certificate.outline_review_sha256,
        "draft_review": certificate.draft_review_sha256,
        "revision_trace": certificate.revision_trace_sha256,
        "prose_polish_trace": certificate.prose_polish_trace_sha256,
        "artifact_manifest": certificate.artifact_manifest_sha256,
        "typeset_source": certificate.typeset_source_sha256,
        "typesetting_report": certificate.typesetting_report_sha256,
        "plan": certificate.plan_sha256,
        "audit": certificate.audit_sha256,
    }
    violations = [
        f"已认证论文产物发生变化或缺失：{name}"
        for name, path in paths.items()
        if not path.is_file() or sha256_file(path) != expected[name]
    ]
    audit = _audit_full_manuscript(root, plan, persist=False)
    if not audit.passed:
        violations.extend(audit.violations)
    return {
        "passed": not violations,
        "paper_draft_ready": audit.paper_draft_ready and not violations,
        "publication_ready": audit.publication_ready and not violations,
        "diagnostic_owner": audit.diagnostic_owner,
        "violations": list(dict.fromkeys(violations)),
    }
