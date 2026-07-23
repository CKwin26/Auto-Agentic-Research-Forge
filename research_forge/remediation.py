from __future__ import annotations

"""Persistent evidence-remediation plans for completed project-bundle runs."""

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field

from .models import MacroStage, StrictModel, utc_now
from .orchestration import (
    RequirementKind,
    TaskPauseError,
    TaskRecord,
    TaskRequirement,
    TaskRequirementError,
)
from .storage import ensure_within, read_json, sha256_file, write_json_atomic


NON_FINAL_VERDICTS = {"mixed", "inconclusive", "unverifiable"}


class RemediationAction(StrictModel):
    action_id: str = Field(pattern=r"^action-[a-z0-9-]+$")
    title: str
    evidence_gap: str
    method: str
    expected_artifact: str
    priority: Literal["critical", "high", "medium"]
    cost: Literal["low", "medium", "high"]
    stage: MacroStage
    execution_mode: Literal["automatic", "user_input", "external"]
    contract_impact: Literal["same_version", "new_version"]
    dependencies: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    status: Literal[
        "proposed", "selected", "queued", "running", "waiting_for_user",
        "completed", "failed", "skipped",
    ] = "proposed"
    experiment_id: str | None = None
    experiment_command: list[str] = Field(default_factory=list)
    experiment_manifest_path: str | None = None
    network_access: bool = False
    execution_record_path: str | None = None
    artifact_manifest_path: str | None = None
    error: str | None = None


