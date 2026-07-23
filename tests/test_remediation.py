from __future__ import annotations

import asyncio
import json
from pathlib import Path

from research_forge.orchestration import TaskRequest, TaskStatus
from research_forge.project_bundle import close_project_bundle_loop
from research_forge.remediation import (
    approve_remediation_plan,
    create_remediation_plan,
    load_remediation_plan,
)
from research_forge.storage import read_json, sha256_file, sha256_tree
from research_forge.workflow_tasks import create_workflow_orchestrator


def _source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "研究说明.md").write_text(
        """# 留存率研究

## 研究问题

不同提醒频率是否影响新用户次日留存率？

## 结论

现有访谈提出了候选差异，但没有冻结协议，暂时不能判断提醒是否有效。
""",
        encoding="utf-8",
    )
    (source / "observations.json").write_text(
        '{"selectedEvaluation":{"users":40,"retentionRate":0.61}}',
        encoding="utf-8",
    )
    return source


def _unverifiable_run(tmp_path: Path) -> tuple[Path, Path]:
    source = _source(tmp_path)
    run = close_project_bundle_loop(
        source, output_root=tmp_path / "runs", name="retention"
    )
    return source, run


def _write_literature_experiment(source: Path) -> None:
    (source / "experiment.py").write_text(
        """import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"papers": [{"doi": "10.1000/example", "verified": True}]}), encoding="utf-8")
print("已完成文献证据实验")
""",
        encoding="utf-8",
    )
    (source / "research-forge.experiments.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiments": [
                    {
                        "experiment_id": "literature-evidence-v1",
                        "action_ids": ["action-literature-grounding"],
                        "title": "生成并核验文献证据",
                        "command": ["{python}", "experiment.py", "{evidence_dir}/literature.json"],
                        "cwd": ".",
                        "artifacts": [
                            {
                                "path": "literature.json",
                                "format": "json",
                                "required_keys": ["papers"],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_remediation_plan_requires_contract_confirmation_for_design_changes(
    tmp_path: Path,
) -> None:
    source, run = _unverifiable_run(tmp_path)
    root = tmp_path / "runs" / ".remediation"
    original_hash = sha256_file(run / "completion_certificate.json")

    plan = create_remediation_plan(run, remediation_root=root)
    first = approve_remediation_plan(
        root, run.name, ["action-freeze-protocol"]
    )

    assert plan.verdict_status == "unverifiable"
    assert first.contract_confirmation_required
    assert not first.contract_confirmed
    assert first.status == "awaiting_approval"

    approved = approve_remediation_plan(
        root,
        run.name,
        first.selected_action_ids,
        confirm_contract_revision=True,
    )

    assert approved.status == "approved"
    assert approved.contract_confirmed
    assert approved.contract_version == 2
    assert (root / run.name / "contract-v2.json").is_file()
    assert sha256_file(run / "completion_certificate.json") == original_hash


def test_remediation_task_waits_for_input_then_creates_successor_run(
    tmp_path: Path,
) -> None:
    source, run = _unverifiable_run(tmp_path)
    runs_root = tmp_path / "runs"
    remediation_root = runs_root / ".remediation"
    plan = create_remediation_plan(run, remediation_root=remediation_root)
    approved = approve_remediation_plan(
        remediation_root,
        run.name,
        ["action-literature-grounding", "action-rescan-and-rejudge"],
    )
    orchestrator = create_workflow_orchestrator(tmp_path / "tasks")
    task = orchestrator.submit(TaskRequest(
        operation="bundle.remediate",
        payload={
            "source_run_name": run.name,
            "plan_id": approved.plan_id,
            "remediation_root": str(remediation_root),
            "output_root": str(runs_root),
        },
    ))

    waiting = asyncio.run(orchestrator.run(task.task_id))
    assert waiting.status is TaskStatus.WAITING_FOR_USER
    requirement = waiting.requirements[0]
    assert requirement.accepted_inputs[0]["action_id"] == "action-literature-grounding"
    persisted_waiting = load_remediation_plan(remediation_root, run.name, approved.plan_id)
    waiting_action = next(
        item for item in persisted_waiting.actions
        if item.action_id == "action-literature-grounding"
    )
    assert waiting_action.status == "waiting_for_user"
    assert waiting_action.artifact_manifest_path is None

    _write_literature_experiment(source)
    source_hash_before_execution = sha256_tree(source)

    paused = orchestrator.resolve_requirement(
        waiting.task_id,
        requirement.requirement_id,
        {
            "action_id": "action-literature-grounding",
            "configured": True,
            "manifest_path": "research-forge.experiments.json",
        },
    )
    assert paused.status is TaskStatus.PAUSED
    completed = asyncio.run(orchestrator.run(task.task_id, resume=True))

    assert completed.status is TaskStatus.SUCCEEDED
    successor = Path(str(completed.result["run_dir"]))
    assert successor != run
    assert successor.is_dir()
    lineage = read_json(successor / "research_lineage.json")
    assert lineage["predecessor_run_id"] == run.name
    assert lineage["remediation_plan_id"] == approved.plan_id
    persisted = load_remediation_plan(remediation_root, run.name, approved.plan_id)
    assert persisted.status == "completed"
    assert persisted.successor_run_name == successor.name
    assert persisted.next_plan_id
    action = next(
        item for item in persisted.actions
        if item.action_id == "action-literature-grounding"
    )
    assert action.status == "completed"
    assert action.experiment_id == "literature-evidence-v1"
    assert action.execution_record_path
    execution = read_json(Path(action.execution_record_path))
    assert execution["status"] == "completed"
    assert execution["exit_code"] == 0
    assert action.artifact_manifest_path
    published = read_json(Path(action.artifact_manifest_path))
    assert published["artifacts"][0]["sha256"]
    assert Path(published["artifacts"][0]["path"]).is_file()
    assert not (source / ".research-forge" / "evidence").exists()
    assert sha256_tree(source) == source_hash_before_execution
    assert Path(lineage["successor_source_snapshot"]).is_dir()
    assert lineage["original_source_root"] == str(source.resolve())


def test_declared_experiment_is_automatic_and_artifact_failure_blocks_completion(
    tmp_path: Path,
) -> None:
    source, run = _unverifiable_run(tmp_path)
    _write_literature_experiment(source)
    manifest = json.loads((source / "research-forge.experiments.json").read_text(encoding="utf-8"))
    manifest["experiments"][0]["artifacts"][0]["required_keys"] = ["missing_key"]
    (source / "research-forge.experiments.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    runs_root = tmp_path / "runs"
    remediation_root = runs_root / ".remediation"
    plan = create_remediation_plan(run, remediation_root=remediation_root)
    action = next(item for item in plan.actions if item.action_id == "action-literature-grounding")
    assert action.execution_mode == "automatic"
    assert action.experiment_id == "literature-evidence-v1"
    assert action.experiment_command[0] == "{python}"
    approved = approve_remediation_plan(
        remediation_root, run.name, ["action-literature-grounding"]
    )
    orchestrator = create_workflow_orchestrator(tmp_path / "tasks")
    task = orchestrator.submit(TaskRequest(
        operation="bundle.remediate",
        payload={
            "source_run_name": run.name,
            "plan_id": approved.plan_id,
            "remediation_root": str(remediation_root),
            "output_root": str(runs_root),
        },
    ))

    failed = asyncio.run(orchestrator.run(task.task_id))

    assert failed.status is TaskStatus.FAILED
    persisted = load_remediation_plan(remediation_root, run.name, approved.plan_id)
    failed_action = next(
        item for item in persisted.actions
        if item.action_id == "action-literature-grounding"
    )
    assert failed_action.status == "failed"
    assert failed_action.artifact_manifest_path is None
    assert "missing_key" in (failed_action.error or "")
