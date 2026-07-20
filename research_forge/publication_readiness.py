from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .journal_recommendation import (
    JournalRecommendation,
    ManuscriptAssessment,
    ProbabilityInterval,
    load_journal_registry,
    recommend_venues,
)
from .models import StrictModel
from .storage import append_jsonl, read_json, sha256_file, write_json_atomic


DEFAULT_READINESS_THRESHOLD = 0.60
CONTRACT_FILENAME = "publication_target_contract.json"
REPORT_FILENAME = "publication_readiness.json"
MARKDOWN_FILENAME = "publication_readiness.md"
DESIGN_REPAIR_FILENAME = "publication_design_repair.json"
DESIGN_REPAIR_MARKDOWN_FILENAME = "publication_design_repair.md"
FAILURE_LEDGER_FILENAME = "publication_failure_ledger.jsonl"
EXPERIMENT_TARGET_FILENAME = "publication_experiment_contract.json"
EXPERIMENT_GATE_FILENAME = "publication_experiment_gate.json"
_EXTERNAL_ONLY_BLOCKER_CODES = {"EXTERNAL-PUBLIC-SUPPLEMENT-PENDING"}


class ReadinessCriterion(StrictModel):
    id: str
    label: str
    weight: float = Field(gt=0, le=1)
    required_score: float = Field(ge=0, le=1)
    hard_minimum: float = Field(ge=0, le=1)
    owner_stage: Literal["stage_1_discovery", "stage_2_protocol", "stage_3_experimentation", "stage_4_synthesis"]
    evidence_contract: str

    @model_validator(mode="after")
    def validate_thresholds(self) -> "ReadinessCriterion":
        if self.hard_minimum > self.required_score:
            raise ValueError(f"hard minimum exceeds required score: {self.id}")
        return self


class PublicationTargetContract(StrictModel):
    schema_version: int = 1
    contract_id: str
    created_at: str
    project_path: str
    baseline_manuscript_path: str
    baseline_manuscript_sha256: str
    venue_id: str
    venue_name: str
    venue_type: Literal["journal", "conference"]
    track: str
    official_venue_url: str
    scope_url: str
    registry_id: str
    registry_source_checked_at: str
    registry_sha256: str
    readiness_threshold: float = Field(ge=0.50, le=0.95)
    threshold_semantics: str
    acceptance_probability_is_not_target: bool
    criteria: list[ReadinessCriterion]

    @model_validator(mode="after")
    def validate_contract(self) -> "PublicationTargetContract":
        if abs(sum(item.weight for item in self.criteria) - 1.0) > 0.001:
            raise ValueError("publication-readiness criterion weights must sum to one")
        if not self.acceptance_probability_is_not_target:
            raise ValueError("the readiness contract cannot target acceptance probability")
        if len({item.id for item in self.criteria}) != len(self.criteria):
            raise ValueError("publication-readiness criterion ids must be unique")
        return self


class PublicationExperimentContract(StrictModel):
    schema_version: int = 1
    contract_id: str
    created_at: str
    project_path: str
    venue_id: str
    venue_name: str
    venue_type: Literal["journal", "conference"]
    track: str
    registry_id: str
    registry_source_checked_at: str
    registry_sha256: str
    recommendation_report_path: str
    recommendation_report_sha256: str
    recommendation_rank: int = Field(ge=1)
    recommendation_scope_fit: float = Field(ge=0, le=1)
    recommendation_quality_score: float = Field(ge=0, le=1)
    recommendation_routing_label: str
    venue_quality_bar: float = Field(ge=0, le=1)
    venue_quality_weights: dict[str, float]
    final_readiness_threshold: float = Field(ge=0.50, le=0.95)
    minimum_tasks: int = Field(ge=1)
    minimum_seeds_per_task: int = Field(ge=1)
    required_construct_metrics: list[str]
    blocking_root_cause_codes: list[str]
    require_shared_artifact_counterfactual: bool
    require_independent_calibration_contract: bool
    require_contextual_novelty_refresh: bool
    venue_target_locked: bool
    target_switch_policy: str

    @model_validator(mode="after")
    def validate_publication_experiment_contract(self) -> "PublicationExperimentContract":
        if abs(sum(self.venue_quality_weights.values()) - 1.0) > 0.001:
            raise ValueError("venue quality weights must sum to one")
        if not self.venue_target_locked:
            raise ValueError("publication experiment venue target must be locked")
        if not (
            self.require_shared_artifact_counterfactual
            and self.require_independent_calibration_contract
            and self.require_contextual_novelty_refresh
        ):
            raise ValueError("publication experiment cannot disable scientific hard gates")
        return self


class PublicationExperimentGateReport(StrictModel):
    schema_version: int = 1
    generated_at: str
    contract_id: str
    protocol_id: str
    intent: Literal["publication"] = "publication"
    venue_id: str
    target_locked: bool
    task_count: int
    seed_count: int
    open_root_cause_codes: list[str]
    construct_metrics: list[str]
    novelty_refresh_present: bool
    pre_experiment_gate_passed: bool
    violations: list[str]
    final_retest_requirements: list[str]
    source_paths: list[str]


class ReadinessDimension(StrictModel):
    id: str
    label: str
    score: float = Field(ge=0, le=1)
    weight: float = Field(gt=0, le=1)
    weighted_score: float = Field(ge=0, le=1)
    required_score: float = Field(ge=0, le=1)
    hard_minimum: float = Field(ge=0, le=1)
    status: Literal["meets_target", "below_target", "hard_fail"]
    owner_stage: str
    evidence: list[str]


class ReadinessBlocker(StrictModel):
    code: str
    severity: Literal["critical", "high"]
    origin_stage: str
    latest_prevention_stage: str
    reason: str
    required_evidence: str
    source: str


class ReadinessAction(StrictModel):
    id: str
    priority: int = Field(ge=1)
    title: str
    return_to_stage: str
    execution_stage: str
    resolves: list[str]
    required_artifact: str
    projected_dimension_floors: dict[str, float]
    projected_readiness_after_action: float = Field(ge=0, le=1)


class StageBackpropagation(StrictModel):
    stage: str
    gate: str
    required_changes: list[str]
    completion_evidence: list[str]


class SystemDesignRepair(StrictModel):
    repair_id: str
    source_codes: list[str]
    root_cause: str
    earliest_repair_stage: str
    system_gap: str
    required_rule_change: str
    enforcement_stage: str
    enforcement_points: list[str]
    regression_tests: list[str]
    applicability: str
    status: Literal["active_for_future_runs", "deferred_external_validation"]


class SystemDesignRepairReport(StrictModel):
    schema_version: int = 1
    generated_at: str
    contract_id: str
    manuscript_sha256: str
    failure_fingerprint: str
    scientific_gate_failed: bool
    historical_artifacts_immutable: bool = True
    repair_principle: str
    repeated_failure_policy: str
    repairs: list[SystemDesignRepair]


class PublicationReadinessReport(StrictModel):
    schema_version: int = 1
    generated_at: str
    method_id: str = "research-forge-publication-readiness-v1"
    contract_id: str
    contract_path: str
    manuscript_path: str
    manuscript_sha256: str
    venue_id: str
    venue_name: str
    venue_type: Literal["journal", "conference"]
    readiness_score: float = Field(ge=0, le=1)
    readiness_threshold: float = Field(ge=0.50, le=0.95)
    score_threshold_passed: bool
    automated_hard_gate_passed: bool
    automated_publication_gate_passed: bool
    human_gate_pending: bool
    automated_hard_blockers: list[ReadinessBlocker]
    external_blockers: list[ReadinessBlocker]
    hard_gate_passed: bool
    publication_submission_ready: bool
    readiness_interpretation: str
    acceptance_probability_interpretation: str
    estimated_acceptance_probability: ProbabilityInterval
    current_cycle_acceptance_probability: ProbabilityInterval
    projected_acceptance_after_known_blockers: ProbabilityInterval
    acceptance_calibration_status: str
    dimensions: list[ReadinessDimension]
    hard_blockers: list[ReadinessBlocker]
    failure_fingerprint: str | None
    system_design_repair_required: bool
    system_design_repairs: list[SystemDesignRepair]
    actions: list[ReadinessAction]
    stage_backpropagation: list[StageBackpropagation]
    projected_readiness_after_plan: float = Field(ge=0, le=1)
    projection_assumptions: list[str]
    evidence_sources: list[str]


