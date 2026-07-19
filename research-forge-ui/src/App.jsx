import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpenText,
  ChartBar,
  CheckCircle,
  CircleNotch,
  Database,
  FileText,
  FolderOpen,
  Lightbulb,
  LockKey,
  Play,
  ShieldCheck,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";

const modes = [
  {
    id: "project",
    number: "01",
    label: "项目生成论文",
    short: "项目生成论文",
    description: "已有项目、数据或结论",
    icon: FolderOpen,
  },
  {
    id: "idea",
    number: "02",
    label: "端到端想法到论文",
    short: "端到端想法到论文",
    description: "只有想法，从验证开始",
    icon: Lightbulb,
  },
];

const stageDefinitions = [
  ["发现与收敛", "边界与假设"],
  ["协议与基线", "口径与对照"],
  ["实验与判定", "证据与结论"],
  ["论文与审计", "草稿与复现"],
];

const statusCopy = {
  supported: "想法得到支持",
  refuted: "想法未获支持",
  mixed: "混合结论",
  inconclusive: "证据不足",
  unverifiable: "暂不可验证",
};

const maturityCopy = {
  prospective_blind: "前瞻盲测证据",
  retrospective: "回顾性证据",
  mixed_or_unspecified: "证据成熟度待确认",
};

const fallbackCandidate = {
  track_id: "exit-signal-research-v1",
  novelty_seed: "检验哪些时点信号可以减少 20 日极端赢家的错误退出。",
  paperability_score: 140,
  evidence_maturity: "retrospective",
  artifact_chain_complete: true,
  protocol_bound_to_output: true,
  blockers: ["证据尚不是前瞻盲测验证"],
};

function api(path, options = {}) {
  return fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  }).then(async (response) => {
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
    return payload;
  });
}

function researchTitle(trackId = "") {
  if (trackId.includes("derived-materials")) return "项目资料派生研究";
  if (trackId.includes("exit-signal")) return "极端赢家退出信号研究";
  if (trackId.includes("ranker-v2")) return "极端赢家识别器 V2";
  if (trackId.includes("expanded-robustness")) return "极端赢家扩展稳健性研究";
  if (trackId.includes("winner-validation")) return "极端赢家识别效度研究";
  if (trackId.includes("quality-timing")) return "质量与择时稳健性研究";
  if (trackId.includes("flexible-exit-portfolio-recalculation")) return "灵活退出组合重算研究";
  if (trackId.includes("baseline-freeze")) return "极端赢家基线锁定研究";
  if (trackId.includes("model-implementation-freeze")) return "极端赢家模型复现一致性研究";
  if (trackId.includes("causal-topic-selection")) return "因果选题筛选研究";
  if (trackId.includes("flexible-exit-model")) return "灵活退出模型研究";
  if (trackId.includes("flexible-exit-v1")) return "灵活退出策略研究";
  if (trackId.includes("extreme-winner-ranker-v1")) return "极端赢家排序器 V1";
  if (trackId.includes("traditional-quality-filter")) return "传统质量过滤效度研究";
  return trackId ? "待归类研究方向" : "待选择研究方向";
}

function uniquePaperDirections(candidates = [], preferredTrackId = "") {
  const groups = new Map();
  candidates.forEach((candidate) => {
    const isQualityTimingFamily = (candidate.track_id || "").includes("quality-timing");
    const rawTitle = candidate.display_title || "";
    const exposesInternalEnglishId = /[a-z]{3,}(?:-[a-z0-9]+)+/i.test(rawTitle);
    const displayTitle = isQualityTimingFamily || exposesInternalEnglishId
      ? researchTitle(candidate.track_id)
      : rawTitle || researchTitle(candidate.track_id);
    const groupKey = displayTitle === "待归类研究方向" ? candidate.track_id : displayTitle;
    const group = groups.get(groupKey) || { displayTitle, items: [] };
    group.items.push(candidate);
    groups.set(groupKey, group);
  });
  return [...groups.values()].map(({ displayTitle, items }) => {
    const preferred = items.find((item) => item.track_id === preferredTrackId);
    const strongest = [...items].sort((left, right) => (right.paperability_score || 0) - (left.paperability_score || 0))[0];
    return {
      ...(preferred || strongest),
      display_title: displayTitle,
      variant_count: items.length,
      variant_track_ids: items.map((item) => item.track_id),
    };
  });
}

