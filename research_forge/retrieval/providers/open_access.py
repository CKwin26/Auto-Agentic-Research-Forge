"""Open-access resolver using official repository metadata only."""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable
from functools import partial
from typing import Any

from ...claim_discovery import TrendSignal
from ..domain.models import ProviderErrorClass, QueryPlan, ResourceType, retrieval_id
from .base import (
    ProviderAdapter,
    ProviderContentResult,
    ProviderFailure,
    ProviderSearchResult,
    binary_transport,
)


_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_LOCAL_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".onion",
    ".test",
    ".invalid",
    ".example",
)


class OpenAccessAdapter(ProviderAdapter):
    provider_id = "open_access"
    domains = {"api.unpaywall.org"}
    http_method = "GET"

    def __init__(
        self,
        *args,
        content_transport: Callable[..., tuple[bytes, str, str, dict[str, str]]]
        | None = None,
        url_validator: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.content_transport = content_transport or binary_transport
        self.url_validator = url_validator or _validate_public_https_url

    def health_check(self) -> dict[str, str]:
        email = self.credentials.get("UNPAYWALL_EMAIL")
        return {
            "provider": self.provider_id,
            "status": "degraded" if email else "unavailable",
            "credential_mode": "server_managed",
            "reason": (
                "contact email configured; live acquisition proof is evaluated "
                "from Gateway artifacts"
                if email
                else "UNPAYWALL_EMAIL is required by the official API"
            ),
        }

    def _resolve(self, doi: str) -> dict[str, Any]:
        email = self.credentials.get("UNPAYWALL_EMAIL")
        if not email:
            raise ProviderFailure(
                "Open-access resolver is not configured",
                ProviderErrorClass.AUTHENTICATION_ERROR,
            )
        url = (
            "https://api.unpaywall.org/v2/"
            + urllib.parse.quote(doi, safe="/")
            + "?"
            + urllib.parse.urlencode({"email": email})
        )
        payload = self.transport(
            urllib.request.Request(
                url,
                headers={"User-Agent": "ResearchForge/0.1"},
            )
        )
        if not isinstance(payload, dict):
            raise ProviderFailure(
                "open-access resolver returned malformed metadata",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        return payload

    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        if not {
            ResourceType.PUBLICATION,
            ResourceType.PREPRINT,
        }.intersection(plan.resource_types):
            raise ProviderFailure(
                "open-access resolution requires a publication identifier",
                ProviderErrorClass.POLICY_DENIED,
            )
        raw_payloads: list[dict[str, Any]] = []
        signals: list[TrendSignal] = []
        for query in plan.sanitized_queries[: plan.budget.max_queries or 1]:
            match = _DOI.search(query)
            if not match:
                continue
            doi = match.group(0).rstrip(".,;)").lower()
            payload = self._resolve(doi)
            raw_payloads.append(payload)
            location = payload.get("best_oa_location") or {}
            if not isinstance(location, dict):
                location = {}
            url = str(
                location.get("url_for_pdf")
                or location.get("url_for_landing_page")
                or payload.get("doi_url")
                or ""
            )
            signals.append(
                TrendSignal(
                    signal_id=retrieval_id("open-access-signal", doi, url),
                    provider=self.provider_id,
                    signal_class="official_source",
                    query=query,
                    title=str(payload.get("title") or doi),
                    summary="Open-access resolution metadata.",
                    url=url or f"https://doi.org/{doi}",
                    published_at=str(payload.get("year") or ""),
                    source_name=str(location.get("host_type") or "Unpaywall"),
                    trend_score=0.0,
                    scientific_density=0.0,
                    metadata={
                        "resource_type": "publication",
                        "doi": doi,
                        "access_status": (
                            "open_access"
                            if bool(payload.get("is_oa")) and url
                            else "link_only"
                        ),
                        "license": location.get("license"),
                        "version": location.get("version"),
                        "full_text_url": url if bool(payload.get("is_oa")) else None,
                        "model_processing_allowed": False,
                    },
                )
            )
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw_payloads,
            signals=signals,
            domains=sorted(self.domains),
            http_method=self.http_method,
        )

    def fetch_content(
        self,
        identifier: str,
        *,
        max_bytes: int,
        allow_proxy_fake_ip: bool = False,
    ) -> ProviderContentResult:
        validator = self.url_validator
        if allow_proxy_fake_ip and validator is _validate_public_https_url:
            validator = partial(
                _validate_public_https_url,
                allow_proxy_fake_ip=True,
            )
        validator(identifier)
        request = urllib.request.Request(
            identifier,
            headers={
                "Accept": "application/pdf",
                "User-Agent": "ResearchForge/0.1",
            },
        )
        content, final_url, mime_type, headers = self.content_transport(
            request,
            max_bytes=max_bytes,
            url_validator=validator,
        )
        validator(final_url)
        if mime_type.casefold() not in {
            "application/pdf",
            "application/octet-stream",
        }:
            raise ProviderFailure(
                "open-access content is not a PDF",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        if not content.startswith(b"%PDF-"):
            raise ProviderFailure(
                "open-access response does not contain a PDF signature",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        return ProviderContentResult(
            provider=self.provider_id,
            source_url=identifier,
            final_url=final_url,
            content=content,
            mime_type="application/pdf",
            response_headers=headers,
        )


def _validate_public_https_url(
    url: str,
    *,
    allow_proxy_fake_ip: bool = False,
) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise ProviderFailure(
            "open-access content URL must use public HTTPS",
            ProviderErrorClass.POLICY_DENIED,
        )
    if parsed.username or parsed.password:
        raise ProviderFailure(
            "open-access content URL cannot contain credentials",
            ProviderErrorClass.POLICY_DENIED,
        )
    hostname = parsed.hostname.casefold().rstrip(".")
    if (
        hostname == "localhost"
        or "." not in hostname
        or hostname.endswith(_LOCAL_HOST_SUFFIXES)
    ):
        raise ProviderFailure(
            "open-access content URL cannot target a local hostname",
            ProviderErrorClass.POLICY_DENIED,
        )
    literal_ip = False
    try:
        addresses = [ipaddress.ip_address(hostname)]
        literal_ip = True
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            ]
        except OSError as exc:
            raise ProviderFailure(
                "open-access content host cannot be resolved",
                ProviderErrorClass.TRANSIENT_NETWORK_ERROR,
            ) from exc
    if addresses and all(address.is_global for address in addresses):
        return
    proxy_fake_ip = (
        allow_proxy_fake_ip
        and not literal_ip
        and bool(addresses)
        and all(address in _PROXY_FAKE_IP_NETWORK for address in addresses)
        and _local_https_proxy_configured()
    )
    if not proxy_fake_ip:
        raise ProviderFailure(
            "open-access content URL resolved to a non-public address",
            ProviderErrorClass.POLICY_DENIED,
        )


def _local_https_proxy_configured() -> bool:
    proxy_url = urllib.request.getproxies().get("https", "")
    parsed = urllib.parse.urlparse(proxy_url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return parsed.hostname.casefold() == "localhost"
    return address.is_loopback
