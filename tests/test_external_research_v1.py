from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from research_forge.claim_discovery import TrendSignal
from research_forge.models import utc_now
from research_forge.retrieval.domain.external_models import (
    AccessDecision,
    AccessMode,
    CapabilityReadiness,
    CapabilityState,
    CorpusDocument,
    CorpusStatus,
    ExternalCapability,
    ProviderCredentialMode,
    ReadinessReport,
)
from research_forge.retrieval.domain.models import (
    ExternalResource,
    MetadataVerificationStatus,
    ProviderAttempt,
    ProviderErrorClass,
    QueryPlan,
    ResourceSnapshot,
    ResourceType,
    ResourceUseBinding,
    RetrievalArtifact,
    RetrievalBudget,
    RetrievalPhase,
    RetrievalRun,
    RetrievalStatus,
)
from research_forge.retrieval.domain.repository import RetrievalRepository
from research_forge.retrieval.evidence import PaperQAEvidenceService
from research_forge.retrieval.institution import (
    BrowserLaunch,
    InstitutionSessionBroker,
    InstitutionSessionStatus,
)
from research_forge.retrieval.interfaces.readiness import ReadinessService
from research_forge.retrieval.pipelines.resources import normalize_signals
from research_forge.retrieval.pipelines.provenance import build_provider_records
from research_forge.retrieval.providers.codex_web import (
    CodexNativeWebSearchAdapter,
)
from research_forge.retrieval.providers.open_access import OpenAccessAdapter
from research_forge.retrieval.providers.openai_web import OpenAIWebSearchAdapter
from research_forge.retrieval.providers.paper_search_mcp import (
    PaperSearchMCPAdapter,
)
from research_forge.retrieval.providers.registry import ProviderRegistry
from research_forge.retrieval.routing import QueryIntent, QueryRouter
from research_forge.web_app import create_server
from research_forge.retrieval.workflow.step_definitions import (
    EXTERNAL_RESEARCH_DAGS,
    append_external_research_dag,
    append_external_research_loop,
    retrieval_step_definitions,
)
from research_forge.workflow_domain import (
    ArtifactRole,
    ArtifactStatus,
    DiagnosticOwner,
    EntryMode,
    GateType,
    Hypothesis,
    HypothesisRole,
    Phase,
    ResearchContractVersion,
    WorkflowRepository,
)
from research_forge.workflow_scheduler import (
    PersistentDAGScheduler,
    stage_one_handlers,
)


def _minimal_text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
    ]
    payload = b"%PDF-1.4\n"
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(payload))
        payload += f"{index} 0 obj\n".encode() + value + b"\nendobj\n"
    xref = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        payload += f"{offset:010d} 00000 n \n".encode()
    return (
        payload
        + (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )


def test_non_provider_modules_cannot_bypass_retrieval_gateway() -> None:
    package_root = Path(__file__).resolve().parents[1] / "research_forge"
    forbidden = ("urlopen(", "requests.", "httpx.", "curl ")
    violations: list[str] = []
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root).as_posix()
        if relative.startswith("retrieval/providers/"):
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for marker in forbidden:
            if marker in content:
                violations.append(f"{relative}: {marker}")
    assert not violations, (
        "external networking must be isolated inside Retrieval Gateway providers: "
        + ", ".join(violations)
    )


def test_external_research_v1_registers_every_required_workflow_node() -> None:
    registered = {item.step_type for item in retrieval_step_definitions()}
    required = {
        step_type
        for step_types in EXTERNAL_RESEARCH_DAGS.values()
        for step_type in step_types
    }
    assert required <= registered
    assert len(required) == 47


