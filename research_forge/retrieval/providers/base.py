from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from ...claim_discovery import TrendSignal
from ..domain.models import ProviderErrorClass, QueryPlan


class ProviderFailure(RuntimeError):
    def __init__(self, message: str, classification: ProviderErrorClass) -> None:
        super().__init__(message)
        self.classification = classification


@dataclass
class ProviderSearchResult:
    provider: str
    raw_payloads: list[dict[str, Any]] = field(default_factory=list)
    signals: list[TrendSignal] = field(default_factory=list)
    query_records: list[dict[str, Any]] = field(default_factory=list)
    source_records: list[dict[str, Any]] = field(default_factory=list)
    citation_records: list[dict[str, Any]] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    http_method: str = "GET"


@dataclass(frozen=True)
class ProviderContentResult:
    provider: str
    source_url: str
    final_url: str
    content: bytes
    mime_type: str
    license: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)


class CredentialResolver:
    """Provider-only credential access; values never enter request models."""

    def get(self, name: str) -> str | None:
        return os.getenv(name, "").strip() or None


def json_transport(
    request: urllib.request.Request,
) -> dict[str, Any] | list[Any]:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ProviderFailure(
                "provider authentication rejected",
                ProviderErrorClass.AUTHENTICATION_ERROR,
            ) from exc
        if exc.code == 429:
            raise ProviderFailure(
                "provider rate limit", ProviderErrorClass.RATE_LIMIT
            ) from exc
        if exc.code == 404:
            raise ProviderFailure(
                "resource not found", ProviderErrorClass.RESOURCE_NOT_FOUND
            ) from exc
        raise ProviderFailure(
            f"provider HTTP error {exc.code}",
            ProviderErrorClass.PERMANENT_PROVIDER_ERROR,
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderFailure(
            "transient provider network error",
            ProviderErrorClass.TRANSIENT_NETWORK_ERROR,
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderFailure(
            "provider returned malformed JSON",
            ProviderErrorClass.MALFORMED_RESPONSE,
        ) from exc
    if not isinstance(payload, (dict, list)):
        raise ProviderFailure(
            "provider response is not a JSON object or array",
            ProviderErrorClass.MALFORMED_RESPONSE,
        )
    return payload


def binary_transport(
    request: urllib.request.Request,
    *,
    max_bytes: int,
    url_validator: Callable[[str], None] | None = None,
) -> tuple[bytes, str, str, dict[str, str]]:
    """Fetch a bounded provider-authorized payload without exposing credentials."""

    if max_bytes <= 0:
        raise ProviderFailure(
            "content acquisition requires a positive byte budget",
            ProviderErrorClass.POLICY_DENIED,
        )
    if url_validator is not None:
        url_validator(request.full_url)

    class ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if url_validator is not None:
                url_validator(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    try:
        opener = urllib.request.build_opener(ValidatingRedirectHandler())
        with opener.open(request, timeout=30) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ProviderFailure(
                    "provider content exceeds the approved byte budget",
                    ProviderErrorClass.POLICY_DENIED,
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ProviderFailure(
                        "provider content exceeds the approved byte budget",
                        ProviderErrorClass.POLICY_DENIED,
                    )
                chunks.append(chunk)
            headers = {
                name.casefold(): value
                for name, value in response.headers.items()
                if name.casefold()
                in {
                    "content-type",
                    "content-length",
                    "etag",
                    "last-modified",
                }
            }
            mime_type = (
                response.headers.get_content_type()
                if hasattr(response.headers, "get_content_type")
                else str(response.headers.get("Content-Type") or "").split(";")[0]
            )
            return b"".join(chunks), response.geturl(), mime_type, headers
    except ProviderFailure:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            classification = ProviderErrorClass.LICENSE_RESTRICTED
        elif exc.code == 404:
            classification = ProviderErrorClass.RESOURCE_NOT_FOUND
        elif exc.code == 429:
            classification = ProviderErrorClass.RATE_LIMIT
        else:
            classification = ProviderErrorClass.PERMANENT_PROVIDER_ERROR
        raise ProviderFailure(
            f"provider content HTTP error {exc.code}", classification
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderFailure(
            "transient provider content network error",
            ProviderErrorClass.TRANSIENT_NETWORK_ERROR,
        ) from exc


class ProviderAdapter(ABC):
    provider_id: str
    domains: set[str]
    http_method: str = "GET"

    def __init__(
        self,
        *,
        credentials: CredentialResolver | None = None,
        transport=json_transport,
    ) -> None:
        self.credentials = credentials or CredentialResolver()
        self.transport = transport

    @abstractmethod
    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        raise NotImplementedError

    def fetch_metadata(self, identifier: str) -> ProviderSearchResult:
        raise ProviderFailure(
            "fetch_metadata is not implemented by this provider",
            ProviderErrorClass.PERMANENT_PROVIDER_ERROR,
        )

    def resolve_identifier(self, identifier: str) -> ProviderSearchResult:
        return self.fetch_metadata(identifier)

    def fetch_content(
        self,
        identifier: str,
        *,
        max_bytes: int,
        allow_proxy_fake_ip: bool = False,
    ) -> ProviderContentResult:
        raise ProviderFailure(
            "content acquisition is not implemented by this provider",
            ProviderErrorClass.LICENSE_RESTRICTED,
        )

    def health_check(self) -> dict[str, str]:
        return {"provider": self.provider_id, "status": "configured"}

    def classify_error(self, exc: Exception) -> ProviderErrorClass:
        if isinstance(exc, ProviderFailure):
            return exc.classification
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return ProviderErrorClass.TRANSIENT_NETWORK_ERROR
        return ProviderErrorClass.PERMANENT_PROVIDER_ERROR
