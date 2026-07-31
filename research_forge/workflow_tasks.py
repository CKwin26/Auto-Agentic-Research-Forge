"""Built-in task operations shared by the CLI and local web application."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import MacroStage
from .orchestration import (
    OperationDefinition,
    RequirementKind,
    TaskRecord,
    TaskRequirement,
    TaskRequirementError,
)


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"task payload requires {key}")
    return value


def _idea_start(payload: dict[str, Any], task: TaskRecord) -> dict[str, Any]:
    from .web_app import initialize_idea_research

    return initialize_idea_research(
        _required_text(payload, "idea"),
        title=str(payload.get("title", "")),
        idea_root=_required_text(payload, "idea_root"),
        task_id=task.task_id,
    )


def _bundle_inspect(payload: dict[str, Any], task: TaskRecord) -> dict[str, Any]:
    from .workflow_domain import (
        DiagnosticOwner,
        Phase,
        WorkflowRepository,
    )
    from .workflow_scheduler import (
        execute_discovery_repair,
        run_project_discovery,
    )

    workflow_root = str(payload.get("workflow_root", "")).strip()
    if not workflow_root:
        # Compatibility for web tasks created before deployment context was
        # injected server-side. This lets an explicit retry repair the old task
        # without asking the user for an internal filesystem setting.
        workflow_root = str(
            Path(__file__).resolve().parents[1]
            / "bundle_runs"
            / ".workflow-v2"
        )
    result = run_project_discovery(
        _required_text(payload, "source"),
        repository_root=workflow_root,
        include_external=bool(payload.get("discover_claims", False)),
        identity=task.task_id,
    )
    workflow = result.get("workflow") or {}
    policy_step = next(
        (
            item
            for item in workflow.get("steps", [])
            if item.get("step_type") == "execute_discovery_retrieval"
            and item.get("status") == "blocked"
            and (item.get("blocker") or {}).get("kind")
            == "retrieval_policy_approval_required"
        ),
        None,
    )
    approved = any(
        item.resolved
        and (item.resolution or {}).get("action_id")
        == "approve_public_research"
        and (item.resolution or {}).get("approve_public_research") is True
        and not (item.resolution or {}).get("alternative")
        for item in task.requirements
    )
    if policy_step and approved:
        repository = WorkflowRepository(workflow_root)
        repair = repository.propose_repair(
            str(result["study_id"]),
            diagnostic_owner=DiagnosticOwner.LITERATURE_GROUNDING,
            scientific_change=False,
            earliest_affected_phase=Phase.DISCOVERY,
            changed_artifact_ids=[],
            regression_checks=[
                {
                    "failure_code": "retrieval_policy_approval_required",
                    "expected": (
                        "a new query plan is built under the owner-approved "
                        "Project retrieval policy"
                    ),
                }
            ],
            earliest_affected_step_type="draft_discovery_query_plan",
        )
        execute_discovery_repair(
            repository,
            repair,
            decided_by="project_owner",
        )
        result = run_project_discovery(
            _required_text(payload, "source"),
            repository_root=workflow_root,
            include_external=True,
            identity=task.task_id,
            auto_repair=False,
        )
        workflow = result.get("workflow") or {}
        policy_step = next(
            (
                item
                for item in workflow.get("steps", [])
                if item.get("step_type") == "execute_discovery_retrieval"
                and item.get("status") != "succeeded"
            ),
            None,
        )
    if policy_step:
        raise TaskRequirementError(
            TaskRequirement.create(
                kind=RequirementKind.PERMISSION,
                title="批准此 Project 使用公开只读检索",
                reason=(
                    "阶段一已完成本地项目扫描，但在线论文、GitHub、"
                    "Hugging Face 和公开网页检索尚未执行。项目默认离线，"
                    "需要负责人明确批准后才能继续；未批准时不得把检索步骤"
                    "标记为完成。"
                ),
                stage=MacroStage.DISCOVERY,
                external_data_disclosure=[
                    "经过清洗的学术关键词和检索式",
                    "研究主题的通用术语；不包含本地路径、源文件或密钥",
                ],
                accepted_inputs=[
                    {
                        "action_id": "approve_public_research",
                        "project_id": str(result["project_id"]),
                    },
                    {
                        "name": "approve_public_research",
                        "type": "boolean",
                        "label": "允许此 Project 进行公开只读检索",
                        "required": True,
                    },
                ],
            )
        )
    return result


def _bundle_close(
    payload: dict[str, Any],
    _: TaskRecord,
    report_progress: Callable[..., None],
) -> dict[str, Any]:
    from .project_bundle import (
        close_project_bundle_loop,
        verify_project_bundle_completion,
    )
    from .workflow_migration import migrate_bundle_run

    run_dir = close_project_bundle_loop(
        _required_text(payload, "source"),
        output_root=_required_text(payload, "output_root"),
        name=str(payload.get("name", "")).strip() or None,
        track_id=str(payload.get("track_id", "auto")).strip() or "auto",
        discover_claims=bool(payload.get("discover_claims", False)),
        progress=report_progress,
    )
    workflow = migrate_bundle_run(
        run_dir,
        repository_root=Path(_required_text(payload, "output_root")) / ".workflow-v2",
    )
    return {
        "run_dir": str(run_dir.resolve()),
        "verification": verify_project_bundle_completion(run_dir),
        "workflow": workflow,
    }


async def _bundle_expand(payload: dict[str, Any], _: TaskRecord) -> dict[str, Any]:
    from .paper_expansion import (
        expand_project_bundle_paper,
        verify_project_bundle_paper,
    )
    from .storage import read_json
    from .workflow_domain import (
        AIReviewStatus,
        SystemReadiness,
        WorkflowRepository,
    )

    run_dir = Path(_required_text(payload, "run_dir")).resolve()
    audit = await expand_project_bundle_paper(run_dir)
    verification = verify_project_bundle_paper(run_dir)
    workflow = None
    pointer_path = run_dir / "workflow_v2.json"
    if pointer_path.is_file():
        pointer = read_json(pointer_path)
        repository = WorkflowRepository(str(pointer["repository_root"]))
        study_id = str(pointer["study_id"])
        readiness = repository.load_readiness(study_id)
        panel_paths = [
            run_dir / "stage_4_synthesis" / "paper_outline_review.json",
            run_dir / "stage_4_synthesis" / "paper_draft_review.json",
        ]
        panel_decisions = [
            str(read_json(path).get("decision", ""))
            for path in panel_paths
            if path.is_file()
        ]
        ai_status = (
            AIReviewStatus.PASSED
            if len(panel_decisions) == 2
            and all(item == "accept" for item in panel_decisions)
            else AIReviewStatus.FAILED
        )
        repository.save_readiness(
            readiness.model_copy(
                update={
                    "system_publication_readiness": (
                        SystemReadiness.CONDITIONS_MET
                        if bool(verification.get("passed"))
                        and bool(verification.get("paper_draft_ready"))
                        else SystemReadiness.NOT_READY
                    ),
                    "ai_scientific_review": ai_status,
                    "system_checks": {
                        **readiness.system_checks,
                        "expanded_paper_verified": bool(verification.get("passed")),
                        "paper_draft_ready": bool(
                            verification.get("paper_draft_ready")
                        ),
                    },
                    "blockers": [
                        str(item) for item in verification.get("violations", [])
                    ],
                }
            )
        )
        workflow = repository.snapshot(study_id)
    return {
        "run_dir": str(run_dir),
        "audit": audit.model_dump(mode="json"),
        "verification": verification,
        "workflow": workflow,
    }


def _bundle_remediate(
    payload: dict[str, Any],
    task: TaskRecord,
    report_progress: Callable[..., None],
    control_status: Callable[[], str],
) -> dict[str, Any]:
    from .remediation import execute_remediation

    return execute_remediation(payload, task, report_progress, control_status)


def built_in_operations() -> tuple[OperationDefinition, ...]:
    operations: list[OperationDefinition] = [
        OperationDefinition(
            "idea.start", _idea_start, stage=MacroStage.DISCOVERY, resumable=True
        ),
        OperationDefinition(
            "bundle.inspect",
            _bundle_inspect,
            stage=MacroStage.DISCOVERY,
            resumable=True,
        ),
        # close-loop creates an immutable run directory. Until that operation
        # has a stage-level checkpoint protocol, an interrupted attempt is
        # visible but deliberately not repeated automatically.
        OperationDefinition(
            "bundle.close", _bundle_close, stage=MacroStage.SYNTHESIS, resumable=False
        ),
        OperationDefinition(
            "bundle.expand-paper",
            _bundle_expand,
            stage=MacroStage.SYNTHESIS,
            resumable=True,
        ),
        OperationDefinition(
            "bundle.remediate",
            _bundle_remediate,
            stage=MacroStage.EXPERIMENTATION,
            resumable=True,
        ),
    ]
    from .publication_adapters import (
        default_publication_registry,
        execute_publication_action,
    )

    adapter = default_publication_registry().get(
        "research-agent-evidence-publication-v1"
    )

    def publication_handler(action: str):
        def execute(payload: dict[str, Any], _: TaskRecord) -> dict[str, Any]:
            parameters = payload.get("parameters") or {}
            if not isinstance(parameters, dict):
                raise ValueError("publication task parameters must be a JSON object")
            return execute_publication_action(
                _required_text(payload, "project"),
                action,
                parameters=parameters,
                adapter_id=str(payload.get("adapter_id", "")).strip() or None,
            )

        return execute

    for action in adapter.actions():
        operations.append(
            OperationDefinition(
                f"publication.{action.action}",
                publication_handler(action.action),
                stage=action.stage,
                resumable=action.resumable,
            )
        )
    return tuple(operations)


def create_workflow_orchestrator(root: str | Path):
    from .orchestration import TaskOrchestrator

    return TaskOrchestrator(root, built_in_operations())
