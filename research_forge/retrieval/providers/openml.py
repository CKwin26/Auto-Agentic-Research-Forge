"""Official OpenML Task and Dataset adapter.

The adapter deliberately resolves exact OpenML identities.  It does not turn
free-form discovery text into an implicit scientific dataset choice.  Open
search belongs to Discovery/Protocol; exact dataset acquisition for Stage 3
is separately authorized and frozen by :class:`RetrievalGateway`.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from collections.abc import Callable
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


_TASK_ID = re.compile(r"^(?:openml[-_ ]?)?task\s*[:/# ]\s*(\d+)$", re.I)
_DATASET_ID = re.compile(
    r"^(?:openml[-_ ]?)?(?:dataset|data)\s*[:/# ]\s*(\d+)(?::v?(\d+))?$",
    re.I,
)
_OPENML_HOSTS = {
    "openml.org",
    "www.openml.org",
    "api.openml.org",
    "data.openml.org",
}


def _validate_openml_https_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in _OPENML_HOSTS:
        raise ProviderFailure(
            "OpenML content URL is outside the approved official domains",
            ProviderErrorClass.POLICY_DENIED,
        )


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderFailure(
            f"OpenML returned malformed {label} metadata",
            ProviderErrorClass.MALFORMED_RESPONSE,
        )
    return value


def _license_id(value: Any) -> str | None:
    text = str(value or "").strip()
    aliases = {
        "cc0": "cc0",
        "cc0 1.0": "cc0-1.0",
        "cc by": "cc-by",
        "cc-by": "cc-by",
        "cc by 4.0": "cc-by-4.0",
        "cc-by-4.0": "cc-by-4.0",
        "public domain": "public-domain",
    }
    return aliases.get(text.casefold(), text or None)


class OpenMLAdapter(ProviderAdapter):
    provider_id = "openml"
    domains = set(_OPENML_HOSTS)
    http_method = "GET"

    def __init__(
        self,
        *args,
        content_transport: Callable[..., tuple[bytes, str, str, dict[str, str]]]
        | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.content_transport = content_transport or binary_transport

    def health_check(self) -> dict[str, str]:
        return {
            "provider": self.provider_id,
            "status": "degraded",
            "credential_mode": "none_required",
            "reason": "adapter is configured; deployment live acceptance is separate",
        }

    def _request(self, path: str) -> dict[str, Any]:
        url = "https://www.openml.org" + path
        payload = self.transport(
            urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ResearchForge/0.1",
                },
            )
        )
        return _object(payload, label="response")

    def _task(self, task_id: int) -> dict[str, Any]:
        payload = self._request(f"/api/v1/json/task/{task_id}")
        return _object(payload.get("task"), label="task")

    def _dataset(self, dataset_id: int) -> dict[str, Any]:
        payload = self._request(f"/api/v1/json/data/{dataset_id}")
        return _object(payload.get("data_set_description"), label="dataset")

    @staticmethod
    def _task_binding(task: dict[str, Any]) -> dict[str, Any]:
        source_data: dict[str, Any] = {}
        estimation: dict[str, Any] = {}
        for item in task.get("input") or []:
            if not isinstance(item, dict):
                continue
            if item.get("name") == "source_data":
                source_data = _object(item.get("data_set"), label="task source_data")
            elif item.get("name") == "estimation_procedure":
                estimation = _object(
                    item.get("estimation_procedure"),
                    label="task estimation_procedure",
                )
        dataset_id = str(source_data.get("data_set_id") or "").strip()
        if not dataset_id.isdigit():
            raise ProviderFailure(
                "OpenML task has no authoritative dataset binding",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        parameters = {
            str(item.get("name")): item.get("value")
            for item in estimation.get("parameter") or []
            if isinstance(item, dict) and item.get("name")
        }
        return {
            "dataset_id": int(dataset_id),
            "target_feature": str(source_data.get("target_feature") or "").strip(),
            "estimation_procedure": {
                "id": estimation.get("id"),
                "type": estimation.get("type"),
                "data_splits_url": estimation.get("data_splits_url"),
                "parameters": parameters,
            },
        }

    def _signal(
        self,
        *,
        query: str,
        dataset: dict[str, Any],
        task: dict[str, Any] | None,
        task_binding: dict[str, Any] | None,
    ) -> TrendSignal:
        dataset_id = str(dataset.get("id") or "").strip()
        version = str(dataset.get("version") or dataset.get("version_label") or "").strip()
        if not dataset_id.isdigit() or not version:
            raise ProviderFailure(
                "OpenML dataset identity is not version-pinned",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        task_id = str((task or {}).get("task_id") or "").strip()
        name = str(dataset.get("name") or f"OpenML dataset {dataset_id}").strip()
        license_id = _license_id(dataset.get("licence") or dataset.get("license"))
        dataset_url = f"https://www.openml.org/d/{dataset_id}"
        metadata = {
            "resource_type": ResourceType.DATASET.value,
            "dataset_identifier": f"openml-dataset:{dataset_id}:v{version}",
            "openml_dataset_id": int(dataset_id),
            "openml_task_id": int(task_id) if task_id.isdigit() else None,
            "version": version,
            "version_label": dataset.get("version_label"),
            "format": dataset.get("format"),
            "file_id": dataset.get("file_id"),
            "download_url": dataset.get("url"),
            "parquet_url": dataset.get("parquet_url"),
            "md5_checksum": dataset.get("md5_checksum"),
            "default_target_attribute": dataset.get("default_target_attribute"),
            "target_feature": (task_binding or {}).get("target_feature"),
            "task_type": (task or {}).get("task_type"),
            "task_type_id": (task or {}).get("task_type_id"),
            "estimation_procedure": (task_binding or {}).get("estimation_procedure"),
            "visibility": dataset.get("visibility"),
            "status": dataset.get("status"),
            "license": license_id,
            "access_status": (
                "open_access"
                if str(dataset.get("visibility") or "").casefold() == "public"
                else "metadata_only"
            ),
            # Legal/model-processing permission is decided later from the
            # exact Research Contract and project policy, never inferred here.
            "model_processing_allowed": False,
        }
        return TrendSignal(
            signal_id=retrieval_id(
                "openml-signal",
                dataset_id,
                version,
                task_id or "dataset-only",
            ),
            provider=self.provider_id,
            signal_class="official_source",
            query=query,
            title=(
                f"{name} — OpenML task {task_id}"
                if task_id
                else f"{name} — OpenML dataset {dataset_id}"
            ),
            summary=str(dataset.get("description") or "")[:1200],
            url=dataset_url,
            published_at=str(dataset.get("upload_date") or ""),
            source_name=str(dataset.get("creator") or "OpenML"),
            terms=[
                item
                for item in (
                    str((task or {}).get("task_type") or "").strip(),
                    str(dataset.get("format") or "").strip(),
                )
                if item
            ],
            trend_score=0.0,
            scientific_density=1.0,
            metadata=metadata,
        )

    def _resolve(self, query: str) -> ProviderSearchResult:
        task_match = _TASK_ID.fullmatch(query.strip())
        dataset_match = _DATASET_ID.fullmatch(query.strip())
        if task_match:
            task_id = int(task_match.group(1))
            task = self._task(task_id)
            binding = self._task_binding(task)
            dataset = self._dataset(binding["dataset_id"])
            signal = self._signal(
                query=query,
                dataset=dataset,
                task=task,
                task_binding=binding,
            )
            return ProviderSearchResult(
                provider=self.provider_id,
                raw_payloads=[{"task": task}, {"data_set_description": dataset}],
                signals=[signal],
                query_records=[{"query": query, "identity_kind": "task", "id": task_id}],
                source_records=[
                    {
                        "provider": self.provider_id,
                        "task_id": task_id,
                        "dataset_id": binding["dataset_id"],
                    }
                ],
                domains=sorted(self.domains),
                http_method=self.http_method,
            )
        if dataset_match:
            dataset_id = int(dataset_match.group(1))
            requested_version = dataset_match.group(2)
            dataset = self._dataset(dataset_id)
            actual_version = str(dataset.get("version") or dataset.get("version_label") or "")
            if requested_version and requested_version != actual_version:
                raise ProviderFailure(
                    "OpenML dataset version does not match the requested identity",
                    ProviderErrorClass.METADATA_CONFLICT,
                )
            signal = self._signal(
                query=query,
                dataset=dataset,
                task=None,
                task_binding=None,
            )
            return ProviderSearchResult(
                provider=self.provider_id,
                raw_payloads=[{"data_set_description": dataset}],
                signals=[signal],
                query_records=[
                    {"query": query, "identity_kind": "dataset", "id": dataset_id}
                ],
                source_records=[
                    {"provider": self.provider_id, "dataset_id": dataset_id}
                ],
                domains=sorted(self.domains),
                http_method=self.http_method,
            )
        raise ProviderFailure(
            "OpenML adapter requires an exact task:<id> or dataset:<id>[:v<version>] query",
            ProviderErrorClass.POLICY_DENIED,
        )

    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        if not set(plan.resource_types).intersection(
            {ResourceType.DATASET, ResourceType.BENCHMARK}
        ):
            raise ProviderFailure(
                "OpenML resolution requires dataset or benchmark resource type",
                ProviderErrorClass.POLICY_DENIED,
            )
        raw_payloads: list[dict[str, Any]] = []
        signals: list[TrendSignal] = []
        query_records: list[dict[str, Any]] = []
        source_records: list[dict[str, Any]] = []
        limit = plan.budget.max_results or len(plan.sanitized_queries) or 1
        for query in plan.sanitized_queries[: plan.budget.max_queries or 1]:
            resolved = self._resolve(query)
            raw_payloads.extend(resolved.raw_payloads)
            signals.extend(resolved.signals[: max(0, limit - len(signals))])
            query_records.extend(resolved.query_records)
            source_records.extend(resolved.source_records)
            if len(signals) >= limit:
                break
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw_payloads,
            signals=signals,
            query_records=query_records,
            source_records=source_records,
            domains=sorted(self.domains),
            http_method=self.http_method,
        )

    def fetch_metadata(self, identifier: str) -> ProviderSearchResult:
        return self._resolve(identifier)

    def fetch_content(
        self,
        identifier: str,
        *,
        max_bytes: int,
        allow_proxy_fake_ip: bool = False,
    ) -> ProviderContentResult:
        del allow_proxy_fake_ip
        _validate_openml_https_url(identifier)
        request = urllib.request.Request(
            identifier,
            headers={
                "Accept": "application/octet-stream, text/plain, application/x-arff",
                "User-Agent": "ResearchForge/0.1",
            },
        )
        content, final_url, mime_type, headers = self.content_transport(
            request,
            max_bytes=max_bytes,
            url_validator=_validate_openml_https_url,
        )
        if mime_type.casefold().startswith("text/html"):
            raise ProviderFailure(
                "OpenML dataset download returned HTML instead of data",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        return ProviderContentResult(
            provider=self.provider_id,
            source_url=identifier,
            final_url=final_url,
            content=content,
            mime_type=mime_type or "application/octet-stream",
            response_headers=headers,
        )


__all__ = ["OpenMLAdapter"]
