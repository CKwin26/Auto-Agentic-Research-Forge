from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from research_forge.orchestration import TaskRequest
from research_forge.project_bundle import NoveltyCandidate
from research_forge.service import create_project
from research_forge.workflow_domain import (
    ArtifactRole,
    ComponentDefectNotice,
    EntryMode,
    ExecutionStatus,
    ExecutorType,
    Phase,
    StudyLifecycle,
    WorkflowRepository,
)
from research_forge.workflow_scheduler import (
    BlockedStepError,
    PersistentDAGScheduler,
    TransientStepError,
    approve_discovery_direction,
    audit_discovery_study,
    auto_repair_discovery_study,
    create_project_discovery_study,
    run_project_discovery,
    stage_one_handlers,
)
from research_forge.workflow_tasks import create_workflow_orchestrator


def _project_bundle(root: Path) -> Path:
    source = root / "bundle"
    (source / "protocols").mkdir(parents=True)
    (source / "outputs").mkdir()
    (source / "reports").mkdir()
    (source / "protocols" / "demo.json").write_text(
        json.dumps(
            {
                "version": "demo",
                "hypothesis": "The candidate improves accuracy.",
                "metric": "accuracy",
            }
        ),
        encoding="utf-8",
    )
    (source / "outputs" / "demo.json").write_text(
        json.dumps({"accuracy": 0.81, "valid": True}), encoding="utf-8"
    )
    (source / "reports" / "demo.md").write_text(
        "# Contributions\n\nThe paired evaluation improves accuracy to 0.81.\n",
        encoding="utf-8",
    )
    return source


def test_requested_external_discovery_stays_blocked_until_project_policy_approval(
    tmp_path: Path,
) -> None:
    source = _project_bundle(tmp_path)
    workflow_root = tmp_path / "workflow"

    result = run_project_discovery(
        source,
        repository_root=workflow_root,
        include_external=True,
        identity="network-approval-required",
        auto_repair=False,
    )

    retrieval_step = next(
        item
        for item in result["workflow"]["steps"]
        if item["step_type"] == "execute_discovery_retrieval"
    )
    assert retrieval_step["status"] == "blocked"
    assert (
        retrieval_step["blocker"]["kind"]
        == "retrieval_policy_approval_required"
    )
    assert result["workflow"]["study"]["phase"] == "discovery"
    assert not result["workflow"]["study"].get("active_scope_version")


def test_bundle_inspect_surfaces_project_network_approval_as_user_requirement(
    tmp_path: Path,
) -> None:
    source = _project_bundle(tmp_path)
    orchestrator = create_workflow_orchestrator(tmp_path / "tasks")
    task = orchestrator.submit(
        TaskRequest(
            operation="bundle.inspect",
            payload={
                "source": str(source),
                "workflow_root": str(tmp_path / "workflow"),
                "discover_claims": True,
            },
            idempotency_key="network-approval-task",
        )
    )

    waiting = orchestrator.run_sync(task.task_id)

    assert waiting.status.value == "waiting_for_user"
    assert waiting.requirements[0].kind.value == "permission"
    assert any(
        item.get("action_id") == "approve_public_research"
        for item in waiting.requirements[0].accepted_inputs
    )
    assert "公开只读检索" in waiting.requirements[0].title