function researchDescription(candidate) {
  if (!candidate) return "分析项目后，系统会从现有数据、代码和结论中收敛一个可验证的论文问题。";
  const trackId = candidate.track_id || "";
  if (candidate.source_mode === "derived_materials") {
    return candidate.novelty_seed || "从项目文本与结构化材料中收敛研究问题，并明确下一步需要补齐的验证证据。";
  }
  if (trackId.includes("exit-signal")) {
    return "识别并验证在极端赢家出现前，其竞争对手的退出信号是否具有可观测、可复现与可迁移的预测力。";
  }
  if (trackId.includes("ranker-v2")) return "检验第二版极端赢家识别器能否在冻结口径下稳定改善排序质量与跨阶段泛化。";
  if (trackId.includes("expanded-robustness")) return "扩展市场阶段、持有周期与样本边界，检验极端赢家识别结论的稳健性。";
  if (trackId.includes("winner-validation")) return "验证极端赢家标签与识别器输出是否真正对应可重复、可交易的后续收益差异。";
  if (trackId.includes("quality-timing")) return "检验质量因子与入场时点的组合，在不同年份和市场阶段是否保持一致的解释力。";
  if (trackId.includes("flexible-exit-portfolio-recalculation")) return "重新计算灵活退出规则在组合层面的收益、回撤与换手影响，避免只看单笔退出。";
  if (trackId.includes("baseline-freeze")) return "锁定极端赢家研究的基线、样本与评价口径，为后续模型改动提供可复现对照。";
  if (trackId.includes("model-implementation-freeze")) return "验证冻结模型实现后，训练、推理与评估结果能否在同一协议下稳定复现。";
  if (trackId.includes("causal-topic-selection")) return "从项目证据中筛选具备明确干预、对照与可证伪结果的因果研究问题。";
  if (trackId.includes("flexible-exit-model")) return "评估灵活退出模型能否减少过早卖出，同时控制回撤、换手与规则复杂度。";
  if (trackId.includes("flexible-exit-v1")) return "检验第一版灵活退出策略相对固定持有期规则的增量价值与失败边界。";
  if (trackId.includes("extreme-winner-ranker-v1")) return "评估第一版极端赢家排序器的识别能力，并作为后续版本的冻结对照。";
  if (trackId.includes("traditional-quality-filter")) return "检验传统质量过滤条件是否能在不牺牲极端赢家召回的前提下改善候选质量。";
  const seed = candidate.novelty_seed || "";
  return seed && !/[a-z]{4,}/i.test(seed) ? seed : "由项目现有证据自动收敛得到的候选研究问题。";
}

function Notice({ notice, error }) {
  if (!notice && !error) return null;
  return (
    <div className={`notice ${error ? "is-error" : "is-success"}`} role="status">
      {error ? <WarningCircle size={18} /> : <CheckCircle size={18} weight="fill" />}
      <span>{error || notice}</span>
    </div>
  );
}

function GlobalHeader() {
  return (
    <header className="global-header">
      <div className="brand">
        <strong>Research Forge</strong>
        <b>研究工作台</b>
      </div>
      <div className="engine-status"><CheckCircle size={15} weight="fill" /><span>研究引擎已就绪</span></div>
    </header>
  );
}

function ModeSelector({ active, onChange }) {
  return (
    <section className="mode-selector" aria-labelledby="mode-selector-title">
      <div className="mode-selector-intro">
        <strong id="mode-selector-title">选择开始方式</strong>
        <span>项目模式为默认入口</span>
      </div>
      <nav className="mode-tabs" aria-label="论文生成方式">
        {modes.map((mode) => {
          const Icon = mode.icon;
          return (
            <button
              type="button"
              key={mode.id}
              className={active === mode.id ? "is-active" : ""}
              onClick={() => onChange(mode.id)}
              aria-pressed={active === mode.id}
            >
              <Icon size={18} weight={active === mode.id ? "fill" : "regular"} />
              <span className="mode-copy">
                <strong>{mode.short}</strong>
              </span>
            </button>
          );
        })}
      </nav>
    </section>
  );
}

function StageStrip({ stages, completed = false }) {
  return (
    <div className="stage-strip" aria-label="四阶段研究流程">
      {stageDefinitions.map(([title, caption], index) => {
        const stage = stages?.[index];
        const ready = completed || stage?.status === "ready" || stage?.status === "completed";
        return (
          <div className={`stage-step ${ready ? "is-ready" : ""}`} key={title}>
            <span>{index + 1}</span>
            <div><strong>{title}</strong><small>{caption}</small></div>
          </div>
        );
      })}
    </div>
  );
}

function WindowHeader({ number, eyebrow, title, description }) {
  return (
    <header className="window-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="window-description">{description}</p>
      </div>
    </header>
  );
}

