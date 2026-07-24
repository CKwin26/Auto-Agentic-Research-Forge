from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from ..domain.external_models import (
    AccessDecision,
    AccessMode,
    CanonicalResource,
    CanonicalResourceType,
    IdentifierGraph,
    IdentifierGraphEdge,
    IdentifierKind,
    ProviderRecord,
    RelationVerification,
    ResourceIdentifier,
    ResourceRelation,
    ResourceRelationType,
)
from ..domain.models import (
    ExternalResource,
    ResourceType,
    RetrievalArtifact,
    retrieval_id,
)


_CANONICAL_TYPES = {
    ResourceType.PUBLICATION: CanonicalResourceType.PUBLICATION,
    ResourceType.PREPRINT: CanonicalResourceType.PREPRINT,
    ResourceType.CODE_REPOSITORY: CanonicalResourceType.CODE_REPOSITORY,
    ResourceType.CODE_RELEASE: CanonicalResourceType.CODE_RELEASE,
    ResourceType.ISSUE: CanonicalResourceType.ISSUE,
    ResourceType.DATASET: CanonicalResourceType.DATASET,
    ResourceType.MODEL: CanonicalResourceType.MODEL,
    ResourceType.SPACE: CanonicalResourceType.SPACE,
    ResourceType.BENCHMARK: CanonicalResourceType.BENCHMARK,
    ResourceType.STANDARD: CanonicalResourceType.STANDARD,
    ResourceType.DOCUMENTATION: CanonicalResourceType.DOCUMENTATION,
    ResourceType.WEB_SOURCE: CanonicalResourceType.WEB_SOURCE,
    ResourceType.RETRACTION_NOTICE: CanonicalResourceType.PUBLISHER_NOTICE,
    ResourceType.SUBMISSION_RULE: CanonicalResourceType.PUBLISHER_NOTICE,
    ResourceType.PUBLISHER_NOTICE: CanonicalResourceType.PUBLISHER_NOTICE,
    ResourceType.INSTITUTIONAL_DOCUMENT: CanonicalResourceType.INSTITUTIONAL_DOCUMENT,
}


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _identifier(
    kind: IdentifierKind,
    value: str | None,
    *,
    canonical: bool = False,
    verified: bool = False,
) -> ResourceIdentifier | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return ResourceIdentifier(
        kind=kind,
        value=normalized,
        canonical=canonical,
        verified=verified,
    )


def resource_identifiers(resource: ExternalResource) -> list[ResourceIdentifier]:
    """Extract only identifiers explicitly present in provider-normalized data."""
    metadata = resource.metadata
    values = [
        _identifier(
            IdentifierKind.DOI,
            resource.doi,
            canonical=bool(resource.doi),
            verified=bool(resource.doi),
        ),
        _identifier(
            IdentifierKind.PMID,
            metadata.get("pmid"),
            verified=bool(metadata.get("pmid")),
        ),
        _identifier(
            IdentifierKind.PMCID,
            metadata.get("pmcid"),
            verified=bool(metadata.get("pmcid")),
        ),
        _identifier(
            IdentifierKind.ARXIV,
            metadata.get("arxiv_id") or metadata.get("arxiv"),
            verified=bool(metadata.get("arxiv_id") or metadata.get("arxiv")),
        ),
        _identifier(
            IdentifierKind.OPENALEX,
            metadata.get("openalex_id"),
            verified=bool(metadata.get("openalex_id")),
        ),
        _identifier(
            IdentifierKind.SEMANTIC_SCHOLAR,
            metadata.get("semantic_scholar_id") or metadata.get("paper_id"),
            verified=bool(
                metadata.get("semantic_scholar_id") or metadata.get("paper_id")
            ),
        ),
        _identifier(
            IdentifierKind.CROSSREF,
            metadata.get("crossref_id"),
            verified=bool(metadata.get("crossref_id")),
        ),
        _identifier(
            IdentifierKind.GITHUB_REPOSITORY,
            resource.repository,
            canonical=resource.resource_type is ResourceType.CODE_REPOSITORY,
            verified=bool(resource.repository),
        ),
        _identifier(
            IdentifierKind.GITHUB_COMMIT,
            resource.commit,
            verified=bool(resource.commit and len(resource.commit) == 40),
        ),
        _identifier(
            IdentifierKind.HUGGINGFACE_REPOSITORY,
            (
                resource.model_identifier
                or resource.dataset_identifier
                or metadata.get("repo_id")
            ),
            canonical=resource.resource_type
            in {ResourceType.MODEL, ResourceType.DATASET, ResourceType.SPACE},
            verified=bool(
                resource.model_identifier
                or resource.dataset_identifier
                or metadata.get("repo_id")
            ),
        ),
        _identifier(
            IdentifierKind.HUGGINGFACE_REVISION,
            metadata.get("revision"),
            verified=bool(
                metadata.get("revision") and len(str(metadata["revision"])) == 40
            ),
        ),
        _identifier(
            IdentifierKind.URL,
            resource.url,
            canonical=not bool(resource.doi),
            verified=bool(resource.url and resource.url.startswith("https://")),
        ),
        _identifier(
            IdentifierKind.CONTENT_HASH,
            resource.canonical_metadata_hash,
            verified=True,
        ),
    ]
    unique: dict[tuple[IdentifierKind, str], ResourceIdentifier] = {}
    for item in values:
        if item is not None:
            unique[(item.kind, item.value.casefold())] = item
    return list(unique.values())


