"""Bounded adapter for the openags Paper Search MCP server.

Only the metadata search tool is callable.  Google Scholar and every download
tool (including Sci-Hub fallbacks exposed by the upstream server) are excluded
by construction.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable
from typing import Any

from ...claim_discovery import TrendSignal
from ..domain.models import ProviderErrorClass, QueryPlan, ResourceType, retrieval_id
from .base import (
    ProviderAdapter,
    ProviderContentResult,
    ProviderFailure,
    ProviderSearchResult,
)


PAPER_SEARCH_MCP_VERSION = "0.1.4"
PAPER_SEARCH_MCP_COMMIT = "c8b642183bb725f0a7faec89e58b558df09079d1"
SAFE_SOURCES = (
    "arxiv",
    "pubmed",
    "biorxiv",
    "medrxiv",
    "iacr",
    "semantic",
    "crossref",
    "openalex",
    "pmc",
    "core",
    "europepmc",
    "dblp",
    "openaire",
    "citeseerx",
    "doaj",
    "base",
    "zenodo",
    "hal",
    "ssrn",
    "unpaywall",
)
DEFAULT_PUBLIC_SOURCES = (
    "arxiv",
    "pubmed",
    "crossref",
    "openalex",
    "pmc",
    "europepmc",
    "dblp",
    "doaj",
)
PROHIBITED_TOOLS = {
    "search_google_scholar",
    "download_scihub",
    "download_with_fallback",
}

MCPRunner = Callable[[str, dict[str, Any]], dict[str, Any]]
ApprovedFullTextAcquirer = Callable[[str, int], ProviderContentResult]


def _unwrap_result(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("result")
    return nested if isinstance(nested, dict) else payload


class PaperSearchMCPAdapter(ProviderAdapter):
    provider_id = "paper_search_mcp"
    domains = {
        "export.arxiv.org",
        "eutils.ncbi.nlm.nih.gov",
        "api.crossref.org",
        "api.openalex.org",
        "www.ncbi.nlm.nih.gov",
        "www.ebi.ac.uk",
        "dblp.org",
        "doaj.org",
    }
    http_method = "MCP"

    def __init__(
        self,
        *,
        runner: MCPRunner | None = None,
        call_timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__()
        if call_timeout_seconds <= 0:
            raise ValueError("Paper Search MCP call timeout must be positive")
        self._runner = runner
        self._call_timeout_seconds = call_timeout_seconds
        self._started = False

    def health_check(self) -> dict[str, str]:
        installed = importlib.util.find_spec("paper_search_mcp") is not None
        if self._started:
            status = "ready"
            reason = "bounded MCP lifecycle started and search_papers is available"
        elif installed or self._runner is not None:
            status = "degraded"
            reason = (
                "bounded runner configured; lifecycle is stopped"
                if self._runner is not None
                else "package installed; live MCP handshake has not been verified"
            )
        else:
            status = "unavailable"
            reason = "paper-search-mcp is not installed"
        return {
            "provider": self.provider_id,
            "status": status,
            "version": PAPER_SEARCH_MCP_VERSION,
            "commit": PAPER_SEARCH_MCP_COMMIT,
            "reason": reason,
        }

    def start(self, *, timeout_seconds: float = 20.0) -> dict[str, str]:
        """Probe a bounded MCP lifecycle without retaining an orphan process."""
        if timeout_seconds <= 0:
            raise ValueError("Paper Search MCP timeout must be positive")
        if self._runner is not None:
            # Injected runners are fixture/local-companion lifecycles. Their
            # callable boundary is the health proof used by tests.
            self._started = True
            return self.health_check()
        try:
            asyncio.run(
                asyncio.wait_for(
                    self._probe_stdio(),
                    timeout=timeout_seconds,
                )
            )
        except TimeoutError as exc:
            raise ProviderFailure(
                "Paper Search MCP startup timed out",
                ProviderErrorClass.TRANSIENT_NETWORK_ERROR,
            ) from exc
        self._started = True
        return self.health_check()

    def stop(self) -> None:
        """Mark the bounded lifecycle stopped.

        Each real call owns a stdio context manager, so leaving that context has
        already terminated the subprocess and closed its streams.
        """
        self._started = False

    async def _probe_stdio(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "paper_search_mcp.server"],
        )
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        if "search_papers" not in names:
            raise ProviderFailure(
                "Paper Search MCP does not expose search_papers",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool != "search_papers" or tool in PROHIBITED_TOOLS:
            raise ProviderFailure(
                "Paper Search MCP tool is outside the safe metadata allowlist",
                ProviderErrorClass.POLICY_DENIED,
            )
        if self._runner is not None:
            return _unwrap_result(self._runner(tool, arguments))
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._call_stdio(tool, arguments),
                    timeout=self._call_timeout_seconds,
                )
            )
        except TimeoutError as exc:
            raise ProviderFailure(
                "Paper Search MCP call timed out",
                ProviderErrorClass.TRANSIENT_NETWORK_ERROR,
            ) from exc

    async def _call_stdio(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "paper_search_mcp.server"],
        )
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
        if result.isError:
            raise ProviderFailure(
                "Paper Search MCP returned a tool error",
                ProviderErrorClass.PERMANENT_PROVIDER_ERROR,
            )
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            return _unwrap_result(structured)
        for item in result.content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return _unwrap_result(parsed)
        raise ProviderFailure(
            "Paper Search MCP returned malformed search output",
            ProviderErrorClass.MALFORMED_RESPONSE,
        )

    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        if not {
            ResourceType.PUBLICATION,
            ResourceType.PREPRINT,
        }.intersection(plan.resource_types):
            raise ProviderFailure(
                "Paper Search MCP only searches publications and preprints",
                ProviderErrorClass.POLICY_DENIED,
            )
        raw_payloads: list[dict[str, Any]] = []
        signals: list[TrendSignal] = []
        per_source = max(1, min(plan.budget.max_results or 5, 20))
        for query in plan.sanitized_queries[: plan.budget.max_queries or 1]:
            payload = self._call(
                "search_papers",
                {
                    "query": query,
                    "max_results_per_source": per_source,
                    "sources": ",".join(DEFAULT_PUBLIC_SOURCES),
                },
            )
            used = {str(item) for item in payload.get("sources_used") or []}
            if "google_scholar" in used:
                raise ProviderFailure(
                    "Paper Search MCP executed a prohibited source",
                    ProviderErrorClass.POLICY_DENIED,
                )
            raw_payloads.append(payload)
            for row in payload.get("papers") or []:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title") or "").strip()
                pdf_url = str(row.get("pdf_url") or "").strip()
                if pdf_url.startswith("http://"):
                    pdf_url = "https://" + pdf_url.removeprefix("http://")
                entry_url = str(row.get("url") or row.get("entry_url") or "").strip()
                url = str(pdf_url or entry_url or "").strip()
                if url.startswith("http://"):
                    url = "https://" + url.removeprefix("http://")
                if not title:
                    continue
                source = str(row.get("source") or "")
                paper_id = str(row.get("paper_id") or row.get("doi") or url or title)
                signals.append(
                    TrendSignal(
                        signal_id=retrieval_id("paper-search-signal", source, paper_id),
                        provider=self.provider_id,
                        signal_class="scholarly_attention",
                        query=query,
                        title=title,
                        summary=str(row.get("abstract") or row.get("summary") or "")[
                            :1200
                        ],
                        url=url or f"urn:paper-search:{paper_id}",
                        published_at=str(
                            row.get("published")
                            or row.get("published_date")
                            or row.get("year")
                            or ""
                        ),
                        source_name=source,
                        terms=(
                            [
                                item.strip()
                                for item in str(row.get("categories") or "").split(";")
                                if item.strip()
                            ]
                            if isinstance(row.get("categories"), str)
                            else [str(item) for item in row.get("categories") or []]
                        ),
                        trend_score=0.0,
                        scientific_density=0.0,
                        metadata={
                            "resource_type": (
                                ResourceType.PREPRINT.value
                                if source in {"arxiv", "biorxiv", "medrxiv", "ssrn"}
                                else ResourceType.PUBLICATION.value
                            ),
                            "doi": row.get("doi"),
                            "paper_id": paper_id,
                            "upstream_source": source,
                            "authors": row.get("authors") or [],
                            "license": row.get("license"),
                            "entry_url": entry_url or None,
                            "full_text_url": pdf_url or None,
                            "access_status": (
                                "open_access" if pdf_url else "metadata_only"
                            ),
                            "model_processing_allowed": False,
                            "upstream_version": PAPER_SEARCH_MCP_VERSION,
                            "upstream_commit": PAPER_SEARCH_MCP_COMMIT,
                        },
                    )
                )
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw_payloads,
            signals=signals[: plan.budget.max_results or None],
            domains=sorted(self.domains),
            http_method=self.http_method,
        )

    def search_publications(self, plan: QueryPlan) -> ProviderSearchResult:
        return self.search(plan)

    def resolve_metadata(
        self,
        plan: QueryPlan,
        identifier: str,
    ) -> ProviderSearchResult:
        identifier = identifier.strip()
        if not identifier:
            raise ValueError("metadata resolution requires an identifier")
        resolution_plan = plan.model_copy(
            update={
                "research_need": f"resolve publication identifier {identifier}",
                "purpose": "metadata_resolution",
                "queries": [identifier],
                "sanitized_queries": [identifier],
            }
        )
        return self.search_publications(resolution_plan)

    def find_open_access_copy(
        self,
        plan: QueryPlan,
        identifier: str,
    ) -> ProviderSearchResult:
        result = self.resolve_metadata(plan, identifier)
        result.signals = [
            signal
            for signal in result.signals
            if signal.metadata.get("full_text_url")
            and signal.metadata.get("access_status") == "open_access"
        ]
        return result

    def fetch_open_full_text(
        self,
        plan: QueryPlan,
        identifier: str,
        *,
        max_bytes: int,
        approved_acquirer: ApprovedFullTextAcquirer,
    ) -> ProviderContentResult:
        """Acquire a discovered OA copy through the Gateway-owned fetch path.

        The upstream MCP exposes unsafe fallback download tools, so this
        adapter never downloads bytes itself. The caller must supply the
        already policy-approved Open Access acquirer, which performs rights,
        byte-budget, redirect and SSRF checks before returning content.
        """
        if max_bytes <= 0:
            raise ProviderFailure(
                "open full-text acquisition requires a positive byte budget",
                ProviderErrorClass.POLICY_DENIED,
            )
        result = self.find_open_access_copy(plan, identifier)
        urls = [
            str(signal.metadata.get("full_text_url") or "").strip()
            for signal in result.signals
            if str(signal.metadata.get("full_text_url") or "").strip()
        ]
        if not urls:
            raise ProviderFailure(
                "no open-access full-text copy was discovered",
                ProviderErrorClass.RESOURCE_NOT_FOUND,
            )
        content = approved_acquirer(urls[0], max_bytes)
        if content.source_url != urls[0]:
            raise ProviderFailure(
                "approved acquirer returned content for a different source URL",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        return content

    @staticmethod
    def get_underlying_provider_coverage(
        result: ProviderSearchResult,
    ) -> dict[str, list[str]]:
        used: set[str] = set()
        failed: set[str] = set()
        warnings: set[str] = set()
        for payload in result.raw_payloads:
            used.update(str(item) for item in payload.get("sources_used") or [])
            failed.update(str(item) for item in payload.get("sources_failed") or [])
            warnings.update(str(item) for item in payload.get("warnings") or [])
        return {
            "used": sorted(used),
            "failed": sorted(failed),
            "warnings": sorted(warnings),
        }

    @staticmethod
    def classify_error(error: BaseException) -> ProviderErrorClass:
        if isinstance(error, ProviderFailure):
            return error.classification
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
            return ProviderErrorClass.TRANSIENT_NETWORK_ERROR
        if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
            return ProviderErrorClass.MALFORMED_RESPONSE
        return ProviderErrorClass.PERMANENT_PROVIDER_ERROR