function IdeaWindow({ busy, ideaResult, onStart, onOpenPath, notice, error }) {
  const [title, setTitle] = useState("");
  const [idea, setIdea] = useState("");

  return (
    <section className="business-window" aria-labelledby="idea-title">
      <WindowHeader
        number="02"
        eyebrow="从想法开始"
        title="先验证想法，再写论文。"
        description="系统先把想法收敛成可检验的问题并跑完证据闭环。只有结论站得住，才进入论文撰写。"
      />

      <form className="primary-form idea-form" onSubmit={(event) => { event.preventDefault(); onStart({ title, idea }); }}>
        <label htmlFor="idea-input">你的研究想法</label>
        <textarea
          id="idea-input"
          value={idea}
          onChange={(event) => setIdea(event.target.value)}
          placeholder="例如：竞争对手的异常退出，能否提前预测一只股票将成为极端赢家？"
          rows={5}
        />
        <div className="form-footer">
          <input
            aria-label="研究任务名称，可选"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="给这项研究起个名字（可选）"
          />
          <button type="submit" className="primary-action" disabled={busy === "idea" || idea.trim().length < 12}>
            {busy === "idea" ? <CircleNotch className="spinner" size={19} /> : <Play size={18} weight="fill" />}
            {busy === "idea" ? "正在创建研究任务" : "开始端到端研究"}
          </button>
        </div>
        <small>提交后先进入“发现与收敛”。系统不会把任务创建误报成想法已验证。</small>
      </form>

      <Notice notice={notice} error={error} />

      {ideaResult ? (
        <article className="result-panel idea-result">
          <div className="result-heading">
            <span className="result-seal"><CheckCircle size={24} weight="fill" /></span>
            <div><small>端到端任务已创建</small><h2>{ideaResult.title}</h2></div>
            <button type="button" className="text-action" onClick={() => onOpenPath(ideaResult.project_path)}><FolderOpen size={18} />打开任务</button>
          </div>
          <p>{ideaResult.message} 当前真实门槛是：{ideaResult.next_gate}。</p>
          <StageStrip stages={ideaResult.stages} />
          <code className="path-line">{ideaResult.project_path}</code>
        </article>
      ) : (
        <div className="workflow-preview">
          <div><span>系统会替你完成</span><strong>收敛边界、验证想法、形成结论、撰写论文</strong></div>
          <StageStrip />
        </div>
      )}

      <p className="gate-note"><ShieldCheck size={17} />闭环完成不等于论文已可用；想法判定、工作稿、论文深度与发表门槛会分别显示。</p>
    </section>
  );
}

const diagnosticOwnerCopy = {
  idea_validation: "想法验证阶段",
  evidence_packaging: "证据整理阶段",
  literature_grounding: "文献研究阶段",
  paper_writer: "论文写作阶段",
  none: "无",
};

const stageDashboardCopy = [
  {
    id: "stage_1_discovery",
    title: "发现与收敛",
    caption: "找出真正值得验证的问题",
    detail: "读取项目报告、结果与实现线索，收敛论文边界和可证伪假设。",
  },
  {
    id: "stage_2_protocol",
    title: "协议与基线",
    caption: "冻结口径、对照与数据边界",
    detail: "把协议、实现和测试绑定到同一套评价口径，避免事后改变问题。",
  },
  {
    id: "stage_3_experimentation",
    title: "实验与判定",
    caption: "让数值证据决定想法结论",
    detail: "读取机器实验结果并检查稳健性，明确支持、反驳或混合结论。",
  },
  {
    id: "stage_4_synthesis",
    title: "论文与审计",
    caption: "只写证据允许写出的结论",
    detail: "把工作稿、数值与研究边界绑定，并独立检查论文补全资格。",
  },
];

function evidenceValue(run, path) {
  const item = (run?.verdict?.numeric_evidence || []).find((evidence) => evidence.path === path);
  return Number.isFinite(item?.value) ? item.value : null;
}

function formatPercent(value, digits = 2) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "待计算";
}

function resourceLabel(reason = "") {
  if (reason.includes("machine-readable")) return "机器实验结果";
  if (reason.includes("frozen protocol") || reason.includes("Binds interpretation")) return "冻结协议与解释口径";
  if (reason.includes("executable or test")) return "实现或测试证据";
  if (reason.includes("project-authored")) return "项目报告与限制";
  if (reason.includes("numerical evidence")) return "论文数值证据";
  if (reason.includes("manuscript scope")) return "论文边界约束";
  if (reason.includes("leakage") || reason.includes("control constraints")) return "泄漏与协议控制";
  return "阶段输入材料";
}

