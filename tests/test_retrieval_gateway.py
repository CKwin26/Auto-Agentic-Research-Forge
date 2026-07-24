from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from research_forge.claim_discovery import TrendSignal
from research_forge.cli import main as cli_main
from research_forge.retrieval.domain.models import (
    ContractRef,
    MetadataVerificationStatus,
    NetworkMode,
    QueryPlan,
    ResourceType,
    RetrievalBudget,
    RetrievalPhase,
    RetrievalStatus,
    retrieval_id,
)
from research_forge.retrieval.domain.external_models import (
    CapabilityState,
    ExternalCapability,
)
from research_forge.retrieval.interfaces.readiness import ReadinessService
from research_forge.retrieval.interfaces.service import RetrievalGateway
from research_forge.retrieval.pipelines.resources import (
    deduplicate_resources,
    normalize_signals,
)
from research_forge.retrieval.policy.engine import RetrievalNetworkPolicy
from research_forge.retrieval.policy.sanitizer import QuerySanitizer
from research_forge.retrieval.providers.base import (
    CredentialResolver,
    ProviderAdapter,
    ProviderContentResult,
    ProviderFailure,
    ProviderSearchResult,
)
from research_forge.retrieval.providers.github import GitHubResearchAdapter
from research_forge.retrieval.providers.huggingface import (
    HuggingFaceResearchAdapter,
)
from research_forge.retrieval.providers.paperqa import PaperQAAdapter
from research_forge.retrieval.providers.redfox import RedFoxAdapter
from research_forge.retrieval.providers.codex_web import (
    CodexNativeWebSearchAdapter,
    _json_safe,
)
from research_forge.retrieval.providers.paper_search_mcp import (
    DEFAULT_PUBLIC_SOURCES,
    PaperSearchMCPAdapter,
    SAFE_SOURCES,
)
from research_forge.retrieval.providers.open_access import (
    OpenAccessAdapter,
    _validate_public_https_url,
)
from research_forge.retrieval.providers.openai_web import OpenAIWebSearchAdapter
from research_forge.retrieval.providers.registry import ProviderRegistry
from research_forge.retrieval.domain.models import ProviderErrorClass
from research_forge.workflow_domain import WorkflowRepository


def _signal(provider: str, *, title: str = "Evidence Bound Agents") -> TrendSignal:
    return TrendSignal(
        signal_id=f"signal-{provider}",
        provider=provider,  # type: ignore[arg-type]
        signal_class=(
            "market_attention" if provider == "redfox_wechat" else "scholarly_attention"
        ),
        query="evidence agents",
        title=title,
        summary="A controlled evaluation. DOI 10.1234/example",
        url="https://doi.org/10.1234/example",
        published_at="2026",
        source_name="Research Group",
        trend_score=0.8,
        scientific_density=0.7,
    )


class FakeAdapter(ProviderAdapter):
    provider_id = "semantic_scholar"
    domains = {"api.semanticscholar.org"}

    def __init__(
        self,
        *,
        fail=None,
        fail_times: int | None = None,
        provider_id: str = "semantic_scholar",
    ):
        super().__init__()
        self.provider_id = provider_id
        self.domains = {
            "api.crossref.org"
            if provider_id == "crossref"
            else "api.semanticscholar.org"
        }
        self.calls = 0
        self.fail = fail
        self.fail_times = fail_times

    def search(self, plan):
        self.calls += 1
        if self.fail and (self.fail_times is None or self.calls <= self.fail_times):
            raise ProviderFailure("fake failure", self.fail)
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=[{"data": [{"title": "Evidence Bound Agents"}]}],
            signals=[_signal(self.provider_id)],
            domains=sorted(self.domains),
        )


def _authorized_policy(project_id: str) -> RetrievalNetworkPolicy:
    return RetrievalNetworkPolicy(
        policy_id=retrieval_id("network-policy", project_id, "academic-read-v1"),
        mode=NetworkMode.ACADEMIC_READ,
        allowed_providers={"semantic_scholar"},
        allowed_domains={"api.semanticscholar.org"},
        allowed_http_methods={"GET"},
        allowed_resource_types={ResourceType.PUBLICATION},
        allow_abstract=True,
        max_queries=10,
        max_results=100,
        max_bytes=1_000_000,
        approved_by="project_owner",
        approved_at="2026-07-23T00:00:00+00:00",
    )