_CRITERIA = [
    ReadinessCriterion(
        id="venue_scope_fit",
        label="目标 venue 范围匹配",
        weight=0.10,
        required_score=0.75,
        hard_minimum=0.60,
        owner_stage="stage_1_discovery",
        evidence_contract="冻结目标 venue、track、官方 scope URL 与匹配理由。",
    ),
    ReadinessCriterion(
        id="scientific_identification",
        label="方法与因果识别",
        weight=0.16,
        required_score=0.72,
        hard_minimum=0.55,
        owner_stage="stage_2_protocol",
        evidence_contract="协议必须隔离目标干预，冻结分支点、随机化、分析单位与停止规则。",
    ),
    ReadinessCriterion(
        id="independent_validation",
        label="独立测量与校准",
        weight=0.15,
        required_score=0.75,
        hard_minimum=0.60,
        owner_stage="stage_2_protocol",
        evidence_contract="主评估器须有跨家族或盲态人工金标准校准，并报告混淆矩阵。",
    ),
    ReadinessCriterion(
        id="evidence_breadth",
        label="证据广度与稳定性",
        weight=0.11,
        required_score=0.70,
        hard_minimum=0.55,
        owner_stage="stage_3_experimentation",
        evidence_contract="至少覆盖冻结计划要求的异质任务、重复种子、原子主张与敏感性分析。",
    ),
    ReadinessCriterion(
        id="construct_coverage",
        label="科学构念覆盖",
        weight=0.10,
        required_score=0.72,
        hard_minimum=0.55,
        owner_stage="stage_2_protocol",
        evidence_contract="同时测量支持性、保留/删除、语义变化与科学信息性，禁止单指标偷换构念。",
    ),
    ReadinessCriterion(
        id="novelty_positioning",
        label="新颖性与领域定位",
        weight=0.11,
        required_score=0.65,
        hard_minimum=0.50,
        owner_stage="stage_1_discovery",
        evidence_contract="冻结实验文献边界，并在投稿前独立刷新领域定位与负面新颖性证据。",
    ),
    ReadinessCriterion(
        id="reproducibility_release",
        label="复现与外部材料",
        weight=0.09,
        required_score=0.78,
        hard_minimum=0.60,
        owner_stage="stage_3_experimentation",
        evidence_contract="协议、代码、数据、环境、原始结果和匿名公共材料必须以版本与哈希绑定。",
    ),
    ReadinessCriterion(
        id="reporting_integrity",
        label="报告完整性与诚信",
        weight=0.08,
        required_score=0.85,
        hard_minimum=0.75,
        owner_stage="stage_4_synthesis",
        evidence_contract="深度、逐单元结果、偏差、限制、引用和 claim-evidence 审计全部通过。",
    ),
    ReadinessCriterion(
        id="venue_compliance",
        label="投稿格式与周期合规",
        weight=0.10,
        required_score=0.82,
        hard_minimum=0.55,
        owner_stage="stage_4_synthesis",
        evidence_contract="目标模板、页数/字数、匿名化、清单、材料链接和开放周期全部核验。",
    ),
]


_ROOT_STAGE_MAP: dict[str, tuple[str, str]] = {
    "RC-MEASUREMENT-CIRCULARITY": ("stage_2_protocol", "before_protocol_freeze"),
    "RC-COUNTERFACTUAL-NONISOLATION": ("stage_2_protocol", "before_protocol_freeze"),
    "RC-CONSTRUCT-UNDERCOVERAGE": ("stage_1_discovery", "before_plan_approval"),
    "RC-MATURITY-TARGET-MISMATCH": ("stage_4_synthesis", "before_external_review_submission"),
    "RC-REPORTING-CONTRACT-GAP": ("stage_2_protocol", "before_plan_approval"),
    "RC-TELEMETRY-SCHEMA-GAP": ("stage_2_protocol", "before_execution"),
    "RC-INTERVENTION-SPECIFICATION-GAP": ("stage_2_protocol", "before_protocol_freeze"),
    "RC-SECONDARY-EVALUATOR-ROBUSTNESS-GAP": ("stage_2_protocol", "before_protocol_freeze"),
    "RC-CONSTRUCT-ANALYSIS-GAP": ("stage_2_protocol", "before_protocol_freeze"),
    "RC-TELEMETRY-SEMANTICS-GAP": ("stage_2_protocol", "before_execution"),
    "RC-LITERATURE-SCOPE-COUPLING": ("stage_1_discovery", "before_manuscript_synthesis"),
}


_DESIGN_REPAIR_ALIASES = {
    "READINESS-SCIENTIFIC-IDENTIFICATION-BELOW-HARD-MINIMUM": "RC-COUNTERFACTUAL-NONISOLATION",
    "READINESS-INDEPENDENT-VALIDATION-BELOW-HARD-MINIMUM": "RC-MEASUREMENT-CIRCULARITY",
    "READINESS-CONSTRUCT-COVERAGE-BELOW-HARD-MINIMUM": "RC-CONSTRUCT-UNDERCOVERAGE",
}


