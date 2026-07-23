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

export const operationCopy = {
  "bundle.inspect": "分析项目材料",
  "bundle.close": "执行四阶段研究闭环",
  "bundle.expand-paper": "补写完整论文",
  "bundle.remediate": "执行补证据计划",
  "idea.start": "收敛研究想法",
};

export function directionTitle(candidate = {}) {
  if (candidate.display_title) return candidate.display_title;
  const id = candidate.track_id || "";
  if (id.includes("exit-signal")) return "极端赢家退出信号研究";
  if (id.includes("ranker-v2")) return "极端赢家识别器 V2";
  if (id.includes("expanded-robustness")) return "扩展稳健性研究";
  if (id.includes("winner-validation")) return "赢家识别效度研究";
  if (id.includes("quality-timing")) return "质量与择时稳健性研究";
  if (candidate.source_mode === "derived_materials") return "从项目材料收敛研究问题";
  return "候选验证任务";
}

export function candidateQuestion(candidate = {}) {
  if (candidate.research_question) return candidate.research_question;
  const id = candidate.track_id || "";
  if (id.includes("exit-signal")) return "哪些时点信号能够减少 20 日极端赢家的错误退出？";
  if (id.includes("ranker-v2")) return "第二版排序器能否更稳定地识别极端赢家？";
  if (id.includes("expanded-robustness")) return "现有结论在扩展样本和不同市场阶段下是否仍然成立？";
  if (id.includes("winner-validation")) return "现有 Top5 规则是否真正捕捉到具有正偏收益的极端赢家？";
  if (id.includes("quality-timing-2025")) return "2025 年各月末样本中的质量与择时结论是否稳健？";
  if (id.includes("flexible-exit-portfolio-recalculation")) return "灵活退出规则能否改善冻结 Top5 组合的收益表现？";
  if (id.includes("baseline-freeze")) return "现有 34 个月证据能否作为后续极端赢家模型的可靠基线？";
  if (id.includes("model-implementation-freeze")) return "冻结模型实现后，前瞻盲测能否避免实现漂移？";
  if (id.includes("causal-topic-selection")) return "因果热门话题信号能否改善股票选择结果？";
  if (id.includes("flexible-exit-model")) return "哪些股票在买入后仍适合持有至第 20 个交易日？";
  if (id.includes("flexible-exit-v1")) return "在不改变选股模型的前提下，灵活退出是否优于固定持有？";
  if (id.includes("ranker-v1")) return "第一版排序器能否在前瞻盲测中识别 20 日极端赢家？";
  if (id.includes("traditional-quality-filter")) return "传统质量过滤能否改善候选质量且不损失极端赢家召回？";
  if (id.includes("quality-timing-v3")) return "科技质量与行业相对择时的组合是否具有稳定增量价值？";
  if (id.includes("quality-timing-v2")) return "第二版质量择时模型在样本外重测中是否仍然有效？";
  if (candidate.source_mode === "derived_materials") return "现有项目材料能够支持检验哪一个明确主张？";
  return "这个候选主张在冻结口径下能否得到证据支持？";
}

export function paperTopicTitle(candidate = {}) {
  if (candidate.topic_title) return candidate.topic_title;
  const title = directionTitle(candidate)
    .replace(/\s*[vV]\d+\b/g, "")
    .replace(/(?:回顾性|历史)?(?:实验|验证|重测)?研究?报告$/g, "")
    .replace(/验证结论$/g, "")
    .trim();
  return title ? `${title}的证据边界与验证` : "项目主张的证据边界与验证";
}

