from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from ...storage import (
    append_jsonl,
    load_jsonl,
    read_json,
    sha256_file,
    write_json_atomic,
)
from .external_models import (
    AccessDecision,
    CanonicalResource,
    CorpusManifest,
    EvidenceResult,
    IdentifierGraph,
    ProviderRecord,
    ReadinessReport,
    ResourceRelation,
)
from .models import (
    ExternalResource,
    EvidenceConflict,
    PolicyDecision,
    QueryPlan,
    RedactionReport,
    ResourceSet,
    ResourceSetStatus,
    ResourceSnapshot,
    ResourceUseBinding,
    RetrievalArtifact,
    RetrievalAuditEvent,
    RetrievalCoverageReport,
    RetrievalRequest,
    RetrievalRun,
    retrieval_id,
)

T = TypeVar("T", bound=BaseModel)


class RetrievalRepository:
    """Append-oriented filesystem persistence colocated with Workflow v2."""

    def __init__(self, workflow_root: str | Path) -> None:
        self.root = Path(workflow_root).resolve() / "retrieval"
        self.root.mkdir(parents=True, exist_ok=True)
        schema_path = self.root / "schema.json"
        if not schema_path.is_file():
            write_json_atomic(
                schema_path,
                {
                    "schema_version": 3,
                    "storage": "append-oriented-filesystem",
                    "migrations": [
                        "001_retrieval_gateway_domain",
                        "002_project_policy_binding",
                        "003_external_research_v1",
                        "004_retrieval_artifact_context",
                    ],
                    "legacy_workflow_data_mutated": False,
                },
            )
        else:
            schema = read_json(schema_path)
            if int(schema.get("schema_version", 0)) < 3:
                write_json_atomic(
                    schema_path,
                    {
                        **schema,
                        "schema_version": 3,
                        "migrations": list(
                            dict.fromkeys(
                                [
                                    *schema.get("migrations", []),
                                    "003_external_research_v1",
                                    "004_retrieval_artifact_context",
                                ]
                            )
                        ),
                        "legacy_workflow_data_mutated": False,
                    },
                )

    def _object_path(self, kind: str, identity: str) -> Path:
        return self.root / kind / f"{identity}.json"

    def _save_immutable(self, kind: str, identity: str, value: BaseModel) -> None:
        path = self._object_path(kind, identity)
        payload = value.model_dump(mode="json")
        if path.is_file():
            if read_json(path) != payload:
                raise ValueError(f"immutable {kind} cannot be overwritten: {identity}")
            return
        write_json_atomic(path, payload)

    def _save_semantic_immutable(
        self,
        kind: str,
        identity: str,
        value: T,
        *,
        volatile_fields: set[str],
    ) -> T:
        """Reuse an immutable semantic record when only its issuance time differs."""
        path = self._object_path(kind, identity)
        if path.is_file():
            existing = type(value).model_validate(read_json(path))
            if existing.model_dump(exclude=volatile_fields) != value.model_dump(
                exclude=volatile_fields
            ):
                raise ValueError(f"immutable {kind} cannot be overwritten: {identity}")
            return existing
        write_json_atomic(path, value.model_dump(mode="json"))
        return value

    def _load(self, kind: str, identity: str, model: type[T]) -> T:
        return model.model_validate(read_json(self._object_path(kind, identity)))

    def _list(self, kind: str, model: type[T]) -> list[T]:
        return [
            model.model_validate(read_json(path))
            for path in sorted((self.root / kind).glob("*.json"))
        ]

    def save_request(self, value: RetrievalRequest) -> RetrievalRequest:
        self._save_immutable("requests", value.request_id, value)
        index = (
            self.root
            / "idempotency"
            / f"{retrieval_id('idem', value.idempotency_key)}.json"
        )
        if index.is_file():
            existing = read_json(index)
            if existing["request_id"] != value.request_id:
                raise ValueError("idempotency key is already bound to another request")
        else:
            write_json_atomic(index, {"request_id": value.request_id})
        return value

    def request_for_idempotency(self, key: str) -> RetrievalRequest | None:
        index = self.root / "idempotency" / f"{retrieval_id('idem', key)}.json"
        if not index.is_file():
            return None
        return self.load_request(str(read_json(index)["request_id"]))

    def load_request(self, identity: str) -> RetrievalRequest:
        return self._load("requests", identity, RetrievalRequest)

    def save_query_plan(self, value: QueryPlan) -> QueryPlan:
        return self._save_semantic_immutable(
            "query_plans",
            value.query_plan_id,
            value,
            volatile_fields={"created_at"},
        )

    def load_query_plan(self, identity: str) -> QueryPlan:
        return self._load("query_plans", identity, QueryPlan)

    def save_redaction_report(self, value: RedactionReport) -> RedactionReport:
        path = self._object_path("redaction_reports", value.report_id)
        if path.is_file():
            existing = RedactionReport.model_validate(read_json(path))
            if existing.model_dump(exclude={"created_at"}) != value.model_dump(
                exclude={"created_at"}
            ):
                raise ValueError(
                    f"immutable redaction_reports cannot be overwritten: "
                    f"{value.report_id}"
                )
            return existing
        self._save_immutable("redaction_reports", value.report_id, value)
        return value

    def save_policy_decision(self, value: PolicyDecision) -> PolicyDecision:
        return self._save_semantic_immutable(
            "policy_decisions",
            value.decision_id,
            value,
            volatile_fields={"created_at"},
        )

    def save_run(self, value: RetrievalRun) -> RetrievalRun:
        write_json_atomic(self._object_path("runs", value.run_id), value)
        return value

    def load_run(self, identity: str) -> RetrievalRun:
        return self._load("runs", identity, RetrievalRun)

    def run_for_request(self, request_id: str) -> RetrievalRun | None:
        path = self._object_path("runs", retrieval_id("retrieval-run", request_id))
        if not path.is_file():
            return None
        return RetrievalRun.model_validate(read_json(path))

    def save_provider_cache(self, cache_key: str, value: dict[str, Any]) -> None:
        digest = (
            __import__("hashlib")
            .sha256(
                json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
            .hexdigest()
        )
        record_id = retrieval_id("provider-cache-record", cache_key, digest)
        path = self._object_path("provider_cache_records", record_id)
        payload = {
            "cache_key": cache_key,
            "record_id": record_id,
            "content_hash": digest,
            **value,
        }
        if not path.is_file():
            write_json_atomic(path, payload)
        write_json_atomic(
            self._object_path("provider_cache_index", cache_key),
            {"record_id": record_id},
        )

    def load_provider_cache(self, cache_key: str) -> dict[str, Any] | None:
        index = self._object_path("provider_cache_index", cache_key)
        if not index.is_file():
            return None
        record_id = str(read_json(index)["record_id"])
        return read_json(self._object_path("provider_cache_records", record_id))

    def list_runs(self, study_id: str | None = None) -> list[RetrievalRun]:
        runs = self._list("runs", RetrievalRun)
        if study_id is None:
            return runs
        request_ids = {
            item.request_id
            for item in self._list("requests", RetrievalRequest)
            if item.study_id == study_id
        }
        return [item for item in runs if item.request_id in request_ids]

    def write_artifact(
        self,
        *,
        project_id: str,
        study_id: str,
        step_instance_id: str,
        kind: str,
        value: Any,
        producer: str,
        input_artifact_ids: list[str] | None = None,
        policy_context: dict[str, Any] | None = None,
    ) -> RetrievalArtifact:
        if kind in {
            "normalized_resources",
            "access_decisions",
            "evidence_results",
        } and isinstance(value, list):
            payload = "\n".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value
            )
            filename = f"{kind}.jsonl"
        else:
            payload = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
            )
            filename = f"{kind}.json"
        digest = __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest()
        lineage = list(input_artifact_ids or [])
        effective_policy_context = policy_context or self._artifact_policy_context(
            project_id
        )
        artifact_id = retrieval_id(
            "rartifact",
            project_id,
            study_id,
            step_instance_id,
            kind,
            digest,
            producer,
            json.dumps(effective_policy_context, ensure_ascii=False, sort_keys=True),
            *lineage,
        )
        path = (
            self.root
            / "artifacts"
            / study_id
            / step_instance_id
            / artifact_id
            / filename
        )
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                payload + ("\n" if not payload.endswith("\n") else ""), encoding="utf-8"
            )
        artifact = RetrievalArtifact(
            artifact_id=artifact_id,
            project_id=project_id,
            study_id=study_id,
            step_instance_id=step_instance_id,
            kind=kind,
            path=str(path),
            content_hash=sha256_file(path),
            producer=producer,
            input_artifact_ids=lineage,
            policy_context=effective_policy_context,
        )
        existing_path = self._object_path("artifact_records", artifact_id)
        if existing_path.is_file():
            existing = RetrievalArtifact.model_validate(read_json(existing_path))
            if (
                existing.content_hash != artifact.content_hash
                or existing.producer != artifact.producer
                or existing.input_artifact_ids != artifact.input_artifact_ids
                or existing.project_id != artifact.project_id
                or existing.policy_context != artifact.policy_context
                or existing.path != artifact.path
            ):
                raise ValueError(
                    "content-addressed artifact has conflicting provenance"
                )
            return existing
        self._save_immutable("artifact_records", artifact_id, artifact)
        return artifact

    def write_binary_artifact(
        self,
        *,
        project_id: str,
        study_id: str,
        step_instance_id: str,
        kind: str,
        content: bytes,
        producer: str,
        extension: str,
        input_artifact_ids: list[str] | None = None,
        policy_context: dict[str, Any] | None = None,
    ) -> RetrievalArtifact:
        safe_extension = extension.casefold().lstrip(".")
        if safe_extension not in {
            "pdf",
            "zip",
            "tar",
            "gz",
            "bin",
            "arff",
            "csv",
            "json",
            "parquet",
            "pq",
        }:
            raise ValueError("unsupported binary artifact extension")
        digest = __import__("hashlib").sha256(content).hexdigest()
        lineage = list(input_artifact_ids or [])
        effective_policy_context = policy_context or self._artifact_policy_context(
            project_id
        )
        artifact_id = retrieval_id(
            "rartifact",
            project_id,
            study_id,
            step_instance_id,
            kind,
            digest,
            producer,
            json.dumps(effective_policy_context, ensure_ascii=False, sort_keys=True),
            *lineage,
        )
        path = (
            self.root
            / "artifacts"
            / study_id
            / step_instance_id
            / artifact_id
            / f"{kind}.{safe_extension}"
        )
        if path.is_file():
            if sha256_file(path) != digest:
                raise ValueError("immutable binary artifact content mismatch")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        artifact = RetrievalArtifact(
            artifact_id=artifact_id,
            project_id=project_id,
            study_id=study_id,
            step_instance_id=step_instance_id,
            kind=kind,
            path=str(path),
            content_hash=digest,
            producer=producer,
            input_artifact_ids=lineage,
            policy_context=effective_policy_context,
        )
        existing_path = self._object_path("artifact_records", artifact_id)
        if existing_path.is_file():
            existing = RetrievalArtifact.model_validate(read_json(existing_path))
            if (
                existing.content_hash != artifact.content_hash
                or existing.producer != artifact.producer
                or existing.input_artifact_ids != artifact.input_artifact_ids
                or existing.project_id != artifact.project_id
                or existing.policy_context != artifact.policy_context
                or existing.path != artifact.path
            ):
                raise ValueError(
                    "content-addressed artifact has conflicting provenance"
                )
            return existing
        self._save_immutable("artifact_records", artifact_id, artifact)
        return artifact

    def _artifact_policy_context(self, project_id: str) -> dict[str, Any]:
        binding_path = self.root / "project_policy_bindings" / f"{project_id}.json"
        if binding_path.is_file():
            return {
                "network_policy_id": str(read_json(binding_path)["policy_id"]),
                "source": "project_policy_binding",
            }
        return {
            "network_policy_id": None,
            "source": "local_only",
        }

    def load_artifact(self, identity: str) -> RetrievalArtifact:
        return self._load("artifact_records", identity, RetrievalArtifact)

    def list_artifacts(self, study_id: str | None = None) -> list[RetrievalArtifact]:
        values = self._list("artifact_records", RetrievalArtifact)
        return (
            values
            if study_id is None
            else [item for item in values if item.study_id == study_id]
        )

    def save_resource(self, value: ExternalResource) -> ExternalResource:
        path = self._object_path("resources", value.resource_id)
        if path.is_file():
            existing = ExternalResource.model_validate(read_json(path))
            providers = sorted(set(existing.providers) | set(value.providers))
            merged_metadata = dict(existing.metadata)
            for key, item in value.metadata.items():
                if key not in merged_metadata or merged_metadata[key] in (
                    None,
                    "",
                    [],
                ):
                    merged_metadata[key] = item
            if value.metadata.get("access_status") == "open_access":
                merged_metadata["access_status"] = "open_access"
                if value.metadata.get("full_text_url"):
                    merged_metadata["full_text_url"] = value.metadata["full_text_url"]
            update: dict[str, Any] = {
                "providers": providers,
                "metadata": merged_metadata,
                "license": existing.license or value.license,
            }
            if existing.canonical_metadata_hash != value.canonical_metadata_hash:
                from .models import MetadataVerificationStatus

                update.update(
                    {
                        "metadata_verification_status": MetadataVerificationStatus.CONFLICT,
                        "metadata": {
                            **merged_metadata,
                            "metadata_conflict": (
                                "A later provider/run returned different canonical metadata."
                            ),
                            "conflicting_metadata_hashes": sorted(
                                {
                                    existing.canonical_metadata_hash,
                                    value.canonical_metadata_hash,
                                }
                            ),
                        },
                    }
                )
            value = existing.model_copy(update=update)
        write_json_atomic(path, value)
        return value

    def load_resource(self, identity: str) -> ExternalResource:
        return self._load("resources", identity, ExternalResource)

    def list_resources(self) -> list[ExternalResource]:
        return self._list("resources", ExternalResource)

    def save_snapshot(self, value: ResourceSnapshot) -> ResourceSnapshot:
        return self._save_semantic_immutable(
            "snapshots",
            value.snapshot_id,
            value,
            volatile_fields={
                "retrieved_at",
                "normalized_content_artifact_id",
            },
        )

    def list_snapshots(self, resource_id: str | None = None) -> list[ResourceSnapshot]:
        values = self._list("snapshots", ResourceSnapshot)
        return (
            values
            if resource_id is None
            else [item for item in values if item.resource_id == resource_id]
        )

    def load_snapshot(self, identity: str) -> ResourceSnapshot:
        return self._load("snapshots", identity, ResourceSnapshot)

    def save_binding(self, value: ResourceUseBinding) -> ResourceUseBinding:
        self._save_immutable("bindings", value.binding_id, value)
        return value

    def load_binding(self, identity: str) -> ResourceUseBinding:
        return self._load("bindings", identity, ResourceUseBinding)

    def list_bindings(self, study_id: str | None = None) -> list[ResourceUseBinding]:
        values = self._list("bindings", ResourceUseBinding)
        return (
            values
            if study_id is None
            else [item for item in values if item.study_id == study_id]
        )

    def save_coverage(self, value: RetrievalCoverageReport) -> RetrievalCoverageReport:
        self._save_immutable("coverage", value.coverage_report_id, value)
        return value

    def load_coverage(self, identity: str) -> RetrievalCoverageReport:
        return self._load("coverage", identity, RetrievalCoverageReport)

    def save_resource_set(self, value: ResourceSet) -> ResourceSet:
        path = self._object_path("resource_sets", value.resource_set_id)
        if path.is_file():
            existing = ResourceSet.model_validate(read_json(path))
            if existing.status is ResourceSetStatus.FROZEN and (
                existing.model_dump(mode="json") != value.model_dump(mode="json")
            ):
                raise ValueError("frozen ResourceSet cannot be modified")
        write_json_atomic(path, value)
        return value

    def load_resource_set(self, identity: str) -> ResourceSet:
        return self._load("resource_sets", identity, ResourceSet)

    def list_resource_sets(self, study_id: str | None = None) -> list[ResourceSet]:
        values = self._list("resource_sets", ResourceSet)
        return (
            values
            if study_id is None
            else [item for item in values if item.study_id == study_id]
        )

    def append_audit(self, value: RetrievalAuditEvent) -> RetrievalAuditEvent:
        append_jsonl(self.root / "audit" / "retrieval_audit.jsonl", value)
        return value

    def list_audit_events(self) -> list[RetrievalAuditEvent]:
        return [
            RetrievalAuditEvent.model_validate(item)
            for item in load_jsonl(self.root / "audit" / "retrieval_audit.jsonl")
        ]

    def save_evidence_conflict(self, value: EvidenceConflict) -> EvidenceConflict:
        self._save_immutable("evidence_conflicts", value.conflict_id, value)
        return value

    def list_evidence_conflicts(
        self, study_id: str | None = None
    ) -> list[EvidenceConflict]:
        values = self._list("evidence_conflicts", EvidenceConflict)
        return (
            values
            if study_id is None
            else [item for item in values if item.study_id == study_id]
        )

    def save_readiness_report(self, value: ReadinessReport) -> ReadinessReport:
        self._save_immutable("readiness_reports", value.report_id, value)
        return value

    def latest_readiness_report(self) -> ReadinessReport | None:
        paths = sorted(
            (self.root / "readiness_reports").glob("*.json"),
            key=lambda item: item.stat().st_mtime_ns,
        )
        if not paths:
            return None
        return ReadinessReport.model_validate(read_json(paths[-1]))

    def save_canonical_resource(self, value: CanonicalResource) -> CanonicalResource:
        self._save_immutable("canonical_resources", value.canonical_resource_id, value)
        return value

    def load_canonical_resource(self, identity: str) -> CanonicalResource:
        return self._load("canonical_resources", identity, CanonicalResource)

    def list_canonical_resources(self) -> list[CanonicalResource]:
        return self._list("canonical_resources", CanonicalResource)

    def save_provider_record(self, value: ProviderRecord) -> ProviderRecord:
        self._save_immutable("provider_records", value.provider_record_id, value)
        return value

    def list_provider_records(
        self, resource_id: str | None = None
    ) -> list[ProviderRecord]:
        values = self._list("provider_records", ProviderRecord)
        return (
            values
            if resource_id is None
            else [item for item in values if item.canonical_resource_id == resource_id]
        )

    def save_identifier_graph(self, value: IdentifierGraph) -> IdentifierGraph:
        return self._save_semantic_immutable(
            "identifier_graphs",
            value.graph_id,
            value,
            volatile_fields={"created_at"},
        )

    def load_identifier_graph(self, identity: str) -> IdentifierGraph:
        return self._load("identifier_graphs", identity, IdentifierGraph)

    def list_identifier_graphs(
        self, study_id: str | None = None
    ) -> list[IdentifierGraph]:
        values = self._list("identifier_graphs", IdentifierGraph)
        return (
            values
            if study_id is None
            else [item for item in values if item.study_id == study_id]
        )

    def save_resource_relation(self, value: ResourceRelation) -> ResourceRelation:
        self._save_immutable("resource_relations", value.relation_id, value)
        return value

    def list_resource_relations(
        self, resource_id: str | None = None
    ) -> list[ResourceRelation]:
        values = self._list("resource_relations", ResourceRelation)
        if resource_id is None:
            return values
        return [
            item
            for item in values
            if resource_id in {item.source_resource_id, item.target_resource_id}
        ]

    def save_access_decision(self, value: AccessDecision) -> AccessDecision:
        return self._save_semantic_immutable(
            "access_decisions",
            value.decision_id,
            value,
            volatile_fields={"decided_at"},
        )

    def load_access_decision(self, identity: str) -> AccessDecision:
        return self._load("access_decisions", identity, AccessDecision)

    def list_access_decisions(
        self, resource_id: str | None = None
    ) -> list[AccessDecision]:
        values = self._list("access_decisions", AccessDecision)
        return (
            values
            if resource_id is None
            else [item for item in values if item.resource_id == resource_id]
        )

    def save_corpus(self, value: CorpusManifest) -> CorpusManifest:
        write_json_atomic(self._object_path("corpora", value.corpus_id), value)
        return value

    def load_corpus(self, identity: str) -> CorpusManifest:
        return self._load("corpora", identity, CorpusManifest)

    def save_evidence_result(self, value: EvidenceResult) -> EvidenceResult:
        valid_abstention = (
            value.answerability == "unanswerable"
            and not value.answer.strip()
            and bool(value.abstention_reason)
            and bool(value.evidence_bundle_hash)
        )
        if not value.evidence_spans and not valid_abstention:
            raise ValueError(
                "evidence results without source spans require a bounded "
                "unanswerable abstention record"
            )
        self._save_immutable("evidence_results", value.result_id, value)
        return value

    def list_evidence_results(
        self, corpus_id: str | None = None
    ) -> list[EvidenceResult]:
        values = self._list("evidence_results", EvidenceResult)
        return (
            values
            if corpus_id is None
            else [item for item in values if item.corpus_id == corpus_id]
        )

    def save_network_policy(self, value: BaseModel) -> BaseModel:
        identity = str(getattr(value, "policy_id"))
        write_json_atomic(self._object_path("network_policies", identity), value)
        return value

    def load_network_policy(self, identity: str):
        from ..policy.engine import RetrievalNetworkPolicy

        return self._load("network_policies", identity, RetrievalNetworkPolicy)

    def policy_for_project(self, project_id: str):
        project_index = self.root / "project_policy_bindings" / f"{project_id}.json"
        if project_index.is_file():
            return self.load_network_policy(str(read_json(project_index)["policy_id"]))
        return None

    def bind_project_policy(self, project_id: str, policy_id: str) -> None:
        self.load_network_policy(policy_id)
        write_json_atomic(
            self.root / "project_policy_bindings" / f"{project_id}.json",
            {"policy_id": policy_id},
        )
