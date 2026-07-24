from __future__ import annotations

import json
from pathlib import Path

from research_forge.workflow_domain import (
    EntryMode,
    ExecutionStatus,
    ExecutorType,
    Phase,
    StudyLifecycle,
    WorkflowRepository,
)
from research_forge.workflow_scheduler import (
    PersistentDAGScheduler,
    TransientStepError,
    approve_discovery_direction,
    auto_repair_discovery_study,
    create_project_discovery_study,
    run_project_discovery,
    stage_one_handlers,
)


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
