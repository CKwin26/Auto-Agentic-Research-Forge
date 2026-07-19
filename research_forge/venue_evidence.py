from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from .models import StrictModel
from .storage import read_json, write_json_atomic


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_QUERY_LIMIT = 2_000
OPENALEX_RESULT_LIMIT = 50


class SimilarPaperEvidence(StrictModel):
    provider: Literal["openalex", "openreview_export", "local_fixture"]
    provider_id: str
    title: str
    year: int | None = None
    venue_name: str
    venue_type: Literal["journal", "conference", "unknown"] = "unknown"
    doi: str | None = None
    url: str
    relevance_score: float = Field(ge=0, le=1)


class VenueEvidenceBundle(StrictModel):
    schema_version: int = 1
    generated_at: str
    manuscript_sha256: str
    query_sha256: str
    query_characters: int = Field(ge=0)
    sent_fields: list[str]
    provider_status: dict[str, str]
    raw_response_sha256: str | None = None
    cache_hit: bool = False
    papers: list[SimilarPaperEvidence]
    warnings: list[str]


class OpenAlexSearchClient(Protocol):
    def search_similar(
        self,
        query: str,
        *,
        from_year: int,
        max_results: int,
    ) -> tuple[list[SimilarPaperEvidence], str]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _tokens(value: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "also",
        "among",
        "and",
        "are",
        "been",
        "for",
        "from",
        "have",
        "into",
        "its",
        "our",
        "that",
        "the",
        "their",
        "these",
        "this",
        "through",
        "using",
        "was",
        "were",
        "with",
    }
    return [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", value.casefold())
        if token not in stopwords
    ]


def _cosine_similarity(left: str, right: str) -> float:
    a = Counter(_tokens(left))
    b = Counter(_tokens(right))
    if not a or not b:
        return 0.0
    numerator = sum(value * b.get(token, 0) for token, value in a.items())
    denominator = math.sqrt(sum(value * value for value in a.values())) * math.sqrt(
        sum(value * value for value in b.values())
    )
    return min(max(numerator / denominator if denominator else 0.0, 0.0), 1.0)


def _content_value(value: object) -> object:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _venue_type(source_type: object, work_type: object) -> Literal["journal", "conference", "unknown"]:
    source = str(source_type or "").casefold()
    work = str(work_type or "").casefold()
    if source == "journal" or "journal" in work:
        return "journal"
    if source == "conference" or "proceedings" in work or "conference" in work:
        return "conference"
    return "unknown"


class OpenAlexClient:
    """Minimal OpenAlex adapter that never persists or reports the API key."""

    def __init__(self, *, api_key: str | None = None, timeout_seconds: int = 30) -> None:
        self.api_key = api_key or os.getenv("OPENALEX_API_KEY") or None
        self.timeout_seconds = timeout_seconds

    def search_similar(
        self,
        query: str,
        *,
        from_year: int,
        max_results: int,
    ) -> tuple[list[SimilarPaperEvidence], str]:
        count = min(max(max_results, 1), OPENALEX_RESULT_LIMIT)
        params: dict[str, object] = {
            "search.semantic": query[:OPENALEX_QUERY_LIMIT],
            "filter": f"publication_year:>{from_year - 1}",
            "per-page": count,
            "select": (
                "id,doi,title,display_name,publication_year,relevance_score,"
                "primary_location,type"
            ),
        }
        if self.api_key:
            params["api_key"] = self.api_key
        url = f"{OPENALEX_WORKS_URL}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "ResearchForge/0.2 (+auditable venue matching)",
            },
            method="GET",
        )
        raw = b""
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                break
            except urllib.error.HTTPError as exc:
                exc.read(1_000)
                if exc.code in {429, 500, 502, 503, 504} and attempt == 0:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        delay = min(max(float(retry_after or 1.0), 0.5), 3.0)
                    except ValueError:
                        delay = 1.0
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"openalex HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise RuntimeError(f"openalex request failed: {exc.reason}") from exc
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("openalex returned an invalid works response")
        papers: list[SimilarPaperEvidence] = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            location = item.get("primary_location")
            location = location if isinstance(location, dict) else {}
            source = location.get("source")
            source = source if isinstance(source, dict) else {}
            title = str(item.get("title") or item.get("display_name") or "").strip()
            venue_name = str(source.get("display_name") or "").strip()
            provider_id = str(item.get("id") or "").strip()
            if not title or not venue_name or not provider_id:
                continue
            relevance = item.get("relevance_score")
            try:
                score = float(relevance)
            except (TypeError, ValueError):
                score = 0.0
            doi = str(item.get("doi") or "").strip() or None
            url_value = str(location.get("landing_page_url") or doi or provider_id).strip()
            year_value = item.get("publication_year")
            try:
                year = int(year_value) if year_value is not None else None
            except (TypeError, ValueError):
                year = None
            papers.append(
                SimilarPaperEvidence(
                    provider="openalex",
                    provider_id=provider_id,
                    title=title,
                    year=year,
                    venue_name=venue_name,
                    venue_type=_venue_type(source.get("type"), item.get("type")),
                    doi=doi,
                    url=url_value,
                    relevance_score=min(max(score, 0.0), 1.0),
                )
            )
        return papers, hashlib.sha256(raw).hexdigest()


