from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .models import (
    EventRecord,
    LiteratureCandidate,
    LiteratureApprovalRecord,
    LiteratureDiscoveryRecord,
    LiteratureReviewEnvelope,
    LiteratureSearchPlan,
    LiteratureScreeningRecord,
    LiteratureSource,
    LiteratureSourceType,
    LiteratureSynthesis,
    PlanEvidenceBinding,
    ResearchPlanDraft,
    Stage,
    Stage1Audit,
    Stage1Manifest,
    utc_now,
)
from .storage import (
    append_jsonl,
    load_meta,
    load_state,
    read_json,
    safe_relative,
    sha256_file,
    write_json_atomic,
)


CROSSREF_BASE = "https://api.crossref.org/v1"
SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"
MIN_VERIFIED_PAPERS = 5
MIN_CANDIDATES = 8
_STOPWORDS = {
    "about",
    "after",
    "against",
    "among",
    "and",
    "are",
    "based",
    "between",
    "for",
    "from",
    "how",
    "into",
    "its",
    "of",
    "on",
    "or",
    "research",
    "scientific",
    "study",
    "that",
    "the",
    "their",
    "this",
    "through",
    "toward",
    "using",
    "with",
}


def _stamp(prefix: str) -> str:
    return f"{prefix}-{utc_now().replace(':', '').replace('+00:00', 'Z')}-{uuid.uuid4().hex[:6]}"


def _strip_markup(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", value.casefold())
        if token not in _STOPWORDS
    }


def _first(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0] or "")
    return str(value or "")


def _crossref_year(item: dict[str, Any]) -> int | None:
    for field in ("published", "published-print", "published-online", "issued", "created"):
        value = item.get(field)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                pass
    return None


def _crossref_authors(item: dict[str, Any]) -> list[str]:
    authors: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(filter(None, (str(author.get("given") or "").strip(), str(author.get("family") or "").strip())))
        if name:
            authors.append(name)
    return authors


def _doi(value: object) -> str | None:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text if text.startswith("10.") and "/" in text else None


def _candidate_id(identity: str) -> str:
    return "candidate-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _source_id(candidate: LiteratureCandidate) -> str:
    identity = candidate.doi or candidate.arxiv_id or _normalized_title(candidate.title)
    return "paper-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


@dataclass
class ScholarlyApiClient:
    timeout_seconds: int = 30
    crossref_mailto: str | None = None
    semantic_scholar_api_key: str | None = None
    _last_request_at: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def get(self, url: str, provider: str) -> tuple[dict[str, Any], bytes]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "ResearchForge/0.2 (+local literature review tool)",
        }
        if provider == "semantic_scholar" and self.semantic_scholar_api_key:
            headers["x-api-key"] = self.semantic_scholar_api_key
        minimum_interval = 1.1 if provider == "semantic_scholar" else 0.05
        elapsed = time.monotonic() - self._last_request_at.get(provider, 0.0)
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        request = urllib.request.Request(url, headers=headers, method="GET")
        raw = b""
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                self._last_request_at[provider] = time.monotonic()
                break
            except urllib.error.HTTPError as exc:
                body = exc.read(1000).decode("utf-8", errors="replace")
                if exc.code == 429 and attempt == 0:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        delay = min(max(float(retry_after or 2.0), 1.0), 5.0)
                    except ValueError:
                        delay = 2.0
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"{provider} HTTP {exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"{provider} request failed: {exc.reason}") from exc
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{provider} returned a non-object JSON response")
        return value, raw


def _client() -> ScholarlyApiClient:
    return ScholarlyApiClient(
        crossref_mailto=os.getenv("CROSSREF_MAILTO") or None,
        semantic_scholar_api_key=os.getenv("S2_API_KEY") or None,
    )


