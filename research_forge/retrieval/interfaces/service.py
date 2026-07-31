from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

from ...claim_discovery import TrendSignal
from ...models import utc_now
from ..domain.external_models import AccessDecision, AccessMode
from ..domain.models import (
    BudgetUsage,
    ContractRef,
    EvidenceConflict,
    MetadataVerificationStatus,
    NetworkMode,
    PolicyOutcome,
    ProviderAttempt,
    ProviderErrorClass,
    QueryPlan,
    ResourceSet,
    ResourceSetStatus,
    ResourceSnapshot,
    ResourceType,
    ResourceUseBinding,
    RetrievalAuditEvent,
    RetrievalBudget,
    RetrievalCoverageReport,
    RetrievalPhase,
    RetrievalRequest,
    RetrievalRun,
    RetrievalStatus,
    retrieval_id,
)
from ..domain.repository import RetrievalRepository
from ..pipelines.resources import (
    deduplicate_resources,
    normalize_signals,
    verify_resources,
)
from ..pipelines.provenance import (
    build_access_decision,
    build_canonical_resource,
    build_identifier_graph,
    build_provider_records,
    build_resource_relations,
)
from ..policy.engine import PolicyEngine, RetrievalNetworkPolicy
from ..policy.content_rights import ContentRightsPolicy
from ..policy.sanitizer import QuerySanitizer
from ..providers.registry import ProviderRegistry, default_provider_registry
from ..providers.base import ProviderFailure, ProviderSearchResult


def _safe_provider_failure_reason(
    exc: Exception, classification: ProviderErrorClass
) -> str:
    """Return a bounded diagnostic without serializing provider responses."""

    if isinstance(exc, ProviderFailure):
        message = " ".join(str(exc).split())
        if message:
            return f"{classification.value}: {message[:180]}"
    return f"{classification.value}: {type(exc).__name__}"


@dataclass(frozen=True)
class RetrievalExecution:
    request: RetrievalRequest
    run: RetrievalRun
    resource_set: ResourceSet | None
    coverage: RetrievalCoverageReport | None

    def model_dump(self) -> dict[str, Any]:
        return {
            "request": self.request.model_dump(mode="json"),
            "run": self.run.model_dump(mode="json"),
            "resource_set": (
                self.resource_set.model_dump(mode="json") if self.resource_set else None
            ),
            "coverage": (
                self.coverage.model_dump(mode="json") if self.coverage else None
            ),
        }


@dataclass(frozen=True)
class AcquisitionExecution:
    request_id: str
    resource_id: str
    snapshot: ResourceSnapshot
    access_decision: AccessDecision
    artifact_id: str
    report_artifact_id: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "resource_id": self.resource_id,
            "snapshot": self.snapshot.model_dump(mode="json"),
            "access_decision": self.access_decision.model_dump(mode="json"),
            "artifact_id": self.artifact_id,
            "report_artifact_id": self.report_artifact_id,
        }


