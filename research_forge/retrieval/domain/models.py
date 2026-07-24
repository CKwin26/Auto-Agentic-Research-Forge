from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from ...models import StrictModel, utc_now


def retrieval_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


class RetrievalPhase(StrEnum):
    DISCOVERY = "discovery"
    PROTOCOL = "protocol"
    EXPERIMENTATION = "experimentation"
    SYNTHESIS = "synthesis"
    REPAIR = "repair"


class NetworkMode(StrEnum):
    OFFLINE = "offline"
    PUBLIC_RESEARCH = "public_research"
    PUBLIC_RESEARCH_PLUS_INSTITUTION = "public_research_plus_institution"
    ACADEMIC_READ = "academic_read"
    PUBLIC_WEB_READ = "public_web_read"
    AUTHENTICATED_READ = "authenticated_read"
    EXTERNAL_WRITE = "external_write"


class ResourceType(StrEnum):
    PUBLICATION = "publication"
    PREPRINT = "preprint"
    DATASET = "dataset"
    CODE_REPOSITORY = "code_repository"
    CODE_RELEASE = "code_release"
    ISSUE = "issue"
    MODEL = "model"
    SPACE = "space"
    BENCHMARK = "benchmark"
    STANDARD = "standard"
    DOCUMENTATION = "documentation"
    WEB_SOURCE = "web_source"
    RETRACTION_NOTICE = "retraction_notice"
    SUBMISSION_RULE = "submission_rule"
    PUBLISHER_NOTICE = "publisher_notice"
    INSTITUTIONAL_DOCUMENT = "institutional_document"


class ResourceSetStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"
    SUPERSEDED = "superseded"


class RetrievalStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    CANCELLED = "cancelled"


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ProviderErrorClass(StrEnum):
    TRANSIENT_NETWORK_ERROR = "transient_network_error"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION_ERROR = "authentication_error"
    POLICY_DENIED = "policy_denied"
    MALFORMED_RESPONSE = "malformed_response"
    METADATA_CONFLICT = "metadata_conflict"
    RESOURCE_NOT_FOUND = "resource_not_found"
    LICENSE_RESTRICTED = "license_restricted"
    PERMANENT_PROVIDER_ERROR = "permanent_provider_error"


class MetadataVerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONFLICT = "conflict"
    NOT_APPLICABLE = "not_applicable"


class RetractionStatus(StrEnum):
    UNKNOWN = "unknown"
    CLEAR = "clear"
    CORRECTED = "corrected"
    RETRACTED = "retracted"
    CONCERN = "expression_of_concern"


class RetrievalBudget(StrictModel):
    max_queries: int = Field(default=0, ge=0)
    max_results: int = Field(default=0, ge=0)
    max_download_bytes: int = Field(default=0, ge=0)
    max_cost: float = Field(default=0.0, ge=0)


class ContractRef(StrictModel):
    contract_type: Literal["scope", "research", "repair"]
    contract_id: str
    version: int | None = Field(default=None, ge=1)
    field: str | None = None


class RetrievalRequest(StrictModel):
    schema_version: int = 1
    request_id: str
    project_id: str
    study_id: str
    phase: RetrievalPhase
    step_instance_id: str
    purpose: str = Field(min_length=2, max_length=200)
    query_plan_id: str
    network_policy_id: str
    contract_refs: list[ContractRef] = Field(default_factory=list)
    requested_providers: list[str] = Field(default_factory=list)
    requested_resource_types: list[ResourceType]
    requested_usage_role: str = Field(min_length=2, max_length=100)
    budget: RetrievalBudget
    idempotency_key: str = Field(min_length=4, max_length=300)
    created_at: str = Field(default_factory=utc_now)


class QueryPlan(StrictModel):
    schema_version: int = 1
    query_plan_id: str
    project_id: str
    study_id: str
    phase: RetrievalPhase
    research_need: str
    purpose: str
    raw_query_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    queries: list[str]
    sanitized_queries: list[str] = Field(default_factory=list)
    providers: list[str]
    freshness: Literal["cache_only", "live"] = "cache_only"
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    require_search_execution: bool = True
    date_range: dict[str, str] = Field(default_factory=dict)
    resource_types: list[ResourceType]
    inclusion_rules: list[str] = Field(default_factory=list)
    exclusion_rules: list[str] = Field(default_factory=list)
    budget: RetrievalBudget
    generated_by: str = "deterministic_gateway"
    generator_version: str = "retrieval-gateway-v1"
    approval_status: Literal["not_required", "pending", "approved", "rejected"] = (
        "not_required"
    )
    created_at: str = Field(default_factory=utc_now)


