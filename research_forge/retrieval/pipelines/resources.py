from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

from ...claim_discovery import TrendSignal
from ..domain.models import (
    ExternalResource,
    MetadataVerificationStatus,
    ResourceType,
    retrieval_id,
)


_DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)


def canonical_doi(value: str) -> str | None:
    match = _DOI.search(value)
    return match.group(0).rstrip(".,;)").lower() if match else None


def normalized_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value).strip()


def _metadata_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def normalize_signals(signals: Iterable[TrendSignal]) -> list[ExternalResource]:
    resources: list[ExternalResource] = []
    for signal in signals:
        doi = canonical_doi(f"{signal.url} {signal.summary}")
        canonical = doi or signal.url
        declared_type = str(signal.metadata.get("resource_type") or "")
        try:
            resource_type = ResourceType(declared_type)
        except ValueError:
            resource_type = (
                ResourceType.WEB_SOURCE
                if signal.provider in {"redfox_wechat", "codex_native_web_search"}
                else ResourceType.PUBLICATION
            )
        metadata = {
            "title": signal.title,
            "summary": signal.summary,
            "published_at": signal.published_at,
            "source_name": signal.source_name,
            "terms": signal.terms,
            "attention": signal.engagement,
            "attention_score": signal.trend_score,
            "scientific_density": signal.scientific_density,
            "signal_class": signal.signal_class,
            "evidence_role": "attention_only",
            "trust_class": "untrusted_external_content",
            "instruction_authority": "none",
            **signal.metadata,
        }
        resources.append(
            ExternalResource(
                resource_id=retrieval_id(
                    "resource", resource_type.value, canonical.casefold()
                ),
                resource_type=resource_type,
                canonical_identifier=canonical,
                title=signal.title,
                authors_or_owners=([signal.source_name] if signal.source_name else []),
                publication_or_release_date=signal.published_at or None,
                doi=doi,
                url=signal.url,
                repository=(
                    str(signal.metadata.get("repository"))
                    if signal.metadata.get("repository")
                    else None
                ),
                commit=(
                    str(signal.metadata.get("commit"))
                    if signal.metadata.get("commit")
                    else None
                ),
                dataset_identifier=(
                    str(signal.metadata.get("dataset_identifier"))
                    if signal.metadata.get("dataset_identifier")
                    else None
                ),
                model_identifier=(
                    str(signal.metadata.get("model_identifier"))
                    if signal.metadata.get("model_identifier")
                    else None
                ),
                license=(
                    str(signal.metadata.get("license"))
                    if signal.metadata.get("license")
                    else None
                ),
                providers=[signal.provider],
                canonical_metadata_hash=_metadata_hash(metadata),
                metadata=metadata,
            )
        )
    return resources


@dataclass
class DeduplicationResult:
    resources: list[ExternalResource]
    merged_resource_ids: dict[str, list[str]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)


def _dedupe_key(resource: ExternalResource) -> str:
    if resource.resource_type is ResourceType.PUBLICATION:
        if resource.doi:
            return "doi:" + resource.doi
        year = (resource.publication_or_release_date or "")[:4]
        owner = (
            normalized_title(resource.authors_or_owners[0])
            if resource.authors_or_owners
            else ""
        )
        return f"title:{normalized_title(resource.title)}:{owner}:{year}"
    if resource.resource_type is ResourceType.CODE_REPOSITORY:
        return f"repo:{resource.repository or resource.url}:{resource.commit or ''}"
    if resource.resource_type is ResourceType.DATASET:
        return (
            f"dataset:{resource.dataset_identifier or resource.canonical_identifier}:"
            f"{resource.metadata.get('version', '')}"
        )
    return f"url:{resource.url or resource.canonical_identifier}"


def deduplicate_resources(
    resources: Iterable[ExternalResource],
) -> DeduplicationResult:
    groups: dict[str, list[ExternalResource]] = {}
    for resource in resources:
        groups.setdefault(_dedupe_key(resource), []).append(resource)
    merged: list[ExternalResource] = []
    lineage: dict[str, list[str]] = {}
    conflicts: list[str] = []
    for key, group in sorted(groups.items()):
        primary = group[0]
        providers = sorted({provider for item in group for provider in item.providers})
        hashes = {item.canonical_metadata_hash for item in group}
        titles = {normalized_title(item.title) for item in group}
        status = primary.metadata_verification_status
        metadata = dict(primary.metadata)
        if len(hashes) > 1 and len(titles) > 1:
            status = MetadataVerificationStatus.CONFLICT
            conflict = f"{key}: provider metadata disagree; manual review is required"
            conflicts.append(conflict)
            metadata["metadata_conflict"] = conflict
        primary = primary.model_copy(
            update={
                "providers": providers,
                "metadata_verification_status": status,
                "metadata": metadata,
            }
        )
        merged.append(primary)
        lineage[primary.resource_id] = [item.resource_id for item in group]
    return DeduplicationResult(merged, lineage, conflicts)


@dataclass
class VerificationResult:
    resources: list[ExternalResource]
    warnings: list[str] = field(default_factory=list)


def verify_resources(
    resources: Iterable[ExternalResource],
) -> VerificationResult:
    verified: list[ExternalResource] = []
    warnings: list[str] = []
    for resource in resources:
        status = resource.metadata_verification_status
        if status is MetadataVerificationStatus.CONFLICT:
            warnings.append(f"{resource.resource_id}: metadata conflict")
        elif resource.resource_type is ResourceType.PUBLICATION:
            if resource.doi:
                status = MetadataVerificationStatus.VERIFIED
            elif resource.url and urlparse(resource.url).scheme == "https":
                status = MetadataVerificationStatus.UNVERIFIED
                warnings.append(
                    f"{resource.resource_id}: publication has no verified DOI"
                )
            else:
                warnings.append(
                    f"{resource.resource_id}: publication identity is incomplete"
                )
        elif resource.url and urlparse(resource.url).scheme == "https":
            status = MetadataVerificationStatus.VERIFIED
        verified.append(
            resource.model_copy(update={"metadata_verification_status": status})
        )
    return VerificationResult(verified, warnings)
