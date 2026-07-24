from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal, Protocol

from ...workflow_domain import ArtifactRole, ArtifactStatus
from ..domain.external_models import CorpusDocument
from ..domain.models import (
    ContractRef,
    NetworkMode,
    ResourceSetStatus,
    ResourceType,
    RetrievalBudget,
    RetrievalPhase,
)
from ..interfaces.service import RetrievalGateway
from ..evidence import PaperQAEvidenceService
from ..evidence.paperqa_runtime import synthesis_model_config_hash
from ..institution import InstitutionSessionBroker, InstitutionSessionStatus
from ..pipelines.provenance import build_identifier_graph as assemble_identifier_graph


class WorkflowStepContext(Protocol):
    repository: Any
    study_id: str
    step: Any

    def result(self, step_type: str) -> dict[str, Any]: ...


def _gateway(context: WorkflowStepContext) -> RetrievalGateway:
    return RetrievalGateway(str(context.repository.root))


def _freshness_for_policy(
    policy: Any,
) -> Literal["cache_only", "live"]:
    """Offline workflows may only reuse frozen results; approved network modes go live."""
    return "cache_only" if policy.mode is NetworkMode.OFFLINE else "live"


def _group_steps(context: WorkflowStepContext) -> dict[str, Any]:
    return {
        item.step_type: item
        for item in context.repository.list_steps(context.study_id)
        if item.task_group == context.step.task_group
    }


def _discovery_queries(context: WorkflowStepContext) -> list[str]:
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    scope = context.repository.latest_scope_contract(context.study_id)
    values = [
        scope.research_question if scope else "",
        scope.direction if scope else "",
        study.title,
        project.title,
        "auditable scientific evidence autonomous research agents",
    ]
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))[:8]