function ResearchDashboard({ run }) {
  const [activeStageId, setActiveStageId] = useState("stage_3_experimentation");
  const paperPlan = run?.paper_expansion_plan || {};
  const stages = run?.stages || [];
  const activeDefinition = stageDashboardCopy.find((stage) => stage.id === activeStageId) || stageDashboardCopy[0];
  const activeStage = stages.find((stage) => stage.id === activeStageId) || stages[0] || {};
  const resources = activeStage.resources || [];
  const totalResources = stages.reduce((total, stage) => total + (stage.resource_count || 0), 0);
  const evidenceCount = paperPlan.numeric_evidence_count ?? run?.verdict?.numeric_evidence?.length ?? 0;
  const claimCount = run?.claims?.length || 0;
  const dynamicReturn = evidenceValue(run, "selectedEvaluation.dynamic.compoundMonthlyReturn");
  const fixedReturn = evidenceValue(run, "selectedEvaluation.fixed20.compoundMonthlyReturn");
  const positiveProbability = evidenceValue(run, "selectedEvaluation.pairedBootstrap.probabilityPositive");
  const returnDelta = Number.isFinite(dynamicReturn) && Number.isFinite(fixedReturn) ? dynamicReturn - fixedReturn : null;
  const chartMax = Math.max(dynamicReturn || 0, fixedReturn || 0, 0.5);
  const gates = [
    {
      label: "想法成立",
      passed: Boolean(paperPlan.idea_gate_passed),
      value: paperPlan.idea_gate_passed ? "已通过" : statusCopy[run?.verdict?.status || run?.idea_status] || "未通过",
      note: "需要干净的前瞻验证",
    },
    {
      label: "证据可写",
      passed: Boolean(paperPlan.evidence_gate_passed),
      value: `${evidenceCount} 条数值证据`,
      note: paperPlan.evidence_gate_passed ? "协议与输出已冻结" : "数量够，证据成熟度不够",
    },
    {
      label: "文献已冻结",
      passed: Boolean(paperPlan.literature_gate_passed),
      value: `${paperPlan.verified_paper_count || 0}/15 篇`,
      note: paperPlan.literature_gate_passed ? "来源已核验" : "尚未形成冻结文献清单",
    },
  ];

  useEffect(() => {
    if (!stages.some((stage) => stage.id === activeStageId)) {
      setActiveStageId(stages[0]?.id || "stage_1_discovery");
    }
  }, [run?.name, stages, activeStageId]);

  return (
    <section className="research-dashboard" aria-labelledby="research-dashboard-title">
      <div className="dashboard-heading">
        <div>
          <span className="dashboard-eyebrow">证据诊断</span>
          <h3 id="research-dashboard-title">研究闭环总览</h3>
          <p>不是只展示“完成了什么”，而是把资源如何进入判定、论文为什么被门槛拦住直接展开。</p>
        </div>
        <dl className="dashboard-totals" aria-label="研究资产统计">
          <div><dt>阶段资源</dt><dd>{totalResources}</dd></div>
          <div><dt>数值证据</dt><dd>{evidenceCount}</dd></div>
          <div><dt>论文主张</dt><dd>{claimCount}</dd></div>
        </dl>
      </div>

      <div className="stage-map" aria-label="四阶段资源流">
        {stageDashboardCopy.map((definition, index) => {
          const stage = stages.find((item) => item.id === definition.id) || {};
          const selected = definition.id === activeStageId;
          return (
            <button
              type="button"
              key={definition.id}
              className={selected ? "is-active" : ""}
              aria-pressed={selected}
              onClick={() => setActiveStageId(definition.id)}
            >
              <span className="stage-map-number">{index + 1}</span>
              <span className="stage-map-copy"><strong>{definition.title}</strong><small>{definition.caption}</small></span>
              <b>{stage.resource_count || 0}<small> 项</small></b>
            </button>
          );
        })}
      </div>

      <div className="dashboard-content">
        <section className="resource-explorer" aria-live="polite">
          <div className="panel-title-row">
            <div><span>当前查看</span><h4>{activeDefinition.title}</h4></div>
            <strong>{activeStage.resource_count || 0} 项资源</strong>
          </div>
          <p>{activeDefinition.detail}</p>
          <ul className="resource-list">
            {resources.slice(0, 5).map((resource) => (
              <li key={`${activeStage.id}-${resource.source_path}`}>
                <Database size={17} aria-hidden="true" />
                <span><strong>{resource.source_path}</strong><small>{resourceLabel(resource.reason)}</small></span>
              </li>
            ))}
          </ul>
          {resources.length > 5 ? <small className="resource-overflow">还有 {resources.length - 5} 项已绑定资源保留在阶段快照中</small> : null}
        </section>

        <section className="gate-board" aria-labelledby="gate-board-title">
          <div className="panel-title-row">
            <div><span>论文补全资格</span><h4 id="gate-board-title">三道门，缺一不可</h4></div>
            <LockKey size={22} aria-hidden="true" />
          </div>
          <div className="gate-list">
            {gates.map((gate) => (
              <div className={gate.passed ? "is-passed" : "is-blocked"} key={gate.label}>
                {gate.passed ? <CheckCircle size={21} weight="fill" /> : <XCircle size={21} weight="fill" />}
                <span><strong>{gate.label}</strong><small>{gate.note}</small></span>
                <b>{gate.value}</b>
              </div>
            ))}
          </div>
          <div className="next-action-callout">
            <span>当前责任边界</span>
            <strong>{diagnosticOwnerCopy[paperPlan.diagnostic_owner] || "前置研究阶段"}</strong>
            <small>{paperPlan.next_action || "完成当前阶段后，系统才会把责任交给论文写作代理。"}</small>
          </div>
        </section>
      </div>

      <section className="evidence-comparison" aria-labelledby="evidence-comparison-title">
        <div className="evidence-explanation">
          <span><ChartBar size={17} />关键证据对比</span>
          <h4 id="evidence-comparison-title">灵活退出只领先 {Number.isFinite(returnDelta) ? `${(returnDelta * 100).toFixed(2)} 个百分点` : "有限幅度"}</h4>
          <p>收益略高不等于想法成立。配对 Bootstrap 显示正向优势概率为 {formatPercent(positiveProbability)}，当前证据只能支持“继续观察”，不能替换冻结的 20 日规则。</p>
        </div>
        <div className="evidence-bars">
          <label><span><b>固定 20 日</b><strong>{formatPercent(fixedReturn)}</strong></span><progress max={chartMax} value={fixedReturn || 0} /></label>
          <label><span><b>灵活退出</b><strong>{formatPercent(dynamicReturn)}</strong></span><progress max={chartMax} value={dynamicReturn || 0} /></label>
          <label className="probability-bar"><span><b>优势概率</b><strong>{formatPercent(positiveProbability)}</strong></span><progress max="1" value={positiveProbability || 0} /></label>
        </div>
      </section>
    </section>
  );
}