def test_readiness_uses_recent_gateway_run_not_package_presence(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    now = utc_now()
    repository.save_run(
        RetrievalRun(
            run_id="retrieval-run-paper-health",
            request_id="retrieval-request-paper-health",
            execution_status=RetrievalStatus.SUCCEEDED,
            provider_attempts=[
                ProviderAttempt(
                    provider="paper_search_mcp",
                    attempt=1,
                    status=RetrievalStatus.SUCCEEDED,
                    result_count=1,
                    completed_at=now,
                )
            ],
            started_at=now,
            completed_at=now,
        )
    )
    report = ReadinessService(
        repository,
        ProviderRegistry(
            [
                PaperSearchMCPAdapter(
                    runner=lambda _tool, _arguments: {
                        "sources_used": [],
                        "papers": [],
                    }
                )
            ]
        ),
        test_evidence={ExternalCapability.ACADEMIC_SEARCH.value: True},
    ).evaluate()
    academic = next(
        item
        for item in report.capabilities
        if item.capability is ExternalCapability.ACADEMIC_SEARCH
    )
    assert academic.state is CapabilityState.READY
    assert academic.health_verified is True
    assert report.external_research_v1_ready is False


def test_codex_native_live_search_satisfies_public_web_readiness(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    now = utc_now()
    repository.save_run(
        RetrievalRun(
            run_id="retrieval-run-codex-web-health",
            request_id="retrieval-request-codex-web-health",
            execution_status=RetrievalStatus.SUCCEEDED,
            provider_attempts=[
                ProviderAttempt(
                    provider="codex_native_web_search",
                    attempt=1,
                    status=RetrievalStatus.SUCCEEDED,
                    result_count=1,
                    completed_at=now,
                )
            ],
            started_at=now,
            completed_at=now,
        )
    )
    report = ReadinessService(
        repository,
        ProviderRegistry([CodexNativeWebSearchAdapter(runner=lambda _plan: {})]),
        test_evidence={ExternalCapability.PUBLIC_WEB.value: True},
    ).evaluate()
    public_web = next(
        item
        for item in report.capabilities
        if item.capability is ExternalCapability.PUBLIC_WEB
    )

    assert public_web.state is CapabilityState.READY
    assert public_web.health_verified is True
    assert public_web.provider_ids == ["codex_native_web_search"]
    assert any(
        "successful policy-controlled Gateway run" in reason
        for reason in public_web.reasons
    )


def test_public_loop_readiness_excludes_optional_institution_and_reads_legacy() -> None:
    public_capabilities = {
        ExternalCapability.ACADEMIC_SEARCH,
        ExternalCapability.GITHUB_RESEARCH,
        ExternalCapability.HUGGINGFACE_RESEARCH,
        ExternalCapability.PUBLIC_WEB,
        ExternalCapability.OPEN_ACCESS,
        ExternalCapability.EVIDENCE_ANALYSIS,
    }
    components = [
        CapabilityReadiness(
            capability=capability,
            state=(
                CapabilityState.READY
                if capability in public_capabilities
                else CapabilityState.DEGRADED
            ),
            credential_mode=ProviderCredentialMode.NONE_REQUIRED,
            configured=True,
            health_verified=capability in public_capabilities,
            tests_verified=True,
            migration_ready=True,
        )
        for capability in (
            ExternalCapability.ACADEMIC_SEARCH,
            ExternalCapability.GITHUB_RESEARCH,
            ExternalCapability.HUGGINGFACE_RESEARCH,
            ExternalCapability.PUBLIC_WEB,
            ExternalCapability.OPEN_ACCESS,
            ExternalCapability.INSTITUTIONAL_ACCESS,
            ExternalCapability.EVIDENCE_ANALYSIS,
        )
    ]
    aggregate = CapabilityReadiness(
        capability=ExternalCapability.EXTERNAL_RESEARCH_V1,
        state=CapabilityState.DEGRADED,
        credential_mode=ProviderCredentialMode.UNAVAILABLE,
        configured=False,
        health_verified=False,
        tests_verified=False,
        migration_ready=True,
    )

    legacy = ReadinessReport.model_validate(
        {
            "report_id": "readiness-legacy-public-loop",
            "capabilities": [
                item.model_dump(mode="json") for item in [*components, aggregate]
            ],
            "external_research_v1_ready": False,
        }
    )

    assert legacy.public_research_loop_ready is True
    assert legacy.external_research_v1_ready is False


def test_external_research_phase_dags_are_persistent_and_idempotent(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "External research DAG test",
        project_id="project-external-dag",
    )
    study = repository.create_study(
        project.project_id,
        "External research DAG test",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-external-dag",
    )
    first = append_external_research_dag(
        repository,
        study.study_id,
        "discovery",
        run_key="registered-v1",
    )
    second = append_external_research_dag(
        repository,
        study.study_id,
        "discovery",
        run_key="registered-v1",
    )
    assert {item.step_instance_id for item in first} == {
        item.step_instance_id for item in second
    }
    by_type = {item.step_type: item for item in first}
    search_dependencies = {
        tuple(by_type[name].depends_on)
        for name in (
            "execute_academic_search",
            "execute_github_search",
            "execute_huggingface_search",
            "execute_official_web_search",
        )
    }
    assert len(search_dependencies) == 1
    normalize = by_type["normalize_resources"]
    assert set(normalize.depends_on) == {
        by_type[name].step_instance_id
        for name in (
            "execute_academic_search",
            "execute_github_search",
            "execute_huggingface_search",
            "execute_official_web_search",
        )
    }
    for stage in ("protocol", "experimentation", "synthesis", "repair"):
        steps = append_external_research_dag(
            repository,
            study.study_id,
            stage,
            run_key="registered-v1",
        )
        assert {item.step_type for item in steps} == set(EXTERNAL_RESEARCH_DAGS[stage])


def test_external_research_loop_connects_four_phases_without_fifth_phase(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "External research loop test",
        project_id="project-external-loop",
    )
    study = repository.create_study(
        project.project_id,
        "External research loop test",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-external-loop",
    )

    first = append_external_research_loop(
        repository,
        study.study_id,
        run_key="closed-loop-v1",
    )
    second = append_external_research_loop(
        repository,
        study.study_id,
        run_key="closed-loop-v1",
    )

    expected_count = sum(
        len(EXTERNAL_RESEARCH_DAGS[stage])
        for stage in ("discovery", "protocol", "experimentation", "synthesis")
    )
    assert len(first) == expected_count
    assert {item.step_instance_id for item in first} == {
        item.step_instance_id for item in second
    }
    assert not any(item.step_type in EXTERNAL_RESEARCH_DAGS["repair"] for item in first)
    by_group_and_type = {
        (item.task_group, item.step_type): item for item in first
    }
    transitions = (
        ("discovery", "protocol"),
        ("protocol", "experimentation"),
        ("experimentation", "synthesis"),
    )
    for upstream, downstream in transitions:
        terminal = by_group_and_type[
            (
                f"external_research:{upstream}:closed-loop-v1",
                EXTERNAL_RESEARCH_DAGS[upstream][-1],
            )
        ]
        first_downstream = by_group_and_type[
            (
                f"external_research:{downstream}:closed-loop-v1",
                EXTERNAL_RESEARCH_DAGS[downstream][0],
            )
        ]
        assert first_downstream.depends_on == [terminal.step_instance_id]


def test_identical_retrieval_artifact_write_reuses_immutable_record(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    first = repository.write_artifact(
        project_id="project-artifact-reuse",
        study_id="study-artifact-reuse",
        step_instance_id="step-artifact-reuse",
        kind="query_plan",
        value={"query": "sanitized"},
        producer="retrieval_gateway",
        input_artifact_ids=[],
    )
    second = repository.write_artifact(
        project_id="project-artifact-reuse",
        study_id="study-artifact-reuse",
        step_instance_id="step-artifact-reuse",
        kind="query_plan",
        value={"query": "sanitized"},
        producer="retrieval_gateway",
        input_artifact_ids=[],
    )
    assert second == first


def test_retrieval_artifact_identity_includes_provenance(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    first = repository.write_artifact(
        project_id="project-artifact-lineage",
        study_id="study-artifact-lineage",
        step_instance_id="step-artifact-lineage",
        kind="query_plan",
        value={"query": "sanitized"},
        producer="retrieval_gateway",
        input_artifact_ids=[],
    )
    second = repository.write_artifact(
        project_id="project-artifact-lineage",
        study_id="study-artifact-lineage",
        step_instance_id="step-artifact-lineage",
        kind="query_plan",
        value={"query": "sanitized"},
        producer="other_producer",
        input_artifact_ids=[],
    )
    assert first.content_hash == second.content_hash
    assert first.artifact_id != second.artifact_id


def test_provider_record_identity_includes_raw_artifact_provenance() -> None:
    resource = ExternalResource(
        resource_id="resource-provider-version",
        resource_type=ResourceType.PUBLICATION,
        canonical_identifier="10.1234/provider-version",
        title="Versioned provider record",
        doi="10.1234/provider-version",
        providers=["open_access"],
        metadata_verification_status=MetadataVerificationStatus.VERIFIED,
        canonical_metadata_hash="a" * 64,
    )
    common = {
        "project_id": "project-provider-version",
        "study_id": "study-provider-version",
        "step_instance_id": "step-provider-version",
        "kind": "provider_raw_response",
        "path": "raw.json",
        "content_hash": "b" * 64,
        "producer": "provider:open_access",
    }
    first_artifact = RetrievalArtifact(
        artifact_id="rartifact-first-provider-version",
        **common,
    )
    second_artifact = RetrievalArtifact(
        artifact_id="rartifact-second-provider-version",
        **common,
    )

    first = build_provider_records(
        resource,
        raw_artifacts={"open_access": [first_artifact]},
    )
    second = build_provider_records(
        resource,
        raw_artifacts={"open_access": [second_artifact]},
    )

    assert first[0].response_hash == second[0].response_hash
    assert first[0].provider_record_id != second[0].provider_record_id


def test_exact_discovery_dag_runs_offline_without_network_calls(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Offline external research",
        project_id="project-offline-external",
    )
    study = repository.create_study(
        project.project_id,
        "Offline external research",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-offline-external",
    )
    append_external_research_dag(
        repository,
        study.study_id,
        "discovery",
        run_key="offline-v1",
    )

    snapshot = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study.study_id)

    steps = {item["step_type"]: item for item in snapshot["steps"]}
    for step_type in EXTERNAL_RESEARCH_DAGS["discovery"][:-1]:
        assert steps[step_type]["status"] == "succeeded"
    assert steps["scope_review_gate"]["status"] == "waiting_for_user"
    assert not (tmp_path / "workflow" / "retrieval" / "runs").exists()


def test_protocol_dag_binds_to_draft_contract_and_runs_offline(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Offline protocol retrieval",
        project_id="project-offline-protocol",
    )
    study = repository.create_study(
        project.project_id,
        "Offline protocol retrieval",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-offline-protocol",
    )
    repository.save_research_contract(
        ResearchContractVersion(
            study_id=study.study_id,
            version=1,
            scope_version=1,
            hypotheses=[
                Hypothesis(
                    hypothesis_id="hypothesis-offline-protocol",
                    statement="A frozen evaluator changes unsupported claim rate.",
                    role=HypothesisRole.PRIMARY,
                    decision_rule={"metric": "unsupported_rate", "direction": "lower"},
                )
            ],
            data_boundary={"dataset": "fixture"},
            metrics=[{"name": "unsupported_rate", "direction": "lower"}],
            baseline={"name": "no_gate"},
            treatment={"name": "claim_evidence_gate"},
            runtime_binding={"model": "fixture-model"},
            evaluator_policy={"type": "deterministic"},
        )
    )
    append_external_research_dag(
        repository,
        study.study_id,
        "protocol",
        run_key="offline-v1",
    )

    snapshot = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study.study_id)

    steps = {item["step_type"]: item for item in snapshot["steps"]}
    assert all(
        steps[step_type]["status"] == "succeeded"
        for step_type in EXTERNAL_RESEARCH_DAGS["protocol"]
    )
    planned = repository.load_step_result(
        study.study_id,
        steps["plan_protocol_retrieval"]["step_instance_id"],
    )
    assert planned["contract_version"] == 1
    run = RetrievalRepository(tmp_path / "workflow").list_runs(study.study_id)[0]
    assert run.execution_status.value == "blocked"


def test_experimentation_dag_rejects_unlisted_resources(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Experiment resource authorization",
        project_id="project-experiment-authorization",
    )
    study = repository.create_study(
        project.project_id,
        "Experiment resource authorization",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-experiment-authorization",
    )
    gate = repository.create_gate(
        study.study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study.study_id}:research-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id,
        gate.gate_id,
        approve=True,
        decided_by="project_owner",
    )
    repository.save_research_contract(
        ResearchContractVersion(
            study_id=study.study_id,
            version=1,
            scope_version=1,
            status=ArtifactStatus.FROZEN,
            hypotheses=[
                Hypothesis(
                    hypothesis_id="hypothesis-experiment-authorization",
                    statement="Pinned resources reproduce the registered result.",
                    role=HypothesisRole.PRIMARY,
                    decision_rule={"metric": "accuracy", "direction": "higher"},
                )
            ],
            data_boundary={"dataset": "fixture"},
            metrics=[{"name": "accuracy", "direction": "higher"}],
            baseline={"name": "baseline"},
            treatment={"name": "treatment"},
            runtime_binding={"model": "fixture-model"},
            evaluator_policy={"type": "deterministic"},
            frozen_at="2026-07-23T00:00:00+00:00",
        )
    )
    append_external_research_dag(
        repository,
        study.study_id,
        "experimentation",
        run_key="authorization-v1",
    )

    snapshot = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study.study_id)

    steps = {item["step_type"]: item for item in snapshot["steps"]}
    resolve = steps["resolve_contract_approved_resources"]
    assert resolve["status"] == "blocked"
    assert resolve["blocker"]["kind"] == "missing_contract_resource_authorization"
    assert all(
        steps[name]["status"] == "blocked"
        for name in EXTERNAL_RESEARCH_DAGS["experimentation"][1:]
    )