def test_retry_reopens_only_dependency_blocked_descendants(tmp_path: Path) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Retry cascade")
    study = repository.create_study(
        project.project_id,
        "Retry cascade",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    parent = repository.add_step(
        study.study_id,
        "parent",
        Phase.PAPER,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    child = repository.add_step(
        study.study_id,
        "child",
        Phase.PAPER,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[parent.step_instance_id],
    )
    grandchild = repository.add_step(
        study.study_id,
        "grandchild",
        Phase.PAPER,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[child.step_instance_id],
    )
    unrelated = repository.add_step(
        study.study_id,
        "unrelated",
        Phase.PAPER,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    repository.update_step(
        study.study_id,
        parent.step_instance_id,
        ExecutionStatus.FAILED,
        blocker={"kind": "permanent_execution_error"},
    )
    for step, dependency in ((child, parent), (grandchild, child)):
        repository.update_step(
            study.study_id,
            step.step_instance_id,
            ExecutionStatus.BLOCKED,
            blocker={
                "kind": "upstream_not_succeeded",
                "dependency_step_ids": [dependency.step_instance_id],
            },
        )
    repository.update_step(
        study.study_id,
        unrelated.step_instance_id,
        ExecutionStatus.BLOCKED,
        blocker={"kind": "scientific_integrity", "message": "keep blocked"},
    )

    scheduler = PersistentDAGScheduler(repository, {})
    scheduler.retry_step(study.study_id, parent.step_instance_id)

    assert repository.load_step(
        study.study_id, parent.step_instance_id
    ).status is ExecutionStatus.QUEUED
    assert repository.load_step(
        study.study_id, child.step_instance_id
    ).status is ExecutionStatus.QUEUED
    assert repository.load_step(
        study.study_id, grandchild.step_instance_id
    ).status is ExecutionStatus.QUEUED
    assert repository.load_step(
        study.study_id, unrelated.step_instance_id
    ).status is ExecutionStatus.BLOCKED


def test_concurrent_success_cannot_overwrite_blocked_phase_redirect(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Redirect precedence")
    study = repository.create_study(
        project.project_id,
        "Redirect precedence",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    repository.save_study(
        study.model_copy(update={"phase": Phase.PAPER}),
        "prepare_parallel_redirect_test",
    )
    repository.add_step(
        study.study_id,
        "requires_stage3_backfill",
        Phase.PAPER,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    repository.add_step(
        study.study_id,
        "parallel_paper_success",
        Phase.PAPER,
        ExecutorType.DETERMINISTIC_SERVICE,
    )

    def redirect(_: object) -> dict[str, object]:
        raise BlockedStepError(
            "Stage 3 evidence backfill required",
            kind="stage3_evidence_backfill_required",
            redirect_phase=Phase.EXPERIMENT,
        )

    def late_paper_success(_: object) -> dict[str, object]:
        time.sleep(0.05)
        return {"_workflow_next_phase": Phase.PAPER.value}

    scheduler = PersistentDAGScheduler(
        repository,
        {
            "requires_stage3_backfill": redirect,
            "parallel_paper_success": late_paper_success,
        },
        max_concurrency=2,
    )
    result = scheduler.run(study.study_id)

    assert result["study"]["phase"] == Phase.EXPERIMENT.value
    blocked = next(
        item
        for item in result["steps"]
        if item["step_type"] == "requires_stage3_backfill"
    )
    assert blocked["blocker"]["redirect_phase"] == Phase.EXPERIMENT.value


def test_stage_one_runs_as_persisted_dag_and_stops_at_scope_gate(
    tmp_path: Path,
) -> None:
    source = _project_bundle(tmp_path)
    workflow_root = tmp_path / "workflow"

    result = run_project_discovery(
        source,
        repository_root=workflow_root,
        include_external=False,
        identity="test-run",
    )
    steps = {item["step_type"]: item for item in result["workflow"]["steps"]}

    assert steps["project_scan"]["status"] == "succeeded"
    assert steps["author_claim_extraction"]["status"] == "succeeded"
    assert steps["candidate_discovery"]["status"] == "succeeded"
    assert steps["academic_concept_normalization"]["status"] == "succeeded"
    for step_type in (
        "draft_discovery_query_plan",
        "evaluate_network_policy",
        "sanitize_discovery_queries",
        "execute_discovery_retrieval",
        "normalize_external_resources",
        "deduplicate_external_resources",
        "verify_external_metadata",
        "build_discovery_source_set",
    ):
        assert steps[step_type]["status"] == "succeeded"
    assert steps["scope_drafting"]["status"] == "succeeded"
    assert steps["discovery_portfolio"]["status"] == "succeeded"
    assert steps["scope_review"]["status"] == "waiting_for_user"
    assert steps["freeze_scope_contract"]["status"] == "queued"
    assert steps["freeze_discovery_source_set"]["status"] == "queued"
    assert result["workflow"]["task_groups"]["retrieval_gateway"] == {
        "completed": 8,
        "total": 9,
    }
    assert set(result["claim_discovery"]["provider_status"].values()) == {
        "not_requested"
    }
    assert {
        "paper_search_mcp",
        "github",
        "huggingface",
        "codex_native_web_search",
        "openai_web_search",
        "redfox_wechat",
        "semantic_scholar",
        "crossref",
    }.issubset(result["claim_discovery"]["provider_status"])
    assert result["discovery_portfolio"]["directions"]
    assert result["discovery_portfolio"]["query_intents"]

    # A process restart resumes from persisted state and does not duplicate nodes.
    repeated = run_project_discovery(
        source,
        repository_root=workflow_root,
        include_external=False,
        identity="test-run",
    )
    assert len(repeated["workflow"]["steps"]) == len(result["workflow"]["steps"])
    assert {item["step_instance_id"] for item in repeated["workflow"]["steps"]} == {
        item["step_instance_id"] for item in result["workflow"]["steps"]
    }


def test_discovery_study_preserves_idea_to_paper_entry_mode(
    tmp_path: Path,
) -> None:
    source = _project_bundle(tmp_path)
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        title="Idea-originated discovery",
        include_external=False,
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    assert (
        repository.load_study(study_id).entry_mode
        is EntryMode.IDEA_TO_PAPER
    )


def test_idea_to_paper_discovery_prioritizes_owner_idea_over_template_code(
    tmp_path: Path,
) -> None:
    idea = (
        "在相同模型骨干与训练预算下，比较中文马克思主义文本微调、"
        "中性社会科学文本微调和未微调模型在劳动资本价值判断上的差异。"
    )
    source = create_project(
        "马克思主义文本微调研究",
        idea,
        slug="idea-study",
        root=tmp_path / "ideas",
    )
    result = run_project_discovery(
        source,
        repository_root=tmp_path / "workflow",
        title="马克思主义文本微调研究",
        include_external=False,
        entry_mode=EntryMode.IDEA_TO_PAPER,
        auto_repair=False,
    )
    assert result["workflow"]["study"]["entry_mode"] == "idea_to_paper"
    repository = WorkflowRepository(tmp_path / "workflow")
    scope = repository.latest_scope_contract(result["study_id"])
    assert scope is not None
    assert "中文马克思主义语料参数高效微调" in scope.direction
    assert "劳动—资本情境" in scope.research_question


def test_package_manager_cache_root_is_blocked_before_project_scan(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".pnpm-store" / "v11" / "files" / "aa"
    source.mkdir(parents=True)
    (source / "cached-package.json").write_text(
        '{"name": "not-a-project"}',
        encoding="utf-8",
    )
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        tmp_path / ".pnpm-store",
        include_external=False,
        identity="dependency-cache-boundary",
    )

    snapshot = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study_id)

    scan = next(
        item
        for item in snapshot["steps"]
        if item["step_type"] == "project_scan"
    )
    assert scan["status"] == "blocked"
    assert "dependency cache" in scan["blocker"]["message"]
    assert "20000-file inventory limit" not in scan["blocker"]["message"]


def test_device_driver_installer_is_blocked_as_non_research_input(
    tmp_path: Path,
) -> None:
    source = tmp_path / "camera-driver"
    drivers = source / "Drivers" / "CameraExtension"
    drivers.mkdir(parents=True)
    (source / "Install.bat").write_text(
        'pnputil -a "Drivers\\\\CameraExtension\\\\camera.inf" /install\n',
        encoding="utf-8",
    )
    (drivers / "camera.inf").write_text("[Version]\n", encoding="utf-8")
    (drivers / "camera.cat").write_bytes(b"catalog")
    (drivers / "camera.sys").write_bytes(b"driver")
    (drivers / "10.0.0.1.txt").write_bytes(b"")
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="driver-installer-boundary",
    )

    snapshot = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study_id)

    scan = next(
        item
        for item in snapshot["steps"]
        if item["step_type"] == "project_scan"
    )
    assert scan["status"] == "blocked"
    assert "device-driver installation bundle" in scan["blocker"]["message"]
    portfolio = next(
        item
        for item in snapshot["steps"]
        if item["step_type"] == "discovery_portfolio"
    )
    assert portfolio["status"] == "blocked"


def test_shared_runtime_bundle_is_blocked_before_discovery(
    tmp_path: Path,
) -> None:
    source = tmp_path / "common_apps"
    shared = source / "dependency_shared(999998)"
    shared.mkdir(parents=True)
    for name in (
        "runtime-a.cab",
        "runtime-b.cab",
        "runtime-c.cab",
        "setup.exe",
        "support.dll",
        "payload.dat",
    ):
        (shared / name).write_bytes(b"binary payload")
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="shared-runtime-boundary",
    )

    snapshot = PersistentDAGScheduler(
        repository,
        stage_one_handlers(),
    ).run(study_id)

    scan = next(
        item
        for item in snapshot["steps"]
        if item["step_type"] == "project_scan"
    )
    assert scan["status"] == "blocked"
    assert "shared binary runtime/dependency" in scan["blocker"]["message"]
    portfolio = next(
        item
        for item in snapshot["steps"]
        if item["step_type"] == "discovery_portfolio"
    )
    assert portfolio["status"] == "blocked"


def test_stage_one_discovers_a_code_only_advisor_project(
    tmp_path: Path,
) -> None:
    source = tmp_path / "advisor-radar-deploy"
    (source / "lib").mkdir(parents=True)
    (source / "package.json").write_text(
        json.dumps({"name": "advisor-radar", "version": "2.4.0"}),
        encoding="utf-8",
    )
    for name in (
        "recommendation.ts",
        "scoring.ts",
        "scoring-standards.ts",
        "pi-review.ts",
        "quality-metrics.ts",
    ):
        (source / "lib" / name).write_text(
            "export const enabled = true;\n", encoding="utf-8"
        )

    result = run_project_discovery(
        source,
        repository_root=tmp_path / "workflow",
        include_external=False,
        identity="code-only",
    )
    steps = {item["step_type"]: item for item in result["workflow"]["steps"]}
    portfolio = result["discovery_portfolio"]

    assert steps["candidate_discovery"]["status"] == "succeeded"
    assert steps["scope_drafting"]["status"] == "succeeded"
    assert portfolio["status"] == "external_grounding_incomplete"
    assert portfolio["recommended_direction_id"] is not None
    direction = next(
        item
        for item in portfolio["directions"]
        if item["direction_id"] == portfolio["recommended_direction_id"]
    )
    assert direction["title"] == (
        "结构化证据评分与复核对学术导师推荐可靠性的影响"
    )
    assert direction["evidence_chain_level"] == "inferred_chain"


def test_discovery_auto_repair_creates_successor_and_reuses_only_safe_steps(
    tmp_path: Path,
) -> None:
    source = _project_bundle(tmp_path)
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="legacy-claim-parser",
    )
    handlers = stage_one_handlers()

    def legacy_claim_parser(context):
        scan = context.result("project_scan")
        resource = scan["resources"][0]
        return {
            "author_claims": [
                {
                    "claim_id": "legacy-procedural-claim",
                    "statement": (
                        "Employees must install the winding fixture before measurement."
                    ),
                    "claim_type": "method",
                    "origin": "author_asserted",
                    "source_spans": [
                        {
                            "path": resource["path"],
                            "sha256": resource["sha256"],
                            "line": 1,
                            "section": "Contributions",
                        }
                    ],
                    "evidence_status": "author_statement_only",
                }
            ]
        }

    handlers["author_claim_extraction"] = legacy_claim_parser
    PersistentDAGScheduler(repository, handlers).run(study_id)
    original_claim_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "author_claim_extraction"
    )
    original_payload = repository.load_step_result(
        study_id, original_claim_step.step_instance_id
    )

    outcome = auto_repair_discovery_study(repository, study_id)

    assert outcome is not None
    assert outcome["regression_passed"] is True
    assert {"project_scan", "candidate_discovery"}.issubset(
        outcome["reused_step_types"]
    )
    assert "author_claim_extraction" not in outcome["reused_step_types"]
    successor_id = outcome["successor_study_id"]
    successor_claim_step = next(
        item
        for item in repository.list_steps(successor_id)
        if item.step_type == "author_claim_extraction"
    )
    successor_payload = repository.load_step_result(
        successor_id, successor_claim_step.step_instance_id
    )
    assert all(
        not item["statement"].startswith("Employees must")
        for item in successor_payload["author_claims"]
    )
    assert (
        repository.load_step_result(study_id, original_claim_step.step_instance_id)
        == original_payload
    )
    assert repository.load_study(study_id).lifecycle is StudyLifecycle.SUPERSEDED
    assert repository.load_study(successor_id).predecessor_study_id == study_id
    successor_scan = next(
        item
        for item in repository.list_steps(successor_id)
        if item.step_type == "project_scan"
    )
    successor_scan_artifact = next(
        item
        for item in repository.list_artifacts(successor_id)
        if item.artifact_id == successor_scan.output_artifact_ids[0]
    )
    assert successor_scan_artifact.predecessor_artifact_id is not None
    repair = repository.list_repair_contracts(study_id)[0]
    assert repair.status.value == "completed"
    assert repair.successor_study_id == successor_id
    assert (
        repository.list_diagnostics(study_id)[0].failure_code
        == "procedural_claim_false_positive"
    )