def load_openreview_export(path: str | Path, query: str) -> list[SimilarPaperEvidence]:
    """Read a public accepted-paper export produced with openreview-py.

    The export may be a JSON list or an object containing ``notes``, ``results``,
    or ``submissions``. Similarity is computed locally; manuscript text is not sent
    to OpenReview.
    """

    source_path = Path(path).resolve()
    payload: object
    if source_path.suffix.casefold() == ".jsonl":
        payload = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        records = next(
            (
                payload[key]
                for key in ("notes", "results", "submissions")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError("OpenReview export must be a JSON list or object containing notes/results/submissions")
    papers: list[SimilarPaperEvidence] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        content = record.get("content")
        content = content if isinstance(content, dict) else record
        title = str(_content_value(content.get("title")) or "").strip()
        abstract = str(_content_value(content.get("abstract")) or "").strip()
        venue = str(
            _content_value(content.get("venueid"))
            or _content_value(content.get("venue"))
            or record.get("venueid")
            or ""
        ).strip()
        if not title or not venue:
            continue
        score = _cosine_similarity(query, f"{title} {abstract}")
        if score <= 0:
            continue
        provider_id = str(record.get("id") or record.get("forum") or f"row-{index}")
        url = str(record.get("url") or f"https://openreview.net/forum?id={provider_id}")
        year_match = re.search(r"(?:19|20)\d{2}", venue)
        papers.append(
            SimilarPaperEvidence(
                provider="openreview_export",
                provider_id=provider_id,
                title=title,
                year=int(year_match.group()) if year_match else None,
                venue_name=venue,
                venue_type="conference",
                url=url,
                relevance_score=round(score, 6),
            )
        )
    return sorted(papers, key=lambda item: item.relevance_score, reverse=True)


def load_local_evidence(path: str | Path) -> list[SimilarPaperEvidence]:
    payload = json.loads(Path(path).resolve().read_text(encoding="utf-8"))
    records = payload.get("papers") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("local venue evidence must contain a papers list")
    normalized: list[SimilarPaperEvidence] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        value = dict(record)
        value.setdefault("provider", "local_fixture")
        normalized.append(SimilarPaperEvidence.model_validate(value))
    return normalized


def collect_venue_evidence(
    *,
    manuscript_sha256: str,
    title: str,
    abstract: str,
    cache_path: str | Path | None,
    live_openalex: bool,
    from_year: int,
    max_results: int,
    openreview_export: str | Path | None = None,
    local_evidence: str | Path | None = None,
    client: OpenAlexSearchClient | None = None,
) -> VenueEvidenceBundle:
    query = re.sub(r"\s+", " ", f"{title}. {abstract}".strip()).strip()[:OPENALEX_QUERY_LIMIT]
    query_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()
    resolved_cache = Path(cache_path).resolve() if cache_path else None
    provider_status: dict[str, str] = {}
    warnings: list[str] = []
    papers: list[SimilarPaperEvidence] = []
    raw_hash: str | None = None
    cache_hit = False

    if resolved_cache and resolved_cache.is_file() and not live_openalex:
        cached = VenueEvidenceBundle.model_validate(read_json(resolved_cache))
        if cached.manuscript_sha256 == manuscript_sha256 and cached.query_sha256 == query_sha:
            papers.extend(cached.papers)
            provider_status.update(cached.provider_status)
            raw_hash = cached.raw_response_sha256
            cache_hit = True
        else:
            warnings.append("Cached venue evidence does not match the current manuscript and was ignored.")

    if live_openalex:
        if len(query) < 40:
            warnings.append("The title/abstract query is too short for meaningful OpenAlex semantic search.")
            provider_status["openalex"] = "skipped_query_too_short"
        else:
            try:
                live_papers, raw_hash = (client or OpenAlexClient()).search_similar(
                    query,
                    from_year=from_year,
                    max_results=max_results,
                )
                papers.extend(live_papers)
                provider_status["openalex"] = f"live_ok_n={len(live_papers)}"
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                provider_status["openalex"] = "unavailable"
                warnings.append(f"OpenAlex evidence unavailable: {exc}")
    elif not cache_hit:
        provider_status["openalex"] = "not_requested"
        warnings.append(
            "No matching OpenAlex cache was available. Use --live-evidence to consent to sending only the title and abstract."
        )

    if openreview_export:
        exported = load_openreview_export(openreview_export, query)
        papers.extend(exported)
        provider_status["openreview_export"] = f"local_ok_n={len(exported)}"
    if local_evidence:
        local = load_local_evidence(local_evidence)
        papers.extend(local)
        provider_status["local_fixture"] = f"local_ok_n={len(local)}"

    deduplicated: dict[str, SimilarPaperEvidence] = {}
    for paper in papers:
        doi_key = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", paper.doi or "", flags=re.I)
        key = doi_key.casefold() if doi_key else _normalized(paper.title)
        if not key:
            key = f"{paper.provider}:{paper.provider_id}"
        previous = deduplicated.get(key)
        if previous is None or paper.relevance_score > previous.relevance_score:
            deduplicated[key] = paper
    result = VenueEvidenceBundle(
        generated_at=_utc_now(),
        manuscript_sha256=manuscript_sha256,
        query_sha256=query_sha,
        query_characters=len(query),
        sent_fields=["title", "abstract"] if live_openalex else [],
        provider_status=provider_status,
        raw_response_sha256=raw_hash,
        cache_hit=cache_hit,
        papers=sorted(deduplicated.values(), key=lambda item: item.relevance_score, reverse=True),
        warnings=warnings,
    )
    if resolved_cache and live_openalex and provider_status.get("openalex", "").startswith("live_ok"):
        write_json_atomic(resolved_cache, result)
    return result


def normalized_venue_name(value: str) -> str:
    value = re.sub(r"\b(?:19|20)\d{2}\b", " ", value)
    value = re.sub(r"\b(?:conference|proceedings|transactions|journal|international|annual)\b", " ", value, flags=re.I)
    return _normalized(value)