const topicFamilies = [
  {
    id: "extreme-winner-validity",
    matches: (id) => /extreme-winner-(?:ranker|expanded|validation|baseline|model-implementation)/.test(id),
    title: "极端赢家排序收益的来源：预测能力与右尾偶然性的区分",
    question: "在同候选池随机 Top5 对照下，现有排序器能否稳定提高 20 日极端赢家命中率与组合收益？",
    contribution: "区分收益改善、赢家识别能力与少数极端收益集中三种机制。",
    scope: "冻结排序器、月度截面、20 日持有结果",
    preferred: ["extreme-winner-expanded-robustness-v1", "extreme-winner-validation-v1"],
  },
  {
    id: "flexible-exit-boundary",
    matches: (id) => /(?:^|-)flexible-exit|exit-signal/.test(id),
    title: "固定持有与灵活退出的边界：极端赢家错误退出的点时信号研究",
    question: "在不改变冻结选股模型的前提下，哪些点时信号能够减少错误退出并改善组合层收益？",
    contribution: "把退出时点判断与选股能力分离，并检验赢家保护门的增量价值。",
    scope: "固定 20 日基线、点时退出信号、组合收益与回撤",
    preferred: ["exit-signal-research-v1", "flexible-exit-portfolio-recalculation-v1"],
  },
  {
    id: "quality-timing-robustness",
    matches: (id) => /quality-timing|traditional-quality-filter/.test(id),
    title: "质量因子与行业择时的样本外稳健性：训练窗口、过滤层与月度表现",
    question: "质量过滤与行业相对择时的组合，在不同训练窗口和月度样本外截面中是否保持稳定增量价值？",
    contribution: "分离质量过滤、训练窗口和择时层各自对结果的贡献。",
    scope: "滚动训练窗口、月末截面、Top5 与 Top20 组合",
    preferred: ["quality-timing-v3", "quality-timing-2025-robustness-v1"],
  },
  {
    id: "causal-topic-selection",
    matches: (id) => /causal-topic-selection/.test(id),
    title: "因果热门信号能否改善股票选择：候选池、解释层与排序器的对照研究",
    question: "因果热门信号适合作为候选池和解释层，还是能够对冻结 Top5 排序器产生可重复的增量？",
    contribution: "区分注意力信号、解释能力与实际排序增益，避免把热度当作预测证据。",
    scope: "公开时点信号、冻结五特征模型、滚动样本外测试",
    preferred: ["causal-topic-selection-v1"],
  },
];

function selectPrimaryCandidate(items, preferred = []) {
  for (const id of preferred) {
    const match = items.find((item) => item.track_id === id && item.closure_input_ready);
    if (match) return match;
  }
  return [...items].sort((left, right) =>
    Number(right.closure_input_ready) - Number(left.closure_input_ready)
    || (right.paperability_score || 0) - (left.paperability_score || 0)
  )[0];
}

export function paperTopics(candidates = []) {
  const consumed = new Set();
  const topics = [];
  topicFamilies.forEach((family) => {
    const items = candidates.filter((candidate) => family.matches(candidate.track_id || ""));
    if (!items.length) return;
    items.forEach((item) => consumed.add(item.track_id));
    const primary = selectPrimaryCandidate(items, family.preferred);
    topics.push({
      ...primary,
      topic_id: family.id,
      topic_title: family.title,
      research_question: family.question,
      contribution: family.contribution,
      scope: family.scope,
      supporting_track_ids: items.map((item) => item.track_id),
      evidence_chain_count: items.filter((item) => item.artifact_chain_complete).length,
    });
  });
  candidates.filter((candidate) => !consumed.has(candidate.track_id)).forEach((candidate) => {
    topics.push({
      ...candidate,
      topic_id: candidate.track_id,
      topic_title: paperTopicTitle(candidate),
      research_question: candidateQuestion(candidate),
      contribution: "把项目中的既有主张转化为边界明确、可以被证据推翻的研究命题。",
      scope: candidate.source_mode === "derived_materials" ? "项目现有材料与可追溯证据" : "冻结协议、输出与结论报告",
      supporting_track_ids: [candidate.track_id],
      evidence_chain_count: Number(candidate.artifact_chain_complete),
    });
  });
  return topics.sort((left, right) =>
    Number(right.closure_input_ready) - Number(left.closure_input_ready)
    || (right.paperability_score || 0) - (left.paperability_score || 0)
  );
}

export function blockerText(blocker = "") {
  if (blocker === "evidence is not a prospective blind validation") return "现有证据不是前瞻盲测结果";
  return blocker;
}

export function uniqueDirections(candidates = []) {
  return paperTopics(candidates);
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
  const message = task?.error?.message || "当前步骤没有满足继续执行的条件。";
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