def _plan(gateway: RetrievalGateway, project_id: str = "project-demo"):
    return gateway.plan(
        project_id=project_id,
        study_id="study-demo",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "1" * 16,
        purpose="related_work_search",
        queries=["evidence bound autonomous research agents"],
        providers=["semantic_scholar"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(
            max_queries=3,
            max_results=20,
            max_download_bytes=100_000,
        ),
        idempotency_key="discovery-demo-v1",
        freshness="live",
    )


def test_default_offline_never_calls_provider(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    request = _plan(gateway)

    execution = gateway.run(request.request_id)

    assert execution.run.execution_status is RetrievalStatus.BLOCKED
    assert execution.run.error_classification is ProviderErrorClass.POLICY_DENIED
    assert adapter.calls == 0
    assert not gateway.repository.list_resources()


def test_codex_native_web_search_accepts_only_structured_search_items() -> None:
    adapter = CodexNativeWebSearchAdapter(
        runner=lambda _plan: {
            "items": [{"type": "webSearch", "query": "auditable agents"}],
            "final_response": json.dumps(
                {
                    "queries": ["auditable agents"],
                    "consulted_sources": [
                        {
                            "title": "Official specification",
                            "url": "https://example.org/spec",
                        }
                    ],
                    "results": [
                        {
                            "title": "Official specification",
                            "url": "https://example.org/spec",
                            "snippet": "A bounded research-agent specification.",
                            "published_at": "2026-07-01",
                            "source_name": "Example Standards",
                        }
                    ],
                }
            ),
        }
    )
    plan = QueryPlan(
        query_plan_id="query-plan-test",
        project_id="project-test",
        study_id="study-test",
        phase=RetrievalPhase.DISCOVERY,
        research_need="official documentation",
        purpose="related_work_search",
        raw_query_digest="a" * 64,
        queries=["auditable agents"],
        sanitized_queries=["auditable agents"],
        providers=[adapter.provider_id],
        resource_types=[ResourceType.WEB_SOURCE],
        budget=RetrievalBudget(max_queries=1, max_results=5),
        allowed_domains=["example.org"],
    )

    result = adapter.search(plan)

    assert result.signals[0].url == "https://example.org/spec"
    assert result.signals[0].provider == "codex_native_web_search"
    assert result.query_records == [{"query": "auditable agents"}]
    assert result.source_records == [
        {
            "title": "Official specification",
            "url": "https://example.org/spec",
        }
    ]
    assert result.citation_records == [
        {
            "title": "Official specification",
            "url": "https://example.org/spec",
            "source_name": "Example Standards",
        }
    ]
    assert adapter.parse_web_search_calls(
        {"items": [{"type": "webSearch", "query": "auditable agents"}]}
    )
    with pytest.raises(ProviderFailure, match="cache miss"):
        adapter.search_cached_only(plan)


def test_formal_web_search_freezes_queries_sources_and_citations_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    adapter = OpenAIWebSearchAdapter(
        runner=lambda _plan: {
            "output": [{"type": "web_search_call"}],
            "output_text": json.dumps(
                {
                    "queries": ["official docs"],
                    "consulted_sources": [
                        {
                            "title": "Official documentation",
                            "url": "https://platform.openai.com/docs",
                        }
                    ],
                    "results": [
                        {
                            "title": "Web search guide",
                            "url": "https://platform.openai.com/docs/guides/tools-web-search",
                            "snippet": "Official guide.",
                            "published_at": "2026",
                            "source_name": "OpenAI",
                        }
                    ],
                }
            ),
        }
    )
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy(
        "project-web-records",
        RetrievalNetworkPolicy(
            policy_id="network-policy-web-records",
            mode=NetworkMode.PUBLIC_WEB_READ,
            allowed_providers={"openai_web_search"},
            allowed_domains={"api.openai.com"},
            allowed_http_methods={"POST"},
            allowed_resource_types={ResourceType.WEB_SOURCE},
            allow_abstract=True,
            max_queries=1,
            max_results=5,
            max_bytes=100_000,
            approved_by="project_owner",
            approved_at="2026-07-23T00:00:00+00:00",
        ),
    )
    request = gateway.plan(
        project_id="project-web-records",
        study_id="study-web-records",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "8" * 16,
        purpose="related_work_search",
        queries=["official docs"],
        providers=["openai_web_search"],
        resource_types=[ResourceType.WEB_SOURCE],
        usage_role="background_source",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=5,
            max_download_bytes=100_000,
        ),
        idempotency_key="formal-web-record-separation-v1",
        freshness="live",
    )
    execution = gateway.run(request.request_id)
    kinds = {
        gateway.repository.load_artifact(item).kind
        for item in execution.run.output_artifact_ids
    }
    assert {
        "provider_query_records_openai_web_search",
        "provider_source_records_openai_web_search",
        "provider_citation_records_openai_web_search",
    }.issubset(kinds)


def test_formal_web_search_explicit_methods_parse_actions_and_citations() -> None:
    raw = {
        "output": [
            {
                "type": "web_search_call",
                "id": "ws_1",
                "action": {
                    "type": "search",
                    "query": "official docs",
                    "sources": [
                        {
                            "title": "Official documentation",
                            "url": "https://docs.example.org/spec",
                        }
                    ],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://docs.example.org/spec",
                                "title": "Official documentation",
                            }
                        ],
                    }
                ],
            },
        ],
        "output_text": json.dumps(
            {
                "queries": ["official docs"],
                "consulted_sources": [],
                "results": [
                    {
                        "title": "Official documentation",
                        "url": "https://docs.example.org/spec",
                        "snippet": "Official.",
                        "published_at": "2026",
                        "source_name": "Example",
                    }
                ],
            }
        ),
    }
    adapter = OpenAIWebSearchAdapter(runner=lambda _plan: raw)
    plan = QueryPlan(
        query_plan_id="query-plan-formal-methods",
        project_id="project-test",
        study_id="study-test",
        phase=RetrievalPhase.PROTOCOL,
        research_need="official API documentation",
        purpose="official_documentation",
        raw_query_digest="f" * 64,
        queries=["official docs"],
        sanitized_queries=["official docs"],
        providers=[adapter.provider_id],
        resource_types=[ResourceType.WEB_SOURCE],
        budget=RetrievalBudget(max_queries=1, max_results=5),
        allowed_domains=["example.org"],
    )

    result = adapter.search_with_domain_filters(
        plan,
        allowed_domains=["example.org"],
        blocked_domains=[],
    )

    assert adapter.parse_web_search_calls(raw)[0]["id"] == "ws_1"
    assert adapter.extract_queries(raw) == ["official docs"]
    assert adapter.extract_consulted_sources(raw)[0]["url"].endswith("/spec")
    assert adapter.extract_inline_citations(raw)[0]["url"].endswith("/spec")
    assert result.signals[0].title == "Official documentation"
    with pytest.raises(ProviderFailure, match="cache miss"):
        adapter.search_cached_only(plan)


def test_codex_usage_metadata_is_json_safe() -> None:
    from dataclasses import dataclass

    @dataclass
    class UsageBreakdown:
        input_tokens: int

    @dataclass
    class Usage:
        total: UsageBreakdown

    payload = _json_safe(Usage(total=UsageBreakdown(input_tokens=123)))

    assert payload == {"total": {"input_tokens": 123}}
    json.dumps(payload)