async def plan_literature_search(project: Path, focus: str = "") -> tuple[str, LiteratureSearchPlan]:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("literature search planning is closed after contracts are frozen")
    meta = load_meta(project)
    prompt = json.dumps(
        {
            "research_idea": meta.idea,
            "user_focus": focus,
            "instruction": (
                "Create 3-6 complementary English scholarly search queries. Cover direct methods, "
                "strong baselines, and critical/evaluation work without inventing papers."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    from .agent_runtime import generate_literature_search_plan, model_name

    plan = await generate_literature_search_plan(prompt, cwd=project)
    plan_id = _stamp("search-plan")
    path = project / "literature" / "search_plans" / f"{plan_id}.json"
    write_json_atomic(path, plan)
    write_json_atomic(project / "literature" / "latest_search_plan.json", {"search_plan_id": plan_id})
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="literature_search_planned",
            from_stage=state.stage,
            to_stage=state.stage,
            details={"search_plan_id": plan_id, "model": model_name()},
        ),
    )
    return plan_id, plan


def _crossref_search_url(query: str, rows: int, from_year: int | None, mailto: str | None) -> str:
    params: dict[str, object] = {"query.bibliographic": query, "rows": rows}
    if from_year is not None:
        params["filter"] = f"from-pub-date:{from_year}-01-01"
    if mailto:
        params["mailto"] = mailto
    return f"{CROSSREF_BASE}/works?{urllib.parse.urlencode(params)}"


def _s2_search_url(query: str, rows: int, from_year: int | None) -> str:
    params: dict[str, object] = {
        "query": query.replace("-", " "),
        "limit": rows,
        "fields": (
            "title,abstract,authors,year,venue,publicationTypes,citationCount,url,externalIds"
        ),
    }
    if from_year is not None:
        params["year"] = f"{from_year}-"
    return f"{SEMANTIC_SCHOLAR_BASE}/paper/search?{urllib.parse.urlencode(params)}"


def _crossref_candidate(item: dict[str, Any], query: str, rank: int) -> dict[str, Any] | None:
    title = _strip_markup(_first(item.get("title")))
    if len(title) < 3:
        return None
    doi = _doi(item.get("DOI"))
    locator = f"https://doi.org/{doi}" if doi else str(item.get("URL") or "")
    if not locator:
        return None
    return {
        "title": title,
        "authors": _crossref_authors(item),
        "year": _crossref_year(item),
        "abstract": _strip_markup(item.get("abstract")),
        "venue": _strip_markup(_first(item.get("container-title"))),
        "work_type": str(item.get("type") or "unknown"),
        "doi": doi,
        "arxiv_id": None,
        "locator": locator,
        "citation_count": max(0, int(item.get("is-referenced-by-count") or 0)),
        "provider_ids": {"crossref": doi or locator},
        "matched_queries": [query],
        "provider_scores": {"crossref": 1.0 / rank},
    }


def _s2_candidate(item: dict[str, Any], query: str, rank: int) -> dict[str, Any] | None:
    title = _strip_markup(item.get("title"))
    paper_id = str(item.get("paperId") or "").strip()
    if len(title) < 3 or not paper_id:
        return None
    external = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    doi = _doi(external.get("DOI"))
    arxiv_id = str(external.get("ArXiv") or "").strip() or None
    authors = [
        str(author.get("name") or "").strip()
        for author in item.get("authors") or []
        if isinstance(author, dict) and str(author.get("name") or "").strip()
    ]
    types = item.get("publicationTypes") or []
    return {
        "title": title,
        "authors": authors,
        "year": int(item["year"]) if item.get("year") else None,
        "abstract": _strip_markup(item.get("abstract")),
        "venue": _strip_markup(item.get("venue")),
        "work_type": ",".join(str(value) for value in types) or "unknown",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "locator": str(item.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}"),
        "citation_count": max(0, int(item.get("citationCount") or 0)),
        "provider_ids": {"semantic_scholar": paper_id},
        "matched_queries": [query],
        "provider_scores": {"semantic_scholar": 1.0 / rank},
    }


def _identity(item: dict[str, Any]) -> str:
    if item.get("doi"):
        return "doi:" + str(item["doi"])
    if item.get("arxiv_id"):
        return "arxiv:" + str(item["arxiv_id"]).casefold()
    return "title:" + _normalized_title(str(item["title"]))


def _merge_candidate(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for field in ("abstract", "venue", "work_type", "locator"):
        if len(str(incoming.get(field) or "")) > len(str(target.get(field) or "")):
            target[field] = incoming[field]
    if len(incoming.get("authors") or []) > len(target.get("authors") or []):
        target["authors"] = incoming["authors"]
    target["year"] = target.get("year") or incoming.get("year")
    target["doi"] = target.get("doi") or incoming.get("doi")
    target["arxiv_id"] = target.get("arxiv_id") or incoming.get("arxiv_id")
    target["citation_count"] = max(int(target.get("citation_count") or 0), int(incoming.get("citation_count") or 0))
    target["provider_ids"].update(incoming["provider_ids"])
    target["provider_scores"].update(incoming["provider_scores"])
    target["matched_queries"] = sorted(set(target["matched_queries"] + incoming["matched_queries"]))


def _relevance(item: dict[str, Any], plan: LiteratureSearchPlan) -> float:
    content = " ".join((item["title"], item.get("abstract") or "", item.get("venue") or ""))
    content_tokens = _tokens(content)
    query_coverage = 0.0
    for query in item["matched_queries"]:
        query_tokens = _tokens(query)
        if query_tokens:
            query_coverage = max(query_coverage, len(query_tokens & content_tokens) / len(query_tokens))
    concept_tokens = _tokens(" ".join(plan.key_concepts))
    concept_coverage = len(concept_tokens & content_tokens) / len(concept_tokens) if concept_tokens else 0.0
    provider_support = min(len(item["provider_ids"]) / 2.0, 1.0)
    abstract_support = 1.0 if len(item.get("abstract") or "") >= 100 else 0.0
    citation_support = min(math.log1p(item.get("citation_count") or 0) / math.log(1001), 1.0)
    score = (
        0.45 * query_coverage
        + 0.25 * concept_coverage
        + 0.15 * provider_support
        + 0.10 * abstract_support
        + 0.05 * citation_support
    )
    return round(min(max(score, 0.0), 1.0), 8)


def _titles_match(left: str, right: str) -> bool:
    return SequenceMatcher(None, _normalized_title(left), _normalized_title(right)).ratio() >= 0.92


def _write_raw(run_dir: Path, name: str, raw: bytes, hashes: dict[str, str], project: Path) -> None:
    path = run_dir / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    relative = path.relative_to(project).as_posix()
    hashes[relative] = sha256_file(path)


def _verify_candidate(
    candidate: dict[str, Any],
    client: ScholarlyApiClient,
    run_dir: Path,
    raw_hashes: dict[str, str],
    project: Path,
) -> tuple[str, str]:
    if len(candidate["provider_ids"]) >= 2:
        return "cross_provider", "Matched by DOI/arXiv ID or normalized title across Crossref and Semantic Scholar."
    doi = candidate.get("doi")
    if doi:
        url = f"{CROSSREF_BASE}/works/{urllib.parse.quote(str(doi), safe='')}"
        if client.crossref_mailto:
            url += "?" + urllib.parse.urlencode({"mailto": client.crossref_mailto})
        try:
            payload, raw = client.get(url, "crossref")
            _write_raw(run_dir, f"verify-crossref-{candidate['candidate_id']}.json", raw, raw_hashes, project)
            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            exact_doi = _doi(message.get("DOI"))
            exact_title = _strip_markup(_first(message.get("title")))
            if exact_doi == doi and _titles_match(candidate["title"], exact_title):
                return "canonical", "Exact Crossref DOI lookup matched the normalized title."
        except Exception:
            pass
    paper_id = candidate["provider_ids"].get("semantic_scholar")
    if paper_id:
        params = urllib.parse.urlencode({"fields": "title,externalIds"})
        url = f"{SEMANTIC_SCHOLAR_BASE}/paper/{urllib.parse.quote(paper_id, safe='')}?{params}"
        try:
            payload, raw = client.get(url, "semantic_scholar")
            _write_raw(run_dir, f"verify-s2-{candidate['candidate_id']}.json", raw, raw_hashes, project)
            if str(payload.get("paperId") or "") == paper_id and _titles_match(candidate["title"], str(payload.get("title") or "")):
                return "canonical", "Exact Semantic Scholar paper-ID lookup matched the normalized title."
        except Exception:
            pass
    return "unverified", "No independent or exact canonical identifier match succeeded."


def _register_candidate_source(
    project: Path, candidate: LiteratureCandidate, discovery_id: str
) -> str:
    source_id = _source_id(candidate)
    source = LiteratureSource(
        source_id=source_id,
        source_type=LiteratureSourceType.PAPER,
        title=candidate.title,
        authors=candidate.authors or ["Unknown authors in provider metadata"],
        year=candidate.year,
        locator=(f"https://doi.org/{candidate.doi}" if candidate.doi else candidate.locator),
        notes=(candidate.abstract[:5000] if candidate.abstract else "Metadata-only record; no abstract was returned by the queried providers."),
        verified=True,
        verification_method=candidate.verification_method,
        origin="discovery",
        origin_id=discovery_id,
    )
    path = project / "literature" / "sources" / f"{source_id}.json"
    if path.is_file():
        existing = LiteratureSource.model_validate(read_json(path))
        if existing.title != source.title or existing.locator != source.locator:
            raise ValueError(f"source ID collision for {source_id}")
        if existing.origin == "discovery":
            write_json_atomic(path, source)
        return source_id
    write_json_atomic(path, source)
    return source_id


def discover_literature(
    project: Path,
    *,
    search_plan_id: str = "latest",
    rows_per_query: int = 20,
    include_count: int = 10,
    from_year: int | None = None,
    min_relevance: float = 0.15,
    client: ScholarlyApiClient | None = None,
) -> LiteratureDiscoveryRecord:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("literature discovery is closed after contracts are frozen")
    if not 5 <= rows_per_query <= 100:
        raise ValueError("rows_per_query must be between 5 and 100")
    if not MIN_VERIFIED_PAPERS <= include_count <= 50:
        raise ValueError(f"include_count must be between {MIN_VERIFIED_PAPERS} and 50")
    if not 0.0 <= min_relevance <= 1.0:
        raise ValueError("min_relevance must be between 0 and 1")

    if search_plan_id == "latest":
        search_plan_id = str(read_json(project / "literature" / "latest_search_plan.json")["search_plan_id"])
    plan_path = project / "literature" / "search_plans" / f"{search_plan_id}.json"
    plan = LiteratureSearchPlan.model_validate(read_json(plan_path))
    api = client or _client()
    discovery_id = _stamp("discovery")
    run_dir = project / "literature" / "discoveries" / discovery_id
    run_dir.mkdir(parents=True)
    raw_hashes: dict[str, str] = {}
    provider_status: dict[str, str] = {}
    merged: dict[str, dict[str, Any]] = {}

    providers = ("crossref", "semantic_scholar")
    for provider in providers:
        successes = 0
        errors: list[str] = []
        for query_index, query in enumerate(plan.queries, start=1):
            try:
                if provider == "crossref":
                    url = _crossref_search_url(query, rows_per_query, from_year, api.crossref_mailto)
                    payload, raw = api.get(url, provider)
                    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
                    items = message.get("items") if isinstance(message.get("items"), list) else []
                    normalize = _crossref_candidate
                else:
                    url = _s2_search_url(query, rows_per_query, from_year)
                    payload, raw = api.get(url, provider)
                    items = payload.get("data") if isinstance(payload.get("data"), list) else []
                    normalize = _s2_candidate
                _write_raw(run_dir, f"search-{provider}-{query_index:02d}.json", raw, raw_hashes, project)
                successes += 1
                for rank, item in enumerate(items, start=1):
                    if not isinstance(item, dict):
                        continue
                    normalized = normalize(item, query, rank)
                    if normalized is None:
                        continue
                    key = _identity(normalized)
                    if key in merged:
                        _merge_candidate(merged[key], normalized)
                    else:
                        merged[key] = normalized
            except Exception as exc:
                errors.append(f"query {query_index}: {type(exc).__name__}: {exc}")
        provider_status[provider] = (
            f"ok:{successes}/{len(plan.queries)}"
            if successes == len(plan.queries)
            else f"degraded:{successes}/{len(plan.queries)}; " + " | ".join(errors[:3])
        )

    if not merged:
        raise RuntimeError("all scholarly providers returned zero usable candidates")
    provisional: list[dict[str, Any]] = []
    for item in merged.values():
        identity = _identity(item)
        item["candidate_id"] = _candidate_id(identity)
        item["relevance_score"] = _relevance(item, plan)
        provisional.append(item)
    provisional.sort(
        key=lambda item: (
            item["relevance_score"],
            len(item["provider_ids"]),
            item["citation_count"],
            item.get("year") or 0,
        ),
        reverse=True,
    )

    verify_limit = min(len(provisional), max(include_count * 2, 20))
    for item in provisional[:verify_limit]:
        status, method = _verify_candidate(item, api, run_dir, raw_hashes, project)
        item["verification_status"] = status
        item["verification_method"] = method
    for item in provisional[verify_limit:]:
        item["verification_status"] = "unverified"
        item["verification_method"] = "Outside the bounded canonical-verification budget."

    hard_excluded_types = {"peer-review", "reference-entry", "reference-book", "component"}
    eligible_items: list[dict[str, Any]] = []
    hard_reasons: dict[str, list[str]] = {}
    for item in provisional:
        reasons: list[str] = []
        if item["verification_status"] == "unverified":
            reasons.append("canonical metadata verification did not pass")
        if item["relevance_score"] < min_relevance:
            reasons.append(f"relevance score below threshold {min_relevance}")
        if from_year is not None and (item.get("year") is None or item["year"] < from_year):
            reasons.append(f"publication year is missing or before {from_year}")
        if not item.get("authors"):
            reasons.append("provider metadata contains no authors")
        if str(item.get("work_type") or "").casefold() in hard_excluded_types:
            reasons.append(f"work type {item.get('work_type')} is excluded")
        if re.match(r"^(review for|correction|retraction|editorial)", item["title"], re.IGNORECASE):
            reasons.append("title indicates a review, correction, retraction, or editorial record")
        hard_reasons[item["candidate_id"]] = reasons
        if not reasons:
            eligible_items.append(item)

    selected: set[str] = set()
    quota = max(1, include_count // len(plan.queries))
    selection_reason: dict[str, str] = {}
    for query in plan.queries:
        matches = [item for item in eligible_items if query in item["matched_queries"]]
        for item in matches[:quota]:
            if len(selected) >= include_count:
                break
            if item["candidate_id"] not in selected:
                selected.add(item["candidate_id"])
                selection_reason[item["candidate_id"]] = "selected by the fixed per-query coverage quota"
    for item in eligible_items:
        if len(selected) >= include_count:
            break
        if item["candidate_id"] not in selected:
            selected.add(item["candidate_id"])
            selection_reason[item["candidate_id"]] = "selected by global relevance after query quotas"

    candidates: list[LiteratureCandidate] = []
    selected_candidates: list[LiteratureCandidate] = []
    for item in provisional:
        reasons = list(hard_reasons[item["candidate_id"]])
        if item["candidate_id"] in selected:
            status = "included"
            reasons.append(selection_reason[item["candidate_id"]])
        else:
            status = "excluded"
            if not reasons:
                reasons.append("outside the fixed inclusion budget")
        candidate = LiteratureCandidate(
            **item,
            screening_status=status,
            screening_reasons=reasons,
        )
        candidates.append(candidate)
        if status == "included":
            selected_candidates.append(candidate)

    if len(selected_candidates) < MIN_VERIFIED_PAPERS:
        raise RuntimeError(
            f"only {len(selected_candidates)} verified papers passed screening; "
            f"at least {MIN_VERIFIED_PAPERS} are required"
        )
    selected_source_ids = {_source_id(candidate) for candidate in selected_candidates}
    for source_path in sorted((project / "literature" / "sources").glob("*.json")):
        source = LiteratureSource.model_validate(read_json(source_path))
        if source.origin == "discovery" and source.source_id not in selected_source_ids:
            source_path.unlink()
    included_source_ids = [
        _register_candidate_source(project, candidate, discovery_id)
        for candidate in selected_candidates
    ]
    record = LiteratureDiscoveryRecord(
        discovery_id=discovery_id,
        search_plan_id=search_plan_id,
        providers_requested=list(providers),
        provider_status=provider_status,
        raw_response_hashes=raw_hashes,
        candidates=candidates,
        included_source_ids=included_source_ids,
    )
    write_json_atomic(run_dir / "record.json", record)
    write_json_atomic(project / "literature" / "latest_discovery.json", {"discovery_id": discovery_id})
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="literature_discovery_completed",
            from_stage=state.stage,
            to_stage=state.stage,
            details={
                "discovery_id": discovery_id,
                "candidate_count": len(candidates),
                "included_count": len(included_source_ids),
            },
        ),
    )
    return record


def _latest_discovery(project: Path) -> LiteratureDiscoveryRecord:
    discovery_id = str(read_json(project / "literature" / "latest_discovery.json")["discovery_id"])
    return LiteratureDiscoveryRecord.model_validate(
        read_json(project / "literature" / "discoveries" / discovery_id / "record.json")
    )


async def screen_literature(
    project: Path,
    *,
    max_candidates: int = 40,
    include_count: int = 12,
) -> LiteratureScreeningRecord:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("literature screening is closed after contracts are frozen")
    if not 10 <= max_candidates <= 60:
        raise ValueError("max_candidates must be between 10 and 60")
    if not MIN_VERIFIED_PAPERS <= include_count <= min(50, max_candidates):
        raise ValueError("include_count must be at least 5 and cannot exceed max_candidates")
    discovery = _latest_discovery(project)
    plan = LiteratureSearchPlan.model_validate(
        read_json(project / "literature" / "search_plans" / f"{discovery.search_plan_id}.json")
    )
    hard_excluded_types = {"peer-review", "reference-entry", "reference-book", "component"}
    pool = [
        candidate
        for candidate in discovery.candidates
        if candidate.verification_status != "unverified"
        and candidate.authors
        and candidate.work_type.casefold() not in hard_excluded_types
        and not re.match(
            r"^(review for|correction|retraction|editorial)", candidate.title, re.IGNORECASE
        )
    ][:max_candidates]
    if len(pool) < MIN_VERIFIED_PAPERS:
        raise ValueError("too few verified candidates are available for semantic screening")
    prompt = json.dumps(
        {
            "review_question": plan.review_question,
            "inclusion_criteria": plan.inclusion_criteria,
            "exclusion_criteria": plan.exclusion_criteria,
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "title": candidate.title,
                    "year": candidate.year,
                    "authors": candidate.authors,
                    "abstract_or_metadata": candidate.abstract[:2000],
                    "venue": candidate.venue,
                    "work_type": candidate.work_type,
                    "matched_queries": candidate.matched_queries,
                    "verification_status": candidate.verification_status,
                }
                for candidate in pool
            ],
            "instruction": "Return exactly one conservative relevance decision per candidate ID.",
        },
        ensure_ascii=False,
        indent=2,
    )
    from .agent_runtime import model_name, screen_literature_candidates

    output = await screen_literature_candidates(prompt, cwd=project)
    evaluated_ids = [candidate.candidate_id for candidate in pool]
    decision_ids = [decision.candidate_id for decision in output.decisions]
    if decision_ids != evaluated_ids:
        raise ValueError("screening output must preserve and cover the supplied candidate order exactly")
    decisions = {decision.candidate_id: decision for decision in output.decisions}
    accepted = [
        candidate
        for candidate in pool
        if decisions[candidate.candidate_id].decision in {"core", "supporting"}
    ]
    core_count = sum(
        1 for candidate in pool if decisions[candidate.candidate_id].decision == "core"
    )
    if core_count < 2:
        raise ValueError("screening found fewer than two core papers; revise the search strategy")
    if len(accepted) < MIN_VERIFIED_PAPERS:
        raise ValueError("screening found fewer than five relevant verified papers")

    accepted.sort(
        key=lambda candidate: (
            1 if decisions[candidate.candidate_id].decision == "core" else 0,
            candidate.relevance_score,
            candidate.citation_count,
        ),
        reverse=True,
    )
    selected: set[str] = set()
    quota = max(1, include_count // len(plan.queries))
    for query in plan.queries:
        for candidate in [item for item in accepted if query in item.matched_queries][:quota]:
            if len(selected) < include_count:
                selected.add(candidate.candidate_id)
    for candidate in accepted:
        if len(selected) >= include_count:
            break
        selected.add(candidate.candidate_id)
    if len(selected) < MIN_VERIFIED_PAPERS:
        raise ValueError("screening selection produced fewer than five included sources")

    selected_candidates: list[LiteratureCandidate] = []
    updated_candidates: list[LiteratureCandidate] = []
    for candidate in discovery.candidates:
        decision = decisions.get(candidate.candidate_id)
        if candidate.candidate_id in selected and decision is not None:
            candidate.screening_status = "included"
            candidate.screening_reasons = [
                f"semantic screening: {decision.decision}",
                decision.rationale,
            ]
            selected_candidates.append(candidate)
        else:
            candidate.screening_status = "excluded"
            if decision is not None:
                candidate.screening_reasons = [
                    f"semantic screening: {decision.decision}",
                    decision.rationale,
                    *decision.concerns,
                ]
            else:
                candidate.screening_reasons = ["outside the bounded semantic-screening pool"]
        updated_candidates.append(candidate)
    selected_source_ids = {_source_id(candidate) for candidate in selected_candidates}
    for source_path in sorted((project / "literature" / "sources").glob("*.json")):
        source = LiteratureSource.model_validate(read_json(source_path))
        if source.origin == "discovery" and source.source_id not in selected_source_ids:
            source_path.unlink()
    included_source_ids = [
        _register_candidate_source(project, candidate, discovery.discovery_id)
        for candidate in selected_candidates
    ]
    discovery.candidates = updated_candidates
    discovery.included_source_ids = included_source_ids
    discovery_path = (
        project / "literature" / "discoveries" / discovery.discovery_id / "record.json"
    )
    write_json_atomic(discovery_path, discovery)
    record = LiteratureScreeningRecord(
        screening_id=_stamp("screening"),
        discovery_id=discovery.discovery_id,
        model=model_name(),
        evaluated_candidate_ids=evaluated_ids,
        decisions=output.decisions,
        included_source_ids=included_source_ids,
    )
    write_json_atomic(project / "literature" / "screening.json", record)
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="literature_screening_completed",
            from_stage=state.stage,
            to_stage=state.stage,
            details={
                "screening_id": record.screening_id,
                "evaluated_count": len(evaluated_ids),
                "included_count": len(included_source_ids),
            },
        ),
    )
    return record


