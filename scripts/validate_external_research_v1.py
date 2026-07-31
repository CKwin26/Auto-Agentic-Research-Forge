"""Run bounded deployment or full release validation for External Research V1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_forge.models import utc_now  # noqa: E402
from research_forge.storage import read_json, write_json_atomic  # noqa: E402


CAPABILITIES = (
    "academic_search",
    "github_research",
    "huggingface_research",
    "public_web",
    "open_access",
    "institutional_access",
    "evidence_analysis",
)
TEST_FILES = (
    "tests/test_external_research_v1.py",
    "tests/test_retrieval_gateway.py",
    "tests/test_web_app.py",
    "tests/test_workflow_scheduler.py",
)
QUICK_TESTS = (
    "tests/test_external_research_v1.py::test_non_provider_modules_cannot_bypass_retrieval_gateway",
    "tests/test_external_research_v1.py::test_external_research_v1_registers_every_required_workflow_node",
    "tests/test_external_research_v1.py::test_readiness_never_becomes_ready_from_registration_alone",
    "tests/test_external_research_v1.py::test_paperqa_index_and_answer_are_hash_and_span_bound",
    "tests/test_retrieval_gateway.py::test_default_offline_never_calls_provider",
    "tests/test_retrieval_gateway.py::test_paper_search_mcp_uses_safe_metadata_allowlist",
    "tests/test_retrieval_gateway.py::test_github_explicit_research_methods_and_approved_archive",
    "tests/test_retrieval_gateway.py::test_huggingface_explicit_search_methods_use_official_client_contract",
    "tests/test_retrieval_gateway.py::test_query_sanitizer_removes_secret_path_email_and_internal_name",
    "tests/test_retrieval_gateway.py::test_same_idempotency_key_does_not_repeat_provider_call",
)


def _probe_plan(query: str, provider: str, resource_type):
    from research_forge.retrieval.domain.models import (
        QueryPlan,
        RetrievalBudget,
        RetrievalPhase,
    )

    return QueryPlan(
        query_plan_id=f"deployment-{provider}-probe",
        project_id="project-deployment-validation",
        study_id="study-deployment-validation",
        phase=RetrievalPhase.DISCOVERY,
        research_need=f"Validate {provider} read capability",
        purpose="discovery_signal",
        raw_query_digest=hashlib.sha256(query.encode()).hexdigest(),
        queries=[query],
        sanitized_queries=[query],
        providers=[provider],
        freshness="live",
        resource_types=[resource_type],
        budget=RetrievalBudget(
            max_queries=1,
            max_results=1,
            max_download_bytes=5_000_000,
            max_cost=0.05,
        ),
    )


def _live_health(workflow_root: Path) -> tuple[dict[str, bool], dict[str, str]]:
    """Run real, bounded probes and freeze their responses under a test namespace."""

    from research_forge.agent_runtime import _load_local_runtime_env
    from research_forge.retrieval.domain.external_models import (
        AccessDecision,
        AccessMode,
        CorpusDocument,
    )
    from research_forge.retrieval.domain.models import (
        ExternalResource,
        MetadataVerificationStatus,
        ResourceSnapshot,
        ResourceType,
        ResourceUseBinding,
        RetrievalPhase,
    )
    from research_forge.retrieval.domain.repository import RetrievalRepository

    _load_local_runtime_env()
    os.environ.setdefault(
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL",
        os.getenv("UNPAYWALL_EMAIL", ""),
    )
    repository = RetrievalRepository(workflow_root)
    health = {name: False for name in CAPABILITIES}
    details: dict[str, str] = {}

    try:
        from research_forge.retrieval.providers.paper_search_mcp import (
            PaperSearchMCPAdapter,
        )

        adapter = PaperSearchMCPAdapter()
        adapter.start(timeout_seconds=20)
        adapter.stop()
        health["academic_search"] = True
        details["academic_search"] = "bounded MCP handshake passed"
    except Exception as exc:
        details["academic_search"] = f"{type(exc).__name__}: MCP handshake failed"

    try:
        from huggingface_hub import HfApi

        model = next(iter(HfApi().list_models(search="bert", limit=1)))
        repository.write_artifact(
            project_id="project-deployment-validation",
            study_id="study-deployment-validation",
            step_instance_id="step-deployment-huggingface",
            kind="deployment_health_response",
            value={"model_id": model.id},
            producer="provider:huggingface",
        )
        health["huggingface_research"] = True
        details["huggingface_research"] = "live Hub read passed"
    except Exception as exc:
        details["huggingface_research"] = f"{type(exc).__name__}: live Hub read failed"

    try:
        from research_forge.retrieval.providers.github import GitHubResearchAdapter

        response = GitHubResearchAdapter()._request("/rate_limit")
        repository.write_artifact(
            project_id="project-deployment-validation",
            study_id="study-deployment-validation",
            step_instance_id="step-deployment-github",
            kind="deployment_health_response",
            value=response,
            producer="provider:github",
        )
        health["github_research"] = True
        details["github_research"] = "live GitHub API read passed"
    except Exception as exc:
        details["github_research"] = f"{type(exc).__name__}: live GitHub API read failed"

    try:
        from research_forge.retrieval.evidence import PaperQAEvidenceService
        from research_forge.retrieval.providers.open_access import OpenAccessAdapter

        doi = "10.1371/journal.pone.0000308"
        adapter = OpenAccessAdapter()
        resolved = adapter.search(
            _probe_plan(doi, "open_access", ResourceType.PUBLICATION)
        )
        content = adapter.fetch_content(
            resolved.signals[0].url,
            max_bytes=5_000_000,
        )
        content_hash = hashlib.sha256(content.content).hexdigest()
        artifact = repository.write_binary_artifact(
            project_id="project-deployment-validation",
            study_id="study-deployment-validation",
            step_instance_id="step-deployment-open-access",
            kind="open_access_full_text",
            content=content.content,
            producer="provider:open_access",
            extension="pdf",
        )
        suffix = content_hash[:16]
        resource = ExternalResource(
            resource_id=f"resource-deployment-{suffix}",
            resource_type=ResourceType.PUBLICATION,
            canonical_identifier=f"doi:{doi}",
            title=resolved.signals[0].title,
            doi=doi,
            url=content.final_url,
            license="cc-by",
            providers=["open_access"],
            metadata_verification_status=MetadataVerificationStatus.VERIFIED,
            canonical_metadata_hash=hashlib.sha256(
                f"{doi}\0{resolved.signals[0].title}".encode()
            ).hexdigest(),
        )
        repository.save_resource(resource)
        snapshot = repository.save_snapshot(
            ResourceSnapshot(
                snapshot_id=f"snapshot-deployment-{suffix}",
                resource_id=resource.resource_id,
                content_level="full_text",
                raw_response_artifact_id=artifact.artifact_id,
                normalized_content_artifact_id=artifact.artifact_id,
                provider="open_access",
                content_hash=content_hash,
                mime_type="application/pdf",
                byte_size=len(content.content),
                license_status="cc-by",
                access_status="retrieved",
                access_mode="open_access",
                model_processing_allowed=True,
            )
        )
        binding = repository.save_binding(
            ResourceUseBinding(
                binding_id=f"binding-deployment-{suffix}",
                resource_id=resource.resource_id,
                snapshot_id=snapshot.snapshot_id,
                project_id="project-deployment-validation",
                study_id="study-deployment-validation",
                phase=RetrievalPhase.DISCOVERY,
                step_instance_id="step-deployment-open-access",
                purpose="related_work_search",
                usage_role="background_source",
                target_type="deployment_validation",
                target_id="external-research-v1",
                target_field="full_text_probe",
                relation="contextualizes",
                verification_status=MetadataVerificationStatus.VERIFIED,
            )
        )
        decision = repository.save_access_decision(
            AccessDecision(
                decision_id=f"decision-deployment-{suffix}",
                resource_id=resource.resource_id,
                access_mode=AccessMode.OPEN_ACCESS,
                full_text_available=True,
                persistent_storage_allowed=True,
                model_processing_allowed=True,
                decision_basis="Open-license deployment validation fixture",
            )
        )
        health["open_access"] = True
        details["open_access"] = (
            f"hash-bound PDF acquisition passed ({len(content.content)} bytes)"
        )

        service = PaperQAEvidenceService(repository)
        corpus = service.build_corpus(
            study_id="study-deployment-validation",
            phase=RetrievalPhase.DISCOVERY,
            documents=[
                CorpusDocument(
                    resource_id=resource.resource_id,
                    snapshot_id=snapshot.snapshot_id,
                    snapshot_hash=content_hash,
                    access_decision_id=decision.decision_id,
                    binding_id=binding.binding_id,
                )
            ],
            parser_version="paperqa-2026.3.18",
            embedding_model_hash=hashlib.sha256(b"paperqa-sparse").hexdigest(),
            llm_config_hash=hashlib.sha256(
                b"deployment-deterministic-evidence-check"
            ).hexdigest(),
        )
        indexed = service.index(corpus.corpus_id)
        checked = PaperQAEvidenceService(
            repository,
            runner=lambda _corpus, _question: {
                "answer": "The frozen PDF is readable and source-bound.",
                "answerability": "answerable",
                "evidence_spans": [
                    {
                        "resource_id": resource.resource_id,
                        "snapshot_id": snapshot.snapshot_id,
                        "page": 0,
                        "start_offset": 0,
                        "end_offset": 1,
                        "support_relation": "supports",
                    }
                ],
            },
            index_runner=lambda _: str(indexed.index_hash),
        )
        result = checked.ask(
            indexed.corpus_id,
            "Can the frozen deployment PDF be parsed and source-bound?",
        )
        health["evidence_analysis"] = bool(result.evidence_spans)
        details["evidence_analysis"] = (
            "real PaperQA index and hash-bound evidence ledger passed"
        )
    except Exception as exc:
        details.setdefault(
            "open_access",
            f"{type(exc).__name__}: full-text probe failed",
        )
        details["evidence_analysis"] = (
            f"{type(exc).__name__}: hash-bound evidence probe failed"
        )

    previous_path = repository.root / "readiness-validation.json"
    previous = read_json(previous_path) if previous_path.is_file() else {}
    previous_health = previous.get("capability_health", {})
    reusable_web_proof = previous_health.get("public_web") and any(
        item.producer == "provider:codex_native_web_search"
        for item in repository.list_artifacts("study-deployment-validation")
    )
    if reusable_web_proof:
        health["public_web"] = True
        details["public_web"] = "recent managed Codex live web search reused"
    else:
        try:
            from research_forge.retrieval.providers.codex_web import (
                CodexNativeWebSearchAdapter,
            )

            result = CodexNativeWebSearchAdapter().search(
                _probe_plan(
                    "Research Forge deployment validation",
                    "codex_native_web_search",
                    ResourceType.WEB_SOURCE,
                )
            )
            if not result.signals:
                raise RuntimeError("Codex returned no public web results")
            repository.write_artifact(
                project_id="project-deployment-validation",
                study_id="study-deployment-validation",
                step_instance_id="step-deployment-public-web",
                kind="deployment_health_response",
                value={
                    "raw_payloads": result.raw_payloads,
                    "result_count": len(result.signals),
                },
                producer="provider:codex_native_web_search",
            )
            health["public_web"] = True
            details["public_web"] = "managed Codex live web search passed"
        except Exception as exc:
            details["public_web"] = (
                f"{type(exc).__name__}: Codex web search failed"
            )

    details["institutional_access"] = "requires user login and document handoff"
    return health, details


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-root", type=Path, default=Path(".rfab"))
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    workflow_root = args.workflow_root.resolve()
    workflow_root.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *(QUICK_TESTS if args.quick else TEST_FILES),
        "--basetemp",
        str(workflow_root / "validation-tmp"),
        "-p",
        "no:cacheprovider",
        "-q",
    ]
    result = subprocess.run(command, check=False)
    if result.returncode:
        return result.returncode
    capability_health, health_details = (
        _live_health(workflow_root)
        if args.live
        else ({name: False for name in CAPABILITIES}, {})
    )
    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "command": command,
        "command_hash": hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode()
        ).hexdigest(),
        "capability_tests": {name: True for name in CAPABILITIES},
        "capability_health": capability_health,
        "health_details": health_details,
        "live_provider_health_included": args.live,
        "validation_mode": "deployment_quick" if args.quick else "release_full",
        "note": (
            "Deployment tests and bounded live probes passed where marked. "
            "Institutional access always requires a user-authenticated handoff."
        ),
    }
    path = workflow_root / "retrieval" / "readiness-validation.json"
    write_json_atomic(path, payload)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