def test_cache_contamination_is_diagnosed_and_repaired_from_project_scan(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bundle"
    cache = source / "torch_cache"
    cache.mkdir(parents=True)
    (cache / "MODEL_CARD.md").write_text("# Cached dependency\n", encoding="utf-8")
    (source / "README.md").write_text("# Primary project\n", encoding="utf-8")
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="legacy-cache-boundary",
    )
    handlers = stage_one_handlers()

    def contaminated_candidate(_context):
        candidate = NoveltyCandidate(
            track_id="cached-model-card-v1",
            novelty_seed="Evaluate an unrelated cached dependency.",
            protocol_path="torch_cache/MODEL_CARD.md",
            latest_artifact_at="2026-01-01T00:00:00+00:00",
            evidence_maturity="mixed_or_unspecified",
            artifact_chain_complete=False,
            protocol_bound_to_output=False,
            paperability_score=20,
            blockers=["cached third-party content"],
            source_mode="derived_materials",
            closure_input_ready=True,
            display_title="Cached model card",
        )
        return {"candidates": [candidate.model_dump(mode="json")]}

    handlers["candidate_discovery"] = contaminated_candidate
    PersistentDAGScheduler(repository, handlers).run(study_id)

    diagnostics = audit_discovery_study(repository, study_id)
    assert [item.failure_code for item in diagnostics] == [
        "cache_boundary_contamination"
    ]
    assert diagnostics[0].earliest_affected_step_type == "project_scan"

    outcome = auto_repair_discovery_study(repository, study_id)

    assert outcome is not None
    assert outcome["regression_passed"] is True
    assert outcome["reused_step_types"] == []
    successor_id = outcome["successor_study_id"]
    successor_candidate_step = next(
        item
        for item in repository.list_steps(successor_id)
        if item.step_type == "candidate_discovery"
    )
    successor_candidates = repository.load_step_result(
        successor_id, successor_candidate_step.step_instance_id
    )["candidates"]
    assert all(
        not str(item.get("protocol_path", "")).startswith("torch_cache/")
        for item in successor_candidates
    )


