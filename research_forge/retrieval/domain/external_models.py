"""External Research V1 domain objects.

These objects deliberately separate a resource's canonical identity, each
provider observation, access rights, research use, and evidence analysis.
They extend the existing Retrieval Gateway without replacing its v1 records.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from ...models import StrictModel, utc_now
from .models import MetadataVerificationStatus, RetrievalPhase


class CapabilityState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    DEGRADED = "degraded"
    READY = "ready"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class ExternalCapability(StrEnum):
    ACADEMIC_SEARCH = "academic_search"
    GITHUB_RESEARCH = "github_research"
    HUGGINGFACE_RESEARCH = "huggingface_research"
    PUBLIC_WEB = "public_web"
    OPEN_ACCESS = "open_access"
    INSTITUTIONAL_ACCESS = "institutional_access"
    EVIDENCE_ANALYSIS = "evidence_analysis"
    EXTERNAL_RESEARCH_V1 = "external_research_v1"


class ProviderCredentialMode(StrEnum):
    NONE_REQUIRED = "none_required"
    SERVER_MANAGED = "server_managed"
    OPTIONAL_USER_OAUTH = "optional_user_oauth"
    INSTITUTION_SESSION = "institution_session"
    UNAVAILABLE = "unavailable"


class SearchFreshness(StrEnum):
    CACHE_ONLY = "cache_only"
    LIVE = "live"


class CapabilityReadiness(StrictModel):
    capability: ExternalCapability
    state: CapabilityState
    credential_mode: ProviderCredentialMode
    provider_ids: list[str] = Field(default_factory=list)
    configured: bool
    health_verified: bool = False
    tests_verified: bool = False
    migration_ready: bool = False
    reasons: list[str] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def ready_requires_evidence(self) -> "CapabilityReadiness":
        if self.state is CapabilityState.READY and not (
            self.configured
            and self.health_verified
            and self.tests_verified
            and self.migration_ready
        ):
            raise ValueError(
                "READY requires configuration, health, tests, and migration"
            )
        return self


class ReadinessReport(StrictModel):
    schema_version: int = 1
    report_id: str
    capabilities: list[CapabilityReadiness]
    external_research_v1_ready: bool
    public_research_loop_ready: bool | None = None
    generated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def aggregate_is_computed(self) -> "ReadinessReport":
        component_states = {
            item.capability: item.state
            for item in self.capabilities
            if item.capability is not ExternalCapability.EXTERNAL_RESEARCH_V1
        }
        expected = all(
            component_states.get(capability) is CapabilityState.READY
            for capability in (
                ExternalCapability.ACADEMIC_SEARCH,
                ExternalCapability.GITHUB_RESEARCH,
                ExternalCapability.HUGGINGFACE_RESEARCH,
                ExternalCapability.PUBLIC_WEB,
                ExternalCapability.OPEN_ACCESS,
                ExternalCapability.INSTITUTIONAL_ACCESS,
                ExternalCapability.EVIDENCE_ANALYSIS,
            )
        )
        if self.external_research_v1_ready != expected:
            raise ValueError(
                "external_research_v1_ready must be computed from components"
            )
        public_expected = all(
            component_states.get(capability) is CapabilityState.READY
            for capability in (
                ExternalCapability.ACADEMIC_SEARCH,
                ExternalCapability.GITHUB_RESEARCH,
                ExternalCapability.HUGGINGFACE_RESEARCH,
                ExternalCapability.PUBLIC_WEB,
                ExternalCapability.OPEN_ACCESS,
                ExternalCapability.EVIDENCE_ANALYSIS,
            )
        )
        if self.public_research_loop_ready is None:
            object.__setattr__(
                self,
                "public_research_loop_ready",
                public_expected,
            )
        elif self.public_research_loop_ready != public_expected:
            raise ValueError(
                "public_research_loop_ready must be computed from public components"
            )
        return self


class CanonicalResourceType(StrEnum):
    PUBLICATION = "publication"
    PREPRINT = "preprint"
    CODE_REPOSITORY = "code_repository"
    CODE_RELEASE = "code_release"
    ISSUE = "issue"
    DATASET = "dataset"
    MODEL = "model"
    SPACE = "space"
    BENCHMARK = "benchmark"
    STANDARD = "standard"
    DOCUMENTATION = "documentation"
    WEB_SOURCE = "web_source"
    PUBLISHER_NOTICE = "publisher_notice"
    INSTITUTIONAL_DOCUMENT = "institutional_document"


class IdentifierKind(StrEnum):
    DOI = "doi"
    PMID = "pmid"
    PMCID = "pmcid"
    ARXIV = "arxiv"
    OPENALEX = "openalex"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    CROSSREF = "crossref"
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_COMMIT = "github_commit"
    HUGGINGFACE_REPOSITORY = "huggingface_repository"
    HUGGINGFACE_REVISION = "huggingface_revision"
    URL = "url"
    CONTENT_HASH = "content_hash"


class ResourceIdentifier(StrictModel):
    kind: IdentifierKind
    value: str
    canonical: bool = False
    verified: bool = False


class ProviderRecord(StrictModel):
    provider_record_id: str
    canonical_resource_id: str
    provider: str
    provider_resource_id: str
    raw_response_artifact_id: str
    normalized_metadata: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: str = Field(default_factory=utc_now)
    response_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_specific_score: float | None = None
    provider_warnings: list[str] = Field(default_factory=list)


class CanonicalResource(StrictModel):
    schema_version: int = 1
    canonical_resource_id: str
    resource_type: CanonicalResourceType
    title: str
    authors_or_owners: list[str] = Field(default_factory=list)
    identifiers: list[ResourceIdentifier] = Field(default_factory=list)
    canonical_url: str | None = None
    publication_or_release_date: str | None = None
    provider_record_ids: list[str] = Field(default_factory=list)
    version: str | None = None
    commit_or_revision: str | None = None
    license: str | None = None
    access_status: Literal[
        "open_access",
        "metadata_only",
        "abstract_only",
        "link_only",
        "access_blocked",
        "gated",
        "private",
    ] = "metadata_only"
    metadata_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    verification_status: MetadataVerificationStatus = (
        MetadataVerificationStatus.UNVERIFIED
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class IdentifierGraphEdge(StrictModel):
    left: ResourceIdentifier
    right: ResourceIdentifier
    verification: Literal[
        "verified_official",
        "verified_cross_provider",
        "strongly_inferred",
        "weakly_inferred",
        "user_confirmed",
        "rejected",
    ]
    basis: str


class IdentifierGraph(StrictModel):
    graph_id: str
    study_id: str
    resource_ids: list[str]
    edges: list[IdentifierGraphEdge] = Field(default_factory=list)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(default_factory=utc_now)


class ResourceRelationType(StrEnum):
    IMPLEMENTS = "implements"
    HAS_OFFICIAL_CODE = "has_official_code"
    USES_DATASET = "uses_dataset"
    RELEASES_MODEL = "releases_model"
    HAS_DEMO = "has_demo"
    TRAINED_ON = "trained_on"
    BASED_ON = "based_on"
    EVALUATES = "evaluates"
    SUPERSEDES = "supersedes"
    AFFECTS = "affects"
    DOCUMENTS = "documents"
    CITES = "cites"


class RelationVerification(StrEnum):
    VERIFIED_OFFICIAL = "verified_official"
    VERIFIED_BIDIRECTIONAL = "verified_bidirectional"
    STRONGLY_INFERRED = "strongly_inferred"
    WEAKLY_INFERRED = "weakly_inferred"
    USER_CONFIRMED = "user_confirmed"
    REJECTED = "rejected"


class ResourceRelation(StrictModel):
    relation_id: str
    source_resource_id: str
    target_resource_id: str
    relation: ResourceRelationType
    verification_status: RelationVerification
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class AccessMode(StrEnum):
    OPEN_ACCESS = "open_access"
    USER_UPLOAD = "user_upload"
    INSTITUTIONAL = "institutional"
    METADATA_ONLY = "metadata_only"
    LINK_ONLY = "link_only"


class AccessDecision(StrictModel):
    decision_id: str
    resource_id: str
    access_mode: AccessMode
    full_text_available: bool
    persistent_storage_allowed: bool
    model_processing_allowed: bool
    cross_user_cache_allowed: Literal[False] = False
    redistribution_allowed: bool = False
    training_allowed: bool = False
    decision_basis: str
    decided_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def institutional_is_private(self) -> "AccessDecision":
        if self.access_mode is AccessMode.INSTITUTIONAL and (
            self.cross_user_cache_allowed or self.redistribution_allowed
        ):
            raise ValueError("institutional content cannot be shared or redistributed")
        return self


class EvidenceSpan(StrictModel):
    resource_id: str
    snapshot_id: str
    page: int | None = Field(default=None, ge=0)
    section: str | None = None
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    support_relation: Literal["supports", "contradicts", "contextualizes"]
    quote_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def offsets_are_ordered(self) -> "EvidenceSpan":
        if self.end_offset < self.start_offset:
            raise ValueError("evidence span end precedes start")
        return self


class EvidenceResult(StrictModel):
    result_id: str
    study_id: str
    corpus_id: str
    question: str
    answer: str
    evidence_spans: list[EvidenceSpan]
    limitations: list[str] = Field(default_factory=list)
    answerability: Literal[
        "answerable", "partially_answerable", "unanswerable"
    ] = "answerable"
    conflicts: list[str] = Field(default_factory=list)
    unsupported_statements: list[str] = Field(default_factory=list)
    abstention_reason: str | None = None
    cache_key: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    normalized_question_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    evidence_bundle_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    corpus_manifest_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    synthesis_prompt_version: str | None = None
    output_schema_version: str | None = None
    policy_version: str | None = None
    latency_breakdown: dict[str, int | None] = Field(default_factory=dict)
    model_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict_authority: Literal[False] = False
    created_at: str = Field(default_factory=utc_now)


class CorpusStatus(StrEnum):
    DRAFT = "draft"
    INDEXING = "indexing"
    READY = "ready"
    INVALIDATED = "invalidated"
    BLOCKED = "blocked"


class CorpusDocument(StrictModel):
    resource_id: str
    snapshot_id: str
    snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    access_decision_id: str
    binding_id: str


class CorpusManifest(StrictModel):
    corpus_id: str
    study_id: str
    phase: RetrievalPhase
    status: CorpusStatus = CorpusStatus.DRAFT
    documents: list[CorpusDocument] = Field(default_factory=list)
    parser_version: str
    embedding_model_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    llm_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