class RetrievalGateway:
    """Policy-controlled entry point for every Workflow v2 external read."""

    def __init__(
        self,
        workflow_root: str,
        *,
        providers: ProviderRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        sanitizer: QuerySanitizer | None = None,
        validate_workflow_context: bool = True,
    ) -> None:
        self.repository = RetrievalRepository(workflow_root)
        self.providers = providers or default_provider_registry()
        self.policy_engine = policy_engine or PolicyEngine()
        self.sanitizer = sanitizer or QuerySanitizer()
        self.validate_workflow_context = validate_workflow_context

    def acquire_open_access_document(
        self,
        *,
        request_id: str,
        resource_id: str,
    ) -> AcquisitionExecution:
        """Acquire one resolver-approved PDF under the originating policy budget."""

        request = self.repository.load_request(request_id)
        policy = self.repository.load_network_policy(request.network_policy_id)
        if "open_access" not in policy.allowed_providers or not policy.allow_full_text:
            raise PermissionError(
                "project policy does not authorize full-text acquisition"
            )
        if "GET" not in policy.allowed_http_methods:
            raise PermissionError("project policy does not authorize GET acquisition")
        source_run = self.repository.run_for_request(request_id)
        if source_run is None or source_run.execution_status not in {
            RetrievalStatus.SUCCEEDED,
            RetrievalStatus.DEGRADED,
        }:
            raise ValueError("open-access resolution must succeed before acquisition")
        resource = self.repository.load_resource(resource_id)
        if not {"open_access", "paper_search_mcp"}.intersection(resource.providers):
            raise ValueError(
                "resource was not resolved by an approved academic/OA provider"
            )
        if resource.metadata.get("access_status") != "open_access":
            raise PermissionError("resource has no resolver-approved open-access copy")
        source_url = str(
            resource.metadata.get("full_text_url") or resource.url or ""
        ).strip()
        if not source_url:
            raise ValueError("open-access resource has no full-text URL")
        adapter = self.providers.get("open_access")
        max_bytes = (
            min(
                value
                for value in (
                    request.budget.max_download_bytes,
                    policy.max_bytes,
                )
                if value > 0
            )
            if any(
                value > 0
                for value in (
                    request.budget.max_download_bytes,
                    policy.max_bytes,
                )
            )
            else 0
        )
        try:
            content = adapter.fetch_content(
                source_url,
                max_bytes=max_bytes,
                allow_proxy_fake_ip=policy.allow_proxy_fake_ip,
            )
        except ProviderFailure as exc:
            failure_reason = _safe_provider_failure_reason(
                exc, exc.classification
            )
            self.repository.append_audit(
                RetrievalAuditEvent(
                    event_id=retrieval_id(
                        "retrieval-event",
                        request_id,
                        resource_id,
                        exc.classification.value,
                        "full-text-acquisition-failed",
                    ),
                    request_id=request_id,
                    project_id=request.project_id,
                    study_id=request.study_id,
                    step_instance_id=request.step_instance_id,
                    purpose=request.purpose,
                    phase=request.phase,
                    policy_decision_id=source_run.policy_decision_id,
                    provider="open_access",
                    target_domain=urllib.parse.urlparse(source_url).hostname,
                    method="GET",
                    response_status="failed",
                    failure_reason=failure_reason,
                    budget_usage=BudgetUsage(),
                )
            )
            raise
        artifact_policy_context = {
            "network_policy_id": policy.policy_id,
            "request_id": request.request_id,
            "phase": request.phase.value,
        }
        artifact = self.repository.write_binary_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="open_access_full_text",
            content=content.content,
            producer="provider:open_access",
            extension="pdf",
            policy_context=artifact_policy_context,
        )
        decision = ContentRightsPolicy().decide(
            resource_id=resource_id,
            access_mode=AccessMode.OPEN_ACCESS,
            full_text_available=True,
            license_id=resource.license,
        )
        self.repository.save_access_decision(decision)
        snapshot = ResourceSnapshot(
            snapshot_id=retrieval_id(
                "snapshot",
                resource_id,
                artifact.content_hash,
                decision.decision_id,
            ),
            resource_id=resource_id,
            content_level="full_text",
            raw_response_artifact_id=artifact.artifact_id,
            normalized_content_artifact_id=artifact.artifact_id,
            provider="open_access",
            content_hash=artifact.content_hash,
            mime_type=content.mime_type,
            byte_size=len(content.content),
            license_status=resource.license or "unknown",
            access_status="retrieved",
            access_mode="open_access",
            model_processing_allowed=decision.model_processing_allowed,
        )
        self.repository.save_snapshot(snapshot)
        report = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="acquisition_report",
            value={
                "request_id": request_id,
                "resource_id": resource_id,
                "provider": "open_access",
                "source_url": content.source_url,
                "final_url": content.final_url,
                "content_hash": artifact.content_hash,
                "byte_size": len(content.content),
                "mime_type": content.mime_type,
                "license": resource.license,
                "snapshot_id": snapshot.snapshot_id,
                "access_decision_id": decision.decision_id,
                "model_processing_allowed": decision.model_processing_allowed,
                "proxy_fake_ip_approved": policy.allow_proxy_fake_ip,
            },
            producer="open_access_acquisition",
            input_artifact_ids=[artifact.artifact_id],
            policy_context=artifact_policy_context,
        )
        self.repository.append_audit(
            RetrievalAuditEvent(
                event_id=retrieval_id(
                    "retrieval-event",
                    request_id,
                    resource_id,
                    artifact.content_hash,
                    "full-text-acquired",
                ),
                request_id=request_id,
                project_id=request.project_id,
                study_id=request.study_id,
                step_instance_id=request.step_instance_id,
                purpose=request.purpose,
                phase=request.phase,
                policy_decision_id=source_run.policy_decision_id,
                provider="open_access",
                target_domain=urllib.parse.urlparse(content.final_url).hostname,
                method="GET",
                response_status="succeeded",
                budget_usage=BudgetUsage(download_bytes=len(content.content)),
                artifact_ids=[artifact.artifact_id, report.artifact_id],
                warnings=(
                    ["owner-approved proxy fake-IP routing"]
                    if policy.allow_proxy_fake_ip
                    else []
                ),
            )
        )
        return AcquisitionExecution(
            request_id=request_id,
            resource_id=resource_id,
            snapshot=snapshot,
            access_decision=decision,
            artifact_id=artifact.artifact_id,
            report_artifact_id=report.artifact_id,
        )

    def acquire_approved_experiment_resource(
        self,
        *,
        request_id: str,
        resource_id: str,
    ) -> AcquisitionExecution:
        """Acquire one exact contract-approved, revision-pinned code archive.

        This is an acquisition operation, not an open search.  The request
        must carry the experimentation phase, frozen Research Contract
        reference, exact resource type, owner-approved network policy and a
        positive byte budget.  Raw bytes are frozen before any consumer may
        inspect or normalize them.
        """

        request = self.repository.load_request(request_id)
        plan = self.repository.load_query_plan(request.query_plan_id)
        policy = self.repository.load_network_policy(request.network_policy_id)
        if request.phase is not RetrievalPhase.EXPERIMENTATION:
            raise PermissionError(
                "experiment resource acquisition requires experimentation phase"
            )
        if not any(
            item.contract_type == "research" for item in request.contract_refs
        ):
            raise PermissionError(
                "experiment resource acquisition requires Research Contract binding"
            )
        if plan.require_search_execution:
            raise PermissionError(
                "exact acquisition request must disable open search execution"
            )
        decision = self.policy_engine.evaluate(
            request, policy, sanitized=bool(plan.sanitized_queries)
        )
        self.repository.save_policy_decision(decision)
        if decision.outcome is not PolicyOutcome.ALLOW:
            raise PermissionError("; ".join(decision.reasons))
        resource = self.repository.load_resource(resource_id)
        if resource.resource_type not in request.requested_resource_types:
            raise PermissionError(
                "resource type is outside the acquisition request"
            )
        if resource.resource_type not in {
            ResourceType.CODE_REPOSITORY,
            ResourceType.CODE_RELEASE,
        }:
            raise ValueError(
                "bounded online acquisition currently supports pinned code archives"
            )
        if not policy.allow_repository_download:
            raise PermissionError(
                "project policy does not authorize repository downloads"
            )
        if "GET" not in policy.allowed_http_methods:
            raise PermissionError("project policy does not authorize GET")
        if "github" not in resource.providers:
            raise ValueError(
                "pinned code acquisition currently requires a GitHub resource"
            )
        if "github" not in decision.allowed_providers:
            raise PermissionError("GitHub provider is not authorized")
        repository_name = str(resource.repository or "").strip()
        commit = str(resource.commit or "").strip()
        if not repository_name or len(commit) != 40:
            raise ValueError(
                "GitHub acquisition requires repository and full commit SHA"
            )
        max_bytes = min(
            value
            for value in (
                request.budget.max_download_bytes,
                policy.max_bytes,
                decision.effective_budget.max_download_bytes,
            )
            if value > 0
        ) if any(
            value > 0
            for value in (
                request.budget.max_download_bytes,
                policy.max_bytes,
                decision.effective_budget.max_download_bytes,
            )
        ) else 0
        if max_bytes <= 0:
            raise PermissionError(
                "repository acquisition requires a positive byte budget"
            )
        existing = [
            item
            for item in self.repository.list_snapshots(resource_id)
            if item.content_level == "source_archive"
            and item.provider == "github"
        ]
        if existing:
            snapshot = existing[-1]
            artifact = self.repository.load_artifact(
                snapshot.raw_response_artifact_id
            )
            reports = [
                item
                for item in self.repository.list_artifacts(request.study_id)
                if item.kind == "experiment_resource_acquisition_report"
                and item.step_instance_id == request.step_instance_id
            ]
            if not reports:
                raise ValueError(
                    "existing source archive lacks its acquisition report"
                )
            access = [
                item
                for item in self.repository.list_access_decisions(resource_id)
                if item.full_text_available
            ]
            if not access:
                raise ValueError(
                    "existing source archive lacks its access decision"
                )
            return AcquisitionExecution(
                request_id=request_id,
                resource_id=resource_id,
                snapshot=snapshot,
                access_decision=access[-1],
                artifact_id=artifact.artifact_id,
                report_artifact_id=reports[-1].artifact_id,
            )
        adapter = self.providers.get("github")
        if adapter.http_method not in policy.allowed_http_methods:
            raise PermissionError(
                "provider HTTP method is not authorized by project policy"
            )
        if policy.allowed_domains and not adapter.domains.issubset(
            policy.allowed_domains
        ):
            raise PermissionError(
                "provider domain is not authorized by project policy"
            )
        try:
            content = adapter.download_approved_archive(
                full_name=repository_name,
                commit_sha=commit,
                max_bytes=max_bytes,
                authorization_approved=True,
            )
        except ProviderFailure as exc:
            failed_run = RetrievalRun(
                run_id=retrieval_id("retrieval-run", request.request_id),
                request_id=request_id,
                execution_status=RetrievalStatus.FAILED,
                policy_decision_id=decision.decision_id,
                started_at=utc_now(),
                completed_at=utc_now(),
                error_classification=exc.classification,
            )
            self.repository.save_run(failed_run)
            self._audit(
                request,
                failed_run,
                response_status="failed",
                failure_reason=_safe_provider_failure_reason(
                    exc, exc.classification
                ),
            )
            raise
        policy_context = {
            "network_policy_id": policy.policy_id,
            "request_id": request_id,
            "phase": request.phase.value,
            "contract_refs": [
                item.model_dump(mode="json") for item in request.contract_refs
            ],
        }
        artifact = self.repository.write_binary_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="approved_code_source_archive",
            content=content.content,
            producer="provider:github",
            extension="zip",
            policy_context=policy_context,
        )
        access_decision = ContentRightsPolicy().decide(
            resource_id=resource_id,
            access_mode=AccessMode.OPEN_ACCESS,
            full_text_available=True,
            license_id=resource.license,
        )
        if not access_decision.persistent_storage_allowed:
            raise PermissionError(
                "content-rights policy denied persistent archive storage"
            )
        self.repository.save_access_decision(access_decision)
        snapshot = ResourceSnapshot(
            snapshot_id=retrieval_id(
                "snapshot",
                resource_id,
                commit,
                artifact.content_hash,
                access_decision.decision_id,
            ),
            resource_id=resource_id,
            content_level="source_archive",
            raw_response_artifact_id=artifact.artifact_id,
            normalized_content_artifact_id=artifact.artifact_id,
            provider="github",
            content_hash=artifact.content_hash,
            mime_type=content.mime_type or "application/zip",
            byte_size=len(content.content),
            license_status=resource.license or "unknown",
            access_status="retrieved",
            access_mode="open_access",
            model_processing_allowed=(
                access_decision.model_processing_allowed
            ),
        )
        self.repository.save_snapshot(snapshot)
        report = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="experiment_resource_acquisition_report",
            value={
                "request_id": request_id,
                "resource_id": resource_id,
                "provider": "github",
                "repository": repository_name,
                "commit": commit,
                "snapshot_id": snapshot.snapshot_id,
                "content_hash": artifact.content_hash,
                "byte_size": len(content.content),
                "license": resource.license,
                "access_decision_id": access_decision.decision_id,
                "raw_bytes_frozen_before_consumption": True,
                "executed": False,
            },
            producer="experiment_resource_acquisition",
            input_artifact_ids=[artifact.artifact_id],
            policy_context=policy_context,
        )
        run = RetrievalRun(
            run_id=retrieval_id("retrieval-run", request.request_id),
            request_id=request_id,
            execution_status=RetrievalStatus.SUCCEEDED,
            policy_decision_id=decision.decision_id,
            started_at=utc_now(),
            completed_at=utc_now(),
            budget_usage=BudgetUsage(
                queries=0,
                results=1,
                download_bytes=len(content.content),
            ),
            output_artifact_ids=[
                artifact.artifact_id,
                report.artifact_id,
            ],
        )
        self.repository.save_run(run)
        self._audit(
            request,
            run,
            response_status="succeeded",
            warnings=[
                "exact pinned code acquisition; no open search executed",
                "downloaded source was not executed",
            ],
        )
        return AcquisitionExecution(
            request_id=request_id,
            resource_id=resource_id,
            snapshot=snapshot,
            access_decision=access_decision,
            artifact_id=artifact.artifact_id,
            report_artifact_id=report.artifact_id,
        )

    def acquire_approved_dataset(
        self,
        *,
        request_id: str,
        resource_id: str,
    ) -> AcquisitionExecution:
        """Freeze one exact OpenML dataset authorized by a Research Contract.

        Dataset discovery and scientific selection must already be complete.
        This method performs no open search: it downloads the exact versioned
        resource, freezes raw bytes, checks the provider checksum, and records
        the access/contract basis before Stage 3 may consume the artifact.
        """

        request = self.repository.load_request(request_id)
        plan = self.repository.load_query_plan(request.query_plan_id)
        policy = self.repository.load_network_policy(request.network_policy_id)
        if request.phase is not RetrievalPhase.EXPERIMENTATION:
            raise PermissionError(
                "dataset acquisition requires experimentation phase"
            )
        if request.purpose != "fetch_approved_dataset":
            raise PermissionError(
                "dataset acquisition requires fetch_approved_dataset purpose"
            )
        research_refs = [
            item
            for item in request.contract_refs
            if item.contract_type == "research"
        ]
        if not research_refs:
            raise PermissionError(
                "dataset acquisition requires Research Contract binding"
            )
        if not any(
            item.field in {"approved_resource_ids", "dataset", "resources"}
            for item in research_refs
        ):
            raise PermissionError(
                "Research Contract must explicitly bind its approved dataset field"
            )
        if plan.require_search_execution:
            raise PermissionError(
                "exact dataset acquisition must disable open search execution"
            )
        decision = self.policy_engine.evaluate(
            request, policy, sanitized=bool(plan.sanitized_queries)
        )
        self.repository.save_policy_decision(decision)
        if decision.outcome is not PolicyOutcome.ALLOW:
            raise PermissionError("; ".join(decision.reasons))
        if not policy.allow_dataset_download:
            raise PermissionError(
                "project policy does not authorize dataset downloads"
            )
        if "GET" not in policy.allowed_http_methods:
            raise PermissionError("project policy does not authorize GET")
        if "openml" not in decision.allowed_providers:
            raise PermissionError("OpenML provider is not authorized")

        resource = self.repository.load_resource(resource_id)
        if resource.resource_type is not ResourceType.DATASET:
            raise ValueError("approved dataset acquisition requires a dataset")
        if ResourceType.DATASET not in request.requested_resource_types:
            raise PermissionError(
                "dataset is outside the acquisition request resource types"
            )
        if "openml" not in resource.providers:
            raise ValueError(
                "approved dataset acquisition currently requires an OpenML resource"
            )
        if (
            resource.metadata_verification_status
            is not MetadataVerificationStatus.VERIFIED
        ):
            raise ValueError(
                "OpenML dataset metadata must be verified before acquisition"
            )
        dataset_identifier = str(resource.dataset_identifier or "").strip()
        version = str(resource.metadata.get("version") or "").strip()
        source_url = str(resource.metadata.get("download_url") or "").strip()
        expected_md5 = str(resource.metadata.get("md5_checksum") or "").strip().lower()
        if not dataset_identifier.startswith("openml-dataset:") or not version:
            raise ValueError("OpenML dataset identity must be version-pinned")
        if not source_url:
            raise ValueError("OpenML dataset has no official download URL")
        if not __import__("re").fullmatch(r"[0-9a-f]{32}", expected_md5):
            raise ValueError("OpenML dataset requires an authoritative MD5 checksum")
        if plan.sanitized_queries != [dataset_identifier]:
            raise PermissionError(
                "exact acquisition query must equal the versioned dataset identity"
            )

        adapter = self.providers.get("openml")
        if adapter.http_method not in policy.allowed_http_methods:
            raise PermissionError(
                "provider HTTP method is not authorized by project policy"
            )
        if policy.allowed_domains and not adapter.domains.issubset(
            policy.allowed_domains
        ):
            raise PermissionError(
                "OpenML provider domains are not authorized by project policy"
            )
        max_bytes = (
            min(
                value
                for value in (
                    request.budget.max_download_bytes,
                    policy.max_bytes,
                    decision.effective_budget.max_download_bytes,
                )
                if value > 0
            )
            if any(
                value > 0
                for value in (
                    request.budget.max_download_bytes,
                    policy.max_bytes,
                    decision.effective_budget.max_download_bytes,
                )
            )
            else 0
        )
        if max_bytes <= 0:
            raise PermissionError(
                "dataset acquisition requires a positive byte budget"
            )

        # A contract-bound exact resource request and an owner-approved
        # dataset-download policy authorize local model processing.  They do
        # not imply redistribution or training-data rights.
        access_decision = ContentRightsPolicy().decide(
            resource_id=resource_id,
            access_mode=AccessMode.OPEN_ACCESS,
            full_text_available=True,
            license_id=resource.license,
            model_processing_explicitly_allowed=True,
        )
        if not access_decision.persistent_storage_allowed:
            raise PermissionError(
                "content-rights policy denied persistent dataset storage"
            )
        self.repository.save_access_decision(access_decision)
        policy_context = {
            "network_policy_id": policy.policy_id,
            "request_id": request_id,
            "phase": request.phase.value,
            "contract_refs": [
                item.model_dump(mode="json") for item in request.contract_refs
            ],
            "dataset_identifier": dataset_identifier,
            "dataset_version": version,
        }

        existing = [
            item
            for item in self.repository.list_snapshots(resource_id)
            if item.content_level == "dataset_file"
            and item.provider == "openml"
        ]
        reused = bool(existing)
        if reused:
            snapshot = existing[-1]
            artifact = self.repository.load_artifact(
                snapshot.raw_response_artifact_id
            )
            artifact_path = __import__("pathlib").Path(artifact.path)
            sha256 = hashlib.sha256()
            md5 = hashlib.md5(usedforsecurity=False)
            with artifact_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    sha256.update(chunk)
                    md5.update(chunk)
            if (
                sha256.hexdigest() != artifact.content_hash
                or artifact.content_hash != snapshot.content_hash
                or md5.hexdigest() != expected_md5
            ):
                raise ProviderFailure(
                    "frozen OpenML dataset failed integrity revalidation",
                    ProviderErrorClass.METADATA_CONFLICT,
                )
            byte_size = artifact_path.stat().st_size
            final_url = source_url
            mime_type = snapshot.mime_type
        else:
            try:
                content = adapter.fetch_content(
                    source_url,
                    max_bytes=max_bytes,
                    allow_proxy_fake_ip=policy.allow_proxy_fake_ip,
                )
            except ProviderFailure as exc:
                failed_run = RetrievalRun(
                    run_id=retrieval_id("retrieval-run", request.request_id),
                    request_id=request_id,
                    execution_status=RetrievalStatus.FAILED,
                    policy_decision_id=decision.decision_id,
                    started_at=utc_now(),
                    completed_at=utc_now(),
                    error_classification=exc.classification,
                )
                self.repository.save_run(failed_run)
                self._audit(
                    request,
                    failed_run,
                    response_status="failed",
                    failure_reason=_safe_provider_failure_reason(
                        exc, exc.classification
                    ),
                )
                raise
            observed_md5 = __import__("hashlib").md5(
                content.content, usedforsecurity=False
            ).hexdigest()
            if observed_md5 != expected_md5:
                failed_run = RetrievalRun(
                    run_id=retrieval_id("retrieval-run", request.request_id),
                    request_id=request_id,
                    execution_status=RetrievalStatus.FAILED,
                    policy_decision_id=decision.decision_id,
                    started_at=utc_now(),
                    completed_at=utc_now(),
                    error_classification=ProviderErrorClass.METADATA_CONFLICT,
                )
                self.repository.save_run(failed_run)
                self._audit(
                    request,
                    failed_run,
                    response_status="failed",
                    failure_reason=(
                        "metadata_conflict: downloaded OpenML bytes do not match "
                        "the frozen provider checksum"
                    ),
                )
                raise ProviderFailure(
                    "downloaded OpenML dataset checksum mismatch",
                    ProviderErrorClass.METADATA_CONFLICT,
                )
            path_suffix = urllib.parse.urlparse(content.final_url).path.rsplit(".", 1)
            extension = (
                path_suffix[-1].casefold()
                if len(path_suffix) == 2
                and path_suffix[-1].casefold() in {"arff", "csv", "json", "parquet", "pq"}
                else "bin"
            )
            artifact = self.repository.write_binary_artifact(
                project_id=request.project_id,
                study_id=request.study_id,
                step_instance_id=request.step_instance_id,
                kind="approved_openml_dataset",
                content=content.content,
                producer="provider:openml",
                extension=extension,
                policy_context=policy_context,
            )
            byte_size = len(content.content)
            final_url = content.final_url
            mime_type = content.mime_type or "application/octet-stream"
            snapshot = ResourceSnapshot(
                snapshot_id=retrieval_id(
                    "snapshot",
                    resource_id,
                    dataset_identifier,
                    version,
                    artifact.content_hash,
                    access_decision.decision_id,
                ),
                resource_id=resource_id,
                content_level="dataset_file",
                raw_response_artifact_id=artifact.artifact_id,
                normalized_content_artifact_id=artifact.artifact_id,
                provider="openml",
                content_hash=artifact.content_hash,
                mime_type=mime_type,
                byte_size=byte_size,
                license_status=resource.license or "unknown",
                access_status="retrieved",
                access_mode="open_access",
                model_processing_allowed=access_decision.model_processing_allowed,
            )
            self.repository.save_snapshot(snapshot)

        report = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="openml_dataset_acquisition_report",
            value={
                "request_id": request_id,
                "resource_id": resource_id,
                "provider": "openml",
                "dataset_identifier": dataset_identifier,
                "version": version,
                "source_url": source_url,
                "final_url": final_url,
                "snapshot_id": snapshot.snapshot_id,
                "content_hash": artifact.content_hash,
                "provider_md5": expected_md5,
                "provider_md5_verified": True,
                "byte_size": byte_size,
                "mime_type": mime_type,
                "license": resource.license,
                "access_decision_id": access_decision.decision_id,
                "raw_bytes_frozen_before_consumption": True,
                "model_processing_basis": (
                    "exact Research Contract binding plus owner-approved local "
                    "dataset acquisition policy"
                ),
                "redistribution_authorized": access_decision.redistribution_allowed,
                "executed": False,
                "reused_existing_snapshot": reused,
            },
            producer="openml_dataset_acquisition",
            input_artifact_ids=[artifact.artifact_id],
            policy_context=policy_context,
        )
        run = RetrievalRun(
            run_id=retrieval_id("retrieval-run", request.request_id),
            request_id=request_id,
            execution_status=RetrievalStatus.SUCCEEDED,
            policy_decision_id=decision.decision_id,
            started_at=utc_now(),
            completed_at=utc_now(),
            budget_usage=BudgetUsage(
                queries=0,
                results=1,
                download_bytes=0 if reused else byte_size,
            ),
            output_artifact_ids=[artifact.artifact_id, report.artifact_id],
        )
        self.repository.save_run(run)
        self._audit(
            request,
            run,
            response_status="succeeded",
            warnings=[
                "exact versioned OpenML dataset acquisition; no open search executed",
                "downloaded dataset was frozen but not executed",
                *( ["reused existing frozen dataset snapshot"] if reused else [] ),
            ],
        )
        return AcquisitionExecution(
            request_id=request_id,
            resource_id=resource_id,
            snapshot=snapshot,
            access_decision=access_decision,
            artifact_id=artifact.artifact_id,
            report_artifact_id=report.artifact_id,
        )

    def get_policy(self, project_id: str) -> RetrievalNetworkPolicy:
        policy = self.repository.policy_for_project(project_id)
        if policy is not None:
            self._sync_project_policy(project_id, policy)
            return policy
        policy = RetrievalNetworkPolicy.offline(project_id)
        self.repository.save_network_policy(policy)
        self.repository.bind_project_policy(project_id, policy.policy_id)
        self._sync_project_policy(project_id, policy)
        return policy

    def set_policy(
        self, project_id: str, policy: RetrievalNetworkPolicy
    ) -> RetrievalNetworkPolicy:
        if policy.mode is not NetworkMode.OFFLINE and not policy.approved_by:
            raise ValueError("network-enabling policy requires approval metadata")
        self.repository.save_network_policy(policy)
        self.repository.bind_project_policy(project_id, policy.policy_id)
        self._sync_project_policy(project_id, policy)
        return policy

    def _sync_project_policy(
        self, project_id: str, policy: RetrievalNetworkPolicy
    ) -> None:
        if not self.validate_workflow_context:
            return
        from ...workflow_domain import NetworkPolicy, WorkflowRepository

        workflow = WorkflowRepository(self.repository.root.parent)
        project = workflow.load_project(project_id)
        projection = NetworkPolicy(
            mode=policy.mode.value,
            retrieval_policy_id=policy.policy_id,
            network_enabled=policy.mode is not NetworkMode.OFFLINE,
            public_read_requests_automatic=policy.mode
            in {
                NetworkMode.ACADEMIC_READ,
                NetworkMode.PUBLIC_WEB_READ,
                NetworkMode.PUBLIC_RESEARCH,
                NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION,
            },
            external_writes_require_approval=True,
            allowed_domains=sorted(policy.allowed_domains),
            budget_limit=policy.max_cost or None,
        )
        if project.network_policy != projection:
            workflow.save_project(
                project.model_copy(update={"network_policy": projection})
            )

    def plan(
        self,
        *,
        project_id: str,
        study_id: str,
        phase: RetrievalPhase,
        step_instance_id: str,
        purpose: str,
        queries: list[str],
        providers: list[str],
        resource_types: list[ResourceType],
        usage_role: str,
        budget: RetrievalBudget,
        idempotency_key: str,
        contract_refs: list[ContractRef] | None = None,
        internal_identifiers: list[str] | None = None,
        research_need: str | None = None,
        freshness: Literal["cache_only", "live"] = "cache_only",
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        require_search_execution: bool = True,
    ) -> RetrievalRequest:
        effective_internal_identifiers = list(internal_identifiers or [])
        if self.validate_workflow_context:
            from pathlib import Path

            from ...workflow_domain import Phase, WorkflowRepository

            workflow = WorkflowRepository(self.repository.root.parent)
            project = workflow.load_project(project_id)
            study = workflow.load_study(study_id)
            step = workflow.load_step(study_id, step_instance_id)
            if study.project_id != project.project_id:
                raise ValueError("Study does not belong to the declared Project")
            expected_phase = {
                RetrievalPhase.DISCOVERY: Phase.DISCOVERY,
                RetrievalPhase.PROTOCOL: Phase.PROTOCOL,
                RetrievalPhase.EXPERIMENTATION: Phase.EXPERIMENT,
                RetrievalPhase.SYNTHESIS: Phase.PAPER,
            }.get(phase)
            if expected_phase is not None and step.phase is not expected_phase:
                raise ValueError("retrieval phase does not match StepInstance phase")
            effective_internal_identifiers.append(project.title)
            if project.source_root:
                effective_internal_identifiers.append(Path(project.source_root).name)
        sanitized = self.sanitizer.sanitize(
            queries,
            internal_identifiers=effective_internal_identifiers,
        )
        sanitized_need = self.sanitizer.sanitize(
            [research_need or purpose],
            internal_identifiers=effective_internal_identifiers,
        ).queries[0]
        existing = self.repository.request_for_idempotency(idempotency_key)
        if existing is not None:
            existing_plan = self.repository.load_query_plan(existing.query_plan_id)
            same_intent = (
                existing.project_id == project_id
                and existing.study_id == study_id
                and existing.phase is phase
                and existing.step_instance_id == step_instance_id
                and existing.purpose == purpose
                and existing.requested_providers == list(dict.fromkeys(providers))
                and existing.requested_resource_types == resource_types
                and existing.requested_usage_role == usage_role
                and existing_plan.raw_query_digest == sanitized.report.raw_query_digest
                and existing_plan.freshness == freshness
                and existing_plan.allowed_domains == list(allowed_domains or [])
                and existing_plan.blocked_domains == list(blocked_domains or [])
            )
            if not same_intent:
                raise ValueError(
                    "idempotency key was reused for a different retrieval intent"
                )
            return existing
        policy = self.get_policy(project_id)
        plan_id = retrieval_id(
            "query-plan",
            project_id,
            study_id,
            phase.value,
            purpose,
            sanitized.report.raw_query_digest,
            freshness,
            *list(dict.fromkeys(providers)),
            *sorted(item.value for item in resource_types),
            *list(dict.fromkeys(allowed_domains or [])),
            *list(dict.fromkeys(blocked_domains or [])),
            *sanitized.queries,
        )
        plan = QueryPlan(
            query_plan_id=plan_id,
            project_id=project_id,
            study_id=study_id,
            phase=phase,
            research_need=sanitized_need,
            purpose=purpose,
            raw_query_digest=sanitized.report.raw_query_digest,
            queries=sanitized.queries,
            sanitized_queries=sanitized.queries,
            providers=list(dict.fromkeys(providers)),
            freshness=freshness,
            allowed_domains=list(dict.fromkeys(allowed_domains or [])),
            blocked_domains=list(dict.fromkeys(blocked_domains or [])),
            require_search_execution=require_search_execution,
            resource_types=resource_types,
            budget=budget,
        )
        request = RetrievalRequest(
            request_id=retrieval_id("retrieval-request", idempotency_key, plan_id),
            project_id=project_id,
            study_id=study_id,
            phase=phase,
            step_instance_id=step_instance_id,
            purpose=purpose,
            query_plan_id=plan_id,
            network_policy_id=policy.policy_id,
            contract_refs=contract_refs or [],
            requested_providers=plan.providers,
            requested_resource_types=resource_types,
            requested_usage_role=usage_role,
            budget=budget,
            idempotency_key=idempotency_key,
        )
        self.repository.save_redaction_report(sanitized.report)
        self.repository.write_artifact(
            project_id=project_id,
            study_id=study_id,
            step_instance_id=step_instance_id,
            kind="redaction_report",
            value=sanitized.report.model_dump(mode="json"),
            producer="query_sanitizer",
            policy_context={
                "network_policy_id": policy.policy_id,
                "phase": phase.value,
            },
        )
        self.repository.write_artifact(
            project_id=project_id,
            study_id=study_id,
            step_instance_id=step_instance_id,
            kind="sanitized_queries",
            value={"queries": sanitized.queries},
            producer="query_sanitizer",
            policy_context={
                "network_policy_id": policy.policy_id,
                "phase": phase.value,
            },
        )
        self.repository.save_query_plan(plan)
        self.repository.write_artifact(
            project_id=project_id,
            study_id=study_id,
            step_instance_id=step_instance_id,
            kind="query_plan",
            value=plan.model_dump(mode="json"),
            producer="retrieval_gateway",
            policy_context={
                "network_policy_id": policy.policy_id,
                "phase": phase.value,
            },
        )
        # ``query_plan`` is the legacy artifact name.  External Research V1
        # names the public/auditable contract ``retrieval_plan.json``.  Keep
        # both immutable records so old readers continue to work while new
        # release audits can rely on the specified filename.
        self.repository.write_artifact(
            project_id=project_id,
            study_id=study_id,
            step_instance_id=step_instance_id,
            kind="retrieval_plan",
            value=plan.model_dump(mode="json"),
            producer="retrieval_gateway",
            policy_context={
                "network_policy_id": policy.policy_id,
                "phase": phase.value,
            },
        )
        self.repository.save_request(request)
        return request

    def run(
        self,
        request_id: str,
        *,
        target_type: str = "study_direction",
        target_id: str = "candidate-direction",
        target_field: str = "background_sources",
        relation: str = "contextualizes",
    ) -> RetrievalExecution:
        request = self.repository.load_request(request_id)
        run_id = retrieval_id("retrieval-run", request.request_id)
        try:
            existing_run = self.repository.load_run(run_id)
        except FileNotFoundError:
            existing_run = None
        if existing_run and existing_run.execution_status in {
            RetrievalStatus.SUCCEEDED,
            RetrievalStatus.DEGRADED,
            RetrievalStatus.BLOCKED,
            RetrievalStatus.FAILED,
            RetrievalStatus.CANCELLED,
        }:
            coverage = (
                self.repository.load_coverage(existing_run.coverage_report_id)
                if existing_run.coverage_report_id
                else None
            )
            sets = [
                item
                for item in self.repository.list_resource_sets(request.study_id)
                if item.query_plan_id == request.query_plan_id
            ]
            return RetrievalExecution(
                request, existing_run, sets[-1] if sets else None, coverage
            )

        plan = self.repository.load_query_plan(request.query_plan_id)
        policy = self.repository.load_network_policy(request.network_policy_id)
        artifact_policy_context = {
            "network_policy_id": policy.policy_id,
            "request_id": request.request_id,
            "phase": request.phase.value,
        }
        decision = self.policy_engine.evaluate(
            request, policy, sanitized=bool(plan.sanitized_queries)
        )
        self.repository.save_policy_decision(decision)
        decision_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="policy_decision",
            value=decision.model_dump(mode="json"),
            producer="retrieval_policy_engine",
            policy_context=artifact_policy_context,
        )
        route_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="provider_route",
            value={
                "request_id": request.request_id,
                "phase": request.phase.value,
                "purpose": request.purpose,
                "requested_providers": plan.providers,
                "allowed_providers": decision.allowed_providers,
                "denied_providers": [
                    item
                    for item in plan.providers
                    if item not in decision.allowed_providers
                ],
                "freshness": plan.freshness,
                "allowed_domains": plan.allowed_domains,
                "blocked_domains": plan.blocked_domains,
            },
            producer="deterministic_query_router",
            input_artifact_ids=[decision_artifact.artifact_id],
            policy_context=artifact_policy_context,
        )
        explicit_retry_count = existing_run.retry_count if existing_run else 0
        run = RetrievalRun(
            run_id=run_id,
            request_id=request.request_id,
            execution_status=(
                RetrievalStatus.BLOCKED
                if decision.outcome is not PolicyOutcome.ALLOW
                else RetrievalStatus.RUNNING
            ),
            policy_decision_id=decision.decision_id,
            started_at=utc_now(),
            retry_count=explicit_retry_count,
            output_artifact_ids=[
                decision_artifact.artifact_id,
                route_artifact.artifact_id,
            ],
        )
        if decision.outcome is not PolicyOutcome.ALLOW:
            run = run.model_copy(
                update={
                    "completed_at": utc_now(),
                    "error_classification": ProviderErrorClass.POLICY_DENIED,
                }
            )
            self.repository.save_run(run)
            self._audit(
                request,
                run,
                response_status="blocked",
                failure_reason="; ".join(decision.reasons),
            )
            return RetrievalExecution(request, run, None, None)

        started = time.perf_counter()
        all_signals: list[TrendSignal] = []
        attempts: list[ProviderAttempt] = []
        raw_artifacts = []
        provider_raw_artifacts: dict[str, list[Any]] = {}
        warnings: list[str] = []
        effective_queries = plan.sanitized_queries[
            : decision.effective_budget.max_queries or None
        ]
        provider_plan = plan.model_copy(
            update={
                "queries": effective_queries,
                "sanitized_queries": effective_queries,
            }
        )
        queries_used = len(effective_queries)
        for provider_id in plan.providers:
            if provider_id not in decision.allowed_providers:
                warnings.append(f"{provider_id}: policy denied")
                attempts.append(
                    ProviderAttempt(
                        provider=provider_id,
                        attempt=1,
                        status=RetrievalStatus.FAILED,
                        error_classification=ProviderErrorClass.POLICY_DENIED,
                        completed_at=utc_now(),
                    )
                )
                continue
            adapter = self.providers.get(provider_id)
            if adapter.http_method not in policy.allowed_http_methods:
                warnings.append(
                    f"{provider_id}: HTTP method {adapter.http_method} denied"
                )
                attempts.append(
                    ProviderAttempt(
                        provider=provider_id,
                        attempt=1,
                        status=RetrievalStatus.FAILED,
                        error_classification=ProviderErrorClass.POLICY_DENIED,
                        completed_at=utc_now(),
                    )
                )
                continue
            if policy.allowed_domains and not adapter.domains.issubset(
                policy.allowed_domains
            ):
                warnings.append(f"{provider_id}: target domain denied")
                attempts.append(
                    ProviderAttempt(
                        provider=provider_id,
                        attempt=1,
                        status=RetrievalStatus.FAILED,
                        error_classification=ProviderErrorClass.POLICY_DENIED,
                        completed_at=utc_now(),
                    )
                )
                continue
            cache_key = retrieval_id(
                "provider-cache",
                provider_id,
                request.phase.value,
                request.purpose,
                *effective_queries,
                *sorted(item.value for item in request.requested_resource_types),
                *sorted(plan.allowed_domains),
                *sorted(plan.blocked_domains),
            )
            cached = (
                self.repository.load_provider_cache(cache_key)
                if plan.freshness == "cache_only"
                else None
            )
            if plan.freshness == "cache_only" and cached is None:
                warnings.append(f"{provider_id}: cache miss")
                attempts.append(
                    ProviderAttempt(
                        provider=provider_id,
                        attempt=1,
                        status=RetrievalStatus.FAILED,
                        error_classification=ProviderErrorClass.RESOURCE_NOT_FOUND,
                        completed_at=utc_now(),
                    )
                )
                self.repository.append_audit(
                    RetrievalAuditEvent(
                        event_id=retrieval_id(
                            "retrieval-event",
                            request.request_id,
                            provider_id,
                            "cache-miss",
                        ),
                        request_id=request.request_id,
                        project_id=request.project_id,
                        study_id=request.study_id,
                        step_instance_id=request.step_instance_id,
                        purpose=request.purpose,
                        phase=request.phase,
                        policy_decision_id=decision.decision_id,
                        provider=provider_id,
                        method="CACHE",
                        sanitized_query=" | ".join(effective_queries)[:2000],
                        response_status="cache_miss",
                        failure_reason="resource_not_found",
                    )
                )
                continue
            max_attempts = 1 if cached is not None else 4
            for attempt_number in range(1, max_attempts + 1):
                try:
                    if cached is not None:
                        result = ProviderSearchResult(
                            provider=provider_id,
                            raw_payloads=[
                                json.loads(
                                    __import__("pathlib")
                                    .Path(
                                        self.repository.load_artifact(artifact_id).path
                                    )
                                    .read_text(encoding="utf-8")
                                )
                                for artifact_id in cached.get("raw_artifact_ids", [])
                            ],
                            signals=[
                                TrendSignal.model_validate(item)
                                for item in cached.get("signals", [])
                            ],
                            query_records=[
                                dict(item)
                                for item in cached.get("query_records", [])
                                if isinstance(item, dict)
                            ],
                            source_records=[
                                dict(item)
                                for item in cached.get("source_records", [])
                                if isinstance(item, dict)
                            ],
                            citation_records=[
                                dict(item)
                                for item in cached.get("citation_records", [])
                                if isinstance(item, dict)
                            ],
                            domains=[str(item) for item in cached.get("domains", [])],
                            http_method="CACHE",
                        )
                    else:
                        result = adapter.search(provider_plan)
                    attempt_artifacts = []
                    if cached is not None:
                        for artifact_id in cached.get("raw_artifact_ids", []):
                            artifact = self.repository.load_artifact(artifact_id)
                            raw_artifacts.append(artifact)
                            provider_raw_artifacts.setdefault(provider_id, []).append(
                                artifact
                            )
                            attempt_artifacts.append(artifact.artifact_id)
                        attempt_artifacts.extend(
                            str(item)
                            for item in cached.get("record_artifact_ids", [])
                        )
                    else:
                        for index, payload in enumerate(result.raw_payloads):
                            artifact = self.repository.write_artifact(
                                project_id=request.project_id,
                                study_id=request.study_id,
                                step_instance_id=request.step_instance_id,
                                kind=(f"provider_raw_response_{provider_id}_{index}"),
                                value=payload,
                                producer=f"provider:{provider_id}",
                                input_artifact_ids=[decision_artifact.artifact_id],
                            )
                            raw_artifacts.append(artifact)
                            provider_raw_artifacts.setdefault(provider_id, []).append(
                                artifact
                            )
                            attempt_artifacts.append(artifact.artifact_id)
                        record_artifact_ids: list[str] = []
                        for kind, record_rows in (
                            ("provider_query_records", result.query_records),
                            ("provider_source_records", result.source_records),
                            ("provider_citation_records", result.citation_records),
                        ):
                            if not record_rows:
                                continue
                            record_artifact = self.repository.write_artifact(
                                project_id=request.project_id,
                                study_id=request.study_id,
                                step_instance_id=request.step_instance_id,
                                kind=f"{kind}_{provider_id}",
                                value=record_rows,
                                producer=f"provider:{provider_id}",
                                input_artifact_ids=list(attempt_artifacts),
                                policy_context=artifact_policy_context,
                            )
                            record_artifact_ids.append(record_artifact.artifact_id)
                        attempt_artifacts.extend(record_artifact_ids)
                    if cached is None:
                        self.repository.save_provider_cache(
                            cache_key,
                            {
                                "provider": provider_id,
                                "raw_artifact_ids": attempt_artifacts,
                                "signals": [
                                    item.model_dump(mode="json")
                                    for item in result.signals
                                ],
                                "query_records": result.query_records,
                                "source_records": result.source_records,
                                "citation_records": result.citation_records,
                                "record_artifact_ids": record_artifact_ids,
                                "domains": result.domains,
                                "source_request_id": request.request_id,
                                "created_at": utc_now(),
                            },
                        )
                    remaining = decision.effective_budget.max_results - len(all_signals)
                    if decision.effective_budget.max_results > 0:
                        all_signals.extend(result.signals[: max(0, remaining)])
                    else:
                        all_signals.extend(result.signals)
                    attempts.append(
                        ProviderAttempt(
                            provider=provider_id,
                            attempt=attempt_number,
                            status=RetrievalStatus.SUCCEEDED,
                            response_artifact_ids=attempt_artifacts,
                            result_count=len(result.signals),
                            completed_at=utc_now(),
                        )
                    )
                    self.repository.append_audit(
                        RetrievalAuditEvent(
                            event_id=retrieval_id(
                                "retrieval-event",
                                request.request_id,
                                provider_id,
                                attempt_number,
                                "succeeded",
                            ),
                            request_id=request.request_id,
                            project_id=request.project_id,
                            study_id=request.study_id,
                            step_instance_id=request.step_instance_id,
                            purpose=request.purpose,
                            phase=request.phase,
                            policy_decision_id=decision.decision_id,
                            provider=provider_id,
                            target_domain=",".join(sorted(adapter.domains)),
                            method=(
                                "CACHE" if cached is not None else adapter.http_method
                            ),
                            sanitized_query=" | ".join(effective_queries)[:2000],
                            response_status="succeeded",
                            retry_count=attempt_number - 1,
                            budget_usage=BudgetUsage(
                                queries=len(effective_queries),
                                results=len(result.signals),
                            ),
                            artifact_ids=attempt_artifacts,
                        )
                    )
                    break
                except Exception as exc:
                    classification = adapter.classify_error(exc)
                    safe_failure_reason = _safe_provider_failure_reason(
                        exc, classification
                    )
                    attempts.append(
                        ProviderAttempt(
                            provider=provider_id,
                            attempt=attempt_number,
                            status=RetrievalStatus.FAILED,
                            error_classification=classification,
                            completed_at=utc_now(),
                        )
                    )
                    self.repository.append_audit(
                        RetrievalAuditEvent(
                            event_id=retrieval_id(
                                "retrieval-event",
                                request.request_id,
                                provider_id,
                                attempt_number,
                                classification.value,
                            ),
                            request_id=request.request_id,
                            project_id=request.project_id,
                            study_id=request.study_id,
                            step_instance_id=request.step_instance_id,
                            purpose=request.purpose,
                            phase=request.phase,
                            policy_decision_id=decision.decision_id,
                            provider=provider_id,
                            target_domain=",".join(sorted(adapter.domains)),
                            method=adapter.http_method,
                            sanitized_query=" | ".join(effective_queries)[:2000],
                            response_status="failed",
                            retry_count=attempt_number - 1,
                            failure_reason=safe_failure_reason,
                        )
                    )
                    retryable = classification in {
                        ProviderErrorClass.TRANSIENT_NETWORK_ERROR,
                        ProviderErrorClass.RATE_LIMIT,
                    }
                    if not retryable or attempt_number == max_attempts:
                        warnings.append(f"{provider_id}: {safe_failure_reason}")
                        break
                    time.sleep(0.01 * (2 ** (attempt_number - 1)))

        raw_bytes = sum(
            int(__import__("pathlib").Path(item.path).stat().st_size)
            for item in raw_artifacts
        )
        provider_output_artifact_ids = list(
            dict.fromkeys(
                artifact_id
                for attempt in attempts
                for artifact_id in attempt.response_artifact_ids
            )
        )
        if (
            decision.effective_budget.max_download_bytes > 0
            and raw_bytes > decision.effective_budget.max_download_bytes
        ):
            coverage = RetrievalCoverageReport(
                coverage_report_id=retrieval_id(
                    "coverage", run_id, explicit_retry_count
                ),
                run_id=run_id,
                providers_used=sorted(
                    {
                        item.provider
                        for item in attempts
                        if item.status is RetrievalStatus.SUCCEEDED
                    }
                ),
                executed_queries=effective_queries,
                raw_result_count=len(all_signals),
                deduplicated_result_count=0,
                verified_result_count=0,
                known_blind_spots=["download byte budget exceeded"],
                retrieval_duration_ms=round((time.perf_counter() - started) * 1000),
                prohibited_claims=[
                    "Do not interpret a budget-truncated retrieval as complete.",
                    "Do not infer evidence absence from missing normalized results.",
                ],
            )
            self.repository.save_coverage(coverage)
            coverage_artifact = self.repository.write_artifact(
                project_id=request.project_id,
                study_id=request.study_id,
                step_instance_id=request.step_instance_id,
                kind="coverage_report",
                value=coverage.model_dump(mode="json"),
                producer="coverage_reporter",
                input_artifact_ids=[item.artifact_id for item in raw_artifacts],
            )
            run = run.model_copy(
                update={
                    "execution_status": RetrievalStatus.BLOCKED,
                    "provider_attempts": attempts,
                    "completed_at": utc_now(),
                    "error_classification": ProviderErrorClass.POLICY_DENIED,
                    "budget_usage": BudgetUsage(
                        queries=queries_used,
                        results=len(all_signals),
                        download_bytes=raw_bytes,
                    ),
                    "output_artifact_ids": [
                        decision_artifact.artifact_id,
                        route_artifact.artifact_id,
                        *provider_output_artifact_ids,
                        coverage_artifact.artifact_id,
                    ],
                    "coverage_report_id": coverage.coverage_report_id,
                }
            )
            self.repository.save_run(run)
            self._audit(
                request,
                run,
                response_status="blocked",
                failure_reason="download byte budget exceeded",
            )
            return RetrievalExecution(request, run, None, coverage)

        normalized = normalize_signals(all_signals)
        normalized_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="normalized_resources",
            value=[item.model_dump(mode="json") for item in normalized],
            producer="resource_normalizer",
            input_artifact_ids=[item.artifact_id for item in raw_artifacts],
        )
        deduplicated = deduplicate_resources(normalized)
        dedup_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="deduplication_report",
            value={
                "lineage": deduplicated.merged_resource_ids,
                "conflicts": deduplicated.conflicts,
                "input_count": len(normalized),
                "output_count": len(deduplicated.resources),
            },
            producer="resource_deduplicator",
            input_artifact_ids=[normalized_artifact.artifact_id],
        )
        verified = verify_resources(deduplicated.resources)
        verification_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="verification_report",
            value={
                "warnings": verified.warnings,
                "resources": [
                    item.model_dump(mode="json") for item in verified.resources
                ],
            },
            producer="metadata_verifier",
            input_artifact_ids=[dedup_artifact.artifact_id],
        )
        metadata_verification_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="metadata_verification_report",
            value={
                "warnings": verified.warnings,
                "resources": [
                    item.model_dump(mode="json") for item in verified.resources
                ],
            },
            producer="metadata_verifier",
            input_artifact_ids=[dedup_artifact.artifact_id],
        )

        canonical_resources = []
        provider_records = []
        access_decisions = []
        for resource in verified.resources:
            records = build_provider_records(
                resource,
                raw_artifacts=provider_raw_artifacts,
            )
            canonical = build_canonical_resource(resource, records)
            records = [
                item.model_copy(
                    update={"canonical_resource_id": canonical.canonical_resource_id}
                )
                for item in records
            ]
            for record in records:
                self.repository.save_provider_record(record)
            self.repository.save_canonical_resource(canonical)
            decision_record = build_access_decision(canonical)
            self.repository.save_access_decision(decision_record)
            canonical_resources.append(canonical)
            provider_records.extend(records)
            access_decisions.append(decision_record)

        identifier_graph = build_identifier_graph(
            request.study_id,
            canonical_resources,
        )
        self.repository.save_identifier_graph(identifier_graph)
        identifier_graph_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="identifier_graph",
            value=identifier_graph.model_dump(mode="json"),
            producer="identifier_graph_builder",
            input_artifact_ids=[verification_artifact.artifact_id],
        )
        resource_relations = build_resource_relations(
            canonical_resources,
            evidence_artifact_ids=[identifier_graph_artifact.artifact_id],
        )
        for resource_relation in resource_relations:
            self.repository.save_resource_relation(resource_relation)
        relation_graph_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="resource_relation_graph",
            value={
                "study_id": request.study_id,
                "relations": [
                    item.model_dump(mode="json") for item in resource_relations
                ],
            },
            producer="resource_relation_graph_builder",
            input_artifact_ids=[identifier_graph_artifact.artifact_id],
        )
        access_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="access_decisions",
            value=[item.model_dump(mode="json") for item in access_decisions],
            producer="content_rights_policy",
            input_artifact_ids=[verification_artifact.artifact_id],
        )
        provider_records_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="provider_records",
            value=[item.model_dump(mode="json") for item in provider_records],
            producer="provider_record_builder",
            input_artifact_ids=[item.artifact_id for item in raw_artifacts],
        )
        canonical_resources_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="canonical_resources",
            value=[item.model_dump(mode="json") for item in canonical_resources],
            producer="canonical_resource_builder",
            input_artifact_ids=[
                verification_artifact.artifact_id,
                provider_records_artifact.artifact_id,
            ],
        )

        binding_ids: list[str] = []
        for resource in verified.resources:
            self.repository.save_resource(resource)
            content = json.dumps(
                resource.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            matching_raw = provider_raw_artifacts.get(resource.providers[0], [])
            raw_id = (
                matching_raw[0].artifact_id
                if matching_raw
                else normalized_artifact.artifact_id
            )
            snapshot = ResourceSnapshot(
                snapshot_id=retrieval_id(
                    "snapshot",
                    resource.resource_id,
                    hashlib.sha256(content).hexdigest(),
                    raw_id,
                    normalized_artifact.artifact_id,
                ),
                resource_id=resource.resource_id,
                content_level="metadata",
                raw_response_artifact_id=raw_id,
                normalized_content_artifact_id=normalized_artifact.artifact_id,
                provider=resource.providers[0],
                content_hash=hashlib.sha256(content).hexdigest(),
                mime_type="application/json",
                byte_size=len(content),
                license_status=resource.license or "unknown",
                access_status="retrieved",
            )
            snapshot = self.repository.save_snapshot(snapshot)
            binding = ResourceUseBinding(
                binding_id=retrieval_id(
                    "binding",
                    resource.resource_id,
                    snapshot.snapshot_id,
                    request.study_id,
                    request.phase.value,
                    request.step_instance_id,
                    request.requested_usage_role,
                    target_type,
                    target_id,
                    target_field,
                ),
                resource_id=resource.resource_id,
                snapshot_id=snapshot.snapshot_id,
                project_id=request.project_id,
                study_id=request.study_id,
                phase=request.phase,
                step_instance_id=request.step_instance_id,
                purpose=request.purpose,
                usage_role=request.requested_usage_role,
                target_type=target_type,
                target_id=target_id,
                target_field=target_field,
                relation=relation,  # type: ignore[arg-type]
                verification_status=resource.metadata_verification_status,
            )
            self.repository.save_binding(binding)
            binding_ids.append(binding.binding_id)

        duration_ms = round((time.perf_counter() - started) * 1000)
        verified_count = sum(
            item.metadata_verification_status is MetadataVerificationStatus.VERIFIED
            for item in verified.resources
        )
        coverage = RetrievalCoverageReport(
            coverage_report_id=retrieval_id("coverage", run_id, explicit_retry_count),
            run_id=run_id,
            query_purpose=request.purpose,
            providers_used=sorted(
                {
                    item.provider
                    for item in attempts
                    if item.status is RetrievalStatus.SUCCEEDED
                }
            ),
            executed_queries=effective_queries,
            date_range=plan.date_range,
            raw_result_count=len(normalized),
            deduplicated_result_count=len(deduplicated.resources),
            verified_result_count=verified_count,
            metadata_only_count=len(verified.resources),
            link_only_count=sum(
                item.metadata.get("access_status") == "link_only"
                for item in verified.resources
            ),
            full_text_available_count=sum(
                item.metadata.get("access_status") == "open_access"
                for item in verified.resources
            ),
            publication_count=sum(
                item.resource_type in {ResourceType.PUBLICATION, ResourceType.PREPRINT}
                for item in verified.resources
            ),
            github_repository_count=sum(
                item.resource_type is ResourceType.CODE_REPOSITORY
                for item in verified.resources
            ),
            huggingface_model_count=sum(
                item.resource_type is ResourceType.MODEL for item in verified.resources
            ),
            huggingface_dataset_count=sum(
                item.resource_type is ResourceType.DATASET
                for item in verified.resources
            ),
            web_source_count=sum(
                item.resource_type is ResourceType.WEB_SOURCE
                for item in verified.resources
            ),
            uncovered_databases=[
                provider
                for provider in plan.providers
                if provider
                not in {
                    item.provider
                    for item in attempts
                    if item.status is RetrievalStatus.SUCCEEDED
                }
            ],
            providers_not_used=[
                provider
                for provider in plan.providers
                if provider
                not in {
                    item.provider
                    for item in attempts
                    if item.status is RetrievalStatus.SUCCEEDED
                }
            ],
            provider_failures={
                item.provider: (
                    item.error_classification.value
                    if item.error_classification
                    else "unknown"
                )
                for item in attempts
                if item.status is RetrievalStatus.FAILED
                and not any(
                    later.provider == item.provider
                    and later.status is RetrievalStatus.SUCCEEDED
                    for later in attempts
                )
            },
            access_blocks=[
                f"{item.resource_id}: {item.metadata.get('access_status')}"
                for item in verified.resources
                if item.metadata.get("access_status")
                in {"access_blocked", "gated", "private"}
            ],
            license_constraints=[
                f"{item.resource_id}: license unknown"
                for item in verified.resources
                if not item.license
            ],
            metadata_conflicts=deduplicated.conflicts,
            known_blind_spots=warnings,
            retrieval_duration_ms=duration_ms,
            prohibited_claims=[
                "Do not claim exhaustive literature coverage.",
                "Do not treat attention signals as scientific evidence.",
                "An empty result is an evidence gap, not evidence of absence.",
            ],
        )
        self.repository.save_coverage(coverage)
        coverage_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="coverage_report",
            value=coverage.model_dump(mode="json"),
            producer="coverage_reporter",
            input_artifact_ids=[verification_artifact.artifact_id],
        )
        set_hash = hashlib.sha256(
            json.dumps(sorted(binding_ids)).encode("utf-8")
        ).hexdigest()
        prior_sets = [
            item
            for item in self.repository.list_resource_sets(request.study_id)
            if item.phase is request.phase
        ]
        next_version = max((item.version for item in prior_sets), default=0) + 1
        predecessor_set = (
            max(prior_sets, key=lambda item: item.version) if prior_sets else None
        )
        resource_set = ResourceSet(
            resource_set_id=retrieval_id(
                "resource-set",
                request.study_id,
                request.phase.value,
                next_version,
                set_hash,
            ),
            study_id=request.study_id,
            phase=request.phase,
            version=next_version,
            binding_ids=binding_ids,
            query_plan_id=plan.query_plan_id,
            coverage_report_id=coverage.coverage_report_id,
            content_hash=set_hash,
            supersedes_id=(
                predecessor_set.resource_set_id if predecessor_set else None
            ),
        )
        self.repository.save_resource_set(resource_set)
        set_artifact = self.repository.write_artifact(
            project_id=request.project_id,
            study_id=request.study_id,
            step_instance_id=request.step_instance_id,
            kind="resource_set",
            value=resource_set.model_dump(mode="json"),
            producer="resource_set_builder",
            input_artifact_ids=[coverage_artifact.artifact_id],
        )
        usage = BudgetUsage(
            queries=queries_used,
            results=len(normalized),
            download_bytes=raw_bytes,
        )
        successful_providers = {
            item.provider
            for item in attempts
            if item.status is RetrievalStatus.SUCCEEDED
        }
        failed_providers = {
            item.provider for item in attempts if item.status is RetrievalStatus.FAILED
        } - successful_providers
        terminal_status = (
            RetrievalStatus.DEGRADED
            if successful_providers and failed_providers
            else RetrievalStatus.FAILED
            if plan.providers and not successful_providers
            else RetrievalStatus.SUCCEEDED
        )
        run = run.model_copy(
            update={
                "execution_status": terminal_status,
                "provider_attempts": attempts,
                "completed_at": utc_now(),
                "retry_count": explicit_retry_count
                + sum(
                    max(0, item.attempt - 1)
                    for item in attempts
                    if item.status is RetrievalStatus.SUCCEEDED
                ),
                "budget_usage": usage,
                "output_artifact_ids": [
                    decision_artifact.artifact_id,
                    route_artifact.artifact_id,
                    *provider_output_artifact_ids,
                    normalized_artifact.artifact_id,
                    dedup_artifact.artifact_id,
                    verification_artifact.artifact_id,
                    metadata_verification_artifact.artifact_id,
                    provider_records_artifact.artifact_id,
                    canonical_resources_artifact.artifact_id,
                    identifier_graph_artifact.artifact_id,
                    relation_graph_artifact.artifact_id,
                    access_artifact.artifact_id,
                    coverage_artifact.artifact_id,
                    set_artifact.artifact_id,
                ],
                "coverage_report_id": coverage.coverage_report_id,
            }
        )
        self.repository.save_run(run)
        self._audit(
            request,
            run,
            response_status=terminal_status.value,
            warnings=warnings + verified.warnings,
        )
        return RetrievalExecution(request, run, resource_set, coverage)

    def freeze_resource_set(self, resource_set_id: str) -> ResourceSet:
        current = self.repository.load_resource_set(resource_set_id)
        if current.status is ResourceSetStatus.FROZEN:
            frozen = current
        else:
            # Freezing records an immutable retrieval snapshot; it does not
            # approve a Scope or Research Contract. The owner Gate may
            # therefore review a frozen ResourceSet without mutating it.
            frozen = current.model_copy(
                update={
                    "status": ResourceSetStatus.FROZEN,
                    "frozen_at": utc_now(),
                }
            )
            frozen = self.repository.save_resource_set(frozen)

        matching_request = next(
            (
                self.repository.load_request(run.request_id)
                for run in self.repository.list_runs(frozen.study_id)
                if self.repository.load_request(run.request_id).query_plan_id
                == frozen.query_plan_id
            ),
            None,
        )
        # Legacy imported sets may predate RetrievalRequest persistence. They
        # must remain freezable for read compatibility, but cannot truthfully
        # receive a V1 provenance-complete retrieval artifact.
        if matching_request is None:
            return frozen
        coverage_artifact_ids = [
            item.artifact_id
            for item in self.repository.list_artifacts(frozen.study_id)
            if item.kind == "coverage_report"
            and item.step_instance_id == matching_request.step_instance_id
        ]
        self.repository.write_artifact(
            project_id=matching_request.project_id,
            study_id=frozen.study_id,
            step_instance_id=matching_request.step_instance_id,
            kind="frozen_resource_set",
            value=frozen.model_dump(mode="json"),
            producer="resource_set_freezer",
            input_artifact_ids=coverage_artifact_ids[-1:],
            policy_context={
                "network_policy_id": matching_request.network_policy_id,
                "request_id": matching_request.request_id,
                "phase": matching_request.phase.value,
            },
        )
        return frozen

    def retry(self, run_id: str) -> RetrievalExecution:
        run = self.repository.load_run(run_id)
        if run.execution_status is RetrievalStatus.SUCCEEDED:
            raise ValueError("successful retrieval runs are immutable and not retried")
        reset = run.model_copy(
            update={
                "execution_status": RetrievalStatus.QUEUED,
                "completed_at": None,
                "error_classification": None,
                "retry_count": run.retry_count + 1,
            }
        )
        self.repository.save_run(reset)
        return self.run(run.request_id)

    def promote_binding(
        self,
        binding_id: str,
        *,
        target_phase: RetrievalPhase,
        step_instance_id: str,
        purpose: str,
        usage_role: str,
        target_type: str,
        target_id: str,
        target_field: str,
        contract_refs: list[ContractRef] | None = None,
        snapshot_id: str | None = None,
    ) -> ResourceUseBinding:
        from ..policy.profiles import STAGE_PROFILES

        source = next(
            item
            for item in self.repository.list_bindings()
            if item.binding_id == binding_id
        )
        profile = STAGE_PROFILES[target_phase]
        if purpose not in profile.allowed_purposes:
            raise ValueError(
                f"purpose {purpose!r} is not allowed in {target_phase.value}"
            )
        if target_phase is RetrievalPhase.EXPERIMENTATION and not contract_refs:
            raise ValueError(
                "promotion into experimentation requires contract approval"
            )
        if target_phase is RetrievalPhase.REPAIR and not any(
            item.contract_type == "repair" for item in (contract_refs or [])
        ):
            raise ValueError("promotion into repair requires repair contract")
        effective_snapshot_id = snapshot_id or source.snapshot_id
        effective_snapshot = self.repository.load_snapshot(
            effective_snapshot_id
        )
        if effective_snapshot.resource_id != source.resource_id:
            raise ValueError(
                "promoted snapshot must belong to the source resource"
            )
        promoted = source.model_copy(
            update={
                "binding_id": retrieval_id(
                    "binding",
                    source.resource_id,
                    effective_snapshot_id,
                    source.study_id,
                    target_phase.value,
                    step_instance_id,
                    usage_role,
                    target_type,
                    target_id,
                    target_field,
                ),
                "snapshot_id": effective_snapshot_id,
                "phase": target_phase,
                "step_instance_id": step_instance_id,
                "purpose": purpose,
                "usage_role": usage_role,
                "target_type": target_type,
                "target_id": target_id,
                "target_field": target_field,
                "created_at": utc_now(),
            }
        )
        promoted = self.repository.save_binding(promoted)
        self.repository.append_audit(
            RetrievalAuditEvent(
                event_id=retrieval_id(
                    "retrieval-event",
                    "promotion",
                    source.binding_id,
                    promoted.binding_id,
                ),
                request_id=retrieval_id(
                    "promotion-request", source.binding_id, promoted.binding_id
                ),
                project_id=source.project_id,
                study_id=source.study_id,
                step_instance_id=step_instance_id,
                purpose=purpose,
                phase=target_phase,
                response_status="promoted",
                warnings=[
                    f"promotion_lineage:{source.binding_id}->{promoted.binding_id}"
                ],
            )
        )
        return promoted

    def record_synthesis_conflict(
        self,
        *,
        study_id: str,
        resource_id: str,
        claim_id: str,
        conflict_type: str,
        details: str,
    ) -> EvidenceConflict:
        from ...workflow_domain import WorkflowRepository

        workflow = WorkflowRepository(self.repository.root.parent)
        study = workflow.load_study(study_id)
        self.repository.load_resource(resource_id)
        conflict = EvidenceConflict(
            conflict_id=retrieval_id(
                "evidence-conflict",
                study_id,
                resource_id,
                claim_id,
                conflict_type,
            ),
            study_id=study_id,
            resource_id=resource_id,
            claim_id=claim_id,
            historical_verdict_id=study.latest_study_verdict_id,
            conflict_type=conflict_type,  # type: ignore[arg-type]
            details=details,
        )
        saved = self.repository.save_evidence_conflict(conflict)
        # Deliberately do not call save_study or verdict APIs.
        return saved

    def _audit(
        self,
        request: RetrievalRequest,
        run: RetrievalRun,
        *,
        response_status: str,
        warnings: list[str] | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self.repository.append_audit(
            RetrievalAuditEvent(
                event_id=retrieval_id(
                    "retrieval-event",
                    request.request_id,
                    response_status,
                    utc_now(),
                ),
                request_id=request.request_id,
                project_id=request.project_id,
                study_id=request.study_id,
                step_instance_id=request.step_instance_id,
                purpose=request.purpose,
                phase=request.phase,
                policy_decision_id=run.policy_decision_id,
                response_status=response_status,
                retry_count=run.retry_count,
                budget_usage=run.budget_usage,
                artifact_ids=run.output_artifact_ids,
                warnings=warnings or [],
                failure_reason=failure_reason,
            )
        )
