from __future__ import annotations

from ...claim_discovery import fetch_crossref_trends, fetch_scholarly_trends
from .base import ProviderAdapter, ProviderSearchResult


class SemanticScholarAdapter(ProviderAdapter):
    provider_id = "semantic_scholar"
    domains = {"api.semanticscholar.org"}

    def search(self, plan):
        raw = []

        def capture(request):
            payload = self.transport(request)
            raw.append(payload)
            return payload

        signals = fetch_scholarly_trends(
            plan.sanitized_queries,
            api_key=self.credentials.get("S2_API_KEY"),
            http_json=capture,
        )
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw,
            signals=signals,
            domains=sorted(self.domains),
        )


class CrossrefAdapter(ProviderAdapter):
    provider_id = "crossref"
    domains = {"api.crossref.org"}

    def search(self, plan):
        raw = []

        def capture(request):
            payload = self.transport(request)
            raw.append(payload)
            return payload

        signals = fetch_crossref_trends(
            plan.sanitized_queries,
            http_json=capture,
        )
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw,
            signals=signals,
            domains=sorted(self.domains),
        )