function VerdictResult({ run, busy, onExpandPaper, onOpenPath }) {
  const verdict = run?.verdict || {};
  const audit = run?.audit || {};
  const paperPlan = run?.paper_expansion_plan || {};
  const paperAudit = run?.paper_expansion_audit || {};
  const depth = run?.manuscript_depth || {};
  const fullDepth = run?.full_manuscript_depth || {};
  const status = statusCopy[verdict.status] || statusCopy[run?.idea_status] || "尚未判定";
  const workingPaperPath = run?.path ? `${run.path}\\stage_4_synthesis\\manuscript.md` : "";
  const fullPaperPath = run?.path && (paperAudit.full_manuscript_generated || run?.full_manuscript_generated)
    ? `${run.path}\\stage_4_synthesis\\full_manuscript.md`
    : "";
  const expansionReady = Boolean(paperPlan.ready || run?.paper_expansion_ready);
  const fullGenerated = Boolean(paperAudit.full_manuscript_generated || run?.full_manuscript_generated);
  const fullReady = Boolean(paperAudit.paper_draft_ready || run?.paper_draft_ready);
  const cards = [
    ["想法判定", status, Boolean(verdict.idea_validated || run?.idea_validated)],
    ["闭环工作稿", audit.pilot_draft_generated || run?.pilot_draft_generated ? "已生成" : "未生成", Boolean(audit.pilot_draft_generated || run?.pilot_draft_generated)],
    ["论文补全资格", expansionReady ? "已解锁" : "前置门槛未过", expansionReady],
    ["完整论文", fullReady ? "通过深度与引用审计" : fullGenerated ? "已写出，审计未过" : "尚未补写", fullReady],
    ["产物审计", audit.passed || run?.audit_passed ? "已通过" : "未通过", Boolean(audit.passed || run?.audit_passed)],
  ];
  const publicationNotes = [];
  if (!fullGenerated) {
    const depthCount = Number.isFinite(depth.total_count) ? `${depth.total_count} ${depth.unit === "han_chars" ? "个汉字" : "词"}` : "短篇幅";
    publicationNotes.push(`当前只有 ${depthCount}的闭环工作稿，完整论文尚未启动`);
  } else if (!fullReady) {
    const depthCount = Number.isFinite(fullDepth.total_count) ? `${fullDepth.total_count} ${fullDepth.unit === "han_chars" ? "个汉字" : "词"}` : "当前篇幅";
    publicationNotes.push(`完整候选稿为 ${depthCount}，但深度、引用或证据绑定审计仍未全部通过`);
  }
  if (verdict.protocol_bound_to_output === false) {
    publicationNotes.push("项目材料缺少冻结协议与结果的精确绑定");
  }
  if (verdict.evidence_maturity === "retrospective") {
    publicationNotes.push("证据仍为回顾性，尚未完成前瞻盲测");
  } else if (verdict.evidence_maturity === "mixed_or_unspecified") {
    publicationNotes.push("证据成熟度尚未确认，尚未完成前瞻盲测");
  }
  if (!paperPlan.literature_gate_passed) {
    publicationNotes.push(`冻结核验文献 ${paperPlan.verified_paper_count || 0}/15`);
  }
  return (
    <article className="result-panel verdict-result">
      <div className="result-heading">
        <span className="result-seal"><FileText size={24} weight="fill" /></span>
        <div><small>项目论文闭环结果</small><h2>{run?.scope?.title || researchTitle(run?.track_id)}</h2></div>
        {fullPaperPath ? <button type="button" className="text-action" onClick={() => onOpenPath(fullPaperPath)}><BookOpenText size={18} />打开完整论文</button> : workingPaperPath ? <button type="button" className="text-action" onClick={() => onOpenPath(workingPaperPath)}><BookOpenText size={18} />打开工作稿</button> : null}
      </div>
      <div className="verdict-grid">
        {cards.map(([label, value, passed]) => (
          <div key={label}><span>{label}</span><strong className={passed ? "is-passed" : ""}>{value}</strong></div>
        ))}
      </div>
      <ResearchDashboard run={run} />
      <p className="verdict-summary">{verdict.conclusion || "四阶段证据、想法判定与闭环工作稿已经绑定到同一条可审计链路。"}</p>
      <div className={`expansion-gate ${expansionReady ? "is-ready" : "is-blocked"}`}>
        <div>
          <span>论文补全阶段</span>
          <strong>{fullReady ? "完整论文已通过审计" : expansionReady ? "想法与资料已就绪" : `当前责任边界：${diagnosticOwnerCopy[paperPlan.diagnostic_owner] || "前置研究阶段"}`}</strong>
          <small>{expansionReady ? "系统将调用写作代理生成完整稿，并单独检查引用、数值、结论和篇幅。" : (paperPlan.blockers || []).join("；") || "请先完成想法验证、证据绑定与文献冻结。"}</small>
        </div>
        {!fullGenerated ? (
          <button type="button" className="primary-action" onClick={onExpandPaper} disabled={!expansionReady || busy === "expand"}>
            {busy === "expand" ? <CircleNotch className="spinner" size={18} /> : <BookOpenText size={18} weight="fill" />}
            {busy === "expand" ? "正在补写完整论文" : expansionReady ? "补写完整论文" : "等待前置门槛"}
          </button>
        ) : null}
      </div>
      <div className="publication-line">
        <span>发表状态</span>
        <strong>{audit.publication_ready || run?.publication_ready ? "达到发表门槛" : "仍有发表前工作"}</strong>
        <small>{publicationNotes.length ? `${publicationNotes.join("；")}。` : "请查看审计材料中的阻断项。"}</small>
      </div>
    </article>
  );
}

