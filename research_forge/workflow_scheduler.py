"""Persistent DAG execution for Workflow v2.

The scheduler treats ``StepInstance`` records as the execution source of truth.
Completed nodes are never repeated, ready siblings may run concurrently, and a
paused Study does not start another node.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, cast

from .claim_discovery import (
    AcademicConceptNormalization,
    AuthorClaim,
    ClaimDiscoveryReport,
    DiscoveryPortfolio,
    DiscoveryQueryIntent,
    ProjectResearchFingerprint,
    TrendSignal,
    _terms,
    build_discovery_portfolio,
    build_discovery_query_intents,
    build_academic_concept_normalizations,
    build_project_fingerprint,
    extract_author_claims,
    recommend_claims,
)
from .project_bundle import (
    BundleInspection,
    BundleResource,
    NoveltyCandidate,
    discover_derived_material_candidate,
    discover_novelty_candidates,
    inventory_project_bundle,
)
from .workflow_domain import (
    ArtifactStatus,
    ArtifactRole,
    EntryMode,
    ExecutionStatus,
    ExecutorType,
    GateType,
    Phase,
    ScopeContractVersion,
    StepInstance,
    StudyLifecycle,
    WorkflowRepository,
    stable_id,
    utc_now,
)
from .retrieval.domain.models import (
    NetworkMode,
    ResourceType,
    RetrievalBudget,
    RetrievalPhase,
    retrieval_id,
)
from .retrieval.interfaces.service import RetrievalGateway
from .retrieval.workflow.step_definitions import (
    UNCONNECTED_RETRIEVAL_STEPS,
    retrieval_step_definitions,
)
from .retrieval.workflow.handlers import external_research_handlers


class TransientStepError(RuntimeError):
    """An execution/environment failure that may succeed on a bounded retry."""

    def __init__(
        self, message: str, *, fallback_result: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.fallback_result = fallback_result


class BlockedStepError(RuntimeError):
    """A non-transient missing condition that requires an external change."""

    def __init__(self, message: str, *, kind: str = "requirement") -> None:
        super().__init__(message)
        self.kind = kind


StepHandler = Callable[["StepContext"], dict[str, Any]]


@dataclass(frozen=True)
class StepContext:
    repository: WorkflowRepository
    study_id: str
    step: StepInstance

    def result(self, step_type: str) -> dict[str, Any]:
        # Only traverse immutable, already-succeeded ancestors. Reading every
        # step file here races with sibling status updates on Windows.
        pending = list(self.step.depends_on)
        visited: set[str] = set()
        matches: list[StepInstance] = []
        while pending:
            step_id = pending.pop()
            if step_id in visited:
                continue
            visited.add(step_id)
            ancestor = self.repository.load_step(self.study_id, step_id)
            if (
                ancestor.step_type == step_type
                and ancestor.status is ExecutionStatus.SUCCEEDED
            ):
                matches.append(ancestor)
            pending.extend(ancestor.depends_on)
        if not matches:
            raise BlockedStepError(
                f"required result is unavailable: {step_type}",
                kind="missing_dependency_result",
            )
        return self.repository.load_step_result(
            self.study_id, matches[-1].step_instance_id
        )


class PersistentDAGScheduler:
    def __init__(
        self,
        repository: WorkflowRepository,
        handlers: dict[str, StepHandler],
        *,
        max_concurrency: int = 4,
        retry_backoff_seconds: float = 0.05,
        recover_interrupted: bool = False,
    ) -> None:
        self.repository = repository
        self.handlers = handlers
        self.max_concurrency = max(1, max_concurrency)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._repository_lock = threading.RLock()
        self.recover_interrupted = recover_interrupted

    def run(self, study_id: str) -> dict[str, Any]:
        """Run until success, a user gate, pause, failure, or dependency block."""
        if self.recover_interrupted:
            self._recover_interrupted_steps(study_id)
            self.recover_interrupted = False
        while True:
            study = self.repository.load_study(study_id)
            if study.lifecycle is not StudyLifecycle.ACTIVE:
                return self.repository.snapshot(study_id)
            if study.execution_status is ExecutionStatus.PAUSED:
                return self.repository.snapshot(study_id)

            self._resolve_owner_steps(study_id)
            steps = self.repository.list_steps(study_id)
            self._block_impossible_descendants(study_id, steps)
            steps = self.repository.list_steps(study_id)
            ready = [
                step
                for step in steps
                if step.status is ExecutionStatus.QUEUED
                and all(
                    self.repository.load_step(study_id, dependency).status
                    is ExecutionStatus.SUCCEEDED
                    for dependency in step.depends_on
                )
            ]
            if not ready:
                return self.repository.snapshot(study_id)

            automatic: list[StepInstance] = []
            for step in ready:
                if step.executor_type is ExecutorType.PROJECT_OWNER:
                    gate_label = (
                        "repair scope"
                        if step.step_type == "regression_scope_review"
                        else "Scope Contract"
                    )
                    self.repository.update_step(
                        study_id,
                        step.step_instance_id,
                        ExecutionStatus.WAITING_FOR_USER,
                        blocker={
                            "kind": "owner_gate",
                            "message": (
                                f"The project owner must approve the {gate_label}."
                            ),
                        },
                    )
                else:
                    automatic.append(step)
            if not automatic:
                return self.repository.snapshot(study_id)

            with ThreadPoolExecutor(
                max_workers=min(self.max_concurrency, len(automatic))
            ) as pool:
                futures = {
                    pool.submit(self._execute_step, study_id, step): step
                    for step in automatic
                }
                for future in as_completed(futures):
                    future.result()

    def retry_step(self, study_id: str, step_id: str) -> StepInstance:
        step = self.repository.load_step(study_id, step_id)
        if step.status not in {
            ExecutionStatus.FAILED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.WAITING_FOR_USER,
        }:
            raise ValueError("only failed, blocked, or waiting steps can be retried")
        # A retry is an explicit new scheduling decision; the attempt counter and
        # prior audit events stay intact.
        return self.repository.update_step(
            study_id, step_id, ExecutionStatus.QUEUED, blocker=None
        )

    def _execute_step(self, study_id: str, step: StepInstance) -> None:
        handler = self.handlers.get(step.step_type)
        if handler is None:
            self.repository.update_step(
                study_id,
                step.step_instance_id,
                ExecutionStatus.BLOCKED,
                blocker={
                    "kind": "missing_executor",
                    "message": f"no executor registered for {step.step_type}",
                },
            )
            return
        with self._repository_lock:
            self.repository.update_step(
                study_id, step.step_instance_id, ExecutionStatus.RUNNING
            )
        while True:
            current = self.repository.load_step(study_id, step.step_instance_id)
            try:
                result = handler(StepContext(self.repository, study_id, current))
                with self._repository_lock:
                    self._persist_success(study_id, step, result)
                return
            except TransientStepError as exc:
                current = self.repository.load_step(study_id, step.step_instance_id)
                if current.attempt >= current.max_retries + 1:
                    with self._repository_lock:
                        if exc.fallback_result is not None:
                            self._persist_success(study_id, step, exc.fallback_result)
                        else:
                            self.repository.update_step(
                                study_id,
                                step.step_instance_id,
                                ExecutionStatus.FAILED,
                                blocker={
                                    "kind": "retry_exhausted",
                                    "message": str(exc),
                                },
                            )
                    return
                with self._repository_lock:
                    self.repository.update_step(
                        study_id,
                        step.step_instance_id,
                        ExecutionStatus.RETRYING,
                        blocker={"kind": "transient", "message": str(exc)},
                    )
                time.sleep(
                    self.retry_backoff_seconds * (2 ** max(0, current.attempt - 1))
                )
            except BlockedStepError as exc:
                with self._repository_lock:
                    self.repository.update_step(
                        study_id,
                        step.step_instance_id,
                        ExecutionStatus.BLOCKED,
                        blocker={"kind": exc.kind, "message": str(exc)},
                    )
                return
            except Exception as exc:
                with self._repository_lock:
                    self.repository.update_step(
                        study_id,
                        step.step_instance_id,
                        ExecutionStatus.FAILED,
                        blocker={
                            "kind": "permanent_execution_error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                return

    def _persist_success(
        self, study_id: str, step: StepInstance, result: dict[str, Any]
    ) -> None:
        artifact = self.repository.save_step_result(
            study_id, step.step_instance_id, result
        )
        extra_output_ids = [
            str(item) for item in result.get("_workflow_output_artifact_ids", [])
        ]
        for dependency_id in step.depends_on:
            dependency = self.repository.load_step(study_id, dependency_id)
            for input_artifact_id in dependency.output_artifact_ids:
                self.repository.add_dependency(
                    study_id,
                    input_artifact_id,
                    artifact.artifact_id,
                    relation="step_depends_on",
                )
        self.repository.update_step(
            study_id,
            step.step_instance_id,
            ExecutionStatus.SUCCEEDED,
            output_artifact_ids=[artifact.artifact_id, *extra_output_ids],
        )

    def _resolve_owner_steps(self, study_id: str) -> None:
        gates = self.repository.list_gates(study_id)
        for step in self.repository.list_steps(study_id):
            if (
                step.executor_type is not ExecutorType.PROJECT_OWNER
                or step.status is not ExecutionStatus.WAITING_FOR_USER
            ):
                continue
            relevant = (
                [gate for gate in gates if gate.gate_type is GateType.SCOPE_APPROVAL]
                if step.step_type in {"scope_review", "scope_review_gate"}
                else [
                    gate
                    for gate in gates
                    if gate.gate_type is GateType.REPAIR_OR_HIGH_COST_RUN
                ]
                if step.step_type == "regression_scope_review"
                else []
            )
            if any(gate.status.value == "approved" for gate in relevant):
                self._persist_success(
                    study_id,
                    step,
                    {
                        "decision": "approved",
                        "gate_ids": [gate.gate_id for gate in relevant],
                    },
                )
            elif any(gate.status.value == "rejected" for gate in relevant):
                self.repository.update_step(
                    study_id,
                    step.step_instance_id,
                    ExecutionStatus.BLOCKED,
                    blocker={
                        "kind": "gate_rejected",
                        "message": (
                            "The project owner rejected the required workflow Gate."
                        ),
                    },
                )

    def _recover_interrupted_steps(self, study_id: str) -> None:
        for step in self.repository.list_steps(study_id):
            if step.status not in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.RETRYING,
            }:
                continue
            self.repository.update_step(
                study_id,
                step.step_instance_id,
                ExecutionStatus.QUEUED,
                blocker={
                    "kind": "recovered_after_process_restart",
                    "message": "The previous executor stopped before a terminal checkpoint.",
                },
            )

    def _block_impossible_descendants(
        self, study_id: str, steps: list[StepInstance]
    ) -> None:
        impossible = {
            ExecutionStatus.FAILED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.CANCELLED,
        }
        current = steps
        while True:
            statuses = {item.step_instance_id: item.status for item in current}
            changed = False
            for step in current:
                if step.status is not ExecutionStatus.QUEUED:
                    continue
                failed = [
                    dependency
                    for dependency in step.depends_on
                    if statuses.get(dependency) in impossible
                ]
                if failed:
                    self.repository.update_step(
                        study_id,
                        step.step_instance_id,
                        ExecutionStatus.BLOCKED,
                        blocker={
                            "kind": "upstream_not_succeeded",
                            "dependency_step_ids": failed,
                            "message": "An upstream dependency did not succeed.",
                        },
                    )
                    changed = True
            if not changed:
                return
            current = self.repository.list_steps(study_id)


def _models(payload: list[dict[str, Any]], model: Any) -> list[Any]:
    return [model.model_validate(item) for item in payload]


def _project_scan(context: StepContext) -> dict[str, Any]:
    project = context.repository.load_project(
        context.repository.load_study(context.study_id).project_id
    )
    if not project.source_root:
        raise BlockedStepError("project source folder is not configured")
    resources, excluded = inventory_project_bundle(project.source_root)
    return {
        "source_root": str(Path(project.source_root).resolve()),
        "resource_count": len(resources),
        "excluded_count": excluded,
        "resources": [item.model_dump(mode="json") for item in resources],
        "include_external": bool(
            context.repository.load_study(context.study_id).settings.get(
                "include_external_discovery", True
            )
        ),
    }


def _author_claim_extraction(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    resources = _models(scan["resources"], BundleResource)
    claims = extract_author_claims(Path(scan["source_root"]), resources)
    return {"author_claims": [item.model_dump(mode="json") for item in claims]}


def _candidate_discovery(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    resources = _models(scan["resources"], BundleResource)
    candidates = discover_novelty_candidates(scan["source_root"], resources)
    if not candidates:
        derived = discover_derived_material_candidate(scan["source_root"], resources)
        if derived is not None:
            candidates = [derived]
    return {"candidates": [item.model_dump(mode="json") for item in candidates]}


def _academic_concept_normalization(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    claims = _models(
        context.result("author_claim_extraction")["author_claims"], AuthorClaim
    )
    candidates = context.result("candidate_discovery")["candidates"]
    normalizations = build_academic_concept_normalizations(
        scan["source_root"], candidates, claims
    )
    return {
        "academic_normalizations": [
            item.model_dump(mode="json") for item in normalizations
        ]
    }


def _fingerprint(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    claims = context.result("author_claim_extraction")
    candidates = context.result("candidate_discovery")
    fingerprint = build_project_fingerprint(
        scan["resources"],
        candidates["candidates"],
        _models(claims["author_claims"], AuthorClaim),
        source_root=scan["source_root"],
    )
    normalization_step = next(
        (
            item
            for item in context.repository.list_steps(context.study_id)
            if item.step_type == "academic_concept_normalization"
            and item.status is ExecutionStatus.SUCCEEDED
        ),
        None,
    )
    if normalization_step is not None:
        payload = context.repository.load_step_result(
            context.study_id, normalization_step.step_instance_id
        )
        normalizations = _models(
            payload["academic_normalizations"],
            AcademicConceptNormalization,
        )
        fingerprint = fingerprint.model_copy(
            update={"academic_normalizations": normalizations}
        )
    return {"fingerprint": fingerprint.model_dump(mode="json")}


def _gateway(context: StepContext) -> RetrievalGateway:
    return RetrievalGateway(str(context.repository.root))


def _draft_discovery_query_plan(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    fingerprint = ProjectResearchFingerprint.model_validate(
        context.result("project_fingerprint")["fingerprint"]
    )
    claims = _models(
        context.result("author_claim_extraction")["author_claims"], AuthorClaim
    )
    query_intents = build_discovery_query_intents(fingerprint, claims)
    all_queries = [item.query for item in query_intents]
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    execute_step = next(
        item
        for item in context.repository.list_steps(context.study_id)
        if item.step_type == "execute_discovery_retrieval"
    )
    gateway = _gateway(context)
    policy = gateway.get_policy(project.project_id)
    provider_types = {
        "paper_search_mcp": {ResourceType.PUBLICATION, ResourceType.PREPRINT},
        "semantic_scholar": {ResourceType.PUBLICATION},
        "crossref": {ResourceType.PUBLICATION},
        "github": {ResourceType.CODE_REPOSITORY},
        "huggingface": {
            ResourceType.MODEL,
            ResourceType.DATASET,
            ResourceType.SPACE,
        },
        "codex_native_web_search": {ResourceType.WEB_SOURCE},
        "openai_web_search": {ResourceType.WEB_SOURCE},
        "redfox_wechat": {ResourceType.WEB_SOURCE},
    }
    providers = [
        provider for provider in provider_types if provider in policy.allowed_providers
    ]
    resource_types = sorted(
        {
            resource_type
            for provider in providers
            for resource_type in provider_types[provider]
        },
        key=lambda item: item.value,
    )
    if (
        policy.mode in {NetworkMode.PUBLIC_WEB_READ, NetworkMode.AUTHENTICATED_READ}
        and "redfox_wechat" in policy.allowed_providers
    ):
        if "redfox_wechat" not in providers:
            providers.append("redfox_wechat")
            resource_types.append(ResourceType.WEB_SOURCE)
    request = gateway.plan(
        project_id=project.project_id,
        study_id=context.study_id,
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id=execute_step.step_instance_id,
        purpose="related_work_search",
        queries=all_queries,
        providers=providers,
        resource_types=resource_types,
        usage_role="background_source",
        budget=RetrievalBudget(
            max_queries=8,
            max_results=120,
            max_download_bytes=5_000_000,
            max_cost=1.0,
        ),
        idempotency_key=f"{context.study_id}:discovery:v1",
        internal_identifiers=[
            project.title,
            Path(scan["source_root"]).name,
        ],
        research_need="Find related work, negative results, and external attention signals.",
        freshness=("cache_only" if policy.mode is NetworkMode.OFFLINE else "live"),
        blocked_domains=["scholar.google.com"],
    )
    return {
        "request_id": request.request_id,
        "query_plan_id": request.query_plan_id,
        "network_policy_id": request.network_policy_id,
        "query_intents": [
            item.model_dump(mode="json") for item in query_intents
        ],
    }


def _evaluate_discovery_policy(context: StepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    request = gateway.repository.load_request(
        context.result("draft_discovery_query_plan")["request_id"]
    )
    plan = gateway.repository.load_query_plan(request.query_plan_id)
    policy = gateway.get_policy(request.project_id)
    decision = gateway.policy_engine.evaluate(
        request, policy, sanitized=bool(plan.sanitized_queries)
    )
    return decision.model_dump(mode="json")


def _sanitize_discovery_queries(context: StepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    request = gateway.repository.load_request(
        context.result("draft_discovery_query_plan")["request_id"]
    )
    plan = gateway.repository.load_query_plan(request.query_plan_id)
    return {
        "query_plan_id": plan.query_plan_id,
        "sanitized_queries": plan.sanitized_queries,
        "raw_query_digest": plan.raw_query_digest,
    }


def _execute_discovery_retrieval(context: StepContext) -> dict[str, Any]:
    request_id = context.result("draft_discovery_query_plan")["request_id"]
    gateway = _gateway(context)
    execution = gateway.run(request_id)
    workflow_artifact_ids: list[str] = []
    for retrieval_artifact_id in execution.run.output_artifact_ids:
        item = gateway.repository.load_artifact(retrieval_artifact_id)
        role = (
            ArtifactRole.LITERATURE_BACKGROUND
            if item.kind
            in {
                "normalized_resources",
                "verification_report",
                "resource_set",
            }
            else ArtifactRole.AUDIT
        )
        registered = context.repository.register_artifact(
            context.study_id,
            item.path,
            item.content_hash,
            kind=f"retrieval_{item.kind}",
            role=role,
        )
        workflow_artifact_ids.append(registered.artifact_id)
    attempts = execution.run.provider_attempts
    statuses: dict[str, str] = {}
    requested_providers = gateway.repository.load_request(
        request_id
    ).requested_providers
    visible_providers = list(
        dict.fromkeys(
            [
                *requested_providers,
                "paper_search_mcp",
                "semantic_scholar",
                "crossref",
                "github",
                "huggingface",
                "codex_native_web_search",
                "openai_web_search",
                "redfox_wechat",
            ]
        )
    )
    include_external = bool(
        context.repository.load_study(context.study_id).settings.get(
            "include_external_discovery", False
        )
    )
    for provider in visible_providers:
        provider_attempts = [item for item in attempts if item.provider == provider]
        succeeded = [
            item for item in provider_attempts if item.status.value == "succeeded"
        ]
        if succeeded:
            statuses[provider] = f"ok:{succeeded[-1].result_count}"
        elif provider_attempts:
            error = provider_attempts[-1].error_classification
            statuses[provider] = f"degraded:{error.value if error else 'unknown'}"
        else:
            statuses[provider] = (
                "not_requested" if not include_external else "policy_denied"
            )
    return {
        **execution.model_dump(),
        "provider_status": statuses,
        "evidence_gap": execution.run.execution_status.value == "blocked"
        or not (execution.resource_set and execution.resource_set.binding_ids),
        "_workflow_output_artifact_ids": workflow_artifact_ids,
    }


def _normalize_external_resources(context: StepContext) -> dict[str, Any]:
    execution = context.result("execute_discovery_retrieval")
    gateway = _gateway(context)
    resource_ids = {
        item.resource_id
        for item in gateway.repository.list_bindings(context.study_id)
        if item.phase is RetrievalPhase.DISCOVERY
    }
    resources = [
        gateway.repository.load_resource(resource_id)
        for resource_id in sorted(resource_ids)
    ]
    signals: list[dict[str, Any]] = []
    for resource in resources:
        metadata = resource.metadata
        provider = resource.providers[0]
        summary = str(metadata.get("summary", ""))
        source_terms = sorted(
            {
                *[str(item) for item in (metadata.get("terms") or [])],
                *_terms(f"{resource.title} {summary}"),
            }
        )[:100]
        signal_class_value = str(
            metadata.get("signal_class")
            or (
                "market_attention"
                if provider == "redfox_wechat"
                else "adoption_signal"
                if provider in {"github", "huggingface"}
                else "official_source"
                if provider
                in {
                    "codex_native_web_search",
                    "openai_web_search",
                }
                else "scholarly_attention"
            )
        )
        if signal_class_value not in {
            "market_attention",
            "scholarly_attention",
            "official_source",
            "adoption_signal",
        }:
            signal_class_value = "official_source"
        signal_class = cast(
            Literal[
                "market_attention",
                "scholarly_attention",
                "official_source",
                "adoption_signal",
            ],
            signal_class_value,
        )
        signals.append(
            TrendSignal(
                signal_id=retrieval_id("signal", resource.resource_id),
                provider=provider,  # type: ignore[arg-type]
                signal_class=signal_class,
                query="gateway-normalized",
                title=resource.title,
                summary=summary,
                url=resource.url or resource.canonical_identifier,
                published_at=resource.publication_or_release_date or "",
                source_name=(
                    resource.authors_or_owners[0] if resource.authors_or_owners else ""
                ),
                engagement=metadata.get("attention") or {},
                terms=source_terms,
                trend_score=float(metadata.get("attention_score", 0)),
                scientific_density=float(metadata.get("scientific_density", 0)),
                metadata={
                    key: value
                    for key, value in metadata.items()
                    if key
                    not in {
                        "title",
                        "summary",
                        "published_at",
                        "source_name",
                    }
                },
            ).model_dump(mode="json")
        )
    return {
        "signals": signals,
        "resource_ids": [item.resource_id for item in resources],
        "provider_status": execution["provider_status"],
        "evidence_gap": execution["evidence_gap"],
    }


def _deduplicate_external_resources(context: StepContext) -> dict[str, Any]:
    normalized = context.result("normalize_external_resources")
    coverage = context.result("execute_discovery_retrieval").get("coverage")
    return {
        "resource_ids": normalized["resource_ids"],
        "input_count": (coverage or {}).get("raw_result_count", 0),
        "output_count": (coverage or {}).get("deduplicated_result_count", 0),
        "metadata_conflicts": (coverage or {}).get("metadata_conflicts", []),
    }


def _verify_external_metadata(context: StepContext) -> dict[str, Any]:
    coverage = context.result("execute_discovery_retrieval").get("coverage")
    return {
        "resource_ids": context.result("deduplicate_external_resources")[
            "resource_ids"
        ],
        "verified_count": (coverage or {}).get("verified_result_count", 0),
        "license_constraints": (coverage or {}).get("license_constraints", []),
        "metadata_conflicts": (coverage or {}).get("metadata_conflicts", []),
    }


def _build_discovery_source_set(context: StepContext) -> dict[str, Any]:
    execution = context.result("execute_discovery_retrieval")
    return {
        "resource_set": execution.get("resource_set"),
        "coverage": execution.get("coverage"),
        "evidence_role": "background_and_discovery_only",
        "verdict_eligible": False,
    }


def _freeze_discovery_source_set(context: StepContext) -> dict[str, Any]:
    payload = context.result("build_discovery_source_set")
    resource_set = payload.get("resource_set")
    if not resource_set:
        return {
            "status": "not_applicable",
            "reason": "offline or empty retrieval produced no ResourceSet",
        }
    frozen = _gateway(context).freeze_resource_set(str(resource_set["resource_set_id"]))
    return {"resource_set": frozen.model_dump(mode="json")}


def _recommendation(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    candidates = context.result("candidate_discovery")["candidates"]
    claims = _models(
        context.result("author_claim_extraction")["author_claims"], AuthorClaim
    )
    fingerprint = ProjectResearchFingerprint.model_validate(
        context.result("project_fingerprint")["fingerprint"]
    )
    retrieval = context.result("normalize_external_resources")
    trends = _models(retrieval["signals"], TrendSignal)
    recommendations = recommend_claims(fingerprint, claims, trends, candidates)
    coverage = context.result("execute_discovery_retrieval").get("coverage") or {}
    warnings = [
        *[str(item) for item in coverage.get("known_blind_spots", [])],
        *[str(item) for item in coverage.get("metadata_conflicts", [])],
    ]
    report = ClaimDiscoveryReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_root=scan["source_root"],
        fingerprint=fingerprint,
        author_claims=claims,
        trend_signals=trends,
        recommended_claims=recommendations,
        provider_status=retrieval["provider_status"],
        warnings=warnings,
    )
    return {"claim_discovery": report.model_dump(mode="json")}


def _evidence_chain_detection(context: StepContext) -> dict[str, Any]:
    candidates = context.result("candidate_discovery")["candidates"]
    chains = []
    for candidate in candidates:
        outputs = [
            path
            for path in (
                candidate.get("output_path"),
                candidate.get("report_path"),
            )
            if path
        ]
        chains.append(
            {
                "track_id": candidate["track_id"],
                "level": (
                    "verified_chain"
                    if candidate.get("artifact_chain_complete")
                    and candidate.get("protocol_bound_to_output")
                    else "inferred_chain"
                ),
                "protocol_path": candidate["protocol_path"],
                "output_paths": outputs,
                "verdict_eligible": bool(
                    candidate.get("artifact_chain_complete")
                    and candidate.get("protocol_bound_to_output")
                ),
            }
        )
    return {"chains": chains}


def _discovery_portfolio(context: StepContext) -> dict[str, Any]:
    fingerprint = ProjectResearchFingerprint.model_validate(
        context.result("project_fingerprint")["fingerprint"]
    )
    candidates = context.result("candidate_discovery")["candidates"]
    claim_report = ClaimDiscoveryReport.model_validate(
        context.result("claim_recommendation")["claim_discovery"]
    )
    query_plan = context.result("draft_discovery_query_plan")
    query_intents = _models(
        query_plan.get("query_intents", []), DiscoveryQueryIntent
    )
    source_set = context.result("build_discovery_source_set")
    resource_set = source_set.get("resource_set") or {}
    resource_set_ids = [
        str(resource_set["resource_set_id"])
    ] if resource_set.get("resource_set_id") else []
    portfolio = build_discovery_portfolio(
        fingerprint,
        candidates,
        claim_report,
        query_intents,
        query_plan_id=str(query_plan.get("query_plan_id") or "") or None,
        resource_set_ids=resource_set_ids,
        coverage=source_set.get("coverage") or {},
    )
    return {"discovery_portfolio": portfolio.model_dump(mode="json")}


def _scope_drafting(context: StepContext) -> dict[str, Any]:
    portfolio = DiscoveryPortfolio.model_validate(
        context.result("discovery_portfolio")["discovery_portfolio"]
    )
    selected = next(
        (
            item
            for item in portfolio.directions
            if item.direction_id == portfolio.recommended_direction_id
        ),
        portfolio.directions[0] if portfolio.directions else None,
    )
    if selected is None:
        raise BlockedStepError(
            "Discovery Portfolio contains no viable direction.",
            kind="no_viable_direction",
        )
    contract = ScopeContractVersion(
        study_id=context.study_id,
        version=1,
        direction=selected.title[:300],
        research_question=selected.research_question[:1200],
        scope_in=selected.scope_in,
        scope_out=selected.scope_out,
        candidate_contribution=selected.candidate_contribution[:2000],
        project_resource_ids=selected.local_evidence_paths,
        literature_set_id=(
            portfolio.resource_set_ids[0] if portfolio.resource_set_ids else None
        ),
        field_diff={"selected_direction_id": selected.direction_id},
    )
    context.repository.save_scope_contract(contract)
    existing = [
        item
        for item in context.repository.list_gates(context.study_id)
        if item.gate_type is GateType.SCOPE_APPROVAL
        and item.subject_version == contract.version
    ]
    gate = (
        existing[0]
        if existing
        else context.repository.create_gate(
            context.study_id,
            GateType.SCOPE_APPROVAL,
            "scope_contract",
            f"{context.study_id}:scope-v{contract.version}",
            subject_version=contract.version,
        )
    )
    return {
        "scope_contract": contract.model_dump(mode="json"),
        "selected_direction_id": selected.direction_id,
        "gate": gate.model_dump(mode="json"),
    }


def _freeze_scope_contract(context: StepContext) -> dict[str, Any]:
    draft = ScopeContractVersion.model_validate(
        context.result("scope_drafting")["scope_contract"]
    )
    frozen = context.repository.save_scope_contract(
        draft.model_copy(
            update={
                "status": ArtifactStatus.FROZEN,
                "frozen_at": utc_now(),
            }
        )
    )
    return {"scope_contract": frozen.model_dump(mode="json")}


def stage_one_handlers() -> dict[str, StepHandler]:
    handlers: dict[str, StepHandler] = {
        "project_scan": _project_scan,
        "author_claim_extraction": _author_claim_extraction,
        "candidate_discovery": _candidate_discovery,
        "academic_concept_normalization": _academic_concept_normalization,
        "project_fingerprint": _fingerprint,
        "draft_discovery_query_plan": _draft_discovery_query_plan,
        "evaluate_network_policy": _evaluate_discovery_policy,
        "sanitize_discovery_queries": _sanitize_discovery_queries,
        "execute_discovery_retrieval": _execute_discovery_retrieval,
        "normalize_external_resources": _normalize_external_resources,
        "deduplicate_external_resources": _deduplicate_external_resources,
        "verify_external_metadata": _verify_external_metadata,
        "build_discovery_source_set": _build_discovery_source_set,
        "freeze_discovery_source_set": _freeze_discovery_source_set,
        "claim_recommendation": _recommendation,
        "evidence_chain_detection": _evidence_chain_detection,
        "discovery_portfolio": _discovery_portfolio,
        "scope_drafting": _scope_drafting,
        "freeze_scope_contract": _freeze_scope_contract,
    }
    handlers.update(external_research_handlers())
    for step_type in UNCONNECTED_RETRIEVAL_STEPS:
        handlers.setdefault(step_type, _unconnected_retrieval_handler)
    return handlers


def _unconnected_retrieval_handler(context: StepContext) -> dict[str, Any]:
    raise BlockedStepError(
        (
            f"{context.step.step_type} has a domain contract and policy profile "
            "but its phase business adapter is not connected yet"
        ),
        kind="not_implemented",
    )


def create_project_discovery_study(
    repository: WorkflowRepository,
    source_root: str | Path,
    *,
    title: str | None = None,
    include_external: bool = True,
    identity: str | None = None,
) -> tuple[str, str]:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project bundle directory not found: {root}")
    for definition in retrieval_step_definitions():
        repository.save_step_definition(definition)
    suffix = identity or root.as_posix()
    project = repository.create_project(
        title or root.name,
        source_root=str(root),
        project_id=stable_id("project", root.as_posix()),
    )
    study = repository.create_study(
        project.project_id,
        title or f"{root.name} discovery",
        entry_mode=EntryMode.PROJECT_TO_PAPER,
        study_id=stable_id("study", project.project_id, "discovery", suffix),
        settings={"include_external_discovery": include_external},
    )
    existing_steps = repository.list_steps(study.study_id)
    if existing_steps:
        by_type = {item.step_type: item for item in existing_steps}
        if (
            "discovery_portfolio" not in by_type
            and {
                "claim_recommendation",
                "evidence_chain_detection",
                "build_discovery_source_set",
                "draft_discovery_query_plan",
            }.issubset(by_type)
        ):
            repository.add_step(
                study.study_id,
                "discovery_portfolio",
                Phase.DISCOVERY,
                ExecutorType.DETERMINISTIC_SERVICE,
                depends_on=[
                    by_type["claim_recommendation"].step_instance_id,
                    by_type["evidence_chain_detection"].step_instance_id,
                    by_type["build_discovery_source_set"].step_instance_id,
                    by_type["draft_discovery_query_plan"].step_instance_id,
                ],
                expected_output=(
                    "Compatibility-generated Discovery Portfolio for a pre-portfolio Study"
                ),
            )
        if (
            "freeze_scope_contract" not in by_type
            and {"scope_review", "scope_drafting"}.issubset(by_type)
        ):
            repository.add_step(
                study.study_id,
                "freeze_scope_contract",
                Phase.DISCOVERY,
                ExecutorType.DETERMINISTIC_SERVICE,
                depends_on=[
                    by_type["scope_review"].step_instance_id,
                    by_type["scope_drafting"].step_instance_id,
                ],
                expected_output="Owner-approved frozen Scope Contract v1",
            )
        return project.project_id, study.study_id

    scan = repository.add_step(
        study.study_id,
        "project_scan",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="Read-only resource inventory",
    )
    claims = repository.add_step(
        study.study_id,
        "author_claim_extraction",
        Phase.DISCOVERY,
        ExecutorType.CODEX,
        depends_on=[scan.step_instance_id],
        task_group="project_discovery",
    )
    candidates = repository.add_step(
        study.study_id,
        "candidate_discovery",
        Phase.DISCOVERY,
        ExecutorType.MODEL,
        depends_on=[scan.step_instance_id],
        task_group="project_discovery",
    )
    academic_normalization = repository.add_step(
        study.study_id,
        "academic_concept_normalization",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            scan.step_instance_id,
            claims.step_instance_id,
            candidates.step_instance_id,
        ],
        expected_output=(
            "Internal labels, operational definitions, scholarly concepts, "
            "academic titles, and query terms"
        ),
    )
    fingerprint = repository.add_step(
        study.study_id,
        "project_fingerprint",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            claims.step_instance_id,
            candidates.step_instance_id,
            academic_normalization.step_instance_id,
        ],
    )
    query_plan = repository.add_step(
        study.study_id,
        "draft_discovery_query_plan",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[fingerprint.step_instance_id],
        task_group="retrieval_gateway",
    )
    policy = repository.add_step(
        study.study_id,
        "evaluate_network_policy",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[query_plan.step_instance_id],
        task_group="retrieval_gateway",
    )
    sanitize = repository.add_step(
        study.study_id,
        "sanitize_discovery_queries",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[query_plan.step_instance_id],
        task_group="retrieval_gateway",
    )
    execute_retrieval = repository.add_step(
        study.study_id,
        "execute_discovery_retrieval",
        Phase.DISCOVERY,
        ExecutorType.RETRIEVAL_SERVICE,
        depends_on=[policy.step_instance_id, sanitize.step_instance_id],
        task_group="retrieval_gateway",
    )
    normalize = repository.add_step(
        study.study_id,
        "normalize_external_resources",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[execute_retrieval.step_instance_id],
        task_group="retrieval_gateway",
    )
    deduplicate = repository.add_step(
        study.study_id,
        "deduplicate_external_resources",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[normalize.step_instance_id],
        task_group="retrieval_gateway",
    )
    verify = repository.add_step(
        study.study_id,
        "verify_external_metadata",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        depends_on=[deduplicate.step_instance_id],
        task_group="retrieval_gateway",
    )
    source_set = repository.add_step(
        study.study_id,
        "build_discovery_source_set",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[verify.step_instance_id],
        task_group="retrieval_gateway",
    )
    recommendation = repository.add_step(
        study.study_id,
        "claim_recommendation",
        Phase.DISCOVERY,
        ExecutorType.MODEL,
        depends_on=[
            fingerprint.step_instance_id,
            normalize.step_instance_id,
            execute_retrieval.step_instance_id,
        ],
    )
    evidence = repository.add_step(
        study.study_id,
        "evidence_chain_detection",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[candidates.step_instance_id],
    )
    portfolio = repository.add_step(
        study.study_id,
        "discovery_portfolio",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            recommendation.step_instance_id,
            evidence.step_instance_id,
            source_set.step_instance_id,
            query_plan.step_instance_id,
        ],
        expected_output=(
            "Candidate-specific Claim–Source matches and comparable research directions"
        ),
    )
    scope = repository.add_step(
        study.study_id,
        "scope_drafting",
        Phase.DISCOVERY,
        ExecutorType.CODEX,
        depends_on=[portfolio.step_instance_id],
    )
    review = repository.add_step(
        study.study_id,
        "scope_review",
        Phase.DISCOVERY,
        ExecutorType.PROJECT_OWNER,
        depends_on=[scope.step_instance_id],
        expected_output="Owner decision on Scope Contract v1",
    )
    frozen_scope = repository.add_step(
        study.study_id,
        "freeze_scope_contract",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[review.step_instance_id, scope.step_instance_id],
        expected_output="Owner-approved frozen Scope Contract v1",
    )
    repository.add_step(
        study.study_id,
        "freeze_discovery_source_set",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[frozen_scope.step_instance_id, source_set.step_instance_id],
        task_group="retrieval_gateway",
        expected_output="Frozen discovery ResourceSet or explicit evidence gap",
    )
    return project.project_id, study.study_id


def run_project_discovery(
    source_root: str | Path,
    *,
    repository_root: str | Path,
    title: str | None = None,
    include_external: bool = True,
    identity: str | None = None,
) -> dict[str, Any]:
    repository = WorkflowRepository(repository_root)
    project_id, study_id = create_project_discovery_study(
        repository,
        source_root,
        title=title,
        include_external=include_external,
        identity=identity,
    )
    snapshot = PersistentDAGScheduler(
        repository, stage_one_handlers(), recover_interrupted=True
    ).run(study_id)
    scan_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "project_scan"
    )
    candidate_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "candidate_discovery"
    )
    recommendation_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "claim_recommendation"
    )
    portfolio_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "discovery_portfolio"
    )
    if any(
        item.status is not ExecutionStatus.SUCCEEDED
        for item in (
            scan_step,
            candidate_step,
            recommendation_step,
            portfolio_step,
        )
    ):
        return {"project_id": project_id, "study_id": study_id, "workflow": snapshot}
    scan = repository.load_step_result(study_id, scan_step.step_instance_id)
    candidates_payload = repository.load_step_result(
        study_id, candidate_step.step_instance_id
    )["candidates"]
    claim_discovery = repository.load_step_result(
        study_id, recommendation_step.step_instance_id
    )["claim_discovery"]
    discovery_portfolio = repository.load_step_result(
        study_id, portfolio_step.step_instance_id
    )["discovery_portfolio"]
    candidates = _models(candidates_payload, NoveltyCandidate)
    inspection = BundleInspection(
        source_root=scan["source_root"],
        resource_count=scan["resource_count"],
        excluded_count=scan["excluded_count"],
        candidates=candidates,
        recommended_track_id=candidates[0].track_id if candidates else None,
        claim_discovery=ClaimDiscoveryReport.model_validate(claim_discovery),
        discovery_portfolio=DiscoveryPortfolio.model_validate(
            discovery_portfolio
        ),
    )
    return {
        **inspection.model_dump(mode="json"),
        "project_id": project_id,
        "study_id": study_id,
        "workflow": snapshot,
    }


def approve_discovery_direction(
    repository: WorkflowRepository,
    study_id: str,
    direction_id: str,
    *,
    decided_by: str = "project_owner",
    reason: str | None = None,
) -> dict[str, Any]:
    """Select one portfolio direction, approve Scope v1, and resume the DAG."""

    portfolio_step = next(
        (
            item
            for item in repository.list_steps(study_id)
            if item.step_type == "discovery_portfolio"
            and item.status is ExecutionStatus.SUCCEEDED
        ),
        None,
    )
    if portfolio_step is None:
        raise ValueError("the Study has no completed Discovery Portfolio")
    portfolio = DiscoveryPortfolio.model_validate(
        repository.load_step_result(
            study_id, portfolio_step.step_instance_id
        )["discovery_portfolio"]
    )
    selected = next(
        (item for item in portfolio.directions if item.direction_id == direction_id),
        None,
    )
    if selected is None:
        raise ValueError("direction_id does not belong to the Discovery Portfolio")

    current = repository.latest_scope_contract(study_id)
    if current is None:
        raise ValueError("the Study has no draft Scope Contract")
    if current.status is ArtifactStatus.FROZEN:
        selected_id = str(current.field_diff.get("selected_direction_id", ""))
        if selected_id != direction_id:
            raise ValueError("a different frozen Scope requires Scope vNext")
        return {
            "scope_contract": current.model_dump(mode="json"),
            "workflow": repository.snapshot(study_id),
        }

    updated = current.model_copy(
        update={
            "direction": selected.title[:300],
            "research_question": selected.research_question[:1200],
            "scope_in": selected.scope_in,
            "scope_out": selected.scope_out,
            "candidate_contribution": selected.candidate_contribution[:2000],
            "project_resource_ids": selected.local_evidence_paths,
            "literature_set_id": (
                portfolio.resource_set_ids[0]
                if portfolio.resource_set_ids
                else None
            ),
            "field_diff": {
                **current.field_diff,
                "selected_direction_id": selected.direction_id,
                "selected_primary_track_id": selected.primary_track_id,
            },
            "created_by": decided_by,
        }
    )
    repository.save_scope_contract(updated)
    gate = next(
        (
            item
            for item in repository.list_gates(study_id)
            if item.gate_type is GateType.SCOPE_APPROVAL
            and item.subject_version == updated.version
        ),
        None,
    )
    if gate is None:
        raise ValueError("the Study has no Scope approval Gate")
    decided = repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by=decided_by,
        reason=reason or f"Selected Discovery direction {direction_id}.",
    )
    workflow = PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)
    frozen = repository.latest_scope_contract(study_id)
    return {
        "scope_contract": (
            frozen.model_dump(mode="json") if frozen is not None else None
        ),
        "gate": decided.model_dump(mode="json"),
        "workflow": workflow,
    }