_SYSTEM_REPAIR_RULES: dict[str, dict[str, Any]] = {
    "RC-MEASUREMENT-CIRCULARITY": {
        "stage": "stage_2_protocol",
        "gap": "协议冻结允许生成器、干预器和主评估器同源，却没有独立校准合同。",
        "rule": "publication 目标的协议只要仍存在同源测量且无冻结校准，就必须在执行前失败。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["root_cause_preflight.analyze_root_causes", "study.freeze_stage2_protocol"],
        "tests": ["同源且无校准必须阻断下一版 Stage 2 freeze", "跨家族评估器也必须提供校准证据而非仅换模型名"],
    },
    "RC-COUNTERFACTUAL-NONISOLATION": {
        "stage": "stage_2_protocol",
        "gap": "协议把独立随机运行误当成配对反事实，没有冻结同一上游 artifact 的分支点与顺序随机化。",
        "rule": "publication 目标的下游干预比较必须声明内容寻址的共享 artifact 分支；否则冻结失败。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["study_models.Stage2Protocol", "root_cause_preflight.analyze_root_causes", "study.freeze_stage2_protocol"],
        "tests": ["baseline-then-treatment 整块顺序必须失败", "共享 artifact 加随机分支顺序必须通过识别检查"],
    },
    "RC-CONSTRUCT-UNDERCOVERAGE": {
        "stage": "stage_1_discovery",
        "gap": "计划批准时只冻结支持性代理指标，没有冻结信息性、删除/保留和语义变化。",
        "rule": "质量/可靠性研究若缺少支持性与信息性双轨构念，计划或协议不得进入 publication 路线。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["ResearchPlanDraft.metrics", "root_cause_preflight.analyze_root_causes", "study.freeze_stage2_protocol"],
        "tests": ["仅 unsupported_claim_rate 必须失败", "加入 semantic_change 与 informativeness 指标后构念检查通过"],
    },
    "RC-MATURITY-TARGET-MISMATCH": {
        "stage": "stage_4_synthesis",
        "gap": "流程完成状态曾可能被误解为 publication 审批资格。",
        "rule": "primary_analysis_interpretable=false 时只能进入 developmental review；投稿证书必须拒发。",
        "enforcement_stage": "stage_4_synthesis",
        "points": ["root_cause_preflight", "publication_readiness", "review routing"],
        "tests": ["延期人审时 publication_submission_ready 必须为 false", "persona panel 不得改变人工验证字段"],
    },
    "READINESS-EVIDENCE-BREADTH-BELOW-HARD-MINIMUM": {
        "stage": "stage_2_protocol",
        "gap": "计划允许少量任务和种子完成 pipeline，却没有按投稿目标反推证据广度下限。",
        "rule": "publication 修复版协议在冻结前至少声明 8 个异质任务和每任务 5 个种子，或记录有依据的等效功效方案。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["publication target contract", "study.freeze_stage2_protocol"],
        "tests": ["3 tasks x 3 seeds 在激活修复单后必须失败", "8 tasks x 5 seeds 应满足默认广度合同"],
    },
    "READINESS-NOVELTY-POSITIONING-BELOW-HARD-MINIMUM": {
        "stage": "stage_1_discovery",
        "gap": "冻结实验文献包曾同时承担投稿定位，缺少投稿前独立 contextual refresh。",
        "rule": "进入修复版 Stage 2 前必须存在独立的 novelty_refresh.json，绑定检索式、筛选账本和贡献矩阵。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["Stage 1 literature refresh", "study.freeze_stage2_protocol"],
        "tests": ["激活 novelty 修复且缺 refresh artifact 时冻结失败", "有效 refresh artifact 必须绑定来源与生成时间"],
    },
    "RC-INTERVENTION-SPECIFICATION-GAP": {
        "stage": "stage_2_protocol",
        "gap": "Evidence gate 只有名称，没有可复现的决策算法和 trace 合同。",
        "rule": "正式协议必须 hash 绑定提取、匹配、决策、允许动作和 trace schema。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["study_models.Stage2Protocol", "study.freeze_stage2_protocol", "publication_readiness.audit_publication_experiment_design"],
        "tests": ["缺 evidence_gate_specification 的正式协议必须失败", "完整规格必须进入协议哈希与实验门"],
    },
    "RC-SECONDARY-EVALUATOR-ROBUSTNESS-GAP": {
        "stage": "stage_2_protocol",
        "gap": "主评估器的校准被误当成第二测量工具，缺少独立稳健性比较。",
        "rule": "正式协议必须绑定与主评估器不同的第二 evaluator contract 及其分析方案。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["study._validate_secondary_evaluator_contract", "study.freeze_stage2_protocol", "publication_readiness.audit_publication_experiment_design"],
        "tests": ["缺 secondary evaluator contract 必须在冻结前失败", "同名主评估器不得伪装成 secondary evaluator"],
    },
    "RC-CONSTRUCT-ANALYSIS-GAP": {
        "stage": "stage_2_protocol",
        "gap": "构念保护字段只被记录，未被预注册为必须报告的分析。",
        "rule": "正式协议必须要求保留删除、语义变化、信息性和逐任务效应的结构化分析。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["study_models.Stage2Protocol", "publication_readiness.audit_publication_experiment_design"],
        "tests": ["缺任一构念分析要求时正式协议必须失败", "四项分析要求完整时门控允许继续"],
    },
    "RC-TELEMETRY-SEMANTICS-GAP": {
        "stage": "stage_2_protocol",
        "gap": "wall-clock 混入队列或恢复等待，不能解释为 controller 的执行成本。",
        "rule": "正式协议须冻结 active-attempt 为主 wall-clock，并把队列或端到端时间分开。",
        "enforcement_stage": "stage_2_protocol",
        "points": ["study_models.Stage2Protocol", "study_runner cell completion", "publication_readiness.audit_publication_experiment_design"],
        "tests": ["created-to-complete 口径不得通过正式协议", "active-attempt 口径必须写入冻结 telemetry contract"],
    },
}


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _round(value: float) -> float:
    return round(_clamp(value), 4)


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_contract_path(project: Path) -> Path:
    return project / "synthesis" / CONTRACT_FILENAME


def _dimension_scores(
    assessment: ManuscriptAssessment,
    recommendation: JournalRecommendation,
    manuscript_text: str,
) -> dict[str, tuple[float, list[str]]]:
    blocker_codes = {item.code for item in assessment.blockers}
    cross_family_proxy = any(
        token in manuscript_text.lower()
        for token in ("cross-family", "cross family", "deberta", "跨模型家族", "跨家族")
    )
    if assessment.human_validation_complete and assessment.primary_analysis_interpretable:
        independent = 1.0
        independent_evidence = ["冻结的人审/独立校准已经完成，主分析可解释。"]
    elif assessment.independent_calibration_passed:
        # A frozen cross-family public-gold calibration supports the automated
        # measurement gate. It never upgrades the separate human-submission
        # gate, which is represented by primary_analysis_interpretable.
        independent = 0.80
        independent_evidence = [
            "冻结跨家族公共金标准校准已通过；"
            f"macro-F1={assessment.independent_calibration_macro_f1:.4f}，"
            f"coverage={assessment.independent_calibration_coverage:.4f}。"
        ]
    elif cross_family_proxy:
        independent = 0.35
        independent_evidence = ["检测到跨家族代理评估，但没有完成金标准校准；代理不能替代独立验证。"]
    else:
        independent = 0.15
        independent_evidence = ["未检测到完成的独立测量或校准。"]
    if "RC-MEASUREMENT-CIRCULARITY" not in blocker_codes and cross_family_proxy:
        independent = max(independent, 0.55)

    breadth = (
        0.20
        + min(assessment.task_count / 8, 1.0) * 0.30
        + min(assessment.seed_count / 5, 1.0) * 0.20
        + min(assessment.claim_count / 20, 1.0) * 0.15
        + (0.15 if assessment.human_validation_complete else 0.0)
    )
    construct = 0.30 if "RC-CONSTRUCT-UNDERCOVERAGE" in blocker_codes else max(0.62, assessment.evidence_score)
    reproducibility = assessment.reproducibility_score
    if not assessment.external_public_repository:
        reproducibility = min(reproducibility, 0.68)
    if recommendation.venue_type == "conference" and not recommendation.current_cycle_eligible:
        compliance = 0.10
    elif recommendation.venue_type == "conference":
        compliance = 0.52
    else:
        compliance = 0.60
    if assessment.external_public_repository:
        compliance += 0.08

    return {
        "venue_scope_fit": (
            recommendation.scope_fit,
            [f"严格白名单 scope-fit={recommendation.scope_fit:.4f}；匹配词：{', '.join(recommendation.matched_scope_terms) or '无'}。"],
        ),
        "scientific_identification": (
            assessment.method_rigor_score,
            [f"方法严谨度={assessment.method_rigor_score:.4f}；项目根因阻塞数={len(assessment.blockers)}。"],
        ),
        "independent_validation": (independent, independent_evidence),
        "evidence_breadth": (
            breadth,
            [f"任务={assessment.task_count}；每任务种子={assessment.seed_count}；结构化主张={assessment.claim_count}。"],
        ),
        "construct_coverage": (
            construct,
            ["构念覆盖根因仍开放。" if "RC-CONSTRUCT-UNDERCOVERAGE" in blocker_codes else "未检测到开放的构念覆盖根因。"],
        ),
        "novelty_positioning": (
            assessment.novelty_score,
            [f"当前最高可辩护结论层级={assessment.maximum_claim_tier}；新颖性/定位得分={assessment.novelty_score:.4f}。"],
        ),
        "reproducibility_release": (
            reproducibility,
            [
                f"内部复现得分={assessment.reproducibility_score:.4f}；"
                f"外部公共仓库={'已检测' if assessment.external_public_repository else '未检测'}。"
            ],
        ),
        "reporting_integrity": (
            assessment.reporting_score if assessment.depth_gate_passed else min(assessment.reporting_score, 0.65),
            [
                f"文字深度门={'通过' if assessment.depth_gate_passed else '失败'}；"
                f"叙事计数={assessment.total_count}；参考文献={assessment.reference_count}。"
            ],
        ),
        "venue_compliance": (
            compliance,
            [
                f"venue 类型={recommendation.venue_type}；周期={recommendation.cycle_status}；"
                f"当前可投={'是' if recommendation.current_cycle_eligible else '否'}；仍需目标模板逐项核验。"
            ],
        ),
    }


