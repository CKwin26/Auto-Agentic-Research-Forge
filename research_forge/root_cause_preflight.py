from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from .models import StrictModel, utc_now
from .storage import read_json, write_json_atomic


class ReviewTarget(StrEnum):
    EXECUTION = "execution"
    DEVELOPMENTAL_REVIEW = "developmental_review"
    PUBLICATION = "publication"


class RootCauseSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


class RootCauseFinding(StrictModel):
    code: str
    name: str
    severity: RootCauseSeverity
    origin_stage: str
    latest_prevention_stage: str
    root_cause: str
    review_symptoms: list[str] = Field(default_factory=list)
    causal_consequence: str
    permanent_rule: str
    required_action: str
    evidence: list[str] = Field(default_factory=list)
    resolved: bool = False


class RootCausePreflightReport(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    project: str
    target: ReviewTarget
    gate_passed: bool
    execution_allowed: bool
    developmental_review_allowed: bool
    publication_submission_ready: bool
    maximum_claim_tier: str
    predicted_editorial_outcome: str
    root_conclusion: str
    findings: list[RootCauseFinding]
    prevention_order: list[str]
    source_paths: list[str]


def _load_optional(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = read_json(path)
    return value if isinstance(value, dict) else None


def _review_corpus(project: Path) -> str:
    review_root = project / "synthesis" / "reviews"
    if not review_root.is_dir():
        return ""
    chunks: list[str] = []
    for path in sorted(review_root.rglob("*.md")):
        chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks).lower()


def _review_symptoms(corpus: str, patterns: dict[str, str]) -> list[str]:
    return [label for pattern, label in patterns.items() if re.search(pattern, corpus)]


def _metric_names(plan: dict[str, Any], protocol: dict[str, Any]) -> set[str]:
    names = {
        str(item.get("name", "")).lower()
        for item in plan.get("metrics", [])
        if isinstance(item, dict)
    }
    primary = protocol.get("primary_metric")
    if primary:
        names.add(str(primary).lower())
    names.update(str(item).lower() for item in protocol.get("secondary_metrics", []))
    return names


def _all_plan_text(plan: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "research_question",
        "hypothesis",
        "novelty_claim",
        "baseline_definition",
        "readiness_summary",
    ):
        values.append(str(plan.get(key, "")))
    for key in ("method_outline", "confounders", "risks", "scope_in", "scope_out"):
        values.extend(str(item) for item in plan.get(key, []))
    return "\n".join(values).lower()


def _manual_validation_complete(analysis: dict[str, Any] | None) -> bool:
    if not analysis:
        return False
    status = str(analysis.get("human_validation", "")).lower()
    return (
        status in {"complete", "completed", "validated"}
        and analysis.get("primary_analysis_interpretable") is True
    )


def analyze_root_causes(
    *,
    project_name: str,
    plan: dict[str, Any],
    protocol: dict[str, Any],
    backbone: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    review_corpus: str = "",
    target: ReviewTarget = ReviewTarget.PUBLICATION,
    source_paths: list[str] | None = None,
) -> RootCausePreflightReport:
    """Find upstream causes that manuscript editing cannot repair.

    This deliberately uses explicit protocol fields instead of an LLM judgment. The
    output is a claim ceiling and a routing decision, not a paper-quality score.
    """

    findings: list[RootCauseFinding] = []
    plan_text = _all_plan_text(plan)
    metrics = _metric_names(plan, protocol)
    evaluator = str(protocol.get("protected_evaluator", "")).lower()
    treatment_gate = str(protocol.get("treatment_gate", "")).lower()
    backbone_model = str(backbone.get("model", "")).lower()
    human_complete = _manual_validation_complete(analysis)

    generator_family = "codex" if "codex" in backbone_model else backbone_model.split(":", 1)[0]
    evaluator_family = "codex" if "codex" in evaluator else evaluator.split("_", 1)[0]
    gate_family = "codex" if "codex" in treatment_gate else treatment_gate.split("_", 1)[0]
    independent_evaluator = bool(
        re.search(r"cross[-_ ]family|independent[-_ ]model|external[-_ ]evaluator", evaluator)
    )
    same_family = (
        bool(generator_family)
        and generator_family == evaluator_family == gate_family
        and not independent_evaluator
    )
    if same_family and not human_complete:
        findings.append(
            RootCauseFinding(
                code="RC-MEASUREMENT-CIRCULARITY",
                name="测量回路同源",
                severity=RootCauseSeverity.CRITICAL,
                origin_stage="Stage 2 measurement design",
                latest_prevention_stage="before protocol freeze",
                root_cause=(
                    "生成器、干预 gate 与主结果评估器共享同一模型家族，且独立人工校准未完成。"
                    "系统因此同时参与了优化目标和目标测量。"
                ),
                review_symptoms=_review_symptoms(
                    review_corpus,
                    {
                        r"same[- ]family": "审稿人质疑 same-family evaluator",
                        r"self[- ]preference|evaluator preference": "审稿人提出评估器偏好替代解释",
                        r"independent (?:audit|calibration|validation)": "审稿人要求独立校准",
                    },
                ),
                causal_consequence=(
                    "unsupported_claim_rate 的下降最多证明内部代理指标下降，不能区分真实证据改进与"
                    "同源评估器偏好。"
                ),
                permanent_rule=(
                    "只要 generator/intervention/evaluator 同源且无独立校准，结论上限自动降为 "
                    "internal_proxy_association；禁止 publication-ready。"
                ),
                required_action=(
                    "增加跨模型家族评估器或完成冻结的双人盲审，并报告评估器混淆矩阵；否则只按内部 pilot 解释。"
                ),
                evidence=[
                    f"backbone.model={backbone_model or 'missing'}",
                    f"protocol.treatment_gate={treatment_gate or 'missing'}",
                    f"protocol.protected_evaluator={evaluator or 'missing'}",
                    f"human_validation_complete={human_complete}",
                ],
            )
        )

    cells = [item for item in protocol.get("cells", []) if isinstance(item, dict)]
    arms = [str(item.get("arm", "")).lower() for item in cells]
    first_treatment = next((index for index, arm in enumerate(arms) if arm == "treatment"), None)
    last_baseline = max((index for index, arm in enumerate(arms) if arm == "baseline"), default=-1)
    blocked_order = (
        first_treatment is not None
        and last_baseline >= 0
        and first_treatment > last_baseline
        and len(set(arms[:first_treatment])) == 1
    )
    explicit_shared_branch = bool(
        re.search(r"identical (?:upstream )?run artifacts?.{0,80}branch|branch.{0,80}identical", plan_text)
        or protocol.get("counterfactual_source") == "shared_run_artifact"
    )
    randomized_pair_order = protocol.get("branch_order") == "pair_randomized"
    if (blocked_order and not randomized_pair_order) or not explicit_shared_branch:
        findings.append(
            RootCauseFinding(
                code="RC-COUNTERFACTUAL-NONISOLATION",
                name="反事实对照未隔离",
                severity=RootCauseSeverity.CRITICAL,
                origin_stage="Stage 2 experimental design",
                latest_prevention_stage="before protocol freeze",
                root_cause=(
                    "两组由独立随机运行产生，而不是从同一上游运行产物分叉；协议还按整块顺序先执行"
                    "全部 baseline，再执行 treatment。"
                ),
                review_symptoms=_review_symptoms(
                    review_corpus,
                    {
                        r"separate stochastic": "审稿人指出独立随机运行",
                        r"fixed (?:execution )?order|baseline[- ]then[- ]gated": "审稿人指出固定整块顺序",
                        r"same run artifacts|identical run artifacts": "审稿人要求同一运行产物分叉",
                        r"task[- ]native.*differ|upstream.*differ": "审稿人观察到上游结果差异",
                    },
                ),
                causal_consequence=(
                    "组间差异混合了 gate 效果、上游随机差异和时间顺序效应；结果只能表述为观察到的组间关联，"
                    "不能识别 gate 的因果效果。"
                ),
                permanent_rule=(
                    "下游干预必须从内容寻址的同一上游 artifact 生成两条分支；若做不到，必须随机化或按 pair "
                    "交错执行，并自动禁止 causal-effect 措辞。"
                ),
                required_action=(
                    "把每个 task-seed 的实验产物只运行一次，再将同一 artifact 同时送入无 gate 与有 gate 的"
                    "结论生成分支；分支顺序随机化。"
                ),
                evidence=[
                    f"cell_arm_order={' -> '.join(arms) if arms else 'missing'}",
                    f"blocked_baseline_then_treatment={blocked_order}",
                    f"shared_artifact_branching_declared={explicit_shared_branch}",
                    f"pair_branch_order_randomized={randomized_pair_order}",
                ],
            )
        )

    construct_metrics = {
        name
        for name in metrics
        if any(token in name for token in ("semantic_change", "informativeness", "usefulness"))
    }
    if "unsupported_claim_rate" in metrics and not construct_metrics:
        findings.append(
            RootCauseFinding(
                code="RC-CONSTRUCT-UNDERCOVERAGE",
                name="指标没有覆盖完整科学构念",
                severity=RootCauseSeverity.HIGH,
                origin_stage="Stage 1 metric contract",
                latest_prevention_stage="before plan approval",
                root_cause=(
                    "主指标只测 supplied evidence 下的 unsupported 标签，没有同时测量主张是否被删除、弱化、"
                    "限定，以及输出对科学用户是否仍有信息价值。"
                ),
                review_symptoms=_review_symptoms(
                    review_corpus,
                    {
                        r"semantic contraction": "审稿人提出语义收缩替代解释",
                        r"equal claim counts": "审稿人指出相同数量不代表信息量相同",
                        r"informativeness|usefulness": "审稿人要求测量信息性或有用性",
                    },
                ),
                causal_consequence=(
                    "即使 unsupported 标签下降，也无法判断是事实纠正，还是通过把主张写得更弱来规避判错。"
                ),
                permanent_rule=(
                    "任何以“可靠性/质量改善”为目标的 gate 实验必须同时冻结支持性、语义变化类型和信息性指标；"
                    "只测支持性时，构念名称强制改为 claim-evidence fidelity proxy。"
                ),
                required_action=(
                    "增加 revision-trace 编码：删除、事实纠正、限定、语义收缩；再增加成对的信息性/有用性判断。"
                ),
                evidence=[
                    "primary_metric=unsupported_claim_rate",
                    f"construct_metrics={sorted(construct_metrics)}",
                ],
            )
        )

    if protocol.get("manual_audit") and not human_complete:
        status = str((analysis or {}).get("human_validation", "not_started"))
        findings.append(
            RootCauseFinding(
                code="RC-MATURITY-TARGET-MISMATCH",
                name="研究成熟度与审批目标不匹配",
                severity=RootCauseSeverity.CRITICAL,
                origin_stage="review routing",
                latest_prevention_stage="before external review submission",
                root_cause=(
                    "协议把独立人工审计定义为主分析解释条件，但当前流程主动延期该审计，却仍把论文送入"
                    "publication-level 审批语境。"
                ),
                review_symptoms=_review_symptoms(
                    review_corpus,
                    {
                        r"human audit.*defer|deferred human audit": "审稿人把延期人审视为发表关键缺口",
                        r"publication[- ]level": "审稿人区分工程 pilot 与发表证据",
                        r"major revision": "编辑结论为大修",
                    },
                ),
                causal_consequence=(
                    "在不改变证据的情况下，发表评审给出大修是可预测结果，而不是写作润色能够消除的意外。"
                ),
                permanent_rule=(
                    "若 primary_analysis_interpretable=false 或 publication_ready=false，外部 AI 审稿只能标记为"
                    " developmental critique；系统不得把它路由为发表审批。"
                ),
                required_action=(
                    "本阶段保留 provisional pilot 身份；等独立审计完成后再开启 publication target。"
                ),
                evidence=[
                    f"human_validation={status}",
                    "manual_audit_is_preregistered=true",
                    f"primary_analysis_interpretable={(analysis or {}).get('primary_analysis_interpretable')}",
                ],
            )
        )

    raw_pair_contract = bool(
        protocol.get("pair_level_table") is True
        or re.search(r"all (?:nine|9) (?:raw )?pairs|raw pair", plan_text)
    )
    if not raw_pair_contract:
        findings.append(
            RootCauseFinding(
                code="RC-REPORTING-CONTRACT-GAP",
                name="报告合同缺少逐配对数据",
                severity=RootCauseSeverity.MEDIUM,
                origin_stage="Stage 1 reporting contract",
                latest_prevention_stage="before plan approval",
                root_cause=(
                    "计划要求 paired comparison，但没有硬性规定在论文中列出每个 task-seed 的原始配对效应。"
                ),
                review_symptoms=_review_symptoms(
                    review_corpus,
                    {r"nine raw paired|raw paired effects|raw pairs": "审稿人要求公开九个原始配对效应"},
                ),
                causal_consequence="综合均值和 bootstrap 区间掩盖了每个 cell 以 0.25 为步长的离散性。",
                permanent_rule=(
                    "当配对单元数不超过 20 时，报告合同自动要求逐单元表、聚合结果与 leave-one-group-out 敏感性分析。"
                ),
                required_action="在协议的 reporting_requirements 中加入 pair_level_table=true。",
                evidence=[f"raw_pair_reporting_preregistered={raw_pair_contract}"],
            )
        )

    cost_metrics = {
        name
        for name in metrics
        if any(token in name for token in ("token", "model_call", "monetary", "energy", "human_labor"))
    }
    if "wall_clock_runtime_seconds" in metrics and len(cost_metrics) < 2:
        findings.append(
            RootCauseFinding(
                code="RC-TELEMETRY-SCHEMA-GAP",
                name="成本遥测在运行前没有冻结",
                severity=RootCauseSeverity.MEDIUM,
                origin_stage="Stage 1 metric contract",
                latest_prevention_stage="before execution",
                root_cause="运行只强制记录 wall-clock，没有同步记录 token、模型调用次数、货币成本和人工升级工作量。",
                review_symptoms=_review_symptoms(
                    review_corpus,
                    {r"wall-clock.*incomplete|model-call|monetary|energy|human review time": "审稿人指出成本口径不完整"},
                ),
                causal_consequence="论文阶段无法从不存在的日志恢复完整成本，只能披露缺失。",
                permanent_rule=(
                    "凡比较 agent 工作流成本，执行合同至少冻结 wall-clock、model-call count、token usage 和"
                    " escalation count；缺字段时在开跑前失败。"
                ),
                required_action="扩展 cell completion schema 并在 runner 中逐调用累计遥测。",
                evidence=[
                    "wall_clock_runtime_seconds=true",
                    f"additional_cost_metrics={sorted(cost_metrics)}",
                ],
            )
        )

    if re.search(r"canonical.*(?:omit|missing)|omits several canonical|literature.*incomplete", review_corpus):
        findings.append(
            RootCauseFinding(
                code="RC-LITERATURE-SCOPE-COUPLING",
                name="实验冻结文献与投稿定位文献被混为一层",
                severity=RootCauseSeverity.MEDIUM,
                origin_stage="Stage 1 literature architecture",
                latest_prevention_stage="before manuscript synthesis",
                root_cause=(
                    "同一份冻结短名单同时承担实验内证据边界和最终论文的领域定位，导致新近 canonical systems "
                    "与 evaluator-bias 文献没有进入投稿版 related work。"
                ),
                review_symptoms=["审稿人指出 canonical autonomous-science 与 judge-bias 文献缺失"],
                causal_consequence="冻结新颖性主张可以保持有效，但投稿版领域定位不完整。",
                permanent_rule=(
                    "分离 frozen experimental corpus 与 post-freeze contextual review；后者可更新，但不得反向改变"
                    "已冻结实验假设。"
                ),
                required_action="在 synthesis 前执行 contextual literature refresh，并显式标注 post-freeze 来源。",
                evidence=["round-1 review corpus contains canonical-literature omission"],
            )
        )

    open_critical = [
        item for item in findings if item.severity == RootCauseSeverity.CRITICAL and not item.resolved
    ]
    measurement_open = any(item.code == "RC-MEASUREMENT-CIRCULARITY" for item in findings)
    isolation_open = any(item.code == "RC-COUNTERFACTUAL-NONISOLATION" for item in findings)
    if measurement_open and isolation_open:
        maximum_claim_tier = "internal_proxy_association"
    elif measurement_open:
        maximum_claim_tier = "independence-unvalidated_proxy_effect"
    elif isolation_open:
        maximum_claim_tier = "noncausal_observed_association"
    else:
        maximum_claim_tier = "bounded_causal_effect"

    publication_ready = not open_critical
    developmental_allowed = True
    execution_allowed = True
    gate_passed = {
        ReviewTarget.EXECUTION: execution_allowed,
        ReviewTarget.DEVELOPMENTAL_REVIEW: developmental_allowed,
        ReviewTarget.PUBLICATION: publication_ready,
    }[target]
    predicted = "reviewable"
    if target == ReviewTarget.PUBLICATION and not publication_ready:
        predicted = "major_revision_or_reject"
    elif target == ReviewTarget.DEVELOPMENTAL_REVIEW and not publication_ready:
        predicted = "developmental_major_revision_expected"

    root_conclusion = (
        "本轮大修的主因不是文字质量，而是测量同源、反事实不隔离，以及尚未完成人工校准时就进入"
        "发表审批语境。稿件修订能改善透明度，但不能补造缺失的实验识别条件。"
    )
    prevention_order = [
        item.code
        for item in sorted(
            findings,
            key=lambda value: (
                {RootCauseSeverity.CRITICAL: 0, RootCauseSeverity.HIGH: 1, RootCauseSeverity.MEDIUM: 2}[
                    value.severity
                ],
                value.latest_prevention_stage,
                value.code,
            ),
        )
    ]
    return RootCausePreflightReport(
        project=project_name,
        target=target,
        gate_passed=gate_passed,
        execution_allowed=execution_allowed,
        developmental_review_allowed=developmental_allowed,
        publication_submission_ready=publication_ready,
        maximum_claim_tier=maximum_claim_tier,
        predicted_editorial_outcome=predicted,
        root_conclusion=root_conclusion,
        findings=findings,
        prevention_order=prevention_order,
        source_paths=source_paths or [],
    )


def render_root_cause_markdown(report: RootCausePreflightReport) -> str:
    decision = "通过" if report.gate_passed else "阻断"
    lines = [
        "# 研究审稿根因预检",
        "",
        f"- 项目：`{report.project}`",
        f"- 目标：`{report.target.value}`",
        f"- 闸门结论：**{decision}**",
        f"- 最大允许结论层级：`{report.maximum_claim_tier}`",
        f"- 预测编辑结论：`{report.predicted_editorial_outcome}`",
        "",
        "## 根结论",
        "",
        report.root_conclusion,
        "",
        "## 因果链",
        "",
        "设计/测量合同 → 可识别性与构念覆盖 → 可支持的主张上限 → 审稿结论",
        "",
        "## 根因清单",
        "",
        "| 编号 | 严重度 | 根因 | 最晚预防阶段 | 永久规则 |",
        "|---|---|---|---|---|",
    ]
    for item in report.findings:
        lines.append(
            "| "
            + " | ".join(
                value.replace("|", "\\|").replace("\n", " ")
                for value in (
                    item.code,
                    item.severity.value,
                    item.name,
                    item.latest_prevention_stage,
                    item.permanent_rule,
                )
            )
            + " |"
        )
    for item in report.findings:
        lines.extend(
            [
                "",
                f"### {item.code} · {item.name}",
                "",
                f"**根因：** {item.root_cause}",
                "",
                f"**后果：** {item.causal_consequence}",
                "",
                f"**系统修复：** {item.required_action}",
                "",
                "**证据：**",
                "",
            ]
        )
        lines.extend(f"- `{evidence}`" for evidence in item.evidence)
        if item.review_symptoms:
            lines.extend(["", "**对应审稿症状：**", ""])
            lines.extend(f"- {symptom}" for symptom in item.review_symptoms)
    lines.extend(
        [
            "",
            "## 路由规则",
            "",
            f"- 允许继续运行工程实验：`{str(report.execution_allowed).lower()}`",
            f"- 允许发展性外部审稿：`{str(report.developmental_review_allowed).lower()}`",
            f"- 允许标记为投稿就绪：`{str(report.publication_submission_ready).lower()}`",
            "",
        ]
    )
    return "\n".join(lines)


def analyze_project_root_causes(
    project: Path,
    *,
    target: ReviewTarget = ReviewTarget.PUBLICATION,
    persist: bool = False,
) -> RootCausePreflightReport:
    project = project.resolve()
    sources = {
        "plan": project / "research_contract.json",
        "protocol": project / "stage2" / "protocol.json",
        "backbone": project / "stage2" / "backbone_manifest.json",
        "analysis": project / "stage2" / "provisional_analysis.json",
    }
    missing = [name for name in ("plan", "protocol", "backbone") if not sources[name].is_file()]
    if missing:
        raise FileNotFoundError("root-cause preflight is missing: " + ", ".join(missing))
    report = analyze_root_causes(
        project_name=project.name,
        plan=read_json(sources["plan"]),
        protocol=read_json(sources["protocol"]),
        backbone=read_json(sources["backbone"]),
        analysis=_load_optional(sources["analysis"]),
        review_corpus=_review_corpus(project),
        target=target,
        source_paths=[
            path.relative_to(project).as_posix()
            for path in sources.values()
            if path.is_file()
        ],
    )
    if persist:
        output = project / "synthesis"
        output.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output / "root_cause_preflight.json", report)
        (output / "root_cause_preflight.md").write_text(
            render_root_cause_markdown(report),
            encoding="utf-8",
            newline="\n",
        )
    return report
