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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence

from pydantic import Field

from .models import StrictModel


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
    "random_baseline", "concentration",
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


class ProjectResearchFingerprint(StrictModel):
    domains: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    evidence_assets: list[str] = Field(default_factory=list)
    source_track_ids: list[str] = Field(default_factory=list)


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


def _markdown_claims(path: Path, relative: str, sha256: str) -> list[AuthorClaim]:
    text = path.read_text(encoding="utf-8", errors="replace")[:500_000]
    lines = text.splitlines()
    active_heading = ""
    active_level = 7
    found: list[AuthorClaim] = []
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
        if not inline and not active_heading:
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


def _json_claims(path: Path, relative: str, sha256: str) -> list[AuthorClaim]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    found: list[AuthorClaim] = []

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                item_path = f"{prefix}.{key}" if prefix else str(key)
                if str(key).casefold() in _CLAIM_KEYS and isinstance(item, str):
                    statement = _clean_statement(item)
                    if len(statement) >= 12:
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
        if not relative or size > 500_000:
            continue
        path = source_root / relative
        digest = str(_resource_value(resource, "sha256", ""))
        if suffix in {".md", ".txt"}:
            candidates.extend(_markdown_claims(path, relative, digest))
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
    }
    terms.update(
        concept
        for concept, aliases in concepts.items()
        if any(alias in lower for alias in aliases)
    )
    return terms


def build_project_fingerprint(
    resources: Sequence[Any], candidates: Sequence[Any], author_claims: Sequence[AuthorClaim]
) -> ProjectResearchFingerprint:
    candidate_text = " ".join(
        f"{_candidate_value(item, 'track_id', '')} {_candidate_value(item, 'display_title', '')} {_candidate_value(item, 'novelty_seed', '')}"
        for item in candidates
    )
    claim_text = " ".join(item.statement for item in author_claims)
    all_text = f"{candidate_text} {claim_text}"
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
                "fields": "title,abstract,url,year,publicationDate,citationCount,influentialCitationCount,venue",
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
                "select": "DOI,title,abstract,URL,published,created,is-referenced-by-count,publisher,container-title",
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
    for track_id in fingerprint.source_track_ids:
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
        if len(topic_seeds) >= 3:
            break
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
        claim_terms = [
            item.replace("_", " ")
            for item in sorted(_terms(statement))
            if item in _CANONICAL_CONCEPTS
            or re.fullmatch(r"[a-z][a-z0-9-]{3,}", item)
        ]
        claim_query = " ".join(claim_terms[:8]).strip() or statement[:120]
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
    for seed_id, statement, source_claim_ids in seeds:
        claim_terms = _terms(statement)
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

    for recommendation in claim_report.recommended_claims:
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
        title = (
            str(_candidate_value(primary, "display_title", "") or "").strip()
            if primary is not None
            else ""
        ) or recommendation.statement[:140].rstrip("。.")
        question = recommendation.statement.strip()
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
        prior_relation = (
            f"已匹配 {scholarly_count} 条相关学术或官方来源；"
            "它们用于界定最近工作，不直接支持项目结论。"
            if scholarly_count
            else "尚未获得足够的最近工作匹配，不能声称新颖性。"
        )
        track_ids = [
            str(_candidate_value(item, "track_id", ""))
            for item in related_candidates
            if str(_candidate_value(item, "track_id", "")).strip()
        ]
        direction = DiscoveryDirection(
            direction_id=_stable_id(
                "discovery-direction",
                f"{recommendation.claim_id}:{recommendation.statement.casefold()}",
            ),
            title=title[:240],
            research_question=question[:1200],
            falsifiable_hypothesis=recommendation.statement[:2000],
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
        )
        directions.append(direction)

    directions.sort(
        key=lambda item: (
            item.evidence_readiness != "strong",
            item.feasibility != "strong",
            item.novelty_grounding not in {"strong", "moderate"},
            bool(item.blockers),
            item.direction_id,
        )
    )
    directions = directions[:5]
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
    fingerprint = build_project_fingerprint(resources, candidates, author_claims)
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
