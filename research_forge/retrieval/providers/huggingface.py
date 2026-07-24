"""Hugging Face Hub provider using the official ``huggingface_hub`` client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...claim_discovery import TrendSignal
from ..domain.models import (
    ProviderErrorClass,
    QueryPlan,
    ResourceType,
    retrieval_id,
)
from .base import ProviderAdapter, ProviderFailure, ProviderSearchResult


class HuggingFaceResearchAdapter(ProviderAdapter):
    provider_id = "huggingface"
    domains = {"huggingface.co"}
    http_method = "SDK"

    def __init__(self, *, api_factory: Callable[..., Any] | None = None) -> None:
        super().__init__()
        self._api_factory = api_factory

    def _api(self):
        if self._api_factory is not None:
            return self._api_factory()
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:
            raise ProviderFailure(
                "huggingface_hub is not installed",
                ProviderErrorClass.PERMANENT_PROVIDER_ERROR,
            ) from exc
        return HfApi(token=self.credentials.get("HF_TOKEN"))

    def health_check(self) -> dict[str, str]:
        try:
            import huggingface_hub
        except ImportError:
            return {
                "provider": self.provider_id,
                "status": "unavailable",
                "reason": "huggingface_hub is not installed",
            }
        return {
            "provider": self.provider_id,
            "status": "degraded",
            "version": str(getattr(huggingface_hub, "__version__", "unknown")),
            "credential_mode": (
                "optional_user_oauth"
                if self.credentials.get("HF_TOKEN")
                else "none_required"
            ),
            "reason": "SDK is installed; live Hub health has not been verified",
        }

    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        api = self._api()
        raw_payloads: list[dict[str, Any]] = []
        signals: list[TrendSignal] = []
        max_results = max(1, min(plan.budget.max_results or 10, 50))
        for query in plan.sanitized_queries[: plan.budget.max_queries or 1]:
            if ResourceType.MODEL in plan.resource_types:
                rows = self.search_models(
                    query,
                    limit=max_results,
                    api=api,
                )
                signals.extend(self._signals(rows, query, "model", raw_payloads))
            if ResourceType.DATASET in plan.resource_types:
                rows = self.search_datasets(
                    query,
                    limit=max_results,
                    api=api,
                )
                signals.extend(self._signals(rows, query, "dataset", raw_payloads))
            if ResourceType.SPACE in plan.resource_types:
                rows = self.search_spaces(
                    query,
                    limit=max_results,
                    api=api,
                )
                signals.extend(self._signals(rows, query, "space", raw_payloads))
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw_payloads,
            signals=signals[:max_results],
            domains=sorted(self.domains),
            http_method=self.http_method,
        )

    def search_models(
        self,
        query: str,
        *,
        limit: int = 10,
        api: Any | None = None,
    ) -> list[dict[str, Any]]:
        client = api or self._api()
        return [
            _as_dict(item)
            for item in client.list_models(
                search=query,
                limit=min(max(limit, 1), 100),
                full=True,
            )
        ]

    def search_datasets(
        self,
        query: str,
        *,
        limit: int = 10,
        api: Any | None = None,
    ) -> list[dict[str, Any]]:
        client = api or self._api()
        return [
            _as_dict(item)
            for item in client.list_datasets(
                search=query,
                limit=min(max(limit, 1), 100),
                full=True,
            )
        ]

    def search_spaces(
        self,
        query: str,
        *,
        limit: int = 10,
        api: Any | None = None,
    ) -> list[dict[str, Any]]:
        client = api or self._api()
        return [
            _as_dict(item)
            for item in client.list_spaces(
                search=query,
                limit=min(max(limit, 1), 100),
                full=True,
            )
        ]

    def _signals(
        self,
        rows: list[Any],
        query: str,
        kind: str,
        raw_payloads: list[dict[str, Any]],
    ) -> list[TrendSignal]:
        signals: list[TrendSignal] = []
        for item in rows:
            row = _as_dict(item)
            raw_payloads.append(row)
            repo_id = str(
                row.get("id") or row.get("modelId") or row.get("datasetId") or ""
            )
            revision = str(row.get("sha") or "")
            if not repo_id or len(revision) != 40:
                continue
            private = bool(row.get("private"))
            gated = bool(row.get("gated"))
            card_data = row.get("cardData") or row.get("card_data") or {}
            if hasattr(card_data, "to_dict"):
                card_data = card_data.to_dict()
            if not isinstance(card_data, dict):
                card_data = {}
            license_id = card_data.get("license")
            tags = [str(value) for value in row.get("tags") or []]
            url = f"https://huggingface.co/{repo_id}"
            if kind == "dataset":
                url = f"https://huggingface.co/datasets/{repo_id}"
            elif kind == "space":
                url = f"https://huggingface.co/spaces/{repo_id}"
            signals.append(
                TrendSignal(
                    signal_id=retrieval_id(
                        "huggingface-signal", kind, repo_id, revision
                    ),
                    provider=self.provider_id,
                    signal_class="adoption_signal",
                    query=query,
                    title=repo_id,
                    summary=str(card_data.get("description") or "")[:1200],
                    url=url,
                    published_at=str(
                        row.get("lastModified") or row.get("last_modified") or ""
                    ),
                    source_name=repo_id.split("/", 1)[0],
                    engagement={
                        "downloads": int(row.get("downloads") or 0),
                        "likes": int(row.get("likes") or 0),
                    },
                    terms=tags,
                    trend_score=0.0,
                    scientific_density=0.0,
                    metadata={
                        "resource_type": kind,
                        "repo_id": repo_id,
                        "revision": revision,
                        "model_identifier": repo_id if kind == "model" else None,
                        "dataset_identifier": repo_id if kind == "dataset" else None,
                        "license": license_id,
                        "private": private,
                        "gated": gated,
                        "access_status": (
                            "private"
                            if private
                            else "gated"
                            if gated
                            else "metadata_only"
                        ),
                        "task": row.get("pipeline_tag"),
                        "library": row.get("library_name"),
                        "tags": tags,
                        "base_model": card_data.get("base_model"),
                        "training_dataset": card_data.get("datasets"),
                        "siblings": row.get("siblings") or [],
                        "adoption_signal": {
                            "downloads": int(row.get("downloads") or 0),
                            "likes": int(row.get("likes") or 0),
                        },
                        "scientific_quality_score": None,
                    },
                )
            )
        return signals

    def get_model_info(
        self, repo_id: str, revision: str | None = None
    ) -> dict[str, Any]:
        return _as_dict(
            self._api().model_info(
                repo_id,
                revision=revision,
                files_metadata=True,
            )
        )

    def get_dataset_info(
        self, repo_id: str, revision: str | None = None
    ) -> dict[str, Any]:
        return _as_dict(
            self._api().dataset_info(
                repo_id,
                revision=revision,
                files_metadata=True,
            )
        )

    def get_space_info(
        self, repo_id: str, revision: str | None = None
    ) -> dict[str, Any]:
        return _as_dict(
            self._api().space_info(
                repo_id,
                revision=revision,
                files_metadata=True,
            )
        )

    def get_model_card(self, repo_id: str, revision: str) -> dict[str, Any]:
        return self.get_model_info(repo_id, revision)

    def get_dataset_card(self, repo_id: str, revision: str) -> dict[str, Any]:
        return self.get_dataset_info(repo_id, revision)

    def get_repo_files(
        self, repo_id: str, resource_type: str, revision: str
    ) -> list[dict[str, Any]]:
        getter = {
            "model": self.get_model_info,
            "dataset": self.get_dataset_info,
            "space": self.get_space_info,
        }.get(resource_type)
        if getter is None:
            raise ValueError("unsupported Hugging Face resource type")
        payload = getter(repo_id, revision)
        return [
            item for item in payload.get("siblings") or [] if isinstance(item, dict)
        ]

    def get_license(
        self, repo_id: str, resource_type: str, revision: str
    ) -> str | list[str] | None:
        getter = (
            self.get_dataset_info if resource_type == "dataset" else self.get_model_info
        )
        payload = getter(repo_id, revision)
        card = payload.get("cardData") or payload.get("card_data") or {}
        return card.get("license") if isinstance(card, dict) else None

    def get_safetensors_metadata(self, repo_id: str, revision: str) -> dict[str, Any]:
        payload = self.get_model_info(repo_id, revision)
        value = payload.get("safetensors")
        return value if isinstance(value, dict) else {}

    def resolve_revision(self, repo_id: str, resource_type: str, revision: str) -> str:
        getter = {
            "model": self.get_model_info,
            "dataset": self.get_dataset_info,
            "space": self.get_space_info,
        }.get(resource_type)
        if getter is None:
            raise ValueError("unsupported Hugging Face resource type")
        sha = str(getter(repo_id, revision).get("sha") or "")
        if len(sha) != 40:
            raise ProviderFailure(
                "Hugging Face revision did not resolve to a full SHA",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        return sha

    def download_approved_file(
        self,
        *,
        repo_id: str,
        filename: str,
        revision_sha: str,
        resource_type: str,
        local_dir: str,
        authorization_approved: bool,
    ) -> str:
        if not authorization_approved:
            raise ProviderFailure(
                "download requires explicit contract or user authorization",
                ProviderErrorClass.POLICY_DENIED,
            )
        from huggingface_hub import hf_hub_download

        return str(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision_sha,
                repo_type=(None if resource_type == "model" else resource_type),
                local_dir=local_dir,
                token=self.credentials.get("HF_TOKEN"),
            )
        )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    raise ProviderFailure(
        "Hugging Face response is not serializable",
        ProviderErrorClass.MALFORMED_RESPONSE,
    )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)
