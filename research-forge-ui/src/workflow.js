export const workflowStages = [
  { id: "discovery", number: "01", title: "发现与收敛", caption: "找到值得验证的问题" },
  { id: "protocol", number: "02", title: "协议与基线", caption: "冻结口径与对照" },
  { id: "experimentation", number: "03", title: "实验与判定", caption: "让证据决定结论" },
  { id: "synthesis", number: "04", title: "论文与审计", caption: "只写证据允许的内容" },
];

export const verdictCopy = {
  supported: { label: "得到支持", tone: "positive" },
  refuted: { label: "未获支持", tone: "negative" },
  not_supported: { label: "未获支持", tone: "negative" },
  mixed: { label: "混合结论", tone: "warning" },
  inconclusive: { label: "证据不足", tone: "warning" },
  unverifiable: { label: "暂不可验证", tone: "neutral" },
};

export const maturityCopy = {
  prospective_blind: "前瞻盲测证据",
  retrospective: "回顾性证据",
  mixed_or_unspecified: "成熟度待确认",
};

export const taskStatusCopy = {
  pending: "等待开始",
  running: "正在执行",
  retrying: "自动重试中",
  pause_requested: "正在安全暂停",
  paused: "已暂停",
  waiting_for_user: "需要你处理",
  succeeded: "已完成",
  failed: "执行失败",
  blocked: "被科学门阻止",
  cancelled: "已结束",
};

const presentationTermMap = new Map([
  ["ready", "可推进"],
  ["conditional", "需先验证"],
  ["eligible", "已验证可用"],
  ["unassessed", "尚未评估"],
  ["moderate", "中等"],
  ["strong", "较强"],
  ["weak", "较弱"],
  ["maximize", "越高越好"],
  ["minimize", "越低越好"],
  ["higher_is_better", "越高越好"],
  ["lower_is_better", "越低越好"],
  ["accuracy", "准确率"],
  ["paired cluster-aware bootstrap", "配对分组自助法"],
  ["frozen_protocol_output_binding", "协议、运行结果与主张已明确绑定"],
  ["derived_materials", "由项目材料推导"],
  ["verified_chain", "已验证证据链"],
  ["inferred_chain", "推断证据链"],
  ["dataset", "数据集"],
  ["implementation", "实验实现"],
  ["benchmark", "评测基准"],
  ["tool", "研究工具"],
  ["code", "代码实现"],
  ["model", "模型"],
  ["document", "文档资料"],
  ["ready_made", "可直接使用"],
  ["build_from_blueprint", "按实验蓝图构建"],
  ["succeeded", "已完成"],
  ["failed", "执行失败"],
  ["blocked", "条件不足"],
  ["running", "正在执行"],
  ["queued", "等待执行"],
  ["pending", "等待开始"],
  ["waiting_for_user", "等待你确认"],
  ["pass", "通过"],
  ["conditional_pass", "满足条件后可通过"],
  ["design_ready", "实验设计已就绪"],
  ["attention_required", "需要处理"],
  ["disclosure_only", "只需如实披露"],
  ["stage3_backfill_required", "需要回到实验阶段补充证据"],
  ["not_ready", "尚未就绪"],
  ["conditions_met", "条件已满足"],
  ["approved", "已批准"],
  ["rejected", "未批准"],
  ["abstained", "无法判断"],
]);