def _build_dimensions(
    contract: PublicationTargetContract,
    assessment: ManuscriptAssessment,
    recommendation: JournalRecommendation,
    manuscript_text: str,
) -> list[ReadinessDimension]:
    scores = _dimension_scores(assessment, recommendation, manuscript_text)
    dimensions: list[ReadinessDimension] = []
    for criterion in contract.criteria:
        raw_score, evidence = scores[criterion.id]
        score = _round(raw_score)
        if score < criterion.hard_minimum:
            status = "hard_fail"
        elif score < criterion.required_score:
            status = "below_target"
        else:
            status = "meets_target"
        dimensions.append(
            ReadinessDimension(
                id=criterion.id,
                label=criterion.label,
                score=score,
                weight=criterion.weight,
                weighted_score=_round(score * criterion.weight),
                required_score=criterion.required_score,
                hard_minimum=criterion.hard_minimum,
                status=status,
                owner_stage=criterion.owner_stage,
                evidence=evidence,
            )
        )
    return dimensions


def _score_with_floors(dimensions: list[ReadinessDimension], floors: dict[str, float]) -> float:
    return _round(
        sum(max(item.score, floors.get(item.id, 0.0)) * item.weight for item in dimensions)
    )


def _actions(dimensions: list[ReadinessDimension], assessment: ManuscriptAssessment) -> list[ReadinessAction]:
    available = [
        (
            "PUB-A1-INDEPENDENT-CALIBRATION",
            "完成独立盲态校准",
            "stage_2_protocol",
            "stage_3_experimentation",
            ["RC-MEASUREMENT-CIRCULARITY", "RC-MATURITY-TARGET-MISMATCH"],
            "冻结样本、两名独立审阅者或独立金标准、混淆矩阵、阈值判定与解盲记录。",
            {"independent_validation": 0.80, "evidence_breadth": 0.70},
        ),
        (
            "PUB-A2-PROSPECTIVE-SAME-ARTIFACT",
            "运行前瞻性同产物随机分支实验",
            "stage_2_protocol",
            "stage_3_experimentation",
            ["RC-COUNTERFACTUAL-NONISOLATION"],
            "预注册协议、内容寻址分支点、随机顺序、运行清单和冻结分析。",
            {"scientific_identification": 0.78, "evidence_breadth": 0.70},
        ),
        (
            "PUB-A3-CONSTRUCT-COVERAGE",
            "补齐信息性与语义变化构念",
            "stage_1_discovery",
            "stage_3_experimentation",
            ["RC-CONSTRUCT-UNDERCOVERAGE"],
            "支持性、删除/保留、语义收缩、事实纠正和科学信息性的冻结双轨量表。",
            {"construct_coverage": 0.75},
        ),
        (
            "PUB-A4-HETEROGENEOUS-BREADTH",
            "扩展异质任务与稳定性分析",
            "stage_2_protocol",
            "stage_3_experimentation",
            [],
            "至少 8 个异质任务、每任务至少 5 个冻结种子、逐任务效应与 leave-one-domain-out 分析。",
            {"evidence_breadth": 0.78, "scientific_identification": 0.72},
        ),
        (
            "PUB-A5-NOVELTY-REFRESH",
            "刷新领域定位和负面新颖性检索",
            "stage_1_discovery",
            "stage_4_synthesis",
            ["RC-LITERATURE-SCOPE-COUPLING"],
            "可复跑检索式、筛选账本、canonical systems 对照和逐贡献 novelty matrix。",
            {"novelty_positioning": 0.68},
        ),
        (
            "PUB-A6-PUBLIC-ARTIFACT",
            "发布匿名可访问的复现材料",
            "stage_3_experimentation",
            "stage_4_synthesis",
            ["RC-TELEMETRY-SCHEMA-GAP"],
            "匿名仓库/归档 DOI、版本标签、环境锁、原始结果、完整成本遥测与哈希绑定。",
            {"reproducibility_release": 0.86},
        ),
        (
            "PUB-A7-TARGET-PACKAGE",
            "按目标 venue 模板完成投稿包",
            "stage_4_synthesis",
            "stage_4_synthesis",
            ["RC-REPORTING-CONTRACT-GAP"],
            "目标模板 PDF、逐项 checklist、匿名化报告、claim audit 和最终独立完整性复核。",
            {"reporting_integrity": 0.90, "venue_compliance": 0.88},
        ),
    ]
    open_codes = {item.code for item in assessment.blockers}
    floors: dict[str, float] = {}
    output: list[ReadinessAction] = []
    for priority, (action_id, title, return_stage, execution_stage, resolves, artifact, action_floors) in enumerate(available, start=1):
        if resolves and not (set(resolves) & open_codes):
            if all(
                next(item.score for item in dimensions if item.id == dimension_id) >= floor
                for dimension_id, floor in action_floors.items()
            ):
                continue
        floors.update({key: max(floors.get(key, 0.0), value) for key, value in action_floors.items()})
        output.append(
            ReadinessAction(
                id=action_id,
                priority=priority,
                title=title,
                return_to_stage=return_stage,
                execution_stage=execution_stage,
                resolves=sorted(set(resolves) & open_codes),
                required_artifact=artifact,
                projected_dimension_floors=action_floors,
                projected_readiness_after_action=_score_with_floors(dimensions, floors),
            )
        )
    return output


def _stage_backpropagation(actions: list[ReadinessAction]) -> list[StageBackpropagation]:
    stage_specs = [
        (
            "stage_1_discovery",
            "在批准研究问题前冻结 venue、贡献、新颖性边界和完整构念。",
            ["目标 venue/track 合同", "可复跑文献检索与 novelty matrix", "构念与反构念清单"],
        ),
        (
            "stage_2_protocol",
            "在协议冻结前反推识别条件、独立校准、任务/种子下限和报告合同。",
            ["前瞻性同产物分支协议", "独立评估器校准计划", "功效/稳定性和遥测 schema"],
        ),
        (
            "stage_3_experimentation",
            "只执行能生成投稿门所需证据的冻结实验，并保持逐单元审计链。",
            ["异质任务矩阵", "独立盲审/金标准", "公开可复现的运行产物和成本遥测"],
        ),
        (
            "stage_4_synthesis",
            "写作、完整性复核、目标模板和投稿清单均通过后才颁发投稿就绪状态。",
            ["深度报告", "claim-evidence audit", "目标模板 PDF 与 publication-readiness gate"],
        ),
    ]
    output: list[StageBackpropagation] = []
    for stage, gate, evidence in stage_specs:
        changes = [
            action.title
            for action in actions
            if stage in {action.return_to_stage, action.execution_stage}
        ]
        output.append(
            StageBackpropagation(
                stage=stage,
                gate=gate,
                required_changes=changes or ["保持现有合同并在下一门重新核验。"],
                completion_evidence=evidence,
            )
        )
    return output


