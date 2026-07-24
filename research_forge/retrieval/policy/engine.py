from __future__ import annotations

from pydantic import Field, model_validator

from ...models import StrictModel
from ..domain.models import (
    NetworkMode,
    PolicyDecision,
    PolicyOutcome,
    ResourceType,
    RetrievalBudget,
    RetrievalRequest,
    retrieval_id,
)
from .profiles import STAGE_PROFILES


class RetrievalNetworkPolicy(StrictModel):
    schema_version: int = 1
    policy_id: str
    mode: NetworkMode = NetworkMode.OFFLINE
    allowed_providers: set[str] = Field(default_factory=set)
    allowed_domains: set[str] = Field(default_factory=set)
    allowed_http_methods: set[str] = Field(default_factory=lambda: {"GET"})
    allowed_resource_types: set[ResourceType] = Field(default_factory=set)
    allow_metadata: bool = True
    allow_abstract: bool = False
    allow_full_text: bool = False
    allow_repository_download: bool = False
    allow_dataset_download: bool = False
    allow_authenticated_access: bool = False
    allow_external_write: bool = False
    allow_proxy_fake_ip: bool = False
    max_queries: int = Field(default=0, ge=0)
    max_results: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=0, ge=0)
    max_cost: float = Field(default=0.0, ge=0)
    allowed_outbound_data_classes: set[str] = Field(
        default_factory=lambda: {"sanitized_search_query"}
    )
    prohibited_outbound_data_classes: set[str] = Field(
        default_factory=lambda: {
            "secret",
            "credential",
            "private_key",
            "local_path",
            "private_project_file",
            "raw_dataset",
        }
    )
    query_sanitization_required: bool = True
    approved_by: str | None = None
    approved_at: str | None = None

    @model_validator(mode="after")
    def validate_proxy_fake_ip_approval(self) -> "RetrievalNetworkPolicy":
        if self.allow_proxy_fake_ip and (
            not self.approved_by or not self.approved_at
        ):
            raise ValueError(
                "proxy fake-IP support requires explicit owner approval metadata"
            )
        return self

    @classmethod
    def offline(cls, project_id: str) -> "RetrievalNetworkPolicy":
        return cls(policy_id=retrieval_id("network-policy", project_id, "offline"))


class PolicyEngine:
    def evaluate(
        self,
        request: RetrievalRequest,
        policy: RetrievalNetworkPolicy,
        *,
        sanitized: bool,
    ) -> PolicyDecision:
        profile = STAGE_PROFILES[request.phase]
        reasons: list[str] = []
        outcome = PolicyOutcome.ALLOW
        if policy.mode is NetworkMode.OFFLINE:
            outcome = PolicyOutcome.DENY
            reasons.append("project network mode is offline")
        if request.purpose not in profile.allowed_purposes:
            outcome = PolicyOutcome.DENY
            reasons.append(
                f"purpose {request.purpose!r} is not allowed in {request.phase.value}"
            )
        if policy.mode not in profile.allowed_modes:
            outcome = PolicyOutcome.DENY
            reasons.append("network mode is not permitted by the stage profile")
        disallowed_types = set(request.requested_resource_types) - (
            profile.allowed_resource_types
            & (policy.allowed_resource_types or set(ResourceType))
        )
        if disallowed_types:
            outcome = PolicyOutcome.DENY
            reasons.append(
                "resource types denied: "
                + ", ".join(sorted(item.value for item in disallowed_types))
            )
        if policy.query_sanitization_required and not sanitized:
            outcome = PolicyOutcome.DENY
            reasons.append("query sanitization has not completed")
        if not policy.allowed_providers:
            outcome = PolicyOutcome.DENY
            reasons.append("no providers are authorized")
        unauthorized_providers = set(request.requested_providers) - set(
            policy.allowed_providers
        )
        if unauthorized_providers:
            outcome = PolicyOutcome.DENY
            reasons.append(
                "providers denied: " + ", ".join(sorted(unauthorized_providers))
            )
        if request.requested_usage_role not in profile.default_usage_roles:
            outcome = PolicyOutcome.DENY
            reasons.append(
                f"usage role {request.requested_usage_role!r} is not allowed "
                f"in {request.phase.value}"
            )
        if profile.requires_contract_binding and not request.contract_refs:
            outcome = PolicyOutcome.DENY
            reasons.append(
                f"{request.phase.value} retrieval requires contract authorization"
            )
        if request.phase.value == "repair" and not any(
            item.contract_type == "repair" for item in request.contract_refs
        ):
            outcome = PolicyOutcome.DENY
            reasons.append("repair retrieval requires a repair contract")
        effective = RetrievalBudget(
            max_queries=min_positive(request.budget.max_queries, policy.max_queries),
            max_results=min_positive(request.budget.max_results, policy.max_results),
            max_download_bytes=min_positive(
                request.budget.max_download_bytes, policy.max_bytes
            ),
            max_cost=min_positive_float(request.budget.max_cost, policy.max_cost),
        )
        if not reasons:
            reasons.append("request satisfies deterministic stage and project policy")
        return PolicyDecision(
            decision_id=retrieval_id(
                "policy-decision",
                request.request_id,
                policy.policy_id,
                outcome.value,
                *reasons,
            ),
            request_id=request.request_id,
            outcome=outcome,
            policy_id=policy.policy_id,
            profile_id=profile.profile_id,
            reasons=reasons,
            allowed_providers=(
                sorted(policy.allowed_providers)
                if outcome is PolicyOutcome.ALLOW
                else []
            ),
            effective_budget=effective,
        )


def min_positive(left: int, right: int) -> int:
    values = [item for item in (left, right) if item > 0]
    return min(values) if values else 0


def min_positive_float(left: float, right: float) -> float:
    values = [item for item in (left, right) if item > 0]
    return min(values) if values else 0.0