const presentationPhraseRules = [
  [/\bStudy\s+(study-[a-z0-9-]+)\b/gi, "研究任务"],
  [/\bExecution Readiness\b/gi, "执行准备情况"],
  [/\bDry Run\b/gi, "试运行"],
  [/\bPublication Control\b/gi, "投稿控制"],
  [/\bformal-primary-task\b/gi, "正式主任务"],
  [/\bStage 3 quick paired experiment\b/gi, "配对实验的可复现性与效果评估"],
  [/read-only project resources bound to this direction/gi, "与当前方向绑定的只读项目资源"],
  [/frozen external discovery sources/gi, "已冻结的外部检索来源"],
  [/a prospective test under a later Research Contract/gi, "在阶段二研究契约下进行前瞻验证"],
  [/The frozen study asks whether\s+(.+?)\s+ranking changes\s+(.+?)\s+relative to\s+(.+?)\s+ranking\.?$/gi, "本研究检验：与$3相比，$1是否提高$2。"],
  [/The frozen study asks whether\s+(.+?)\s+relative to\s+(.+?)\.?$/gi, "本研究检验$1相对于$2是否产生预先声明的差异。"],
  [/\btop_k_event_identification_rate\b/gi, "高收益事件识别率"],
  [/\bdual_quality_top5\b/gi, "兼顾预测质量与执行质量的候选排序方法"],
  [/\bv3_tech_quality_top5\b/gi, "技术指标排序基线"],
  [/\bpaired improvement\b/gi, "配对提升"],
  [/\babsolute difference\b/gi, "绝对差异"],
  [/\bchanges\b/gi, "改变"],
  [/\bimproves\b/gi, "提高"],
  [/\breduces\b/gi, "降低"],
  [/\bcompared with\b/gi, "与……相比"],
  [/\bversus\b/gi, "相对于"],
  [/\bpredeclared computational task, sample, or run\b/gi, "预先声明的任务、样本或运行"],
  [/Do the central project findings remain stable across frozen seeds, splits, and justified sensitivity checks\?/gi, "当前项目的核心结论在固定随机种子、数据划分和合理的敏感性条件下是否保持稳定？"],
  [/Which predeclared data, metric, or evaluation conditions make the project result unverifiable or unstable\?/gi, "哪些预先声明的数据、指标或评估条件会使项目结论无法验证或不稳定？"],
  [/The registered hypothesis could not be evaluated with the currently authorized resources and executable bindings\.?/gi, "现有授权资源和可执行条件不足以检验预先登记的研究假设。"],
  [/The full dataset, exact runtime lock, formal baseline matrix, treatment execution, and scientific effect estimate are explicit Stage 3 tasks\.?/gi, "完整数据集、精确运行环境、正式基线矩阵、实验组执行和科学效果估计均由阶段三完成。"],
  [/The visual sequence must explain the evidence boundary without displaying or implying an experimental effect\.?/gi, "图表应说明当前证据边界，不展示或暗示尚未得到验证的实验效果。"],
  [/Where did the registered study stop, and what evidence is required before its hypothesis can be evaluated\?/gi, "研究在哪一环节停止，以及检验该假设前还需要哪些证据？"],
  [/\branking\b/gi, "排序方法"],
  [/候选排序方法\s+排序方法/g, "候选排序方法"],
  [/排序基线\s+排序方法/g, "排序基线"],
  [/\bResearch Contract\b/gi, "实验契约"],
  [/\bScope Contract\b/gi, "研究范围契约"],
  [/\bCompletion Record\b/gi, "完成记录"],
  [/\bEvidence Sufficiency\b/gi, "证据充分性"],
  [/\bCoverage Report\b/gi, "检索覆盖报告"],
  [/\bProvider\b/gi, "检索来源"],
  [/\bVerdict\b/gi, "研究结论"],
  [/\bRun\b/gi, "实验运行"],
  [/\bBuild\b/gi, "构建"],
  [/\bsmoke test\b/gi, "冒烟测试"],
  [/\bsmoke\b/gi, "冒烟测试"],
];

export function humanizeResearchText(value, fallback = "待系统补全") {
  if (value === null || value === undefined || value === "") return fallback;
  const raw = String(value).trim();
  const exact = presentationTermMap.get(raw.toLowerCase());
  if (exact) return exact;
  if (/^Reproducibility and robustness of\s+/i.test(raw)) {
    return `${humanizeResearchText(raw.replace(/^Reproducibility and robustness of\s+/i, ""))}的可复现性与稳健性`;
  }
  if (/^Measurement boundaries and failure modes in\s+/i.test(raw)) {
    return `${humanizeResearchText(raw.replace(/^Measurement boundaries and failure modes in\s+/i, ""))}的测量边界与失效模式`;
  }
  const projected = presentationPhraseRules.reduce(
    (text, [pattern, replacement]) => text.replace(pattern, replacement),
    raw,
  );
  return projected.replace(
    /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/gi,
    (identifier) => presentationTermMap.get(identifier.toLowerCase()) || "系统内部字段",
  );
}

