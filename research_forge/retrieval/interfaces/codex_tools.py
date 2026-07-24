"""Narrow Codex facade.

No generic URL, HTTP method, credential, form submission, or executable
download capability is exposed here. Callers submit retrieval intent; the
deterministic policy engine decides whether an adapter may execute.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import ResourceType, RetrievalPhase
from .service import RetrievalGateway


class CodexRetrievalTools:
    def __init__(self, gateway: RetrievalGateway) -> None:
        self.gateway = gateway

    def plan_retrieval(self, **context: Any) -> dict[str, Any]:
        return self.gateway.plan(**context).model_dump(mode="json")

    def search_publications(self, **context: Any) -> dict[str, Any]:
        context["resource_types"] = [ResourceType.PUBLICATION]
        return self.gateway.plan(**context).model_dump(mode="json")

    def search_datasets(self, **context: Any) -> dict[str, Any]:
        context["resource_types"] = [ResourceType.DATASET]
        return self.gateway.plan(**context).model_dump(mode="json")

    def search_code_repositories(self, **context: Any) -> dict[str, Any]:
        context["resource_types"] = [ResourceType.CODE_REPOSITORY]
        return self.gateway.plan(**context).model_dump(mode="json")

    def resolve_identifier(self, **context: Any) -> dict[str, Any]:
        context.setdefault("purpose", "doi_resolution")
        return self.gateway.plan(**context).model_dump(mode="json")

    def fetch_resource_metadata(self, resource_id: str) -> dict[str, Any]:
        return self.gateway.repository.load_resource(resource_id).model_dump(
            mode="json"
        )

    def fetch_approved_resource(self, binding_id: str) -> dict[str, Any]:
        binding = next(
            item
            for item in self.gateway.repository.list_bindings()
            if item.binding_id == binding_id
        )
        if binding.phase is not RetrievalPhase.EXPERIMENTATION:
            raise ValueError("resource is not approved for experimentation")
        snapshots = self.gateway.repository.list_snapshots(binding.resource_id)
        return {
            "binding": binding.model_dump(mode="json"),
            "snapshot": next(
                item.model_dump(mode="json")
                for item in snapshots
                if item.snapshot_id == binding.snapshot_id
            ),
        }

    def verify_citation(self, **context: Any) -> dict[str, Any]:
        context["phase"] = RetrievalPhase.SYNTHESIS
        context["purpose"] = "citation_verification"
        context["resource_types"] = [ResourceType.PUBLICATION]
        return self.gateway.plan(**context).model_dump(mode="json")

    def check_retraction(self, **context: Any) -> dict[str, Any]:
        context["phase"] = RetrievalPhase.SYNTHESIS
        context["purpose"] = "retraction_check"
        context["resource_types"] = [
            ResourceType.PUBLICATION,
            ResourceType.RETRACTION_NOTICE,
        ]
        return self.gateway.plan(**context).model_dump(mode="json")

    def get_retrieval_coverage(self, coverage_report_id: str) -> dict[str, Any]:
        return self.gateway.repository.load_coverage(coverage_report_id).model_dump(
            mode="json"
        )

    def propose_resource_promotion(
        self, binding_id: str, **context: Any
    ) -> dict[str, Any]:
        return self.gateway.promote_binding(binding_id, **context).model_dump(
            mode="json"
        )