function ProjectWindow({ source, setSource, inspection, candidates, candidate, selectedTrack, setSelectedTrack, run, busy, onChooseFolder, onInspect, onCloseLoop, onExpandPaper, onOpenPath, notice, error }) {
  const rawCandidateCount = inspection?.candidates?.length || 0;
  const mergedCandidateCount = Math.max(0, rawCandidateCount - candidates.length);
  return (
    <section className="business-window" aria-labelledby="project-title">
      <WindowHeader
        number="01"
        eyebrow="项目生成论文 · 默认"
        title="把已有项目，变成一篇可信的论文。"
        description="放入项目包。系统会读取现有数据、实验与结论，收敛最值得写的研究问题，再生成证据约束的论文。"
      />

      <form className="primary-form project-form" onSubmit={(event) => { event.preventDefault(); onInspect(); }}>
        <label htmlFor="project-path">项目文件夹 / 文本资料库</label>
        <div className="project-input-row">
          <div className="path-input"><FolderOpen size={20} /><input id="project-path" value={source} onChange={(event) => setSource(event.target.value)} placeholder="粘贴项目文件夹路径" /></div>
          <button type="button" className="secondary-action" onClick={onChooseFolder} disabled={Boolean(busy)}>
            {busy === "folder" ? <CircleNotch className="spinner" size={18} /> : <FolderOpen size={18} />}
            {busy === "folder" ? "等待选择" : "选择文件夹"}
          </button>
          <button type="submit" className="primary-action" disabled={Boolean(busy) || !source.trim()}>
            {busy === "inspect" ? <CircleNotch className="spinner" size={19} /> : <ArrowRight size={18} />}
            {busy === "inspect" ? "正在分析项目" : "分析项目"}
          </button>
        </div>
        <small>点击“选择文件夹”打开系统目录选择器，也可以直接粘贴路径；分析过程只读源资料。</small>
      </form>

      <Notice notice={notice} error={error} />

      {inspection ? (
        <article className="candidate-panel">
          <div className="candidate-topline">
            <div className="candidate-summary"><span>推荐写成论文的方向</span><small>{candidates.length} 个去重方向{mergedCandidateCount ? ` · 已合并 ${mergedCandidateCount} 个重复版本` : ""}</small></div>
            {candidates.length > 1 ? (
              <label><span>切换方向</span><select value={candidate?.track_id || ""} onChange={(event) => setSelectedTrack(event.target.value)}>{candidates.map((item) => <option key={item.track_id} value={item.track_id}>{item.display_title}</option>)}</select></label>
            ) : null}
          </div>
          <div className="candidate-body">
            <div className="candidate-index">A</div>
            <div className="candidate-copy">
              <h2>{candidate?.display_title || researchTitle(candidate?.track_id)}</h2>
              <p>{researchDescription(candidate)}</p>
              {candidate?.source_mode === "derived_materials" ? <p className="variant-note">未发现完整的冻结协议链：本轮会先收敛研究问题并生成证据缺口工作稿，想法判定保持“暂不可验证”。</p> : null}
              {candidate?.variant_count > 1 ? <p className="variant-note">该论文方向已合并 {candidate.variant_count} 条内部实验轨道，运行时将采用论文潜力最高的版本。</p> : null}
              <dl>
                <div><dt>论文潜力</dt><dd>{candidate?.paperability_score || "待评估"}</dd></div>
                <div><dt>证据成熟度</dt><dd>{maturityCopy[candidate?.evidence_maturity] || "待检查"}</dd></div>
                <div><dt>项目资源</dt><dd>{inspection.resource_count || 0} 项</dd></div>
              </dl>
            </div>
            <button type="button" className="primary-action paper-action" onClick={onCloseLoop} disabled={!candidate || busy === "close"}>
              {busy === "close" ? <CircleNotch className="spinner" size={19} /> : <FileText size={18} weight="fill" />}
              {busy === "close" ? "正在生成论文" : "从项目生成论文"}
            </button>
          </div>
          <StageStrip completed={Boolean(run)} stages={run?.stages?.map(() => ({ status: "completed" }))} />
        </article>
      ) : (
        <div className="project-empty"><BookOpenText size={28} /><div><strong>先放入项目，再讨论论文</strong><span>系统会优先检查已有证据，而不是凭空包装新颖性。</span></div></div>
      )}

      {run ? <VerdictResult run={run} busy={busy} onExpandPaper={onExpandPaper} onOpenPath={onOpenPath} /> : null}
    </section>
  );
}