def _system_design_repairs(blockers: list[ReadinessBlocker]) -> tuple[str | None, list[SystemDesignRepair]]:
    grouped: dict[str, list[ReadinessBlocker]] = {}
    for blocker in blockers:
        canonical = _DESIGN_REPAIR_ALIASES.get(blocker.code, blocker.code)
        if canonical not in _SYSTEM_REPAIR_RULES:
            continue
        grouped.setdefault(canonical, []).append(blocker)
    if not grouped:
        return None, []
    fingerprint_payload = {
        "canonical_root_codes": sorted(grouped),
        "all_trigger_codes": sorted(item.code for item in blockers),
    }
    fingerprint = f"pubfail-{_canonical_hash(fingerprint_payload)[:16]}"
    repairs: list[SystemDesignRepair] = []
    for canonical in sorted(grouped):
        sources = grouped[canonical]
        rule = _SYSTEM_REPAIR_RULES[canonical]
        external_deferred = canonical == "RC-MATURITY-TARGET-MISMATCH"
        repairs.append(
            SystemDesignRepair(
                repair_id=f"repair-{_canonical_hash({'fingerprint': fingerprint, 'code': canonical})[:12]}",
                source_codes=sorted({canonical, *(item.code for item in sources)}),
                root_cause=" ".join(dict.fromkeys(item.reason for item in sources)),
                earliest_repair_stage=str(rule["stage"]),
                system_gap=str(rule["gap"]),
                required_rule_change=str(rule["rule"]),
                enforcement_stage=str(rule["enforcement_stage"]),
                enforcement_points=[str(item) for item in rule["points"]],
                regression_tests=[str(item) for item in rule["tests"]],
                applicability="新建实验和基于本项目启动的下一版协议；历史冻结协议与结果保持不可变。",
                status=(
                    "deferred_external_validation"
                    if external_deferred
                    else "active_for_future_runs"
                ),
            )
        )
    return fingerprint, repairs


def build_system_design_repair_report(
    readiness: PublicationReadinessReport,
) -> SystemDesignRepairReport:
    return SystemDesignRepairReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        contract_id=readiness.contract_id,
        manuscript_sha256=readiness.manuscript_sha256,
        failure_fingerprint=readiness.failure_fingerprint or "no-scientific-gate-failure",
        scientific_gate_failed=not readiness.hard_gate_passed,
        repair_principle=(
            "科学硬门失败必须修订最早可预防阶段的系统合同和回归测试，不能只润色当前论文。"
        ),
        repeated_failure_policy=(
            "同一 failure fingerprint 再次出现时，系统必须审计上次修复的执行点和回归测试；"
            "不得把同一建议换一种文字后再次关闭问题。"
        ),
        repairs=readiness.system_design_repairs,
    )


def stage2_design_repair_violations(
    repair_report: dict[str, Any],
    *,
    open_root_cause_codes: set[str],
    task_count: int,
    seed_count: int,
    novelty_refresh_present: bool,
) -> list[str]:
    """Apply a previous publication failure as a hard constraint on the next protocol.

    The function is intentionally data-only so Stage 2 can enforce a persisted repair
    without mutating or reinterpreting the historical study that triggered it.
    """

    violations: list[str] = []
    for item in repair_report.get("repairs", []):
        if not isinstance(item, dict) or item.get("status") != "active_for_future_runs":
            continue
        codes = {str(code) for code in item.get("source_codes", [])}
        root_codes = {code for code in codes if code.startswith("RC-")}
        still_open = sorted(root_codes & open_root_cause_codes)
        if still_open:
            violations.append(
                "active design repair still fails root-cause checks: " + ", ".join(still_open)
            )
        if "READINESS-EVIDENCE-BREADTH-BELOW-HARD-MINIMUM" in codes:
            if task_count < 8 or seed_count < 5:
                violations.append(
                    f"active evidence-breadth repair requires at least 8 tasks x 5 seeds; got {task_count} x {seed_count}"
                )
        if "READINESS-NOVELTY-POSITIONING-BELOW-HARD-MINIMUM" in codes:
            if not novelty_refresh_present:
                violations.append(
                    "active novelty-positioning repair requires design_revisions/novelty_refresh.json"
                )
    return list(dict.fromkeys(violations))


def create_publication_target(
    manuscript: str | Path,
    *,
    project: str | Path,
    venue_id: str,
    readiness_threshold: float = DEFAULT_READINESS_THRESHOLD,
    registry_path: str | Path | None = None,
    contract_path: str | Path | None = None,
    replace: bool = False,
) -> tuple[PublicationTargetContract, Path]:
    source = Path(manuscript).resolve()
    project_path = Path(project).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not project_path.is_dir():
        raise FileNotFoundError(project_path)
    registry = load_journal_registry(registry_path)
    profile = next((item for item in registry.venues if item.id == venue_id), None)
    if profile is None:
        raise ValueError(f"venue is not in the strict registry: {venue_id}")
    registry_file = Path(registry_path).resolve() if registry_path else Path(__file__).resolve().parent / "resources" / "venues" / "ai_research_strict.v2.json"
    output_path = Path(contract_path).resolve() if contract_path else _default_contract_path(project_path)
    stable_fields = {
        "project_path": str(project_path),
        "venue_id": profile.id,
        "venue_type": profile.venue_type,
        "track": profile.track,
        "registry_id": registry.registry_id,
        "readiness_threshold": readiness_threshold,
        "criteria": [item.model_dump(mode="json") for item in _CRITERIA],
    }
    contract = PublicationTargetContract(
        contract_id=f"pubtarget-{_canonical_hash(stable_fields)[:16]}",
        created_at=datetime.now(timezone.utc).isoformat(),
        project_path=str(project_path),
        baseline_manuscript_path=str(source),
        baseline_manuscript_sha256=sha256_file(source),
        venue_id=profile.id,
        venue_name=profile.name,
        venue_type=profile.venue_type,
        track=profile.track,
        official_venue_url=profile.official_venue_url,
        scope_url=profile.scope_url,
        registry_id=registry.registry_id,
        registry_source_checked_at=registry.source_checked_at,
        registry_sha256=sha256_file(registry_file),
        readiness_threshold=readiness_threshold,
        threshold_semantics=(
            "Deterministic internal submission-readiness threshold controlled by Research Forge. "
            "It is not and must not be reported as the probability of peer-review acceptance."
        ),
        acceptance_probability_is_not_target=True,
        criteria=[item.model_copy(deep=True) for item in _CRITERIA],
    )
    if output_path.is_file() and not replace:
        existing = PublicationTargetContract.model_validate(read_json(output_path))
        if existing.contract_id == contract.contract_id:
            return existing, output_path
        raise FileExistsError(
            f"a different publication target is already frozen at {output_path}; use --replace to replace it explicitly"
        )
    write_json_atomic(output_path, contract)
    return contract, output_path


