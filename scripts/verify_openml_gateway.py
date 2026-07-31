"""Run one live, bounded OpenML Retrieval Gateway acceptance.

Generated datasets and retrieval state live in a temporary directory unless
``--workflow-root`` is explicitly supplied.  Only the small summary selected
by ``--output`` should be retained as evidence.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from research_forge.retrieval.domain.models import (
    ContractRef,
    NetworkMode,
    ResourceType,
    RetrievalBudget,
    RetrievalPhase,
    retrieval_id,
)
from research_forge.retrieval.interfaces.service import RetrievalGateway
from research_forge.retrieval.policy.engine import RetrievalNetworkPolicy
from research_forge.retrieval.providers.openml import OpenMLAdapter
from research_forge.retrieval.providers.registry import ProviderRegistry


def run_acceptance(workflow_root: Path, *, task_id: int) -> dict:
    adapter = OpenMLAdapter()
    gateway = RetrievalGateway(
        str(workflow_root),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    project_id = "openml-gateway-acceptance"
    study_id = f"openml-task-{task_id}-acceptance"
    policy = RetrievalNetworkPolicy(
        policy_id=retrieval_id(
            "network-policy", project_id, "openml-live-acceptance-v1"
        ),
        mode=NetworkMode.PUBLIC_RESEARCH,
        allowed_providers={"openml"},
        allowed_domains=set(adapter.domains),
        allowed_http_methods={"GET"},
        allowed_resource_types={ResourceType.DATASET},
        allow_dataset_download=True,
        max_queries=1,
        max_results=1,
        max_bytes=10_000_000,
        approved_by="research_forge_acceptance_owner",
        approved_at="2026-07-31T00:00:00+00:00",
    )
    gateway.set_policy(project_id, policy)

    discovery = gateway.plan(
        project_id=project_id,
        study_id=study_id,
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-openml-discover",
        purpose="dataset_discovery",
        queries=[f"task:{task_id}"],
        providers=["openml"],
        resource_types=[ResourceType.DATASET],
        usage_role="feasibility_signal",
        budget=RetrievalBudget(max_queries=1, max_results=1),
        idempotency_key=f"openml-task-{task_id}-metadata-v1",
        freshness="live",
    )
    discovery_result = gateway.run(discovery.request_id)
    resources = gateway.repository.list_resources()
    if discovery_result.resource_set is None or len(resources) != 1:
        raise RuntimeError("OpenML discovery did not create exactly one resource")
    resource = resources[0]
    dataset_identifier = str(resource.dataset_identifier or "")

    acquisition_request = gateway.plan(
        project_id=project_id,
        study_id=study_id,
        phase=RetrievalPhase.EXPERIMENTATION,
        step_instance_id="step-openml-acquire",
        purpose="fetch_approved_dataset",
        queries=[dataset_identifier],
        providers=["openml"],
        resource_types=[ResourceType.DATASET],
        usage_role="approved_experiment_resource",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=1,
            max_download_bytes=10_000_000,
        ),
        idempotency_key=f"{dataset_identifier}-acquisition-v1",
        contract_refs=[
            ContractRef(
                contract_type="research",
                contract_id=f"{study_id}:research-v1",
                version=1,
                field="approved_resource_ids",
            )
        ],
        freshness="live",
        require_search_execution=False,
    )
    acquisition = gateway.acquire_approved_dataset(
        request_id=acquisition_request.request_id,
        resource_id=resource.resource_id,
    )
    report_path = Path(
        gateway.repository.load_artifact(acquisition.report_artifact_id).path
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "task_id": task_id,
        "dataset_identifier": dataset_identifier,
        "resource_id": resource.resource_id,
        "metadata_verification_status": resource.metadata_verification_status.value,
        "discovery_status": discovery_result.run.execution_status.value,
        "dataset_snapshot_id": acquisition.snapshot.snapshot_id,
        "dataset_content_sha256": acquisition.snapshot.content_hash,
        "dataset_byte_size": acquisition.snapshot.byte_size,
        "provider_md5": report["provider_md5"],
        "provider_md5_verified": report["provider_md5_verified"],
        "raw_bytes_frozen_before_consumption": report[
            "raw_bytes_frozen_before_consumption"
        ],
        "model_processing_allowed": acquisition.snapshot.model_processing_allowed,
        "executed": report["executed"],
        "network_policy_id": policy.policy_id,
        "contract_field": "approved_resource_ids",
        "generated_dataset_retained": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", type=int, default=37)
    parser.add_argument("--workflow-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.workflow_root:
        args.workflow_root.mkdir(parents=True, exist_ok=True)
        summary = run_acceptance(args.workflow_root, task_id=args.task_id)
    else:
        with tempfile.TemporaryDirectory(prefix="rf-openml-gateway-") as root:
            summary = run_acceptance(Path(root), task_id=args.task_id)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