def plan_external_research(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    policy = gateway.get_policy(project.project_id)
    steps = _group_steps(context)
    queries = _discovery_queries(context)
    groups: dict[
        str,
        tuple[
            str,
            list[str],
            list[ResourceType],
            str,
            str,
        ],
    ] = {
        "academic": (
            "execute_academic_search",
            [
                item
                for item in (
                    "paper_search_mcp",
                    "semantic_scholar",
                    "crossref",
                )
                if item in policy.allowed_providers
            ],
            [ResourceType.PUBLICATION, ResourceType.PREPRINT],
            "closest_prior_work",
            "novelty_grounding",
        ),
        "github": (
            "execute_github_search",
            ["github"] if "github" in policy.allowed_providers else [],
            [ResourceType.CODE_REPOSITORY],
            "related_work_search",
            "feasibility_signal",
        ),
        "huggingface": (
            "execute_huggingface_search",
            ["huggingface"] if "huggingface" in policy.allowed_providers else [],
            [ResourceType.MODEL, ResourceType.DATASET, ResourceType.SPACE],
            "dataset_discovery",
            "feasibility_signal",
        ),
        "official_web": (
            "execute_official_web_search",
            (
                ["codex_native_web_search"]
                if "codex_native_web_search" in policy.allowed_providers
                else []
            ),
            [ResourceType.WEB_SOURCE],
            "related_work_search",
            "background_source",
        ),
    }
    requests: dict[str, dict[str, Any]] = {}
    for group, (
        step_type,
        providers,
        resource_types,
        purpose,
        usage_role,
    ) in groups.items():
        if not providers:
            requests[group] = {
                "status": "not_authorized",
                "providers": [],
                "request_id": None,
            }
            continue
        request = gateway.plan(
            project_id=project.project_id,
            study_id=context.study_id,
            phase=RetrievalPhase.DISCOVERY,
            step_instance_id=steps[step_type].step_instance_id,
            purpose=purpose,
            queries=queries,
            providers=providers,
            resource_types=resource_types,
            usage_role=usage_role,
            budget=RetrievalBudget(
                max_queries=min(8, max(1, policy.max_queries or 8)),
                max_results=min(120, max(1, policy.max_results or 120)),
                max_download_bytes=min(
                    5_000_000,
                    max(1, policy.max_bytes or 5_000_000),
                ),
                max_cost=min(1.0, max(0.0, policy.max_cost)),
            ),
            idempotency_key=(f"{context.study_id}:{context.step.task_group}:{group}"),
            internal_identifiers=[
                project.title,
                Path(project.source_root).name if project.source_root else "",
            ],
            research_need=(
                "Find bounded prior work and implementation resources for the "
                "current Study."
            ),
            freshness=_freshness_for_policy(policy),
            blocked_domains=["scholar.google.com"],
        )
        requests[group] = {
            "status": "planned",
            "providers": providers,
            "request_id": request.request_id,
            "query_plan_id": request.query_plan_id,
        }
    return {
        "policy_id": policy.policy_id,
        "network_mode": policy.mode.value,
        "requests": requests,
    }


def evaluate_retrieval_policy(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    planned = context.result("plan_external_research")
    decisions: dict[str, Any] = {}
    for group, item in planned["requests"].items():
        request_id = item.get("request_id")
        if not request_id:
            decisions[group] = {
                "outcome": "deny",
                "reasons": ["provider group is not authorized by Project policy"],
            }
            continue
        request = gateway.repository.load_request(request_id)
        plan = gateway.repository.load_query_plan(request.query_plan_id)
        policy = gateway.repository.load_network_policy(request.network_policy_id)
        decisions[group] = gateway.policy_engine.evaluate(
            request,
            policy,
            sanitized=bool(plan.sanitized_queries),
        ).model_dump(mode="json")
    return {"decisions": decisions}


def sanitize_queries(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    planned = context.result("plan_external_research")
    plans = {}
    for group, item in planned["requests"].items():
        request_id = item.get("request_id")
        if not request_id:
            continue
        request = gateway.repository.load_request(request_id)
        plan = gateway.repository.load_query_plan(request.query_plan_id)
        plans[group] = {
            "query_plan_id": plan.query_plan_id,
            "raw_query_digest": plan.raw_query_digest,
            "sanitized_queries": plan.sanitized_queries,
        }
    return {"plans": plans}


def _execute_group(context: WorkflowStepContext, group: str) -> dict[str, Any]:
    planned = context.result("plan_external_research")
    item = planned["requests"][group]
    request_id = item.get("request_id")
    if not request_id:
        return {
            "status": "not_applicable",
            "reason": "provider group is not authorized by Project policy",
            "provider_group": group,
        }
    gateway = _gateway(context)
    execution = gateway.run(request_id)
    workflow_artifact_ids: list[str] = []
    for retrieval_artifact_id in execution.run.output_artifact_ids:
        artifact = gateway.repository.load_artifact(retrieval_artifact_id)
        registered = context.repository.register_artifact(
            context.study_id,
            artifact.path,
            artifact.content_hash,
            kind=f"retrieval_{artifact.kind}",
            role=(
                ArtifactRole.LITERATURE_BACKGROUND
                if artifact.kind
                in {
                    "normalized_resources",
                    "canonical_resources",
                    "resource_set",
                    "identifier_graph",
                    "resource_relation_graph",
                }
                else ArtifactRole.AUDIT
            ),
        )
        workflow_artifact_ids.append(registered.artifact_id)
    return {
        **execution.model_dump(),
        "provider_group": group,
        "_workflow_output_artifact_ids": workflow_artifact_ids,
    }


def execute_academic_search(context: WorkflowStepContext) -> dict[str, Any]:
    return _execute_group(context, "academic")


def execute_github_search(context: WorkflowStepContext) -> dict[str, Any]:
    return _execute_group(context, "github")


def execute_huggingface_search(context: WorkflowStepContext) -> dict[str, Any]:
    return _execute_group(context, "huggingface")


def execute_official_web_search(context: WorkflowStepContext) -> dict[str, Any]:
    return _execute_group(context, "official_web")


_SEARCH_STEPS = (
    "execute_academic_search",
    "execute_github_search",
    "execute_huggingface_search",
    "execute_official_web_search",
)


def normalize_resources(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    step_ids = {
        _group_steps(context)[step_type].step_instance_id for step_type in _SEARCH_STEPS
    }
    bindings = [
        item
        for item in gateway.repository.list_bindings(context.study_id)
        if item.phase is RetrievalPhase.DISCOVERY and item.step_instance_id in step_ids
    ]
    resource_ids = sorted({item.resource_id for item in bindings})
    canonical = [
        item
        for item in gateway.repository.list_canonical_resources()
        if any(
            str(record.normalized_metadata.get("resource_id")) in resource_ids
            for record in gateway.repository.list_provider_records(
                item.canonical_resource_id
            )
        )
    ]
    return {
        "resource_ids": resource_ids,
        "canonical_resource_ids": [item.canonical_resource_id for item in canonical],
        "binding_ids": sorted(item.binding_id for item in bindings),
    }


def build_identifier_graph(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    normalized = context.result("normalize_resources")
    canonical = [
        gateway.repository.load_canonical_resource(item)
        for item in normalized["canonical_resource_ids"]
    ]
    graph = assemble_identifier_graph(context.study_id, canonical)
    gateway.repository.save_identifier_graph(graph)
    artifact = gateway.repository.write_artifact(
        project_id=context.repository.load_study(context.study_id).project_id,
        study_id=context.study_id,
        step_instance_id=context.step.step_instance_id,
        kind="identifier_graph",
        value=graph.model_dump(mode="json"),
        producer="identifier_graph_builder",
    )
    return {
        "graph": graph.model_dump(mode="json"),
        "artifact_id": artifact.artifact_id,
    }


def deduplicate_resources(context: WorkflowStepContext) -> dict[str, Any]:
    normalized = context.result("normalize_resources")
    return {
        **normalized,
        "input_count": len(normalized["binding_ids"]),
        "deduplicated_count": len(normalized["resource_ids"]),
    }


def verify_metadata(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    payload = context.result("deduplicate_resources")
    resources = [
        gateway.repository.load_resource(item) for item in payload["resource_ids"]
    ]
    return {
        **payload,
        "verified_count": sum(
            item.metadata_verification_status.value == "verified" for item in resources
        ),
        "metadata_conflicts": [
            item.resource_id
            for item in resources
            if item.metadata_verification_status.value == "conflict"
        ],
    }


def build_resource_relation_graph(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    gateway = _gateway(context)
    canonical_ids = set(context.result("normalize_resources")["canonical_resource_ids"])
    relations = [
        item
        for item in gateway.repository.list_resource_relations()
        if item.source_resource_id in canonical_ids
        and item.target_resource_id in canonical_ids
    ]
    artifact = gateway.repository.write_artifact(
        project_id=context.repository.load_study(context.study_id).project_id,
        study_id=context.study_id,
        step_instance_id=context.step.step_instance_id,
        kind="resource_relation_graph",
        value={
            "study_id": context.study_id,
            "relations": [item.model_dump(mode="json") for item in relations],
        },
        producer="resource_relation_graph_builder",
    )
    return {
        "relation_count": len(relations),
        "artifact_id": artifact.artifact_id,
    }


def resolve_open_access(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    policy = gateway.get_policy(project.project_id)
    if "open_access" not in policy.allowed_providers:
        return {
            "status": "not_configured",
            "reason": "open_access provider is not authorized",
        }
    resources = [
        gateway.repository.load_resource(item)
        for item in context.result("verify_metadata")["resource_ids"]
    ]
    dois = sorted({item.doi for item in resources if item.doi})
    if not dois:
        return {"status": "not_applicable", "reason": "no verified DOI was found"}
    request = gateway.plan(
        project_id=project.project_id,
        study_id=context.study_id,
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id=context.step.step_instance_id,
        purpose="related_work_search",
        queries=dois,
        providers=["open_access"],
        resource_types=[ResourceType.PUBLICATION, ResourceType.PREPRINT],
        usage_role="background_source",
        budget=RetrievalBudget(
            max_queries=len(dois),
            max_results=len(dois),
            max_download_bytes=policy.max_bytes,
        ),
        idempotency_key=(f"{context.study_id}:{context.step.task_group}:open-access"),
        research_need="Resolve legal open-access locations for known DOI records.",
        freshness=_freshness_for_policy(policy),
    )
    return gateway.run(request.request_id).model_dump()


def optionally_request_institution_access(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    gateway = _gateway(context)
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    policy = gateway.get_policy(project.project_id)
    if policy.mode is not NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION:
        return {
            "status": "not_applicable",
            "reason": "Project policy does not enable institutional access",
        }
    sessions = InstitutionSessionBroker(context.repository.root).list_for_study(
        context.study_id
    )
    active = [
        item for item in sessions if item.status is InstitutionSessionStatus.ACTIVE
    ]
    if active:
        return {
            "status": "active",
            "session_ids": [item.session_id for item in active],
            "credential_capture": False,
        }
    expired = [
        item.session_id
        for item in sessions
        if item.status is InstitutionSessionStatus.EXPIRED
    ]
    return {
        "status": "waiting_for_user",
        "reason": (
            "User must create a local, user-bound institution session and "
            "authenticate on the institution's official page."
        ),
        "credential_capture": False,
        "expired_session_ids": expired,
    }


def acquire_approved_documents(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    resolution = context.result("resolve_open_access")
    acquired = []
    failures: dict[str, str] = {}
    seen_resources: set[str] = set()
    source_runs = [
        resolution,
        *[context.result(step_type) for step_type in _SEARCH_STEPS],
    ]
    for source_run in source_runs:
        resource_set = source_run.get("resource_set")
        request = source_run.get("request")
        if not isinstance(resource_set, dict) or not isinstance(request, dict):
            continue
        for binding_id in resource_set.get("binding_ids") or []:
            binding = next(
                (
                    item
                    for item in gateway.repository.list_bindings(context.study_id)
                    if item.binding_id == binding_id
                ),
                None,
            )
            if binding is None:
                continue
            resource = gateway.repository.load_resource(binding.resource_id)
            if (
                resource.resource_id in seen_resources
                or resource.metadata.get("access_status") != "open_access"
            ):
                continue
            seen_resources.add(resource.resource_id)
            try:
                execution = gateway.acquire_open_access_document(
                    request_id=str(request["request_id"]),
                    resource_id=resource.resource_id,
                )
            except Exception as exc:
                failures[resource.resource_id] = (
                    exc.classification.value
                    if hasattr(exc, "classification")
                    else type(exc).__name__
                )
                continue
            acquired.append(execution)
    institutional = context.result("optionally_request_institution_access")
    institutional_snapshots = []
    if institutional["status"] == "active":
        bound_resources = {
            item.resource_id
            for item in gateway.repository.list_bindings(context.study_id)
        }
        institutional_snapshots = [
            item
            for item in gateway.repository.list_snapshots()
            if item.resource_id in bound_resources
            and item.provider == "institutional_access"
            and item.content_level == "full_text"
        ]
    if acquired or institutional_snapshots:
        all_snapshots = [
            *[item.snapshot for item in acquired],
            *institutional_snapshots,
        ]
        return {
            "status": "acquired",
            "snapshot_ids": [item.snapshot_id for item in all_snapshots],
            "access_decision_ids": [
                item.access_decision.decision_id for item in acquired
            ],
            "model_processing_snapshot_ids": [
                item.snapshot_id
                for item in all_snapshots
                if item.model_processing_allowed
            ],
            "acquisition_report_artifact_ids": [
                item.report_artifact_id for item in acquired
            ],
            "failures": failures,
        }
    return {
        "status": "degraded" if failures else "not_applicable",
        "reason": (
            "No rights-approved open-access full text was acquired."
            if institutional["status"] != "waiting_for_user"
            else "Institutional user handoff has not produced an authorized document."
        ),
        "snapshot_ids": [],
        "model_processing_snapshot_ids": [],
        "failures": failures,
    }


def build_paperqa_corpus(context: WorkflowStepContext) -> dict[str, Any]:
    acquisition = context.result("acquire_approved_documents")
    snapshot_ids = acquisition.get("model_processing_snapshot_ids") or []
    if not snapshot_ids:
        return {
            "status": "not_applicable",
            "reason": (
                "no full-text snapshots are explicitly authorized for model processing"
            ),
        }
    gateway = _gateway(context)
    documents = []
    for snapshot_id in snapshot_ids:
        snapshot = gateway.repository.load_snapshot(snapshot_id)
        decisions = [
            item
            for item in gateway.repository.list_access_decisions(snapshot.resource_id)
            if item.full_text_available and item.model_processing_allowed
        ]
        bindings = [
            item
            for item in gateway.repository.list_bindings(context.study_id)
            if item.resource_id == snapshot.resource_id
        ]
        if not decisions or not bindings:
            continue
        documents.append(
            CorpusDocument(
                resource_id=snapshot.resource_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_hash=snapshot.content_hash,
                access_decision_id=decisions[-1].decision_id,
                binding_id=bindings[-1].binding_id,
            )
        )
    if not documents:
        return {
            "status": "not_applicable",
            "reason": "rights-approved snapshots have no valid Study binding",
        }
    service = PaperQAEvidenceService(gateway.repository)
    try:
        corpus = service.build_corpus(
            study_id=context.study_id,
            phase=RetrievalPhase.DISCOVERY,
            documents=documents,
            parser_version="paperqa-2026.3.18",
            embedding_model_hash=hashlib.sha256(b"paperqa-sparse").hexdigest(),
            llm_config_hash=synthesis_model_config_hash(),
        )
        corpus = service.index(corpus.corpus_id)
    except RuntimeError as exc:
        _blocked(str(exc), kind="paperqa_runtime_unavailable")
    return {
        "status": "ready",
        "corpus": corpus.model_dump(mode="json"),
        "document_count": len(documents),
    }


def analyze_discovery_evidence(context: WorkflowStepContext) -> dict[str, Any]:
    corpus = context.result("build_paperqa_corpus")
    if corpus["status"] == "ready":
        gateway = _gateway(context)
        service = PaperQAEvidenceService(gateway.repository)
        queries = _discovery_queries(context)
        question = (
            "Across the frozen sources, what findings, limitations, and closest "
            "prior work are relevant to this bounded research direction: "
            + "; ".join(queries)
        )
        try:
            result = service.ask(corpus["corpus"]["corpus_id"], question)
        except ValueError as exc:
            return {
                "status": "inconclusive",
                "verdict_authority": False,
                "evidence_results": [],
                "reason": str(exc),
            }
        return {
            "status": "analyzed",
            "verdict_authority": False,
            "evidence_results": [result.model_dump(mode="json")],
            "reason": (
                "PaperQA evidence is a scoped discovery aid and cannot decide "
                "the Study verdict."
            ),
        }
    return {
        "status": (
            "not_applicable" if corpus["status"] == "not_applicable" else "blocked"
        ),
        "verdict_authority": False,
        "evidence_results": [],
        "reason": corpus["reason"],
    }


def generate_coverage_report(context: WorkflowStepContext) -> dict[str, Any]:
    runs = [context.result(step_type) for step_type in _SEARCH_STEPS]
    coverage = [
        item["coverage"] for item in runs if isinstance(item.get("coverage"), dict)
    ]
    return {
        "provider_runs": len(runs),
        "providers_used": sorted(
            {
                provider
                for item in coverage
                for provider in item.get("providers_used", [])
            }
        ),
        "provider_failures": {
            provider: reason
            for item in coverage
            for provider, reason in item.get("provider_failures", {}).items()
        },
        "raw_result_count": sum(
            int(item.get("raw_result_count", 0)) for item in coverage
        ),
        "deduplicated_result_count": len(
            context.result("normalize_resources")["resource_ids"]
        ),
        "verified_result_count": context.result("verify_metadata")["verified_count"],
        "known_blind_spots": sorted(
            {
                warning
                for item in coverage
                for warning in item.get("known_blind_spots", [])
            }
        ),
        "prohibited_claims": [
            "Do not claim exhaustive web or literature coverage.",
            "Do not treat discovery signals as verdict evidence.",
        ],
    }


def freeze_discovery_resource_set(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    gateway = _gateway(context)
    group_step_ids = {item.step_instance_id for item in _group_steps(context).values()}
    request_ids = {
        item.request_id
        for item in gateway.repository.list_runs(context.study_id)
        if gateway.repository.load_request(item.request_id).step_instance_id
        in group_step_ids
    }
    sets = [
        item
        for item in gateway.repository.list_resource_sets(context.study_id)
        if item.query_plan_id
        in {
            gateway.repository.load_request(request_id).query_plan_id
            for request_id in request_ids
        }
    ]
    frozen = [
        gateway.freeze_resource_set(item.resource_set_id)
        if item.status is not ResourceSetStatus.FROZEN
        else item
        for item in sets
    ]
    return {
        "status": "frozen" if frozen else "not_applicable",
        "resource_sets": [item.model_dump(mode="json") for item in frozen],
    }


def exact_discovery_handlers() -> dict[str, Any]:
    return {
        "plan_external_research": plan_external_research,
        "evaluate_retrieval_policy": evaluate_retrieval_policy,
        "sanitize_queries": sanitize_queries,
        "execute_academic_search": execute_academic_search,
        "execute_github_search": execute_github_search,
        "execute_huggingface_search": execute_huggingface_search,
        "execute_official_web_search": execute_official_web_search,
        "normalize_resources": normalize_resources,
        "build_identifier_graph": build_identifier_graph,
        "deduplicate_resources": deduplicate_resources,
        "verify_metadata": verify_metadata,
        "build_resource_relation_graph": build_resource_relation_graph,
        "resolve_open_access": resolve_open_access,
        "optionally_request_institution_access": (
            optionally_request_institution_access
        ),
        "acquire_approved_documents": acquire_approved_documents,
        "build_paperqa_corpus": build_paperqa_corpus,
        "analyze_discovery_evidence": analyze_discovery_evidence,
        "generate_coverage_report": generate_coverage_report,
        "freeze_discovery_resource_set": freeze_discovery_resource_set,
    }


def _blocked(message: str, *, kind: str) -> None:
    # Imported lazily to avoid a module cycle while workflow_scheduler registers
    # the handlers.
    from ...workflow_scheduler import BlockedStepError

    raise BlockedStepError(message, kind=kind)


def _contract_queries(contract: Any) -> list[str]:
    values: list[str] = []
    values.extend(item.statement for item in contract.hypotheses)
    values.extend(
        str(item.get("name") or item.get("metric") or "") for item in contract.metrics
    )
    values.extend(str(item) for item in contract.tasks)
    for field in (contract.baseline, contract.treatment, contract.runtime_binding):
        for key, value in field.items():
            if isinstance(value, (str, int, float)) and value:
                values.append(f"{key} {value}")
    values.append(
        "official baseline implementation dataset version model revision "
        "evaluation metric documentation"
    )
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))[:12]


def plan_protocol_retrieval(context: WorkflowStepContext) -> dict[str, Any]:
    contract = context.repository.latest_research_contract(context.study_id)
    if contract is None:
        _blocked(
            "Protocol retrieval requires a draft Research Contract",
            kind="missing_research_contract",
        )
    gateway = _gateway(context)
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    policy = gateway.get_policy(project.project_id)
    providers: list[str] = [
        item
        for item in (
            "paper_search_mcp",
            "semantic_scholar",
            "crossref",
            "github",
            "huggingface",
            "codex_native_web_search",
        )
        if item in policy.allowed_providers
    ]
    execute_step = _group_steps(context)["search_protocol_sources"]
    request = gateway.plan(
        project_id=project.project_id,
        study_id=context.study_id,
        phase=RetrievalPhase.PROTOCOL,
        step_instance_id=execute_step.step_instance_id,
        purpose="protocol_grounding",
        queries=_contract_queries(contract),
        providers=providers,
        resource_types=[
            ResourceType.PUBLICATION,
            ResourceType.PREPRINT,
            ResourceType.CODE_REPOSITORY,
            ResourceType.CODE_RELEASE,
            ResourceType.DATASET,
            ResourceType.MODEL,
            ResourceType.BENCHMARK,
            ResourceType.STANDARD,
            ResourceType.DOCUMENTATION,
            ResourceType.WEB_SOURCE,
        ],
        usage_role="protocol_grounding",
        budget=RetrievalBudget(
            max_queries=min(12, max(1, policy.max_queries or 12)),
            max_results=min(160, max(1, policy.max_results or 160)),
            max_download_bytes=min(
                5_000_000,
                max(1, policy.max_bytes or 5_000_000),
            ),
            max_cost=min(1.0, max(0.0, policy.max_cost)),
        ),
        idempotency_key=(f"{context.study_id}:{context.step.task_group}:protocol"),
        contract_refs=[
            ContractRef(
                contract_type="research",
                contract_id=f"{context.study_id}:research-v{contract.version}",
                version=contract.version,
            )
        ],
        internal_identifiers=[project.title],
        research_need=(
            "Ground frozen protocol fields in official baselines, versions, "
            "metric definitions, and statistical methods."
        ),
        freshness=_freshness_for_policy(policy),
        blocked_domains=["scholar.google.com"],
    )
    return {
        "request_id": request.request_id,
        "query_plan_id": request.query_plan_id,
        "contract_version": contract.version,
        "providers": providers,
    }


def search_protocol_sources(context: WorkflowStepContext) -> dict[str, Any]:
    planned = context.result("plan_protocol_retrieval")
    gateway = _gateway(context)
    execution = gateway.run(
        planned["request_id"],
        target_type="research_contract",
        target_id=(f"{context.study_id}:research-v{planned['contract_version']}"),
        target_field="protocol_grounding",
        relation="justifies",
    )
    workflow_artifact_ids = []
    for artifact_id in execution.run.output_artifact_ids:
        artifact = gateway.repository.load_artifact(artifact_id)
        registered = context.repository.register_artifact(
            context.study_id,
            artifact.path,
            artifact.content_hash,
            kind=f"retrieval_{artifact.kind}",
            role=(
                ArtifactRole.LITERATURE_DECISION
                if artifact.kind
                in {
                    "normalized_resources",
                    "canonical_resources",
                    "resource_set",
                }
                else ArtifactRole.AUDIT
            ),
        )
        workflow_artifact_ids.append(registered.artifact_id)
    return {
        **execution.model_dump(),
        "contract_version": planned["contract_version"],
        "_workflow_output_artifact_ids": workflow_artifact_ids,
    }


def _protocol_resources(
    context: WorkflowStepContext, allowed_types: set[ResourceType]
) -> dict[str, Any]:
    gateway = _gateway(context)
    execute_step = _group_steps(context)["search_protocol_sources"]
    bindings = [
        item
        for item in gateway.repository.list_bindings(context.study_id)
        if item.phase is RetrievalPhase.PROTOCOL
        and item.step_instance_id == execute_step.step_instance_id
    ]
    resources = [
        gateway.repository.load_resource(item.resource_id) for item in bindings
    ]
    selected = [item for item in resources if item.resource_type in allowed_types]
    return {
        "resource_ids": sorted({item.resource_id for item in selected}),
        "binding_ids": sorted(
            item.binding_id
            for item in bindings
            if item.resource_id in {resource.resource_id for resource in selected}
        ),
    }


def resolve_baseline_code(context: WorkflowStepContext) -> dict[str, Any]:
    return _protocol_resources(
        context,
        {ResourceType.CODE_REPOSITORY, ResourceType.CODE_RELEASE},
    )


def resolve_dataset_versions(context: WorkflowStepContext) -> dict[str, Any]:
    return _protocol_resources(context, {ResourceType.DATASET})


def resolve_model_revisions(context: WorkflowStepContext) -> dict[str, Any]:
    return _protocol_resources(context, {ResourceType.MODEL})


def search_official_documentation(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    return _protocol_resources(
        context,
        {
            ResourceType.DOCUMENTATION,
            ResourceType.STANDARD,
            ResourceType.BENCHMARK,
            ResourceType.WEB_SOURCE,
        },
    )


def verify_licenses(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    resource_ids = {
        resource_id
        for step_type in (
            "resolve_baseline_code",
            "resolve_dataset_versions",
            "resolve_model_revisions",
            "search_official_documentation",
        )
        for resource_id in context.result(step_type)["resource_ids"]
    }
    resources = [
        gateway.repository.load_resource(resource_id)
        for resource_id in sorted(resource_ids)
    ]
    restricted = [
        item.resource_id
        for item in resources
        if item.metadata.get("private") or item.metadata.get("gated")
    ]
    unknown = [
        item.resource_id
        for item in resources
        if item.resource_type
        in {
            ResourceType.CODE_REPOSITORY,
            ResourceType.CODE_RELEASE,
            ResourceType.DATASET,
            ResourceType.MODEL,
        }
        and not item.license
    ]
    return {
        "resource_ids": sorted(resource_ids),
        "license_restricted_resource_ids": restricted,
        "license_unknown_resource_ids": unknown,
        "eligible_resource_ids": sorted(resource_ids - set(restricted)),
    }


def bind_resources_to_contract_fields(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    gateway = _gateway(context)
    contract = context.repository.latest_research_contract(context.study_id)
    if contract is None:
        _blocked(
            "Research Contract disappeared during Protocol binding",
            kind="missing_research_contract",
        )
    eligible = set(context.result("verify_licenses")["eligible_resource_ids"])
    source_bindings = [
        item
        for item in gateway.repository.list_bindings(context.study_id)
        if item.phase is RetrievalPhase.PROTOCOL and item.resource_id in eligible
    ]
    field_by_type = {
        ResourceType.CODE_REPOSITORY: "baseline",
        ResourceType.CODE_RELEASE: "baseline",
        ResourceType.DATASET: "data_boundary",
        ResourceType.MODEL: "runtime_binding",
        ResourceType.BENCHMARK: "metrics",
        ResourceType.STANDARD: "metrics",
        ResourceType.DOCUMENTATION: "runtime_binding",
        ResourceType.WEB_SOURCE: "runtime_binding",
        ResourceType.PUBLICATION: "protocol_grounding",
        ResourceType.PREPRINT: "protocol_grounding",
    }
    promoted = []
    for binding in source_bindings:
        resource = gateway.repository.load_resource(binding.resource_id)
        target_field = field_by_type.get(resource.resource_type, "protocol_grounding")
        promoted.append(
            gateway.promote_binding(
                binding.binding_id,
                target_phase=RetrievalPhase.PROTOCOL,
                step_instance_id=context.step.step_instance_id,
                purpose="protocol_grounding",
                usage_role="contract_field_justification",
                target_type="research_contract",
                target_id=(f"{context.study_id}:research-v{contract.version}"),
                target_field=target_field,
                contract_refs=[
                    ContractRef(
                        contract_type="research",
                        contract_id=(
                            f"{context.study_id}:research-v{contract.version}"
                        ),
                        version=contract.version,
                        field=target_field,
                    )
                ],
            )
        )
    return {
        "contract_version": contract.version,
        "binding_ids": [item.binding_id for item in promoted],
        "field_bindings": {
            field: [item.binding_id for item in promoted if item.target_field == field]
            for field in sorted({item.target_field for item in promoted})
        },
    }


def freeze_protocol_resource_set(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    gateway = _gateway(context)
    planned = context.result("plan_protocol_retrieval")
    sets = [
        item
        for item in gateway.repository.list_resource_sets(context.study_id)
        if item.query_plan_id == planned["query_plan_id"]
    ]
    frozen = [
        gateway.freeze_resource_set(item.resource_set_id)
        if item.status is not ResourceSetStatus.FROZEN
        else item
        for item in sets
    ]
    return {
        "status": "frozen" if frozen else "not_applicable",
        "resource_sets": [item.model_dump(mode="json") for item in frozen],
        "contract_version": planned["contract_version"],
    }


def protocol_handlers() -> dict[str, Any]:
    return {
        "plan_protocol_retrieval": plan_protocol_retrieval,
        "search_protocol_sources": search_protocol_sources,
        "resolve_baseline_code": resolve_baseline_code,
        "resolve_dataset_versions": resolve_dataset_versions,
        "resolve_model_revisions": resolve_model_revisions,
        "search_official_documentation": search_official_documentation,
        "verify_licenses": verify_licenses,
        "bind_resources_to_contract_fields": bind_resources_to_contract_fields,
        "freeze_protocol_resource_set": freeze_protocol_resource_set,
    }


def resolve_contract_approved_resources(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    contract = context.repository.latest_research_contract(context.study_id)
    study = context.repository.load_study(context.study_id)
    if (
        contract is None
        or contract.status is not ArtifactStatus.FROZEN
        or study.active_contract_version != contract.version
    ):
        _blocked(
            "Experimentation requires the active frozen Research Contract",
            kind="contract_not_frozen",
        )
    approved_ids = contract.runtime_binding.get("approved_resource_ids")
    if not isinstance(approved_ids, list) or not approved_ids:
        _blocked(
            "Research Contract runtime_binding must list approved_resource_ids",
            kind="missing_contract_resource_authorization",
        )
    approved = {str(item) for item in approved_ids}
    gateway = _gateway(context)
    protocol_bindings = [
        item
        for item in gateway.repository.list_bindings(context.study_id)
        if item.phase is RetrievalPhase.PROTOCOL and item.resource_id in approved
    ]
    missing = sorted(approved - {item.resource_id for item in protocol_bindings})
    if missing:
        _blocked(
            "Contract-approved resources have no frozen Protocol binding: "
            + ", ".join(missing),
            kind="missing_protocol_binding",
        )
    contract_ref = ContractRef(
        contract_type="research",
        contract_id=f"{context.study_id}:research-v{contract.version}",
        version=contract.version,
    )
    promoted = [
        gateway.promote_binding(
            binding.binding_id,
            target_phase=RetrievalPhase.EXPERIMENTATION,
            step_instance_id=context.step.step_instance_id,
            purpose=(
                "fetch_pinned_code_revision"
                if gateway.repository.load_resource(binding.resource_id).resource_type
                in {ResourceType.CODE_REPOSITORY, ResourceType.CODE_RELEASE}
                else "fetch_approved_model"
                if gateway.repository.load_resource(binding.resource_id).resource_type
                is ResourceType.MODEL
                else "fetch_approved_dataset"
            ),
            usage_role="approved_experiment_resource",
            target_type="research_contract",
            target_id=contract_ref.contract_id,
            target_field="runtime_binding",
            contract_refs=[contract_ref],
        )
        for binding in protocol_bindings
    ]
    return {
        "contract_version": contract.version,
        "contract_ref": contract_ref.model_dump(mode="json"),
        "resource_ids": sorted(approved),
        "binding_ids": [item.binding_id for item in promoted],
    }


def _fetch_pinned(
    context: WorkflowStepContext,
    *,
    resource_types: set[ResourceType],
    content_levels: set[str],
) -> dict[str, Any]:
    gateway = _gateway(context)
    approved = context.result("resolve_contract_approved_resources")
    resources = [
        gateway.repository.load_resource(item) for item in approved["resource_ids"]
    ]
    selected = [item for item in resources if item.resource_type in resource_types]
    if not selected:
        return {
            "status": "not_applicable",
            "resource_ids": [],
            "snapshot_ids": [],
        }
    unpinned = []
    snapshot_ids: list[str] = []
    missing_archives = []
    for resource in selected:
        if resource.resource_type in {
            ResourceType.CODE_REPOSITORY,
            ResourceType.CODE_RELEASE,
        }:
            pinned = bool(resource.commit and len(resource.commit) == 40)
        else:
            revision = str(
                resource.metadata.get("revision")
                or resource.metadata.get("version")
                or ""
            )
            pinned = bool(revision)
        if not pinned:
            unpinned.append(resource.resource_id)
            continue
        snapshots = [
            item
            for item in gateway.repository.list_snapshots(resource.resource_id)
            if item.content_level in content_levels
        ]
        if not snapshots:
            missing_archives.append(resource.resource_id)
        else:
            snapshot_ids.extend(item.snapshot_id for item in snapshots)
    if unpinned:
        _blocked(
            "Contract resource is not pinned to an immutable revision: "
            + ", ".join(unpinned),
            kind="unpinned_contract_resource",
        )
    if missing_archives:
        _blocked(
            "Pinned resources are authorized but their approved archives have "
            "not been acquired through RetrievalGateway: "
            + ", ".join(missing_archives),
            kind="approved_resource_not_acquired",
        )
    return {
        "status": "acquired",
        "resource_ids": [item.resource_id for item in selected],
        "snapshot_ids": sorted(set(snapshot_ids)),
    }


def fetch_pinned_code(context: WorkflowStepContext) -> dict[str, Any]:
    return _fetch_pinned(
        context,
        resource_types={
            ResourceType.CODE_REPOSITORY,
            ResourceType.CODE_RELEASE,
        },
        content_levels={"source_archive"},
    )


def fetch_pinned_model(context: WorkflowStepContext) -> dict[str, Any]:
    return _fetch_pinned(
        context,
        resource_types={ResourceType.MODEL},
        content_levels={"model_file"},
    )


def fetch_pinned_dataset(context: WorkflowStepContext) -> dict[str, Any]:
    return _fetch_pinned(
        context,
        resource_types={ResourceType.DATASET},
        content_levels={"dataset_file"},
    )


def verify_hashes(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    snapshot_ids = sorted(
        {
            snapshot_id
            for step_type in (
                "fetch_pinned_code",
                "fetch_pinned_model",
                "fetch_pinned_dataset",
            )
            for snapshot_id in context.result(step_type)["snapshot_ids"]
        }
    )
    snapshots = {
        item.snapshot_id: item
        for resource_id in context.result("resolve_contract_approved_resources")[
            "resource_ids"
        ]
        for item in gateway.repository.list_snapshots(resource_id)
        if item.snapshot_id in snapshot_ids
    }
    missing = sorted(set(snapshot_ids) - set(snapshots))
    if missing:
        _blocked(
            "Snapshot disappeared before hash verification: " + ", ".join(missing),
            kind="snapshot_missing",
        )
    return {
        "verified": True,
        "snapshot_hashes": {
            identity: snapshot.content_hash for identity, snapshot in snapshots.items()
        },
    }


def release_to_sandbox(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    hashes = context.result("verify_hashes")["snapshot_hashes"]
    snapshots = [
        item
        for resource_id in context.result("resolve_contract_approved_resources")[
            "resource_ids"
        ]
        for item in gateway.repository.list_snapshots(resource_id)
        if item.snapshot_id in hashes
    ]
    manifest = {
        "study_id": context.study_id,
        "contract_version": context.result("resolve_contract_approved_resources")[
            "contract_version"
        ],
        "network_access": "disabled",
        "snapshots": [
            {
                "snapshot_id": item.snapshot_id,
                "resource_id": item.resource_id,
                "content_hash": item.content_hash,
                "normalized_content_artifact_id": (item.normalized_content_artifact_id),
            }
            for item in snapshots
        ],
    }
    artifact = gateway.repository.write_artifact(
        project_id=context.repository.load_study(context.study_id).project_id,
        study_id=context.study_id,
        step_instance_id=context.step.step_instance_id,
        kind="sandbox_release_manifest",
        value=manifest,
        producer="sandbox_resource_releaser",
    )
    return {
        **manifest,
        "manifest_artifact_id": artifact.artifact_id,
    }


def experimentation_handlers() -> dict[str, Any]:
    return {
        "resolve_contract_approved_resources": (resolve_contract_approved_resources),
        "fetch_pinned_code": fetch_pinned_code,
        "fetch_pinned_model": fetch_pinned_model,
        "fetch_pinned_dataset": fetch_pinned_dataset,
        "verify_hashes": verify_hashes,
        "release_to_sandbox": release_to_sandbox,
    }


_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
_URL_PATTERN = re.compile(r"https://[^\s<>{}\[\]]+")


def build_synthesis_corpus(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    frozen_sets = [
        item
        for item in gateway.repository.list_resource_sets(context.study_id)
        if item.status is ResourceSetStatus.FROZEN
    ]
    binding_ids = {
        binding_id for item in frozen_sets for binding_id in item.binding_ids
    }
    source_bindings = [
        item
        for item in gateway.repository.list_bindings(context.study_id)
        if item.binding_id in binding_ids
    ]
    snapshot_ids = sorted({item.snapshot_id for item in source_bindings})
    eligible_snapshots = []
    for resource_id in {item.resource_id for item in source_bindings}:
        for snapshot in gateway.repository.list_snapshots(resource_id):
            if (
                snapshot.snapshot_id in snapshot_ids
                and snapshot.content_level == "full_text"
                and snapshot.model_processing_allowed
            ):
                eligible_snapshots.append(snapshot)
    if not eligible_snapshots:
        return {
            "resource_set_ids": [item.resource_set_id for item in frozen_sets],
            "binding_ids": sorted(binding_ids),
            "snapshot_ids": snapshot_ids,
            "paperqa_eligible_snapshot_ids": [],
            "status": "metadata_only",
            "reason": "no rights-approved full text is available for synthesis",
        }
    documents = []
    synthesis_binding_ids = []
    for snapshot in eligible_snapshots:
        source_binding = next(
            (
                item
                for item in source_bindings
                if item.resource_id == snapshot.resource_id
                and item.snapshot_id == snapshot.snapshot_id
            ),
            None,
        )
        decisions = [
            item
            for item in gateway.repository.list_access_decisions(snapshot.resource_id)
            if item.full_text_available and item.model_processing_allowed
        ]
        if source_binding is None or not decisions:
            continue
        promoted = gateway.promote_binding(
            source_binding.binding_id,
            target_phase=RetrievalPhase.SYNTHESIS,
            step_instance_id=context.step.step_instance_id,
            purpose="citation_verification",
            usage_role="synthesis_evidence",
            target_type="manuscript",
            target_id=context.study_id,
            target_field="claim_support",
        )
        synthesis_binding_ids.append(promoted.binding_id)
        documents.append(
            CorpusDocument(
                resource_id=snapshot.resource_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_hash=snapshot.content_hash,
                access_decision_id=max(
                    decisions, key=lambda item: item.decided_at
                ).decision_id,
                binding_id=promoted.binding_id,
            )
        )
    if not documents:
        return {
            "resource_set_ids": [item.resource_set_id for item in frozen_sets],
            "binding_ids": sorted(binding_ids),
            "snapshot_ids": snapshot_ids,
            "paperqa_eligible_snapshot_ids": [],
            "status": "metadata_only",
            "reason": (
                "eligible full text lacks a valid access decision or frozen binding"
            ),
        }
    service = PaperQAEvidenceService(gateway.repository)
    try:
        corpus = service.create_corpus(
            study_id=context.study_id,
            phase=RetrievalPhase.SYNTHESIS,
            documents=documents,
            parser_version="paperqa-2026.3.18",
            embedding_model_hash=hashlib.sha256(b"paperqa-sparse").hexdigest(),
            llm_config_hash=synthesis_model_config_hash(),
        )
        corpus = service.build_index(corpus.corpus_id)
    except RuntimeError as exc:
        _blocked(str(exc), kind="paperqa_runtime_unavailable")
    return {
        "resource_set_ids": [item.resource_set_id for item in frozen_sets],
        "binding_ids": sorted(synthesis_binding_ids),
        "snapshot_ids": snapshot_ids,
        "paperqa_eligible_snapshot_ids": sorted(
            item.snapshot_id for item in eligible_snapshots
        ),
        "corpus": corpus.model_dump(mode="json"),
        "status": "ready",
    }


def verify_citations(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    manuscript_artifacts = [
        item
        for item in context.repository.list_artifacts(context.study_id)
        if item.role is ArtifactRole.MANUSCRIPT
    ]
    if not manuscript_artifacts:
        _blocked(
            "Citation verification requires a registered manuscript artifact",
            kind="missing_manuscript",
        )
    citations: set[str] = set()
    unreadable = []
    for artifact in manuscript_artifacts:
        path = Path(artifact.path)
        if not path.is_file():
            unreadable.append(artifact.artifact_id)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        citations.update(
            item.rstrip(".,;)").casefold() for item in _DOI_PATTERN.findall(text)
        )
        citations.update(item.rstrip(".,;)") for item in _URL_PATTERN.findall(text))
    resources = gateway.repository.list_resources()
    matched = {
        citation: [
            item.resource_id
            for item in resources
            if citation
            in {
                str(item.doi or "").casefold(),
                str(item.url or ""),
                str(item.canonical_identifier).casefold(),
            }
        ]
        for citation in sorted(citations)
    }
    return {
        "manuscript_artifact_ids": [item.artifact_id for item in manuscript_artifacts],
        "unreadable_artifact_ids": unreadable,
        "citations": sorted(citations),
        "matched_resources": matched,
        "unresolved_citations": [
            citation for citation, resource_ids in matched.items() if not resource_ids
        ],
    }


def check_retractions_and_corrections(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    citation_audit = context.result("verify_citations")
    dois = [item for item in citation_audit["citations"] if item.startswith("10.")]
    if not dois:
        return {
            "status": "not_applicable",
            "reason": "manuscript has no DOI citations",
            "notices": [],
        }
    gateway = _gateway(context)
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    policy = gateway.get_policy(project.project_id)
    providers: list[str] = [
        item
        for item in (
            "crossref",
            "paper_search_mcp",
            "codex_native_web_search",
        )
        if item in policy.allowed_providers
    ]
    request = gateway.plan(
        project_id=project.project_id,
        study_id=context.study_id,
        phase=RetrievalPhase.SYNTHESIS,
        step_instance_id=context.step.step_instance_id,
        purpose="retraction_check",
        queries=[f"{doi} retraction correction publisher notice" for doi in dois],
        providers=providers,
        resource_types=[
            ResourceType.PUBLICATION,
            ResourceType.PREPRINT,
            ResourceType.RETRACTION_NOTICE,
            ResourceType.PUBLISHER_NOTICE,
        ],
        usage_role="citation_source",
        budget=RetrievalBudget(
            max_queries=len(dois),
            max_results=max(10, len(dois) * 5),
            max_download_bytes=2_000_000,
            max_cost=min(1.0, max(0.0, policy.max_cost)),
        ),
        idempotency_key=(f"{context.study_id}:{context.step.task_group}:retractions"),
        research_need=(
            "Check cited DOI records for official retraction and correction notices."
        ),
        freshness=_freshness_for_policy(policy),
        blocked_domains=["scholar.google.com"],
    )
    execution = gateway.run(
        request.request_id,
        target_type="manuscript",
        target_id=context.study_id,
        target_field="citations",
        relation="warns",
    )
    return execution.model_dump()


def verify_resource_versions(context: WorkflowStepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    corpus = context.result("build_synthesis_corpus")
    bindings = [
        item
        for item in gateway.repository.list_bindings(context.study_id)
        if item.binding_id in set(corpus["binding_ids"])
    ]
    resources = [
        gateway.repository.load_resource(item.resource_id) for item in bindings
    ]
    unpinned = []
    for resource in resources:
        if resource.resource_type in {
            ResourceType.CODE_REPOSITORY,
            ResourceType.CODE_RELEASE,
        } and not (resource.commit and len(resource.commit) == 40):
            unpinned.append(resource.resource_id)
        if resource.resource_type in {
            ResourceType.MODEL,
            ResourceType.DATASET,
        } and not (
            resource.metadata.get("revision") or resource.metadata.get("version")
        ):
            unpinned.append(resource.resource_id)
    return {
        "checked_resource_ids": sorted({item.resource_id for item in resources}),
        "unpinned_resource_ids": sorted(set(unpinned)),
        "passed": not unpinned,
    }


def search_submission_requirements(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    study = context.repository.load_study(context.study_id)
    venue = str(study.settings.get("target_venue") or "").strip()
    if not venue:
        return {
            "status": "not_applicable",
            "reason": "Study has no target_venue setting",
        }
    gateway = _gateway(context)
    project = context.repository.load_project(study.project_id)
    policy = gateway.get_policy(project.project_id)
    providers: list[str] = (
        ["codex_native_web_search"]
        if "codex_native_web_search" in policy.allowed_providers
        else []
    )
    domains = [
        str(item)
        for item in study.settings.get("submission_domains", [])
        if str(item).strip()
    ]
    request = gateway.plan(
        project_id=project.project_id,
        study_id=context.study_id,
        phase=RetrievalPhase.SYNTHESIS,
        step_instance_id=context.step.step_instance_id,
        purpose="submission_guideline_lookup",
        queries=[f"{venue} official author submission formatting requirements"],
        providers=providers,
        resource_types=[
            ResourceType.SUBMISSION_RULE,
            ResourceType.DOCUMENTATION,
        ],
        usage_role="submission_requirement",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=10,
            max_download_bytes=500_000,
            max_cost=min(0.25, max(0.0, policy.max_cost)),
        ),
        idempotency_key=(f"{context.study_id}:{context.step.task_group}:submission"),
        research_need="Find the target venue's official submission requirements.",
        freshness=_freshness_for_policy(policy),
        allowed_domains=domains,
        blocked_domains=["scholar.google.com"],
        require_search_execution=True,
    )
    return gateway.run(
        request.request_id,
        target_type="manuscript",
        target_id=context.study_id,
        target_field="submission_requirements",
        relation="contextualizes",
    ).model_dump()


def analyze_claim_support(context: WorkflowStepContext) -> dict[str, Any]:
    corpus = context.result("build_synthesis_corpus")
    if corpus["status"] != "ready":
        return {
            "status": "not_applicable",
            "reason": corpus.get(
                "reason", "no rights-approved full-text corpus is indexed"
            ),
            "evidence_results": [],
            "verdict_authority": False,
        }
    contract = context.repository.latest_research_contract(context.study_id)
    questions = [
        (
            "What evidence in the frozen corpus supports, contradicts, or "
            f"qualifies this registered hypothesis: {item.statement}"
        )
        for item in (contract.hypotheses if contract is not None else [])
    ]
    if not questions:
        return {
            "status": "not_applicable",
            "reason": "no registered hypotheses are available for evidence mapping",
            "evidence_results": [],
            "verdict_authority": False,
        }
    service = PaperQAEvidenceService(_gateway(context).repository)
    results: list[dict[str, Any]] = []
    limitations = []
    try:
        batch_results = service.query_evidence_batch(
            corpus["corpus"]["corpus_id"],
            [
                {"question_id": f"hypothesis-{index + 1}", "question": question}
                for index, question in enumerate(questions)
            ],
        )
    except (RuntimeError, ValueError) as exc:
        limitations.append(str(exc))
        batch_results = []
    results.extend(item.model_dump(mode="json") for item in batch_results)
    return {
        "status": "analyzed" if results else "inconclusive",
        "reason": (
            "PaperQA maps frozen source spans to registered hypotheses; it "
            "cannot decide or overwrite the scientific verdict."
        ),
        "evidence_results": results,
        "limitations": limitations,
        "verdict_authority": False,
    }


def produce_citation_audit(context: WorkflowStepContext) -> dict[str, Any]:
    citation = context.result("verify_citations")
    versions = context.result("verify_resource_versions")
    retractions = context.result("check_retractions_and_corrections")
    submission = context.result("search_submission_requirements")
    analysis = context.result("analyze_claim_support")
    report = {
        "study_id": context.study_id,
        "citation_count": len(citation["citations"]),
        "unresolved_citations": citation["unresolved_citations"],
        "resource_versions_passed": versions["passed"],
        "unpinned_resource_ids": versions["unpinned_resource_ids"],
        "retraction_check_status": (
            retractions.get("run", {}).get("execution_status")
            if isinstance(retractions.get("run"), dict)
            else retractions.get("status")
        ),
        "submission_check_status": (
            submission.get("run", {}).get("execution_status")
            if isinstance(submission.get("run"), dict)
            else submission.get("status")
        ),
        "paperqa_status": analysis["status"],
        "historical_verdict_modified": False,
        "requires_repair": bool(
            citation["unresolved_citations"] or versions["unpinned_resource_ids"]
        ),
    }
    artifact = _gateway(context).repository.write_artifact(
        project_id=context.repository.load_study(context.study_id).project_id,
        study_id=context.study_id,
        step_instance_id=context.step.step_instance_id,
        kind="citation_audit",
        value=report,
        producer="citation_auditor",
    )
    return {**report, "artifact_id": artifact.artifact_id}


def synthesis_handlers() -> dict[str, Any]:
    return {
        "build_synthesis_corpus": build_synthesis_corpus,
        "verify_citations": verify_citations,
        "check_retractions_and_corrections": (check_retractions_and_corrections),
        "verify_resource_versions": verify_resource_versions,
        "search_submission_requirements": search_submission_requirements,
        "analyze_claim_support": analyze_claim_support,
        "produce_citation_audit": produce_citation_audit,
    }


def _repair_contract(context: WorkflowStepContext) -> Any:
    repairs = context.repository.list_repair_contracts(context.study_id)
    if not repairs:
        _blocked(
            "Repair retrieval requires an existing Repair Contract",
            kind="missing_repair_contract",
        )
    return max(repairs, key=lambda item: item.version)


def plan_diagnostic_retrieval(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    repair = _repair_contract(context)
    gateway = _gateway(context)
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    policy = gateway.get_policy(project.project_id)
    providers: list[str] = [
        item
        for item in (
            "paper_search_mcp",
            "semantic_scholar",
            "crossref",
            "github",
            "huggingface",
            "codex_native_web_search",
        )
        if item in policy.allowed_providers
    ]
    purpose_by_owner = {
        "idea_validation": "diagnose_methodological_failure",
        "evidence_packaging": "diagnose_evidence_binding_failure",
        "literature_grounding": "diagnose_literature_grounding_failure",
        "paper_writer": "diagnose_literature_grounding_failure",
        "execution_environment": "diagnose_runtime_failure",
        "integrity_binding": "diagnose_evidence_binding_failure",
    }
    purpose = purpose_by_owner[repair.diagnostic_owner.value]
    execute_step = _group_steps(context)["execute_diagnostic_search"]
    request = gateway.plan(
        project_id=project.project_id,
        study_id=context.study_id,
        phase=RetrievalPhase.REPAIR,
        step_instance_id=execute_step.step_instance_id,
        purpose=purpose,
        queries=[
            (
                f"{repair.diagnostic_owner.value} "
                f"{repair.earliest_affected_phase.value} "
                "official known issue reproducible fix"
            )
        ],
        providers=providers,
        resource_types=list(ResourceType),
        usage_role="diagnostic_source",
        budget=RetrievalBudget(
            max_queries=min(4, max(1, policy.max_queries or 4)),
            max_results=min(40, max(1, policy.max_results or 40)),
            max_download_bytes=min(
                2_000_000,
                max(1, policy.max_bytes or 2_000_000),
            ),
            max_cost=min(0.5, max(0.0, policy.max_cost)),
        ),
        idempotency_key=(
            f"{context.study_id}:{context.step.task_group}:repair-v{repair.version}"
        ),
        contract_refs=[
            ContractRef(
                contract_type="repair",
                contract_id=repair.repair_id,
                version=repair.version,
            )
        ],
        internal_identifiers=[project.title],
        research_need=(
            "Find official diagnostic evidence for the bounded failure without "
            "changing the historical verdict."
        ),
        freshness=_freshness_for_policy(policy),
        blocked_domains=["scholar.google.com"],
    )
    return {
        "request_id": request.request_id,
        "query_plan_id": request.query_plan_id,
        "repair_contract_id": repair.repair_id,
        "repair_version": repair.version,
        "diagnostic_owner": repair.diagnostic_owner.value,
        "earliest_preventable_phase": repair.earliest_affected_phase.value,
        "affected_artifact_ids": repair.invalidated_artifact_ids,
    }


def execute_diagnostic_search(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    planned = context.result("plan_diagnostic_retrieval")
    execution = _gateway(context).run(
        planned["request_id"],
        target_type="repair_contract",
        target_id=planned["repair_contract_id"],
        target_field="diagnostic_evidence",
        relation="warns",
    )
    return {
        **execution.model_dump(),
        "repair_contract_id": planned["repair_contract_id"],
    }


def bind_findings_to_failure(
    context: WorkflowStepContext,
) -> dict[str, Any]:
    gateway = _gateway(context)
    planned = context.result("plan_diagnostic_retrieval")
    execute_step = _group_steps(context)["execute_diagnostic_search"]
    bindings = [
        item
        for item in gateway.repository.list_bindings(context.study_id)
        if item.phase is RetrievalPhase.REPAIR
        and item.step_instance_id == execute_step.step_instance_id
    ]
    return {
        "repair_contract_id": planned["repair_contract_id"],
        "diagnostic_owner": planned["diagnostic_owner"],
        "earliest_preventable_phase": planned["earliest_preventable_phase"],
        "affected_artifact_ids": planned["affected_artifact_ids"],
        "diagnostic_binding_ids": [item.binding_id for item in bindings],
        "historical_verdict_modified": False,
    }


def propose_repair_contract(context: WorkflowStepContext) -> dict[str, Any]:
    repair = _repair_contract(context)
    findings = context.result("bind_findings_to_failure")
    proposal = {
        "repair_contract_id": repair.repair_id,
        "repair_version": repair.version,
        "status": repair.status.value,
        "scientific_change": repair.scientific_change,
        "diagnostic_owner": repair.diagnostic_owner.value,
        "earliest_affected_phase": repair.earliest_affected_phase.value,
        "invalidated_artifact_ids": repair.invalidated_artifact_ids,
        "reusable_artifact_ids": repair.reusable_artifact_ids,
        "diagnostic_binding_ids": findings["diagnostic_binding_ids"],
        "requires_owner_approval": repair.scientific_change,
        "historical_run_modified": False,
        "historical_verdict_modified": False,
    }
    artifact = _gateway(context).repository.write_artifact(
        project_id=context.repository.load_study(context.study_id).project_id,
        study_id=context.study_id,
        step_instance_id=context.step.step_instance_id,
        kind="repair_retrieval_proposal",
        value=proposal,
        producer="repair_retrieval_planner",
    )
    return {**proposal, "artifact_id": artifact.artifact_id}


def repair_handlers() -> dict[str, Any]:
    return {
        "plan_diagnostic_retrieval": plan_diagnostic_retrieval,
        "execute_diagnostic_search": execute_diagnostic_search,
        "bind_findings_to_failure": bind_findings_to_failure,
        "propose_repair_contract": propose_repair_contract,
    }


def external_research_handlers() -> dict[str, Any]:
    return {
        **exact_discovery_handlers(),
        **protocol_handlers(),
        **experimentation_handlers(),
        **synthesis_handlers(),
        **repair_handlers(),
    }
