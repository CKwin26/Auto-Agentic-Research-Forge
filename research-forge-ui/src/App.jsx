import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpenText,
  Check,
  CheckCircle,
  CircleNotch,
  ClockCounterClockwise,
  Database,
  FileArrowUp,
  FileText,
  FolderOpen,
  Gauge,
  Key,
  Lightbulb,
  ListChecks,
  LockKey,
  Pause,
  Play,
  ShieldCheck,
  SlidersHorizontal,
  Sparkle,
  WarningCircle,
  X,
  XCircle,
} from "@phosphor-icons/react";
import {
  blockerText,
  candidateQuestion,
  deriveStageState,
  inferRequirement,
  maturityCopy,
  operationCopy,
  paperTopicTitle,
  taskStatusCopy,
  taskTitle,
  taskUiState,
  uniqueDirections,
  verdictCopy,
  workflowStages,
} from "./workflow";

const initialState = {
  boot: "loading",
  mode: "project",
  view: "intake",
  source: "",
  runsRoot: "",
  ideaRoot: "",
  inspection: null,
  selectedDirectionId: "",
  activeTask: null,
  taskEvents: [],
  remediationPlan: null,
  tasks: [],
  run: null,
  ideaResult: null,
  request: { type: null, status: "idle" },
  notice: "",
  error: "",
};