def _markdown_cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).replace("|", "\\|").strip()


def _render_review(
    envelope: LiteratureReviewEnvelope,
    sources: dict[str, LiteratureSource],
    screening: LiteratureScreeningRecord,
    discovery: LiteratureDiscoveryRecord,
) -> str:
    synthesis = envelope.synthesis
    lines = [
        "# Literature Review and Novelty Map",
        "",
        f"- Review ID: `{envelope.review_id}`",
        f"- Discovery ID: `{envelope.discovery_id}`",
        f"- Scope: {synthesis.review_scope}",
        "",
        "## Human approval checklist",
        "",
        "Before approving this exact review ID:",
        "",
        "- inspect the included and excluded screening decisions below;",
        "- open the registered locators for the papers central to the chosen novelty candidate;",
        "- treat every proposed gap as a bounded hypothesis rather than proof of novelty;",
        "- record important missing prior work by revising the search plan instead of approving stale evidence;",
        "- confirm that metadata/abstract-only evidence is sufficient only for choosing a first study, not for publication claims.",
        "",
        "## Themes",
        "",
    ]
    for theme in synthesis.themes:
        citations = ", ".join(f"[{source_id}]" for source_id in theme.source_ids)
        lines.extend([f"### {theme.label}", "", f"{theme.summary} {citations}", ""])
    lines.extend(["## Novelty candidates", ""])
    for candidate in synthesis.novelty_candidates:
        marker = " (recommended)" if candidate.novelty_id == synthesis.recommended_novelty_id else ""
        citations = ", ".join(f"[{source_id}]" for source_id in candidate.source_ids)
        lines.extend(
            [
                f"### {candidate.novelty_id}{marker}",
                "",
                f"- Gap: {candidate.gap_statement} {citations}",
                f"- Question: {candidate.proposed_question}",
                f"- Differentiator: {candidate.differentiator}",
                f"- Falsification risk: {candidate.falsification_risk}",
                "",
            ]
        )
    lines.extend(["## Evidence limitations", ""])
    lines.extend(f"- {item}" for item in synthesis.evidence_limitations)
    lines.extend(["", "## Registered sources", ""])
    for source_id in sorted(sources):
        source = sources[source_id]
        lines.extend(
            [
                f"- [{source_id}] {', '.join(source.authors)} ({source.year or 'n.d.'}). "
                f"*{source.title}*. {source.locator}",
                f"  - Verification: {source.verification_method}",
            ]
        )
    candidates = {candidate.candidate_id: candidate for candidate in discovery.candidates}
    included_candidates = [
        candidate
        for candidate in discovery.candidates
        if candidate.screening_status == "included"
    ]
    source_by_candidate = {
        candidate.candidate_id: source_id
        for candidate, source_id in zip(
            included_candidates, discovery.included_source_ids, strict=True
        )
    }
    lines.extend(
        [
            "",
            "## Screening audit trail",
            "",
            "| Decision | Candidate ID | Registered source | Title | Rationale and concerns |",
            "|---|---|---|---|---|",
        ]
    )
    for decision in screening.decisions:
        candidate = candidates[decision.candidate_id]
        explanation = "; ".join(
            [decision.rationale, *decision.concerns]
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(decision.decision),
                    f"`{_markdown_cell(decision.candidate_id)}`",
                    (
                        f"`{_markdown_cell(source_by_candidate[decision.candidate_id])}`"
                        if decision.candidate_id in source_by_candidate
                        else "—"
                    ),
                    _markdown_cell(candidate.title),
                    _markdown_cell(explanation),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _validate_synthesis(synthesis: LiteratureSynthesis, allowed_source_ids: set[str]) -> list[str]:
    errors: list[str] = []
    for theme in synthesis.themes:
        unknown = sorted(set(theme.source_ids) - allowed_source_ids)
        if unknown:
            errors.append(f"theme {theme.theme_id} cites unknown sources: {', '.join(unknown)}")
    for candidate in synthesis.novelty_candidates:
        unknown = sorted(set(candidate.source_ids) - allowed_source_ids)
        if unknown:
            errors.append(f"novelty {candidate.novelty_id} cites unknown sources: {', '.join(unknown)}")
    return errors


async def synthesize_literature(project: Path) -> tuple[LiteratureReviewEnvelope, Stage1Audit]:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("literature synthesis is closed after contracts are frozen")
    discovery = _latest_discovery(project)
    screening = LiteratureScreeningRecord.model_validate(
        read_json(project / "literature" / "screening.json")
    )
    if screening.discovery_id != discovery.discovery_id:
        raise ValueError("literature screening is stale relative to the latest discovery")
    if screening.included_source_ids != discovery.included_source_ids:
        raise ValueError("literature screening and discovery inclusion sets disagree")
    sources = {
        source_id: LiteratureSource.model_validate(
            read_json(project / "literature" / "sources" / f"{source_id}.json")
        )
        for source_id in discovery.included_source_ids
    }
    prompt = json.dumps(
        {
            "review_question": LiteratureSearchPlan.model_validate(
                read_json(
                    project / "literature" / "search_plans" / f"{discovery.search_plan_id}.json"
                )
            ).review_question,
            "verified_sources": {
                source_id: source.model_dump(mode="json") for source_id, source in sources.items()
            },
            "instruction": (
                "Build a cautious related-work map and falsifiable novelty candidates. Every theme and "
                "gap must cite only the exact source IDs supplied. Absence from this bounded search is "
                "not proof that work does not exist."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    from .agent_runtime import generate_literature_synthesis, model_name

    synthesis = await generate_literature_synthesis(prompt, cwd=project)
    errors = _validate_synthesis(synthesis, set(sources))
    if errors:
        raise ValueError("literature synthesis failed source validation: " + "; ".join(errors))
    review_id = _stamp("review")
    envelope = LiteratureReviewEnvelope(
        review_id=review_id,
        discovery_id=discovery.discovery_id,
        model=model_name(),
        synthesis=synthesis,
    )
    review_path = project / "literature" / "review.json"
    write_json_atomic(review_path, envelope)
    (project / "literature" / "review.md").write_text(
        _render_review(envelope, sources, screening, discovery),
        encoding="utf-8",
        newline="\n",
    )
    hashes = {
        f"literature/search_plans/{discovery.search_plan_id}.json": sha256_file(
            project / "literature" / "search_plans" / f"{discovery.search_plan_id}.json"
        ),
        f"literature/discoveries/{discovery.discovery_id}/record.json": sha256_file(
            project / "literature" / "discoveries" / discovery.discovery_id / "record.json"
        ),
        "literature/review.json": sha256_file(review_path),
        "literature/review.md": sha256_file(project / "literature" / "review.md"),
        "literature/screening.json": sha256_file(project / "literature" / "screening.json"),
    }
    hashes.update(discovery.raw_response_hashes)
    hashes.update(
        {
            f"literature/sources/{source_id}.json": sha256_file(
                project / "literature" / "sources" / f"{source_id}.json"
            )
            for source_id in discovery.included_source_ids
        }
    )
    write_json_atomic(
        project / "literature" / "stage1_manifest.json",
        Stage1Manifest(review_id=review_id, hashes=hashes),
    )
    audit = audit_stage1(project, persist=True)
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="literature_synthesis_completed",
            from_stage=state.stage,
            to_stage=state.stage,
            details={"review_id": review_id, "audit_passed": audit.passed},
        ),
    )
    return envelope, audit


def approve_literature(
    project: Path,
    *,
    confirmation: str,
    selected_novelty_id: str,
    note: str = "",
) -> tuple[LiteratureApprovalRecord, Stage1Audit]:
    state = load_state(project)
    if state.stage not in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        raise ValueError("literature approval is closed after contracts are frozen")
    review_path = project / "literature" / "review.json"
    screening_path = project / "literature" / "screening.json"
    review = LiteratureReviewEnvelope.model_validate(read_json(review_path))
    screening = LiteratureScreeningRecord.model_validate(read_json(screening_path))
    discovery = _latest_discovery(project)
    if confirmation != review.review_id:
        raise ValueError("literature approval confirmation must exactly match the review ID")
    novelty_ids = {
        candidate.novelty_id for candidate in review.synthesis.novelty_candidates
    }
    if selected_novelty_id not in novelty_ids:
        raise ValueError(
            "selected novelty ID is not present in the approved literature review"
        )
    if review.discovery_id != discovery.discovery_id or screening.discovery_id != discovery.discovery_id:
        raise ValueError("cannot approve stale review or screening artifacts")
    if screening.included_source_ids != discovery.included_source_ids:
        raise ValueError("screening and discovery inclusion sets disagree")
    approval = LiteratureApprovalRecord(
        review_id=review.review_id,
        discovery_id=discovery.discovery_id,
        screening_id=screening.screening_id,
        review_hash=sha256_file(review_path),
        screening_hash=sha256_file(screening_path),
        selected_novelty_id=selected_novelty_id,
        included_source_ids=discovery.included_source_ids,
        note=note,
    )
    approval_path = project / "literature" / "approval.json"
    write_json_atomic(approval_path, approval)
    manifest_path = project / "literature" / "stage1_manifest.json"
    manifest = Stage1Manifest.model_validate(read_json(manifest_path))
    if manifest.review_id != review.review_id:
        raise ValueError("Stage 1 manifest is not bound to the review being approved")
    manifest.hashes["literature/approval.json"] = sha256_file(approval_path)
    write_json_atomic(manifest_path, manifest)
    audit = audit_stage1(project, persist=True)
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="literature_review_approved",
            from_stage=state.stage,
            to_stage=state.stage,
            details={"review_id": review.review_id, "audit_passed": audit.passed},
        ),
    )
    return approval, audit