def build_provider_records(
    resource: ExternalResource,
    *,
    raw_artifacts: Mapping[str, list[RetrievalArtifact]],
) -> list[ProviderRecord]:
    pending: list[dict[str, Any]] = []
    for provider in resource.providers:
        artifacts = raw_artifacts.get(provider, [])
        if not artifacts:
            continue
        artifact = artifacts[0]
        provider_resource_id = str(
            resource.metadata.get("provider_resource_id")
            or resource.metadata.get("paper_id")
            or resource.metadata.get("repo_id")
            or resource.repository
            or resource.model_identifier
            or resource.dataset_identifier
            or resource.canonical_identifier
        )
        score = resource.metadata.get("provider_score")
        pending.append(
            {
                "provider_record_id": retrieval_id(
                    "provider-record",
                    resource.resource_id,
                    provider,
                    artifact.artifact_id,
                    artifact.content_hash,
                ),
                "provider": provider,
                "provider_resource_id": provider_resource_id,
                "raw_response_artifact_id": artifact.artifact_id,
                "normalized_metadata": resource.model_dump(
                    mode="json", exclude={"retrieved_at"}
                ),
                "retrieved_at": artifact.created_at,
                "response_hash": artifact.content_hash,
                "provider_specific_score": (
                    float(score) if isinstance(score, (int, float)) else None
                ),
                "provider_warnings": [],
            }
        )
    canonical_resource_id = retrieval_id(
        "canonical-resource",
        resource.resource_id,
        resource.canonical_metadata_hash,
        *sorted(str(item["provider_record_id"]) for item in pending),
    )
    return [
        ProviderRecord(
            canonical_resource_id=canonical_resource_id,
            **item,
        )
        for item in pending
    ]


def build_canonical_resource(
    resource: ExternalResource,
    provider_records: Iterable[ProviderRecord],
) -> CanonicalResource:
    records = list(provider_records)
    access_status = str(resource.metadata.get("access_status") or "metadata_only")
    if access_status not in {
        "open_access",
        "metadata_only",
        "abstract_only",
        "link_only",
        "access_blocked",
        "gated",
        "private",
    }:
        access_status = "metadata_only"
    revision = str(resource.metadata.get("revision") or "").strip() or None
    version = str(resource.metadata.get("version") or "").strip() or None
    canonical_resource_id = retrieval_id(
        "canonical-resource",
        resource.resource_id,
        resource.canonical_metadata_hash,
        *sorted(item.provider_record_id for item in records),
    )
    return CanonicalResource(
        canonical_resource_id=canonical_resource_id,
        resource_type=_CANONICAL_TYPES[resource.resource_type],
        title=resource.title,
        authors_or_owners=resource.authors_or_owners,
        identifiers=resource_identifiers(resource),
        canonical_url=resource.url,
        publication_or_release_date=resource.publication_or_release_date,
        provider_record_ids=[item.provider_record_id for item in records],
        version=version,
        commit_or_revision=resource.commit or revision,
        license=resource.license,
        access_status=access_status,  # type: ignore[arg-type]
        metadata_hash=resource.canonical_metadata_hash,
        verification_status=resource.metadata_verification_status,
        metadata=resource.metadata,
    )


def build_identifier_graph(
    study_id: str, resources: Iterable[CanonicalResource]
) -> IdentifierGraph:
    resource_list = list(resources)
    edges: list[IdentifierGraphEdge] = []
    for resource in resource_list:
        identifiers = resource.identifiers
        anchor = next((item for item in identifiers if item.canonical), None)
        if anchor is None and identifiers:
            anchor = identifiers[0]
        if anchor is None:
            continue
        for other in identifiers:
            if other == anchor:
                continue
            edges.append(
                IdentifierGraphEdge(
                    left=anchor,
                    right=other,
                    verification=(
                        "verified_official"
                        if anchor.verified and other.verified
                        else "strongly_inferred"
                    ),
                    basis=f"co-reported for {resource.canonical_resource_id}",
                )
            )
    resource_ids = sorted(
        item.canonical_resource_id for item in resource_list
    )
    payload = {
        "study_id": study_id,
        "resource_ids": resource_ids,
        "edges": [item.model_dump(mode="json") for item in edges],
    }
    digest = _hash_json(payload)
    return IdentifierGraph(
        graph_id=retrieval_id("identifier-graph", study_id, digest),
        study_id=study_id,
        resource_ids=resource_ids,
        edges=edges,
        content_hash=digest,
    )