def test_scope_approval_completes_waiting_owner_node(tmp_path: Path) -> None:
    source = _project_bundle(tmp_path)
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository, source, include_external=False, identity="approval"
    )
    scheduler = PersistentDAGScheduler(repository, stage_one_handlers())
    scheduler.run(study_id)
    portfolio_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "discovery_portfolio"
    )
    portfolio = repository.load_step_result(
        study_id, portfolio_step.step_instance_id
    )["discovery_portfolio"]
    snapshot = approve_discovery_direction(
        repository,
        study_id,
        portfolio["recommended_direction_id"],
        decided_by="project_owner",
        reason="Keep the study computational and exclude deployment claims.",
        scope_overrides={
            "research_question": "Does the frozen bundle support the revised computational question?",
            "scope_in": ["frozen local bundle", "computational evaluation"],
            "scope_out": ["deployment claims"],
        },
    )["workflow"]

    scope_review = next(
        item for item in snapshot["steps"] if item["step_type"] == "scope_review"
    )
    assert scope_review["status"] == "succeeded"
    freeze_scope = next(
        item for item in snapshot["steps"] if item["step_type"] == "freeze_scope_contract"
    )
    assert freeze_scope["status"] == "succeeded"
    contract = repository.latest_scope_contract(study_id)
    assert contract is not None
    assert contract.status.value == "frozen"
    assert contract.field_diff["selected_direction_id"] == portfolio[
        "recommended_direction_id"
    ]
    assert contract.research_question == (
        "Does the frozen bundle support the revised computational question?"
    )
    assert contract.scope_out == ["deployment claims"]
    assert contract.field_diff["owner_scope_overrides"]["scope_in"] == [
        "frozen local bundle",
        "computational evaluation",
    ]