class RedactionFinding(StrictModel):
    category: str
    query_index: int = Field(ge=0)
    replacement: str
    count: int = Field(ge=1)


class RedactionReport(StrictModel):
    report_id: str
    raw_query_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    findings: list[RedactionFinding] = Field(default_factory=list)
    safe_reference_only: Literal[True] = True
    created_at: str = Field(default_factory=utc_now)


class PolicyDecision(StrictModel):
    decision_id: str
    request_id: str
    outcome: PolicyOutcome
    policy_id: str
    profile_id: str
    reasons: list[str]
    allowed_providers: list[str] = Field(default_factory=list)
    effective_budget: RetrievalBudget
    created_at: str = Field(default_factory=utc_now)


class ProviderAttempt(StrictModel):
    provider: str
    attempt: int = Field(ge=1)
    status: RetrievalStatus
    error_classification: ProviderErrorClass | None = None
    response_artifact_ids: list[str] = Field(default_factory=list)
    result_count: int = Field(default=0, ge=0)
    started_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None


class BudgetUsage(StrictModel):
    queries: int = Field(default=0, ge=0)
    results: int = Field(default=0, ge=0)
    download_bytes: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0)


class RetrievalRun(StrictModel):
    schema_version: int = 1
    run_id: str
    request_id: str
    execution_status: RetrievalStatus = RetrievalStatus.QUEUED
    policy_decision_id: str | None = None
    readiness_snapshot_id: str | None = None
    provider_attempts: list[ProviderAttempt] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    error_classification: ProviderErrorClass | None = None
    retry_count: int = Field(default=0, ge=0)
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    output_artifact_ids: list[str] = Field(default_factory=list)
    coverage_report_id: str | None = None


class ExternalResource(StrictModel):
    schema_version: int = 1
    resource_id: str
    resource_type: ResourceType
    canonical_identifier: str
    title: str
    authors_or_owners: list[str] = Field(default_factory=list)
    publication_or_release_date: str | None = None
    doi: str | None = None
    url: str | None = None
    repository: str | None = None
    commit: str | None = None
    dataset_identifier: str | None = None
    model_identifier: str | None = None
    license: str | None = None
    providers: list[str]
    metadata_verification_status: MetadataVerificationStatus = (
        MetadataVerificationStatus.UNVERIFIED
    )
    retraction_or_correction_status: RetractionStatus = RetractionStatus.UNKNOWN
    canonical_metadata_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceSnapshot(StrictModel):
    schema_version: int = 1
    snapshot_id: str
    resource_id: str
    content_level: Literal[
        "metadata",
        "abstract",
        "full_text",
        "webpage",
        "repository_metadata",
        "source_archive",
        "dataset_metadata",
        "dataset_file",
        "model_file",
        "model_card",
        "dataset_card",
        "documentation",
        "official_documentation",
    ]
    raw_response_artifact_id: str
    normalized_content_artifact_id: str
    retrieved_at: str = Field(default_factory=utc_now)
    provider: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str
    byte_size: int = Field(ge=0)
    license_status: str
    access_status: str
    access_mode: Literal[
        "open_access",
        "user_upload",
        "institutional",
        "metadata_only",
        "link_only",
    ] = "metadata_only"
    model_processing_allowed: bool = False
    immutable: Literal[True] = True


class ResourceUseBinding(StrictModel):
    schema_version: int = 1
    binding_id: str
    resource_id: str
    snapshot_id: str
    project_id: str
    study_id: str
    phase: RetrievalPhase
    step_instance_id: str
    purpose: str
    usage_role: str
    target_type: str
    target_id: str
    target_field: str
    relation: Literal[
        "supports",
        "contradicts",
        "contextualizes",
        "justifies",
        "implements",
        "supplies",
        "warns",
    ]
    verification_status: MetadataVerificationStatus
    # Discovery sources can propose or contextualize a claim, but they cannot
    # become scientific verdict evidence merely by being retrieved.
    verdict_eligible: bool = False
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def discovery_cannot_authorize_verdicts(self) -> "ResourceUseBinding":
        if self.phase is RetrievalPhase.DISCOVERY and self.verdict_eligible:
            raise ValueError("Discovery bindings cannot be verdict eligible")
        return self