def freeze_publication_experiment_target(
    *,
    project: str | Path,
    venue_id: str,
    recommendation_report_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    final_readiness_threshold: float = DEFAULT_READINESS_THRESHOLD,
    output_path: str | Path | None = None,
    replace: bool = False,
) -> tuple[PublicationExperimentContract, Path]:
    """Freeze a strict venue recommendation into a pre-experiment publication contract."""

    project_path = Path(project).resolve()
    if not project_path.is_dir():
        raise FileNotFoundError(project_path)
    recommendation_path = (
        Path(recommendation_report_path).resolve()
        if recommendation_report_path
        else project_path / "synthesis" / "venue_recommendation.json"
    )
    if not recommendation_path.is_file():
        raise FileNotFoundError(
            f"venue recommendation report is required before freezing a publication experiment: {recommendation_path}"
        )
    recommendation_report = read_json(recommendation_path)
    selected = next(
        (
            item
            for item in recommendation_report.get("recommendations", [])
            if isinstance(item, dict)
            and (item.get("journal_id") or item.get("venue_id")) == venue_id
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            f"venue {venue_id} is absent from the supplied recommendation report"
        )
    registry = load_journal_registry(registry_path)
    profile = next((item for item in registry.venues if item.id == venue_id), None)
    if profile is None:
        raise ValueError(f"venue is not in the strict registry: {venue_id}")
    if recommendation_report.get("registry_id") != registry.registry_id:
        raise ValueError("venue recommendation registry does not match the active strict registry")
    registry_file = (
        Path(registry_path).resolve()
        if registry_path
        else Path(__file__).resolve().parent
        / "resources"
        / "venues"
        / "ai_research_strict.v2.json"
    )
    stable = {
        "project_path": str(project_path),
        "venue_id": profile.id,
        "track": profile.track,
        "registry_id": registry.registry_id,
        "recommendation_report_sha256": sha256_file(recommendation_path),
        "final_readiness_threshold": final_readiness_threshold,
        "minimum_tasks": 8,
        "minimum_seeds_per_task": 5,
    }
    contract = PublicationExperimentContract(
        contract_id=f"pubexp-{_canonical_hash(stable)[:16]}",
        created_at=datetime.now(timezone.utc).isoformat(),
        project_path=str(project_path),
        venue_id=profile.id,
        venue_name=profile.name,
        venue_type=profile.venue_type,
        track=profile.track,
        registry_id=registry.registry_id,
        registry_source_checked_at=registry.source_checked_at,
        registry_sha256=sha256_file(registry_file),
        recommendation_report_path=str(recommendation_path),
        recommendation_report_sha256=sha256_file(recommendation_path),
        recommendation_rank=int(selected.get("rank", 0) or 0),
        recommendation_scope_fit=float(selected.get("scope_fit", 0.0) or 0.0),
        recommendation_quality_score=float(selected.get("quality_score", 0.0) or 0.0),
        recommendation_routing_label=str(selected.get("routing_label", "unknown")),
        venue_quality_bar=profile.quality_bar,
        venue_quality_weights=profile.weights,
        final_readiness_threshold=final_readiness_threshold,
        minimum_tasks=8,
        minimum_seeds_per_task=5,
        required_construct_metrics=[
            "claim_retention_or_deletion",
            "semantic_change_type",
            "informativeness_or_usefulness",
        ],
        blocking_root_cause_codes=[
            "RC-MEASUREMENT-CIRCULARITY",
            "RC-COUNTERFACTUAL-NONISOLATION",
            "RC-CONSTRUCT-UNDERCOVERAGE",
        ],
        require_shared_artifact_counterfactual=True,
        require_independent_calibration_contract=True,
        require_contextual_novelty_refresh=True,
        venue_target_locked=True,
        target_switch_policy=(
            "Changing venue or track requires a new contract id and a new pre-experiment audit; "
            "an experiment cannot inherit a passing gate from another venue."
        ),
    )
    destination = (
        Path(output_path).resolve()
        if output_path
        else project_path / EXPERIMENT_TARGET_FILENAME
    )
    if destination.is_file() and not replace:
        existing = PublicationExperimentContract.model_validate(read_json(destination))
        if existing.contract_id == contract.contract_id:
            return existing, destination
        raise FileExistsError(
            f"a different publication experiment target is already frozen at {destination}; use --replace explicitly"
        )
    write_json_atomic(destination, contract)
    return contract, destination


def load_publication_experiment_target(
    path: str | Path,
) -> PublicationExperimentContract:
    return PublicationExperimentContract.model_validate(read_json(Path(path).resolve()))


def audit_publication_experiment_design(
    contract: PublicationExperimentContract | str | Path,
    *,
    protocol: dict[str, Any],
    root_cause_report: dict[str, Any],
    novelty_refresh_present: bool,
    source_paths: list[str] | None = None,
) -> PublicationExperimentGateReport:
    target = (
        contract
        if isinstance(contract, PublicationExperimentContract)
        else load_publication_experiment_target(contract)
    )
    open_codes = {
        str(item.get("code"))
        for item in root_cause_report.get("findings", [])
        if isinstance(item, dict) and item.get("resolved") is not True
    }
    tasks = [item for item in protocol.get("tasks", []) if isinstance(item, dict)]
    seeds = list(protocol.get("seeds", []) or [])
    metric_names = {
        str(protocol.get("primary_metric", "")).casefold(),
        *(str(item).casefold() for item in protocol.get("secondary_metrics", [])),
    }
    construct_groups = {
        "claim_retention_or_deletion": ("retention", "deletion", "removal"),
        "semantic_change_type": ("semantic_change", "semantic change", "contraction", "qualification"),
        "informativeness_or_usefulness": ("informativeness", "usefulness", "utility"),
    }
    present_constructs = sorted(
        name
        for name, tokens in construct_groups.items()
        if any(any(token in metric for token in tokens) for metric in metric_names)
    )
    violations: list[str] = []
    if protocol.get("study_intent") != "publication":
        violations.append(
            "publication experiment protocol is not explicitly frozen with publication intent"
        )
    if protocol.get("publication_contract_id") != target.contract_id:
        violations.append(
            "publication experiment protocol is not bound to the locked publication contract"
        )
    blocking_open = sorted(set(target.blocking_root_cause_codes) & open_codes)
    if blocking_open:
        violations.append(
            "publication experiment retains blocking scientific root causes: "
            + ", ".join(blocking_open)
        )
    if len(tasks) < target.minimum_tasks:
        violations.append(
            f"publication experiment requires at least {target.minimum_tasks} heterogeneous tasks; got {len(tasks)}"
        )
    if len(set(seeds)) < target.minimum_seeds_per_task:
        violations.append(
            f"publication experiment requires at least {target.minimum_seeds_per_task} seeds per task; got {len(set(seeds))}"
        )
    missing_constructs = sorted(set(target.required_construct_metrics) - set(present_constructs))
    if missing_constructs:
        violations.append(
            "publication experiment is missing frozen construct metrics: "
            + ", ".join(missing_constructs)
        )
    gate_specification = protocol.get("evidence_gate_specification")
    if not isinstance(gate_specification, dict) or not {
        "claim_extraction_prompt_sha256",
        "evidence_matching_prompt_sha256",
        "decision_policy",
        "allowed_actions",
        "decision_trace_schema",
    }.issubset(gate_specification):
        violations.append(
            "publication experiment requires a frozen evidence-gate algorithm specification"
        )
    if not protocol.get("secondary_evaluator_contract"):
        violations.append(
            "publication experiment requires a frozen independent secondary evaluator contract"
        )
    required_construct_analyses = {
        "claim_retention_deletion",
        "semantic_change_distribution",
        "informativeness_usefulness",
        "per_task_effects",
    }
    if not required_construct_analyses.issubset(
        {str(item) for item in protocol.get("construct_analysis_requirements", [])}
    ):
        violations.append(
            "publication experiment requires frozen construct-preservation analysis requirements"
        )
    telemetry_contract = protocol.get("telemetry_contract")
    if not isinstance(telemetry_contract, dict) or telemetry_contract.get(
        "wall_clock_definition"
    ) != "active_attempt_seconds":
        violations.append(
            "publication experiment requires active-attempt wall-clock telemetry semantics"
        )
    if target.require_contextual_novelty_refresh and not novelty_refresh_present:
        violations.append(
            "publication experiment requires design_revisions/novelty_refresh.json before protocol freeze"
        )
    protocol_id = str(protocol.get("protocol_id", "unfrozen-protocol"))
    return PublicationExperimentGateReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        contract_id=target.contract_id,
        protocol_id=protocol_id,
        venue_id=target.venue_id,
        target_locked=target.venue_target_locked,
        task_count=len(tasks),
        seed_count=len(set(seeds)),
        open_root_cause_codes=sorted(open_codes),
        construct_metrics=present_constructs,
        novelty_refresh_present=novelty_refresh_present,
        pre_experiment_gate_passed=not violations,
        violations=violations,
        final_retest_requirements=[
            f"rerun strict venue recommendation against locked venue {target.venue_id}",
            f"venue-weighted quality_score must be at least {target.venue_quality_bar:.4f}",
            f"publication readiness must be at least {target.final_readiness_threshold:.4f}",
            "zero critical/high scientific blockers",
            "the final manuscript and experiment contract must retain the same venue id and contract hash",
        ],
        source_paths=source_paths or [],
    )