def test_open_access_acquisition_freezes_hash_bound_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Credentials(CredentialResolver):
        def get(self, name: str) -> str | None:
            return "contact@example.org" if name == "UNPAYWALL_EMAIL" else None

    pdf = b"%PDF-1.7\nsynthetic open access fixture\n%%EOF\n"
    monkeypatch.setattr(
        "research_forge.retrieval.providers.open_access.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("198.18.0.42", 443),
            )
        ],
    )
    monkeypatch.setattr(
        "research_forge.retrieval.providers.open_access.urllib.request.getproxies",
        lambda: {"https": "http://127.0.0.1:7890"},
    )
    adapter = OpenAccessAdapter(
        credentials=Credentials(),
        transport=lambda _request: {
            "title": "Open Evidence",
            "year": 2026,
            "is_oa": True,
            "doi_url": "https://doi.org/10.1234/open",
            "best_oa_location": {
                "url_for_pdf": "https://fixtures.example.org/open.pdf",
                "host_type": "repository",
                "license": "cc-by-4.0",
                "version": "publishedVersion",
            },
        },
        content_transport=lambda request, **_kwargs: (
            pdf,
            request.full_url,
            "application/pdf",
            {"content-type": "application/pdf"},
        ),
    )
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    project_id = "project-open-access"
    gateway.set_policy(
        project_id,
        RetrievalNetworkPolicy(
            policy_id=retrieval_id("network-policy", project_id, "oa"),
            mode=NetworkMode.PUBLIC_RESEARCH,
            allowed_providers={"open_access"},
            allowed_domains={"api.unpaywall.org"},
            allowed_http_methods={"GET"},
            allowed_resource_types={ResourceType.PUBLICATION},
            allow_full_text=True,
            allow_proxy_fake_ip=True,
            max_queries=2,
            max_results=2,
            max_bytes=100_000,
            approved_by="owner",
            approved_at="2026-07-23T00:00:00+00:00",
        ),
    )
    request = gateway.plan(
        project_id=project_id,
        study_id="study-open-access",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "a" * 16,
        purpose="related_work_search",
        queries=["10.1234/open"],
        providers=["open_access"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=1,
            max_download_bytes=100_000,
        ),
        idempotency_key="open-access-acquisition-v1",
        freshness="live",
    )
    run = gateway.run(request.request_id)
    resource = gateway.repository.list_resources()[0]

    acquisition = gateway.acquire_open_access_document(
        request_id=request.request_id,
        resource_id=resource.resource_id,
    )

    assert run.run.execution_status is RetrievalStatus.SUCCEEDED
    assert acquisition.snapshot.content_level == "full_text"
    assert acquisition.snapshot.model_processing_allowed is True
    assert (
        acquisition.snapshot.content_hash
        == __import__("hashlib").sha256(pdf).hexdigest()
    )
    assert (
        Path(
            gateway.repository.load_artifact(acquisition.artifact_id).path
        ).read_bytes()
        == pdf
    )
    assert acquisition.access_decision.cross_user_cache_allowed is False
    report = json.loads(
        Path(
            gateway.repository.load_artifact(
                acquisition.report_artifact_id
            ).path
        ).read_text(encoding="utf-8")
    )
    assert report["proxy_fake_ip_approved"] is True
    success_events = [
        item
        for item in gateway.repository.list_audit_events()
        if item.response_status == "succeeded"
        and item.provider == "open_access"
        and item.method == "GET"
    ]
    assert success_events[-1].warnings == [
        "owner-approved proxy fake-IP routing"
    ]


def test_proxy_fake_ip_policy_requires_explicit_owner_approval() -> None:
    with pytest.raises(ValueError, match="explicit owner approval"):
        RetrievalNetworkPolicy(
            policy_id="network-policy-unapproved-proxy",
            allow_proxy_fake_ip=True,
        )


def test_proxy_fake_ip_never_allows_literal_or_local_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "research_forge.retrieval.providers.open_access.urllib.request.getproxies",
        lambda: {"https": "http://127.0.0.1:7890"},
    )
    with pytest.raises(ProviderFailure, match="non-public"):
        _validate_public_https_url(
            "https://198.18.0.42/paper.pdf",
            allow_proxy_fake_ip=True,
        )
    with pytest.raises(ProviderFailure, match="local hostname"):
        _validate_public_https_url(
            "https://repository.internal/paper.pdf",
            allow_proxy_fake_ip=True,
        )


def test_open_access_acquisition_failure_is_audited(
    tmp_path: Path,
) -> None:
    def reject_non_public_mapping(_url: str) -> None:
        raise ProviderFailure(
            "open-access content URL resolved to a non-public address",
            ProviderErrorClass.POLICY_DENIED,
        )

    adapter = OpenAccessAdapter(
        credentials={"UNPAYWALL_EMAIL": "contact@example.org"},
        transport=lambda _request: {
            "title": "Open Evidence",
            "year": 2026,
            "is_oa": True,
            "doi_url": "https://doi.org/10.1234/open",
            "best_oa_location": {
                "url_for_pdf": "https://fixtures.example/open.pdf",
                "host_type": "repository",
                "license": "cc-by-4.0",
                "version": "publishedVersion",
            },
        },
        url_validator=reject_non_public_mapping,
    )
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    project_id = "project-open-access-failure"
    gateway.set_policy(
        project_id,
        RetrievalNetworkPolicy(
            policy_id=retrieval_id("network-policy", project_id, "oa"),
            mode=NetworkMode.PUBLIC_RESEARCH,
            allowed_providers={"open_access"},
            allowed_domains={"api.unpaywall.org"},
            allowed_http_methods={"GET"},
            allowed_resource_types={ResourceType.PUBLICATION},
            allow_full_text=True,
            max_queries=1,
            max_results=1,
            max_bytes=100_000,
            approved_by="owner",
            approved_at="2026-07-23T00:00:00+00:00",
        ),
    )
    request = gateway.plan(
        project_id=project_id,
        study_id="study-open-access-failure",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "f" * 16,
        purpose="related_work_search",
        queries=["10.1234/open"],
        providers=["open_access"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=1,
            max_download_bytes=100_000,
        ),
        idempotency_key="open-access-acquisition-failure-v1",
        freshness="live",
    )
    gateway.run(request.request_id)
    resource = gateway.repository.list_resources()[0]

    with pytest.raises(ProviderFailure, match="non-public address"):
        gateway.acquire_open_access_document(
            request_id=request.request_id,
            resource_id=resource.resource_id,
        )

    failures = [
        item
        for item in gateway.repository.list_audit_events()
        if item.request_id == request.request_id
        and item.provider == "open_access"
        and item.response_status == "failed"
    ]
    assert len(failures) == 1
    assert failures[0].failure_reason is not None
    assert "policy_denied" in failures[0].failure_reason
    assert "non-public address" in failures[0].failure_reason