class ResourceSet(StrictModel):
    schema_version: int = 1
    resource_set_id: str
    study_id: str
    phase: RetrievalPhase
    version: int = Field(ge=1)
    status: ResourceSetStatus = ResourceSetStatus.DRAFT
    binding_ids: list[str]
    query_plan_id: str
    coverage_report_id: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    frozen_at: str | None = None
    supersedes_id: str | None = None

    @model_validator(mode="after")
    def frozen_has_time(self) -> "ResourceSet":
        if self.status is ResourceSetStatus.FROZEN and not self.frozen_at:
            raise ValueError("frozen ResourceSet requires frozen_at")
        return self


class RetrievalCoverageReport(StrictModel):
    schema_version: int = 1
    coverage_report_id: str
    run_id: str
    providers_used: list[str]
    query_purpose: str = ""
    executed_queries: list[str]
    date_range: dict[str, str] = Field(default_factory=dict)
    raw_result_count: int = Field(ge=0)
    deduplicated_result_count: int = Field(ge=0)
    verified_result_count: int = Field(ge=0)
    full_text_available_count: int = Field(default=0, ge=0)
    metadata_only_count: int = Field(default=0, ge=0)
    link_only_count: int = Field(default=0, ge=0)
    publication_count: int = Field(default=0, ge=0)
    github_repository_count: int = Field(default=0, ge=0)
    huggingface_model_count: int = Field(default=0, ge=0)
    huggingface_dataset_count: int = Field(default=0, ge=0)
    web_source_count: int = Field(default=0, ge=0)
    institutional_full_text_count: int = Field(default=0, ge=0)
    providers_not_used: list[str] = Field(default_factory=list)
    provider_failures: dict[str, str] = Field(default_factory=dict)
    access_blocks: list[str] = Field(default_factory=list)
    uncovered_databases: list[str] = Field(default_factory=list)
    license_constraints: list[str] = Field(default_factory=list)
    metadata_conflicts: list[str] = Field(default_factory=list)
    known_blind_spots: list[str] = Field(default_factory=list)
    retrieval_duration_ms: int = Field(default=0, ge=0)
    prohibited_claims: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class RetrievalArtifact(StrictModel):
    schema_version: int = 2
    artifact_id: str
    # Optional only for schema-v1 compatibility. Repository writes require it.
    project_id: str | None = None
    study_id: str
    step_instance_id: str
    kind: str
    path: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(default_factory=utc_now)
    producer: str
    input_artifact_ids: list[str] = Field(default_factory=list)
    policy_context: dict[str, Any] = Field(default_factory=dict)
    immutable_version: int = Field(default=1, ge=1)
    immutable: Literal[True] = True


class EvidenceConflict(StrictModel):
    schema_version: int = 1
    conflict_id: str
    study_id: str
    resource_id: str
    claim_id: str
    historical_verdict_id: str | None = None
    conflict_type: Literal[
        "retraction",
        "correction",
        "contradictory_evidence",
        "unsupported_citation",
        "metadata_conflict",
    ]
    status: Literal["open", "reviewed", "resolved"] = "open"
    repair_recommended: Literal[True] = True
    details: str
    created_at: str = Field(default_factory=utc_now)


class RetrievalAuditEvent(StrictModel):
    schema_version: int = 2
    event_id: str
    request_id: str
    # Optional only when reading schema-v1 audit rows.
    project_id: str | None = None
    study_id: str
    step_instance_id: str
    purpose: str
    phase: RetrievalPhase
    policy_decision_id: str | None = None
    provider: str | None = None
    target_domain: str | None = None
    method: str | None = None
    sanitized_query: str | None = None
    response_status: str
    retry_count: int = Field(default=0, ge=0)
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    created_at: str = Field(default_factory=utc_now)