class RemediationPlan(StrictModel):
    schema_version: int = 1
    plan_id: str = Field(pattern=r"^remediation-[a-f0-9]{12}$")
    source_run_name: str
    source_run_path: str
    source_certificate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(ge=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    verdict_status: str
    gap_summary: str
    status: Literal[
        "draft", "awaiting_approval", "approved", "executing",
        "awaiting_user", "completed", "superseded", "archived",
    ] = "awaiting_approval"
    contract_id: str
    contract_version: int = Field(default=1, ge=1)
    contract_confirmation_required: bool = False
    contract_confirmed: bool = False
    selected_action_ids: list[str] = Field(default_factory=list)
    actions: list[RemediationAction]
    successor_run_name: str | None = None
    next_plan_id: str | None = None


def _plan_dir(remediation_root: str | Path, run_name: str) -> Path:
    root = Path(remediation_root).resolve()
    safe_name = Path(run_name).name
    if safe_name != run_name or not safe_name:
        raise ValueError("invalid remediation run name")
    return root / safe_name


def _plan_path(remediation_root: str | Path, run_name: str, version: int) -> Path:
    return _plan_dir(remediation_root, run_name) / f"plan-v{version}.json"


def _plan_files(remediation_root: str | Path, run_name: str) -> list[Path]:
    directory = _plan_dir(remediation_root, run_name)
    if not directory.is_dir():
        return []
    return sorted(
        directory.glob("plan-v*.json"),
        key=lambda path: int(re.search(r"v(\d+)$", path.stem).group(1)),  # type: ignore[union-attr]
    )


def load_remediation_plan(
    remediation_root: str | Path, run_name: str, plan_id: str | None = None
) -> RemediationPlan:
    files = _plan_files(remediation_root, run_name)
    if not files:
        raise FileNotFoundError(f"remediation plan not found: {run_name}")
    plans = [RemediationPlan.model_validate(read_json(path)) for path in files]
    if plan_id:
        for plan in plans:
            if plan.plan_id == plan_id:
                return plan
        raise FileNotFoundError(f"remediation plan not found: {plan_id}")
    return plans[-1]


def _persist(remediation_root: str | Path, plan: RemediationPlan) -> RemediationPlan:
    updated = plan.model_copy(update={"updated_at": utc_now()})
    write_json_atomic(
        _plan_path(remediation_root, updated.source_run_name, updated.version), updated
    )
    return updated


def _action(
    slug: str,
    title: str,
    gap: str,
    method: str,
    artifact: str,
    *,
    priority: Literal["critical", "high", "medium"],
    cost: Literal["low", "medium", "high"],
    stage: MacroStage,
    mode: Literal["automatic", "user_input", "external"],
    impact: Literal["same_version", "new_version"],
    inputs: list[str] | None = None,
) -> RemediationAction:
    return RemediationAction(
        action_id=f"action-{slug}",
        title=title,
        evidence_gap=gap,
        method=method,
        expected_artifact=artifact,
        priority=priority,
        cost=cost,
        stage=stage,
        execution_mode=mode,
        contract_impact=impact,
        required_inputs=inputs or [],
    )


def create_remediation_plan(
    run_dir: str | Path,
    *,
    remediation_root: str | Path,
    force_new: bool = False,
) -> RemediationPlan:
    run = Path(run_dir).resolve()
    certificate_path = run / "completion_certificate.json"
    if not certificate_path.is_file():
        raise FileNotFoundError(f"completed run not found: {run}")
    verdict = read_json(run / "stage_3_experimentation" / "idea_verdict.json")
    paper_plan = read_json(run / "stage_4_synthesis" / "paper_expansion_plan.json")
    status = str(verdict.get("status", "unverifiable"))
    if status not in NON_FINAL_VERDICTS:
        raise ValueError("clear supported/refuted verdicts do not require remediation")
    existing_files = _plan_files(remediation_root, run.name)
    if existing_files and not force_new:
        existing = RemediationPlan.model_validate(read_json(existing_files[-1]))
        if existing.status not in {"superseded", "archived"}:
            return existing
    version = len(existing_files) + 1
    actions: list[RemediationAction] = []
    bound = bool(verdict.get("protocol_bound_to_output"))
    maturity = str(verdict.get("evidence_maturity", "mixed_or_unspecified"))
    if status == "unverifiable":
        actions.append(_action(
            "freeze-protocol", "建立可执行的冻结研究协议",
            "当前材料没有能够约束实验判定的预注册协议。",
            "明确样本、基线、指标、通过门槛和禁止解释，并创建下一版契约。",
            "冻结协议和机器可读评价规范", priority="critical", cost="medium",
            stage=MacroStage.PROTOCOL, mode="user_input", impact="new_version",
            inputs=["确认后的研究协议或实验配置"],
        ))
    else:
        actions.append(_action(
            "independent-evaluation", "补充一次独立评价",
            f"当前研究判定为 {status}，尚未形成明确的支持或反驳。",
            "沿用冻结口径取得新的独立评估结果，不根据已见结果调参。",
            "独立评价输出与结论报告", priority="critical", cost="high",
            stage=MacroStage.EXPERIMENTATION, mode="user_input",
            impact="same_version" if bound else "new_version",
            inputs=["新增的机器可读实验输出", "对应的结论报告"],
        ))
    if not bound:
        actions.append(_action(
            "bind-future-output", "建立协议与未来实验输出的精确绑定",
            "现有实验输出无法证明它来自当前冻结协议。",
            "为下一轮实验保存协议哈希、实现版本和输出引用，不追认旧结果。",
            "协议锁、实现哈希和新实验输出", priority="critical", cost="medium",
            stage=MacroStage.PROTOCOL, mode="user_input", impact="new_version",
            inputs=["协议绑定配置", "下一轮实验输出"],
        ))
    if maturity != "prospective_blind":
        actions.append(_action(
            "prospective-blind", "取得前瞻盲测证据",
            "现有证据不是冻结后的前瞻盲测。",
            "在不查看结果的前提下锁定评价窗口，完成独立运行后再揭盲。",
            "前瞻盲测输出和揭盲记录", priority="high", cost="high",
            stage=MacroStage.EXPERIMENTATION, mode="user_input",
            impact="same_version" if bound else "new_version",
            inputs=["新的前瞻盲测结果"],
        ))
    if not bool(paper_plan.get("literature_gate_passed")):
        actions.append(_action(
            "literature-grounding", "补齐并冻结已核验文献",
            "文献数量、来源核验或冻结清单尚未通过。",
            "检索与研究问题直接相关的论文，核验来源后生成不可静默替换的清单。",
            "核验文献条目与冻结文献清单", priority="medium", cost="medium",
            stage=MacroStage.DISCOVERY, mode="external", impact="same_version",
            inputs=["外部文献服务授权，或本地文献来源文件"],
        ))
    actions.append(_action(
        "rescan-and-rejudge", "重新扫描项目并执行研究判定",
        "补充材料尚未进入新的可审计研究运行。",
        "只读扫描更新后的源项目，创建后继运行并重新执行四阶段研究闭环。",
        "后继运行、研究判定和完成证书", priority="high", cost="low",
        stage=MacroStage.SYNTHESIS, mode="automatic", impact="same_version",
    ))
    # A project-owned declaration turns evidence work into an executable action.
    # Invalid manifests are handled as a structured configuration requirement at
    # execution time rather than preventing the plan from being inspected.
    try:
        from .experiment_execution import load_project_experiment_manifest

        bundle_manifest = read_json(run / "bundle_manifest.json")
        source_root = str(bundle_manifest.get("source_root", "")).strip()
        experiment_manifest, experiment_manifest_path = (
            load_project_experiment_manifest(source_root) if source_root else (None, None)
        )
        if experiment_manifest:
            declarations = {
                action_id: experiment
                for experiment in experiment_manifest.experiments
                for action_id in experiment.action_ids
            }
            actions = [
                item.model_copy(update={
                    "execution_mode": "automatic",
                    "experiment_id": declarations[item.action_id].experiment_id,
                    "experiment_command": declarations[item.action_id].command,
                    "experiment_manifest_path": str(experiment_manifest_path),
                    "network_access": declarations[item.action_id].network_access,
                })
                if item.action_id in declarations else item
                for item in actions
            ]
    except (OSError, ValueError):
        pass
    digest = hashlib.sha256(
        f"{run.name}|{sha256_file(certificate_path)}|{version}".encode("utf-8")
    ).hexdigest()[:12]
    contract_id = hashlib.sha256(str(run).encode("utf-8")).hexdigest()[:12]
    plan = RemediationPlan(
        plan_id=f"remediation-{digest}",
        source_run_name=run.name,
        source_run_path=str(run),
        source_certificate_sha256=sha256_file(certificate_path),
        version=version,
        verdict_status=status,
        gap_summary="；".join(dict.fromkeys(item.evidence_gap for item in actions if item.action_id != "action-rescan-and-rejudge")),
        contract_id=f"contract-{contract_id}",
        actions=actions,
    )
    return _persist(remediation_root, plan)


def approve_remediation_plan(
    remediation_root: str | Path,
    run_name: str,
    selected_action_ids: list[str],
    *,
    confirm_contract_revision: bool = False,
) -> RemediationPlan:
    plan = load_remediation_plan(remediation_root, run_name)
    known = {item.action_id for item in plan.actions}
    selected = list(dict.fromkeys(selected_action_ids))
    if not selected or any(item not in known for item in selected):
        raise ValueError("select at least one valid remediation action")
    by_id = {item.action_id: item for item in plan.actions}
    cursor = 0
    while cursor < len(selected):
        action = by_id[selected[cursor]]
        for dependency in action.dependencies:
            if dependency not in known:
                raise ValueError(f"unknown remediation dependency: {dependency}")
            if dependency not in selected:
                selected.append(dependency)
        cursor += 1
    if "action-rescan-and-rejudge" not in selected:
        selected.append("action-rescan-and-rejudge")
    needs_revision = any(
        item.action_id in selected and item.contract_impact == "new_version"
        for item in plan.actions
    )
    actions = [
        item.model_copy(update={"status": "selected" if item.action_id in selected else "skipped"})
        for item in plan.actions
    ]
    update: dict[str, Any] = {
        "selected_action_ids": selected,
        "actions": actions,
        "contract_confirmation_required": needs_revision,
        "contract_confirmed": confirm_contract_revision if needs_revision else True,
        "status": "approved" if (not needs_revision or confirm_contract_revision) else "awaiting_approval",
    }
    if needs_revision and confirm_contract_revision:
        update["contract_version"] = plan.contract_version + 1
        write_json_atomic(
            _plan_dir(remediation_root, run_name) / f"contract-v{plan.contract_version + 1}.json",
            {
                "schema_version": 1,
                "contract_id": plan.contract_id,
                "version": plan.contract_version + 1,
                "predecessor_version": plan.contract_version,
                "confirmed_at": utc_now(),
                "selected_changes": [
                    item.model_dump(mode="json") for item in actions
                    if item.action_id in selected and item.contract_impact == "new_version"
                ],
            },
        )
    return _persist(remediation_root, plan.model_copy(update=update))


def archive_remediation_plan(remediation_root: str | Path, run_name: str) -> RemediationPlan:
    plan = load_remediation_plan(remediation_root, run_name)
    return _persist(remediation_root, plan.model_copy(update={"status": "archived"}))


def _action_resolutions(task: TaskRecord) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in task.requirements:
        if item.resolved and item.resolution and item.resolution.get("action_id"):
            action_id = str(item.resolution["action_id"])
            merged[action_id] = {**merged.get(action_id, {}), **item.resolution}
    return merged


def _ui_stage(stage: MacroStage) -> str:
    return {
        MacroStage.DISCOVERY: "discovery",
        MacroStage.PROTOCOL: "protocol",
        MacroStage.EXPERIMENTATION: "experimentation",
        MacroStage.SYNTHESIS: "synthesis",
    }.get(stage, "discovery")


def _replace_action(plan: RemediationPlan, action_id: str, **updates: Any) -> RemediationPlan:
    return plan.model_copy(update={
        "actions": [
            item.model_copy(update=updates) if item.action_id == action_id else item
            for item in plan.actions
        ]
    })


def _configuration_requirement(
    action: RemediationAction,
    *,
    reason: str,
    kind: RequirementKind = RequirementKind.CONFIGURATION,
    fields: list[dict[str, Any]] | None = None,
) -> TaskRequirementError:
    accepted = [{"action_id": action.action_id}]
    accepted.extend(fields or [])
    return TaskRequirementError(TaskRequirement.create(
        kind=kind,
        title=f"配置自动实验：{action.title}",
        reason=reason,
        stage=action.stage,
        changes_protocol=False,
        accepted_inputs=accepted,
        alternatives=[{"id": "skip", "label": "本轮跳过"}],
    ))


def _publish_experiment_artifacts(
    report: dict[str, Any],
    *,
    publication_root: Path,
    plan: RemediationPlan,
    action: RemediationAction,
    execution_dir: Path,
) -> Path:
    publish_root = (
        publication_root / plan.plan_id / action.action_id
    ).resolve()
    ensure_within(publication_root, publish_root)
    publish_root.mkdir(parents=True, exist_ok=True)
    published: list[dict[str, Any]] = []
    for item in report.get("artifacts", []):
        source = Path(str(item["path"])).resolve()
        ensure_within(execution_dir, source)
        relative = source.relative_to(execution_dir)
        target = (publish_root / relative).resolve()
        ensure_within(publish_root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        published.append({**item, "path": str(target), "sha256": sha256_file(target)})
    manifest_path = publish_root / "artifact_manifest.json"
    write_json_atomic(manifest_path, {
        "schema_version": 1,
        "created_at": utc_now(),
        "plan_id": plan.plan_id,
        "action_id": action.action_id,
        "experiment_id": report.get("experiment_id"),
        "execution_record_sha256": sha256_file(execution_dir / "execution.json"),
        "artifacts": published,
    })
    return manifest_path


def _build_remediation_snapshot(
    source_root: Path,
    *,
    plan: RemediationPlan,
    remediation_root: Path,
    attempt: int,
) -> Path:
    """Create a read-only-source overlay containing verified new evidence."""

    from .project_bundle import inventory_project_bundle

    snapshot = (
        remediation_root.parent
        / ".remediation-snapshots" / f"{plan.plan_id[-12:]}-a{attempt}" / source_root.name
    ).resolve()
    snapshot.mkdir(parents=True, exist_ok=True)
    resources, _ = inventory_project_bundle(source_root)
    for resource in resources:
        source = (source_root / resource.path).resolve()
        ensure_within(source_root, source)
        target = (snapshot / resource.path).resolve()
        ensure_within(snapshot, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    published_root = (
        remediation_root.parent / ".remediation-evidence" / plan.plan_id
    ).resolve()
    if published_root.is_dir():
        for source in sorted(path for path in published_root.rglob("*") if path.is_file()):
            relative = source.relative_to(published_root)
            target = (snapshot / ".research-forge" / "evidence" / plan.plan_id / relative).resolve()
            ensure_within(snapshot, target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    write_json_atomic(snapshot / ".research-forge" / "remediation_context.json", {
        "schema_version": 1,
        "created_at": utc_now(),
        "original_source_root": str(source_root),
        "remediation_plan_id": plan.plan_id,
        "contract_id": plan.contract_id,
        "contract_version": plan.contract_version,
    })
    return snapshot


def execute_remediation(
    payload: dict[str, Any],
    task: TaskRecord,
    report_progress: Callable[..., None],
    control_status: Callable[[], str] | None = None,
) -> dict[str, Any]:
    from .experiment_execution import (
        ExperimentPaused,
        ExperimentPreflightError,
        experiment_for_action,
        load_project_experiment_manifest,
        run_declared_experiment,
    )
    from .project_bundle import close_project_bundle_loop, verify_project_bundle_completion

    remediation_root = Path(str(payload["remediation_root"])).resolve()
    output_root = Path(str(payload["output_root"])).resolve()
    plan = load_remediation_plan(
        remediation_root, str(payload["source_run_name"]), str(payload["plan_id"])
    )
    if plan.status not in {"approved", "executing", "awaiting_user"}:
        raise ValueError("remediation plan is not approved")
    source_run = Path(plan.source_run_path).resolve()
    manifest = read_json(source_run / "bundle_manifest.json")
    source_root = str(manifest.get("source_root", "")).strip()
    source_path = Path(source_root).resolve()
    if not source_path.is_dir():
        raise FileNotFoundError(f"source project not found: {source_path}")
    track_id = str(manifest.get("selected_track_id", "auto"))
    resolutions = _action_resolutions(task)
    plan = _persist(remediation_root, plan.model_copy(update={"status": "executing"}))
    for action in plan.actions:
        if action.action_id not in plan.selected_action_ids:
            continue
        if action.status in {"completed", "skipped"}:
            continue
        report_progress(
            stage=_ui_stage(action.stage),
            title=action.title,
            detail=action.method,
            reason=action.evidence_gap,
            output=action.expected_artifact,
            next="完成后继续执行本轮补证据计划",
        )
        resolution = resolutions.get(action.action_id)
        if resolution and resolution.get("alternative") == "skip":
            plan = _persist(remediation_root, _replace_action(plan, action.action_id, status="skipped"))
            continue
        if action.action_id == "action-rescan-and-rejudge":
            continue

        explicit_manifest = str((resolution or {}).get("manifest_path", "")).strip() or None
        try:
            experiment_manifest, manifest_path = load_project_experiment_manifest(
                source_path, explicit_manifest
            )
        except (OSError, ValueError) as exc:
            plan = _persist(
                remediation_root,
                _replace_action(plan, action.action_id, status="waiting_for_user", error=str(exc)).model_copy(update={"status": "awaiting_user"}),
            )
            raise _configuration_requirement(
                action,
                reason=f"自动实验配置无法读取或格式不正确：{exc}",
                fields=[{
                    "name": "manifest_path", "type": "path", "label": "实验配置文件",
                    "placeholder": "research-forge.experiments.json", "required": True,
                }],
            )
        spec = experiment_for_action(experiment_manifest, action.action_id) if experiment_manifest else None
        if spec is None:
            plan = _persist(
                remediation_root,
                _replace_action(plan, action.action_id, status="waiting_for_user", error="missing experiment declaration").model_copy(update={"status": "awaiting_user"}),
            )
            raise _configuration_requirement(
                action,
                reason=(
                    "项目尚未声明这个动作的安全实验命令。请在项目中提供 "
                    "research-forge.experiments.json；确认只代表允许系统读取配置，"
                    "不会把任务直接标记为完成。"
                ),
                fields=[{
                    "name": "manifest_path", "type": "path", "label": "实验配置文件",
                    "placeholder": "research-forge.experiments.json", "required": True,
                }],
            )
        execution_dir = (
            remediation_root.parent / ".remediation-executions"
            / plan.plan_id / f"a{task.attempt}" / action.action_id
        ).resolve()
        plan = _persist(
            remediation_root,
            _replace_action(
                plan, action.action_id, status="running", experiment_id=spec.experiment_id,
                execution_record_path=str(execution_dir / "execution.json"), error=None,
            ),
        )
        try:
            report = run_declared_experiment(
                spec,
                source_root=source_path,
                evidence_dir=execution_dir,
                action_id=action.action_id,
                plan_id=plan.plan_id,
                network_authorized=bool((resolution or {}).get("network_authorized")),
                report_progress=report_progress,
                control_status=control_status,
            )
        except ExperimentPreflightError as exc:
            kind = {
                "environment": RequirementKind.ENVIRONMENT,
                "dataset": RequirementKind.DATASET,
                "permission": RequirementKind.PERMISSION,
                "configuration": RequirementKind.CONFIGURATION,
            }[exc.kind]
            plan = _persist(
                remediation_root,
                _replace_action(plan, action.action_id, status="waiting_for_user", error=str(exc)).model_copy(update={"status": "awaiting_user"}),
            )
            fields = []
            if exc.kind == "permission":
                fields.append({
                    "name": "network_authorized", "type": "boolean",
                    "label": "允许本实验访问网络", "required": True,
                })
            raise _configuration_requirement(
                action,
                reason=f"自动实验暂时缺少执行条件：{'、'.join(exc.missing)}",
                kind=kind,
                fields=fields,
            )
        except ExperimentPaused as exc:
            plan = _persist(
                remediation_root,
                _replace_action(plan, action.action_id, status="queued", error=str(exc)),
            )
            raise TaskPauseError() from exc
        except Exception as exc:
            _persist(
                remediation_root,
                _replace_action(plan, action.action_id, status="failed", error=str(exc)),
            )
            raise
        artifact_manifest = _publish_experiment_artifacts(
            report,
            publication_root=(
                remediation_root.parent / ".remediation-evidence"
            ),
            plan=plan,
            action=action,
            execution_dir=execution_dir,
        )
        plan = _persist(
            remediation_root,
            _replace_action(
                plan, action.action_id, status="completed",
                artifact_manifest_path=str(artifact_manifest), error=None,
            ),
        )
    report_progress(
        stage="discovery",
        title="正在创建不可覆盖原判定的后继运行",
        detail="重新扫描更新后的项目材料，并沿用已确认的研究谱系。",
        reason="原运行保持不可变，新增证据必须在新的运行中重新接受完整判定。",
        output="带前驱关系的新研究运行",
        next="重新执行研究判定",
    )
    successor_source = _build_remediation_snapshot(
        source_path,
        plan=plan,
        remediation_root=remediation_root,
        attempt=task.attempt,
    )
    successor = close_project_bundle_loop(
        successor_source,
        output_root=output_root,
        name=f"{source_run.name}-evidence-v{plan.version}",
        track_id=track_id,
        discover_claims=True,
        progress=report_progress,
    )
    from .workflow_migration import migrate_bundle_run

    workflow_snapshot = migrate_bundle_run(
        successor,
        repository_root=output_root / ".workflow-v2",
    )
    write_json_atomic(successor / "research_lineage.json", {
        "schema_version": 1,
        "created_at": utc_now(),
        "predecessor_run_id": source_run.name,
        "contract_id": plan.contract_id,
        "contract_version": plan.contract_version,
        "remediation_plan_id": plan.plan_id,
        "predecessor_certificate_sha256": plan.source_certificate_sha256,
        "original_source_root": str(source_path),
        "successor_source_snapshot": str(successor_source),
    })
    successor_verdict = read_json(successor / "stage_3_experimentation" / "idea_verdict.json")
    next_plan_id = None
    if str(successor_verdict.get("status")) in NON_FINAL_VERDICTS:
        next_plan = create_remediation_plan(
            successor, remediation_root=remediation_root, force_new=True
        )
        next_plan_id = next_plan.plan_id
    plan = _persist(remediation_root, plan.model_copy(update={
        "status": "completed",
        "actions": [
            item.model_copy(update={"status": "completed"})
            if item.action_id == "action-rescan-and-rejudge" else item
            for item in plan.actions
        ],
        "successor_run_name": successor.name,
        "next_plan_id": next_plan_id,
    }))
    return {
        "run_dir": str(successor),
        "verification": verify_project_bundle_completion(successor),
        "workflow": workflow_snapshot,
        "remediation_plan": plan.model_dump(mode="json"),
        "next_plan_id": next_plan_id,
    }


__all__ = [
    "NON_FINAL_VERDICTS",
    "RemediationAction",
    "RemediationPlan",
    "approve_remediation_plan",
    "archive_remediation_plan",
    "create_remediation_plan",
    "execute_remediation",
    "load_remediation_plan",
]