def test_paper_search_open_pdf_can_be_acquired_without_unpaywall_lookup(
    tmp_path: Path,
) -> None:
    pdf = b"%PDF-1.7\npublic repository fixture\n%%EOF\n"
    papers = PaperSearchMCPAdapter(
        runner=lambda _tool, _arguments: {
            "sources_used": ["arxiv"],
            "papers": [
                {
                    "title": "Public Repository Evidence",
                    "paper_id": "2607.12345",
                    "source": "arxiv",
                    "pdf_url": "https://fixtures.example/arxiv.pdf",
                    "url": "https://arxiv.org/abs/2607.12345",
                    "authors": ["Fixture Author"],
                    "license": "cc-by-4.0",
                }
            ],
        }
    )
    open_access = OpenAccessAdapter(
        content_transport=lambda request, **_kwargs: (
            pdf,
            request.full_url,
            "application/pdf",
            {"content-type": "application/pdf"},
        ),
        url_validator=lambda _url: None,
    )
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([papers, open_access]),
        validate_workflow_context=False,
    )
    project_id = "project-paper-search-open"
    gateway.set_policy(
        project_id,
        RetrievalNetworkPolicy(
            policy_id=retrieval_id("network-policy", project_id, "public"),
            mode=NetworkMode.PUBLIC_RESEARCH,
            allowed_providers={"paper_search_mcp", "open_access"},
            allowed_domains={*papers.domains, *open_access.domains},
            allowed_http_methods={"MCP", "GET"},
            allowed_resource_types={
                ResourceType.PUBLICATION,
                ResourceType.PREPRINT,
            },
            allow_full_text=True,
            max_queries=1,
            max_results=5,
            max_bytes=100_000,
            approved_by="owner",
            approved_at="2026-07-23T00:00:00+00:00",
        ),
    )
    request = gateway.plan(
        project_id=project_id,
        study_id="study-paper-search-open",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "b" * 16,
        purpose="closest_prior_work",
        queries=["public repository evidence"],
        providers=["paper_search_mcp"],
        resource_types=[ResourceType.PUBLICATION, ResourceType.PREPRINT],
        usage_role="novelty_grounding",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=5,
            max_download_bytes=100_000,
        ),
        idempotency_key="paper-search-open-acquisition-v1",
        freshness="live",
    )
    gateway.run(request.request_id)
    resource = gateway.repository.list_resources()[0]

    acquisition = gateway.acquire_open_access_document(
        request_id=request.request_id,
        resource_id=resource.resource_id,
    )

    assert resource.metadata["access_status"] == "open_access"
    assert acquisition.snapshot.provider == "open_access"
    assert acquisition.snapshot.model_processing_allowed is True
    report = ReadinessService(
        gateway.repository,
        gateway.providers,
        test_evidence={ExternalCapability.OPEN_ACCESS.value: True},
    ).evaluate()
    open_access_readiness = next(
        item
        for item in report.capabilities
        if item.capability is ExternalCapability.OPEN_ACCESS
    )
    assert open_access_readiness.state is CapabilityState.READY


def test_codex_native_web_search_rejects_non_search_tool_use() -> None:
    adapter = CodexNativeWebSearchAdapter(
        runner=lambda _plan: {
            "items": [{"type": "commandExecution", "command": "dir"}],
            "final_response": "{}",
        }
    )
    plan = QueryPlan(
        query_plan_id="query-plan-test",
        project_id="project-test",
        study_id="study-test",
        phase=RetrievalPhase.DISCOVERY,
        research_need="official documentation",
        purpose="related_work_search",
        raw_query_digest="b" * 64,
        queries=["auditable agents"],
        sanitized_queries=["auditable agents"],
        providers=[adapter.provider_id],
        resource_types=[ResourceType.WEB_SOURCE],
        budget=RetrievalBudget(max_queries=1, max_results=5),
    )

    with pytest.raises(ProviderFailure) as exc:
        adapter.search(plan)
    assert exc.value.classification is ProviderErrorClass.POLICY_DENIED


def test_paper_search_mcp_uses_safe_metadata_allowlist() -> None:
    captured: dict[str, object] = {}

    def runner(tool: str, arguments: dict[str, object]) -> dict[str, object]:
        captured.update({"tool": tool, **arguments})
        return {
            "result": {
                "sources_used": ["arxiv", "crossref"],
                "papers": [
                    {
                        "paper_id": "2401.00001",
                        "title": "Auditable Research Agents",
                        "abstract": "A paired evaluation.",
                        "url": "https://arxiv.org/abs/2401.00001",
                        "published": "2026",
                        "source": "arxiv",
                    }
                ],
            }
        }

    adapter = PaperSearchMCPAdapter(runner=runner)
    assert adapter.start()["status"] == "ready"
    plan = QueryPlan(
        query_plan_id="query-plan-paper",
        project_id="project-test",
        study_id="study-test",
        phase=RetrievalPhase.DISCOVERY,
        research_need="closest prior work",
        purpose="closest_prior_work",
        raw_query_digest="c" * 64,
        queries=["auditable agents"],
        sanitized_queries=["auditable agents"],
        providers=[adapter.provider_id],
        resource_types=[ResourceType.PUBLICATION, ResourceType.PREPRINT],
        budget=RetrievalBudget(max_queries=1, max_results=5),
    )

    result = adapter.search_publications(plan)

    assert captured["tool"] == "search_papers"
    assert "google_scholar" not in str(captured["sources"])
    assert set(str(captured["sources"]).split(",")) == set(DEFAULT_PUBLIC_SOURCES)
    assert set(DEFAULT_PUBLIC_SOURCES) <= set(SAFE_SOURCES)
    assert result.signals[0].metadata["resource_type"] == "preprint"
    assert adapter.get_underlying_provider_coverage(result) == {
        "used": ["arxiv", "crossref"],
        "failed": [],
        "warnings": [],
    }
    resolved = adapter.resolve_metadata(plan, "arXiv:2401.00001")
    assert resolved.signals[0].title == "Auditable Research Agents"
    assert captured["query"] == "arXiv:2401.00001"
    assert adapter.find_open_access_copy(plan, "arXiv:2401.00001").signals == []
    assert (
        adapter.classify_error(
            ProviderFailure("timeout", ProviderErrorClass.TRANSIENT_NETWORK_ERROR)
        )
        is ProviderErrorClass.TRANSIENT_NETWORK_ERROR
    )
    adapter.stop()
    assert adapter.health_check()["status"] == "degraded"


