from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research_forge.retrieval.domain.models import (
    ContractRef,
    ExternalResource,
    MetadataVerificationStatus,
    NetworkMode,
    ProviderErrorClass,
    QueryPlan,
    ResourceType,
    RetrievalBudget,
    RetrievalPhase,
    RetrievalStatus,
    retrieval_id,
)
from research_forge.retrieval.interfaces.service import RetrievalGateway
from research_forge.retrieval.policy.engine import RetrievalNetworkPolicy
from research_forge.retrieval.providers.base import ProviderFailure
from research_forge.retrieval.providers.openml import OpenMLAdapter
from research_forge.retrieval.providers.registry import ProviderRegistry


TASK = {
    "task_id": "37",
    "task_name": "Task 37: diabetes (Supervised Classification)",
    "task_type_id": "1",
    "task_type": "Supervised Classification",
    "input": [
        {
            "name": "source_data",
            "data_set": {"data_set_id": "37", "target_feature": "class"},
        },
        {
            "name": "estimation_procedure",
            "estimation_procedure": {
                "id": "1",
                "type": "crossvalidation",
                "data_splits_url": (
                    "https://openml.org/api_splits/get/37/Task_37_splits.arff"
                ),
                "parameter": [
                    {"name": "number_repeats", "value": "1"},
                    {"name": "number_folds", "value": "10"},
                ],
            },
        },
    ],
}


def _dataset(dataset_bytes: bytes) -> dict:
    return {
        "id": "37",
        "name": "diabetes",
        "version": "1",
        "format": "ARFF",
        "creator": "OpenML test fixture",
        "upload_date": "2014-04-06T23:22:13",
        "licence": "CC BY 4.0",
        "url": "https://openml.org/data/v1/download/37/diabetes.arff",
        "parquet_url": "https://data.openml.org/datasets/0000/0037/dataset_37.pq",
        "file_id": "37",
        "default_target_attribute": "class",
        "visibility": "public",
        "status": "active",
        "md5_checksum": hashlib.md5(
            dataset_bytes, usedforsecurity=False
        ).hexdigest(),
    }


def _adapter(dataset_bytes: bytes, *, wrong_bytes: bytes | None = None):
    dataset = _dataset(dataset_bytes)
    calls = {"metadata": 0, "content": 0}

    def transport(request):
        calls["metadata"] += 1
        if "/api/v1/json/task/37" in request.full_url:
            return {"task": TASK}
        if "/api/v1/json/data/37" in request.full_url:
            return {"data_set_description": dataset}
        raise AssertionError(request.full_url)

    def content_transport(request, *, max_bytes: int, url_validator):
        calls["content"] += 1
        assert max_bytes == 100_000
        url_validator(request.full_url)
        final_url = "https://data.openml.org/datasets/0000/0037/dataset_37.arff"
        url_validator(final_url)
        return (
            wrong_bytes if wrong_bytes is not None else dataset_bytes,
            final_url,
            "application/x-arff",
            {"etag": "fixture"},
        )

    return (
        OpenMLAdapter(transport=transport, content_transport=content_transport),
        dataset,
        calls,
    )


def _query_plan() -> QueryPlan:
    return QueryPlan(
        query_plan_id="query-plan-openml-task-37",
        project_id="project-openml",
        study_id="study-openml",
        phase=RetrievalPhase.DISCOVERY,
        research_need="resolve official benchmark task",
        purpose="dataset_discovery",
        raw_query_digest="a" * 64,
        queries=["task:37"],
        sanitized_queries=["task:37"],
        providers=["openml"],
        resource_types=[ResourceType.DATASET],
        budget=RetrievalBudget(max_queries=1, max_results=1),
    )


def _gateway(tmp_path: Path, adapter: OpenMLAdapter) -> RetrievalGateway:
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy(
        "project-openml",
        RetrievalNetworkPolicy(
            policy_id=retrieval_id(
                "network-policy", "project-openml", "approved-openml-v1"
            ),
            mode=NetworkMode.PUBLIC_RESEARCH,
            allowed_providers={"openml"},
            allowed_domains=set(adapter.domains),
            allowed_http_methods={"GET"},
            allowed_resource_types={ResourceType.DATASET},
            allow_dataset_download=True,
            max_queries=1,
            max_results=1,
            max_bytes=100_000,
            approved_by="project_owner",
            approved_at="2026-07-31T00:00:00+00:00",
        ),
    )
    return gateway