export function App() {
  const [active, setActive] = useState("project");
  const [source, setSource] = useState("");
  const [inspection, setInspection] = useState(null);
  const [selectedTrack, setSelectedTrack] = useState("");
  const [run, setRun] = useState(null);
  const [ideaResult, setIdeaResult] = useState(null);
  const [busy, setBusy] = useState("bootstrap");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/bootstrap")
      .then((payload) => {
        const latest = payload.latest_run;
        setSource(latest?.source_root || payload.default_source || "");
        setRun(latest || null);
        setInspection(latest?.inspection || null);
        setSelectedTrack(latest?.track_id || latest?.inspection?.recommended_track_id || "");
      })
      .catch((reason) => {
        setError(`本地服务连接失败：${reason.message}`);
        setSource("C:\\Users\\austa\\Documents\\炒股");
        setInspection({ candidates: [fallbackCandidate], recommended_track_id: fallbackCandidate.track_id, resource_count: 0 });
        setSelectedTrack(fallbackCandidate.track_id);
      })
      .finally(() => setBusy(""));
  }, []);

  useEffect(() => {
    setNotice("");
    setError("");
  }, [active]);

  const candidates = useMemo(
    () => uniquePaperDirections(inspection?.candidates || [], selectedTrack),
    [inspection, selectedTrack],
  );

  const candidate = useMemo(() => {
    return candidates.find((item) => item.track_id === selectedTrack) || candidates[0] || null;
  }, [candidates, selectedTrack]);

  async function chooseProjectFolder() {
    setBusy("folder");
    setError("");
    setNotice("");
    try {
      const result = await api("/api/select-folder", { method: "POST", body: JSON.stringify({ initial: source }) });
      if (!result.cancelled && result.path) {
        setSource(result.path);
        setInspection(null);
        setRun(null);
        setSelectedTrack("");
        setNotice("文件夹已选择。点击“分析项目”后，系统会读取其中的项目材料与文本资料。");
      }
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy("");
    }
  }

  async function startIdea({ title, idea }) {
    setBusy("idea");
    setError("");
    setNotice("");
    try {
      const result = await api("/api/idea/start", { method: "POST", body: JSON.stringify({ title, idea }) });
      setIdeaResult(result);
      setNotice("研究任务已真实创建；系统已记录四阶段状态与下一道证据门槛。");
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy("");
    }
  }

  async function inspectProject() {
    setBusy("inspect");
    setError("");
    setNotice("");
    try {
      const nextInspection = await api("/api/inspect", { method: "POST", body: JSON.stringify({ source }) });
      const nextDirections = uniquePaperDirections(nextInspection.candidates || [], nextInspection.recommended_track_id || "");
      setInspection(nextInspection);
      setSelectedTrack(nextDirections.find((item) => item.track_id === nextInspection.recommended_track_id)?.track_id || nextDirections[0]?.track_id || "");
      setRun(null);
      setNotice(`分析完成：从 ${nextInspection.resource_count || 0} 项资源中提炼出 ${nextDirections.length} 个不重复的论文方向。`);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy("");
    }
  }

  async function closeLoop() {
    setBusy("close");
    setError("");
    setNotice("");
    try {
      const completedRun = await api("/api/close-loop", {
        method: "POST",
        body: JSON.stringify({ source, track_id: candidate.track_id, name: `${candidate.display_title || researchTitle(candidate.track_id)}-web` }),
      });
      setRun(completedRun);
      setInspection(completedRun.inspection);
      setSelectedTrack(completedRun.track_id);
      setNotice("四阶段闭环完成：想法判定、闭环工作稿、可用论文状态与产物审计已分别生成。");
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy("");
    }
  }

  async function expandPaper() {
    if (!run?.name) return;
    setBusy("expand");
    setError("");
    setNotice("");
    try {
      const completedRun = await api("/api/expand-paper", {
        method: "POST",
        body: JSON.stringify({ name: run.name }),
      });
      setRun(completedRun);
      setNotice(completedRun.paper_draft_ready
        ? "完整论文已生成，并通过篇幅、引用、数值与冻结结论审计。"
        : "完整论文候选稿已生成，但写作审计未通过；责任已定位到论文写作阶段。"
      );
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy("");
    }
  }

  async function openPath(path) {
    setError("");
    try {
      await api("/api/open-path", { method: "POST", body: JSON.stringify({ path }) });
      setNotice("已在本机打开对应材料。");
    } catch (reason) {
      setError(reason.message);
    }
  }

  return (
    <div className="app-shell">
      <GlobalHeader />
      <main className="workspace">
        <ModeSelector active={active} onChange={setActive} />
        {busy === "bootstrap" ? (
          <div className="loading-screen"><CircleNotch className="spinner" size={29} /><span>正在连接研究引擎……</span></div>
        ) : active === "idea" ? (
          <IdeaWindow busy={busy} ideaResult={ideaResult} onStart={startIdea} onOpenPath={openPath} notice={notice} error={error} />
        ) : (
          <ProjectWindow {...{ source, setSource, inspection, candidates, candidate, selectedTrack, setSelectedTrack, run, busy, notice, error }} onChooseFolder={chooseProjectFolder} onInspect={inspectProject} onCloseLoop={closeLoop} onExpandPaper={expandPaper} onOpenPath={openPath} />
        )}
      </main>
    </div>
  );
}