def build_resource_relations(
    resources: Iterable[CanonicalResource],
    *,
    evidence_artifact_ids: list[str],
) -> list[ResourceRelation]:
    """Create relations only when both endpoints and an explicit link exist."""
    resource_list = list(resources)
    by_repo = {
        identifier.value.casefold(): item
        for item in resource_list
        for identifier in item.identifiers
        if identifier.kind
        in {
            IdentifierKind.GITHUB_REPOSITORY,
            IdentifierKind.HUGGINGFACE_REPOSITORY,
        }
    }
    by_doi = {
        identifier.value.casefold(): item
        for item in resource_list
        for identifier in item.identifiers
        if identifier.kind is IdentifierKind.DOI
    }
    relations: dict[str, ResourceRelation] = {}

    def add(
        source: CanonicalResource,
        target: CanonicalResource,
        relation: ResourceRelationType,
        verification: RelationVerification,
    ) -> None:
        if source.canonical_resource_id == target.canonical_resource_id:
            return
        identity = retrieval_id(
            "resource-relation",
            source.canonical_resource_id,
            target.canonical_resource_id,
            relation.value,
            *sorted(evidence_artifact_ids),
        )
        relations[identity] = ResourceRelation(
            relation_id=identity,
            source_resource_id=source.canonical_resource_id,
            target_resource_id=target.canonical_resource_id,
            relation=relation,
            verification_status=verification,
            evidence_artifact_ids=evidence_artifact_ids,
        )

    for resource in resource_list:
        metadata = resource.metadata
        paper_doi = str(
            metadata.get("paper_doi") or metadata.get("publication_doi") or ""
        ).casefold()
        if paper_doi and paper_doi in by_doi:
            add(
                by_doi[paper_doi],
                resource,
                ResourceRelationType.HAS_OFFICIAL_CODE,
                RelationVerification.VERIFIED_OFFICIAL,
            )
        repository = str(metadata.get("repository") or "").casefold()
        if (
            resource.resource_type
            in {CanonicalResourceType.PUBLICATION, CanonicalResourceType.PREPRINT}
            and repository in by_repo
        ):
            add(
                resource,
                by_repo[repository],
                ResourceRelationType.IMPLEMENTS,
                RelationVerification.STRONGLY_INFERRED,
            )
        base_model = metadata.get("base_model")
        for model_id in (
            [base_model] if isinstance(base_model, str) else base_model or []
        ):
            target = by_repo.get(str(model_id).casefold())
            if target is not None:
                add(
                    resource,
                    target,
                    ResourceRelationType.BASED_ON,
                    RelationVerification.VERIFIED_OFFICIAL,
                )
        training_data = metadata.get("training_dataset")
        for dataset_id in (
            [training_data] if isinstance(training_data, str) else training_data or []
        ):
            target = by_repo.get(str(dataset_id).casefold())
            if target is not None:
                add(
                    resource,
                    target,
                    ResourceRelationType.TRAINED_ON,
                    RelationVerification.VERIFIED_OFFICIAL,
                )
    return list(relations.values())


def build_access_decision(resource: CanonicalResource) -> AccessDecision:
    status = resource.access_status
    if status == "open_access":
        return AccessDecision(
            decision_id=retrieval_id(
                "access-decision", resource.canonical_resource_id, status
            ),
            resource_id=resource.canonical_resource_id,
            access_mode=AccessMode.OPEN_ACCESS,
            full_text_available=True,
            persistent_storage_allowed=True,
            model_processing_allowed=True,
            decision_basis="provider metadata reports an open-access copy",
        )
    if status == "link_only":
        mode = AccessMode.LINK_ONLY
    else:
        mode = AccessMode.METADATA_ONLY
    return AccessDecision(
        decision_id=retrieval_id(
            "access-decision", resource.canonical_resource_id, status
        ),
        resource_id=resource.canonical_resource_id,
        access_mode=mode,
        full_text_available=False,
        persistent_storage_allowed=True,
        model_processing_allowed=False,
        decision_basis=f"no rights-approved full text acquired; status={status}",
    )