def _resource(gateway: RetrievalGateway, dataset: dict) -> ExternalResource:
    resource = ExternalResource(
        resource_id="resource-openml-dataset-37-v1",
        resource_type=ResourceType.DATASET,
        canonical_identifier="https://www.openml.org/d/37",
        dataset_identifier="openml-dataset:37:v1",
        title="diabetes — OpenML dataset 37",
        url="https://www.openml.org/d/37",
        license="cc-by-4.0",
        providers=["openml"],
        metadata_verification_status=MetadataVerificationStatus.VERIFIED,
        canonical_metadata_hash=hashlib.sha256(
            b"openml-dataset:37:v1"
        ).hexdigest(),
        metadata={
            "version": "1",
            "download_url": dataset["url"],
            "md5_checksum": dataset["md5_checksum"],
            "format": "ARFF",
            "access_status": "open_access",
        },
    )
    gateway.repository.save_resource(resource)
    return resource


def _acquisition_request(
    gateway: RetrievalGateway,
    resource: ExternalResource,
    *,
    include_contract: bool = True,
):
    return gateway.plan(
        project_id="project-openml",
        study_id="study-openml",
        phase=RetrievalPhase.EXPERIMENTATION,
        step_instance_id="step-" + "7" * 16,
        purpose="fetch_approved_dataset",
        queries=[resource.dataset_identifier or ""],
        providers=["openml"],
        resource_types=[ResourceType.DATASET],
        usage_role="approved_experiment_resource",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=1,
            max_download_bytes=100_000,
        ),
        idempotency_key=(
            "openml-dataset-acquisition-with-contract"
            if include_contract
            else "openml-dataset-acquisition-without-contract"
        ),
        contract_refs=(
            [
                ContractRef(
                    contract_type="research",
                    contract_id="study-openml:research-v1",
                    version=1,
                    field="approved_resource_ids",
                )
            ]
            if include_contract
            else []
        ),
        freshness="live",
        require_search_execution=False,
    )


def test_openml_adapter_resolves_task_dataset_split_and_version() -> None:
    adapter, dataset, _calls = _adapter(b"@RELATION diabetes\n")

    result = adapter.search(_query_plan())

    assert len(result.raw_payloads) == 2
    assert result.signals[0].metadata["dataset_identifier"] == (
        "openml-dataset:37:v1"
    )
    assert result.signals[0].metadata["target_feature"] == "class"
    assert result.signals[0].metadata["estimation_procedure"]["type"] == (
        "crossvalidation"
    )
    assert result.signals[0].metadata["md5_checksum"] == dataset["md5_checksum"]
    with pytest.raises(ProviderFailure, match="exact task"):
        adapter.search(
            _query_plan().model_copy(
                update={"queries": ["diabetes"], "sanitized_queries": ["diabetes"]}
            )
        )


def test_gateway_freezes_openml_task_metadata_before_normalization(
    tmp_path: Path,
) -> None:
    adapter, dataset, calls = _adapter(b"@RELATION diabetes\n")
    gateway = _gateway(tmp_path, adapter)
    request = gateway.plan(
        project_id="project-openml",
        study_id="study-openml",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "3" * 16,
        purpose="dataset_discovery",
        queries=["task:37"],
        providers=["openml"],
        resource_types=[ResourceType.DATASET],
        usage_role="feasibility_signal",
        budget=RetrievalBudget(max_queries=1, max_results=1),
        idempotency_key="openml-task-37-discovery",
        freshness="live",
    )

    execution = gateway.run(request.request_id)

    assert execution.run.execution_status is RetrievalStatus.SUCCEEDED
    assert execution.resource_set is not None
    resources = gateway.repository.list_resources()
    assert len(resources) == 1
    resource = resources[0]
    assert resource.dataset_identifier == "openml-dataset:37:v1"
    assert resource.metadata["md5_checksum"] == dataset["md5_checksum"]
    raw = [
        item
        for item in gateway.repository.list_artifacts("study-openml")
        if item.kind.startswith("provider_raw_response_openml_")
    ]
    assert len(raw) == 2
    assert calls["metadata"] == 2