function reducer(state, action) {
  switch (action.type) {
    case "BOOTSTRAP_SUCCESS":
      return {
        ...state,
        boot: "ready",
        source: action.payload.default_source || "",
        runsRoot: action.payload.runs_root || "",
        ideaRoot: action.payload.idea_root || "",
        tasks: action.payload.tasks || [],
      };
    case "BOOTSTRAP_ERROR":
      return { ...state, boot: "error", error: action.error };
    case "SET_MODE":
      return {
        ...initialState,
        boot: state.boot,
        tasks: state.tasks,
        source: state.source,
        runsRoot: state.runsRoot,
        ideaRoot: state.ideaRoot,
        mode: action.mode,
        view: "intake",
      };
    case "PATCH":
      return { ...state, ...action.patch };
    case "REQUEST":
      return {
        ...state,
        request: { type: action.name, status: action.status },
        error: action.status === "pending" ? "" : state.error,
      };
    case "TASK_UPDATED": {
      const tasks = [action.task, ...state.tasks.filter((item) => item.task_id !== action.task.task_id)];
      return { ...state, activeTask: action.task, tasks };
    }
    case "RESET":
      return {
        ...initialState,
        boot: state.boot,
        mode: "project",
        source: state.source,
        runsRoot: state.runsRoot,
        ideaRoot: state.ideaRoot,
        tasks: state.tasks,
      };
    default:
      return state;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

function requestId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function GlobalHeader({ connected, tasks, activeTask, onSelectTask }) {
  const [open, setOpen] = useState(false);
  const needsAttention = tasks.filter((task) => ["waiting_for_user", "failed", "blocked"].includes(task.status));
  return (
    <header className="global-header">
      <button className="brand" type="button" onClick={() => onSelectTask(null)}>
        <strong>Research Forge</strong>
        <span>研究工作台</span>
      </button>
      <div className="header-actions">
        <div className={`engine-status ${connected ? "is-online" : ""}`}>
          <i />
          <span>{connected ? "本地研究引擎已连接" : "正在连接本地引擎"}</span>
        </div>
        <div className="task-switcher">
          <button
            type="button"
            className="task-switcher-button"
            onClick={() => setOpen(!open)}
            aria-label={activeTask ? `切换研究任务：${taskTitle(activeTask)}` : "打开研究任务列表"}
            aria-expanded={open}
          >
            <ClockCounterClockwise size={17} />
            <span>{activeTask ? taskTitle(activeTask) : "研究任务"}</span>
            {needsAttention.length ? <b>{needsAttention.length}</b> : null}
          </button>
          {open ? (
            <div className="task-popover">
              <div className="task-popover-heading">
                <div><strong>最近研究</strong><small>{tasks.length} 个持久任务</small></div>
                <button type="button" onClick={() => setOpen(false)} aria-label="关闭任务列表"><X size={17} /></button>
              </div>
              <button className="new-task-row" type="button" onClick={() => { onSelectTask(null); setOpen(false); }}>
                <Sparkle size={18} /><span><strong>开始新的研究</strong><small>选择项目或输入想法</small></span>
              </button>
              <div className="task-list">
                {tasks.length ? tasks.slice(0, 12).map((task) => (
                  <button key={task.task_id} type="button" onClick={() => { onSelectTask(task); setOpen(false); }}>
                    <span className={`task-dot is-${task.status}`} />
                    <span><strong>{taskTitle(task)}</strong><small>{operationCopy[task.request?.operation] || task.request?.operation}</small></span>
                    <em>{taskStatusCopy[task.status] || task.status}</em>
                  </button>
                )) : <p className="task-empty">还没有研究任务。先从下面选择一种开始方式。</p>}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function ModeSelector({ mode, onChange, disabled }) {
  return (
    <section className="mode-selector">
      <div>
        <strong>选择开始方式</strong>
        <span>已有项目是默认入口</span>
      </div>
      <nav aria-label="研究开始方式">
        <button type="button" disabled={disabled} className={mode === "project" ? "is-active" : ""} onClick={() => onChange("project")}>
          <FolderOpen size={18} weight={mode === "project" ? "fill" : "regular"} />
          <span><strong>检查已有项目</strong><small>已有代码、数据或结论</small></span>
        </button>
        <button type="button" disabled={disabled} className={mode === "idea" ? "is-active" : ""} onClick={() => onChange("idea")}>
          <Lightbulb size={18} weight={mode === "idea" ? "fill" : "regular"} />
          <span><strong>验证一个想法</strong><small>从研究问题开始</small></span>
        </button>
      </nav>
    </section>
  );
}

function StageProgress({ activeStage = "discovery", compact = false }) {
  return (
    <div className={`stage-progress ${compact ? "is-compact" : ""}`} aria-label="研究阶段">
      {workflowStages.map((stage, index) => {
        const status = deriveStageState(activeStage, index);
        return (
          <div key={stage.id} className={`stage-progress-item is-${status}`}>
            <span>{status === "complete" ? <Check size={15} weight="bold" /> : stage.number}</span>
            <div><strong>{stage.title}</strong><small>{stage.caption}</small></div>
          </div>
        );
      })}
    </div>
  );
}

const stepStatusLabel = {
  queued: "等待执行",
  running: "正在执行",
  retrying: "自动重试",
  paused: "已暂停",
  waiting_for_user: "等待用户",
  blocked: "被阻止",
  failed: "执行失败",
  succeeded: "已完成",
  cancelled: "已取消",
};

const executorLabel = {
  deterministic_service: "确定性服务",
  retrieval_service: "检索服务",
  model: "语义模型",
  codex: "Codex",
  sandbox_runner: "受控实验环境",
  deterministic_evaluator: "确定性评估器",
  nli_service: "NLI 风险提示",
  ai_scientific_review_panel: "AI 科学审核面板",
  project_owner: "项目负责人",
  external: "外部步骤",
};

const stepTitle = {
  project_scan: "扫描项目资源",
  evidence_chain_detection: "识别证据链",
  scope_review: "确认研究边界",
  scope_freeze: "冻结 Scope Contract",
  scope_drafting: "起草研究边界",
  protocol_drafting: "起草 Research Contract",
  contract_review: "审核研究契约",
  contract_freeze: "冻结研究契约",
  evidence_import: "导入实验与结果",
  deterministic_validation: "验证数值与工件绑定",
  result_review: "AI 科学审核",
  claim_mapping: "建立主张—证据映射",
  manuscript_writing: "撰写论文",
  manuscript_audit: "审计论文",
  completion_record: "生成完成记录",
};

function StudyWorkflowPanel({ workflow }) {
  if (!workflow?.study || !workflow?.steps?.length) return null;
  const currentPhase = workflow.study.phase;
  const phaseMap = {
    discovery: { title: "发现与收敛", number: "01" },
    protocol: { title: "协议与基线", number: "02" },
    experiment: { title: "实验与判定", number: "03" },
    paper: { title: "论文与审计", number: "04" },
  };
  return (
    <section className="surface-card study-workflow-panel">
      <div className="card-heading">
        <div><span>持久化研究步骤</span><h2>实际完成了什么</h2></div>
        <small>{workflow.study.study_id}</small>
      </div>
      <p className="workflow-panel-note">状态来自后端 step instance，不根据日志文字或虚假百分比推算。</p>
      <div className="study-phase-list">
        {Object.entries(phaseMap).map(([phase, meta]) => {
          const steps = workflow.steps.filter((item) => item.phase === phase);
          const complete = steps.filter((item) => item.status === "succeeded").length;
          const hasAttention = steps.some((item) => ["failed", "blocked", "waiting_for_user"].includes(item.status));
          return (
            <details key={phase} open={phase === currentPhase || hasAttention}>
              <summary>
                <span>{meta.number}</span>
                <div><strong>{meta.title}</strong><small>{complete}/{steps.length} 个步骤完成</small></div>
                <em>{hasAttention ? "需要处理" : complete === steps.length && steps.length ? "已完成" : "进行中"}</em>
              </summary>
              <ol>
                {steps.map((step) => (
                  <li key={step.step_instance_id} className={`is-${step.status}`}>
                    <i>{step.status === "succeeded" ? <Check size={13} weight="bold" /> : null}</i>
                    <div>
                      <strong>{stepTitle[step.step_type] || step.step_type}</strong>
                      <small>{executorLabel[step.executor_type] || step.executor_type} · 尝试 {step.attempt} 次</small>
                      {step.blocker ? <p>{step.blocker.reason || step.blocker.message || "当前步骤被阻止"}</p> : null}
                    </div>
                    <span>{stepStatusLabel[step.status] || step.status}</span>
                  </li>
                ))}
              </ol>
            </details>
          );
        })}
      </div>
    </section>
  );
}

function Hero({ mode }) {
  return (
    <section className="hero-card">
      <div className="hero-copy">
        <span className="eyebrow">{mode === "project" ? "已有项目 · 默认" : "只有想法 · 从验证开始"}</span>
        <h1>{mode === "project" ? "先判断什么值得写，\n再生成论文。" : "先把想法变成问题，\n再让证据回答。"}</h1>
        <p>{mode === "project"
          ? "系统只读检查现有数据、代码、实验与报告，找到最值得验证的主张，并在证据不足时明确阻止扩写。"
          : "系统先收敛研究边界、冻结验证口径，再进入文献、实验和判定，不把任务创建误报成研究成功。"}</p>
      </div>
      <div className="hero-principle">
        <ShieldCheck size={24} weight="fill" />
        <span><strong>证据先于写作</strong><small>论文 Agent 无权改写实验判定</small></span>
      </div>
    </section>
  );
}

function ProjectIntake({ source, onSource, onChooseFolder, onSubmit, loading }) {
  return (
    <section className="surface-card intake-card">
      <div className="section-heading">
        <div><span>第一步</span><h2>选择要检查的项目</h2><p>源项目保持只读，生成的运行记录和证据包会写入独立目录。</p></div>
        <Database size={25} />
      </div>
      <form onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
        <label htmlFor="project-source">项目文件夹或文本资料库</label>
        <div className="path-control">
          <FolderOpen size={20} />
          <input id="project-source" value={source} onChange={(event) => onSource(event.target.value)} placeholder="选择文件夹，或粘贴本地路径" />
          <button type="button" className="secondary-button" onClick={onChooseFolder} disabled={loading}>选择文件夹</button>
        </div>
        <div className="form-actions">
          <div className="privacy-note"><LockKey size={16} /><span>只读分析 · 联网检索默认开放 · 密钥和私密文件永不外发</span></div>
          <button type="submit" className="primary-button" disabled={loading || !source.trim()}>
            {loading ? <CircleNotch className="spinner" size={18} /> : <ArrowRight size={18} />}
            {loading ? "正在创建分析任务" : "扫描项目"}
          </button>
        </div>
      </form>
    </section>
  );
}

function IdeaIntake({ onSubmit, loading }) {
  const [title, setTitle] = useState("");
  const [idea, setIdea] = useState("");
  return (
    <section className="surface-card intake-card">
      <div className="section-heading">
        <div><span>第一步</span><h2>描述你真正想验证的想法</h2><p>先写清现象和判断，不需要提前把它包装成论文标题。</p></div>
        <Lightbulb size={25} />
      </div>
      <form onSubmit={(event) => { event.preventDefault(); onSubmit({ title, idea }); }}>
        <label htmlFor="idea">研究想法</label>
        <textarea id="idea" rows={5} value={idea} onChange={(event) => setIdea(event.target.value)} placeholder="例如：竞争对手的异常退出，能否提前预测一只股票将成为极端赢家？" />
        <label htmlFor="idea-title">任务名称 <small>可选</small></label>
        <input id="idea-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="给这项研究一个容易识别的名字" />
        <div className="form-actions">
          <div className="privacy-note"><ListChecks size={16} /><span>提交后先形成研究边界，不会直接宣布想法成立</span></div>
          <button type="submit" className="primary-button" disabled={loading || idea.trim().length < 12}>
            {loading ? <CircleNotch className="spinner" size={18} /> : <ArrowRight size={18} />}
            {loading ? "正在创建任务" : "形成研究问题"}
          </button>
        </div>
      </form>
    </section>
  );
}

const activityCopy = {
  "bundle.inspect": {
    action: "读取项目材料，识别可以独立验证的研究问题",
    detail: "正在整理数据、代码、实验结果和已有结论之间的对应关系。",
    reason: "先确认材料真正支持什么，避免把工程任务或一句结论误当成选题。",
    output: "候选选题、证据基础与仍然缺失的条件",
    next: "请你选择一个候选选题并确认研究边界",
  },
  "bundle.close": {
    action: "按冻结的研究契约执行四阶段研究闭环",
    detail: "正在连接协议、实验产物、研究判定和论文资格，检查它们是否相互一致。",
    reason: "只有证据、规则和结论彼此绑定，系统才能给出可复核的研究判定。",
    output: "研究判定、证据门状态和可审计工作稿",
    next: "完成后进入研究判定，而不是直接宣布论文成立",
  },
  "bundle.expand-paper": {
    action: "在研究判定允许的范围内扩写论文",
    detail: "正在把已通过审计的证据组织成论文结构，不改写实验判定。",
    reason: "让每一项论文主张都能回到对应的实验、协议和证据。",
    output: "受证据边界约束的论文稿",
    next: "完成后检查引用、主张和证据是否逐项对应",
  },
  "bundle.remediate": {
    action: "执行已确认的补证据计划",
    detail: "正在处理用户选中的证据任务，并为新增证据创建独立后继运行。",
    reason: "证据不足是继续研究的入口，不能靠扩写文字绕过。",
    output: "补充证据、后继运行和新的研究判定",
    next: "重新判定；若仍不足则生成下一轮计划",
  },
  "idea.start": {
    action: "把研究想法收敛成可验证的问题",
    detail: "正在明确对象、判断标准、研究边界和下一步需要的材料。",
    reason: "先形成可证伪的问题，后续研究才知道该收集什么证据。",
    output: "研究问题、验证边界与初始证据需求",
    next: "进入发现阶段并补齐研究材料",
  },
};

const taskEventCopy = {
  submitted: ["任务已进入队列", "研究参数已经保存，等待本地研究引擎接手。"],
  started: ["开始执行", "研究引擎已读取本次任务和冻结参数。"],
  resumed: ["继续执行", "已从安全检查点恢复研究。"],
  pause_requested: ["已收到暂停请求", "正在完成当前不可中断步骤，到安全检查点后暂停。"],
  paused: ["已安全暂停", "当前进度和已有产物已经保存。"],
  paused_at_checkpoint: ["已在检查点暂停", "本阶段产物已经保存，可以稍后继续。"],
  waiting_for_user: ["需要用户补充条件", "研究已停止推进，等待数据、环境、配置、API 或授权。"],
  requirement_resolved: ["所需条件已补充", "系统已记录用户提供的条件，可以继续研究。"],
  protocol_change_required: ["需要更新研究契约", "新增条件会改变研究设计，不能沿用原判定。"],
  succeeded: ["执行完成", "产物已保存，正在进入下一道研究判定。"],
  failed: ["执行未完成", "失败原因和已完成工作已经记录。"],
  retrying: ["正在自动重试", "遇到临时网络或服务错误；系统最多自动重试三次。"],
  blocked: ["研究已阻止", "当前条件不允许继续推进。"],
};

function RunningState({ task, events = [], onPause, onResume }) {
  const status = task?.status || "pending";
  const operation = task?.request?.operation;
  const paused = status === "paused";
  const pauseRequested = status === "pause_requested";
  const latestProgress = [...events].reverse().find((item) => item.event === "progress");
  const fallbackActivity = activityCopy[operation] || {
    action: operationCopy[operation] || "执行当前研究任务",
    detail: "研究引擎正在处理当前步骤，并持续保存任务状态。",
    reason: "完成当前步骤后，系统才能判断下一步是否可以继续。",
    output: "当前阶段的研究记录与产物",
    next: "根据研究结果进入下一阶段或请求用户补充条件",
  };
  const activity = latestProgress ? {
    action: latestProgress.title || fallbackActivity.action,
    detail: latestProgress.detail || fallbackActivity.detail,
    reason: latestProgress.reason || fallbackActivity.reason,
    output: latestProgress.output || fallbackActivity.output,
    next: latestProgress.next || fallbackActivity.next,
  } : fallbackActivity;
  const stage = latestProgress?.stage || ({
    stage_1_discovery: "discovery",
    stage_2_protocol: "protocol",
    stage_3_experimentation: "experimentation",
    stage_4_synthesis: "synthesis",
  })[task?.stage] || (operation === "bundle.close" ? "synthesis" : "discovery");
  const visibleEvents = events.slice(-6).reverse();
  return (
    <section className="workflow-state running-state">
      <div className="state-hero compact-hero">
        <span className="state-icon"><CircleNotch className={paused ? "" : "spinner"} size={27} /></span>
        <div>
          <p className="eyebrow">{taskStatusCopy[status] || status}</p>
          <h1>{paused ? "研究已在安全检查点暂停。" : pauseRequested ? "正在到达安全检查点。" : operationCopy[operation] || "正在执行研究任务"}</h1>
          <p>{paused ? "当前产物已经保存。恢复后会从检查点继续，不重复已经完成的工作。" : activity.action}</p>
        </div>
        <div className="state-actions">
          {paused ? <button className="primary-button" type="button" onClick={onResume}><Play size={18} weight="fill" />恢复研究</button>
            : <div className="pause-control"><button className="secondary-button" type="button" onClick={onPause} disabled={pauseRequested || status !== "running"}><Pause size={18} />{pauseRequested ? "正在安全暂停" : "暂停研究"}</button><small>到检查点后暂停，已完成产物不会丢失</small></div>}
        </div>
      </div>
      <StageProgress activeStage={stage} />
      <div className="execution-observability" aria-live="polite">
        <article className="surface-card live-work-card">
          <div className="live-work-heading"><span><i />当前工作摘要</span><small>展示可审计的工作说明，不展示内部思维过程</small></div>
          <h2>{paused ? "研究已暂停，等待你决定是否继续" : pauseRequested ? "正在完成当前步骤并前往安全检查点" : activity.action}</h2>
          <p>{paused ? "恢复后会从已经保存的检查点继续。" : activity.detail}</p>
          <dl className="work-summary-grid">
            <div><dt>为什么做</dt><dd>{activity.reason}</dd></div>
            <div><dt>完成后得到</dt><dd>{activity.output}</dd></div>
            <div><dt>下一步</dt><dd>{activity.next}</dd></div>
          </dl>
          {!paused ? <div className="pulse-track"><i /></div> : null}
        </article>
        <article className="surface-card activity-log-card">
          <div className="activity-log-heading"><div><span>执行记录</span><h3>研究引擎刚刚做了什么</h3></div><em>{paused ? "已暂停" : "自动更新"}</em></div>
          <ol>
            {visibleEvents.length ? visibleEvents.map((item, index) => {
              const copy = item.event === "progress"
                ? [item.title || "研究步骤更新", item.detail || "当前步骤已记录。"]
                : taskEventCopy[item.event] || [item.event, taskStatusCopy[item.status] || item.status];
              return <li key={`${item.recorded_at}-${item.event}-${index}`} className={index === 0 ? "is-latest" : ""}><i /><div><strong>{copy[0]}</strong><p>{copy[1]}</p></div><time>{formatTime(item.recorded_at)}</time></li>;
            }) : <li className="is-latest"><i /><div><strong>正在读取任务状态</strong><p>执行记录将在研究引擎开始工作后显示。</p></div><time>刚刚</time></li>}
          </ol>
          <div className="task-meta"><span>任务 {task?.task_id}</span><span>尝试 {task?.attempt || 0} 次</span><span>更新于 {formatTime(task?.updated_at)}</span></div>
        </article>
      </div>
    </section>
  );
}

function DirectionSelect({ inspection, selectedId, onSelect, onBack, onContinue }) {
  const directions = useMemo(() => uniqueDirections(inspection?.candidates || []), [inspection]);
  const discovery = inspection?.claim_discovery;
  const signals = discovery?.trend_signals || [];
  const wechatSignals = signals.filter((item) => item.provider === "redfox_wechat").length;
  const academicSignals = signals.filter((item) => ["semantic_scholar", "crossref"].includes(item.provider)).length;
  const matchedClaims = (discovery?.recommended_claims || []).filter((item) => item.matched_signal_ids?.length).length;
  const wechatReady = String(discovery?.provider_status?.redfox_wechat || "").startsWith("ok:");
  return (
    <section className="workflow-state direction-state">
      <div className="state-hero compact-hero">
        <button type="button" className="back-button" onClick={onBack}><ArrowLeft size={18} />重新选择项目</button>
        <div>
          <p className="eyebrow">项目扫描完成</p>
          <h1>基于项目证据形成 {directions.length} 个候选选题。</h1>
          <p>系统把相关实验、结论和协议合并为可以独立成文的主题；工程性冻结任务不再单独算作选题。</p>
          {discovery ? (
            <div className="trend-status-strip" aria-label="外部选题信号状态">
              <div className={wechatReady ? "is-ready" : "is-degraded"}><span>公众号热度</span><strong>{wechatReady ? `${wechatSignals} 条` : "本轮未取得"}</strong></div>
              <div className={academicSignals ? "is-ready" : "is-degraded"}><span>学术关注</span><strong>{academicSignals} 条</strong></div>
              <div><span>与项目主张匹配</span><strong>{matchedClaims} 条</strong></div>
              <small>外部热度只帮助发现值得研究的题材，不计入科学证据。</small>
            </div>
          ) : null}
        </div>
      </div>
      <div className="direction-list">
        {directions.map((direction, index) => {
          const selected = direction.track_id === selectedId;
          return (
            <button key={direction.track_id} type="button" className={`direction-card ${selected ? "is-selected" : ""}`} onClick={() => onSelect(direction.track_id)}>
              <span className="direction-index">{String(index + 1).padStart(2, "0")}</span>
              <div className="direction-copy">
                <div><h2>{paperTopicTitle(direction)}</h2>{index === 0 ? <em>推荐</em> : null}</div>
                <p><span className="topic-label">核心问题</span>{candidateQuestion(direction)}</p>
                <dl>
                  <div><dt>候选贡献</dt><dd>{direction.contribution}</dd></div>
                  <div><dt>研究范围</dt><dd>{direction.scope}</dd></div>
                  <div><dt>证据基础</dt><dd>{direction.evidence_chain_count || 0} 条完整验证链</dd></div>
                </dl>
                {direction.blockers?.length ? <div className="direction-warning"><WarningCircle size={16} weight="fill" /><span>{blockerText(direction.blockers[0])}</span></div> : null}
              </div>
              <span className="select-indicator">{selected ? <Check size={16} weight="bold" /> : null}</span>
            </button>
          );
        })}
      </div>
      <div className="sticky-action-bar">
        <div><strong>下一步：确认选题边界</strong><span>确认后再冻结核心问题与证据范围</span></div>
        <button type="button" className="primary-button" disabled={!selectedId} onClick={onContinue}>确认候选选题<ArrowRight size={18} /></button>
      </div>
    </section>
  );
}

function ContractReview({ direction, source, onBack, onConfirm, loading }) {
  const derived = direction?.source_mode === "derived_materials";
  const rules = [
    ["研究问题", candidateQuestion(direction)],
    ["候选贡献", direction?.contribution || "待确认"],
    ["研究范围", direction?.scope || "待确认"],
    ["数据边界", source],
    ["证据类型", maturityCopy[direction?.evidence_maturity] || "待确认"],
    ["协议与输出", direction?.protocol_bound_to_output ? "精确绑定，可进入判定" : "未绑定，只能形成证据缺口报告"],
    ["禁止升级", derived ? "缺少冻结协议时保持“暂不可验证”" : "论文写作不能改变实验判定"],
  ];
  return (
    <section className="workflow-state contract-state">
      <div className="state-hero compact-hero">
        <button type="button" className="back-button" onClick={onBack}><ArrowLeft size={18} />返回候选选题</button>
        <div><p className="eyebrow">研究契约 · v1</p><h1>这次研究到底要判断什么？</h1><p>确认之后，研究问题、证据边界和禁止解释会一起冻结。历史结果不会被后续写作覆盖。</p></div>
      </div>
      {derived ? <div className="protocol-alert"><WarningCircle size={21} weight="fill" /><div><strong>当前材料缺少完整协议链</strong><p>本轮可以收敛问题并生成证据缺口工作稿，但想法判定必须保持“暂不可验证”。</p></div></div> : null}
      <div className="contract-layout">
        <article className="surface-card contract-card">
          <div className="card-heading"><div><span>冻结候选选题</span><h2>{paperTopicTitle(direction)}</h2></div><LockKey size={23} /></div>
          <dl className="contract-rules">
            {rules.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
          </dl>
        </article>
        <aside className="surface-card contract-aside">
          <ShieldCheck size={27} weight="fill" />
          <h3>确认后会发生什么</h3>
          <ol><li>保存项目资源快照</li><li>冻结协议与输出绑定</li><li>生成独立想法判定</li><li>再决定是否允许补写论文</li></ol>
          <p>更换数据、指标或基线会生成新契约版本。</p>
        </aside>
      </div>
      <div className="sticky-action-bar">
        <div><strong>研究契约将被持久化</strong><span>继续不代表想法已经成立</span></div>
        <button type="button" className="primary-button" onClick={onConfirm} disabled={loading}>
          {loading ? <CircleNotch className="spinner" size={18} /> : <Play size={18} weight="fill" />}
          {loading ? "正在启动研究" : "确认并开始验证"}
        </button>
      </div>
    </section>
  );
}

function AttentionState({ task, onResolve, onResume, onBackToContract }) {
  const requirement = inferRequirement(task);
  const changesProtocol = requirement.changes_protocol;
  const actionId = requirement.accepted_inputs?.find((item) => item.action_id)?.action_id;
  const fields = (requirement.accepted_inputs || []).filter((item) => item.name);
  const [inputValues, setInputValues] = useState({});
  useEffect(() => { setInputValues({}); }, [requirement.requirement_id]);
  const missingRequired = fields.some((field) => field.required && !inputValues[field.name]);
  const submitResolution = () => onResolve(requirement, {
    configured: true,
    method: "user_supplied_execution_condition",
    action_id: actionId,
    ...inputValues,
  });
  return (
    <section className="workflow-state attention-state">
      <div className="state-hero attention-hero">
        <span className="state-icon"><WarningCircle size={28} weight="fill" /></span>
        <div><p className="eyebrow">研究已安全停下</p><h1>需要你补一个条件。</h1><p>已完成的工作和失败原因都已记录，不需要从头开始。</p></div>
      </div>
      <div className="attention-layout">
        <article className="surface-card requirement-card">
          <div className="requirement-heading">
            <span className={`requirement-kind is-${requirement.kind}`}>{requirementIcon(requirement.kind)}{requirementKindLabel(requirement.kind)}</span>
            <small>{requirement.required ? "继续所必需" : "可选增强"}</small>
          </div>
          <h2>{requirement.title}</h2>
          <p>{requirement.reason}</p>
          {requirement.external_data_disclosure?.length ? (
            <div className="disclosure-box"><strong>如果授权，会发送</strong><ul>{requirement.external_data_disclosure.map((item) => <li key={item}>{item}</li>)}</ul><small>不会发送本地项目、数据集或未发表全文，除非另行明确说明。</small></div>
          ) : null}
          {fields.length ? (
            <div className="requirement-fields">
              {fields.map((field) => (
                field.type === "boolean" ? (
                  <label className="requirement-checkbox" key={field.name}>
                    <input
                      type="checkbox"
                      checked={Boolean(inputValues[field.name])}
                      onChange={(event) => setInputValues((current) => ({ ...current, [field.name]: event.target.checked }))}
                    />
                    <span>{field.label}</span>
                  </label>
                ) : (
                  <label className="requirement-field" key={field.name}>
                    <span>{field.label}</span>
                    <input
                      type="text"
                      value={inputValues[field.name] || ""}
                      placeholder={field.placeholder || ""}
                      onChange={(event) => setInputValues((current) => ({ ...current, [field.name]: event.target.value }))}
                    />
                  </label>
                )
              ))}
              <small>这里补充的是执行条件。系统仍会实际运行实验并验证产物，不会因为点击确认就把证据标记为完成。</small>
            </div>
          ) : null}
          {changesProtocol ? <div className="protocol-change-note"><SlidersHorizontal size={18} /><span><strong>这会改变研究契约</strong>解决后不能续接原判定，需要创建新版本。</span></div> : null}
          <div className="requirement-actions">
            {requirement.requirement_id !== "inferred" ? <button className="primary-button" type="button" disabled={missingRequired} onClick={submitResolution}>继续并执行实验</button> : null}
            {requirement.alternatives?.map((alternative) => <button className="secondary-button" type="button" key={alternative.id || alternative.label} onClick={() => onResolve(requirement, { alternative: alternative.id || alternative.label, action_id: actionId })}>{alternative.label}</button>)}
            {task?.resumable && requirement.requirement_id === "inferred" ? <button className="primary-button" type="button" onClick={onResume}><Play size={18} weight="fill" />重试当前任务</button> : null}
          </div>
        </article>
        <aside className="surface-card completed-work-card">
          <span>已经保留</span>
          <h3>任务状态与运行记录</h3>
          <ul><li><CheckCircle size={17} weight="fill" />任务编号和输入参数</li><li><CheckCircle size={17} weight="fill" />失败类型与原始原因</li><li><CheckCircle size={17} weight="fill" />已完成的阶段产物</li></ul>
          <button type="button" className="text-button" onClick={onBackToContract}>返回研究契约检查范围<ArrowRight size={16} /></button>
        </aside>
      </div>
    </section>
  );
}

function IdeaReady({ result, onOpenPath, onNew }) {
  return (
    <section className="workflow-state idea-ready-state">
      <div className="state-hero success-hero">
        <span className="state-icon"><CheckCircle size={29} weight="fill" /></span>
        <div><p className="eyebrow">研究边界已创建</p><h1>{result?.title || "研究任务已进入发现阶段"}</h1><p>{result?.message} 当前下一道科学门：{result?.next_gate}。</p></div>
      </div>
      <StageProgress activeStage="discovery" />
      <article className="surface-card next-gate-card">
        <div><span>当前真实状态</span><h2>等待文献检索与研究边界确认</h2><p>任务创建只证明工作区已经持久化，不代表文献、实验或发表门已经通过。</p></div>
        <div className="next-gate-actions"><button className="secondary-button" type="button" onClick={() => onOpenPath(result?.project_path)}><FolderOpen size={18} />打开任务目录</button><button className="text-button" type="button" onClick={onNew}>开始另一个任务<ArrowRight size={16} /></button></div>
      </article>
    </section>
  );
}

function RemediationWorkspace({ plan, onApprove, onConfirmContract, onArchive, loading }) {
  const proposed = plan?.selected_action_ids?.length
    ? plan.selected_action_ids
    : (plan?.actions || []).map((item) => item.action_id);
  const [selected, setSelected] = useState(proposed);
  useEffect(() => {
    setSelected(plan?.selected_action_ids?.length
      ? plan.selected_action_ids
      : (plan?.actions || []).map((item) => item.action_id));
  }, [plan?.plan_id, plan?.updated_at]);
  const requiresContract = plan?.contract_confirmation_required && !plan?.contract_confirmed;
  const toggle = (actionId) => setSelected((current) => current.includes(actionId)
    ? current.filter((item) => item !== actionId)
    : [...current, actionId]);
  return (
    <section className="workflow-state remediation-state">
      <div className="state-hero remediation-hero">
        <span className="state-icon"><ListChecks size={28} weight="fill" /></span>
        <div><p className="eyebrow">补证据计划 · v{plan?.version || 1}</p><h1>研究没有结束，下一步是补齐证据。</h1><p>{plan?.gap_summary || "系统正在把未通过的科学门转化为可以执行的研究任务。"}</p></div>
      </div>
      <div className="remediation-layout">
        <article className="surface-card remediation-plan-card">
          <div className="card-heading"><div><span>本轮可执行任务</span><h2>选择要推进的补证据工作</h2></div><ShieldCheck size={23} /></div>
          <p className="plan-instruction">系统只会执行你确认的任务；重新扫描和重新判定会作为必要收尾自动加入。</p>
          <div className="remediation-actions-list">
            {(plan?.actions || []).map((action) => {
              const checked = selected.includes(action.action_id);
              const required = action.action_id === "action-rescan-and-rejudge";
              return (
                <label key={action.action_id} className={`remediation-action ${checked ? "is-selected" : ""}`}>
                  <input type="checkbox" checked={checked} disabled={required || loading || requiresContract} onChange={() => toggle(action.action_id)} />
                  <span className="remediation-check">{checked ? <Check size={14} weight="bold" /> : null}</span>
                  <span className="remediation-action-copy">
                    <span><strong>{action.title}</strong><em>{action.priority === "critical" ? "必须优先" : action.priority === "high" ? "高优先级" : "建议"}</em></span>
                    <p>{action.evidence_gap}</p>
                    <dl><div><dt>怎么补</dt><dd>{action.method}</dd></div><div><dt>产物</dt><dd>{action.expected_artifact}</dd></div></dl>
                    {action.experiment_command?.length ? (
                      <div className="declared-command">
                        <span>将执行已声明命令{action.network_access ? " · 需要网络" : " · 不需要网络"}</span>
                        <code>{action.experiment_command.join(" ")}</code>
                      </div>
                    ) : null}
                    <small>{action.execution_mode === "automatic" ? "系统可自动执行" : action.execution_mode === "external" ? "需要外部来源或授权" : "需要你补充材料"} · {action.contract_impact === "new_version" ? "会创建新契约版本" : "沿用当前契约"} · 成本 {action.cost}</small>
                  </span>
                </label>
              );
            })}
          </div>
        </article>
        <aside className="surface-card remediation-summary-card">
          <span>研究谱系</span><h3>原判定保持不变</h3>
          <p>补充证据会生成新的后继运行，不会覆盖本次判定或历史产物。</p>
          <dl><div><dt>来源运行</dt><dd>{plan?.source_run_name}</dd></div><div><dt>研究判定</dt><dd>{verdictCopy[plan?.verdict_status]?.label || plan?.verdict_status}</dd></div><div><dt>契约版本</dt><dd>v{plan?.contract_version || 1}</dd></div><div><dt>已选择</dt><dd>{selected.length} 项</dd></div></dl>
          {requiresContract ? <div className="contract-revision-box"><WarningCircle size={19} weight="fill" /><div><strong>所选任务会改变研究设计</strong><p>系统已准备契约 v{(plan?.contract_version || 1) + 1}。确认后才会执行，新结果不会追认旧实验。</p></div></div> : null}
          <div className="remediation-buttons">
            {requiresContract
              ? <button className="primary-button" type="button" disabled={loading} onClick={() => onConfirmContract(selected)}><LockKey size={17} />确认新契约并执行</button>
              : <button className="primary-button" type="button" disabled={loading || selected.length === 0} onClick={() => onApprove(selected)}>{loading ? <CircleNotch className="spinner" size={17} /> : <Play size={17} weight="fill" />}确认本轮计划</button>}
            <button className="text-button" type="button" disabled={loading} onClick={onArchive}>结束并归档当前研究</button>
          </div>
        </aside>
      </div>
    </section>
  );
}

function VerdictState({ run, onOpenPath, onExpand, onRemediate, onNew, onInspectEvidence }) {
  const verdict = run?.verdict || {};
  const status = verdictCopy[verdict.status || run?.idea_status] || verdictCopy.unverifiable;
  const plan = run?.paper_expansion_plan || {};
  const audit = run?.audit || {};
  const gates = [
    { id: "protocol", label: "协议提前冻结", passed: verdict.protocol_bound_to_output === true, actual: verdict.protocol_bound_to_output ? "协议与结果精确绑定" : "尚未找到精确绑定", evidence: run?.protocol },
    { id: "integrity", label: "产物完整性", passed: audit.passed === true || run?.audit_passed === true, actual: audit.passed ? "四阶段产物审计通过" : "存在未通过的产物检查", evidence: audit },
    { id: "idea", label: "想法判定门", passed: plan.idea_gate_passed === true, actual: plan.idea_gate_passed ? "想法得到干净验证" : status.label, evidence: verdict },
    { id: "evidence", label: "证据可写门", passed: plan.evidence_gate_passed === true, actual: `${plan.numeric_evidence_count ?? verdict.numeric_evidence?.length ?? 0} 条数值证据`, evidence: verdict.numeric_evidence },
    { id: "literature", label: "冻结文献门", passed: plan.literature_gate_passed === true, actual: `${plan.verified_paper_count || 0}/15 篇已核验`, evidence: plan },
  ];
  const readiness = plan.ready || run?.paper_expansion_ready;
  const needsRemediation = ["mixed", "inconclusive", "unverifiable"].includes(verdict.status || run?.idea_status);
  const workingPaper = run?.path ? `${run.path}\\stage_4_synthesis\\manuscript.md` : "";
  const fullPaper = run?.path && run?.full_manuscript_generated ? `${run.path}\\stage_4_synthesis\\full_manuscript.md` : "";
  return (
    <section className="workflow-state verdict-state">
      <div className={`verdict-hero is-${status.tone}`}>
        <div className="verdict-title"><span className="eyebrow">研究判定</span><h1>{status.label}</h1><p>{verdict.conclusion || "现有证据已经完成结构化判定。展开下方规则可以查看每个结论的来源。"}</p></div>
        <div className="verdict-actions"><button className="secondary-button" type="button" onClick={() => onOpenPath(fullPaper || workingPaper)} disabled={!fullPaper && !workingPaper}><BookOpenText size={18} />{fullPaper ? "打开完整论文" : "打开工作稿"}</button></div>
      </div>
      <div className="verdict-summary-grid">
        <article><span>想法判定</span><strong className={`is-${status.tone}`}>{status.label}</strong><small>{verdict.idea_validated ? "已形成独立验证" : "尚未验证成功"}</small></article>
        <article><span>证据成熟度</span><strong>{maturityCopy[verdict.evidence_maturity] || "待确认"}</strong><small>{verdict.protocol_bound_to_output ? "证据来源已绑定" : "协议输出尚未绑定"}</small></article>
        <article><span>论文资格</span><strong className={readiness ? "is-positive" : "is-warning"}>{readiness ? "可以补写" : "前置门未通过"}</strong><small>{run?.publication_ready ? "达到发表门" : "发表准备仍需继续"}</small></article>
      </div>
      <div className="verdict-layout">
        <article className="surface-card gate-trace-card">
          <div className="card-heading"><div><span>判定链</span><h2>系统为什么得到这个结论</h2></div><ShieldCheck size={23} /></div>
          <div className="gate-trace-list">
            {gates.map((gate) => (
              <button type="button" key={gate.id} onClick={() => onInspectEvidence(gate)}>
                {gate.passed ? <CheckCircle size={21} weight="fill" /> : <XCircle size={21} weight="fill" />}
                <span><strong>{gate.label}</strong><small>{gate.actual}</small></span>
                <ArrowRight size={17} />
              </button>
            ))}
          </div>
        </article>
        <aside className="surface-card next-action-card">
          <span>当前责任边界</span>
          <h2>{diagnosticOwner(plan.diagnostic_owner)}</h2>
          <p>{plan.next_action || verdict.next_action || "完成当前科学门后，才能把责任交给论文写作阶段。"}</p>
          {plan.blockers?.length ? <ul>{plan.blockers.slice(0, 4).map((item) => <li key={item}>{item}</li>)}</ul> : null}
          {needsRemediation ? <button className="primary-button" type="button" onClick={onRemediate}><ListChecks size={18} weight="fill" />查看补证据计划</button>
            : readiness && !run?.full_manuscript_generated ? <button className="primary-button" type="button" onClick={onExpand}><BookOpenText size={18} weight="fill" />生成完整论文</button>
              : <button className="secondary-button" type="button" onClick={onNew}><FileArrowUp size={18} />开始新的研究</button>}
        </aside>
      </div>
      <StudyWorkflowPanel workflow={run?.workflow} />
      <StageProgress activeStage="synthesis" compact />
    </section>
  );
}

function EvidenceDrawer({ item, onClose }) {
  if (!item) return null;
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label="判定依据" onMouseDown={(event) => event.stopPropagation()}>
        <div className="drawer-heading"><div><span>判定依据</span><h2>{item.label}</h2></div><button type="button" onClick={onClose} aria-label="关闭"><X size={20} /></button></div>
        <div className={`drawer-status ${item.passed ? "is-passed" : "is-blocked"}`}>{item.passed ? <CheckCircle size={21} weight="fill" /> : <XCircle size={21} weight="fill" />}<span>{item.actual}</span></div>
        <p>该项由后端冻结规则和运行产物计算，前端只负责展示，不会根据某一个高分自行升级研究结论。</p>
        <pre>{JSON.stringify(item.evidence || {}, null, 2)}</pre>
      </aside>
    </div>
  );
}

function Notice({ notice, error }) {
  if (!notice && !error) return null;
  return <div className={`notice ${error ? "is-error" : "is-success"}`}>{error ? <WarningCircle size={18} weight="fill" /> : <CheckCircle size={18} weight="fill" />}<span>{error || notice}</span></div>;
}

function formatTime(value) {
  if (!value) return "刚刚";
  try { return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
  catch { return value; }
}

function requirementKindLabel(kind) {
  return ({ credential: "访问权限", dataset: "数据文件", environment: "运行环境", permission: "用户授权", configuration: "配置条件", protocol_change: "协议变更" })[kind] || "研究条件";
}

function requirementIcon(kind) {
  if (kind === "credential" || kind === "permission") return <Key size={16} />;
  if (kind === "dataset") return <Database size={16} />;
  if (kind === "environment") return <Gauge size={16} />;
  return <SlidersHorizontal size={16} />;
}

function diagnosticOwner(owner) {
  return ({ idea_validation: "想法验证阶段", evidence_packaging: "证据整理阶段", literature_grounding: "文献研究阶段", paper_writer: "论文写作阶段" })[owner] || "前置研究阶段";
}

export function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [drawerItem, setDrawerItem] = useState(null);
  const pollRef = useRef(null);
  const directions = useMemo(() => uniqueDirections(state.inspection?.candidates || []), [state.inspection]);
  const selectedDirection = directions.find((item) => item.track_id === state.selectedDirectionId) || directions[0] || null;
  const requestPending = state.request.status === "pending";

  useEffect(() => {
    let cancelled = false;
    Promise.all([api("/api/bootstrap"), api("/api/tasks")])
      .then(async ([bootstrap, taskPayload]) => {
        if (cancelled) return;
        const tasks = taskPayload.tasks || [];
        dispatch({ type: "BOOTSTRAP_SUCCESS", payload: { ...bootstrap, tasks } });
        const params = new URLSearchParams(window.location.search);
        const taskId = params.get("task");
        const remediationName = params.get("remediation");
        const runName = params.get("run");
        const restoredTask = tasks.find((item) => item.task_id === taskId);
        if (restoredTask) {
          dispatch({ type: "TASK_UPDATED", task: restoredTask });
          dispatch({ type: "PATCH", patch: { mode: restoredTask.request?.operation === "idea.start" ? "idea" : "project", view: taskUiState(restoredTask) || "running" } });
          handleTaskTransition(restoredTask).catch((error) => dispatch({ type: "PATCH", patch: { error: error.message } }));
        } else if (remediationName) {
          const [run, remediationPlan] = await Promise.all([
            api(`/api/run?name=${encodeURIComponent(remediationName)}`),
            api(`/api/remediation?name=${encodeURIComponent(remediationName)}`),
          ]);
          if (!cancelled) dispatch({ type: "PATCH", patch: { view: "remediation", run, remediationPlan } });
        } else if (runName) {
          const run = await api(`/api/run?name=${encodeURIComponent(runName)}`);
          if (!cancelled) dispatch({ type: "PATCH", patch: { view: "verdict", run } });
        }
      })
      .catch((error) => { if (!cancelled) dispatch({ type: "BOOTSTRAP_ERROR", error: error.message }); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const task = state.activeTask;
    const shouldPoll = task && ["pending", "running", "pause_requested", "paused"].includes(task.status);
    if (!shouldPoll) return undefined;
    const poll = async () => {
      try {
        const next = await api(`/api/task?id=${encodeURIComponent(task.task_id)}`);
        dispatch({ type: "TASK_UPDATED", task: next });
        await handleTaskTransition(next);
      } catch (error) {
        dispatch({ type: "PATCH", patch: { error: error.message } });
      }
    };
    pollRef.current = window.setInterval(poll, 1200);
    return () => window.clearInterval(pollRef.current);
  }, [state.activeTask?.task_id, state.activeTask?.status]);

  useEffect(() => {
    const taskId = state.activeTask?.task_id;
    if (!taskId) return undefined;
    let cancelled = false;
    const loadEvents = async () => {
      try {
        const payload = await api(`/api/task/events?id=${encodeURIComponent(taskId)}`);
        if (!cancelled) dispatch({ type: "PATCH", patch: { taskEvents: payload.events || [] } });
      } catch {
        // Task polling remains the source of truth; a missing activity feed should not stop research.
      }
    };
    loadEvents();
    const active = ["pending", "running", "pause_requested"].includes(state.activeTask?.status);
    const timer = active ? window.setInterval(loadEvents, 1200) : null;
    return () => { cancelled = true; if (timer) window.clearInterval(timer); };
  }, [state.activeTask?.task_id, state.activeTask?.status]);

  async function handleTaskTransition(task) {
    const nextView = taskUiState(task);
    if (nextView === "directions") {
      const inspection = task.result || {};
      const nextDirections = uniqueDirections(inspection.candidates || []);
      const recommendedTopic = nextDirections.find((topic) => topic.supporting_track_ids?.includes(inspection.recommended_track_id)) || nextDirections[0];
      dispatch({ type: "PATCH", patch: { view: "directions", inspection, selectedDirectionId: recommendedTopic?.track_id || "", notice: "项目扫描完成。请先确认一个可以独立成文的候选选题。" } });
    } else if (nextView === "idea_ready") {
      dispatch({ type: "PATCH", patch: { view: "idea_ready", ideaResult: task.result || {}, notice: "研究边界已创建，并进入发现阶段。" } });
    } else if (nextView === "attention") {
      dispatch({ type: "PATCH", patch: { view: "attention" } });
    } else if (nextView === "verdict") {
      const runDir = task.result?.run_dir;
      if (runDir) {
        const name = runDir.replaceAll("\\", "/").split("/").filter(Boolean).at(-1);
        const run = await api(`/api/run?name=${encodeURIComponent(name)}`);
        if (["mixed", "inconclusive", "unverifiable"].includes(run?.verdict?.status || run?.idea_status)) {
          const remediationPlan = await api("/api/remediation/plan", { method: "POST", body: JSON.stringify({ name }) });
          window.history.replaceState({}, "", `?remediation=${encodeURIComponent(name)}`);
          dispatch({ type: "PATCH", patch: { view: "remediation", run, remediationPlan, notice: "当前判定需要继续补证据。系统已经生成本轮计划。" } });
        } else {
          window.history.replaceState({}, "", `?run=${encodeURIComponent(name)}`);
          dispatch({ type: "PATCH", patch: { view: "verdict", run, remediationPlan: null, notice: "研究判定和论文资格已经分别生成。" } });
        }
      }
    }
  }

  async function submitTask(operation, payload, prefix) {
    const task = await api("/api/tasks/submit", { method: "POST", body: JSON.stringify({ operation, payload, request_id: requestId(prefix) }) });
    window.history.replaceState({}, "", `?task=${encodeURIComponent(task.task_id)}`);
    dispatch({ type: "TASK_UPDATED", task });
    dispatch({ type: "PATCH", patch: { view: "running", taskEvents: [], notice: "" } });
    return task;
  }

  async function chooseFolder() {
    dispatch({ type: "REQUEST", name: "folder", status: "pending" });
    try {
      const result = await api("/api/select-folder", { method: "POST", body: JSON.stringify({ initial: state.source }) });
      if (!result.cancelled && result.path) dispatch({ type: "PATCH", patch: { source: result.path, notice: "文件夹已选择。扫描过程只读。" } });
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function inspectProject() {
    dispatch({ type: "REQUEST", name: "inspect", status: "pending" });
    try { await submitTask("bundle.inspect", { source: state.source, discover_claims: true }, "inspect"); }
    catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function startIdea(values) {
    dispatch({ type: "REQUEST", name: "idea", status: "pending" });
    try { await submitTask("idea.start", { ...values, idea_root: state.ideaRoot }, "idea"); }
    catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function confirmContract() {
    dispatch({ type: "REQUEST", name: "close", status: "pending" });
    try {
      await submitTask("bundle.close", { source: state.source, output_root: state.runsRoot, name: `${paperTopicTitle(selectedDirection)}-web`, track_id: selectedDirection.track_id, discover_claims: true }, "close");
    } catch (error) {
      // The server owns the canonical run root. Fall back to the compatibility endpoint when not supplied.
      try {
        const run = await api("/api/close-loop", { method: "POST", body: JSON.stringify({ source: state.source, name: `${paperTopicTitle(selectedDirection)}-web`, track_id: selectedDirection.track_id, discover_claims: true, request_id: requestId("close") }) });
        dispatch({ type: "PATCH", patch: { view: "verdict", run, activeTask: run._task || state.activeTask, notice: "研究判定和论文资格已经分别生成。" } });
      } catch (fallbackError) { dispatch({ type: "PATCH", patch: { error: fallbackError.message } }); }
    } finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function pauseTask() {
    try {
      const task = await api("/api/tasks/pause", { method: "POST", body: JSON.stringify({ task_id: state.activeTask.task_id }) });
      dispatch({ type: "TASK_UPDATED", task });
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
  }

  async function resumeTask() {
    dispatch({ type: "REQUEST", name: "resume", status: "pending" });
    try {
      const task = await api("/api/tasks/resume", { method: "POST", body: JSON.stringify({ task_id: state.activeTask.task_id }) });
      dispatch({ type: "TASK_UPDATED", task });
      await handleTaskTransition(task);
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function resolveRequirement(requirement, resolution) {
    try {
      const task = await api("/api/tasks/requirements/resolve", { method: "POST", body: JSON.stringify({ task_id: state.activeTask.task_id, requirement_id: requirement.requirement_id, resolution }) });
      dispatch({ type: "TASK_UPDATED", task });
      if (task.status === "paused") await resumeTask();
      else dispatch({ type: "PATCH", patch: { view: "contract", notice: "该输入会改变研究设计，请创建并确认新的契约版本。" } });
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
  }

  async function expandPaper() {
    dispatch({ type: "REQUEST", name: "expand", status: "pending" });
    try { await submitTask("bundle.expand-paper", { run_dir: state.run.path }, "expand"); }
    catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function openRemediation() {
    if (!state.run?.name) return;
    dispatch({ type: "REQUEST", name: "remediation-plan", status: "pending" });
    try {
      const remediationPlan = await api("/api/remediation/plan", { method: "POST", body: JSON.stringify({ name: state.run.name }) });
      window.history.replaceState({}, "", `?remediation=${encodeURIComponent(state.run.name)}`);
      dispatch({ type: "PATCH", patch: { view: "remediation", remediationPlan, notice: "补证据计划已根据当前证据门生成。" } });
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function approveRemediation(selectedActionIds, confirmContractRevision = false) {
    dispatch({ type: "REQUEST", name: "remediation-approve", status: "pending" });
    try {
      const result = await api("/api/remediation/approve", {
        method: "POST",
        body: JSON.stringify({
          name: state.run.name,
          selected_action_ids: selectedActionIds,
          confirm_contract_revision: confirmContractRevision,
          request_id: requestId("remediate"),
        }),
      });
      dispatch({ type: "PATCH", patch: { remediationPlan: result.plan } });
      if (result.task) {
        window.history.replaceState({}, "", `?task=${encodeURIComponent(result.task.task_id)}`);
        dispatch({ type: "TASK_UPDATED", task: result.task });
        dispatch({ type: "PATCH", patch: { view: "running", taskEvents: [], notice: "补证据计划已经确认，正在执行本轮任务。" } });
      } else if (result.requires_contract_confirmation) {
        dispatch({ type: "PATCH", patch: { notice: "所选任务会改变研究设计。请确认新的契约版本后再执行。" } });
      }
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function archiveRemediation() {
    dispatch({ type: "REQUEST", name: "remediation-archive", status: "pending" });
    try {
      const remediationPlan = await api("/api/remediation/archive", { method: "POST", body: JSON.stringify({ name: state.run.name }) });
      window.history.replaceState({}, "", `?run=${encodeURIComponent(state.run.name)}`);
      dispatch({ type: "PATCH", patch: { view: "verdict", remediationPlan, notice: "当前研究已归档，历史判定和产物仍然保留。" } });
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function openPath(path) {
    if (!path) return;
    try { await api("/api/open-path", { method: "POST", body: JSON.stringify({ path }) }); }
    catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
  }

  async function selectTask(task) {
    if (!task) { window.history.replaceState({}, "", window.location.pathname); dispatch({ type: "RESET" }); return; }
    window.history.replaceState({}, "", `?task=${encodeURIComponent(task.task_id)}`);
    dispatch({ type: "TASK_UPDATED", task });
    dispatch({ type: "PATCH", patch: { mode: task.request?.operation === "idea.start" ? "idea" : "project", view: taskUiState(task) || "running", taskEvents: [], notice: "", error: "" } });
    try { await handleTaskTransition(task); }
    catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
  }

  if (state.boot === "loading") return <div className="boot-screen"><CircleNotch className="spinner" size={30} /><span>正在连接研究引擎…</span></div>;

  return (
    <div className="app-shell">
      <GlobalHeader connected={state.boot === "ready"} tasks={state.tasks} activeTask={state.activeTask} onSelectTask={selectTask} />
      <main className="workspace">
        {state.view === "intake" ? <ModeSelector mode={state.mode} onChange={(mode) => dispatch({ type: "SET_MODE", mode })} disabled={requestPending} /> : null}
        <Notice notice={state.notice} error={state.error} />
        {state.view === "intake" ? <><Hero mode={state.mode} />{state.mode === "project" ? <ProjectIntake source={state.source} onSource={(source) => dispatch({ type: "PATCH", patch: { source } })} onChooseFolder={chooseFolder} onSubmit={inspectProject} loading={requestPending} /> : <IdeaIntake onSubmit={startIdea} loading={requestPending} />}</> : null}
        {state.view === "running" ? <RunningState task={state.activeTask} events={state.taskEvents} onPause={pauseTask} onResume={resumeTask} /> : null}
        {state.view === "directions" ? <DirectionSelect inspection={state.inspection} selectedId={state.selectedDirectionId || selectedDirection?.track_id} onSelect={(selectedDirectionId) => dispatch({ type: "PATCH", patch: { selectedDirectionId } })} onBack={() => dispatch({ type: "RESET" })} onContinue={() => dispatch({ type: "PATCH", patch: { view: "contract" } })} /> : null}
        {state.view === "contract" ? <ContractReview direction={selectedDirection} source={state.source} onBack={() => dispatch({ type: "PATCH", patch: { view: "directions" } })} onConfirm={confirmContract} loading={requestPending} /> : null}
        {state.view === "attention" ? <AttentionState task={state.activeTask} onResolve={resolveRequirement} onResume={resumeTask} onBackToContract={() => dispatch({ type: "PATCH", patch: { view: "contract" } })} /> : null}
        {state.view === "idea_ready" ? <IdeaReady result={state.ideaResult} onOpenPath={openPath} onNew={() => dispatch({ type: "RESET" })} /> : null}
        {state.view === "remediation" ? <RemediationWorkspace plan={state.remediationPlan} onApprove={(selected) => approveRemediation(selected, false)} onConfirmContract={(selected) => approveRemediation(selected, true)} onArchive={archiveRemediation} loading={requestPending} /> : null}
        {state.view === "verdict" ? <VerdictState run={state.run} onOpenPath={openPath} onExpand={expandPaper} onRemediate={openRemediation} onNew={() => dispatch({ type: "RESET" })} onInspectEvidence={setDrawerItem} /> : null}
      </main>
      <EvidenceDrawer item={drawerItem} onClose={() => setDrawerItem(null)} />
    </div>
  );
}