export function humanizeIdentifier(value, fallback = "系统记录") {
  if (value === null || value === undefined || value === "") return fallback;
  const raw = String(value).trim();
  if (
    /^[a-z][a-z0-9_-]*$/i.test(raw)
    && !presentationTermMap.has(raw.toLowerCase())
    && !presentationPhraseRules.some(([pattern]) => {
      pattern.lastIndex = 0;
      return pattern.test(raw);
    })
  ) return fallback;
  const translated = humanizeResearchText(raw, fallback);
  if (translated !== raw) return translated;
  if (/^[a-z][a-z0-9_-]*$/i.test(raw)) return fallback;
  return translated;
}

export function humanizeUiError(value, fallback = "当前步骤遇到问题，请重试或查看需要补充的条件。") {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (/generated package abstained because requirements are missing/i.test(raw)) {
    return "实验资产尚未生成。冻结契约仍缺少可执行的数据边界、对照方法、实验方法或测量规则，请返回阶段二补全后再继续。";
  }
  if (/string_too_long|should have at most \d+ characters/i.test(raw)) {
    return "生成内容超过系统允许的长度，已停止保存。请缩短输入或重新生成。";
  }
  if (/unsupported[_ ]profile|does not support.*profile/i.test(raw)) {
    return "当前实验设计还没有可直接复用的实现，系统需要按照冻结的实验蓝图重新构建。";
  }
  if (/contract_revision_required|contract.*cannot compile/i.test(raw)) {
    return "实验契约还缺少必要的科学定义，请回到阶段二补全并生成新版本。";
  }
  if (/timeout|timed out/i.test(raw)) {
    return "当前步骤等待时间过长。系统已保留进度，可以稍后重试。";
  }
  if (/network|connection|fetch failed/i.test(raw)) {
    return "外部连接暂时不可用。系统已保留本地进度，可以稍后重试。";
  }
  const translated = humanizeResearchText(raw, fallback);
  const latinWords = translated.match(/\b[A-Za-z]{3,}\b/g) || [];
  if (latinWords.length >= 8) return fallback;
  return translated;
}

export function compactStudyLabel(studyId = "") {
  if (!studyId) return "研究任务";
  const suffix = String(studyId).replace(/^study-/, "").slice(-6).toUpperCase();
  return `研究任务 ${suffix}`;
}

export const operationCopy = {
  "bundle.inspect": "分析项目材料",
  "bundle.close": "执行四阶段研究闭环",
  "bundle.expand-paper": "补写完整论文",
  "bundle.remediate": "执行补证据计划",
  "idea.start": "收敛研究想法",
};

export function directionTitle(candidate = {}) {
  return candidate.title
    || candidate.topic_title
    || candidate.display_title
    || candidate.novelty_seed
    || candidate.track_id
    || "候选研究方向";
}

export function candidateQuestion(candidate = {}) {
  if (candidate.research_question) return candidate.research_question;
  const statement = candidate.falsifiable_hypothesis || candidate.novelty_seed;
  if (statement) return `在冻结口径下，是否能够验证：${String(statement).replace(/[。.?？]+$/, "")}？`;
  return "现有项目材料能够支持检验哪一个明确主张？";
}

export function paperTopicTitle(candidate = {}) {
  if (candidate.title || candidate.topic_title) return candidate.title || candidate.topic_title;
  const title = directionTitle(candidate)
    .replace(/\s*[vV]\d+\b/g, "")
    .replace(/(?:回顾性|历史)?(?:实验|验证|重测)?研究?报告$/g, "")
    .replace(/验证结论$/g, "")
    .trim();
  return title ? `${title}的证据边界与验证` : "项目主张的证据边界与验证";
}