def load_publication_target(path: str | Path) -> PublicationTargetContract:
    return PublicationTargetContract.model_validate(read_json(Path(path).resolve()))


def audit_publication_readiness(
    contract: PublicationTargetContract | str | Path,
    *,
    manuscript: str | Path | None = None,
    project: str | Path | None = None,
    registry_path: str | Path | None = None,
    history_path: str | Path | None = None,
    contract_path: str | Path | None = None,
) -> PublicationReadinessReport:
    if isinstance(contract, PublicationTargetContract):
        target = contract
        resolved_contract_path = Path(contract_path).resolve() if contract_path else _default_contract_path(Path(target.project_path))
    else:
        resolved_contract_path = Path(contract).resolve()
        target = load_publication_target(resolved_contract_path)
    source = Path(manuscript or target.baseline_manuscript_path).resolve()
    project_path = Path(project or target.project_path).resolve()
    recommendation_report = recommend_venues(
        source,
        project=project_path,
        registry_path=registry_path,
        history_path=history_path,
        top=None,
    )
    selected = next(
        (item for item in recommendation_report.recommendations if item.journal_id == target.venue_id),
        None,
    )
    if selected is None:
        raise ValueError(f"target venue is unavailable in the active strict registry: {target.venue_id}")
    active_registry = load_journal_registry(registry_path)
    selected_profile = next(
        item for item in active_registry.venues if item.id == target.venue_id
    )
    manuscript_text = source.read_text(encoding="utf-8", errors="replace")
    assessment = recommendation_report.assessment
    dimensions = _build_dimensions(target, assessment, selected, manuscript_text)
    readiness_score = _round(sum(item.score * item.weight for item in dimensions))

    blockers: list[ReadinessBlocker] = []
    for blocker in assessment.blockers:
        if blocker.severity not in {"critical", "high"}:
            continue
        origin, prevention = _ROOT_STAGE_MAP.get(blocker.code, ("stage_2_protocol", "before_submission"))
        blockers.append(
            ReadinessBlocker(
                code=blocker.code,
                severity=blocker.severity,
                origin_stage=origin,
                latest_prevention_stage=prevention,
                reason=blocker.reason,
                required_evidence=blocker.required_action,
                source=blocker.source,
            )
        )
    for dimension in dimensions:
        if dimension.status != "hard_fail":
            continue
        blockers.append(
            ReadinessBlocker(
                code=f"READINESS-{dimension.id.upper().replace('_', '-')}-BELOW-HARD-MINIMUM",
                severity="critical",
                origin_stage=dimension.owner_stage,
                latest_prevention_stage=f"before_{dimension.owner_stage}_exit",
                reason=(
                    f"{dimension.label}得分 {dimension.score:.2f}，低于硬下限 {dimension.hard_minimum:.2f}。"
                ),
                required_evidence=next(
                    item.evidence_contract for item in target.criteria if item.id == dimension.id
                ),
                source=str(source),
            )
        )
    if not assessment.external_public_repository:
        blockers.append(
            ReadinessBlocker(
                code="EXTERNAL-PUBLIC-SUPPLEMENT-PENDING",
                severity="critical",
                origin_stage="stage_4_synthesis",
                latest_prevention_stage="before_external_submission",
                reason=(
                    "A local anonymous supplement package may be hash-bound and auditable, "
                    "but no externally accessible publication release has been verified."
                ),
                required_evidence=(
                    "An authorized public anonymous release with immutable version identifier, "
                    "package manifest hash, and independently reachable contents."
                ),
                source=str(project_path / "synthesis" / "anonymous_supplement_package.json"),
            )
        )
    if selected.venue_type == "conference" and not selected.current_cycle_eligible:
        blockers.append(
            ReadinessBlocker(
                code="CURRENT-SUBMISSION-CYCLE-UNAVAILABLE",
                severity="critical",
                origin_stage="stage_1_discovery",
                latest_prevention_stage="before_submission",
                reason="目标会议的当前正式投稿周期不可用。",
                required_evidence="更新到官方已开放周期，并重新冻结 deadline URL 与日期。",
                source=selected.official_venue_url,
            )
        )
    if selected.quality_score < selected_profile.quality_bar:
        blockers.append(
            ReadinessBlocker(
                code="VENUE-QUALITY-BAR-NOT-MET",
                severity="high",
                origin_stage="stage_1_discovery",
                latest_prevention_stage="before_submission",
                reason=(
                    f"锁定 venue 的加权质量分 {selected.quality_score:.4f} 低于冻结质量线 "
                    f"{selected_profile.quality_bar:.4f}。"
                ),
                required_evidence=(
                    "不得靠换 venue 或加文字过门；必须提升该 venue 权重下的方法、证据、复现与成熟度，"
                    "然后用同一 venue id 重新推荐和审计。"
                ),
                source=str(resolved_contract_path),
            )
        )
    unique_blockers = {item.code: item for item in blockers}
    blockers = sorted(unique_blockers.values(), key=lambda item: (item.severity != "critical", item.code))
    # Human validation is an explicit external gate.  Keep it separate from
    # scientific/design blockers so the system can report an automated pass
    # without ever emitting a submission-ready claim prematurely.
    protocol_path = project_path / "stage2" / "protocol.json"
    protocol = read_json(protocol_path) if protocol_path.is_file() else {}
    human_gate_pending = bool(protocol.get("manual_audit")) and not (
        assessment.human_validation_complete and assessment.primary_analysis_interpretable
    )
    external_blockers = [item for item in blockers if item.code in _EXTERNAL_ONLY_BLOCKER_CODES]
    automated_hard_blockers = [item for item in blockers if item.code not in _EXTERNAL_ONLY_BLOCKER_CODES]
    failure_fingerprint, design_repairs = _system_design_repairs(automated_hard_blockers)
    actions = _actions(dimensions, assessment)
    projected = actions[-1].projected_readiness_after_action if actions else readiness_score
    score_passed = readiness_score >= target.readiness_threshold
    automated_hard_gate_passed = not automated_hard_blockers
    automated_publication_gate_passed = score_passed and automated_hard_gate_passed
    hard_gate_passed = automated_hard_gate_passed and not external_blockers and not human_gate_pending
    ready = score_passed and hard_gate_passed
    evidence_sources = sorted(
        set(
            assessment.evidence_sources
            + [str(source), str(resolved_contract_path)]
        )
    )
    return PublicationReadinessReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        contract_id=target.contract_id,
        contract_path=str(resolved_contract_path),
        manuscript_path=str(source),
        manuscript_sha256=sha256_file(source),
        venue_id=selected.journal_id,
        venue_name=selected.journal_name,
        venue_type=selected.venue_type,
        readiness_score=readiness_score,
        readiness_threshold=target.readiness_threshold,
        score_threshold_passed=score_passed,
        automated_hard_gate_passed=automated_hard_gate_passed,
        automated_publication_gate_passed=automated_publication_gate_passed,
        human_gate_pending=human_gate_pending,
        automated_hard_blockers=automated_hard_blockers,
        external_blockers=external_blockers,
        hard_gate_passed=hard_gate_passed,
        publication_submission_ready=ready,
        readiness_interpretation=(
            "This score measures controllable internal readiness. The automated gate requires zero "
            "automated hard blockers; external release and human-validation gates remain separately fail-closed. "
            "It is not evidence that the venue will accept the paper."
        ),
        acceptance_probability_interpretation=recommendation_report.probability_interpretation,
        estimated_acceptance_probability=selected.combined_submission_success,
        current_cycle_acceptance_probability=selected.current_cycle_submission_success,
        projected_acceptance_after_known_blockers=selected.after_known_blockers_resolved,
        acceptance_calibration_status=selected.calibration_status,
        dimensions=dimensions,
        hard_blockers=blockers,
        failure_fingerprint=failure_fingerprint,
        system_design_repair_required=bool(design_repairs),
        system_design_repairs=design_repairs,
        actions=actions,
        stage_backpropagation=_stage_backpropagation(actions),
        projected_readiness_after_plan=projected,
        projection_assumptions=[
            "投影只是假设每项要求的证据真实生成并通过复核；它不会自动清除任何根因。",
            "投影不改变录用概率估计，也不能用作未来结果、人工验证或投稿保证。",
            "每次稿件、协议、实验或 venue 周期变化后都必须重新运行硬门。",
        ],
        evidence_sources=evidence_sources,
    )


