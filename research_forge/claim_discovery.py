"""Auditable, two-channel claim discovery for local project bundles.

External trend signals recommend questions worth testing.  Project-authored
statements record what the project says it achieved.  Neither channel is
scientific evidence; evidence eligibility remains owned by later Research
Forge stages.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence
from xml.etree import ElementTree

from pydantic import Field

from .models import StrictModel
from .pdf_materials import extract_pdf_material


REDFOX_API_URL = "https://redfox.hk/story/api/gzhData/searchArticle"
REDFOX_SOURCE_TAG = "A股科技雷达-redfox-v1"
SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_API_URL = "https://api.crossref.org/works"

_CLAIM_HEADINGS = {
    "结论",
    "一句话结论",
    "最终判断",
    "研究发现",
    "主要发现",
    "贡献",
    "主要贡献",
    "创新点",
    "conclusion",
    "conclusions",
    "findings",
    "contributions",
    "novelty",
    "results summary",
}
_CLAIM_KEYS = {
    "conclusion",
    "conclusions",
    "finding",
    "findings",
    "contribution",
    "contributions",
    "noveltyclaim",
    "novelty_claim",
    "resultsummary",
    "result_summary",
    "researchquestion",
    "research_question",
    "hypothesis",
}
_PHRASE_CUES = (
    "极端赢家",
    "灵活退出",
    "退出信号",
    "择时稳健",
    "量化交易",
    "质量筛选",
    "组合回测",
    "因果选题",
    "科研智能体",
    "自主科研",
    "证据门控",
    "claim evidence",
    "flexible exit",
    "extreme winner",
    "portfolio",
    "robustness",
    "drawdown",
    "ablation",
)
_STOPWORDS = {
    "about", "after", "also", "and", "are", "from", "into", "that", "the",
    "this", "through", "using", "with", "研究", "项目", "系统", "结果", "方法",
    "分析", "报告", "结论", "我们", "可以", "是否", "一个", "一种",
}
_CANONICAL_CONCEPTS = {
    "flexible_exit", "exit_signal", "drawdown", "portfolio", "return",
    "robustness", "timing", "extreme_winner", "quality", "baseline",
    "causal", "claim_evidence", "right_tail", "ranking_model", "catalyst",
    "random_baseline", "concentration", "cross_sectional",
    "rare_high_return", "learning_to_rank", "top_k", "walk_forward",
    "motor_rotor", "surface_mounted", "manufacturing_process",
    "reliability",
}
_DISTINCTIVE_CONCEPTS = _CANONICAL_CONCEPTS - {"portfolio", "return", "baseline"}


class SourceSpan(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    line: int | None = Field(default=None, ge=1)
    section: str = ""


class AuthorClaim(StrictModel):
    claim_id: str
    statement: str
    claim_type: Literal[
        "research_question", "method", "result", "comparative", "robustness",
        "novelty", "limitation", "unspecified"
    ]
    origin: Literal["author_asserted"] = "author_asserted"
    source_spans: list[SourceSpan] = Field(min_length=1)
    evidence_status: Literal["author_statement_only"] = "author_statement_only"


class TrendSignal(StrictModel):
    signal_id: str
    provider: str = Field(min_length=2, max_length=100)
    signal_class: Literal[
        "market_attention",
        "scholarly_attention",
        "official_source",
        "adoption_signal",
    ]
    query: str
    title: str
    summary: str = ""
    url: str
    published_at: str = ""
    source_name: str = ""
    engagement: dict[str, int] = Field(default_factory=dict)
    terms: list[str] = Field(default_factory=list)
    trend_score: float = Field(ge=0.0, le=1.0)
    scientific_density: float = Field(ge=0.0, le=1.0)
    evidence_role: Literal["attention_only"] = "attention_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcademicConceptNormalization(StrictModel):
    normalization_id: str
    source_track_id: str
    internal_label: str
    operational_definition: str = ""
    academic_title: str = ""
    academic_concepts: list[str] = Field(default_factory=list)
    academic_query_terms: list[str] = Field(default_factory=list)
    comparison_frame: dict[str, Any] = Field(default_factory=dict)
    source_paths: list[str] = Field(default_factory=list)
    status: Literal["normalized", "needs_owner_review"]
    warnings: list[str] = Field(default_factory=list)


class ProjectResearchFingerprint(StrictModel):
    domains: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    evidence_assets: list[str] = Field(default_factory=list)
    source_track_ids: list[str] = Field(default_factory=list)
    academic_normalizations: list[AcademicConceptNormalization] = Field(
        default_factory=list
    )


class RecommendedClaim(StrictModel):
    claim_id: str
    statement: str
    origin: Literal["trend_and_author", "trend_recommended", "author_asserted"]
    recommendation_score: int = Field(ge=0, le=100)
    trend_score: int = Field(ge=0, le=100)
    project_match_score: int = Field(ge=0, le=100)
    evidence_readiness_score: int = Field(ge=0, le=100)
    matched_signal_ids: list[str] = Field(default_factory=list)
    source_claim_ids: list[str] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)
    local_evidence_paths: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    status: Literal["recommended_for_validation"] = "recommended_for_validation"
    scientific_evidence_status: Literal["not_yet_validated"] = "not_yet_validated"


class ClaimDiscoveryReport(StrictModel):
    schema_version: int = 1
    generated_at: str
    source_root: str
    fingerprint: ProjectResearchFingerprint
    author_claims: list[AuthorClaim]
    trend_signals: list[TrendSignal]
    recommended_claims: list[RecommendedClaim]
    provider_status: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    integrity_rule: str = (
        "Trend signals rank validation opportunities and never count as scientific evidence."
    )


class DiscoveryQueryIntent(StrictModel):
    query_id: str
    purpose: Literal[
        "closest_prior_work",
        "method_and_baseline",
        "contradicting_evidence",
        "recent_trend",
        "dataset_and_model",
    ]
    query: str = Field(min_length=3, max_length=800)
    source_claim_ids: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)


class ClaimSourceMatch(StrictModel):
    match_id: str
    claim_id: str
    source_signal_id: str
    relation: Literal[
        "closest_prior_work",
        "method_or_baseline",
        "conflicting_context",
        "supporting_context",
        "attention_signal",
    ]
    match_score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    evidence_role: Literal["background_only", "attention_only"]
    verdict_authority: Literal[False] = False


_DISCOVERY_STRENGTH = Literal["strong", "moderate", "limited", "unassessed"]


class DiscoveryDirection(StrictModel):
    direction_id: str
    title: str
    research_question: str
    falsifiable_hypothesis: str
    candidate_contribution: str
    scope_in: list[str] = Field(min_length=1)
    scope_out: list[str] = Field(min_length=1)
    source_claim_ids: list[str] = Field(default_factory=list)
    supporting_track_ids: list[str] = Field(default_factory=list)
    primary_track_id: str | None = None
    local_evidence_paths: list[str] = Field(default_factory=list)
    external_source_ids: list[str] = Field(default_factory=list)
    closest_prior_work_ids: list[str] = Field(default_factory=list)
    conflicting_source_ids: list[str] = Field(default_factory=list)
    trend_signal_ids: list[str] = Field(default_factory=list)
    relation_to_prior_work: str
    novelty_grounding: _DISCOVERY_STRENGTH
    evidence_readiness: _DISCOVERY_STRENGTH
    feasibility: _DISCOVERY_STRENGTH
    external_attention: _DISCOVERY_STRENGTH
    evidence_chain_level: Literal["verified_chain", "inferred_chain"]
    recommendation_reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    internal_label: str = ""
    operational_definition: str = ""
    academic_concepts: list[str] = Field(default_factory=list)
    academic_query_terms: list[str] = Field(default_factory=list)
    comparison_frame: dict[str, Any] = Field(default_factory=dict)
    academic_normalization_status: Literal[
        "normalized", "needs_owner_review"
    ] = "needs_owner_review"
    scientific_evidence_status: Literal["discovery_only"] = "discovery_only"


class DiscoveryPortfolio(StrictModel):
    schema_version: int = 1
    generated_at: str
    fingerprint: ProjectResearchFingerprint
    query_plan_id: str | None = None
    query_intents: list[DiscoveryQueryIntent]
    claim_source_matches: list[ClaimSourceMatch]
    directions: list[DiscoveryDirection]
    recommended_direction_id: str | None = None
    resource_set_ids: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    status: Literal[
        "ready_for_scope_selection",
        "external_grounding_incomplete",
        "no_viable_direction",
    ]
    integrity_rule: str = (
        "External literature and trend signals ground and rank discovery "
        "directions; they cannot decide a scientific verdict."
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _resource_value(resource: Any, name: str, default: Any = None) -> Any:
    if isinstance(resource, dict):
        return resource.get(name, default)
    return getattr(resource, name, default)


def _candidate_value(candidate: Any, name: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


def _clean_statement(value: str) -> str:
    value = re.sub(r"^\s*(?:[-*+] |\d+[.)]\s+)", "", value.strip())
    value = re.sub(r"!?(?:\[([^\]]+)\])\([^\)]+\)", r"\1", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" `#*\t")[:1200]


def _natural_archive_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(item) if item.isdigit() else item.casefold()
        for item in re.split(r"(\d+)", value)
    )


def _xml_text_values(
    archive: zipfile.ZipFile,
    member: str,
    *,
    accepted_tags: set[str],
    limit: int,
) -> list[str]:
    values: list[str] = []
    length = 0
    with archive.open(member) as handle:
        for _, element in ElementTree.iterparse(handle, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in accepted_tags and element.text:
                value = re.sub(r"\s+", " ", element.text).strip()
                if value:
                    values.append(value)
                    length += len(value) + 1
            element.clear()
            if length >= limit:
                break
    return values


def extract_office_text(path: Path, *, limit: int = 500_000) -> str:
    """Extract bounded text from OOXML without executing macros or formulas."""

    if path.suffix.casefold() not in {".docx", ".pptx", ".xlsx"}:
        return ""
    try:
        with zipfile.ZipFile(path) as archive:
            members = set(archive.namelist())
            parts = [f"# {path.stem}"]
            remaining = max(0, limit - len(parts[0]))
            if path.suffix.casefold() == ".docx":
                member = "word/document.xml"
                if member in members:
                    parts.extend(
                        _xml_text_values(
                            archive,
                            member,
                            accepted_tags={"t"},
                            limit=remaining,
                        )
                    )
            elif path.suffix.casefold() == ".pptx":
                slides = sorted(
                    (
                        item
                        for item in members
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", item)
                    ),
                    key=_natural_archive_key,
                )
                for index, member in enumerate(slides, start=1):
                    if sum(len(item) + 1 for item in parts) >= limit:
                        break
                    parts.append(f"## Slide {index}")
                    parts.extend(
                        _xml_text_values(
                            archive,
                            member,
                            accepted_tags={"t"},
                            limit=max(
                                0,
                                limit
                                - sum(len(item) + 1 for item in parts),
                            ),
                        )
                    )
            else:
                shared: list[str] = []
                if "xl/sharedStrings.xml" in members:
                    shared = _xml_text_values(
                        archive,
                        "xl/sharedStrings.xml",
                        accepted_tags={"t"},
                        limit=min(limit, 300_000),
                    )
                sheets = sorted(
                    (
                        item
                        for item in members
                        if re.fullmatch(
                            r"xl/worksheets/sheet\d+\.xml", item
                        )
                    ),
                    key=_natural_archive_key,
                )
                for index, member in enumerate(sheets, start=1):
                    current_length = sum(len(item) + 1 for item in parts)
                    if current_length >= limit:
                        break
                    parts.append(f"## Sheet {index}")
                    current_length += len(parts[-1]) + 1
                    with archive.open(member) as handle:
                        for _, cell in ElementTree.iterparse(
                            handle, events=("end",)
                        ):
                            if cell.tag.rsplit("}", 1)[-1] != "c":
                                continue
                            cell_type = cell.attrib.get("t", "")
                            value = ""
                            for child in cell.iter():
                                local = child.tag.rsplit("}", 1)[-1]
                                if local in {"v", "t"} and child.text:
                                    value = child.text.strip()
                                    if value:
                                        break
                            if cell_type == "s" and value.isdigit():
                                position = int(value)
                                value = (
                                    shared[position]
                                    if position < len(shared)
                                    else ""
                                )
                            if value:
                                parts.append(value)
                                current_length += len(value) + 1
                            cell.clear()
                            if current_length >= limit:
                                break
            return "\n".join(parts)[:limit]
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return ""


def _claim_type(statement: str) -> str:
    lower = statement.casefold()
    if statement.endswith(("?", "？")):
        return "research_question"
    if any(item in lower for item in ("局限", "不能", "尚未", "limitation", "cannot")):
        return "limitation"
    if any(item in lower for item in ("首个", "首次", "新颖", "novel", "first")):
        return "novelty"
    if any(item in lower for item in ("稳健", "跨周期", "robust", "stable")):
        return "robustness"
    if any(item in lower for item in ("优于", "提高", "降低", "改善", "outperform", "improv", "reduc")):
        return "comparative"
    if any(item in lower for item in ("结果", "显示", "发现", "达到", "result", "achiev")):
        return "result"
    if any(item in lower for item in ("使用", "实现", "方法", "采用", "model", "method", "implement")):
        return "method"
    return "unspecified"


def _is_procedural_statement(statement: str) -> bool:
    lower = statement.casefold().lstrip(" .,:;，。；：")
    if re.match(
        r"^(第[一二三四五六七八九十]+步|"
        r"the\s+(first|second|third|fourth|fifth)\s+step)",
        lower,
    ):
        return True
    if lower.startswith(
        (
            "员工需",
            "employees need",
            "employees must",
            "使用设备",
            "use equipment",
            "作业要领",
            "operational guidelines",
            "操作方法",
            "operational approach",
            "确认方法",
            "confirmation method",
        )
    ):
        return True
    if re.match(
        r"^(summary report|汇报人|document number|文件编号|"
        r"motor model|马达型号)",
        lower,
    ):
        return True
    return False


def _claims_from_text(
    text: str,
    relative: str,
    sha256: str,
    *,
    allow_typed_lines: bool = False,
) -> list[AuthorClaim]:
    lines = text.splitlines()
    active_heading = ""
    active_level = 7
    found: list[AuthorClaim] = []
    research_bearing_path = any(
        cue in relative.casefold()
        for cue in (
            "研究",
            "实验",
            "评测",
            "评价",
            "结论",
            "报告",
            "research",
            "experiment",
            "evaluation",
            "result",
            "report",
            "protocol",
        )
    )
    nonresearch_corpus_path = any(
        cue in relative.casefold()
        for cue in (
            "提示词",
            "关键词",
            "prompt library",
            "prompt corpus",
            "注册教程",
            "无限积分",
            "去水印",
            "临时邮箱",
            "account registration",
            "watermark removal",
        )
    )
    for index, raw in enumerate(lines, start=1):
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if title.casefold() in _CLAIM_HEADINGS:
                active_heading, active_level = title, level
            elif level <= active_level:
                active_heading, active_level = "", 7
            continue
        inline = re.match(
            r"^\s*(?:结论|主要发现|贡献|创新点|conclusion|finding|contribution)\s*[:：]\s*(.+)$",
            raw,
            re.IGNORECASE,
        )
        statement = _clean_statement(inline.group(1) if inline else raw)
        if not statement or len(statement) < 12 or len(statement) > 1200:
            continue
        if statement.endswith((":", "：")):
            continue
        if _is_procedural_statement(statement):
            continue
        if nonresearch_corpus_path and not inline and not active_heading:
            continue
        if (
            not inline
            and not active_heading
            and not (
                allow_typed_lines
                and research_bearing_path
                and _claim_type(statement) != "unspecified"
            )
        ):
            continue
        if statement.startswith(("|", "```", "http://", "https://")):
            continue
        claim_id = _stable_id("author-claim", f"{relative}:{index}:{statement.casefold()}")
        found.append(
            AuthorClaim(
                claim_id=claim_id,
                statement=statement,
                claim_type=_claim_type(statement),  # type: ignore[arg-type]
                source_spans=[SourceSpan(path=relative, sha256=sha256, line=index, section=active_heading)],
            )
        )
    return found


def _markdown_claims(
    path: Path, relative: str, sha256: str
) -> list[AuthorClaim]:
    text = path.read_text(encoding="utf-8", errors="replace")[:500_000]
    return _claims_from_text(text, relative, sha256)


def _json_claims(path: Path, relative: str, sha256: str) -> list[AuthorClaim]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    found: list[AuthorClaim] = []
    is_research_contract = path.name.casefold() == "research_contract.json"
    composite_added = False
    if is_research_contract and isinstance(payload, dict):
        title = _clean_statement(str(payload.get("title") or ""))
        hypothesis = _clean_statement(str(payload.get("hypothesis") or ""))
        if (
            title
            and hypothesis
            and len(hypothesis) <= 700
            and not hypothesis.lstrip().startswith("#")
        ):
            statement = f"{title}: {hypothesis}"
            found.append(
                AuthorClaim(
                    claim_id=_stable_id(
                        "author-claim",
                        f"{relative}:title+hypothesis:{statement.casefold()}",
                    ),
                    statement=statement,
                    claim_type=_claim_type(statement),  # type: ignore[arg-type]
                    source_spans=[
                        SourceSpan(
                            path=relative,
                            sha256=sha256,
                            section="title+hypothesis",
                        )
                    ],
                )
            )
            composite_added = True

    def embedded_task_brief(statement: str) -> bool:
        lower = statement.casefold()
        return len(statement) > 700 or (
            ("## task description" in lower or "# overview" in lower)
            and any(
                cue in lower
                for cue in (
                    "dataset structure",
                    "submission file",
                    "evaluation criteria",
                )
            )
        )

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                item_path = f"{prefix}.{key}" if prefix else str(key)
                if str(key).casefold() in _CLAIM_KEYS and isinstance(item, str):
                    statement = _clean_statement(item)
                    duplicate_contract_hypothesis = (
                        is_research_contract
                        and composite_added
                        and str(key).casefold() == "hypothesis"
                    )
                    if (
                        len(statement) >= 12
                        and not embedded_task_brief(statement)
                        and not duplicate_contract_hypothesis
                    ):
                        found.append(
                            AuthorClaim(
                                claim_id=_stable_id("author-claim", f"{relative}:{item_path}:{statement.casefold()}"),
                                statement=statement,
                                claim_type=_claim_type(statement),  # type: ignore[arg-type]
                                source_spans=[SourceSpan(path=relative, sha256=sha256, section=item_path)],
                            )
                        )
                walk(item, item_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{prefix}[{index}]")

    walk(payload)
    return found


def extract_author_claims(source_root: Path, resources: Sequence[Any]) -> list[AuthorClaim]:
    candidates: list[AuthorClaim] = []
    for resource in resources:
        relative = str(_resource_value(resource, "path", ""))
        suffix = str(_resource_value(resource, "suffix", Path(relative).suffix)).casefold()
        size = int(_resource_value(resource, "size_bytes", 0) or 0)
        size_limit = (
            100 * 1024 * 1024
            if suffix in {".docx", ".pdf", ".pptx", ".xlsx"}
            else 500_000
        )
        if not relative or size > size_limit:
            continue
        path = source_root / relative
        digest = str(_resource_value(resource, "sha256", ""))
        if suffix in {".md", ".txt"}:
            candidates.extend(_markdown_claims(path, relative, digest))
        elif suffix in {".docx", ".pptx", ".xlsx"}:
            office_text = extract_office_text(path, limit=500_000)
            candidates.extend(
                _claims_from_text(
                    office_text,
                    relative,
                    digest,
                    allow_typed_lines=True,
                )
            )
        elif suffix == ".pdf":
            pdf = extract_pdf_material(path, limit=500_000)
            if pdf["text_status"] == "extractable":
                candidates.extend(
                    _claims_from_text(
                        str(pdf["text"]),
                        relative,
                        digest,
                        allow_typed_lines=True,
                    )
                )
        elif suffix == ".json":
            candidates.extend(_json_claims(path, relative, digest))
    merged: dict[str, AuthorClaim] = {}
    for claim in candidates:
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", claim.statement.casefold())
        existing = merged.get(key)
        if existing is None:
            merged[key] = claim
        else:
            existing.source_spans.extend(claim.source_spans)
    return list(merged.values())[:80]


def _terms(text: str) -> set[str]:
    lower = text.casefold()
    terms = {
        item for item in re.findall(r"[a-z][a-z0-9-]{2,}", lower)
        if item not in _STOPWORDS
    }
    terms.update(cue.casefold() for cue in _PHRASE_CUES if cue.casefold() in lower)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if chunk in _STOPWORDS:
            continue
        if len(chunk) <= 8:
            terms.add(chunk)
        for size in (2, 3, 4):
            terms.update(chunk[index:index + size] for index in range(max(0, len(chunk) - size + 1)))
    concepts = {
        "flexible_exit": ("灵活退出", "动态退出", "flexible exit", "adaptive exit"),
        "exit_signal": ("退出信号", "exit signal", "exit strategy", "exit rule"),
        "drawdown": ("回撤", "drawdown"),
        "portfolio": ("组合", "portfolio"),
        "return": ("收益", "return"),
        "robustness": ("稳健", "robust", "stability"),
        "timing": ("择时", "market timing"),
        "extreme_winner": ("极端赢家", "extreme winner"),
        "quality": ("质量", "quality factor", "quality filter"),
        "baseline": ("基线", "baseline"),
        "causal": ("因果", "causal"),
        "claim_evidence": ("主张证据", "证据门控", "claim evidence"),
        "right_tail": ("右尾", "正偏", "right tail", "right-tail", "skewness"),
        "ranking_model": ("lambdamart", "learning to rank", "ranking model"),
        "catalyst": ("催化剂", "catalyst"),
        "random_baseline": ("随机基线", "random baseline", "random portfolio"),
        "concentration": ("集中", "concentration", "portfolio size"),
        "cross_sectional": ("横截面", "cross-sectional", "cross sectional"),
        "rare_high_return": (
            "稀有高收益",
            "高收益事件",
            "rare high-return",
            "rare high return",
        ),
        "learning_to_rank": (
            "学习排序",
            "learning-to-rank",
            "learning to rank",
            "lambdamart",
        ),
        "top_k": ("top-k", "top k", "top5", "top 5"),
        "walk_forward": (
            "滚动窗口",
            "前瞻评估",
            "walk-forward",
            "walk forward",
        ),
        "motor_rotor": (
            "电机转子",
            "内转子",
            "motor rotor",
            "rotor topology",
        ),
        "surface_mounted": (
            "表贴式",
            "表贴结构",
            "surface-mounted",
            "surface mounted",
        ),
        "manufacturing_process": (
            "制造工艺",
            "工艺优化",
            "manufacturing process",
            "process optimization",
        ),
        "reliability": ("可靠性", "reliability"),
    }
    terms.update(
        concept
        for concept, aliases in concepts.items()
        if any(alias in lower for alias in aliases)
    )
    return terms


def _candidate_source_text(
    source_root: Path | None, candidate: Any
) -> tuple[str, list[str]]:
    parts = [
        str(_candidate_value(candidate, "display_title", "") or ""),
        str(_candidate_value(candidate, "novelty_seed", "") or ""),
        str(_candidate_value(candidate, "conclusion_excerpt", "") or ""),
        str(_candidate_value(candidate, "output_path", "") or ""),
        " ".join(
            str(item)
            for item in _candidate_value(
                candidate, "implementation_paths", []
            )
        ),
    ]
    source_paths: list[str] = []
    for field in ("protocol_path", "report_path"):
        relative = str(_candidate_value(candidate, field, "") or "").strip()
        if not relative:
            continue
        source_paths.append(relative)
        if source_root is None:
            continue
        path = source_root / relative
        try:
            if path.is_file() and path.suffix.casefold() == ".pdf":
                parts.append(
                    str(
                        extract_pdf_material(
                            path, limit=300_000
                        )["text"]
                    )
                )
            elif path.is_file() and path.stat().st_size <= 500_000:
                parts.append(
                    path.read_text(encoding="utf-8", errors="replace")
                )
        except OSError:
            continue
    return "\n".join(parts), source_paths


def _high_return_operational_definition(text: str) -> str:
    for raw_line in text.splitlines():
        line = re.sub(r"[`*_#]+", "", raw_line).strip(" -：:")
        lower = line.casefold()
        has_horizon = bool(
            re.search(r"(未来|future)\s*20", lower)
            or re.search(r"20\s*(个)?(交易日|trading days?)", lower)
        )
        has_relative_threshold = bool(
            re.search(
                r"(行业|industry).{0,40}(前|top).{0,20}10\s*%", lower
            )
        )
        has_absolute_threshold = bool(
            re.search(r"(绝对|absolute).{0,40}10\s*%", lower)
        )
        if has_horizon and has_relative_threshold and has_absolute_threshold:
            definition = re.split(r"(?<=[。.!?])\s*", line, maxsplit=1)[0]
            return re.sub(
                r"^(主要事件定义继续沿用冻结版|目标固定为|定义|标签定义)"
                r"\s*[：:]\s*",
                "",
                definition,
            )[:600]
    return ""


def _markdown_section(text: str, headings: Sequence[str]) -> str:
    """Return a bounded Markdown section without treating prose as authority."""

    wanted = {item.casefold().strip() for item in headings}
    lines = text.splitlines()
    captured: list[str] = []
    active = False
    for line in lines:
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            heading = re.sub(r"[`*_]+", "", match.group(1)).casefold().strip()
            if active:
                break
            active = heading in wanted
            continue
        if active and line.strip():
            captured.append(line.strip())
    return "\n".join(captured)[:12_000]


def _generic_comparative_normalization(
    text: str, internal_label: str
) -> dict[str, Any] | None:
    """Abstract an engineering brief into a cautious scholarly comparison.

    This path is deliberately project-name agnostic.  It requires explicit
    baseline, problem, and target sections and will abstain when the material
    does not expose a comparator, intervention, and measurable outcome.
    """

    baseline = _markdown_section(
        text, ("baseline", "current state", "现状", "当前方案", "基线")
    )
    problem = _markdown_section(
        text, ("problem", "gap", "问题", "现存问题", "研究缺口")
    )
    target = _markdown_section(
        text, ("target path", "target", "proposed change", "目标方案", "目标路径")
    )
    if not all((baseline, problem, target)):
        return None

    joined = "\n".join((baseline, problem, target)).casefold()
    domain_title = ""
    domain_concepts: list[str] = []
    domain_query = ""
    if any(
        cue in joined
        for cue in (
            "professor",
            "academic advisor",
            "phd advisor",
            "导师",
            "研究生指导",
        )
    ):
        domain_title = "学术导师推荐"
        domain_concepts = [
            "academic advisor recommendation",
            "academic recommender systems",
            "expert finding",
        ]
        domain_query = "academic advisor recommender systems expert finding"
    elif any(
        cue in joined
        for cue in ("recommendation", "recommender", "ranking", "推荐", "排序")
    ):
        domain_title = "推荐与排序系统"
        domain_concepts = [
            "recommender systems",
            "ranking systems",
        ]
        domain_query = "recommender systems ranking empirical evaluation"
    elif any(
        cue in joined
        for cue in ("retrieval", "search", "information access", "检索", "搜索")
    ):
        domain_title = "信息检索系统"
        domain_concepts = ["information retrieval", "search systems"]
        domain_query = "information retrieval system empirical evaluation"
    else:
        return None

    interventions: list[str] = []
    intervention_titles: list[str] = []
    if (
        any(cue in joined for cue in ("dimension", "维度"))
        and any(cue in joined for cue in ("retrieval", "search", "检索", "搜索"))
        and any(cue in joined for cue in ("mandatory", "persist", "强制", "持久"))
    ):
        interventions.append("mandatory multi-dimensional evidence retrieval")
        intervention_titles.append("强制多维证据检索")
    if (
        "deterministic" in joined or "确定性" in joined
    ) and any(cue in joined for cue in ("evidence ledger", "scoring", "证据账本", "评分")):
        interventions.append("deterministic evidence-ledger scoring")
        intervention_titles.append("确定性证据账本评分")
    if not interventions:
        return None

    outcomes: list[str] = []
    outcome_titles: list[str] = []
    problem_lower = problem.casefold()
    if any(cue in problem_lower for cue in ("coverage", "覆盖")):
        outcomes.append("evidence-dimension coverage rate")
        outcome_titles.append("证据维度覆盖率")
    if any(
        cue in problem_lower
        for cue in ("asymmetric", "consistency", "不一致", "不对称")
    ):
        outcomes.append("execution-path consistency")
        outcome_titles.append("执行路径一致性")
    if any(
        cue in joined
        for cue in ("unsupported claim", "unsupported score", "无证据", "不受支持")
    ):
        outcomes.append("unsupported scoring-claim rate")
        outcome_titles.append("无证据评分主张率")
    if not outcomes:
        return None

    baseline_flat = re.sub(r"\s+", " ", baseline.casefold())
    problem_flat = re.sub(r"\s+", " ", problem.casefold())
    comparator = "opportunistic retrieval with model-emitted scoring signals"
    if not (
        any(
            cue in baseline_flat
            for cue in ("model emit", "model emitted", "model-emitted")
        )
        and any(cue in problem_flat for cue in ("happened to run", "coverage"))
    ):
        comparator = "the documented current pipeline"
    intervention = " plus ".join(interventions)
    controls = [
        "same frozen cases or corpus",
        "same source-access policy",
        "same retrieval budget",
        "same output eligibility rules",
    ]
    operational_definition = (
        f"Compare {comparator} with {intervention} under "
        + ", ".join(controls)
        + "."
    )
    title = (
        "与".join(intervention_titles)
        + f"对{domain_title}"
        + "证据完整性的影响"
    )
    question = (
        f"在冻结对象、数据源、检索预算与判定口径后，"
        f"{'与'.join(intervention_titles)}能否相对于现有流程提高"
        f"{'与'.join(outcome_titles)}？"
    )
    hypothesis = (
        f"{intervention} improves {outcomes[0]} relative to {comparator}; "
        "the result is falsified if the preregistered minimum effect is not "
        "reached or a protected secondary outcome degrades beyond tolerance."
    )
    concepts = [
        *domain_concepts,
        *interventions,
        *outcomes,
        "comparative engineering evaluation",
        "evidence completeness",
    ]
    query_terms = [
        f"{domain_query} evidence completeness",
        f"{domain_query} {' '.join(interventions)}",
        f"{' '.join(interventions)} {' '.join(outcomes)}",
    ]
    return {
        "operational_definition": operational_definition,
        "academic_title": title,
        "academic_concepts": list(dict.fromkeys(concepts)),
        "academic_query_terms": list(dict.fromkeys(query_terms)),
        "comparison_frame": {
            "schema_version": 1,
            "source": "baseline_problem_target_sections",
            "internal_label": internal_label,
            "comparator": comparator,
            "intervention": intervention,
            "primary_outcome": outcomes[0],
            "secondary_outcomes": outcomes[1:],
            "unit_of_analysis": "one frozen recommendation or retrieval case",
            "matched_controls": controls,
            "research_question": question,
            "falsifiable_hypothesis": hypothesis,
            "confidence": "moderate",
            "scientific_evidence_status": "not_yet_validated",
        },
    }


def _generative_media_normalization(
    text: str, internal_label: str
) -> dict[str, Any] | None:
    """Map prompt/tutorial corpora to a testable generative-media study."""

    lower = text.casefold()
    generative_media = any(
        cue in lower
        for cue in (
            "ai生成视频",
            "ai生成图",
            "文生图",
            "文生视频",
            "text-to-image",
            "text to image",
            "text-to-video",
            "text to video",
            "image generation",
            "video generation",
        )
    )
    prompt_material = any(
        cue in lower
        for cue in (
            "提示词",
            "关键词",
            "prompt",
            "seed",
            "sampler",
            "cfg scale",
        )
    )
    if not (generative_media and prompt_material):
        return None
    comparator = "unstructured or ad-hoc prompt selection"
    intervention = (
        "structured prompt templates with controlled content, style, camera, "
        "and motion attributes"
    )
    primary_outcome = "prompt-output semantic alignment rate"
    secondary = [
        "generation success rate",
        "perceptual quality",
        "temporal consistency for generated video",
        "seed-level output stability",
    ]
    question = (
        "在冻结生成模型、采样参数、随机种子和内容主题后，结构化提示词模板"
        "能否相对于非结构化提示词提高提示—输出语义一致率，并改善生成"
        "成功率与跨种子稳定性？"
    )
    hypothesis = (
        f"{intervention} improve {primary_outcome} relative to {comparator} "
        "without a preregistered material reduction in perceptual quality."
    )
    return {
        "operational_definition": (
            f"Compare {comparator} with {intervention} under the same frozen "
            "generative model, sampler, content themes, random seeds, and "
            "generation budget."
        ),
        "academic_title": (
            "结构化提示词属性对生成式图像与视频语义一致性及稳定性的影响"
        ),
        "academic_concepts": [
            "prompt engineering",
            "text-to-image generation",
            "text-to-video generation",
            "controllable generative media",
            "prompt sensitivity",
            "semantic alignment evaluation",
            "generation stability",
            "comparative engineering evaluation",
        ],
        "academic_query_terms": [
            "prompt engineering text to image semantic alignment evaluation",
            "structured prompts controllable text to video generation",
            "prompt sensitivity seed stability generative media",
            "text to image prompt adherence benchmark",
        ],
        "comparison_frame": {
            "schema_version": 1,
            "source": "generative_media_material_corpus",
            "internal_label": internal_label,
            "comparator": comparator,
            "intervention": intervention,
            "primary_outcome": primary_outcome,
            "secondary_outcomes": secondary,
            "unit_of_analysis": (
                "one frozen prompt-theme-seed generation case"
            ),
            "matched_controls": [
                "same frozen generative model and revision",
                "same sampler and generation parameters",
                "same content themes and random seeds",
                "same generation budget",
            ],
            "research_question": question,
            "falsifiable_hypothesis": hypothesis,
            "candidate_contribution": (
                "A controlled estimate of how structured prompt attributes "
                "affect semantic alignment and stability in generative media."
            ),
            "confidence": "moderate",
            "scientific_evidence_status": "not_yet_validated",
        },
    }


def _technical_manual_normalization(
    text: str, internal_label: str
) -> dict[str, Any] | None:
    """Turn a static product manual into a computational retrieval study."""

    lower = text.casefold()
    manual = any(
        cue in lower
        for cue in (
            "使用说明书",
            "电子说明书",
            "instructions for use",
            "user manual",
            "用户手册",
        )
    )
    task_content = any(
        cue in lower
        for cue in (
            "安全须知",
            "警告",
            "危险",
            "故障",
            "troubleshooting",
            "warning",
            "bios",
            "电池",
            "battery",
        )
    )
    if not (manual and task_content):
        return None
    comparator = "static linear manual browsing and keyword search"
    intervention = (
        "task-oriented section retrieval with safety-severity ranking and "
        "configuration-aware navigation"
    )
    primary_outcome = "correct instruction retrieval at k"
    secondary = [
        "critical warning retrieval recall",
        "mean reciprocal rank",
        "abstention accuracy for unsupported configurations",
        "retrieval latency",
    ]
    question = (
        "在冻结说明书版本、任务查询集和正确章节标注后，任务导向的结构化"
        "检索与安全等级排序能否相对于线性浏览和关键词搜索，提高正确操作"
        "指引的 Top-K 检索率及关键警告召回率？"
    )
    hypothesis = (
        f"{intervention} improves {primary_outcome} relative to {comparator} "
        "without increasing unsupported-configuration answers."
    )
    return {
        "operational_definition": (
            f"Compare {comparator} with {intervention} under the same frozen "
            "manual, troubleshooting queries, answer-section annotations, "
            "retrieval budget, and abstention policy."
        ),
        "academic_title": (
            "任务导向结构化检索对电子设备说明书操作指引可发现性的影响"
        ),
        "academic_concepts": [
            "technical documentation retrieval",
            "task-oriented information access",
            "safety-critical information retrieval",
            "user manual usability",
            "troubleshooting question answering",
            "configuration-aware retrieval",
            "calibrated abstention",
            "comparative engineering evaluation",
        ],
        "academic_query_terms": [
            "technical documentation retrieval user manual evaluation",
            "task oriented information access troubleshooting manuals",
            "safety warning retrieval technical documentation",
            "configuration aware question answering user manuals",
        ],
        "comparison_frame": {
            "schema_version": 1,
            "source": "technical_manual_material",
            "internal_label": internal_label,
            "comparator": comparator,
            "intervention": intervention,
            "primary_outcome": primary_outcome,
            "secondary_outcomes": secondary,
            "unit_of_analysis": "one frozen manual troubleshooting query",
            "matched_controls": [
                "same frozen manual revision",
                "same query and answer-span set",
                "same retrieval budget",
                "same abstention policy",
            ],
            "research_question": question,
            "falsifiable_hypothesis": hypothesis,
            "candidate_contribution": (
                "A controlled evaluation of structured, safety-aware retrieval "
                "for static technical manuals."
            ),
            "confidence": "moderate",
            "scientific_evidence_status": "not_yet_validated",
        },
    }


def build_academic_concept_normalizations(
    source_root: str | Path | None,
    candidates: Sequence[Any],
    author_claims: Sequence[AuthorClaim],
) -> list[AcademicConceptNormalization]:
    """Map project labels to scholarly concepts without granting claim authority."""

    root = Path(source_root).resolve() if source_root is not None else None
    claim_text = "\n".join(item.statement for item in author_claims)
    candidate_texts = [
        _candidate_source_text(root, candidate) for candidate in candidates
    ]
    project_text = "\n".join([claim_text, *[item[0] for item in candidate_texts]])
    shared_high_return_definition = _high_return_operational_definition(
        project_text
    )
    rows: list[AcademicConceptNormalization] = []
    for candidate, (candidate_text, source_paths) in zip(
        candidates, candidate_texts, strict=True
    ):
        track_id = str(_candidate_value(candidate, "track_id", "") or "")
        internal_label = str(
            _candidate_value(candidate, "display_title", "")
            or _candidate_value(candidate, "novelty_seed", "")
            or track_id
        ).strip()
        lower = candidate_text.casefold()
        operational_definition = ""
        academic_title = ""
        academic_concepts: list[str] = []
        academic_query_terms: list[str] = []
        comparison_frame: dict[str, Any] = {}

        if (
            any(
                cue in lower
                for cue in (
                    "self-play",
                    "self play",
                    "proposer–critic",
                    "proposer-critic",
                )
            )
            and any(
                cue in lower
                for cue in (
                    "claim",
                    "evidence",
                    "critique",
                    "revision",
                    "verification",
                )
            )
        ):
            operational_definition = (
                "Under the same frozen claim-evidence items, model or "
                "decision rule, decoding budget, and evaluator, compare a "
                "single-pass proposer with one bounded proposer-critic-"
                "revision round whose critique and revision are retained."
            )
            academic_title = (
                "Bounded Proposer-Critic Revision for Evidence-Grounded "
                "Claim Delivery: A Paired Computational Evaluation"
            )
            academic_concepts = [
                "multi-agent debate",
                "self-critique in language models",
                "iterative refinement",
                "claim-evidence verification",
                "evidence-grounded generation",
                "paired computational evaluation",
                "auditable agent workflows",
            ]
            academic_query_terms = [
                "language model self critique factuality evidence grounded generation",
                "proposer critic revision claim verification",
                "multi agent debate factual accuracy empirical evaluation",
                "iterative refinement language model hallucination evaluation",
            ]
            comparison_frame = {
                "schema_version": 1,
                "source": "local_project_design_cue",
                "internal_label": internal_label,
                "comparator": "single-pass claim proposer",
                "intervention": (
                    "one bounded proposer-critic-revision round"
                ),
                "primary_outcome": (
                    "accuracy of frozen claim-delivery decisions"
                ),
                "unit_of_analysis": "one registered task-seed pair",
                "matched_controls": [
                    "same frozen claim-evidence items",
                    "same decision budget",
                    "same evaluator",
                    "same seed schedule",
                ],
                "research_question": (
                    "Does one bounded, inspectable proposer-critic-revision "
                    "round improve claim-delivery decision accuracy over a "
                    "single-pass proposer?"
                ),
                "falsifiable_hypothesis": (
                    "The bounded revision arm improves paired decision "
                    "accuracy by at least the preregistered threshold."
                ),
                "candidate_contribution": (
                    "A bounded and auditable alternative to open-ended hidden "
                    "self-play for claim-evidence delivery."
                ),
                "confidence": "moderate",
                "scientific_evidence_status": "not_yet_validated",
            }
        elif (
            any(
                cue in lower
                for cue in (
                    "马克思主义",
                    "marxism",
                    "marxist",
                )
            )
            and any(
                cue in lower
                for cue in (
                    "微调",
                    "fine-tun",
                    "lora",
                    "开源语言模型",
                    "open-source language model",
                )
            )
        ):
            operational_definition = (
                "Under the same open-source language-model backbone, training "
                "token budget, optimization settings, prompts, and evaluation "
                "items, compare no fine-tuning, Chinese Marxist-text LoRA "
                "fine-tuning, and equal-volume neutral social-science LoRA "
                "fine-tuning."
            )
            academic_title = (
                "中文马克思主义语料参数高效微调对语言模型劳动—资本价值判断的影响"
            )
            academic_concepts = [
                "parameter-efficient fine-tuning",
                "language-model value alignment",
                "political ideology in language models",
                "labor-capital relations",
                "normative judgment evaluation",
                "factual knowledge retention",
                "matched-corpus controlled experiment",
            ]
            academic_query_terms = [
                "political ideology language models fine tuning empirical evaluation",
                "language model values training data intervention",
                "parameter efficient fine tuning political bias benchmark",
                "Chinese Marxist corpus language model",
                "labor capital factual question answering dataset",
            ]
            comparison_frame = {
                "schema_version": 1,
                "source": "owner_approved_idea",
                "internal_label": internal_label,
                "comparator": (
                    "the unchanged backbone and an equal-token neutral "
                    "social-science LoRA control"
                ),
                "intervention": "Chinese Marxist-text LoRA fine-tuning",
                "primary_outcome": (
                    "preregistered Marxist-rubric score on labor-capital "
                    "normative scenarios"
                ),
                "secondary_outcomes": [
                    "accuracy on labor, capital, and economic-institution facts",
                    "protected general-language capability",
                ],
                "unit_of_analysis": "one frozen evaluation item",
                "matched_controls": [
                    "same model backbone",
                    "same training-token budget",
                    "same optimizer and update budget",
                    "same decoding configuration",
                    "same blinded evaluation set",
                ],
                "research_question": (
                    "在相同模型骨干、训练 token 数和微调协议下，与未微调模型及"
                    "等量中性社会科学文本微调相比，中文马克思主义文本微调是否会"
                    "使开源语言模型在劳动—资本情境中的价值判断系统性地接近预注册"
                    "的马克思主义评价量表？"
                ),
                "falsifiable_hypothesis": (
                    "The Marxist-corpus arm has a higher preregistered "
                    "normative-rubric score than both controls; the hypothesis "
                    "is not supported if either comparison misses its frozen "
                    "threshold."
                ),
                "candidate_contribution": (
                    "A same-backbone, equal-token controlled evaluation that "
                    "separates ideological output shifts from factual-accuracy "
                    "and capability changes."
                ),
                "confidence": "moderate",
                "scientific_evidence_status": "not_yet_validated",
            }
        elif (
            any(
                cue in lower
                for cue in (
                    "行级安全",
                    "row level security",
                    "row-level security",
                    "enable row level security",
                )
            )
            and any(
                cue in lower
                for cue in (
                    "证据绑定",
                    "evidence",
                    "audit",
                    "foreign key",
                    "references",
                )
            )
        ):
            operational_definition = (
                "Compare application-only authorization with a matched "
                "database-enforced design using row-level security, evidence "
                "foreign keys, and append-only audit records under the same "
                "multi-tenant academic recommendation transaction workload."
            )
            academic_title = (
                "行级安全与证据绑定对学术推荐工作流数据完整性的影响"
            )
            academic_concepts = [
                "row-level security",
                "multi-tenant data isolation",
                "database access control",
                "referential integrity",
                "data provenance",
                "audit logging",
                "policy enforcement testing",
            ]
            academic_query_terms = [
                "row level security multi tenant isolation empirical evaluation",
                "database policy enforcement testing referential integrity",
                "data provenance audit logging academic recommender systems",
                "application authorization versus database row level security",
            ]
        elif (
            any(
                cue in lower
                for cue in (
                    "advisor-radar",
                    "学术导师推荐",
                    "academic advisor recommendation",
                    "professor recommendation",
                )
            )
            and any(
                cue in lower
                for cue in (
                    "结构化证据评分",
                    "scoring",
                    "recommendation",
                    "review",
                )
            )
        ):
            operational_definition = (
                "Compare single-pass professor recommendation with a matched "
                "structured evidence-scoring, risk-constraint, and review "
                "pipeline under the same applicant-professor corpus, model, "
                "retrieval budget, and shortlist size."
            )
            academic_title = (
                "结构化证据评分与复核对学术导师推荐可靠性的影响"
            )
            academic_concepts = [
                "academic recommender systems",
                "expert finding",
                "human-in-the-loop decision support",
                "ranking stability",
                "algorithmic auditing",
                "evidence-grounded recommendation",
                "calibrated abstention",
            ]
            academic_query_terms = [
                "academic advisor recommender systems expert finding evaluation",
                "evidence grounded professor recommendation ranking stability",
                "human in the loop academic recommendation audit",
                "expert recommendation unsupported claim detection",
            ]
        elif (
            any(
                cue in lower
                for cue in (
                    "investment advisor",
                    "robo-adviser",
                    "ai 投资顾问",
                    "衡策",
                    "roundtable-engine",
                )
            )
            and any(
                cue in lower
                for cue in (
                    "roundtable",
                    "反证",
                    "证据检索",
                    "knowledge",
                    "advisor",
                )
            )
        ):
            operational_definition = (
                "Compare a single-agent financial answer pipeline with a "
                "matched multi-role review and counter-evidence retrieval "
                "pipeline under the same model, question set, corpus, token "
                "budget, and deterministic claim-evidence audit."
            )
            academic_title = (
                "多角色反证与证据检索对金融决策支持可靠性的影响"
            )
            academic_concepts = [
                "financial decision support systems",
                "robo-advisors",
                "retrieval-augmented generation",
                "multi-agent deliberation",
                "claim-evidence verification",
                "risk communication",
                "calibrated abstention",
            ]
            academic_query_terms = [
                "multi-agent financial decision support evidence verification",
                "robo-advisor retrieval augmented generation risk disclosure",
                "counter-evidence retrieval financial question answering",
                "multi-agent deliberation factuality finance",
            ]
        elif any(
            cue in lower
            for cue in (
                "airs-bench",
                "frozen benchmark metric",
                "closed-loop experimental improvement",
            )
        ):
            task_labels = {
                "coreferenceresolutionsupergluewsc": "指代消解（SuperGLUE WSC）",
                "coreferenceresolutionwinogrande": "指代消解（Winogrande）",
                "mathquestionansweringsvamp": "数学问答（SVAMP）",
                "questionansweringfinqa": "金融问答（FinQA）",
                "readingcomprehensionsquad": "阅读理解（SQuAD）",
                "sentimentanalysisyelpreviewfull": "情感分析（Yelp Review Full）",
                "textualclassificationsick": "文本分类（SICK）",
                "textualsimilaritysick": "文本相似度（SICK）",
            }
            task_label = next(
                (
                    label
                    for cue, label in task_labels.items()
                    if cue in re.sub(r"[^a-z0-9]+", "", lower)
                ),
                "跨任务机器学习基准",
            )
            operational_definition = (
                "Compare baseline and bounded candidate runs under the same "
                "frozen task, evaluator, primary metric, data boundary, seed "
                "schedule, and isolated runtime."
            )
            academic_title = (
                f"冻结评估器下研究代理的闭环基准改进：{task_label}"
            )
            academic_concepts = [
                "autonomous research agents",
                "closed-loop benchmark optimization",
                "frozen evaluator",
                "paired computational evaluation",
                "reproducible machine learning experimentation",
            ]
            academic_query_terms = [
                "autonomous research agent closed-loop benchmark evaluation",
                "frozen evaluator paired machine learning experiments",
                "reproducible agentic benchmark optimization",
            ]
        elif (
            "robotic imitation learning" in lower
            and any(
                cue in lower
                for cue in (
                    "stereo rgb",
                    "rgb-depth",
                    "stereo dino",
                    "point clouds",
                )
            )
        ):
            operational_definition = (
                "Compare observation representations under matched robot tasks, "
                "demonstration counts, trajectory boundaries, policy backbone, "
                "training budget, evaluator, and seed schedule."
            )
            academic_title = (
                "机器人模仿学习中双目视觉、深度特征与点云表示的配对评估"
            )
            academic_concepts = [
                "robotic imitation learning",
                "visual representation learning for robot manipulation",
                "stereo vision for robot policy learning",
                "point-cloud policy learning",
                "sample efficiency in imitation learning",
                "paired representation ablation",
            ]
            academic_query_terms = [
                "robot imitation learning visual representation ablation",
                "stereo vision versus point cloud robot manipulation policy",
                "DINO visual features imitation learning robotics",
                "sample efficiency observation representation robomimic",
            ]
        elif (
            ("内转子" in lower and "表贴" in lower)
            or (
                "embedded structure" in lower
                and "surface-mounted structure" in lower
            )
        ):
            academic_title = "内嵌式与表贴式电机转子结构的性能比较"
            academic_concepts = [
                "electric motor rotor topology",
                "interior versus surface-mounted rotor structure",
                "comparative motor performance evaluation",
                "design-of-experiments for motor manufacturing",
            ]
            academic_query_terms = [
                "interior versus surface-mounted motor rotor performance",
                "electric motor rotor topology comparison",
                "motor design comparative experimental evaluation",
            ]
        elif "极端赢家" in lower or "extreme winner" in lower:
            operational_definition = (
                _high_return_operational_definition(candidate_text)
                or shared_high_return_definition
            )
            academic_concepts = [
                "cross-sectional equity return prediction",
                "rare high-return event ranking",
                "learning-to-rank for stock selection",
                "industry-relative return ranking",
                "top-k portfolio selection",
                "walk-forward evaluation",
            ]
            academic_query_terms = [
                "cross-sectional equity ranking rare high-return events",
                "learning-to-rank stock selection",
                "industry-relative return prediction",
                "top-k portfolio walk-forward evaluation",
            ]
            if "lambdamart" in lower or "催化" in lower:
                academic_title = (
                    "公开催化特征增强的横截面股票排序："
                    "LambdaMART 配对比较"
                )
            elif "稳健" in lower or "robust" in lower:
                academic_title = (
                    "面向稀有高收益事件识别的横截面股票排序："
                    "滚动窗口稳健性评估"
                )
            else:
                academic_title = (
                    "面向稀有高收益事件识别的横截面股票排序与前瞻评估"
                )
        elif (
            ("绕线" in lower or "winding process" in lower)
            and "pcba" in lower
        ):
            academic_title = "电机定子绕线与 PCBA 固定工艺优化的质量效应"
            academic_concepts = [
                "electric motor manufacturing process optimization",
                "stator winding process",
                "PCBA hot-riveting reliability",
                "manufacturing quality evaluation",
            ]
            academic_query_terms = [
                "motor stator winding process optimization quality",
                "PCBA hot riveting manufacturing reliability",
                "electric motor manufacturing process evaluation",
            ]
        elif "涂覆" in lower or "powder coating" in lower:
            academic_title = "转子粉末涂覆工艺的材料—过程—质量关系"
            academic_concepts = [
                "rotor powder coating process",
                "coating material composition",
                "manufacturing defect prevention",
                "process quality control",
            ]
            academic_query_terms = [
                "rotor powder coating process quality",
                "coating material process defect control",
                "motor rotor coating manufacturing",
            ]
        elif any(
            cue in lower
            for cue in ("灵活退出", "动态退出", "flexible exit", "exit signal")
        ):
            academic_title = "基于动态退出规则的投资组合持有期决策"
            academic_concepts = [
                "dynamic portfolio exit policy",
                "holding-period return prediction",
                "optimal stopping in portfolio management",
            ]
            academic_query_terms = [
                "dynamic portfolio exit policy",
                "holding-period return prediction",
                "optimal stopping portfolio management",
            ]
        elif (
            ("质量" in lower and "择时" in lower)
            or "quality timing" in lower
        ):
            academic_title = "滚动择时窗口下的因子型横截面股票选择"
            academic_concepts = [
                "factor-based equity selection",
                "rolling-window market timing",
                "cross-sectional return prediction",
            ]
            academic_query_terms = [
                "factor-based equity selection rolling window",
                "market timing window robustness",
                "cross-sectional return prediction",
            ]

        generic = _generic_comparative_normalization(
            candidate_text, internal_label
        )
        if generic is not None:
            # An explicit Baseline → Problem → Target frame is stronger than
            # a product-name or domain-keyword mapping and therefore wins.
            operational_definition = str(generic["operational_definition"])
            academic_title = str(generic["academic_title"])
            academic_concepts = [
                str(item) for item in generic["academic_concepts"]
            ]
            academic_query_terms = [
                str(item) for item in generic["academic_query_terms"]
            ]
            comparison_frame = dict(generic["comparison_frame"])
        elif not academic_title:
            media = _generative_media_normalization(
                candidate_text, internal_label
            )
            if media is not None:
                operational_definition = str(
                    media["operational_definition"]
                )
                academic_title = str(media["academic_title"])
                academic_concepts = [
                    str(item) for item in media["academic_concepts"]
                ]
                academic_query_terms = [
                    str(item) for item in media["academic_query_terms"]
                ]
                comparison_frame = dict(media["comparison_frame"])
            else:
                manual = _technical_manual_normalization(
                    candidate_text, internal_label
                )
                if manual is not None:
                    operational_definition = str(
                        manual["operational_definition"]
                    )
                    academic_title = str(manual["academic_title"])
                    academic_concepts = [
                        str(item)
                        for item in manual["academic_concepts"]
                    ]
                    academic_query_terms = [
                        str(item)
                        for item in manual["academic_query_terms"]
                    ]
                    comparison_frame = dict(
                        manual["comparison_frame"]
                    )

        normalized = bool(academic_title and academic_concepts)
        warnings: list[str] = []
        if not normalized:
            warnings.append(
                "No reliable scholarly concept mapping was found; owner "
                "confirmation is required before literature retrieval."
            )
        elif not operational_definition:
            warnings.append(
                "The scholarly concept mapping is available, but the project "
                "does not expose a concise operational definition."
            )
        rows.append(
            AcademicConceptNormalization(
                normalization_id=_stable_id(
                    "academic-normalization",
                    f"{track_id}:{internal_label}:{academic_title}",
                ),
                source_track_id=track_id,
                internal_label=internal_label,
                operational_definition=operational_definition,
                academic_title=academic_title,
                academic_concepts=academic_concepts,
                academic_query_terms=academic_query_terms,
                comparison_frame=comparison_frame,
                source_paths=source_paths,
                status=(
                    "normalized" if normalized else "needs_owner_review"
                ),
                warnings=warnings,
            )
        )
    return rows


def build_project_fingerprint(
    resources: Sequence[Any],
    candidates: Sequence[Any],
    author_claims: Sequence[AuthorClaim],
    *,
    source_root: str | Path | None = None,
) -> ProjectResearchFingerprint:
    academic_normalizations = build_academic_concept_normalizations(
        source_root, candidates, author_claims
    )
    candidate_text = " ".join(
        f"{_candidate_value(item, 'track_id', '')} {_candidate_value(item, 'display_title', '')} {_candidate_value(item, 'novelty_seed', '')}"
        for item in candidates
    )
    claim_text = " ".join(item.statement for item in author_claims)
    academic_text = " ".join(
        " ".join(
            [
                item.academic_title,
                *item.academic_concepts,
                *item.academic_query_terms,
            ]
        )
        for item in academic_normalizations
    )
    all_text = f"{candidate_text} {claim_text} {academic_text}"
    discovered_terms = _terms(all_text)
    canonical_terms = sorted(discovered_terms & _CANONICAL_CONCEPTS)
    ranked_terms = sorted(
        discovered_terms - _CANONICAL_CONCEPTS,
        key=lambda item: (-all_text.casefold().count(item.casefold()), item),
    )
    terms = [*canonical_terms, *ranked_terms[: 80 - len(canonical_terms)]]
    methods = [item for item in terms if any(cue in item for cue in ("exit", "filter", "model", "gate", "退出", "筛选", "门控", "识别"))][:20]
    metrics = [item for item in terms if any(cue in item for cue in ("accuracy", "return", "drawdown", "rate", "auc", "收益", "回撤", "准确"))][:20]
    problems = [
        _candidate_value(item, "novelty_seed", "") for item in candidates
        if str(_candidate_value(item, "novelty_seed", "")).strip()
    ][:20]
    evidence_assets = [
        str(_resource_value(item, "path", "")) for item in resources
        if str(_resource_value(item, "suffix", "")) in {".json", ".jsonl", ".csv", ".tsv"}
    ][:60]
    domains = [cue for cue in _PHRASE_CUES if cue.casefold() in all_text.casefold()][:15]
    return ProjectResearchFingerprint(
        domains=domains,
        problems=problems,
        methods=methods,
        metrics=metrics,
        terms=terms,
        evidence_assets=evidence_assets,
        source_track_ids=[str(_candidate_value(item, "track_id", "")) for item in candidates],
        academic_normalizations=academic_normalizations,
    )


def _env_value(path: Path, name: str) -> str | None:
    process = os.getenv(name, "").strip()
    if process:
        return process
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("\"'") or None
    return None


def _project_queries(fingerprint: ProjectResearchFingerprint) -> list[str]:
    selected: list[str] = []
    joined = " ".join([*fingerprint.domains, *fingerprint.problems, *fingerprint.methods])
    for cue in _PHRASE_CUES:
        if cue.casefold() in joined.casefold() and len(cue) <= 10:
            selected.append(cue)
    for term in fingerprint.terms:
        compact = term.replace(" ", "")
        if 2 <= len(compact) <= 10 and compact not in selected:
            selected.append(compact)
    return selected[:6]


def _scholarly_queries(fingerprint: ProjectResearchFingerprint) -> list[str]:
    queries: list[str] = []
    for track_id in fingerprint.source_track_ids:
        lower = track_id.casefold()
        mapped = None
        for cue, query in (
            ("flexible-exit", "portfolio exit strategy drawdown returns"),
            ("extreme-winner", "extreme stock returns portfolio strategy"),
            ("quality-timing", "quality factor market timing portfolio"),
            ("causal-topic", "causal signals stock selection portfolio"),
        ):
            if cue in lower:
                mapped = query
                break
        if mapped:
            queries.append(mapped)
    mappings = (
        ("灵活退出", "adaptive portfolio exit drawdown robustness"),
        ("极端赢家", "extreme winner portfolio exit timing"),
        ("择时稳健", "market timing robustness portfolio"),
        ("质量筛选", "quality filter portfolio selection"),
        ("科研智能体", "autonomous scientific research agents"),
        ("证据门控", "claim evidence gating autonomous agents"),
    )
    joined = " ".join([*fingerprint.domains, *fingerprint.problems, *fingerprint.terms])
    for cue, query in mappings:
        if cue in joined:
            queries.append(query)
    return list(dict.fromkeys(queries))[:4]


def _query_relevant(query: str, title: str, summary: str) -> bool:
    query_terms = {
        item for item in _terms(query)
        if "_" in item or re.fullmatch(r"[a-z][a-z0-9-]{3,}", item)
    }
    record_terms = _terms(f"{title} {summary}")
    return len(query_terms & record_terms) >= 2


HttpJson = Callable[[urllib.request.Request], dict[str, Any]]


def fetch_redfox_trends(
    queries: Sequence[str], *, api_key: str, http_json: HttpJson
) -> list[TrendSignal]:
    signals: dict[str, TrendSignal] = {}
    now = datetime.now(timezone.utc)
    for query in queries[:6]:
        request = urllib.request.Request(
            REDFOX_API_URL,
            data=json.dumps({"keyword": query, "offset": 0, "sortType": "time", "source": REDFOX_SOURCE_TAG}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-API-KEY": api_key},
            method="POST",
        )
        payload = http_json(request)
        if payload.get("code") not in {200, 2000}:
            raise RuntimeError(f"RedFox provider error: code={payload.get('code')}")
        rows = (payload.get("data") or {}).get("list") or []
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            title = _clean_statement(str(row.get("title") or ""))
            url = str(row.get("workUrl") or row.get("url") or "").strip()
            source = str(row.get("author") or row.get("accountName") or "").strip()
            if not title or not url.startswith("https://mp.weixin.qq.com/"):
                continue
            engagement = {
                key: max(0, int(row.get(provider_key) or 0))
                for key, provider_key in (
                    ("reads", "readCount"), ("likes", "likeCount"),
                    ("shares", "shareCount"), ("comments", "commentCount"),
                )
            }
            engagement_score = min(1.0, math.log1p(sum(engagement.values())) / math.log(100_001))
            published = str(row.get("publishTime") or row.get("publicTime") or "")
            recency = 0.5
            try:
                parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                days = max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 86400)
                recency = math.exp(-days / 45)
            except ValueError:
                pass
            density_terms = _terms(f"{title} {row.get('summary') or ''}")
            signal = TrendSignal(
                signal_id=_stable_id("wechat-signal", url),
                provider="redfox_wechat",
                signal_class="market_attention",
                query=query,
                title=title,
                summary=str(row.get("summary") or "")[:1200],
                url=url,
                published_at=published,
                source_name=source,
                engagement=engagement,
                terms=sorted(density_terms)[:80],
                trend_score=round(0.55 * recency + 0.45 * engagement_score, 4),
                scientific_density=round(min(1.0, len(density_terms) / 28), 4),
            )
            signals[signal.signal_id] = signal
    return list(signals.values())


def fetch_scholarly_trends(
    queries: Sequence[str], *, api_key: str | None = None, http_json: HttpJson
) -> list[TrendSignal]:
    signals: dict[str, TrendSignal] = {}
    current_year = datetime.now(timezone.utc).year
    for query in queries[:4]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "limit": 20,
                "fields": "title,abstract,url,year,publicationDate,citationCount,influentialCitationCount,venue,authors",
            }
        )
        headers = {"User-Agent": "ResearchForge/0.1 claim-trend-discovery"}
        if api_key:
            headers["x-api-key"] = api_key
        payload = http_json(urllib.request.Request(f"{SEMANTIC_SCHOLAR_API_URL}?{params}", headers=headers))
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            title = _clean_statement(str(row.get("title") or ""))
            url = str(row.get("url") or "").strip()
            if not title or not url:
                continue
            year = int(row.get("year") or current_year)
            citations = max(0, int(row.get("citationCount") or 0))
            influential = max(0, int(row.get("influentialCitationCount") or 0))
            age = max(1, current_year - year + 1)
            velocity = citations / age
            score = min(1.0, (math.log1p(velocity) + 0.5 * math.log1p(influential)) / 8)
            abstract = str(row.get("abstract") or "")[:1800]
            if not _query_relevant(query, title, abstract):
                continue
            density_terms = _terms(f"{title} {abstract}")
            signal = TrendSignal(
                signal_id=_stable_id("scholarly-signal", url),
                provider="semantic_scholar",
                signal_class="scholarly_attention",
                query=query,
                title=title,
                summary=abstract,
                url=url,
                published_at=str(row.get("publicationDate") or year),
                source_name=str(row.get("venue") or ""),
                engagement={"citations": citations, "influential_citations": influential},
                terms=sorted(density_terms)[:100],
                trend_score=round(score, 4),
                scientific_density=round(min(1.0, (len(density_terms) + (10 if abstract else 0)) / 55), 4),
                metadata={
                    "authors": [
                        str(author.get("name") or "").strip()
                        for author in row.get("authors") or []
                        if isinstance(author, dict)
                        and str(author.get("name") or "").strip()
                    ]
                },
            )
            signals[signal.signal_id] = signal
    return list(signals.values())


def fetch_crossref_trends(
    queries: Sequence[str], *, http_json: HttpJson
) -> list[TrendSignal]:
    signals: dict[str, TrendSignal] = {}
    current_year = datetime.now(timezone.utc).year
    for query in queries[:4]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "rows": 20,
                "filter": f"from-pub-date:{current_year - 2}-01-01",
                "select": "DOI,title,abstract,URL,published,created,is-referenced-by-count,publisher,container-title,author",
            }
        )
        payload = http_json(
            urllib.request.Request(
                f"{CROSSREF_API_URL}?{params}",
                headers={"User-Agent": "ResearchForge/0.1 (mailto:research-forge-local@example.invalid)"},
            )
        )
        for row in (payload.get("message") or {}).get("items") or []:
            if not isinstance(row, dict):
                continue
            raw_title = row.get("title") or []
            title = _clean_statement(str(raw_title[0] if raw_title else ""))
            url = str(row.get("URL") or "").strip()
            if not title or not url:
                continue
            citations = max(0, int(row.get("is-referenced-by-count") or 0))
            date_parts = ((row.get("published") or {}).get("date-parts") or [[current_year]])[0]
            year = int(date_parts[0] if date_parts else current_year)
            age = max(1, current_year - year + 1)
            abstract = re.sub(r"<[^>]+>", " ", str(row.get("abstract") or ""))[:1800]
            if not _query_relevant(query, title, abstract):
                continue
            density_terms = _terms(f"{title} {abstract}")
            venue = row.get("container-title") or []
            signal = TrendSignal(
                signal_id=_stable_id("scholarly-signal", url),
                provider="crossref",
                signal_class="scholarly_attention",
                query=query,
                title=title,
                summary=abstract,
                url=url,
                published_at=str(year),
                source_name=str(venue[0] if venue else row.get("publisher") or ""),
                engagement={"citations": citations},
                terms=sorted(density_terms)[:100],
                trend_score=round(min(1.0, math.log1p(citations / age) / 6), 4),
                scientific_density=round(min(1.0, (len(density_terms) + (10 if abstract else 0)) / 55), 4),
                metadata={
                    "authors": [
                        " ".join(
                            part
                            for part in (
                                str(author.get("given") or "").strip(),
                                str(author.get("family") or "").strip(),
                            )
                            if part
                        )
                        for author in row.get("author") or []
                        if isinstance(author, dict)
                        and (
                            str(author.get("given") or "").strip()
                            or str(author.get("family") or "").strip()
                        )
                    ]
                },
            )
            signals[signal.signal_id] = signal
    return list(signals.values())


def _match_score(left: Iterable[str], right: Iterable[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    overlap = left_set & right_set
    return min(1.0, 0.65 * len(overlap) / min(len(left_set), len(right_set)) + 0.35 * len(overlap) / len(left_set | right_set))


def build_discovery_query_intents(
    fingerprint: ProjectResearchFingerprint,
    author_claims: Sequence[AuthorClaim],
) -> list[DiscoveryQueryIntent]:
    """Turn the local project fingerprint into an auditable query matrix."""

    canonical = [
        item.replace("_", " ")
        for item in fingerprint.terms
        if item in _CANONICAL_CONCEPTS
    ]
    short_project_terms = [
        item.strip()
        for item in [
            *fingerprint.domains,
            *fingerprint.problems,
            *fingerprint.methods,
            *fingerprint.metrics,
        ]
        if 2 <= len(item.strip()) <= 32
        and not any(mark in item for mark in ("。", "？", "?", ":", "：", "\n"))
    ]
    ignored_track_tokens = {
        "research",
        "report",
        "validation",
        "implementation",
        "freeze",
        "study",
        "test",
        "model",
        "baseline",
    }
    topic_seeds: list[str] = []
    topic_signatures: set[tuple[str, ...]] = set()
    normalized_track_ids = {
        item.source_track_id
        for item in fingerprint.academic_normalizations
        if item.status == "normalized"
    }
    for normalization in fingerprint.academic_normalizations:
        if (
            normalization.status != "normalized"
            or not normalization.academic_query_terms
        ):
            continue
        seed = re.sub(
            r"\s+", " ", normalization.academic_query_terms[0]
        ).strip()
        signature = tuple(seed.casefold().split()[:3])
        if not seed or signature in topic_signatures:
            continue
        topic_seeds.append(seed)
        topic_signatures.add(signature)
        if len(topic_seeds) >= 3:
            break
    for track_id in fingerprint.source_track_ids:
        if len(topic_seeds) >= 3:
            break
        if track_id in normalized_track_ids:
            continue
        tokens = [
            item
            for item in re.split(r"[-_\s]+", track_id.casefold())
            if item
            and item not in ignored_track_tokens
            and not re.fullmatch(r"v?\d+", item)
            and not re.search(r"\d", item)
            and len(item) > 1
        ]
        seed = " ".join(tokens[:5]).strip()
        if not seed:
            continue
        signature = tuple(tokens[:2])
        if signature in topic_signatures:
            continue
        seed_terms = set(seed.split())
        if any(
            _match_score(seed_terms, set(existing.split())) >= 0.7
            for existing in topic_seeds
        ):
            continue
        topic_seeds.append(seed)
        topic_signatures.add(signature)
    if not topic_seeds:
        topic_seeds = list(dict.fromkeys([*canonical, *short_project_terms]))[:3]
    if not topic_seeds:
        topic_seeds = ["project evidence verification"]

    context_terms = [
        item
        for item in canonical
        if item in {"portfolio", "return", "robustness", "drawdown"}
        if item not in " ".join(topic_seeds)
    ][:2]
    rows: list[tuple[str, str, list[str]]] = [
        (
            "closest_prior_work",
            " ".join([seed, *context_terms, "empirical study"]).strip(),
            [],
        )
        for seed in topic_seeds
    ]
    primary = topic_seeds[0]
    rows.extend(
        [
            ("method_and_baseline", f"{primary} benchmark baseline", []),
            (
                "recent_trend",
                f"{primary} recent research trends",
                [],
            ),
            (
                "dataset_and_model",
                f"{primary} reproducible dataset model",
                [],
            ),
        ]
    )
    eligible_claims = [
        item
        for item in author_claims
        if item.claim_type
        in {"research_question", "result", "comparative", "robustness", "novelty"}
        and "sha-256" not in item.statement.casefold()
        and not re.search(r"\b[a-f0-9]{32,}\b", item.statement.casefold())
    ]
    remaining = max(0, 8 - len(rows))
    for claim in eligible_claims[:remaining]:
        statement = _clean_statement(claim.statement)
        if not statement:
            continue
        statement_terms = _terms(statement)
        related_normalizations = sorted(
            (
                (
                    _match_score(
                        statement_terms,
                        _terms(
                            f"{item.source_track_id} {item.internal_label} "
                            f"{item.operational_definition}"
                        ),
                    ),
                    item,
                )
                for item in fingerprint.academic_normalizations
                if item.status == "normalized"
                and item.academic_query_terms
            ),
            key=lambda item: -item[0],
        )
        if related_normalizations and related_normalizations[0][0] >= 0.12:
            claim_query = related_normalizations[0][1].academic_query_terms[0]
        else:
            claim_terms = [
                item.replace("_", " ")
                for item in sorted(statement_terms)
                if item in _CANONICAL_CONCEPTS
                or re.fullmatch(r"[a-z][a-z0-9-]{3,}", item)
            ]
            claim_query = (
                " ".join(claim_terms[:8]).strip() or statement[:120]
            )
        rows.append(
            (
                "contradicting_evidence",
                f"{claim_query} limitations negative results failure",
                [claim.claim_id],
            )
        )

    intents: list[DiscoveryQueryIntent] = []
    seen: set[str] = set()
    for purpose, query, claim_ids in rows:
        normalized = re.sub(r"\s+", " ", query).strip()[:800]
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        intents.append(
            DiscoveryQueryIntent(
                query_id=_stable_id("discovery-query", f"{purpose}:{key}"),
                purpose=purpose,  # type: ignore[arg-type]
                query=normalized,
                source_claim_ids=claim_ids,
                terms=sorted(_terms(normalized))[:80],
            )
        )
    return intents[:8]


def recommend_claims(
    fingerprint: ProjectResearchFingerprint,
    author_claims: Sequence[AuthorClaim],
    trends: Sequence[TrendSignal],
    candidates: Sequence[Any],
) -> list[RecommendedClaim]:
    evidence_ready = any(bool(_candidate_value(item, "artifact_chain_complete", False)) for item in candidates)
    local_paths = list(dict.fromkeys(fingerprint.evidence_assets))[:20]
    seeds: list[tuple[str, str, list[str]]] = [
        (claim.claim_id, claim.statement, [claim.claim_id]) for claim in author_claims
    ]
    if not seeds:
        seeds.extend(
            (
                _stable_id("trend-candidate", str(_candidate_value(item, "track_id", ""))),
                str(_candidate_value(item, "novelty_seed", "")),
                [],
            )
            for item in candidates
            if str(_candidate_value(item, "novelty_seed", "")).strip()
        )
    recommendations: list[RecommendedClaim] = []
    project_terms = set(fingerprint.terms)
    normalization_terms = [
        (
            _terms(
                f"{item.source_track_id} {item.internal_label} "
                f"{item.operational_definition}"
            ),
            _terms(
                " ".join(
                    [
                        item.academic_title,
                        *item.academic_concepts,
                        *item.academic_query_terms,
                    ]
                )
            ),
        )
        for item in fingerprint.academic_normalizations
        if item.status == "normalized"
    ]
    for seed_id, statement, source_claim_ids in seeds:
        claim_terms = _terms(statement)
        related_normalizations = sorted(
            normalization_terms,
            key=lambda item: -_match_score(claim_terms, item[0]),
        )
        if (
            related_normalizations
            and _match_score(claim_terms, related_normalizations[0][0])
            >= 0.12
        ):
            claim_terms |= related_normalizations[0][1]
        seed_terms = claim_terms | project_terms
        matched: list[tuple[float, TrendSignal]] = []
        for trend in trends:
            trend_terms = set(trend.terms)
            claim_score = _match_score(claim_terms, trend_terms)
            project_score = _match_score(project_terms, trend_terms)
            concepts = (seed_terms & trend_terms) & _CANONICAL_CONCEPTS
            claim_concepts = (claim_terms & trend_terms) & _DISTINCTIVE_CONCEPTS
            score = 0.7 * claim_score + 0.3 * project_score
            if (
                score >= 0.08
                and len(concepts) >= 2
                and bool(claim_concepts)
            ):
                matched.append((score, trend))
        matched.sort(key=lambda item: (-(item[0] * 0.7 + item[1].trend_score * 0.3), item[1].signal_id))
        top = matched[:6]
        project_match = max((item[0] for item in top), default=0.0)
        trend_score = max((item[1].trend_score for item in top), default=0.0)
        evidence_score = 0.75 if evidence_ready else (0.35 if local_paths else 0.1)
        origin = "trend_and_author" if top and source_claim_ids else "trend_recommended" if top else "author_asserted"
        final_score = 100 * (0.4 * project_match + 0.25 * trend_score + 0.25 * evidence_score + 0.1 * bool(source_claim_ids))
        missing = []
        if not evidence_ready:
            missing.extend(["frozen_protocol_output_binding", "independent_validation"])
        if not top:
            missing.append("external_trend_match")
        reasons = []
        if source_claim_ids:
            reasons.append("作者在项目文件中明确声称了该结论或贡献")
        if top:
            reasons.append(f"匹配到 {len(top)} 条外部趋势或学术注意力信号")
        if evidence_ready:
            reasons.append("项目存在完整的协议—输出—报告链，可进入验证")
        recommendations.append(
            RecommendedClaim(
                claim_id=_stable_id("recommended-claim", seed_id),
                statement=statement,
                origin=origin,  # type: ignore[arg-type]
                recommendation_score=max(0, min(100, round(final_score))),
                trend_score=round(trend_score * 100),
                project_match_score=round(project_match * 100),
                evidence_readiness_score=round(evidence_score * 100),
                matched_signal_ids=[item[1].signal_id for item in top],
                source_claim_ids=source_claim_ids,
                match_reasons=reasons,
                local_evidence_paths=local_paths,
                missing_context=missing,
            )
        )
    recommendations.sort(key=lambda item: (-item.recommendation_score, item.claim_id))
    return recommendations[:30]


def _discovery_strength(
    value: float,
) -> Literal["strong", "moderate", "limited", "unassessed"]:
    if value >= 0.75:
        return "strong"
    if value >= 0.45:
        return "moderate"
    if value > 0:
        return "limited"
    return "unassessed"


def _candidate_terms(candidate: Any) -> set[str]:
    return _terms(
        " ".join(
            str(_candidate_value(candidate, field, "") or "")
            for field in (
                "track_id",
                "display_title",
                "novelty_seed",
                "conclusion_excerpt",
            )
        )
    )


def _match_relation(signal: TrendSignal) -> str:
    if signal.signal_class in {"market_attention", "adoption_signal"}:
        return "attention_signal"
    lower = f"{signal.title} {signal.summary}".casefold()
    if any(
        cue in lower
        for cue in (
            "limitation",
            "negative result",
            "failure",
            "does not",
            "cannot",
            "局限",
            "失败",
            "无显著",
        )
    ):
        return "conflicting_context"
    if any(
        cue in lower
        for cue in ("benchmark", "baseline", "dataset", "method", "protocol", "基线", "数据集")
    ):
        return "method_or_baseline"
    return "closest_prior_work"


def build_discovery_portfolio(
    fingerprint: ProjectResearchFingerprint,
    candidates: Sequence[Any],
    claim_report: ClaimDiscoveryReport,
    query_intents: Sequence[DiscoveryQueryIntent],
    *,
    query_plan_id: str | None = None,
    resource_set_ids: Sequence[str] = (),
    coverage: dict[str, Any] | None = None,
) -> DiscoveryPortfolio:
    """Build candidate-specific discovery directions without verdict authority."""

    signals = {item.signal_id: item for item in claim_report.trend_signals}
    author_claims = {item.claim_id: item for item in claim_report.author_claims}
    candidate_rows = list(candidates)
    candidate_term_rows = [
        (candidate, _candidate_terms(candidate)) for candidate in candidate_rows
    ]
    claim_source_matches: list[ClaimSourceMatch] = []
    directions: list[DiscoveryDirection] = []
    accepted_terms: list[set[str]] = []
    recommendations = list(claim_report.recommended_claims)
    represented_statements = {
        item.statement.casefold() for item in recommendations
    }
    for candidate in candidate_rows:
        statement = str(
            _candidate_value(candidate, "novelty_seed", "")
            or _candidate_value(candidate, "display_title", "")
            or ""
        ).strip()
        if not statement or statement.casefold() in represented_statements:
            continue
        represented_statements.add(statement.casefold())
        local_paths = [
            str(path)
            for path in (
                _candidate_value(candidate, "protocol_path", ""),
                _candidate_value(candidate, "output_path", ""),
                _candidate_value(candidate, "report_path", ""),
                *_candidate_value(candidate, "implementation_paths", []),
                *_candidate_value(candidate, "test_paths", []),
            )
            if path
        ]
        readiness = max(
            0,
            min(
                100,
                int(
                    _candidate_value(candidate, "paperability_score", 0)
                    or 0
                ),
            ),
        )
        recommendations.append(
            RecommendedClaim(
                claim_id=_stable_id(
                    "candidate-claim",
                    f"{_candidate_value(candidate, 'track_id', '')}:{statement}",
                ),
                statement=statement,
                origin="author_asserted",
                recommendation_score=readiness,
                trend_score=0,
                project_match_score=100,
                evidence_readiness_score=max(10, readiness),
                local_evidence_paths=local_paths,
                match_reasons=[
                    "The direction is derived from the local project boundary, "
                    "implementation, and authored materials."
                ],
                missing_context=[
                    *(
                        ["No eligible author Claim was extracted"]
                        if not claim_report.recommended_claims
                        and not claim_report.author_claims
                        else []
                    ),
                    "frozen_protocol_output_binding",
                    "independent_validation",
                    "external_trend_match",
                ],
            )
        )
    if not recommendations:
        for candidate in candidate_rows[:5]:
            statement = str(
                _candidate_value(candidate, "novelty_seed", "")
                or _candidate_value(candidate, "display_title", "")
                or _candidate_value(candidate, "track_id", "")
            ).strip()
            if not statement:
                continue
            local_paths = [
                str(path)
                for path in (
                    _candidate_value(candidate, "protocol_path", ""),
                    _candidate_value(candidate, "output_path", ""),
                    _candidate_value(candidate, "report_path", ""),
                )
                if path
            ]
            readiness = int(
                max(
                    0,
                    min(
                        100,
                        int(
                            _candidate_value(
                                candidate, "paperability_score", 0
                            )
                            or 0
                        ),
                    ),
                )
            )
            recommendations.append(
                RecommendedClaim(
                    claim_id=_stable_id(
                        "candidate-claim",
                        f"{_candidate_value(candidate, 'track_id', '')}:{statement}",
                    ),
                    statement=statement,
                    origin="author_asserted",
                    recommendation_score=readiness,
                    trend_score=0,
                    project_match_score=100,
                    evidence_readiness_score=readiness,
                    local_evidence_paths=local_paths,
                    match_reasons=[
                        "The direction is derived from an explicit local "
                        "protocol/output/report candidate."
                    ],
                    missing_context=[
                        "No eligible author Claim was extracted; the owner "
                        "must review the candidate-derived hypothesis.",
                        "external_trend_match",
                    ],
                )
            )

    for recommendation in recommendations:
        source_claims = [
            author_claims[claim_id]
            for claim_id in recommendation.source_claim_ids
            if claim_id in author_claims
        ]
        if source_claims and not any(
            item.claim_type
            in {
                "research_question",
                "result",
                "comparative",
                "robustness",
                "novelty",
            }
            for item in source_claims
        ):
            continue
        if (
            "sha-256" in recommendation.statement.casefold()
            or re.search(
                r"\b[a-f0-9]{32,}\b", recommendation.statement.casefold()
            )
        ):
            continue
        statement_terms = _terms(recommendation.statement)
        if any(
            _match_score(statement_terms, existing) >= 0.62
            for existing in accepted_terms
        ):
            continue
        accepted_terms.append(statement_terms)

        source_paths = {
            span.path for claim in source_claims for span in claim.source_spans
        }
        exact_candidates = [
            candidate
            for candidate in candidate_rows
            if source_paths
            & {
                str(_candidate_value(candidate, "protocol_path", "") or ""),
                str(_candidate_value(candidate, "output_path", "") or ""),
                str(_candidate_value(candidate, "report_path", "") or ""),
            }
        ]
        ranked_candidates = sorted(
            candidate_term_rows,
            key=lambda item: (
                -_match_score(statement_terms, item[1]),
                -int(bool(_candidate_value(item[0], "closure_input_ready", False))),
                -int(_candidate_value(item[0], "paperability_score", 0) or 0),
            ),
        )
        statement_concepts = statement_terms & _CANONICAL_CONCEPTS

        def candidate_is_related(item: tuple[Any, set[str]]) -> bool:
            _, terms = item
            score = _match_score(statement_terms, terms)
            concept_overlap = statement_concepts & (terms & _CANONICAL_CONCEPTS)
            return score >= 0.35 or (score >= 0.20 and bool(concept_overlap))

        primary = (
            exact_candidates[0]
            if exact_candidates
            else ranked_candidates[0][0]
            if ranked_candidates
            and candidate_is_related(ranked_candidates[0])
            else None
        )
        related_candidates = list(exact_candidates)
        if not related_candidates:
            related_candidates.extend(
                item
                for item, terms in ranked_candidates
                if candidate_is_related((item, terms))
            )
        related_candidates = related_candidates[:2]
        if not related_candidates and primary is not None:
            related_candidates = [primary]

        matched_signals = [
            signals[signal_id]
            for signal_id in recommendation.matched_signal_ids
            if signal_id in signals
        ]
        relations: dict[str, list[str]] = {
            "closest_prior_work": [],
            "method_or_baseline": [],
            "conflicting_context": [],
            "supporting_context": [],
            "attention_signal": [],
        }
        for signal in matched_signals:
            relation = _match_relation(signal)
            relations[relation].append(signal.signal_id)
            evidence_role = (
                "attention_only"
                if relation == "attention_signal"
                else "background_only"
            )
            score = round(
                100
                * _match_score(
                    statement_terms | set(fingerprint.terms),
                    set(signal.terms) or _terms(f"{signal.title} {signal.summary}"),
                )
            )
            claim_source_matches.append(
                ClaimSourceMatch(
                    match_id=_stable_id(
                        "claim-source-match",
                        f"{recommendation.claim_id}:{signal.signal_id}:{relation}",
                    ),
                    claim_id=recommendation.claim_id,
                    source_signal_id=signal.signal_id,
                    relation=relation,  # type: ignore[arg-type]
                    match_score=max(0, min(100, score)),
                    reasons=[
                        "The source shares project and claim concepts.",
                        (
                            "This is an attention signal, not scientific evidence."
                            if evidence_role == "attention_only"
                            else "This source is discovery background only."
                        ),
                    ],
                    evidence_role=evidence_role,  # type: ignore[arg-type]
                )
            )

        verified = any(
            bool(_candidate_value(item, "artifact_chain_complete", False))
            and bool(_candidate_value(item, "protocol_bound_to_output", False))
            for item in related_candidates
        )
        local_paths = list(
            dict.fromkeys(
                [
                    *sorted(source_paths),
                    *[
                        str(path)
                        for item in related_candidates
                        for path in (
                            _candidate_value(item, "protocol_path", ""),
                            _candidate_value(item, "output_path", ""),
                            _candidate_value(item, "report_path", ""),
                            *_candidate_value(
                                item, "implementation_paths", []
                            ),
                            *_candidate_value(item, "test_paths", []),
                        )
                        if path
                    ],
                ]
            )
        )[:30]
        blockers = list(
            dict.fromkeys(
                [
                    *recommendation.missing_context,
                    *[
                        str(blocker)
                        for item in related_candidates
                        for blocker in _candidate_value(item, "blockers", [])
                    ],
                ]
            )
        )
        external_ids = [item.signal_id for item in matched_signals]
        scholarly_count = sum(
            item.signal_class in {"scholarly_attention", "official_source"}
            for item in matched_signals
        )
        attention_count = sum(
            item.signal_class in {"market_attention", "adoption_signal"}
            for item in matched_signals
        )
        track_ids = [
            str(_candidate_value(item, "track_id", ""))
            for item in related_candidates
            if str(_candidate_value(item, "track_id", "")).strip()
        ]
        normalizations = {
            item.source_track_id: item
            for item in fingerprint.academic_normalizations
        }
        academic_normalization = next(
            (
                normalizations[track_id]
                for track_id in track_ids
                if track_id in normalizations
            ),
            None,
        )
        internal_title = (
            str(_candidate_value(primary, "display_title", "") or "").strip()
            if primary is not None
            else ""
        ) or recommendation.statement[:140].rstrip("。.")
        title = (
            academic_normalization.academic_title
            if academic_normalization is not None
            and academic_normalization.academic_title
            else internal_title
        )
        if (
            academic_normalization is None
            or academic_normalization.status == "needs_owner_review"
        ):
            blockers.append(
                "Academic concept normalization requires owner review."
            )
        if (
            academic_normalization is not None
            and academic_normalization.operational_definition
            and "rare high-return event ranking"
            in academic_normalization.academic_concepts
        ):
            operational_definition = (
                academic_normalization.operational_definition.rstrip(
                    "。.!?？"
                )
            )
            question = (
                "在冻结样本、持有期和基线后，候选横截面排序模型能否提高"
                "由下述操作性定义确定的高收益事件在 Top-K 组合中的识别率："
                f"{operational_definition}？"
            )
            falsifiable_hypothesis = (
                "候选横截面排序模型相对于冻结基线具有更高的 Top-K "
                f"高收益事件命中率；事件定义为：{operational_definition}"
            )
        elif (
            academic_normalization is not None
            and "financial decision support systems"
            in academic_normalization.academic_concepts
        ):
            question = recommendation.statement.strip()
            falsifiable_hypothesis = (
                "Under the same model, question set, corpus, and token budget, "
                "multi-role review plus counter-evidence retrieval reduces the "
                "unsupported financial-claim rate and increases risk-factor "
                "coverage relative to a single-agent pipeline without a "
                "predeclared material loss in usefulness."
            )
        elif (
            academic_normalization is not None
            and academic_normalization.comparison_frame
        ):
            frame = academic_normalization.comparison_frame
            question = str(frame["research_question"])
            falsifiable_hypothesis = str(frame["falsifiable_hypothesis"])
        else:
            question = recommendation.statement.strip()
            falsifiable_hypothesis = recommendation.statement
        if not question.endswith(("?", "？")):
            question = (
                "在冻结的项目资源与评价口径下，是否能够验证："
                + question.rstrip("。.;；")
                + "？"
            )
        contribution = (
            str(_candidate_value(primary, "novelty_seed", "") or "").strip()
            if primary is not None
            else ""
        ) or recommendation.statement
        if (
            academic_normalization is not None
            and academic_normalization.comparison_frame.get(
                "candidate_contribution"
            )
        ):
            contribution = str(
                academic_normalization.comparison_frame[
                    "candidate_contribution"
                ]
            )
        prior_relation = (
            f"已匹配 {scholarly_count} 条相关学术或官方来源；"
            "它们用于界定最近工作，不直接支持项目结论。"
            if scholarly_count
            else "尚未获得足够的最近工作匹配，不能声称新颖性。"
        )
        direction = DiscoveryDirection(
            direction_id=_stable_id(
                "discovery-direction",
                f"{recommendation.claim_id}:{recommendation.statement.casefold()}",
            ),
            title=title[:240],
            research_question=question[:1200],
            falsifiable_hypothesis=falsifiable_hypothesis[:2000],
            candidate_contribution=contribution[:2000],
            scope_in=[
                "read-only project resources bound to this direction",
                "frozen external discovery sources",
                "a prospective test under a later Research Contract",
            ],
            scope_out=[
                "claims not bound to project artifacts",
                "treating attention signals as scientific evidence",
                "claiming novelty before closest-prior-work review",
            ],
            source_claim_ids=recommendation.source_claim_ids,
            supporting_track_ids=track_ids,
            primary_track_id=track_ids[0] if track_ids else None,
            local_evidence_paths=local_paths,
            external_source_ids=external_ids,
            closest_prior_work_ids=[
                *relations["closest_prior_work"],
                *relations["method_or_baseline"],
            ],
            conflicting_source_ids=relations["conflicting_context"],
            trend_signal_ids=relations["attention_signal"],
            relation_to_prior_work=prior_relation,
            novelty_grounding=_discovery_strength(min(1.0, scholarly_count / 3)),
            evidence_readiness=(
                "strong"
                if verified
                else "moderate"
                if local_paths
                else "limited"
            ),
            feasibility=(
                "strong"
                if primary is not None
                and bool(_candidate_value(primary, "closure_input_ready", False))
                else "moderate"
                if primary is not None
                else "limited"
            ),
            external_attention=_discovery_strength(min(1.0, attention_count / 3)),
            evidence_chain_level=(
                "verified_chain" if verified else "inferred_chain"
            ),
            recommendation_reasons=recommendation.match_reasons,
            blockers=blockers,
            prohibited_claims=[
                "Do not treat discovery sources as experiment evidence.",
                "Do not claim exhaustive literature coverage.",
                "Do not claim novelty without an explicit closest-work comparison.",
            ],
            internal_label=internal_title,
            operational_definition=(
                academic_normalization.operational_definition
                if academic_normalization is not None
                else ""
            ),
            academic_concepts=(
                academic_normalization.academic_concepts
                if academic_normalization is not None
                else []
            ),
            academic_query_terms=(
                academic_normalization.academic_query_terms
                if academic_normalization is not None
                else []
            ),
            comparison_frame=(
                academic_normalization.comparison_frame
                if academic_normalization is not None
                else {}
            ),
            academic_normalization_status=(
                academic_normalization.status
                if academic_normalization is not None
                else "needs_owner_review"
            ),
        )
        directions.append(direction)

    if not directions and claim_report.recommended_claims:
        return build_discovery_portfolio(
            fingerprint,
            candidate_rows,
            claim_report.model_copy(update={"recommended_claims": []}),
            query_intents,
            query_plan_id=query_plan_id,
            resource_set_ids=resource_set_ids,
            coverage=coverage,
        )

    directions.sort(
        key=lambda item: (
            item.evidence_readiness != "strong",
            item.feasibility != "strong",
            item.novelty_grounding not in {"strong", "moderate"},
            bool(item.blockers),
            item.direction_id,
        )
    )
    unique_directions: dict[str, DiscoveryDirection] = {}
    for direction in directions:
        key = (
            f"track:{direction.primary_track_id}"
            if direction.primary_track_id
            else f"title:{direction.title.casefold()}"
        )
        existing = unique_directions.get(key)
        if existing is None:
            unique_directions[key] = direction
            continue
        unique_directions[key] = existing.model_copy(
            update={
                "source_claim_ids": list(
                    dict.fromkeys(
                        [
                            *existing.source_claim_ids,
                            *direction.source_claim_ids,
                        ]
                    )
                ),
                "recommendation_reasons": list(
                    dict.fromkeys(
                        [
                            *existing.recommendation_reasons,
                            *direction.recommendation_reasons,
                        ]
                    )
                ),
                "blockers": list(
                    dict.fromkeys(
                        [*existing.blockers, *direction.blockers]
                    )
                ),
            }
        )
    directions = list(unique_directions.values())[:5]
    external_complete = any(
        item.external_source_ids for item in directions
    )
    status: Literal[
        "ready_for_scope_selection",
        "external_grounding_incomplete",
        "no_viable_direction",
    ]
    if not directions:
        status = "no_viable_direction"
    elif external_complete:
        status = "ready_for_scope_selection"
    else:
        status = "external_grounding_incomplete"
    return DiscoveryPortfolio(
        generated_at=_now(),
        fingerprint=fingerprint,
        query_plan_id=query_plan_id,
        query_intents=list(query_intents),
        claim_source_matches=claim_source_matches,
        directions=directions,
        recommended_direction_id=(
            directions[0].direction_id if directions else None
        ),
        resource_set_ids=list(dict.fromkeys(resource_set_ids)),
        coverage=coverage or {},
        status=status,
    )


def discover_project_claims(
    source_root: str | Path,
    resources: Sequence[Any],
    candidates: Sequence[Any],
    *,
    include_external: bool = True,
    http_json: HttpJson | None = None,
) -> ClaimDiscoveryReport:
    root = Path(source_root).resolve()
    author_claims = extract_author_claims(root, resources)
    fingerprint = build_project_fingerprint(
        resources,
        candidates,
        author_claims,
        source_root=source_root,
    )
    queries = _project_queries(fingerprint)
    provider_status: dict[str, str] = {}
    warnings: list[str] = []
    trends: list[TrendSignal] = []
    if not include_external:
        provider_status = {
            "redfox_wechat": "not_requested",
            "semantic_scholar": "not_requested",
            "crossref": "not_requested",
        }
    else:
        if http_json is None:
            raise ValueError(
                "legacy claim discovery cannot perform direct network access; "
                "use Workflow v2 RetrievalGateway or inject a provider transport "
                "for an offline test"
            )
        redfox_key = _env_value(root / ".env.local", "REDFOX_API_KEY")
        if redfox_key:
            try:
                redfox = fetch_redfox_trends(queries, api_key=redfox_key, http_json=http_json)
                trends.extend(redfox)
                provider_status["redfox_wechat"] = f"ok:{len(redfox)}"
            except Exception as exc:
                provider_status["redfox_wechat"] = f"degraded:{type(exc).__name__}"
                warnings.append(f"RedFox trend retrieval degraded: {exc}")
        else:
            provider_status["redfox_wechat"] = "not_configured"
        try:
            scholarly_queries = _scholarly_queries(fingerprint)
            scholarly = fetch_scholarly_trends(
                scholarly_queries or queries[:4],
                api_key=os.getenv("S2_API_KEY") or None,
                http_json=http_json,
            )
            trends.extend(scholarly)
            provider_status["semantic_scholar"] = f"ok:{len(scholarly)}"
        except Exception as exc:
            provider_status["semantic_scholar"] = f"degraded:{type(exc).__name__}"
            warnings.append(f"Semantic Scholar trend retrieval degraded: {exc}")
        try:
            crossref = fetch_crossref_trends(
                scholarly_queries or queries[:4], http_json=http_json
            )
            trends.extend(crossref)
            provider_status["crossref"] = f"ok:{len(crossref)}"
        except Exception as exc:
            provider_status["crossref"] = f"degraded:{type(exc).__name__}"
            warnings.append(f"Crossref trend retrieval degraded: {exc}")
    recommendations = recommend_claims(fingerprint, author_claims, trends, candidates)
    return ClaimDiscoveryReport(
        generated_at=_now(),
        source_root=str(root),
        fingerprint=fingerprint,
        author_claims=author_claims,
        trend_signals=trends,
        recommended_claims=recommendations,
        provider_status=provider_status,
        warnings=warnings,
    )