export function paperTopics(candidates = [], portfolio = null) {
  if (portfolio?.directions?.length) {
    return portfolio.directions.map((direction) => ({
      ...direction,
      topic_id: direction.direction_id,
      topic_title: direction.title,
      track_id: direction.primary_track_id || direction.supporting_track_ids?.[0] || "",
      contribution: direction.candidate_contribution,
      scope: direction.scope_in?.join("；") || "待负责人确认",
      evidence_chain_count: direction.evidence_chain_level === "verified_chain" ? 1 : 0,
      closure_input_ready: direction.evidence_chain_level === "verified_chain",
      paperability_score: 0,
    }));
  }
  return candidates.map((candidate) => ({
    ...candidate,
    topic_id: candidate.track_id,
    topic_title: paperTopicTitle(candidate),
    research_question: candidateQuestion(candidate),
    contribution: candidate.novelty_seed || "把项目中的既有主张转化为可以被证据推翻的研究命题。",
    scope: candidate.source_mode === "derived_materials" ? "项目现有材料与可追溯证据" : "冻结协议、输出与结论报告",
    supporting_track_ids: [candidate.track_id],
    evidence_chain_count: Number(candidate.artifact_chain_complete),
  })).sort((left, right) =>
    Number(right.closure_input_ready) - Number(left.closure_input_ready)
    || (right.paperability_score || 0) - (left.paperability_score || 0)
  );
}

export function blockerText(blocker = "") {
  if (blocker === "evidence is not a prospective blind validation") return "现有证据不是前瞻盲测结果";
  return humanizeResearchText(blocker, "当前证据边界仍需确认");
}

export function uniqueDirections(candidates = [], portfolio = null) {
  return paperTopics(candidates, portfolio);
}

export function taskUiState(task) {
  if (!task) return null;
  if (["pending", "running", "pause_requested", "paused"].includes(task.status)) return "running";
  if (["waiting_for_user", "failed", "blocked"].includes(task.status)) return "attention";
  if (task.status !== "succeeded") return null;
  if (task.request?.operation === "bundle.inspect") return "directions";
  if (["bundle.close", "bundle.expand-paper", "bundle.remediate"].includes(task.request?.operation)) return "verdict";
  if (task.request?.operation === "idea.start") return "idea_ready";
  return null;
}

export function phaseForTaskStage(stage) {
  return ({
    stage_1_discovery: "discovery",
    discovery: "discovery",
    stage_2_protocol: "protocol",
    protocol: "protocol",
    stage_3_experimentation: "experiment",
    experimentation: "experiment",
    experiment: "experiment",
    stage_4_synthesis: "paper",
    synthesis: "paper",
    paper: "paper",
  })[stage] || "discovery";
}

export function preferredWorkflowSnapshot({ run, ideaResult, inspection, activeTask } = {}) {
  return run?.workflow
    || ideaResult?.workflow
    || inspection?.workflow
    || activeTask?.result?.workflow
    || null;
}

const resourceTypeLabel = {
  dataset: "数据包",
  implementation: "实验工具",
  benchmark: "测评基准",
  model: "模型",
  environment: "运行环境",
};

function packageIntent(topic = {}, index = 0) {
  const text = `${topic.title || ""} ${topic.research_question || ""}`.toLowerCase();
  if (text.includes("reproducibility") || text.includes("robustness") || text.includes("稳健")) {
    return {
      name: "稳健性验证包",
      effect: "检验核心结果换种子、数据划分或敏感性设置后是否仍然成立。",
    };
  }
  if (text.includes("measurement") || text.includes("failure mode") || text.includes("边界") || text.includes("失效")) {
    return {
      name: "边界诊断包",
      effect: "定位哪些数据、指标或评估条件会让结论不稳定或无法验证。",
    };
  }
  return {
    name: index === 0 ? "核心效果包" : "直接验证包",
    effect: "直接回答当前研究方向最核心的效果问题，优先复用已有数据与实现。",
  };
}

