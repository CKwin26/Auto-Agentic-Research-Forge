from __future__ import annotations

from pydantic import Field

from ...models import StrictModel
from ..domain.models import NetworkMode, ResourceType, RetrievalPhase


class StageProfile(StrictModel):
    profile_id: str
    phase: RetrievalPhase
    allowed_purposes: set[str]
    allowed_modes: set[NetworkMode]
    allowed_resource_types: set[ResourceType]
    default_usage_roles: set[str]
    decisive_verdict_evidence_allowed: bool = False
    requires_contract_binding: bool = False
    requires_failure_binding: bool = False
    prohibited_open_search: bool = False
    notes: list[str] = Field(default_factory=list)


STAGE_PROFILES: dict[RetrievalPhase, StageProfile] = {
    RetrievalPhase.DISCOVERY: StageProfile(
        profile_id="discovery-profile-v1",
        phase=RetrievalPhase.DISCOVERY,
        allowed_purposes={
            "novelty_check",
            "related_work_search",
            "negative_result_search",
            "discovery_signal",
            "dataset_discovery",
            "research_gap_discovery",
            "closest_prior_work",
        },
        allowed_modes={
            NetworkMode.OFFLINE,
            NetworkMode.ACADEMIC_READ,
            NetworkMode.PUBLIC_WEB_READ,
            NetworkMode.AUTHENTICATED_READ,
            NetworkMode.PUBLIC_RESEARCH,
            NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION,
        },
        allowed_resource_types={
            ResourceType.PUBLICATION,
            ResourceType.DATASET,
            ResourceType.MODEL,
            ResourceType.CODE_REPOSITORY,
            ResourceType.WEB_SOURCE,
            ResourceType.PREPRINT,
            ResourceType.CODE_RELEASE,
            ResourceType.ISSUE,
            ResourceType.SPACE,
            ResourceType.BENCHMARK,
            ResourceType.WEB_SOURCE,
        },
        default_usage_roles={
            "discovery_signal",
            "background_source",
            "novelty_grounding",
            "feasibility_signal",
        },
        notes=["Discovery bindings cannot directly decide an idea verdict."],
    ),
    RetrievalPhase.PROTOCOL: StageProfile(
        profile_id="protocol-profile-v1",
        phase=RetrievalPhase.PROTOCOL,
        allowed_purposes={
            "metric_grounding",
            "baseline_discovery",
            "dataset_version_check",
            "evaluation_method_search",
            "protocol_grounding",
            "implementation_reference",
            "statistical_method_grounding",
            "known_failure_mode_search",
        },
        allowed_modes={
            NetworkMode.OFFLINE,
            NetworkMode.ACADEMIC_READ,
            NetworkMode.PUBLIC_WEB_READ,
            NetworkMode.PUBLIC_RESEARCH,
            NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION,
        },
        allowed_resource_types={
            ResourceType.PUBLICATION,
            ResourceType.DATASET,
            ResourceType.CODE_REPOSITORY,
            ResourceType.STANDARD,
            ResourceType.DOCUMENTATION,
            ResourceType.PREPRINT,
            ResourceType.CODE_RELEASE,
            ResourceType.BENCHMARK,
        },
        default_usage_roles={"protocol_grounding", "contract_field_justification"},
        requires_contract_binding=True,
    ),
    RetrievalPhase.EXPERIMENTATION: StageProfile(
        profile_id="experimentation-profile-v1",
        phase=RetrievalPhase.EXPERIMENTATION,
        allowed_purposes={
            "fetch_approved_dataset",
            "fetch_approved_model",
            "fetch_pinned_code_revision",
            "fetch_official_documentation",
            "dependency_diagnosis",
            "runtime_error_diagnosis",
            "repair_diagnosis",
        },
        allowed_modes={
            NetworkMode.OFFLINE,
            NetworkMode.ACADEMIC_READ,
            NetworkMode.PUBLIC_WEB_READ,
            NetworkMode.AUTHENTICATED_READ,
            NetworkMode.PUBLIC_RESEARCH,
            NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION,
        },
        allowed_resource_types={
            ResourceType.DATASET,
            ResourceType.CODE_REPOSITORY,
            ResourceType.MODEL,
            ResourceType.DOCUMENTATION,
            ResourceType.CODE_RELEASE,
            ResourceType.SPACE,
        },
        default_usage_roles={"approved_experiment_resource", "runtime_diagnosis"},
        requires_contract_binding=True,
        prohibited_open_search=True,
        notes=["Open-ended novelty and result-guided searches are prohibited."],
    ),
    RetrievalPhase.SYNTHESIS: StageProfile(
        profile_id="synthesis-profile-v1",
        phase=RetrievalPhase.SYNTHESIS,
        allowed_purposes={
            "citation_verification",
            "doi_resolution",
            "metadata_verification",
            "retraction_check",
            "correction_check",
            "reference_completion",
            "submission_guideline_lookup",
            "journal_format_lookup",
            "background_literature_update",
        },
        allowed_modes={
            NetworkMode.OFFLINE,
            NetworkMode.ACADEMIC_READ,
            NetworkMode.PUBLIC_WEB_READ,
            NetworkMode.PUBLIC_RESEARCH,
            NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION,
        },
        allowed_resource_types={
            ResourceType.PUBLICATION,
            ResourceType.RETRACTION_NOTICE,
            ResourceType.SUBMISSION_RULE,
            ResourceType.DOCUMENTATION,
            ResourceType.PREPRINT,
            ResourceType.PUBLISHER_NOTICE,
            ResourceType.INSTITUTIONAL_DOCUMENT,
            ResourceType.WEB_SOURCE,
        },
        default_usage_roles={"citation_source", "submission_requirement"},
        notes=[
            "Conflicting evidence creates evidence_conflict; verdicts are immutable."
        ],
    ),
    RetrievalPhase.REPAIR: StageProfile(
        profile_id="repair-profile-v1",
        phase=RetrievalPhase.REPAIR,
        allowed_purposes={
            "diagnose_methodological_failure",
            "diagnose_evidence_binding_failure",
            "diagnose_literature_grounding_failure",
            "diagnose_runtime_failure",
            "find_official_fix",
            "verify_known_issue",
        },
        allowed_modes={
            NetworkMode.OFFLINE,
            NetworkMode.ACADEMIC_READ,
            NetworkMode.PUBLIC_WEB_READ,
            NetworkMode.AUTHENTICATED_READ,
            NetworkMode.PUBLIC_RESEARCH,
            NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION,
        },
        allowed_resource_types=set(ResourceType),
        default_usage_roles={"diagnostic_source", "repair_justification"},
        requires_failure_binding=True,
    ),
}
