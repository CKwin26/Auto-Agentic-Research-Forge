from __future__ import annotations

from ...claim_discovery import fetch_redfox_trends
from ..domain.models import ProviderErrorClass
from .base import ProviderAdapter, ProviderFailure, ProviderSearchResult


class RedFoxAdapter(ProviderAdapter):
    provider_id = "redfox_wechat"
    domains = {"redfox.hk", "mp.weixin.qq.com"}
    http_method = "POST"

    def search(self, plan):
        key = self.credentials.get("REDFOX_API_KEY")
        if not key:
            raise ProviderFailure(
                "RedFox credential is not configured",
                ProviderErrorClass.AUTHENTICATION_ERROR,
            )
        raw = []

        def capture(request):
            payload = self.transport(request)
            raw.append(payload)
            return payload

        signals = fetch_redfox_trends(
            plan.sanitized_queries,
            api_key=key,
            http_json=capture,
        )
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw,
            signals=signals,
            domains=sorted(self.domains),
            http_method=self.http_method,
        )