def test_gateway_freezes_checksum_verified_openml_dataset_and_reuses_it(
    tmp_path: Path,
) -> None:
    dataset_bytes = b"@RELATION diabetes\n@DATA\n1,0\n"
    adapter, dataset, calls = _adapter(dataset_bytes)
    gateway = _gateway(tmp_path, adapter)
    resource = _resource(gateway, dataset)
    request = _acquisition_request(gateway, resource)

    acquired = gateway.acquire_approved_dataset(
        request_id=request.request_id,
        resource_id=resource.resource_id,
    )

    assert acquired.snapshot.content_level == "dataset_file"
    assert acquired.snapshot.model_processing_allowed is True
    assert acquired.snapshot.content_hash == hashlib.sha256(dataset_bytes).hexdigest()
    assert Path(gateway.repository.load_artifact(acquired.artifact_id).path).suffix == (
        ".arff"
    )
    report = json.loads(
        Path(
            gateway.repository.load_artifact(acquired.report_artifact_id).path
        ).read_text(encoding="utf-8")
    )
    assert report["provider_md5_verified"] is True
    assert report["raw_bytes_frozen_before_consumption"] is True
    assert report["executed"] is False
    assert calls["content"] == 1

    replay = gateway.acquire_approved_dataset(
        request_id=request.request_id,
        resource_id=resource.resource_id,
    )
    assert replay.snapshot.snapshot_id == acquired.snapshot.snapshot_id
    assert calls["content"] == 1
    run = gateway.repository.run_for_request(request.request_id)
    assert run is not None
    assert run.execution_status is RetrievalStatus.SUCCEEDED
    assert run.budget_usage.download_bytes == 0


def test_gateway_rejects_openml_checksum_mismatch(tmp_path: Path) -> None:
    expected = b"@RELATION expected\n"
    adapter, dataset, _calls = _adapter(expected, wrong_bytes=b"tampered")
    gateway = _gateway(tmp_path, adapter)
    resource = _resource(gateway, dataset)
    request = _acquisition_request(gateway, resource)

    with pytest.raises(ProviderFailure, match="checksum mismatch") as error:
        gateway.acquire_approved_dataset(
            request_id=request.request_id,
            resource_id=resource.resource_id,
        )

    assert error.value.classification is ProviderErrorClass.METADATA_CONFLICT
    assert not gateway.repository.list_snapshots(resource.resource_id)
    run = gateway.repository.run_for_request(request.request_id)
    assert run is not None
    assert run.execution_status is RetrievalStatus.FAILED


def test_gateway_rejects_dataset_acquisition_without_research_contract(
    tmp_path: Path,
) -> None:
    adapter, dataset, calls = _adapter(b"@RELATION diabetes\n")
    gateway = _gateway(tmp_path, adapter)
    resource = _resource(gateway, dataset)
    request = _acquisition_request(gateway, resource, include_contract=False)

    with pytest.raises(PermissionError, match="Research Contract"):
        gateway.acquire_approved_dataset(
            request_id=request.request_id,
            resource_id=resource.resource_id,
        )

    assert calls["content"] == 0


def test_gateway_requires_explicit_dataset_field_binding(tmp_path: Path) -> None:
    adapter, dataset, calls = _adapter(b"@RELATION diabetes\n")
    gateway = _gateway(tmp_path, adapter)
    resource = _resource(gateway, dataset)
    request = gateway.plan(
        project_id="project-openml",
        study_id="study-openml",
        phase=RetrievalPhase.EXPERIMENTATION,
        step_instance_id="step-" + "8" * 16,
        purpose="fetch_approved_dataset",
        queries=[resource.dataset_identifier or ""],
        providers=["openml"],
        resource_types=[ResourceType.DATASET],
        usage_role="approved_experiment_resource",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=1,
            max_download_bytes=100_000,
        ),
        idempotency_key="openml-dataset-generic-contract-ref",
        contract_refs=[
            ContractRef(
                contract_type="research",
                contract_id="study-openml:research-v1",
                version=1,
            )
        ],
        freshness="live",
        require_search_execution=False,
    )

    with pytest.raises(PermissionError, match="approved dataset field"):
        gateway.acquire_approved_dataset(
            request_id=request.request_id,
            resource_id=resource.resource_id,
        )

    assert calls["content"] == 0