def render_publication_readiness_markdown(report: PublicationReadinessReport) -> str:
    lines = [
        "# Research Forge 投稿就绪硬门",
        "",
        f"- 目标：**{report.venue_name}**（`{report.venue_id}`）",
        f"- 内部投稿就绪度：**{report.readiness_score:.1%}** / 门槛 **{report.readiness_threshold:.1%}**",
        f"- 投稿就绪：**{'通过' if report.publication_submission_ready else '阻断'}**",
        f"- 自动化发表门：**{'通过' if report.automated_publication_gate_passed else '阻断'}**",
        f"- 科学硬门：**{'通过' if report.hard_gate_passed else '失败'}**",
        f"- 当前录用概率估计：**{report.estimated_acceptance_probability.center:.1%}** "
        f"[{report.estimated_acceptance_probability.low:.1%}, {report.estimated_acceptance_probability.high:.1%}]",
        f"- 概率校准：`{report.acceptance_calibration_status}`",
        "",
        "> 60% 是 Research Forge 可控制的投稿就绪门，不是期刊录用率。系统禁止为了达到 60% 而修改录用概率模型。",
        "",
        "## 九维就绪度",
        "",
        "| 维度 | 得分 | 目标 | 硬下限 | 权重 | 状态 | 回流阶段 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for item in report.dimensions:
        lines.append(
            f"| {item.label} | {item.score:.0%} | {item.required_score:.0%} | {item.hard_minimum:.0%} | "
            f"{item.weight:.0%} | `{item.status}` | `{item.owner_stage}` |"
        )
    lines.extend(["", "## 当前科学硬阻塞", ""])
    if report.hard_blockers:
        for item in report.hard_blockers:
            lines.append(
                f"- **{item.code}**（{item.origin_stage}）：{item.reason} 需要：{item.required_evidence}"
            )
    else:
        lines.append("- 无。")
    if report.external_blockers:
        lines.extend(["", "## 外部授权阻断（不计入自动化系统缺陷）", ""])
        for item in report.external_blockers:
            lines.append(f"- **{item.code}**：{item.reason} 需要：{item.required_evidence}")
    lines.extend(["", "## 失败后的系统设计修复", ""])
    if report.system_design_repairs:
        lines.append(
            f"失败指纹：`{report.failure_fingerprint}`。旧实验保持不可变；以下规则对下一版协议和未来运行生效。"
        )
        lines.append("")
        for item in report.system_design_repairs:
            lines.append(
                f"- **{item.repair_id} · {item.earliest_repair_stage}**：{item.system_gap} "
                f"系统规则改为：{item.required_rule_change} 回归测试：{'；'.join(item.regression_tests)}"
            )
    else:
        lines.append("- 科学硬门未触发系统设计修复。")
    lines.extend(["", "## 最短过线路线（必须用真实产物复核）", ""])
    for item in report.actions:
        lines.append(
            f"{item.priority}. **{item.title}**：回到 `{item.return_to_stage}`，在 `{item.execution_stage}` 完成；"
            f"累计情景就绪度 {item.projected_readiness_after_action:.1%}。产物：{item.required_artifact}"
        )
    lines.extend(
        [
            "",
            f"全部动作完成后的情景投影：**{report.projected_readiness_after_plan:.1%}**。",
            "",
            "## 四阶段反向约束",
            "",
        ]
    )
    for item in report.stage_backpropagation:
        lines.append(f"### {item.stage}")
        lines.append("")
        lines.append(item.gate)
        lines.append("")
        lines.extend(f"- {change}" for change in item.required_changes)
        lines.append("")
    lines.extend(["## 解释边界", ""])
    lines.extend(f"- {item}" for item in report.projection_assumptions)
    return "\n".join(lines) + "\n"


def persist_publication_readiness(
    report: PublicationReadinessReport,
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> tuple[Path, Path]:
    json_output = Path(json_path).resolve()
    markdown_output = Path(markdown_path).resolve()
    write_json_atomic(json_output, report)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(
        render_publication_readiness_markdown(report),
        encoding="utf-8",
        newline="\n",
    )
    return json_output, markdown_output


def render_system_design_repair_markdown(report: SystemDesignRepairReport) -> str:
    lines = [
        "# Research Forge 科学失败 → 系统设计修复单",
        "",
        f"- 失败指纹：`{report.failure_fingerprint}`",
        f"- 科学硬门失败：**{'是' if report.scientific_gate_failed else '否'}**",
        f"- 历史冻结产物保持不可变：**{'是' if report.historical_artifacts_immutable else '否'}**",
        "",
        report.repair_principle,
        "",
        "> " + report.repeated_failure_policy,
        "",
    ]
    for item in report.repairs:
        lines.extend(
            [
                f"## {item.repair_id}",
                "",
                f"- 触发：`{', '.join(item.source_codes)}`",
                f"- 最早修复阶段：`{item.earliest_repair_stage}`",
                f"- 执行门：`{item.enforcement_stage}`",
                f"- 状态：`{item.status}`",
                f"- 系统缺口：{item.system_gap}",
                f"- 新规则：{item.required_rule_change}",
                f"- 生效范围：{item.applicability}",
                "- 实现点：" + "；".join(item.enforcement_points),
                "- 回归测试：" + "；".join(item.regression_tests),
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def persist_system_design_repair(
    report: SystemDesignRepairReport,
    *,
    json_path: str | Path,
    markdown_path: str | Path,
    ledger_path: str | Path | None = None,
) -> tuple[Path, Path]:
    json_output = Path(json_path).resolve()
    markdown_output = Path(markdown_path).resolve()
    write_json_atomic(json_output, report)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(
        render_system_design_repair_markdown(report),
        encoding="utf-8",
        newline="\n",
    )
    if ledger_path is not None:
        ledger = Path(ledger_path).resolve()
        previous = 0
        if ledger.is_file():
            for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("failure_fingerprint") == report.failure_fingerprint:
                    previous += 1
        append_jsonl(
            ledger,
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "failure_fingerprint": report.failure_fingerprint,
                "occurrence_index": previous + 1,
                "contract_id": report.contract_id,
                "manuscript_sha256": report.manuscript_sha256,
                "repair_report": str(json_output),
                "repair_ids": [item.repair_id for item in report.repairs],
            },
        )
    return json_output, markdown_output
