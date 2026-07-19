from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .manuscript_depth import audit_manuscript_depth
from .models import LiteratureSource, LiteratureSourceType, StrictModel, utc_now
from .storage import read_json, sha256_file, write_json_atomic


MIN_VERIFIED_PAPERS = 15
MIN_NUMERIC_EVIDENCE = 15
_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]{1,79})\]")


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
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    audit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


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
) -> str:
    return (
        f"# {draft.title}\n\n"
        "> 状态：证据约束的完整论文候选稿。想法判定来自冻结实验产物，引用和数值表由本地审计器生成。\n\n"
        f"## 摘要\n\n{draft.abstract}\n\n"
        f"## 引言\n\n{draft.introduction}\n\n"
        f"## 相关工作\n\n{draft.related_work}\n\n"
        f"## 方法\n\n{draft.methods}\n\n"
        f"## 结果\n\n{draft.results}\n\n{_numeric_table(verdict)}\n\n"
        f"## 讨论\n\n{draft.discussion}\n\n"
        f"## 局限性\n\n{draft.limitations}\n\n"
        f"## 结论\n\n{draft.conclusion}\n\n"
        f"## 数据与材料可得性\n\n{draft.data_availability}\n\n"
        f"## 伦理声明\n\n{draft.ethics_statement}\n\n"
        f"## 作者贡献\n\n{draft.author_contributions}\n\n"
        f"## 利益冲突\n\n{draft.conflict_of_interest}\n\n"
        f"## 资助声明\n\n{draft.funding}\n\n"
        f"## AI 使用披露\n\n{draft.ai_disclosure}\n\n"
        f"## 参考文献\n\n{_references(sources)}\n"
    )


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
    }
    violations: list[str] = []
    depth_passed = False
    citation_passed = False
    conclusion_passed = False
    numeric_passed = False
    if manuscript_path.is_file():
        manuscript = manuscript_path.read_text(encoding="utf-8")
        allowed = {source.source_id for source in sources}
        cited = set(_CITATION_RE.findall(manuscript))
        related_match = re.search(
            r"(?s)^## 相关工作\s*$\s*(.*?)(?=^##\s+)", manuscript, re.MULTILINE
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


def _agent_prompt(root: Path, sources: list[LiteratureSource]) -> str:
    payload = {
        "task": "Write a complete Chinese academic manuscript from the frozen bundle. Return only the requested structured sections.",
        "scope_contract": _json(root / "stage_1_discovery" / "scope_contract.json"),
        "protocol_lock": _json(root / "stage_2_protocol" / "protocol_lock.json"),
        "idea_verdict": _json(root / "stage_3_experimentation" / "idea_verdict.json"),
        "claim_registry": _json(root / "stage_4_synthesis" / "claims.json"),
        "verified_literature": [source.model_dump(mode="json") for source in sources],
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
            "subsections": "Use at least four ### subsections in methods, three in results, and three in discussion.",
            "integrity": "Do not fabricate references, data, authors, venues, statistical tests, or causal claims.",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def expand_project_bundle_paper(run_dir: str | Path) -> PaperExpansionAudit:
    root = Path(run_dir).resolve()
    plan = prepare_project_bundle_paper(root, persist=True)
    if not plan.ready:
        raise ValueError(
            "完整论文补写尚未解锁："
            + "；".join(item.rstrip("。；;") for item in plan.blockers)
        )
    sources, _, _ = _literature_sources(root)
    from .agent_runtime import generate_bundle_paper_draft

    draft = await generate_bundle_paper_draft(_agent_prompt(root, sources), cwd=root)
    verdict = _json(root / "stage_3_experimentation" / "idea_verdict.json")
    manuscript = render_full_manuscript(draft, verdict=verdict, sources=sources)
    manuscript_path = root / "stage_4_synthesis" / "full_manuscript.md"
    manuscript_path.write_text(manuscript, encoding="utf-8", newline="\n")
    audit = _audit_full_manuscript(root, plan, persist=True)
    certificate = PaperCompletionCertificate(
        track_id=plan.track_id,
        paper_draft_ready=audit.paper_draft_ready,
        publication_ready=audit.publication_ready,
        manuscript_sha256=sha256_file(manuscript_path),
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
        "plan": root / "stage_4_synthesis" / "paper_expansion_plan.json",
        "audit": root / "stage_4_synthesis" / "paper_expansion_audit.json",
    }
    expected = {
        "manuscript": certificate.manuscript_sha256,
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
