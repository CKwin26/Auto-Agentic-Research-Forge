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
  return blocker;
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