def test_scheduler_retries_transient_failure_without_repeating_success(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Retry")
    study = repository.create_study(
        project.project_id, "Retry", entry_mode=EntryMode.IDEA_TO_PAPER
    )
    step = repository.add_step(
        study.study_id,
        "transient_probe",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    attempts = {"count": 0}

    def handler(_context):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TransientStepError("temporary")
        return {"ok": True}

    scheduler = PersistentDAGScheduler(
        repository, {"transient_probe": handler}, retry_backoff_seconds=0
    )
    scheduler.run(study.study_id)
    completed = repository.load_step(study.study_id, step.step_instance_id)
    scheduler.run(study.study_id)

    assert completed.status is ExecutionStatus.SUCCEEDED
    assert completed.attempt == 3
    assert attempts["count"] == 3


def test_expired_worker_cannot_publish_after_fencing_token_advances(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Fencing")
    study = repository.create_study(
        project.project_id, "Fencing", entry_mode=EntryMode.IDEA_TO_PAPER
    )
    step = repository.add_step(
        study.study_id,
        "fencing_probe",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    first = repository.update_step(
        study.study_id, step.step_instance_id, ExecutionStatus.RUNNING
    )
    second = repository.update_step(
        study.study_id, step.step_instance_id, ExecutionStatus.RETRYING
    )

    assert second.fencing_token == first.fencing_token + 1
    assert second.lease_id != first.lease_id
    with pytest.raises(ValueError, match="expired or mismatched"):
        repository.save_step_result(
            study.study_id,
            step.step_instance_id,
            {"worker": "expired"},
            lease_id=first.lease_id,
            fencing_token=first.fencing_token,
        )
    with pytest.raises(ValueError, match="expired or mismatched"):
        repository.update_step(
            study.study_id,
            step.step_instance_id,
            ExecutionStatus.FAILED,
            blocker={"kind": "late_worker", "message": "stale failure"},
            lease_id=first.lease_id,
            fencing_token=first.fencing_token,
        )
    artifact = repository.save_step_result(
        study.study_id,
        step.step_instance_id,
        {"worker": "current"},
        lease_id=second.lease_id,
        fencing_token=second.fencing_token,
    )
    assert artifact.sha256


def test_component_defect_creates_cross_study_impact_overlay(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Shared component")
    digest = "a" * 64
    studies = [
        repository.create_study(
            project.project_id,
            f"Study {index}",
            entry_mode=EntryMode.IDEA_TO_PAPER,
        )
        for index in range(2)
    ]
    for study in studies:
        repository.register_artifact(
            study.study_id,
            str(tmp_path / f"{study.study_id}.json"),
            digest,
            kind="shared_component",
            role=ArtifactRole.RUN,
        )
    analysis = repository.record_component_defect(
        ComponentDefectNotice(
            notice_id="component-defect-0123456789abcdef",
            component_digest=digest,
            component_name="paired evaluator",
            description="Boundary handling defect in shared evaluator.",
            severity="high",
            reported_by="maintainer",
        )
    )

    assert set(analysis.affected_study_ids) == {
        item.study_id for item in studies
    }
    assert all(
        list(
            (
                repository.root
                / "studies"
                / item.study_id
                / "errata"
            ).glob("research-erratum-*.json")
        )
        for item in studies
    )


def test_paused_study_starts_no_nodes(tmp_path: Path) -> None:
    source = _project_bundle(tmp_path)
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository, source, include_external=False, identity="paused"
    )
    repository.pause_study(study_id)

    PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)

    assert all(
        step.status is ExecutionStatus.QUEUED
        for step in repository.list_steps(study_id)
    )


def test_scheduler_recovers_interrupted_step_after_explicit_restart(
    tmp_path: Path,
) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Restart")
    study = repository.create_study(
        project.project_id, "Restart", entry_mode=EntryMode.IDEA_TO_PAPER
    )
    step = repository.add_step(
        study.study_id,
        "restart_probe",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    repository.update_step(
        study.study_id, step.step_instance_id, ExecutionStatus.RUNNING
    )

    snapshot = PersistentDAGScheduler(
        repository,
        {"restart_probe": lambda _context: {"recovered": True}},
        recover_interrupted=True,
    ).run(study.study_id)

    recovered = next(
        item for item in snapshot["steps"] if item["step_type"] == "restart_probe"
    )
    assert recovered["status"] == "succeeded"
    assert recovered["attempt"] == 2


def test_unconnected_cross_stage_retrieval_is_blocked_not_faked(
    tmp_path: Path,
) -> None:
    from research_forge.workflow_scheduler import stage_one_handlers

    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Protocol retrieval")
    study = repository.create_study(
        project.project_id,
        "Protocol retrieval",
        entry_mode=EntryMode.PROJECT_TO_PAPER,
    )
    step = repository.add_step(
        study.study_id,
        "execute_protocol_grounding",
        Phase.PROTOCOL,
        ExecutorType.RETRIEVAL_SERVICE,
    )

    PersistentDAGScheduler(repository, stage_one_handlers()).run(study.study_id)

    blocked = repository.load_step(study.study_id, step.step_instance_id)
    assert blocked.status is ExecutionStatus.BLOCKED
    assert blocked.blocker and blocked.blocker["kind"] == "not_implemented"