export function buildStageTwoResourcePackages({
  topics = [],
  resources = [],
  shortlists = [],
  recommendedTopicId = null,
} = {}) {
  const resourceById = new Map(resources.map((item) => [item.candidate_id, item]));
  const fallbackByType = new Map(
    shortlists.map((item) => [
      item.need_type,
      item.recommended_candidate_id
        || item.candidate_ids?.find((candidateId) => resourceById.has(candidateId))
        || null,
    ]),
  );
  const packages = topics.map((topic, index) => {
    const requestedIds = topic.recommended_resource_candidate_ids?.length
      ? topic.recommended_resource_candidate_ids
      : (topic.required_resource_types || []).map((type) => fallbackByType.get(type)).filter(Boolean);
    const requestedTypes = new Set(
      requestedIds
        .map((candidateId) => resourceById.get(candidateId)?.need_type)
        .filter(Boolean),
    );
    const completedIds = [
      ...requestedIds,
      ...(topic.required_resource_types || [])
        .filter((type) => !requestedTypes.has(type))
        .map((type) => fallbackByType.get(type))
        .filter(Boolean),
    ];
    const selectedResources = [...new Set(completedIds)]
      .map((candidateId) => resourceById.get(candidateId))
      .filter(Boolean);
    const coveredTypes = new Set(selectedResources.map((item) => item.need_type));
    const missingTypes = (topic.required_resource_types || []).filter((type) => !coveredTypes.has(type));
    const conditionalResources = selectedResources.filter((item) => item.eligibility === "conditional");
    const intent = packageIntent(topic, index);
    const recommended = topic.topic_id === recommendedTopicId
      || (!recommendedTopicId && index === 0);
    const effort = missingTypes.length
      ? `阶段三还需获取或构建：${missingTypes.map((type) => resourceTypeLabel[type] || type).join("、")}`
      : conditionalResources.length
        ? `阶段二先用最小可行性验证检查 ${conditionalResources.length} 项条件资源`
        : "现有资源足以开始阶段二最小可行性验证";
    return {
      package_id: `resource-package-${topic.topic_id || index}`,
      topic,
      name: intent.name,
      effect: intent.effect,
      recommended,
      resources: selectedResources,
      resourceIds: selectedResources.map((item) => item.candidate_id),
      missingTypes,
      requiredTypeLabels: (topic.required_resource_types || []).map(
        (type) => resourceTypeLabel[type] || type,
      ),
      effort,
      risks: (topic.unresolved_conditions || []).slice(0, 3),
    };
  });
  return packages
    .sort((left, right) => Number(right.recommended) - Number(left.recommended))
    .slice(0, 3);
}

export function deriveStageState(activeStage, index) {
  const activeIndex = workflowStages.findIndex((stage) => stage.id === activeStage);
  if (activeIndex < 0) return "pending";
  if (index < activeIndex) return "complete";
  if (index === activeIndex) return "active";
  return "pending";
}

export function inferRequirement(task) {
  const explicit = task?.requirements?.find((item) => !item.resolved);
  if (explicit) return explicit;
  if (!["waiting_for_user", "failed", "blocked"].includes(task?.status)) return null;
  const workflowSteps = task?.result?.workflow?.steps || [];
  const currentStepIds = new Set(task?.result?.workflow?.study?.current_step_ids || []);
  const blockedStep = workflowSteps.find(
    (step) => currentStepIds.has(step.step_instance_id) && step.blocker?.message,
  ) || workflowSteps.find(
    (step) => ["waiting_for_user", "failed", "blocked"].includes(step.status) && step.blocker?.message,
  );
  const message = task?.error?.message
    || blockedStep?.blocker?.message
    || "系统没有返回可操作的阻塞原因。请查看任务审计记录，不要盲目重试。";
  const lower = message.toLowerCase();
  let kind = "configuration";
  let title = "研究需要你处理一个条件";
  if (lower.includes("api") || lower.includes("credential") || lower.includes("key")) {
    kind = "credential";
    title = "需要配置外部服务访问权限";
  } else if (lower.includes("docker") || lower.includes("environment") || lower.includes("module")) {
    kind = "environment";
    title = "需要准备受控运行环境";
  } else if (lower.includes("data") || lower.includes("dataset") || lower.includes("file")) {
    kind = "dataset";
    title = "需要补充研究数据或文件";
  }
  return {
    requirement_id: "inferred",
    kind,
    title,
    reason: message,
    step_type: blockedStep?.step_type || null,
    required: true,
    changes_protocol: false,
    external_data_disclosure: [],
    accepted_inputs: [],
    alternatives: [],
  };
}

export function taskTitle(task) {
  const payload = task?.request?.payload || {};
  return payload.name || payload.title || operationCopy[task?.request?.operation] || "研究任务";
}