def test_synthesis_dag_audits_citations_without_changing_verdict(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Synthesis citation audit",
        project_id="project-synthesis-audit",
    )
    study = repository.create_study(
        project.project_id,
        "Synthesis citation audit",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-synthesis-audit",
    )
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text(
        "Prior work is identified by DOI 10.1234/example.",
        encoding="utf-8",
    )
    repository.register_artifact(
        study.study_id,
        str(manuscript),
        hashlib.sha256(manuscript.read_bytes()).hexdigest(),
        kind="manuscript",
        role=ArtifactRole.MANUSCRIPT,
    )
    append_external_research_dag(
        repository,
        study.study_id,
        "synthesis",
        run_key="citation-v1",
    )

    snapshot = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study.study_id)

    steps = {item["step_type"]: item for item in snapshot["steps"]}
    assert all(
        steps[step_type]["status"] == "succeeded"
        for step_type in EXTERNAL_RESEARCH_DAGS["synthesis"]
    )
    result = repository.load_step_result(
        study.study_id,
        steps["produce_citation_audit"]["step_instance_id"],
    )
    assert result["unresolved_citations"] == ["10.1234/example"]
    assert result["requires_repair"] is True
    assert result["historical_verdict_modified"] is False


def test_repair_dag_binds_diagnostics_and_waits_for_owner_gate(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Repair retrieval",
        project_id="project-repair-retrieval",
    )
    study = repository.create_study(
        project.project_id,
        "Repair retrieval",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-repair-retrieval",
    )
    source = tmp_path / "failed-evidence.json"
    source.write_text('{"valid": false}', encoding="utf-8")
    artifact = repository.register_artifact(
        study.study_id,
        str(source),
        hashlib.sha256(source.read_bytes()).hexdigest(),
        kind="evidence_packet",
        role=ArtifactRole.EVALUATION,
    )
    repair = repository.propose_repair(
        study.study_id,
        diagnostic_owner=DiagnosticOwner.EVIDENCE_PACKAGING,
        scientific_change=True,
        earliest_affected_phase=Phase.EXPERIMENT,
        changed_artifact_ids=[artifact.artifact_id],
        regression_checks=[{"name": "packet_context_complete"}],
    )
    append_external_research_dag(
        repository,
        study.study_id,
        "repair",
        run_key="repair-v1",
    )

    first = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study.study_id)
    steps = {item["step_type"]: item for item in first["steps"]}
    for step_type in EXTERNAL_RESEARCH_DAGS["repair"][:-1]:
        assert steps[step_type]["status"] == "succeeded"
    assert steps["regression_scope_review"]["status"] == "waiting_for_user"
    proposal = repository.load_step_result(
        study.study_id,
        steps["propose_repair_contract"]["step_instance_id"],
    )
    assert proposal["repair_contract_id"] == repair.repair_id
    assert proposal["historical_verdict_modified"] is False

    gate = next(
        item
        for item in repository.list_gates(study.study_id)
        if item.gate_type is GateType.REPAIR_OR_HIGH_COST_RUN
    )
    repository.decide_gate(
        study.study_id,
        gate.gate_id,
        approve=True,
        decided_by="project_owner",
    )
    second = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study.study_id)
    final = {item["step_type"]: item for item in second["steps"]}[
        "regression_scope_review"
    ]
    assert final["status"] == "succeeded"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _plan(**updates) -> QueryPlan:
    values = {
        "query_plan_id": "query-plan-external",
        "project_id": "project-external",
        "study_id": "study-external",
        "phase": RetrievalPhase.DISCOVERY,
        "research_need": "official sources",
        "purpose": "related_work_search",
        "raw_query_digest": "a" * 64,
        "queries": ["auditable agents"],
        "sanitized_queries": ["auditable agents"],
        "providers": ["openai_web_search"],
        "resource_types": [ResourceType.WEB_SOURCE],
        "budget": RetrievalBudget(max_queries=1, max_results=10),
    }
    values.update(updates)
    return QueryPlan.model_validate(values)