def audit_stage1(
    project: Path, *, persist: bool = False, require_plan: bool = True
) -> Stage1Audit:
    sources = [
        LiteratureSource.model_validate(read_json(path))
        for path in sorted((project / "literature" / "sources").glob("*.json"))
    ]
    if (project / "benchmark" / "task.json").is_file():
        checks = {
            "verified_benchmark_source": bool(sources) and all(source.verified for source in sources),
            "protected_evaluator_manifest": (project / "protected_manifest.json").is_file(),
        }
        violations = [f"failed Stage 1 benchmark check: {name}" for name, ok in checks.items() if not ok]
        audit = Stage1Audit(
            passed=all(checks.values()),
            mode="benchmark",
            checks=checks,
            violations=violations,
            verified_paper_count=sum(
                1 for source in sources if source.source_type == LiteratureSourceType.PAPER and source.verified
            ),
            included_count=len(sources),
        )
        if persist:
            write_json_atomic(project / "literature" / "stage1_audit.json", audit)
        return audit

    checks: dict[str, bool] = {}
    violations: list[str] = []
    warnings: list[str] = []
    discovery: LiteratureDiscoveryRecord | None = None
    review: LiteratureReviewEnvelope | None = None
    manifest: Stage1Manifest | None = None
    screening: LiteratureScreeningRecord | None = None
    approval: LiteratureApprovalRecord | None = None
    latest_plan_id: str | None = None
    try:
        latest_plan = read_json(project / "literature" / "latest_search_plan.json")
        latest_plan_id = str(latest_plan["search_plan_id"])
        LiteratureSearchPlan.model_validate(
            read_json(project / "literature" / "search_plans" / f"{latest_plan_id}.json")
        )
        checks["search_strategy_present"] = True
    except Exception as exc:
        checks["search_strategy_present"] = False
        violations.append(f"search strategy is missing or invalid: {exc}")
    try:
        discovery = _latest_discovery(project)
        checks["discovery_record_valid"] = True
    except Exception as exc:
        checks["discovery_record_valid"] = False
        violations.append(f"discovery record is missing or invalid: {exc}")
    try:
        review = LiteratureReviewEnvelope.model_validate(read_json(project / "literature" / "review.json"))
        checks["review_schema_valid"] = True
    except Exception as exc:
        checks["review_schema_valid"] = False
        violations.append(f"literature review is missing or invalid: {exc}")
    try:
        screening = LiteratureScreeningRecord.model_validate(
            read_json(project / "literature" / "screening.json")
        )
        checks["semantic_screening_valid"] = True
    except Exception as exc:
        checks["semantic_screening_valid"] = False
        violations.append(f"semantic screening is missing or invalid: {exc}")
    try:
        approval = LiteratureApprovalRecord.model_validate(
            read_json(project / "literature" / "approval.json")
        )
        checks["human_approval_present"] = True
    except Exception as exc:
        checks["human_approval_present"] = False
        violations.append(f"literature approval is missing or invalid: {exc}")
    try:
        manifest = Stage1Manifest.model_validate(
            read_json(project / "literature" / "stage1_manifest.json")
        )
        bad_hashes = [
            relative
            for relative, expected in manifest.hashes.items()
            if not safe_relative(project, relative.replace("\\", "/")).is_file()
            or sha256_file(safe_relative(project, relative.replace("\\", "/"))) != expected
        ]
        checks["stage1_artifacts_hash_valid"] = not bad_hashes
        if bad_hashes:
            violations.append("Stage 1 artifacts changed or disappeared: " + ", ".join(bad_hashes))
    except Exception as exc:
        checks["stage1_artifacts_hash_valid"] = False
        violations.append(f"Stage 1 manifest is missing or invalid: {exc}")

    verified_papers = [
        source
        for source in sources
        if source.source_type == LiteratureSourceType.PAPER and source.verified
    ]
    included_ids = set(discovery.included_source_ids if discovery else [])
    included_verified_papers = [source for source in verified_papers if source.source_id in included_ids]
    checks["minimum_verified_papers"] = len(included_verified_papers) >= MIN_VERIFIED_PAPERS
    checks["included_sources_exist"] = bool(included_ids) and included_ids.issubset(
        {source.source_id for source in verified_papers}
    )
    checks["discovery_bound_to_latest_search_plan"] = bool(
        discovery and latest_plan_id and discovery.search_plan_id == latest_plan_id
    )
    checks["screening_bound_to_discovery"] = bool(
        screening
        and discovery
        and screening.discovery_id == discovery.discovery_id
        and screening.included_source_ids == discovery.included_source_ids
    )
    screening_consistent = False
    if screening and discovery:
        sources_by_id = {source.source_id: source for source in sources}
        candidates_by_id = {
            candidate.candidate_id: candidate for candidate in discovery.candidates
        }
        decisions_by_id = {
            decision.candidate_id: decision for decision in screening.decisions
        }
        included_candidates = [
            candidate
            for candidate in discovery.candidates
            if candidate.screening_status == "included"
        ]
        included_candidate_ids = {candidate.candidate_id for candidate in included_candidates}
        accepted_candidate_ids = {
            decision.candidate_id
            for decision in screening.decisions
            if decision.decision in {"core", "supporting"}
        }
        source_candidate_alignment = bool(
            len(included_candidates) == len(discovery.included_source_ids)
            and all(
                source_id in sources_by_id
                and _titles_match(candidate.title, sources_by_id[source_id].title)
                for candidate, source_id in zip(
                    included_candidates, discovery.included_source_ids, strict=True
                )
            )
        )
        screening_consistent = bool(
            set(screening.evaluated_candidate_ids).issubset(candidates_by_id)
            and set(screening.evaluated_candidate_ids) == set(decisions_by_id)
            and sum(1 for decision in screening.decisions if decision.decision == "core") >= 2
            and len(accepted_candidate_ids) >= MIN_VERIFIED_PAPERS
            and included_candidate_ids.issubset(accepted_candidate_ids)
            and source_candidate_alignment
            and screening.included_source_ids == discovery.included_source_ids
        )
    checks["screening_decisions_consistent"] = screening_consistent
    checks["approval_bound_to_artifacts"] = bool(
        approval
        and review
        and screening
        and discovery
        and approval.review_id == review.review_id
        and approval.discovery_id == discovery.discovery_id
        and approval.screening_id == screening.screening_id
        and approval.review_hash == sha256_file(project / "literature" / "review.json")
        and approval.screening_hash == sha256_file(project / "literature" / "screening.json")
        and approval.selected_novelty_id
        in {candidate.novelty_id for candidate in review.synthesis.novelty_candidates}
        and approval.included_source_ids == discovery.included_source_ids
    )
    candidate_count = len(discovery.candidates) if discovery else 0
    checks["minimum_candidate_pool"] = candidate_count >= MIN_CANDIDATES
    provider_count = 0
    if discovery:
        for status in discovery.provider_status.values():
            match = re.match(r"(?:ok|degraded):(\d+)/(\d+)", status)
            if match and int(match.group(1)) > 0:
                provider_count += 1
            if match and int(match.group(1)) < int(match.group(2)):
                warnings.append("one literature provider completed only part of the query plan")
    checks["provider_diversity"] = provider_count >= 2
    if review:
        cited_ids = {
            source_id
            for theme in review.synthesis.themes
            for source_id in theme.source_ids
        } | {
            source_id
            for candidate in review.synthesis.novelty_candidates
            for source_id in candidate.source_ids
        }
        checks["review_citations_resolve"] = bool(cited_ids) and cited_ids.issubset(included_ids)
        checks["review_bound_to_latest_discovery"] = bool(discovery) and review.discovery_id == discovery.discovery_id
        checks["manifest_bound_to_review"] = bool(manifest) and manifest.review_id == review.review_id
    else:
        checks["review_citations_resolve"] = False
        checks["review_bound_to_latest_discovery"] = False
        checks["manifest_bound_to_review"] = False
    if require_plan:
        plan_check_names = (
            "research_plan_present",
            "research_plan_ready",
            "research_plan_has_no_blocking_questions",
            "plan_evidence_binding_valid",
            "plan_binding_copies_match",
            "plan_artifacts_in_manifest",
        )
        try:
            state = load_state(project)
            if not state.latest_plan_draft:
                raise FileNotFoundError("no research plan draft is registered in project state")
            plan_id = state.latest_plan_draft
            plan_path = project / "plans" / f"{plan_id}.json"
            plan = ResearchPlanDraft.model_validate(read_json(plan_path))
            checks["research_plan_present"] = True
            checks["research_plan_ready"] = plan.ready_to_freeze
            checks["research_plan_has_no_blocking_questions"] = not plan.clarifying_questions

            root_binding_path = project / "plan_evidence_binding.json"
            per_plan_binding_path = project / "plans" / f"{plan_id}-evidence.json"
            binding = PlanEvidenceBinding.model_validate(read_json(root_binding_path))
            per_plan_binding = PlanEvidenceBinding.model_validate(
                read_json(per_plan_binding_path)
            )
            plan_hash = sha256_file(plan_path)
            review_hash = sha256_file(project / "literature" / "review.json")
            checks["plan_evidence_binding_valid"] = bool(
                review
                and binding.plan_id == plan_id
                and binding.plan_hash == plan_hash
                and binding.review_id == review.review_id
                and binding.review_hash == review_hash
                and approval
                and binding.selected_novelty_id == approval.selected_novelty_id
                and binding.source_ids == sorted(included_ids)
            )
            checks["plan_binding_copies_match"] = binding == per_plan_binding
            required_manifest_hashes = {
                f"plans/{plan_id}.json": plan_hash,
                f"plans/{plan_id}-evidence.json": sha256_file(per_plan_binding_path),
                "plan_evidence_binding.json": sha256_file(root_binding_path),
            }
            checks["plan_artifacts_in_manifest"] = bool(
                manifest
                and all(
                    manifest.hashes.get(relative) == expected
                    for relative, expected in required_manifest_hashes.items()
                )
            )
        except Exception as exc:
            for name in plan_check_names:
                checks.setdefault(name, False)
            violations.append(f"research plan evidence binding is missing or invalid: {exc}")
    for name, ok in checks.items():
        if not ok and not any(name in item for item in violations):
            violations.append(f"failed Stage 1 check: {name}")
    audit = Stage1Audit(
        passed=all(checks.values()),
        mode="scientific",
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        warnings=warnings,
        verified_paper_count=len(included_verified_papers),
        provider_count=provider_count,
        candidate_count=candidate_count,
        included_count=len(included_ids),
        review_id=review.review_id if review else None,
    )
    if persist:
        write_json_atomic(project / "literature" / "stage1_audit.json", audit)
    return audit


def review_context(project: Path) -> dict[str, Any]:
    review = LiteratureReviewEnvelope.model_validate(read_json(project / "literature" / "review.json"))
    return review.model_dump(mode="json")


def included_source_ids(project: Path) -> list[str]:
    return list(_latest_discovery(project).included_source_ids)