def test_paper_search_mcp_timeout_and_cancellation_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = QueryPlan(
        query_plan_id="query-plan-paper-timeout",
        project_id="project-test",
        study_id="study-test",
        phase=RetrievalPhase.DISCOVERY,
        research_need="closest prior work",
        purpose="closest_prior_work",
        raw_query_digest="c" * 64,
        queries=["auditable agents"],
        sanitized_queries=["auditable agents"],
        providers=["paper_search_mcp"],
        resource_types=[ResourceType.PUBLICATION],
        budget=RetrievalBudget(max_queries=1, max_results=1),
    )
    adapter = PaperSearchMCPAdapter(call_timeout_seconds=0.01)

    async def slow_call(_tool: str, _arguments: dict[str, object]):
        await asyncio.sleep(1)
        return {}

    monkeypatch.setattr(adapter, "_call_stdio", slow_call)
    with pytest.raises(ProviderFailure) as timeout:
        adapter.search_publications(plan)
    assert (
        timeout.value.classification
        is ProviderErrorClass.TRANSIENT_NETWORK_ERROR
    )

    async def cancelled_call(_tool: str, _arguments: dict[str, object]):
        raise asyncio.CancelledError

    monkeypatch.setattr(adapter, "_call_stdio", cancelled_call)
    with pytest.raises(asyncio.CancelledError):
        adapter.search_publications(plan)


def test_paper_search_full_text_delegates_to_approved_acquirer() -> None:
    def runner(_tool: str, _arguments: dict[str, object]) -> dict[str, object]:
        return {
            "result": {
                "sources_used": ["arxiv"],
                "papers": [
                    {
                        "paper_id": "2401.00001",
                        "title": "Auditable Research Agents",
                        "pdf_url": "https://arxiv.org/pdf/2401.00001",
                        "source": "arxiv",
                    }
                ],
            }
        }

    adapter = PaperSearchMCPAdapter(runner=runner)
    plan = QueryPlan(
        query_plan_id="query-plan-paper-full-text",
        project_id="project-test",
        study_id="study-test",
        phase=RetrievalPhase.DISCOVERY,
        research_need="open full text",
        purpose="closest_prior_work",
        raw_query_digest="d" * 64,
        queries=["arXiv:2401.00001"],
        sanitized_queries=["arXiv:2401.00001"],
        providers=[adapter.provider_id],
        resource_types=[ResourceType.PUBLICATION],
        budget=RetrievalBudget(max_queries=1, max_results=1),
    )
    calls: list[tuple[str, int]] = []

    def acquire(url: str, max_bytes: int) -> ProviderContentResult:
        calls.append((url, max_bytes))
        return ProviderContentResult(
            provider="open_access",
            source_url=url,
            final_url=url,
            content=b"%PDF-1.4",
            mime_type="application/pdf",
        )

    content = adapter.fetch_open_full_text(
        plan,
        "arXiv:2401.00001",
        max_bytes=10_000,
        approved_acquirer=acquire,
    )

    assert content.provider == "open_access"
    assert calls == [("https://arxiv.org/pdf/2401.00001", 10_000)]


def test_github_explicit_research_methods_and_approved_archive() -> None:
    sha = "a" * 40

    def transport(request):
        url = request.full_url
        if "/search/repositories" in url:
            return {
                "items": [
                    {
                        "full_name": "org/repo",
                        "default_branch": "main",
                        "html_url": "https://github.com/org/repo",
                    }
                ]
            }
        if "/commits/" in url:
            return {"sha": sha}
        if "/releases" in url:
            return [{"tag_name": "v1.0.0"}]
        raise AssertionError(url)

    def content_transport(request, *, max_bytes: int, url_validator):
        url_validator(request.full_url)
        final_url = f"https://codeload.github.com/org/repo/zip/{sha}"
        url_validator(final_url)
        assert max_bytes == 100_000
        return b"PK\x03\x04", final_url, "application/zip", {}

    adapter = GitHubResearchAdapter(
        transport=transport,
        content_transport=content_transport,
    )
    plan = QueryPlan(
        query_plan_id="query-plan-github-methods",
        project_id="project-test",
        study_id="study-test",
        phase=RetrievalPhase.DISCOVERY,
        research_need="official implementation",
        purpose="code_discovery",
        raw_query_digest="e" * 64,
        queries=["auditable agents"],
        sanitized_queries=["auditable agents"],
        providers=[adapter.provider_id],
        resource_types=[ResourceType.CODE_REPOSITORY],
        budget=RetrievalBudget(max_queries=1, max_results=1),
    )

    assert adapter.search_repositories(plan).signals[0].metadata["commit"] == sha
    assert adapter.search_releases("auditable agents")["items"][0][
        "repository"
    ] == "org/repo"
    archive = adapter.download_approved_archive(
        full_name="org/repo",
        commit_sha=sha,
        max_bytes=100_000,
        authorization_approved=True,
    )
    assert archive.content.startswith(b"PK")
    with pytest.raises(ProviderFailure, match="explicit authorization"):
        adapter.download_approved_archive(
            full_name="org/repo",
            commit_sha=sha,
            max_bytes=100_000,
            authorization_approved=False,
        )


def test_huggingface_explicit_search_methods_use_official_client_contract() -> None:
    sha = "b" * 40

    class FakeHub:
        def list_models(self, **_kwargs):
            return [{"id": "org/model", "sha": sha}]

        def list_datasets(self, **_kwargs):
            return [{"id": "org/data", "sha": sha}]

        def list_spaces(self, **_kwargs):
            return [{"id": "org/space", "sha": sha}]

    adapter = HuggingFaceResearchAdapter(api_factory=FakeHub)

    assert adapter.search_models("agents")[0]["id"] == "org/model"
    assert adapter.search_datasets("agents")[0]["id"] == "org/data"
    assert adapter.search_spaces("agents")[0]["id"] == "org/space"
    with pytest.raises(ProviderFailure, match="explicit contract"):
        adapter.download_approved_file(
            repo_id="org/model",
            filename="model.safetensors",
            revision_sha=sha,
            resource_type="model",
            local_dir=".",
            authorization_approved=False,
        )


def test_paperqa_adapter_exposes_frozen_corpus_contract() -> None:
    required = {
        "create_corpus",
        "add_document",
        "remove_document",
        "build_index",
        "query_evidence",
        "synthesize_related_work",
        "find_conflicting_evidence",
        "get_index_status",
        "invalidate_index",
    }
    adapter = PaperQAAdapter()

    assert required <= set(dir(adapter))
    with pytest.raises(ProviderFailure, match="RetrievalRepository"):
        adapter.get_index_status("missing-corpus")