def test_readiness_never_becomes_ready_from_registration_alone(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    report = ReadinessService(
        repository,
        ProviderRegistry([]),
        test_evidence={item.value: True for item in ExternalCapability},
    ).evaluate()
    assert report.external_research_v1_ready is False
    assert all(
        item.state is not CapabilityState.READY
        for item in report.capabilities
        if item.capability is not ExternalCapability.EXTERNAL_RESEARCH_V1
    )


def test_open_access_readiness_rejects_non_acquisition_fixture(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    pdf = _minimal_text_pdf("A local fixture is not a live open-access acquisition.")
    artifact = repository.write_binary_artifact(
        project_id="project-open-access-readiness",
        study_id="study-open-access-readiness",
        step_instance_id="step-local-fixture",
        kind="open_access_full_text",
        content=pdf,
        producer="readiness_fixture",
        extension="pdf",
    )
    repository.save_snapshot(
        ResourceSnapshot(
            snapshot_id="snapshot-local-fixture",
            resource_id="resource-local-fixture",
            content_level="full_text",
            raw_response_artifact_id=artifact.artifact_id,
            normalized_content_artifact_id=artifact.artifact_id,
            provider="open_access",
            content_hash=hashlib.sha256(pdf).hexdigest(),
            mime_type="application/pdf",
            byte_size=len(pdf),
            license_status="cc-by-4.0",
            access_status="retrieved",
            access_mode="open_access",
            model_processing_allowed=True,
        )
    )

    report = ReadinessService(
        repository,
        ProviderRegistry(
            [OpenAccessAdapter(credentials={"UNPAYWALL_EMAIL": "release@example.org"})]
        ),
        test_evidence={ExternalCapability.OPEN_ACCESS.value: True},
    ).evaluate()
    open_access = next(
        item
        for item in report.capabilities
        if item.capability is ExternalCapability.OPEN_ACCESS
    )

    assert open_access.state is CapabilityState.DEGRADED
    assert open_access.health_verified is False


def test_open_access_readiness_rejects_resolver_only_success(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    now = utc_now()
    repository.save_run(
        RetrievalRun(
            run_id="retrieval-run-open-access-resolver",
            request_id="retrieval-request-open-access-resolver",
            execution_status=RetrievalStatus.SUCCEEDED,
            provider_attempts=[
                ProviderAttempt(
                    provider="open_access",
                    attempt=1,
                    status=RetrievalStatus.SUCCEEDED,
                    result_count=1,
                    completed_at=now,
                )
            ],
            started_at=now,
            completed_at=now,
        )
    )

    report = ReadinessService(
        repository,
        ProviderRegistry(
            [OpenAccessAdapter(credentials={"UNPAYWALL_EMAIL": "release@example.org"})]
        ),
        test_evidence={ExternalCapability.OPEN_ACCESS.value: True},
    ).evaluate()
    open_access = next(
        item
        for item in report.capabilities
        if item.capability is ExternalCapability.OPEN_ACCESS
    )

    assert open_access.state is CapabilityState.DEGRADED
    assert open_access.health_verified is False
    assert any(
        "required hash-bound acquisition/evidence artifact" in reason
        for reason in open_access.reasons
    )


def test_institution_session_rejects_credential_fields(tmp_path: Path) -> None:
    broker = InstitutionSessionBroker(tmp_path)
    with pytest.raises(ValueError, match="prohibited credential"):
        broker.create(
            owner_user_id="alice",
            project_id="project",
            study_id="study",
            institution_id="university",
            target_url="https://library.example.edu/login",
            extra={"password": "must-never-be-accepted"},
        )
    with pytest.raises(ValueError, match="prohibited credential"):
        broker.create(
            owner_user_id="alice",
            project_id="project",
            study_id="study",
            institution_id="university",
            target_url="https://library.example.edu/login",
            extra={"nested": {"mfa": "must-never-be-accepted"}},
        )


def test_institution_session_is_user_bound_and_revocable(tmp_path: Path) -> None:
    broker = InstitutionSessionBroker(tmp_path)
    session = broker.create(
        owner_user_id="alice",
        project_id="project",
        study_id="study",
        institution_id="university",
        target_url="https://library.example.edu/login",
    )
    with pytest.raises(PermissionError):
        broker.load(session.session_id, owner_user_id="bob")
    revoked = broker.revoke(session.session_id, owner_user_id="alice")
    assert revoked.status is InstitutionSessionStatus.REVOKED
    with pytest.raises(ValueError, match="revoked"):
        broker.reauthenticate(session.session_id, owner_user_id="alice")


def test_institution_browser_and_single_document_handoff_are_user_controlled(
    tmp_path: Path,
) -> None:
    class Browser:
        def launch(self, *, browser_profile_id: str, target_url: str):
            assert browser_profile_id
            assert target_url == "https://library.example.edu/login"
            return BrowserLaunch(process_id=1234, browser="fixture-browser")

    broker = InstitutionSessionBroker(
        tmp_path,
        browser_controller=Browser(),  # type: ignore[arg-type]
    )
    session = broker.create(
        owner_user_id="alice",
        project_id="project",
        study_id="study",
        institution_id="university",
        target_url="https://library.example.edu/login",
    )
    opened, launch = broker.open_browser(session.session_id, owner_user_id="alice")
    assert launch.process_id == 1234
    assert opened.authentication_confirmed_by_user is False
    active = broker.confirm_authenticated(
        session.session_id,
        owner_user_id="alice",
        user_confirmation=True,
        duration_minutes=30,
    )
    assert active.status is InstitutionSessionStatus.ACTIVE
    pdf_path = tmp_path / "authorized.pdf"
    pdf_path.write_bytes(
        _minimal_text_pdf("Authorized subscription evidence for one study.")
    )

    registered = broker.register_authorized_document(
        session.session_id,
        owner_user_id="alice",
        file_path=pdf_path,
        step_instance_id="step-institution",
        model_processing_approved=True,
    )

    assert registered["snapshot"]["access_mode"] == "institutional"
    assert registered["snapshot"]["model_processing_allowed"] is True
    assert registered["access_decision"]["cross_user_cache_allowed"] is False
    assert registered["source_path_persisted"] is False
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (tmp_path / "retrieval").rglob("*.json*")
    )
    assert str(pdf_path) not in persisted


def test_paperqa_corpus_rejects_processing_without_rights(tmp_path: Path) -> None:
    repository = RetrievalRepository(tmp_path)
    repository.save_access_decision(
        AccessDecision(
            decision_id="decision-denied",
            resource_id="resource-1",
            access_mode=AccessMode.METADATA_ONLY,
            full_text_available=False,
            persistent_storage_allowed=True,
            model_processing_allowed=False,
            decision_basis="metadata only",
        )
    )
    with pytest.raises(ValueError, match="not authorized"):
        PaperQAEvidenceService(repository).build_corpus(
            study_id="study",
            phase=RetrievalPhase.DISCOVERY,
            documents=[
                CorpusDocument(
                    resource_id="resource-1",
                    snapshot_id="snapshot-1",
                    snapshot_hash="b" * 64,
                    access_decision_id="decision-denied",
                    binding_id="binding-1",
                )
            ],
            parser_version="parser-v1",
            embedding_model_hash="c" * 64,
            llm_config_hash="d" * 64,
        )


def test_paperqa_index_and_answer_are_hash_and_span_bound(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    repository.save_access_decision(
        AccessDecision(
            decision_id="decision-approved",
            resource_id="resource-1",
            access_mode=AccessMode.OPEN_ACCESS,
            full_text_available=True,
            persistent_storage_allowed=True,
            model_processing_allowed=True,
            decision_basis="open license verified",
        )
    )
    service = PaperQAEvidenceService(
        repository,
        index_runner=lambda _corpus: "e" * 64,
        runner=lambda _corpus, _question: {
            "answer": "The paper reports a paired evaluation.",
            "evidence_spans": [
                {
                    "resource_id": "resource-1",
                    "snapshot_id": "snapshot-1",
                    "page": 2,
                    "start_offset": 10,
                    "end_offset": 55,
                    "support_relation": "supports",
                    "quote_hash": "f" * 64,
                }
            ],
        },
    )
    corpus = service.create_corpus(
        study_id="study",
        phase=RetrievalPhase.DISCOVERY,
        documents=[
            CorpusDocument(
                resource_id="resource-1",
                snapshot_id="snapshot-1",
                snapshot_hash="b" * 64,
                access_decision_id="decision-approved",
                binding_id="binding-1",
            )
        ],
        parser_version="parser-v1",
        embedding_model_hash="c" * 64,
        llm_config_hash="d" * 64,
    )
    assert corpus.status is CorpusStatus.DRAFT
    ready = service.build_index(corpus.corpus_id)
    assert ready.status is CorpusStatus.READY
    assert service.get_index_status(ready.corpus_id).index_hash == "e" * 64
    result = service.query_evidence(ready.corpus_id, "What design was used?")
    assert result.verdict_authority is False
    assert result.evidence_spans[0].page == 2
    related = service.synthesize_related_work(
        ready.corpus_id,
        "How should paired evaluations be designed?",
    )
    assert "Synthesize how the frozen sources relate" in related.question
    conflicts = service.find_conflicting_evidence(
        ready.corpus_id,
        "Every evaluation is paired.",
    )
    assert "Identify evidence in the frozen corpus" in conflicts.question

    invalidated = service.invalidate_index(
        ready.corpus_id,
        reason="source snapshot was superseded",
    )
    assert invalidated.status is CorpusStatus.INVALIDATED
    assert invalidated.index_hash == "e" * 64
    with pytest.raises(ValueError, match="not ready"):
        service.query_evidence(ready.corpus_id, "Can the old index be used?")


def test_paperqa_document_changes_create_a_new_draft_corpus(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    documents = []
    for suffix in ("1", "2"):
        resource_id = f"resource-{suffix}"
        decision_id = f"decision-{suffix}"
        repository.save_access_decision(
            AccessDecision(
                decision_id=decision_id,
                resource_id=resource_id,
                access_mode=AccessMode.OPEN_ACCESS,
                full_text_available=True,
                persistent_storage_allowed=True,
                model_processing_allowed=True,
                decision_basis="open license verified",
            )
        )
        documents.append(
            CorpusDocument(
                resource_id=resource_id,
                snapshot_id=f"snapshot-{suffix}",
                snapshot_hash=suffix * 64,
                access_decision_id=decision_id,
                binding_id=f"binding-{suffix}",
            )
        )

    service = PaperQAEvidenceService(repository, index_runner=lambda _: "e" * 64)
    original = service.create_corpus(
        study_id="study",
        phase=RetrievalPhase.DISCOVERY,
        documents=[documents[0]],
        parser_version="parser-v1",
        embedding_model_hash="c" * 64,
        llm_config_hash="d" * 64,
    )
    expanded = service.add_document(original.corpus_id, documents[1])
    assert expanded.corpus_id != original.corpus_id
    assert [item.resource_id for item in expanded.documents] == [
        "resource-1",
        "resource-2",
    ]
    assert repository.load_corpus(original.corpus_id).documents == [documents[0]]

    reduced = service.remove_document(
        expanded.corpus_id,
        resource_id="resource-1",
    )
    assert reduced.corpus_id not in {original.corpus_id, expanded.corpus_id}
    assert reduced.documents == [documents[1]]
    with pytest.raises(ValueError, match="only a draft"):
        service.add_document(service.build_index(original.corpus_id).corpus_id, documents[1])


def test_real_paperqa_runtime_indexes_hash_bound_pdf(tmp_path: Path) -> None:
    repository = RetrievalRepository(tmp_path)
    pdf = _minimal_text_pdf(
        "Evidence-bound evaluation preserves provenance and source identity."
    )
    artifact = repository.write_binary_artifact(
        project_id="project-real-paperqa",
        study_id="study-real-paperqa",
        step_instance_id="step-paperqa-source",
        kind="open_access_full_text",
        content=pdf,
        producer="fixture",
        extension="pdf",
    )
    resource = ExternalResource(
        resource_id="resource-real-paperqa",
        resource_type=ResourceType.PUBLICATION,
        canonical_identifier="doi:10.1234/paperqa",
        title="Evidence-bound evaluation",
        authors_or_owners=["Fixture Author"],
        publication_or_release_date="2026",
        doi="10.1234/paperqa",
        url="https://example.org/paperqa.pdf",
        license="cc-by-4.0",
        providers=["open_access"],
        metadata_verification_status=MetadataVerificationStatus.VERIFIED,
        canonical_metadata_hash="1" * 64,
    )
    repository.save_resource(resource)
    snapshot = ResourceSnapshot(
        snapshot_id="snapshot-real-paperqa",
        resource_id=resource.resource_id,
        content_level="full_text",
        raw_response_artifact_id=artifact.artifact_id,
        normalized_content_artifact_id=artifact.artifact_id,
        provider="open_access",
        content_hash=hashlib.sha256(pdf).hexdigest(),
        mime_type="application/pdf",
        byte_size=len(pdf),
        license_status="cc-by-4.0",
        access_status="retrieved",
        access_mode="open_access",
        model_processing_allowed=True,
    )
    repository.save_snapshot(snapshot)
    binding = ResourceUseBinding(
        binding_id="binding-real-paperqa",
        resource_id=resource.resource_id,
        snapshot_id=snapshot.snapshot_id,
        project_id="project-real-paperqa",
        study_id="study-real-paperqa",
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id="step-paperqa-source",
        purpose="related_work_search",
        usage_role="background_source",
        target_type="study_direction",
        target_id="direction",
        target_field="background_sources",
        relation="contextualizes",
        verification_status=MetadataVerificationStatus.VERIFIED,
    )
    repository.save_binding(binding)
    decision = AccessDecision(
        decision_id="decision-real-paperqa",
        resource_id=resource.resource_id,
        access_mode=AccessMode.OPEN_ACCESS,
        full_text_available=True,
        persistent_storage_allowed=True,
        model_processing_allowed=True,
        decision_basis="fixture open license",
    )
    repository.save_access_decision(decision)
    service = PaperQAEvidenceService(repository)
    corpus = service.build_corpus(
        study_id="study-real-paperqa",
        phase=RetrievalPhase.DISCOVERY,
        documents=[
            CorpusDocument(
                resource_id=resource.resource_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_hash=snapshot.content_hash,
                access_decision_id=decision.decision_id,
                binding_id=binding.binding_id,
            )
        ],
        parser_version="paperqa-2026.3.18",
        embedding_model_hash=hashlib.sha256(b"paperqa-sparse").hexdigest(),
        llm_config_hash=hashlib.sha256(b"codex-evidence-synthesis").hexdigest(),
    )

    indexed = service.index(corpus.corpus_id)

    assert indexed.status is CorpusStatus.READY
    assert indexed.index_hash
    index_artifact = next(
        item
        for item in repository.list_artifacts("study-real-paperqa")
        if item.kind == "paperqa_index"
    )
    state_artifact = next(
        item
        for item in repository.list_artifacts("study-real-paperqa")
        if item.kind == "paperqa_index_state"
    )
    assert indexed.index_hash == index_artifact.content_hash
    assert state_artifact.input_artifact_ids == [artifact.artifact_id]
    index_manifest = json.loads(Path(index_artifact.path).read_text(encoding="utf-8"))
    assert index_manifest["state_artifact_id"] == state_artifact.artifact_id
    assert index_manifest["state_content_hash"] == state_artifact.content_hash

    from research_forge.retrieval.evidence.paperqa_runtime import PaperQARuntime

    runtime = PaperQARuntime(repository)
    loaded = runtime._load_indexed_docs(indexed)
    assert loaded is not None
    assert loaded.name == indexed.corpus_id
    assert list(loaded.docs) == [snapshot.content_hash]
    assert runtime._load_indexed_docs(indexed) is loaded

    source_path = Path(artifact.path)
    source_bytes = source_path.read_bytes()
    source_path.write_bytes(source_bytes + b"replaced")
    with pytest.raises(ValueError, match="source snapshot hash verification failed"):
        PaperQARuntime(repository)._load_indexed_docs(indexed)
    source_path.write_bytes(source_bytes)

    state_path = Path(state_artifact.path)
    state_path.write_bytes(state_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="state hash verification failed"):
        PaperQARuntime(repository)._load_indexed_docs(indexed)


def test_paperqa_settings_disable_implicit_multimodal_llm_calls() -> None:
    from research_forge.retrieval.evidence.paperqa_runtime import (
        _paperqa_settings,
    )

    settings = _paperqa_settings()

    assert settings.embedding == "sparse"
    assert settings.parsing.multimodal is False
    assert settings.parsing.use_doc_details is False


def test_paperqa_user_answer_strips_internal_context_citations() -> None:
    from research_forge.retrieval.evidence.paperqa_runtime import (
        _clean_synthesized_answer,
    )

    answer = (
        "First result. [pqac-a1b2c3, pqac-d4e5f6]\n\n"
        "Second result. \ue200cite\ue202pqac-a1b2c3\ue202pqac-d4e5f6\ue201\n"
        "Third result (pqac-a1b2c3)."
    )

    cleaned = _clean_synthesized_answer(answer)

    assert cleaned == "First result.\n\nSecond result.\nThird result."
    assert "pqac-" not in cleaned
    assert "\ue200" not in cleaned


def test_paperqa_answer_without_spans_cannot_enter_ledger(
    tmp_path: Path,
) -> None:
    repository = RetrievalRepository(tmp_path)
    repository.save_access_decision(
        AccessDecision(
            decision_id="decision-approved",
            resource_id="resource-1",
            access_mode=AccessMode.OPEN_ACCESS,
            full_text_available=True,
            persistent_storage_allowed=True,
            model_processing_allowed=True,
            decision_basis="open license verified",
        )
    )
    service = PaperQAEvidenceService(
        repository,
        index_runner=lambda _corpus: "e" * 64,
        runner=lambda _corpus, _question: {
            "answer": "Unsupported generated text.",
            "evidence_spans": [],
        },
    )
    corpus = service.build_corpus(
        study_id="study",
        phase=RetrievalPhase.SYNTHESIS,
        documents=[
            CorpusDocument(
                resource_id="resource-1",
                snapshot_id="snapshot-1",
                snapshot_hash="b" * 64,
                access_decision_id="decision-approved",
                binding_id="binding-1",
            )
        ],
        parser_version="parser-v1",
        embedding_model_hash="c" * 64,
        llm_config_hash="d" * 64,
    )
    service.index(corpus.corpus_id)
    with pytest.raises(ValueError, match="without source spans"):
        service.ask(corpus.corpus_id, "Unsupported?")


def test_paperqa_reuses_matching_immutable_evidence_result(tmp_path: Path) -> None:
    repository = RetrievalRepository(tmp_path)
    repository.save_access_decision(
        AccessDecision(
            decision_id="decision-reuse",
            resource_id="resource-reuse",
            access_mode=AccessMode.OPEN_ACCESS,
            full_text_available=True,
            persistent_storage_allowed=True,
            model_processing_allowed=True,
            decision_basis="open license verified",
        )
    )
    calls = 0

    def run(_corpus, _question):
        nonlocal calls
        calls += 1
        return {
            "answer": "A clean evidence-bound answer.",
            "evidence_spans": [
                {
                    "resource_id": "resource-reuse",
                    "snapshot_id": "snapshot-reuse",
                    "page": 1,
                    "section": "context-1",
                    "start_offset": 0,
                    "end_offset": 10,
                    "support_relation": "supports",
                    "quote_hash": "f" * 64,
                }
            ],
        }

    service = PaperQAEvidenceService(
        repository,
        index_runner=lambda _corpus: "e" * 64,
        runner=run,
    )
    corpus = service.build_corpus(
        study_id="study-reuse",
        phase=RetrievalPhase.SYNTHESIS,
        documents=[
            CorpusDocument(
                resource_id="resource-reuse",
                snapshot_id="snapshot-reuse",
                snapshot_hash="b" * 64,
                access_decision_id="decision-reuse",
                binding_id="binding-reuse",
            )
        ],
        parser_version="parser-v1",
        embedding_model_hash="c" * 64,
        llm_config_hash="d" * 64,
    )
    service.index(corpus.corpus_id)

    first = service.ask(corpus.corpus_id, "What is supported?")
    second = service.ask(corpus.corpus_id, "What is supported?")
    forced = service.ask(
        corpus.corpus_id,
        "What is supported?",
        reuse_existing=False,
    )

    assert first.result_id == second.result_id
    assert forced.result_id == first.result_id
    assert calls == 2


def test_openai_web_domain_filters_are_enforced() -> None:
    adapter = OpenAIWebSearchAdapter(
        runner=lambda _plan: {
            "output": [{"type": "web_search_call"}],
            "output_text": json.dumps(
                {
                    "queries": ["official docs"],
                    "consulted_sources": [],
                    "results": [
                        {
                            "title": "Allowed",
                            "url": "https://docs.example.org/spec",
                            "snippet": "Official.",
                            "published_at": "2026",
                            "source_name": "Example",
                        },
                        {
                            "title": "Blocked",
                            "url": "https://blocked.example.net/page",
                            "snippet": "Blocked.",
                            "published_at": "2026",
                            "source_name": "Blocked",
                        },
                    ],
                }
            ),
        }
    )
    result = adapter.search(
        _plan(
            allowed_domains=["example.org"],
            blocked_domains=["blocked.example.net"],
        )
    )
    assert [item.title for item in result.signals] == ["Allowed"]


def test_openai_web_policy_domain_uses_actual_configured_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.test/v1")
    adapter = OpenAIWebSearchAdapter(runner=lambda _plan: {})
    assert adapter.domains == {"gateway.example.test"}


@pytest.mark.parametrize(
    "base_url",
    [
        "http://gateway.example.test/v1",
        "gateway.example.test/v1",
        "https://user:secret@gateway.example.test/v1",
    ],
)
def test_openai_web_rejects_unsafe_configured_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", base_url)
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        OpenAIWebSearchAdapter(runner=lambda _plan: {})


def test_openai_web_classifies_permission_denial_as_authentication_error() -> None:
    adapter = OpenAIWebSearchAdapter(runner=lambda _plan: {})
    assert (
        adapter.classify_error(RuntimeError("Permission denied"))
        is ProviderErrorClass.AUTHENTICATION_ERROR
    )


def test_official_web_routes_to_codex_without_synapai() -> None:
    decision = QueryRouter().route(
        study_id="study",
        phase=RetrievalPhase.DISCOVERY,
        purpose="related_work_search",
        intent=QueryIntent.OFFICIAL_WEB,
    )

    assert decision.providers == ["codex_native_web_search"]


def test_experimentation_open_metric_search_is_denied() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        QueryRouter().route(
            study_id="study",
            phase=RetrievalPhase.EXPERIMENTATION,
            purpose="metric_grounding",
            intent=QueryIntent.PUBLICATION,
        )


def test_adoption_signals_do_not_become_scientific_scores() -> None:
    resources = normalize_signals(
        [
            TrendSignal(
                signal_id="github-1",
                provider="github",
                signal_class="adoption_signal",
                query="agents",
                title="org/repo",
                summary="Implementation.",
                url="https://github.com/org/repo",
                published_at="2026",
                source_name="org",
                engagement={"stars": 100000},
                trend_score=1.0,
                scientific_density=0.0,
                metadata={
                    "resource_type": "code_repository",
                    "repository": "org/repo",
                    "commit": "a" * 40,
                    "scientific_quality_score": None,
                },
            )
        ]
    )
    assert resources[0].metadata["scientific_quality_score"] is None
    assert resources[0].metadata["attention"]["stars"] == 100000


def test_readiness_and_institution_api_round_trip(tmp_path: Path) -> None:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    server = create_server(
        "127.0.0.1",
        0,
        runs_root=tmp_path / "runs",
        static_root=static,
        workflow_root=tmp_path / "workflow",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(f"{base}/retrieval/readiness") as response:
            readiness = json.loads(response.read().decode())
        assert readiness["external_research_v1_ready"] is False

        request = Request(
            f"{base}/institution-sessions",
            data=json.dumps(
                {
                    "owner_user_id": "alice",
                    "project_id": "project",
                    "study_id": "study",
                    "institution_id": "university",
                    "target_url": "https://library.example.edu/login",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            session = json.loads(response.read().decode())
        with urlopen(
            f"{base}/institution-sessions/{session['session_id']}?owner_user_id=alice"
        ) as response:
            loaded = json.loads(response.read().decode())
        assert loaded["status"] == "waiting_for_user"

        revoke = Request(
            f"{base}/institution-sessions/{session['session_id']}?owner_user_id=alice",
            method="DELETE",
        )
        with urlopen(revoke) as response:
            revoked = json.loads(response.read().decode())
        assert revoked["status"] == "revoked"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_workflow_retrieval_dag_api_is_end_to_end(tmp_path: Path) -> None:
    workflow_root = tmp_path / "workflow"
    repository = WorkflowRepository(workflow_root)
    project = repository.create_project(
        "API retrieval DAG",
        project_id="project-api-retrieval-dag",
    )
    study = repository.create_study(
        project.project_id,
        "API retrieval DAG",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-api-retrieval-dag",
    )
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    server = create_server(
        "127.0.0.1",
        0,
        runs_root=tmp_path / "runs",
        static_root=static,
        workflow_root=workflow_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            (
                f"http://127.0.0.1:{server.server_port}/studies/"
                f"{study.study_id}/retrieval-dags"
            ),
            data=json.dumps(
                {
                    "stage": "discovery",
                    "run_key": "api-v1",
                    "run": False,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            payload = json.loads(response.read().decode())
        assert len(payload["created_step_ids"]) == len(
            EXTERNAL_RESEARCH_DAGS["discovery"]
        )
        assert {item["step_type"] for item in payload["snapshot"]["steps"]} == set(
            EXTERNAL_RESEARCH_DAGS["discovery"]
        )

        all_request = Request(
            (
                f"http://127.0.0.1:{server.server_port}/studies/"
                f"{study.study_id}/retrieval-dags"
            ),
            data=json.dumps(
                {
                    "stage": "all",
                    "run_key": "api-all-v1",
                    "run": False,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(all_request) as response:
            all_payload = json.loads(response.read().decode())
        assert len(all_payload["created_step_ids"]) == sum(
            len(EXTERNAL_RESEARCH_DAGS[stage])
            for stage in ("discovery", "protocol", "experimentation", "synthesis")
        )
        assert not any(
            item["task_group"] == "external_research:repair:api-all-v1"
            for item in all_payload["snapshot"]["steps"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