def test_retrieval_cli_policy_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(tmp_path / "workflow")
    WorkflowRepository(root).create_project(
        "CLI retrieval test", project_id="project-cli"
    )
    assert (
        cli_main(
            [
                "--workflow-root",
                root,
                "retrieval",
                "policy-set",
                "project-cli",
                "--mode",
                "offline",
            ]
        )
        == 0
    )
    set_payload = json.loads(capsys.readouterr().out)
    assert set_payload["mode"] == "offline"

    assert (
        cli_main(
            [
                "--workflow-root",
                root,
                "retrieval",
                "policy-show",
                "project-cli",
            ]
        )
        == 0
    )
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["policy_id"] == set_payload["policy_id"]
    assert (
        cli_main(
            [
                "--workflow-root",
                root,
                "retrieval",
                "policy",
                "show",
                "project-cli",
            ]
        )
        == 0
    )
    nested_show_payload = json.loads(capsys.readouterr().out)
    assert nested_show_payload["policy_id"] == set_payload["policy_id"]


def test_unauthorized_provider_is_denied_before_adapter_call(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(provider_id="crossref")
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    request = gateway.plan(
        project_id="project-demo",
        study_id="study-demo",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "9" * 16,
        purpose="related_work_search",
        queries=["evidence agents"],
        providers=["crossref"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(max_queries=1, max_results=10),
        idempotency_key="unauthorized-provider",
    )
    execution = gateway.run(request.request_id)
    assert execution.run.execution_status is RetrievalStatus.BLOCKED
    assert adapter.calls == 0


def test_authorized_discovery_persists_raw_normalizes_and_builds_set(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter()
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    project_id = "project-demo"
    gateway.set_policy(project_id, _authorized_policy(project_id))
    request = _plan(gateway, project_id)

    execution = gateway.run(request.request_id)

    assert execution.run.execution_status is RetrievalStatus.SUCCEEDED
    assert execution.resource_set is not None
    assert execution.coverage is not None
    assert execution.coverage.raw_result_count == 1
    assert execution.coverage.verified_result_count == 1
    assert len(gateway.repository.list_resources()) == 1
    artifact_files = {
        path.name
        for path in (tmp_path / "retrieval" / "artifacts" / "study-demo").glob(
            "**/*.json*"
        )
    }
    assert {
        "query_plan.json",
        "retrieval_plan.json",
        "policy_decision.json",
        "provider_route.json",
        "sanitized_queries.json",
        "redaction_report.json",
        "normalized_resources.jsonl",
        "deduplication_report.json",
        "verification_report.json",
        "metadata_verification_report.json",
        "provider_records.json",
        "canonical_resources.json",
        "identifier_graph.json",
        "resource_relation_graph.json",
        "access_decisions.jsonl",
        "coverage_report.json",
        "resource_set.json",
    } <= artifact_files
    canonical = gateway.repository.list_canonical_resources()
    assert len(canonical) == 1
    assert canonical[0].identifiers
    provider_records = gateway.repository.list_provider_records(
        canonical[0].canonical_resource_id
    )
    assert len(provider_records) == 1
    assert (
        provider_records[0].raw_response_artifact_id
        in execution.run.output_artifact_ids
    )
    graphs = gateway.repository.list_identifier_graphs("study-demo")
    assert len(graphs) == 1
    assert canonical[0].canonical_resource_id in graphs[0].resource_ids
    decisions = gateway.repository.list_access_decisions(
        canonical[0].canonical_resource_id
    )
    assert len(decisions) == 1
    assert decisions[0].model_processing_allowed is False
    raw_records = gateway.repository.root / "artifact_records"
    assert any(
        "provider_raw_response" in item.kind
        for item in [
            __import__(
                "research_forge.retrieval.domain.models",
                fromlist=["RetrievalArtifact"],
            ).RetrievalArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            for path in raw_records.glob("*.json")
        ]
    )
    assert (tmp_path / "retrieval" / "audit" / "retrieval_audit.jsonl").is_file()


def test_cache_only_never_calls_provider_and_reuses_frozen_live_result(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter()
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    project_id = "project-cache"
    gateway.set_policy(project_id, _authorized_policy(project_id))
    live = gateway.plan(
        project_id=project_id,
        study_id="study-cache",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "c" * 16,
        purpose="related_work_search",
        queries=["evidence bound autonomous research agents"],
        providers=["semantic_scholar"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(max_queries=1, max_results=10),
        idempotency_key="cache-live-v1",
        freshness="live",
    )
    first = gateway.run(live.request_id)
    assert first.run.execution_status is RetrievalStatus.SUCCEEDED
    assert adapter.calls == 1
    cached = gateway.plan(
        project_id=project_id,
        study_id="study-cache",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "d" * 16,
        purpose="related_work_search",
        queries=["evidence bound autonomous research agents"],
        providers=["semantic_scholar"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(max_queries=1, max_results=10),
        idempotency_key="cache-read-v1",
        freshness="cache_only",
    )
    second = gateway.run(cached.request_id)
    assert second.run.execution_status is RetrievalStatus.SUCCEEDED
    assert adapter.calls == 1
    miss = gateway.plan(
        project_id=project_id,
        study_id="study-cache",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "e" * 16,
        purpose="related_work_search",
        queries=["a query never executed live"],
        providers=["semantic_scholar"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(max_queries=1, max_results=10),
        idempotency_key="cache-miss-v1",
        freshness="cache_only",
    )
    missing = gateway.run(miss.request_id)
    assert missing.run.execution_status is RetrievalStatus.FAILED
    assert missing.run.provider_attempts[0].error_classification is (
        ProviderErrorClass.RESOURCE_NOT_FOUND
    )
    assert adapter.calls == 1


def test_query_sanitizer_removes_secret_path_email_and_internal_name() -> None:
    result = QuerySanitizer().sanitize(
        [
            r"project Orion C:\Users\alice\secret\data.csv "
            "api_key=supersecretvalue alice@example.com"
        ],
        internal_identifiers=["Orion"],
    )
    query = result.queries[0]
    assert "supersecretvalue" not in query
    assert "C:\\Users" not in query
    assert "alice@example.com" not in query
    assert "Orion" not in query
    assert {item.category for item in result.report.findings} >= {
        "api_key_or_token",
        "windows_path",
        "email",
        "internal_identifier",
    }


def test_same_idempotency_key_does_not_repeat_provider_call(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    first = _plan(gateway)
    second = _plan(gateway)
    gateway.run(first.request_id)
    gateway.run(second.request_id)
    assert first.request_id == second.request_id
    assert adapter.calls == 1
    assert len(gateway.repository.list_resources()) == 1


def test_secret_query_material_never_enters_artifacts_or_audit(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter()
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    request = gateway.plan(
        project_id="project-demo",
        study_id="study-demo",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "8" * 16,
        purpose="related_work_search",
        queries=["agents api_key=do-not-persist-this-secret"],
        providers=["semantic_scholar"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(max_queries=1, max_results=10),
        idempotency_key="secret-egress-test",
    )
    gateway.run(request.request_id)
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in gateway.repository.root.rglob("*")
        if path.is_file()
    )
    assert "do-not-persist-this-secret" not in persisted


def test_same_doi_merges_providers_and_conflict_is_explicit() -> None:
    first, second = normalize_signals(
        [
            _signal("semantic_scholar", title="Evidence Bound Agents"),
            _signal("crossref", title="Conflicting Provider Title"),
        ]
    )
    result = deduplicate_resources([first, second])
    assert len(result.resources) == 1
    assert set(result.resources[0].providers) == {
        "semantic_scholar",
        "crossref",
    }
    assert (
        result.resources[0].metadata_verification_status
        is MetadataVerificationStatus.CONFLICT
    )
    assert result.conflicts


@pytest.mark.parametrize(
    "error_class",
    [
        ProviderErrorClass.AUTHENTICATION_ERROR,
        ProviderErrorClass.LICENSE_RESTRICTED,
    ],
)
def test_auth_and_license_failures_are_not_retried(
    tmp_path: Path, error_class: ProviderErrorClass
) -> None:
    adapter = FakeAdapter(fail=error_class)
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    request = _plan(gateway)
    execution = gateway.run(request.request_id)
    assert execution.run.execution_status is RetrievalStatus.FAILED
    assert adapter.calls == 1
    assert execution.coverage is not None
    assert "semantic_scholar" in execution.coverage.uncovered_databases


def test_provider_credential_never_enters_artifacts(tmp_path: Path) -> None:
    secret = "redfox-test-secret-never-persist"

    class FixedCredential(CredentialResolver):
        def get(self, name: str) -> str | None:
            assert name == "REDFOX_API_KEY"
            return secret

    def transport(request):
        assert request.get_header("X-api-key") == secret
        return {"code": 200, "data": {"list": []}}

    adapter = RedFoxAdapter(
        credentials=FixedCredential(),
        transport=transport,
    )
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy(
        "project-demo",
        RetrievalNetworkPolicy(
            policy_id="network-policy-redfox-test",
            mode=NetworkMode.PUBLIC_WEB_READ,
            allowed_providers={"redfox_wechat"},
            allowed_domains={"redfox.hk", "mp.weixin.qq.com"},
            allowed_http_methods={"POST"},
            allowed_resource_types={ResourceType.WEB_SOURCE},
            allow_abstract=True,
            max_queries=2,
            max_results=20,
            max_bytes=100_000,
            approved_by="project_owner",
            approved_at="2026-07-23T00:00:00+00:00",
        ),
    )
    request = gateway.plan(
        project_id="project-demo",
        study_id="study-demo",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "7" * 16,
        purpose="discovery_signal",
        queries=["autonomous research agents"],
        providers=["redfox_wechat"],
        resource_types=[ResourceType.WEB_SOURCE],
        usage_role="discovery_signal",
        budget=RetrievalBudget(
            max_queries=1,
            max_results=20,
            max_download_bytes=100_000,
        ),
        idempotency_key="redfox-credential-isolation-v1",
        freshness="live",
    )
    execution = gateway.run(request.request_id)
    assert execution.run.execution_status is RetrievalStatus.SUCCEEDED
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert secret not in persisted


def test_transient_provider_failure_has_bounded_retries(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(
        fail=ProviderErrorClass.TRANSIENT_NETWORK_ERROR,
        fail_times=3,
    )
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    execution = gateway.run(_plan(gateway).request_id)
    assert execution.run.execution_status is RetrievalStatus.SUCCEEDED
    assert adapter.calls == 4
    assert execution.run.retry_count == 3
    artifacts = [
        gateway.repository.load_artifact(artifact_id)
        for artifact_id in execution.run.output_artifact_ids
    ]
    assert artifacts
    assert {item.project_id for item in artifacts} == {"project-demo"}
    assert all(item.policy_context.get("network_policy_id") for item in artifacts)
    assert all(item.immutable_version == 1 for item in artifacts)
    assert {
        item.project_id for item in gateway.repository.list_audit_events()
    } == {"project-demo"}


def test_failed_run_requires_explicit_retry_and_preserves_old_coverage(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(fail=ProviderErrorClass.PERMANENT_PROVIDER_ERROR)
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    request = _plan(gateway)
    failed = gateway.run(request.request_id)
    assert failed.run.execution_status is RetrievalStatus.FAILED
    assert failed.coverage is not None
    first_coverage_id = failed.coverage.coverage_report_id
    assert adapter.calls == 1

    adapter.fail = None
    unchanged = gateway.run(request.request_id)
    assert unchanged.run.execution_status is RetrievalStatus.FAILED
    assert adapter.calls == 1

    retried = gateway.retry(failed.run.run_id)
    assert retried.run.execution_status is RetrievalStatus.SUCCEEDED
    assert retried.run.retry_count == 1
    assert retried.coverage is not None
    assert retried.coverage.coverage_report_id != first_coverage_id
    assert gateway.repository.load_coverage(first_coverage_id) == failed.coverage


def test_policy_skipped_provider_makes_partial_run_degraded(
    tmp_path: Path,
) -> None:
    semantic = FakeAdapter(provider_id="semantic_scholar")
    crossref = FakeAdapter(provider_id="crossref")
    crossref.http_method = "SDK"
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([semantic, crossref]),
        validate_workflow_context=False,
    )
    policy = _authorized_policy("project-demo").model_copy(
        update={
            "allowed_providers": {"semantic_scholar", "crossref"},
            "allowed_domains": {
                "api.semanticscholar.org",
                "api.crossref.org",
            },
        }
    )
    gateway.set_policy("project-demo", policy)
    request = gateway.plan(
        project_id="project-demo",
        study_id="study-demo",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-" + "6" * 16,
        purpose="related_work_search",
        queries=["evidence agents"],
        providers=["semantic_scholar", "crossref"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="background_source",
        budget=RetrievalBudget(max_queries=1, max_results=10),
        idempotency_key="partial-policy-denial",
        freshness="live",
    )

    execution = gateway.run(request.request_id)

    assert execution.run.execution_status is RetrievalStatus.DEGRADED
    assert semantic.calls == 1
    assert crossref.calls == 0
    assert execution.coverage is not None
    assert (
        execution.coverage.provider_failures["crossref"]
        == ProviderErrorClass.POLICY_DENIED.value
    )


def test_frozen_resource_set_is_immutable_and_promotion_creates_binding(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter()
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    execution = gateway.run(_plan(gateway).request_id)
    assert execution.resource_set is not None
    frozen = gateway.freeze_resource_set(execution.resource_set.resource_set_id)
    frozen_artifacts = [
        item
        for item in gateway.repository.list_artifacts("study-demo")
        if item.kind == "frozen_resource_set"
    ]
    assert len(frozen_artifacts) == 1
    assert Path(frozen_artifacts[0].path).name == "frozen_resource_set.json"
    with pytest.raises(ValueError, match="frozen ResourceSet"):
        gateway.repository.save_resource_set(
            frozen.model_copy(update={"binding_ids": []})
        )
    source = gateway.repository.list_bindings("study-demo")[0]
    assert source.verdict_eligible is False
    with pytest.raises(ValueError, match="Discovery bindings cannot"):
        source.__class__.model_validate(
            {**source.model_dump(mode="json"), "verdict_eligible": True}
        )
    promoted = gateway.promote_binding(
        source.binding_id,
        target_phase=RetrievalPhase.PROTOCOL,
        step_instance_id="step-" + "2" * 16,
        purpose="baseline_discovery",
        usage_role="contract_field_justification",
        target_type="research_contract",
        target_id="research-v1",
        target_field="baseline",
    )
    assert promoted.binding_id != source.binding_id
    assert promoted.snapshot_id == source.snapshot_id
    assert promoted.phase is RetrievalPhase.PROTOCOL
    assert promoted.verdict_eligible is False


def test_experiment_open_metric_search_is_policy_denied(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    gateway = RetrievalGateway(
        str(tmp_path),
        providers=ProviderRegistry([adapter]),
        validate_workflow_context=False,
    )
    gateway.set_policy("project-demo", _authorized_policy("project-demo"))
    request = gateway.plan(
        project_id="project-demo",
        study_id="study-demo",
        phase=RetrievalPhase.EXPERIMENTATION,
        step_instance_id="step-" + "3" * 16,
        purpose="metric_grounding",
        queries=["find a better metric after seeing results"],
        providers=["semantic_scholar"],
        resource_types=[ResourceType.PUBLICATION],
        usage_role="experiment_metric",
        budget=RetrievalBudget(max_queries=1, max_results=10),
        idempotency_key="forbidden-experiment-search",
        contract_refs=[
            ContractRef(
                contract_type="research",
                contract_id="research-v1",
                version=1,
            )
        ],
    )
    execution = gateway.run(request.request_id)
    assert execution.run.execution_status is RetrievalStatus.BLOCKED
    assert adapter.calls == 0


def test_authorized_discovery_workflow_reaches_scope_gate_with_draft_source_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research_forge.workflow_scheduler as scheduler_module
    from research_forge.workflow_domain import WorkflowRepository
    from research_forge.workflow_scheduler import (
        PersistentDAGScheduler,
        create_project_discovery_study,
        stage_one_handlers,
    )

    source = tmp_path / "bundle"
    (source / "protocols").mkdir(parents=True)
    (source / "outputs").mkdir()
    (source / "reports").mkdir()
    (source / "protocols" / "demo.json").write_text(
        '{"version":"demo","hypothesis":"Evidence binding helps"}',
        encoding="utf-8",
    )
    (source / "outputs" / "demo.json").write_text(
        '{"accuracy":0.81,"valid":true}', encoding="utf-8"
    )
    (source / "reports" / "demo.md").write_text(
        "# Contributions\n\nEvidence binding supports auditable agents.",
        encoding="utf-8",
    )
    workflow_root = tmp_path / "workflow"
    repository = WorkflowRepository(workflow_root)
    project_id, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=True,
        identity="gateway-integration",
    )
    semantic = FakeAdapter(provider_id="semantic_scholar")
    crossref = FakeAdapter(provider_id="crossref")
    gateway = RetrievalGateway(
        str(workflow_root),
        providers=ProviderRegistry([semantic, crossref]),
    )
    policy = _authorized_policy(project_id).model_copy(
        update={
            "allowed_providers": {"semantic_scholar", "crossref"},
            "allowed_domains": {
                "api.semanticscholar.org",
                "api.crossref.org",
            },
        }
    )
    gateway.set_policy(project_id, policy)
    monkeypatch.setattr(scheduler_module, "_gateway", lambda _context: gateway)

    snapshot = PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)

    steps = {item["step_type"]: item for item in snapshot["steps"]}
    assert steps["execute_discovery_retrieval"]["status"] == "succeeded"
    assert steps["scope_review"]["status"] == "waiting_for_user"
    sets = gateway.repository.list_resource_sets(study_id)
    assert len(sets) == 1
    assert sets[0].status.value == "draft"
    assert semantic.calls == 1
    assert crossref.calls == 1
    assert len(gateway.repository.list_resources()) == 1
    assert any(
        item.kind.startswith("retrieval_provider_raw_response")
        for item in repository.list_artifacts(study_id)
    )
    gate = repository.list_gates(study_id)[0]
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by="project_owner",
    )
    PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)
    assert gateway.repository.list_resource_sets(study_id)[0].status.value == "frozen"

    before = repository.load_study(study_id)
    resource_id = gateway.repository.list_resources()[0].resource_id
    conflict = gateway.record_synthesis_conflict(
        study_id=study_id,
        resource_id=resource_id,
        claim_id="claim-demo",
        conflict_type="retraction",
        details="A synthesis-stage retraction check requires repair review.",
    )
    after = repository.load_study(study_id)
    assert conflict.repair_recommended is True
    assert after.latest_study_verdict_id == before.latest_study_verdict_id
