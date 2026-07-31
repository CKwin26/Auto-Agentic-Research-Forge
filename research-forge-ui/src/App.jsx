import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowClockwise,
  ArrowRight,
  BookOpenText,
  Check,
  CheckCircle,
  CircleNotch,
  Clock,
  ClockCounterClockwise,
  Cloud,
  Cpu,
  Database,
  DownloadSimple,
  FileArrowUp,
  FileText,
  Flask,
  FolderOpen,
  Gauge,
  GearSix,
  Globe,
  Key,
  Lightbulb,
  ListChecks,
  LockKey,
  Pause,
  Play,
  ShieldCheck,
  SlidersHorizontal,
  Sparkle,
  UserCircle,
  WarningCircle,
  WifiSlash,
  Wrench,
  X,
  XCircle,
} from "@phosphor-icons/react";
import {
  blockerText,
  buildStageTwoResourcePackages,
  candidateQuestion,
  compactStudyLabel,
  deriveStageState,
  humanizeIdentifier,
  humanizeResearchText,
  humanizeUiError,
  inferRequirement,
  maturityCopy,
  operationCopy,
  paperTopicTitle,
  phaseForTaskStage,
  preferredWorkflowSnapshot,
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

function GlobalHeader({ connected, runtime, tasks, activeTask, onSelectTask, onOpenDeployment }) {
  const [open, setOpen] = useState(false);
  const needsAttention = tasks.filter((task) => ["waiting_for_user", "failed", "blocked"].includes(task.status));
  const engineName = runtime?.backend === "api" ? "API 模型" : "Codex";
  return (
    <header className="global-header">
      <button className="brand" type="button" onClick={() => onSelectTask(null)}>
        <strong>Research Forge</strong>
        <span>研究工作台</span>
      </button>
      <div className="header-actions">
        <div className={`engine-status ${connected ? "is-online" : ""}`}>
          <i />
          <span>{connected ? `${engineName} · ${runtime?.model || runtime?.provider_model || "已连接"}` : "正在连接研究引擎"}</span>
        </div>
        <button className="deployment-settings-button" type="button" onClick={onOpenDeployment}>
          <GearSix size={17} />
          <span>模型设置</span>
        </button>
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
                    <em>{taskStatusCopy[task.status] || humanizeIdentifier(task.status, "状态已记录")}</em>
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

function DeploymentWizard({
  runtime,
  externalSetup,
  busy,
  error,
  onRefresh,
  onConfigure,
  onConfigureResearch,
  onComplete,
}) {
  const [step, setStep] = useState(1);
  const [method, setMethod] = useState(runtime?.backend === "api" ? "api" : "codex");
  const [researchChoice, setResearchChoice] = useState(
    externalSetup?.choice === "offline" ? "offline" : "public",
  );
  const [apiForm, setApiForm] = useState({
    model: runtime?.backend === "api" ? runtime?.model || "" : "gpt-5.6-terra",
    baseUrl: "",
    apiKey: "",
  });
  const codexReady = runtime?.codex_authenticated === true;
  const apiReady = runtime?.backend === "api" && runtime?.ready === true;
  const selectedReady = method === "codex" ? codexReady : apiReady;

  const connect = async (event) => {
    event.preventDefault();
    const next = method === "codex"
      ? await onConfigure({ backend: "codex", provider_mode: "managed" })
      : await onConfigure({
        backend: "api",
        api_key: apiForm.apiKey,
        model: apiForm.model,
        base_url: apiForm.baseUrl,
      });
    if (next?.ready) setStep(2);
  };

  const finishResearchSetup = async () => {
    const result = await onConfigureResearch({
      choice: researchChoice,
      install_optional: researchChoice === "public" && !externalSetup?.dependencies_installed,
      run_validation: researchChoice === "public",
    });
    if (result) onComplete(runtime);
  };

  const continueOffline = async () => {
    setResearchChoice("offline");
    const result = await onConfigureResearch({
      choice: "offline",
      install_optional: false,
      run_validation: false,
    });
    if (result) onComplete(runtime);
  };

  return (
    <div className="deployment-screen">
      <main className="deployment-shell">
        <header className="deployment-brand">
          <div className="deployment-mark"><Flask size={25} weight="fill" /></div>
          <div><strong>Research Forge</strong><span>首次部署</span></div>
        </header>
        <section className="deployment-intro">
          <span>{step} / 2 · {step === 1 ? "连接研究引擎" : "配置外部研究"}</span>
          <h1>{step === 1 ? "先决定由谁完成研究工作。" : "再决定是否准备外部研究能力。"}</h1>
          <p>{step === 1
            ? "使用你已有的 Codex 登录，或者连接一个兼容 API。密钥只保存到本机忽略文件，不进入研究任务、审计事件或浏览器历史。"
            : "公共检索组件可以现在安装，也可以保持离线以后再配置。无论选择哪一种，每个 Project 默认仍然离线，正式联网前还要由负责人批准。"}</p>
        </section>
        {step === 1 ? (
          <>
            <div className="deployment-methods" role="radiogroup" aria-label="研究引擎连接方式">
              <button
                type="button"
                role="radio"
                aria-checked={method === "codex"}
                className={method === "codex" ? "is-selected" : ""}
                onClick={() => setMethod("codex")}
              >
                <span className="method-icon"><UserCircle size={25} /></span>
                <span><strong>使用 Codex 登录</strong><small>复用本机 ChatGPT / Codex 订阅登录</small></span>
                <i>{method === "codex" ? <Check size={14} weight="bold" /> : null}</i>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={method === "api"}
                className={method === "api" ? "is-selected" : ""}
                onClick={() => setMethod("api")}
              >
                <span className="method-icon"><Cloud size={25} /></span>
                <span><strong>连接 AI API</strong><small>适合服务器部署或自定义兼容服务</small></span>
                <i>{method === "api" ? <Check size={14} weight="bold" /> : null}</i>
              </button>
            </div>
            <form className="deployment-config" onSubmit={connect}>
              {method === "codex" ? (
                <div className={`codex-connection-card ${codexReady ? "is-ready" : ""}`}>
                  <Cpu size={26} />
                  <div>
                    <span>本机 Codex 状态</span>
                    <strong>{codexReady ? "登录已验证，可以直接使用" : "尚未检测到可用登录"}</strong>
                    <p>{codexReady
                      ? `${runtime?.provider_name || "OpenAI managed"} · ${runtime?.provider_model || runtime?.model || "默认模型"}`
                      : "请先在 Codex 完成登录，再返回这里重新检测。Research Forge 不会复制或读取你的登录令牌。"}</p>
                  </div>
                  <button type="button" className="secondary-button" onClick={onRefresh} disabled={busy}>
                    {busy ? <CircleNotch className="spinner" size={17} /> : <ClockCounterClockwise size={17} />}
                    重新检测
                  </button>
                </div>
              ) : (
                <div className="api-connection-form">
                  <div>
                    <label htmlFor="runtime-model">模型名称</label>
                    <input
                      id="runtime-model"
                      value={apiForm.model}
                      onChange={(event) => setApiForm({ ...apiForm, model: event.target.value })}
                      placeholder="例如 gpt-5.6-terra"
                      autoComplete="off"
                    />
                  </div>
                  <div>
                    <label htmlFor="runtime-base-url">兼容 API 地址 <small>可选</small></label>
                    <input
                      id="runtime-base-url"
                      type="url"
                      value={apiForm.baseUrl}
                      onChange={(event) => setApiForm({ ...apiForm, baseUrl: event.target.value })}
                      placeholder="留空使用官方服务；自定义地址必须为 HTTPS"
                      autoComplete="url"
                    />
                  </div>
                  <div className="api-key-field">
                    <label htmlFor="runtime-api-key">API Key</label>
                    <input
                      id="runtime-api-key"
                      type="password"
                      value={apiForm.apiKey}
                      onChange={(event) => setApiForm({ ...apiForm, apiKey: event.target.value })}
                      placeholder={apiReady ? "已安全配置；输入新密钥可替换" : "仅发送到本机 Research Forge 服务"}
                      autoComplete="new-password"
                    />
                    <small><LockKey size={14} />不会写入任务、提示词、审计日志或 Git</small>
                  </div>
                </div>
              )}
          {error ? <div className="deployment-error" role="alert"><WarningCircle size={18} weight="fill" />{humanizeUiError(error)}</div> : null}
              <footer className="deployment-footer">
                <div><ShieldCheck size={18} weight="fill" /><span>下一步只配置可选研究能力，不会自动联网</span></div>
                {selectedReady ? (
                  <button type="button" className="primary-button" onClick={() => setStep(2)}>
                    下一步：研究能力<ArrowRight size={18} />
                  </button>
                ) : (
                  <button
                    type="submit"
                    className="primary-button"
                    disabled={busy || (method === "codex" && !codexReady) || (method === "api" && (!apiForm.model.trim() || apiForm.apiKey.trim().length < 8))}
                  >
                    {busy ? <CircleNotch className="spinner" size={18} /> : <Key size={18} />}
                    {busy ? "正在验证连接" : method === "codex" ? "使用 Codex 继续" : "保存并验证 API"}
                  </button>
                )}
              </footer>
            </form>
          </>
        ) : (
          <section className="research-setup-panel">
            <div className="research-setup-choices" role="radiogroup" aria-label="外部研究配置">
              <button
                type="button"
                role="radio"
                aria-checked={researchChoice === "public"}
                className={researchChoice === "public" ? "is-selected" : ""}
                onClick={() => setResearchChoice("public")}
              >
                <span className="method-icon"><Globe size={25} /></span>
                <span>
                  <strong>准备公共外部研究 <em>推荐</em></strong>
                  <small>安装论文检索、Hugging Face 与全文证据分析组件</small>
                </span>
                <i>{researchChoice === "public" ? <Check size={14} weight="bold" /> : null}</i>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={researchChoice === "offline"}
                className={researchChoice === "offline" ? "is-selected" : ""}
                onClick={() => setResearchChoice("offline")}
              >
                <span className="method-icon"><WifiSlash size={25} /></span>
                <span><strong>暂时保持离线</strong><small>跳过组件安装，之后仍可从模型设置补充</small></span>
                <i>{researchChoice === "offline" ? <Check size={14} weight="bold" /> : null}</i>
              </button>
            </div>
            {researchChoice === "public" ? (
              <div className="research-component-list">
                {(externalSetup?.components || []).map((item) => (
                  <div key={item.capability}>
                    {item.installed ? <CheckCircle size={18} weight="fill" /> : <DownloadSimple size={18} />}
                    <span>
                      <strong>{capabilityNames[item.capability] || humanizeIdentifier(item.capability, "研究能力")}</strong>
                      <small>{item.installed ? "组件已安装" : "将在本机安装固定版本"}</small>
                    </span>
                  </div>
                ))}
                <p><ShieldCheck size={17} weight="fill" />安装组件不等于开放网络。每个项目仍以离线开始，负责人批准后才允许公共只读检索。</p>
              </div>
            ) : (
              <div className="offline-setup-note"><WifiSlash size={20} /><span><strong>离线工作流保持完整</strong><small>仍可扫描本地项目、设计实验、运行受控实验并撰写论文。</small></span></div>
            )}
            {error ? (
              <div className="deployment-recovery" role="alert">
                <WarningCircle size={20} weight="fill" />
                <div>
                  <strong>外部研究能力没有全部准备好</strong>
                <p>{humanizeUiError(error)}</p>
                  <small>这不会影响本地项目扫描、实验设计和离线工作流。你可以先进入工作台，之后从“模型设置”重新验收。</small>
                </div>
                <button type="button" className="secondary-button" onClick={continueOffline} disabled={busy}>
                  先离线进入
                </button>
              </div>
            ) : null}
            <footer className="deployment-footer">
              <button type="button" className="secondary-button" onClick={() => setStep(1)} disabled={busy}>
                <ArrowLeft size={17} />返回模型连接
              </button>
              <button type="button" className="primary-button" onClick={finishResearchSetup} disabled={busy}>
                {busy ? <CircleNotch className="spinner" size={18} /> : researchChoice === "public" && !externalSetup?.dependencies_installed ? <DownloadSimple size={18} /> : <ArrowRight size={18} />}
                {busy
                  ? "正在安装并验收研究组件"
                  : researchChoice === "public" && !externalSetup?.dependencies_installed
                    ? "安装、验收并进入工作台"
                    : researchChoice === "public"
                      ? "重新验收并进入工作台"
                      : "保存选择并进入工作台"}
              </button>
            </footer>
          </section>
        )}
      </main>
    </div>
  );
}

const phaseTabMeta = {
  discovery: { number: "01", title: "方向发现", caption: "范围与候选问题" },
  protocol: { number: "02", title: "协议与可行性", caption: "议题、最小验证与契约" },
  experiment: { number: "03", title: "实验与判定", caption: "执行、证据与结论" },
  paper: { number: "04", title: "论文与审计", caption: "写作、复核与投稿" },
};

const executingStepStatuses = new Set(["running", "retrying", "pause_requested"]);

function StepStatusGlyph({ status, size = 12 }) {
  if (executingStepStatuses.has(status)) {
    return <CircleNotch className="spinner" size={size} aria-hidden="true" />;
  }
  if (status === "queued") return <Clock size={size} aria-hidden="true" />;
  if (status === "succeeded") return <Check size={size} weight="bold" aria-hidden="true" />;
  return null;
}

function isCompatibilityImport(workflow) {
  return (
    workflow?.study?.settings?.workflow_origin === "legacy_bundle_import"
    || (
      workflow?.study?.lifecycle === "completed"
      && (workflow?.steps?.length || 0) > 0
      && workflow.steps.every((item) => !(item.output_artifact_ids || []).length)
      && (workflow?.artifacts?.length || 0) > 0
    )
  );
}

function ResearchStageTabs({ activePhase, workflow, onChange }) {
  const hasStudy = Boolean(workflow?.study);
  const phaseOrder = Object.keys(phaseTabMeta);
  const currentStepIds = new Set(workflow?.study?.current_step_ids || []);
  const liveCurrentPhase = workflow?.steps?.find(
    (item) => currentStepIds.has(item.step_instance_id)
      && ["queued", "running", "retrying", "pause_requested", "waiting_for_user"].includes(item.status),
  )?.phase;
  const currentPhase = liveCurrentPhase || workflow?.study?.phase || "discovery";
  const currentIndex = Math.max(0, phaseOrder.indexOf(currentPhase));
  const compatibilityImport = isCompatibilityImport(workflow);
  return (
    <nav className="research-stage-tabs" aria-label="研究四阶段">
      {phaseOrder.map((phase, index) => {
        const meta = phaseTabMeta[phase];
        const steps = workflow?.steps?.filter((item) => item.phase === phase) || [];
        const complete = !compatibilityImport && steps.length > 0 && steps.every((item) => item.status === "succeeded");
        const phaseComplete = complete || index < currentIndex;
        const attention = !phaseComplete && steps.some(
          (item) => ["failed", "blocked", "waiting_for_user"].includes(item.status),
        );
        const running = index === currentIndex && steps.some(
          (item) => currentStepIds.has(item.step_instance_id) && executingStepStatuses.has(item.status),
        );
        const state = compatibilityImport ? "imported" : attention ? "attention" : phaseComplete ? "complete" : index === currentIndex ? "current" : "upcoming";
        return (
          <button
            key={phase}
            type="button"
            role="tab"
            aria-selected={activePhase === phase}
            aria-disabled={!hasStudy && index > 0}
            disabled={!hasStudy && index > 0}
            title={!hasStudy && index > 0 ? "先从阶段一创建研究任务" : undefined}
            className={`is-${state} ${activePhase === phase ? "is-active" : ""}`}
            onClick={() => onChange(phase)}
          >
            <span>{running ? <CircleNotch className="spinner" size={15} /> : state === "complete" ? <Check size={14} weight="bold" /> : meta.number}</span>
            <div><strong>{meta.title}</strong><small>{compatibilityImport ? "仅导入旧产物" : attention ? "需要处理" : running ? "正在执行" : meta.caption}</small></div>
          </button>
        );
      })}
    </nav>
  );
}

const capabilityNames = {
  academic_search: "论文检索",
  github_research: "GitHub",
  huggingface_research: "Hugging Face",
  public_web: "公开网页",
  open_access: "开放全文",
  institutional_access: "机构资源",
  evidence_analysis: "全文证据分析",
};

const institutionPresets = {
  cmu: {
    ownerUserId: "",
    institutionId: "carnegie-mellon-university",
    targetUrl: "https://www.library.cmu.edu/find",
  },
};

const readinessStateNames = {
  ready: "已验证",
  degraded: "降级可用",
  blocked: "已阻止",
  unavailable: "不可用",
  not_configured: "未配置",
};

const readinessStateDescriptions = {
  ready: "近期真实运行和发布测试均已通过。",
  degraded: "基础能力存在，但仍缺近期真实运行或发布验收。",
  blocked: "当前被策略、权限或前置条件阻止。",
  unavailable: "当前环境不具备运行该能力的必要条件。",
  not_configured: "当前项目尚未配置该能力。",
};

function ExternalResearchStatus({ report, workflow, compact = false }) {
  const [expanded, setExpanded] = useState(false);
  const [overview, setOverview] = useState(null);
  const [panelError, setPanelError] = useState("");
  const [loopSnapshot, setLoopSnapshot] = useState(null);
  const [loopBusy, setLoopBusy] = useState(false);
  const [institution, setInstitution] = useState({
    ownerUserId: "",
    institutionId: "",
    targetUrl: "",
  });
  const [session, setSession] = useState(null);
  const capabilities = (report?.capabilities || []).filter(
    (item) => item.capability !== "external_research_v1",
  );
  const study = workflow?.study;
  const projectId = study?.project_id;
  const studyId = study?.study_id;
  const modeNames = {
    offline: "离线",
    academic_read: "学术只读",
    public_web_read: "公开网页只读",
    authenticated_read: "授权只读",
    public_research: "公开研究",
    public_research_plus_institution: "公开研究 + 机构资源",
  };
  const retrievalSteps = (loopSnapshot?.steps || workflow?.steps || []).filter(
    (item) => item.executor_type === "retrieval_service",
  );
  const latestCoverage = [...(overview?.runs || [])]
    .reverse()
    .find((item) => item.coverage)?.coverage;

  useEffect(() => {
    if (!expanded || !projectId || !studyId) return undefined;
    let cancelled = false;
    api(
      `/api/retrieval/overview?project_id=${encodeURIComponent(projectId)}&study_id=${encodeURIComponent(studyId)}`,
    )
      .then((payload) => {
        if (!cancelled) {
          setOverview(payload);
          setPanelError("");
        }
      })
      .catch((error) => {
        if (!cancelled) setPanelError(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, projectId, studyId]);

  async function prepareExternalLoop(run) {
    if (!studyId) return;
    setLoopBusy(true);
    setPanelError("");
    try {
      const payload = await api(
        `/studies/${encodeURIComponent(studyId)}/retrieval-dags`,
        {
          method: "POST",
          body: JSON.stringify({
            stage: "all",
            run_key: "ui-public-loop-v1",
            run,
          }),
        },
      );
      setLoopSnapshot(payload.snapshot);
      setExpanded(true);
    } catch (error) {
      setPanelError(error.message);
    } finally {
      setLoopBusy(false);
    }
  }

  async function connectInstitution(event) {
    event.preventDefault();
    setPanelError("");
    try {
      const created = await api("/institution-sessions", {
        method: "POST",
        body: JSON.stringify({
          owner_user_id: institution.ownerUserId,
          project_id: projectId,
          study_id: studyId,
          institution_id: institution.institutionId,
          target_url: institution.targetUrl,
          open_browser: true,
        }),
      });
      setSession(created.session || created);
    } catch (error) {
      setPanelError(error.message);
    }
  }

  async function confirmInstitution() {
    try {
      const confirmed = await api(
        `/institution-sessions/${encodeURIComponent(session.session_id)}/confirm-authenticated`,
        {
          method: "POST",
          body: JSON.stringify({
            owner_user_id: institution.ownerUserId,
            user_confirmation: true,
            duration_minutes: 30,
          }),
        },
      );
      setSession(confirmed);
      setPanelError("");
    } catch (error) {
      setPanelError(error.message);
    }
  }

  if (!report) return null;
  return (
    <section className="external-research-strip" aria-label="外部研究能力">
      <div className="external-research-heading">
        <div>
          <p className="eyebrow">外部研究能力</p>
          <h2>
            {compact
              ? (report.public_research_loop_ready ? "外部检索已准备，需要时自动调用" : "可以先离线开始，外部检索按需补充")
              : overview?.policy
              ? `当前网络模式：${modeNames[overview.policy.mode] || overview.policy.mode}`
              : "默认网络模式：离线"}
          </h2>
        </div>
        <div className="external-research-actions">
          <span className={report.public_research_loop_ready ? "external-status-badge is-ready" : "external-status-badge is-degraded"}>
            {report.public_research_loop_ready
              ? (report.external_research_v1_ready ? "全部能力可用" : "公共闭环可用")
              : "按能力降级"}
          </span>
          <button type="button" onClick={() => setExpanded((value) => !value)}>
            {expanded ? "收起" : "查看详情"}
          </button>
        </div>
      </div>
      {!compact || expanded ? (
        <>
          <div className="capability-pills">
            {capabilities.map((item) => (
              <span key={item.capability} className={`capability-pill is-${item.state}`}>
                <i />
                {capabilityNames[item.capability] || humanizeIdentifier(item.capability, "研究能力")}
                <small>{readinessStateNames[item.state] || humanizeIdentifier(item.state, "状态已记录")}</small>
              </span>
            ))}
          </div>
          <p className="external-research-note">
            网络默认关闭，启用需项目负责人批准。机构资源只打开学校官方登录页，由你本人完成登录和 MFA；这里不会收集密码。
          </p>
        </>
      ) : (
        <p className="external-research-note is-compact">
          这不会阻止你扫描本地项目。系统会在阶段一需要查文献时再说明调用了什么、为什么调用。
        </p>
      )}
      {study && expanded ? (
        <div className="external-loop-controls">
          <div>
            <strong>为当前研究补充公开资料</strong>
            <small>系统只会运行已获批准的检索步骤，并保留来源和查询记录。</small>
          </div>
          <button
            type="button"
            disabled={loopBusy}
            onClick={() => prepareExternalLoop(false)}
          >
            {loopBusy ? "处理中…" : "准备检索计划"}
          </button>
          <button
            type="button"
            disabled={loopBusy}
            onClick={() => prepareExternalLoop(true)}
          >
            开始检索
          </button>
        </div>
      ) : null}
      {expanded ? (
        <div className="external-research-details">
          <div className="capability-detail-grid">
            {capabilities.map((item) => (
              <article key={item.capability}>
                <div className="capability-detail-title">
                  <strong>{capabilityNames[item.capability] || humanizeIdentifier(item.capability, "外部研究能力")}</strong>
                  <em>{readinessStateNames[item.state] || humanizeIdentifier(item.state, "状态待确认")}</em>
                </div>
                <p>{readinessStateDescriptions[item.state] || "状态信息暂不可用。"}</p>
                <span>检索来源：{(item.provider_ids || []).map((id) => humanizeIdentifier(id, "已配置来源")).join(" · ") || "无"}</span>
                {(item.reasons || []).length ? (
                  <details>
                    <summary>查看审计信息</summary>
                    <small>{item.reasons.map((reason) => humanizeUiError(reason, "该能力尚未完成运行验收。")).join("；")}</small>
                  </details>
                ) : null}
              </article>
            ))}
          </div>
          {study ? (
            <>
              <div className="retrieval-overview-grid">
                <article>
                  <h3>当前检索步骤</h3>
                  {retrievalSteps.length ? retrievalSteps.map((step) => (
                    <p key={step.step_instance_id}>
                      <span>{stageTwoStepTitle[step.step_type] || stageFourStepTitle[step.step_type] || humanizeIdentifier(step.step_type, "外部检索步骤")}</span>
                      <b>{stepStatusLabel[step.status] || humanizeIdentifier(step.status, "状态已记录")}</b>
                    </p>
                  )) : <small>当前研究任务还没有外部检索步骤。</small>}
                </article>
                <article>
                  <h3>检索来源与查询目的</h3>
                  {(overview?.runs || []).length ? overview.runs.slice(-6).map((item) => (
                    <p key={item.run.run_id}>
                      <span>
                        {(item.query_plan.providers || []).map((id) => humanizeIdentifier(id, "检索来源")).join(" · ")}
                        <small>{humanizeResearchText(item.request.purpose, "补充研究证据")}</small>
                      </span>
                      <b>{humanizeIdentifier(item.run.execution_status, "状态已记录")}</b>
                    </p>
                  )) : <small>尚无检索运行记录。</small>}
                </article>
                <article>
                  <h3>检索覆盖情况</h3>
                  {latestCoverage ? (
                    <dl>
                      <div><dt>原始结果</dt><dd>{latestCoverage.raw_result_count}</dd></div>
                      <div><dt>去重后</dt><dd>{latestCoverage.deduplicated_result_count}</dd></div>
                      <div><dt>验证通过</dt><dd>{latestCoverage.verified_result_count}</dd></div>
                      <div><dt>开放全文</dt><dd>{latestCoverage.full_text_available_count}</dd></div>
                    </dl>
                  ) : <small>尚未生成检索覆盖报告。</small>}
                </article>
                <article>
                  <h3>研究任务资源</h3>
                  {(overview?.resources || []).length ? overview.resources.slice(0, 8).map((item) => (
                    <p key={item.resource_id}>
                      <span>{humanizeResearchText(item.title, "已绑定研究资源")}</span>
                      <b>{humanizeIdentifier(item.resource_type, "研究资源")}</b>
                    </p>
                  )) : <small>尚无已绑定资源。</small>}
                </article>
              </div>
              <form className="institution-connect-form" onSubmit={connectInstitution}>
                <div>
                  <h3>连接机构资源</h3>
                  <p>这里只记录会话范围；账号、密码和 MFA 只在学校官方页面中输入。</p>
                  <div className="institution-presets" aria-label="机构预设">
                    <button
                      type="button"
                      onClick={() => setInstitution({
                        ...institutionPresets.cmu,
                        ownerUserId: institution.ownerUserId,
                      })}
                    >
                      使用 CMU 官方入口
                    </button>
                    <small>校外访问建议先连接 vpn.cmu.edu 的 Full VPN。</small>
                  </div>
                </div>
                <label>
                  本机用户标识
                  <input required value={institution.ownerUserId} onChange={(event) => setInstitution({ ...institution, ownerUserId: event.target.value })} />
                </label>
                <label>
                  学校或图书馆
                  <input required value={institution.institutionId} onChange={(event) => setInstitution({ ...institution, institutionId: event.target.value })} />
                </label>
                <label>
                  官方登录页
                  <input required type="url" placeholder="https://library.example.edu/login" value={institution.targetUrl} onChange={(event) => setInstitution({ ...institution, targetUrl: event.target.value })} />
                </label>
                <button type="submit">打开官方登录页</button>
                {session ? (
                  <div className="institution-session-state">
                    <span>会话状态：{session.status}</span>
                    {session.status === "waiting_for_user" ? (
                      <button type="button" onClick={confirmInstitution}>我已完成登录与 MFA</button>
                    ) : null}
                  </div>
                ) : null}
              </form>
            </>
          ) : (
            <p className="external-research-context-note">
              进入研究任务后，这里会显示网络策略、检索步骤、检索来源、覆盖情况、资源和机构连接。
            </p>
          )}
          {panelError ? <p className="external-research-error">{panelError}</p> : null}
        </div>
      ) : null}
    </section>
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

function FirstRunGuide({ mode }) {
  return (
    <section className="first-run-guide" aria-label="第一次使用说明">
      <div className="first-run-guide-heading">
        <span>第一次使用</span>
        <strong>你只需要先提供起点，系统会带你走到下一次需要确认的位置。</strong>
      </div>
      <ol>
        <li>
          <span>1</span>
          <div>
            <strong>{mode === "project" ? "选择项目文件夹" : "写下研究想法"}</strong>
            <small>{mode === "project" ? "只读扫描，不修改源项目。" : "先收敛问题，不会直接宣布结论。"}</small>
          </div>
        </li>
        <li>
          <span>2</span>
          <div>
            <strong>系统整理少量可选方案</strong>
            <small>自动匹配本地资源和公开资料，不让你逐项挑工具。</small>
          </div>
        </li>
        <li>
          <span>3</span>
          <div>
            <strong>轮到你时再确认</strong>
            <small>每次只展示本次决定、影响和“提出修改”入口。</small>
          </div>
        </li>
      </ol>
    </section>
  );
}

function ResumeTaskCard({ task, onOpen }) {
  if (!task) return null;
  const needsAction = ["waiting_for_user", "failed", "blocked"].includes(task.status);
  return (
    <section className={`resume-task-card surface-card ${needsAction ? "needs-action" : ""}`} aria-label="继续上次研究">
      <div className="resume-task-copy">
        <span>{needsAction ? "上次研究需要你处理" : "上次研究仍在进行"}</span>
        <strong>{taskTitle(task)}</strong>
        <small>
          {needsAction
            ? "系统已经保留完成的步骤和失败原因，可以从卡点继续。"
            : "任务状态已持久化，打开后可以查看当前步骤和产物。"}
        </small>
      </div>
      <button type="button" className={needsAction ? "primary-button" : "secondary-button"} onClick={() => onOpen(task)}>
        {needsAction ? "继续处理" : "查看进度"}<ArrowRight size={17} />
      </button>
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
  academic_concept_normalization: "规范化学术概念",
  discovery_portfolio: "形成候选方向组合",
  scope_review: "确认研究边界",
  freeze_scope_contract: "冻结研究边界",
  scope_freeze: "冻结研究范围",
  scope_drafting: "起草研究边界",
  protocol_drafting: "起草实验契约",
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

const stageTwoStepTitle = {
  stage2_input_check: "检查阶段二输入",
  investigate_related_methods: "调查相关方法与基线",
  inventory_research_resources: "盘点本地研究资源",
  define_data_boundary: "定义数据边界与泄漏风险",
  diagnose_resource_gaps: "诊断资源缺口",
  define_resource_requirements: "把研究需求拆成数据包与工具包条件",
  discover_concrete_resources: "查找具体数据集、基准与工具包",
  validate_and_compare_resources: "验证并比较资源候选",
  generate_candidate_topics: "生成资源可行的具体课题",
  select_specific_topic: "负责人选择具体课题",
  draft_research_protocol: "起草实验协议",
  define_decision_rules: "定义五类科学判定规则",
  run_stage2_preflight: "执行实验前预检",
  validate_stage2_baseline: "运行并验证声明的基线",
  lint_research_contract: "检查契约占位符与科学语义",
  compile_research_contract: "编译运行、评估与分析规格",
  dry_run_research_contract: "试运行实验契约（不产生科学证据）",
  execution_readiness_gate: "验收阶段三执行准备度",
  assess_stage2_gate: "评估阶段二出口条件",
  research_contract_review: "确认实验契约",
  freeze_research_contract: "保存已确认的实验契约",
  freeze_stage2_locks: "保存可复核的实验版本",
  complete_stage2: "完成阶段二并移交正式实验",
  receive_stage2_handoff: "接收实验契约、实验蓝图与最小可行性验证记录",
  validate_scientific_handoff: "验证冻结的科学规格",
  stage3_build_admission: "批准开始建设正式实验资产",
  resolve_experiment_profile: "匹配受支持的实验类型",
  create_experiment_build_plan: "创建实验资产建设计划",
  approve_experiment_build_plan: "按冻结规格核准建设计划",
  resolve_external_resources: "绑定合同批准的冻结外部资源",
  resolve_or_build_assets: "复用、适配或构建实验资产",
  run_engineering_smoke_tests: "在开发分区执行工程冒烟测试",
  verify_spec_conformance: "验证具体实现符合科学规格",
  freeze_execution_package: "冻结正式执行包",
  formal_execution_admission: "执行第二次正式准入",
  stage3_admission: "验证正式执行包与不可变锁",
  compile_stage3_run_plan: "编译确定性实验运行矩阵",
  stage3_execution_gate: "负责人批准正式实验",
  execute_stage3_run_cell: "执行实验单元",
  evaluate_stage3_results: "确定性验证输出并计算统计量",
  build_stage3_evidence_ledger: "建立可验证证据图",
  materialize_stage3_verdict: "生成假设与研究判定",
  audit_stage3_completion: "审计阶段三完成条件",
  complete_stage3: "生成阶段三完成包并移交论文",
};

const stageFourStepTitle = {
  stage4_claim_intake: "接收阶段三冻结主张",
  publication_prerequisite_gate: "检查论文准入条件",
  venue_and_reporting_profile_freeze: "冻结投稿与报告规范",
  evaluation_transparency_register: "登记评估证据透明度",
  stage4_evidence_sufficiency_gate: "检查论文证据充分性",
  mandatory_reporting_register: "登记所有必须报告的结果",
  contribution_candidate_generation: "生成并比较核心贡献候选",
  publication_narrative_selection: "负责人批准论文主线",
  publication_narrative_contract_freeze: "冻结论文叙事契约",
  evidence_claim_mapping: "建立主张—证据映射",
  visual_argument_planning: "设计图表论证计划",
  visual_argument_plan_approval: "负责人批准图表计划",
  outline_generation_and_audit: "生成并审核论文大纲",
  draft_generation: "生成受证据约束的完整初稿",
  scientific_review_panel: "AI 科学家面板盲审",
  bounded_content_revision: "按审稿意见做有界修订",
  sci_ssci_preservation_audit: "执行 SCI/SSCI 主张强度与内容保真审计",
  figure_and_table_generation: "从冻结证据生成图表",
  visual_integrity_audit: "审计图表来源与表达完整性",
  final_visual_approval: "负责人批准最终图表",
  authorial_humanization: "按作者语气进行学术润色",
  humanization_integrity_audit: "验证润色未改变科学语义",
  humanization_author_approval: "负责人批准作者语气",
  reviewer_attack_surface_audit: "检查审稿人攻击面",
  reporting_and_disclosure_audit: "审计强制报告与 AI 披露",
  latex_typesetting: "按投稿规范排版 LaTeX",
  pdf_compile_and_verify: "编译并验证投稿 PDF",
  system_publication_readiness: "评估系统投稿就绪度",
  author_final_review_and_approval: "负责人最终投稿批准",
  completion_record: "生成可验证完成记录",
};

const stageFourEvidenceCategory = {
  sample_flow: {
    label: "样本流转",
    gap: "样本纳入、排除和最终分析数量尚未完整绑定。",
    action: "补齐冻结的样本流转记录与分母。",
  },
  eligibility: {
    label: "资格与排除",
    gap: "排除原因未覆盖全部被排除记录。",
    action: "在资格判定边界增加不可变原因码。",
  },
  abstention: {
    label: "弃权记录",
    gap: "评估器弃权数量或处理方式尚未完整记录。",
    action: "冻结弃权数量、原因与后续处理规则。",
  },
  threshold_provenance: {
    label: "阈值来源",
    gap: "已有阈值，但缺少冻结的选择依据。",
    action: "冻结校准语料、选择划分、目标函数和锁定时间。",
  },
  numeric_precision: {
    label: "数值精度",
    gap: "显示值相同，但未冻结精度或容差。",
    action: "补充计算精度、舍入和相等容差。",
  },
  evaluator_sensitivity: {
    label: "评估器敏感性",
    gap: "缺少第二评估器或替代配置的敏感性比较。",
    action: "增加第二评估器家族并报告一致性。",
  },
  verifier_ablation: {
    label: "验证器消融",
    gap: "尚未比较确定性、NLI 与混合验证器。",
    action: "执行 NLI-only、deterministic-only 与 hybrid 消融。",
  },
  packet_example: {
    label: "证据包示例",
    gap: "缺少可审计的证据包字段示例。",
    action: "冻结匿名证据包、字段不变量与验证示例。",
  },
  prospective_validation: {
    label: "前瞻验证",
    gap: "目前只有历史案例或同案例修复证据。",
    action: "使用新样本运行不复用已诊断案例的冻结后继实验。",
  },
  fault_localization_baseline: {
    label: "故障定位基线",
    gap: "尚未与普通日志、单元测试或其他定位方式比较。",
    action: "比较定位准确率、耗时和证据成本。",
  },
  semantic_preservation: {
    label: "语义保留",
    gap: "任务原生得分不能单独证明主张仍然有用。",
    action: "增加盲法人工有用性或信息量抽样审核。",
  },
  release_assets: {
    label: "复现发布物",
    gap: "证据包模式、评估器清单和回归样例尚未全部登记。",
    action: "登记匿名模式、评估器清单与回归样例。",
  },
};

const contractFieldLabel = {
  eligibility_rules: "资格与排除规则",
  evaluator_policy: "评估器策略",
  output_schema: "输出结构",
  statistical_rules: "统计判定规则",
  tasks: "任务范围",
  seeds: "随机种子",
  metrics: "指标定义",
  metric_direction: "主指标方向",
  primary_metric: "主指标",
  denominator: "统计分母",
  unit_of_analysis: "分析单位",
  sample_size: "样本规模",
  success_threshold: "成功阈值",
  minimum_meaningful_effect: "最小有意义差异",
  threshold_basis: "阈值依据",
  baseline: "对照方法",
  treatment: "实验方法",
  measurement_protocol: "测量规则",
  missing_data_policy: "缺失数据规则",
  inclusion_rules: "纳入规则",
  exclusion_rules: "排除规则",
  time_boundary: "时间边界",
  split_boundary: "数据划分边界",
  preprocessing: "预处理规则",
  warm_up_policy: "预热规则",
  cache_policy: "缓存规则",
};

function humanizeContractFieldList(value = "") {
  return String(value)
    .split(/[,;，；]/)
    .map((field) => field.trim())
    .filter(Boolean)
    .map((field) => contractFieldLabel[field] || humanizeIdentifier(field, "其他研究设置"))
    .join("、");
}

const evidenceDestinationLabel = {
  methods: "方法",
  results: "结果",
  limitations: "局限性",
  supplement: "补充材料",
  completion_package: "完成包",
};

function workflowBlockerMessage(step) {
  const blocker = step?.blocker || {};
  if (blocker.kind === "stage3_evidence_backfill_required") {
    return "第四阶段发现关键证据缺口，已回到第三阶段创建补数据后继任务。";
  }
  const message = blocker.reason || blocker.message;
  return step?.phase === "protocol"
    ? stageTwoMessage(message)
    : message || "当前步骤被阻止";
}

function WorkflowWindow({ phase, meta, onClose, children }) {
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="workflow-window-backdrop" role="presentation" onMouseDown={onClose}>
      <section className={`workflow-window is-${phase}`} role="dialog" aria-modal="true" aria-label={`${meta.title}工作窗口`} onMouseDown={(event) => event.stopPropagation()}>
        <header className="workflow-window-header">
          <div>
            <span>{meta.number} · Research Forge</span>
            <h2>{meta.title}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭阶段窗口"><X size={20} /></button>
        </header>
        <div className="workflow-window-body">{children}</div>
      </section>
    </div>
  );
}

function PhaseStepList({
  steps,
  currentStepIds = [],
  importedWorkflow = false,
  stepOutcomes = {},
}) {
  const stepOrder = [
    ...Object.keys(stepTitle),
    ...Object.keys(stageTwoStepTitle),
    ...Object.keys(stageFourStepTitle),
  ];
  const orderIndex = new Map(stepOrder.map((stepType, index) => [stepType, index]));
  const currentIds = new Set(currentStepIds);
  const latestByType = new Map();
  for (const step of steps) {
    const previous = latestByType.get(step.step_type);
    const shouldReplace = !previous
      || currentIds.has(step.step_instance_id)
      || (!currentIds.has(previous.step_instance_id)
        && String(step.updated_at || step.created_at || "").localeCompare(
          String(previous.updated_at || previous.created_at || ""),
        ) > 0);
    if (shouldReplace) latestByType.set(step.step_type, step);
  }
  const orderedSteps = [...latestByType.values()].sort((left, right) => (
    (orderIndex.get(left.step_type) ?? Number.MAX_SAFE_INTEGER)
    - (orderIndex.get(right.step_type) ?? Number.MAX_SAFE_INTEGER)
    || String(left.created_at || left.updated_at || "").localeCompare(
      String(right.created_at || right.updated_at || ""),
    )
  ));
  const historicalAttemptCount = Math.max(0, steps.length - orderedSteps.length);
  const currentStep = orderedSteps.find((step) => currentIds.has(step.step_instance_id))
    || orderedSteps.find((step) => ["waiting_for_user", "running", "retrying", "blocked", "failed"].includes(step.status))
    || orderedSteps.find((step) => step.status === "queued");
  const currentIndex = Math.max(0, orderedSteps.indexOf(currentStep));
  const nextStep = orderedSteps.slice(currentIndex + 1).find((step) => step.status === "queued");
  const acceptedCount = orderedSteps.filter((step) => (
    step.acceptance_status === "accepted"
    || (!step.acceptance_status && step.status === "succeeded")
  )).length;
  const renderStep = (step, index, compact = false) => {
    const imported = importedWorkflow
      || step.parameters?.execution_provenance === "legacy_bundle_import";
    const isCurrent = currentIds.has(step.step_instance_id);
    const outcome = stepOutcomes[step.step_type];
    return (
      <li
        key={step.step_instance_id}
        className={`${imported ? "is-imported" : `is-${step.status}`} ${outcome ? `has-outcome is-outcome-${outcome.tone}` : ""} ${isCurrent ? "is-current" : ""} ${compact ? "is-focus" : ""}`}
      >
        <i>{imported ? <DownloadSimple size={12} /> : <StepStatusGlyph status={step.status} />}</i>
        <div>
          <strong>{index + 1}. {stageTwoStepTitle[step.step_type] || stageFourStepTitle[step.step_type] || stepTitle[step.step_type] || humanizeIdentifier(step.step_type, "系统处理步骤")}</strong>
          <small>
            {isCurrent
              ? step.status === "waiting_for_user" ? "现在需要你决定" : "系统正在处理"
              : outcome?.detail
                || (imported ? "兼容导入 · 未在本轮执行" : `${executorLabel[step.executor_type] || humanizeIdentifier(step.executor_type, "系统服务")}`)}
          </small>
          {step.blocker ? <p>{workflowBlockerMessage(step)}</p> : null}
        </div>
        <span>{imported ? "导入记录" : outcome?.label || stepStatusLabel[step.status] || humanizeIdentifier(step.status, "状态已记录")}</span>
      </li>
    );
  };
  return (
    <aside className="phase-step-window">
      <header>
        <ListChecks size={18} />
        <div>
          <strong>阶段路线</strong>
          <small>{orderedSteps.length ? `科学验收 ${acceptedCount}/${orderedSteps.length} 步` : "等待接收交付后创建步骤"}</small>
        </div>
      </header>
      {currentStep ? (
        <div className="phase-route-focus">
          <span>现在</span>
          <ol>{renderStep(currentStep, currentIndex, true)}</ol>
          {nextStep ? (
            <>
              <ArrowRight size={15} />
              <span>接下来</span>
              <ol>{renderStep(nextStep, orderedSteps.indexOf(nextStep), true)}</ol>
            </>
          ) : null}
        </div>
      ) : null}
      {orderedSteps.length ? (
        <details className="phase-route-all">
          <summary>
            查看完整 {orderedSteps.length} 步顺序
            {historicalAttemptCount ? ` · ${historicalAttemptCount} 条历史尝试已收起` : ""}
          </summary>
          <ol>{orderedSteps.map((step, index) => renderStep(step, index))}</ol>
        </details>
      ) : (
        <p className="phase-route-empty">当前还没有阶段三任务。交接检查通过后，系统才会创建真实步骤。</p>
      )}
    </aside>
  );
}

function stageTwoStepOutcomes(data) {
  const artifacts = data?.artifacts || {};
  const preflight = artifacts["preflight_report.json"];
  const baseline = artifacts["baseline_validation_report.json"];
  const compileReport = artifacts["contract_compile_report.json"];
  const dryRun = artifacts["contract_dry_run_report.json"];
  const executionReadiness = artifacts["execution_readiness_gate.json"];
  const gate = artifacts["stage2_gate_report.json"];
  const build = artifacts["experiment_build_plan.json"];
  const outcomes = {};
  if (preflight) {
    const checks = preflight.checks || [];
    const failed = checks.filter((item) => item.status === "fail").length;
    const unknown = checks.filter((item) => item.status === "unknown").length;
    outcomes.run_stage2_preflight = {
      tone: failed ? "blocked" : unknown ? "warning" : "passed",
      label: failed ? "已检查 · 未通过" : unknown ? "已检查 · 待补证据" : "预检通过",
      detail: failed
        ? `${failed} 项条件未通过；检查程序本身已正常完成`
        : unknown
          ? `${unknown} 项条件仍缺证据`
          : "所有预检条件均已满足",
    };
  }
  if (baseline) {
    const verified = Boolean(baseline.baseline_verified || baseline.feasibility_mvp_verified);
    outcomes.validate_stage2_baseline = {
      tone: verified ? "passed" : "blocked",
      label: verified ? "最小可行性已验证" : "已检查 · 无可用结果",
      detail: verified
        ? "非科学烟雾验证通过；不会作为论文证据"
        : stageTwoMessage((baseline.unresolved_integrity_errors || [])[0] || "baseline is not verified"),
    };
  }
  if (compileReport) {
    outcomes.compile_research_contract = {
      tone: compileReport.compile_passed ? "passed" : "blocked",
      label: compileReport.compile_passed ? "契约可编译" : "契约需修订",
      detail: compileReport.compile_passed
        ? `可执行算法 ${compileReport.executable_algorithm_count}/${compileReport.required_algorithm_count}；生成 ${compileReport.run_specifications?.length || 0} 个运行规格`
        : `${compileReport.blocking_issues?.length || 0} 个科学语义阻塞项`,
    };
  }
  if (dryRun) {
    outcomes.dry_run_research_contract = {
      tone: dryRun.passed ? "passed" : "blocked",
      label: dryRun.passed ? "试运行通过" : "试运行未通过",
      detail: "只验证规格和绑定，不进入科学证据链",
    };
  }
  if (executionReadiness) {
    outcomes.execution_readiness_gate = {
      tone: executionReadiness.status === "ready" ? "passed" : "blocked",
      label: executionReadiness.status === "ready" ? "执行设计已验收" : "执行设计未就绪",
      detail: `算法 ${executionReadiness.executable_algorithms} · 阻塞项 ${executionReadiness.blocker_count}`,
    };
  }
  if (gate) {
    const ready = ["PASS", "CONDITIONAL_PASS", "DESIGN_READY"].includes(gate.status);
    outcomes.assess_stage2_gate = {
      tone: ready ? "passed" : "blocked",
      label: ready ? "出口条件满足" : "出口条件未满足",
      detail: ready
        ? "可以进入负责人审批"
        : uniqueStageTwoMessages(gate.blockers).join("；"),
    };
  }
  if (build) {
    outcomes.build_experiment_assets = {
      tone: build.stage2_mvp?.verified ? "passed" : "warning",
      label: build.stage2_mvp?.verified ? "最小可行性已验证" : "实验骨架已生成",
      detail: build.stage2_mvp?.verified
        ? "最小可行性已验证"
        : "只生成了实验清单和适配计划，尚不代表能够运行",
    };
  }
  return outcomes;
}

const experimentProfileLabel = {
  computational_paired_comparison_v1: "配对对照实验（v1）",
  computational_paired_comparison_v2: "配对连续型（聚类 Bootstrap v2）",
  paired_binary_independent_v1: "配对二元型（独立样本 / exact McNemar）",
  paired_binary_clustered_v1: "配对二元型（聚类 Bootstrap）",
};

function StudyWorkflowPanel({ workflow, activePhase, onPhaseChange }) {
  const [stage2Data, setStage2Data] = useState(null);
  const [stage3Data, setStage3Data] = useState(null);
  const [stage2Workflow, setStage2Workflow] = useState(null);
  const [stage2Busy, setStage2Busy] = useState("");
  const [stage2Error, setStage2Error] = useState("");
  const [stage3Busy, setStage3Busy] = useState("");
  const [stage3Error, setStage3Error] = useState("");
  const [stage4Data, setStage4Data] = useState(null);
  const [stage4Busy, setStage4Busy] = useState("");
  const [stage4Error, setStage4Error] = useState("");
  const [openContractRevision, setOpenContractRevision] = useState(false);
  const [contractRevisionReason, setContractRevisionReason] = useState("");
  const liveWorkflow = stage2Workflow || workflow;
  const studyId = liveWorkflow?.study?.study_id || "";
  const currentPhase = liveWorkflow?.study?.phase;
  const refreshStage2 = async () => {
    try {
      const payload = await api(`/api/studies/stage2?study_id=${encodeURIComponent(studyId)}`);
      setStage2Data(payload);
      setStage2Error("");
    } catch (error) {
      setStage2Error(error.message);
    }
  };
  useEffect(() => {
    if (studyId && liveWorkflow?.steps?.some((item) => item.phase === "protocol")) refreshStage2();
  }, [studyId]);
  const refreshStage3 = async () => {
    try {
      const payload = await api(`/api/studies/stage3?study_id=${encodeURIComponent(studyId)}`);
      setStage3Data(payload);
      setStage3Error("");
      return payload;
    } catch (error) {
      setStage3Error(error.message);
      return null;
    }
  };
  useEffect(() => {
    if (studyId && (currentPhase === "experiment" || currentPhase === "paper" || liveWorkflow?.steps?.some((item) => item.phase === "experiment"))) refreshStage3();
  }, [studyId, currentPhase]);
  useEffect(() => {
    if (!studyId || !stage3Busy) return undefined;
    const timer = window.setInterval(refreshStage3, 1200);
    return () => window.clearInterval(timer);
  }, [studyId, stage3Busy]);
  const refreshStage4 = async () => {
    try {
      const payload = await api(`/api/studies/stage4?study_id=${encodeURIComponent(studyId)}`);
      setStage4Data(payload);
      setStage4Error("");
    } catch (error) {
      setStage4Error(error.message);
    }
  };
  useEffect(() => {
    if (studyId && (currentPhase === "paper" || liveWorkflow?.steps?.some((item) => item.phase === "paper"))) refreshStage4();
  }, [studyId, currentPhase]);
  useEffect(() => {
    if (!studyId) return undefined;
    const stage4IsRunning = (stage4Data?.steps || []).some(
      (item) => ["running", "retrying"].includes(item.status),
    );
    if (!stage4IsRunning && !stage4Busy) return undefined;
    const timer = window.setInterval(refreshStage4, 1200);
    return () => window.clearInterval(timer);
  }, [studyId, stage4Busy, stage4Data]);
  if (!liveWorkflow?.study || !liveWorkflow?.steps?.length) {
    return <PhaseWaitingState phase={activePhase} currentPhase="discovery" />;
  }
  const chooseStage2Topic = async (topic, resourceCandidateIds) => {
    setStage2Busy(topic.topic_id);
    setStage2Error("");
    try {
      const result = await api("/api/studies/stage2/topics/select", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          topic_id: topic.topic_id,
          resource_candidate_ids: resourceCandidateIds,
          scope_overrides: {
            primary_outcome: topic.primary_outcome,
          },
        }),
      });
      setStage2Workflow(result.workflow);
      await refreshStage2();
      if (result.workflow?.study?.phase === "experiment") onPhaseChange("experiment");
    } catch (error) {
      setStage2Error(error.message);
    } finally {
      setStage2Busy("");
    }
  };
  const approveStage2 = async () => {
    setStage2Busy("approve");
    setStage2Error("");
    try {
      const result = await api("/api/studies/stage2/approve", {
        method: "POST",
        body: JSON.stringify({ study_id: studyId, decided_by: "project_owner" }),
      });
      setStage2Workflow(result.workflow);
      await refreshStage2();
      if (result.workflow?.study?.phase === "experiment") {
        onPhaseChange("experiment");
      }
    } catch (error) {
      setStage2Error(error.message);
    } finally {
      setStage2Busy("");
    }
  };
  const retryStage2Step = async (step) => {
    if (!step?.step_instance_id) return;
    setStage2Busy(`retry-${step.step_instance_id}`);
    setStage2Error("");
    try {
      const result = await api("/api/studies/retry-step", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          step_id: step.step_instance_id,
          decided_by: "project_owner",
          reason: "Retry the failed deterministic Stage 2 freeze step without repeating owner approval.",
        }),
      });
      setStage2Workflow(result);
      await refreshStage2();
      if (result?.study?.phase === "experiment") {
        onPhaseChange("experiment");
      }
    } catch (error) {
      setStage2Error(error.message);
    } finally {
      setStage2Busy("");
    }
  };
  const reviseStage2Protocol = async (protocolOverrides, revisionReason) => {
    setStage2Busy("revise-protocol");
    setStage2Error("");
    try {
      const result = await api("/api/studies/stage2/protocol/revise", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          decided_by: "project_owner",
          reason: revisionReason || "负责人补齐冻结前研究协议。",
          protocol_overrides: protocolOverrides,
        }),
      });
      setStage2Workflow(result.workflow);
      await refreshStage2();
      setOpenContractRevision(false);
      setContractRevisionReason("");
    } catch (error) {
      setStage2Error(error.message);
    } finally {
      setStage2Busy("");
    }
  };
  const initializeStage3 = async () => {
    setStage3Busy("initialize");
    setStage3Error("");
    try {
      const result = await api("/api/studies/stage3/initialize", {
        method: "POST",
        body: JSON.stringify({ study_id: studyId }),
      });
      setStage2Workflow(result.workflow);
      setStage3Data(result.stage3);
    } catch (error) {
      setStage3Error(error.message);
    } finally {
      setStage3Busy("");
    }
  };
  const initializeStage3Build = async () => {
    setStage3Busy("build-initialize");
    setStage3Error("");
    try {
      const result = await api("/api/studies/stage3/build/initialize", {
        method: "POST",
        body: JSON.stringify({ study_id: studyId }),
      });
      setStage3Data(result.stage3);
    } catch (error) {
      setStage3Error(error.message);
      await refreshStage3();
    } finally {
      setStage3Busy("");
    }
  };
  const advanceStage3Build = async () => {
    const latestStage3 = await refreshStage3();
    const currentStage3 = latestStage3 || stage3Data;
    const status = currentStage3?.overview?.status;
    const buildPlan = currentStage3?.build_plan;
    const requiresStage2Design = ["contract_revision_required", "unsupported_profile"].includes(status)
      || currentStage3?.build_steps?.some(
        (step) => step?.blocker?.kind === "contract_revision_required",
      );
    if (requiresStage2Design) {
      setStage3Error("");
      return;
    }
    if (!buildPlan) return initializeStage3Build();
    setStage3Busy("build-advance");
    setStage3Error("");
    try {
      if (["resource_blocked", "license_blocked"].includes(status)) {
        await api("/api/studies/stage3/build/resolve-resources", {
          method: "POST",
          body: JSON.stringify({ study_id: studyId, build_plan_id: buildPlan.build_plan_id }),
        });
      } else if (status === "build_plan_created" && buildPlan.build_mode === "build_from_blueprint") {
        await api("/api/studies/stage3/build/generate", {
          method: "POST",
          body: JSON.stringify({ study_id: studyId, build_plan_id: buildPlan.build_plan_id }),
        });
      } else if (status === "build_blocked" && buildPlan.build_mode === "build_from_blueprint") {
        await api("/api/studies/stage3/build/generate", {
          method: "POST",
          body: JSON.stringify({ study_id: studyId, build_plan_id: buildPlan.build_plan_id }),
        });
      } else if (status === "build_plan_created") {
        await api("/api/studies/stage3/build/freeze-ready-made", {
          method: "POST",
          body: JSON.stringify({
            study_id: studyId,
            build_plan_id: buildPlan.build_plan_id,
            trust_level: "trusted_local_project",
          }),
        });
      } else if (status === "assets_materialized") {
        await api("/api/studies/stage3/build/smoke-generated", {
          method: "POST",
          body: JSON.stringify({ study_id: studyId, build_plan_id: buildPlan.build_plan_id }),
        });
      } else if (status === "smoke_tested") {
        await api("/api/studies/stage3/build/freeze-generated", {
          method: "POST",
          body: JSON.stringify({ study_id: studyId, build_plan_id: buildPlan.build_plan_id }),
        });
      } else if (status === "execution_package_frozen") {
        await api("/api/studies/stage3/formal-admission", {
          method: "POST",
          body: JSON.stringify({
            study_id: studyId,
            execution_package_seal_id: currentStage3.execution_package_seal?.seal_id,
          }),
        });
      } else if (status === "formal_execution_admitted") {
        await initializeStage3();
        return;
      }
      await refreshStage3();
    } catch (error) {
      const latest = await refreshStage3();
      const latestStatus = latest?.overview?.status;
      const routedToStage2 = ["contract_revision_required", "unsupported_profile"].includes(latestStatus)
        || latest?.build_steps?.some(
          (step) => step?.blocker?.kind === "contract_revision_required",
        );
      setStage3Error(
        routedToStage2
          ? ""
          : "阶段三操作未完成，请查看当前步骤显示的阻塞原因。",
      );
    } finally {
      setStage3Busy("");
    }
  };
  const approveStage3 = async () => {
    if (!stage3Data?.gate?.gate_id) return;
    setStage3Busy("approve");
    setStage3Error("");
    try {
      const result = await api("/api/studies/gates/decide", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          gate_id: stage3Data.gate.gate_id,
          approve: true,
          decided_by: "project_owner",
          reason: "负责人批准冻结的 Stage 3 Run Plan。",
        }),
      });
      setStage2Workflow(result.workflow);
      await refreshStage3();
    } catch (error) {
      setStage3Error(error.message);
    } finally {
      setStage3Busy("");
    }
  };
  const requestStage3ContractRevision = async (revisionReason) => {
    const reason = String(revisionReason || "").trim();
    if (!reason) {
      setStage3Error("请先写明希望修改的实验契约内容。");
      return;
    }
    setContractRevisionReason(reason);
    setOpenContractRevision(true);
    onPhaseChange("protocol");
  };
  const completeStage3AsEvidenceBoundary = async (reasons = []) => {
    setStage3Busy("complete-boundary");
    setStage3Error("");
    try {
      await api("/api/studies/stage3/complete-boundary", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          reasons: Array.isArray(reasons) ? reasons : [],
        }),
      });
      await Promise.all([refreshStage3(), refreshStage4()]);
      onPhaseChange("paper");
    } catch (error) {
      setStage3Error(error.message);
    } finally {
      setStage3Busy("");
    }
  };
  const retryStage3Cell = async (item) => {
    if (!item.step_instance_id) return;
    setStage3Busy(item.run_cell_id);
    setStage3Error("");
    try {
      const result = await api("/api/studies/retry-step", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          step_id: item.step_instance_id,
          decided_by: "project_owner",
          reason: "负责人明确批准重试该失败的 Stage 3 RunCell。",
        }),
      });
      setStage2Workflow(result);
      await refreshStage3();
    } catch (error) {
      setStage3Error(error.message);
    } finally {
      setStage3Busy("");
    }
  };
  const initializeStage4 = async (venuePolicyId) => {
    setStage4Busy("initialize");
    setStage4Error("");
    try {
      const result = await api("/api/studies/stage4/initialize", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          venue_policy_id: venuePolicyId,
        }),
      });
      setStage2Workflow(result.workflow);
      setStage4Data(result.stage4);
    } catch (error) {
      setStage4Error(error.message);
    } finally {
      setStage4Busy("");
    }
  };
  const decideStage4Gate = async (gate, approve, revisionReason = "") => {
    setStage4Busy(gate.gate_id);
    setStage4Error("");
    try {
      const result = await api(
        approve
          ? "/api/studies/gates/decide"
          : "/api/studies/stage4/revise",
        {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          gate_id: gate.gate_id,
          ...(approve ? { approve: true } : {}),
          decided_by: "project_owner",
          reason: approve
            ? "负责人已审阅对应阶段四产物并批准。"
            : revisionReason || "负责人要求生成修订版本。",
        }),
        },
      );
      setStage2Workflow(result.workflow);
      setStage4Data(result.stage4 || null);
      if (!result.stage4) await refreshStage4();
    } catch (error) {
      setStage4Error(error.message);
    } finally {
      setStage4Busy("");
    }
  };
  const retryStage4Step = async (step) => {
    if (!step?.step_instance_id) return;
    setStage4Busy(`retry-${step.step_instance_id}`);
    setStage4Error("");
    try {
      const result = await api("/api/studies/retry-step", {
        method: "POST",
        body: JSON.stringify({
          study_id: studyId,
          step_id: step.step_instance_id,
          decided_by: "project_owner",
          reason: "负责人批准按当前平台规则重试该阶段四步骤。",
        }),
      });
      setStage2Workflow(result);
      await refreshStage4();
    } catch (error) {
      setStage4Error(error.message);
    } finally {
      setStage4Busy("");
    }
  };
  const evidenceBackfill = stage4Data?.artifacts?.stage4_evidence_backfill_request_v1;
  const workflowPhaseSteps = liveWorkflow.steps.filter((item) => item.phase === activePhase);
  const stage3BuildSteps = activePhase === "experiment" ? (stage3Data?.build_steps || []) : [];
  const activeSteps = [...new Map(
    [...workflowPhaseSteps, ...stage3BuildSteps].map((item) => [item.step_instance_id, item]),
  ).values()];
  const liveCurrentStepIds = liveWorkflow.study.current_step_ids || [];
  const stage3CurrentStepIds = stage3BuildSteps
    .filter((item) => ["running", "retrying", "blocked"].includes(item.status))
    .map((item) => item.step_instance_id);
  const visibleCurrentStepIds = [...new Set([...liveCurrentStepIds, ...stage3CurrentStepIds])];
  const currentStepIds = new Set(visibleCurrentStepIds);
  const importedWorkflow = isCompatibilityImport(liveWorkflow);
  const localBusy = activePhase === "protocol"
    ? stage2Busy
    : activePhase === "experiment"
      ? stage3Busy
      : activePhase === "paper"
        ? stage4Busy
        : "";
  const phaseLoading = currentPhase === activePhase && (
    Boolean(localBusy)
    || activeSteps.some(
      (item) => currentStepIds.has(item.step_instance_id) && executingStepStatuses.has(item.status),
    )
    || (activePhase === "protocol" && !stage2Data && !stage2Error)
  );
  return (
    <section className={`study-phase-workspace is-${activePhase}`}>
      <header className="phase-workspace-heading">
        <div>
          <span>{phaseTabMeta[activePhase]?.number} · {currentPhase === activePhase ? "当前阶段" : "研究记录"}</span>
          <h1>{phaseTabMeta[activePhase]?.title}</h1>
          <details className="technical-id">
            <summary>{compactStudyLabel(studyId)}</summary>
            <code>{studyId}</code>
          </details>
        </div>
        {phaseLoading ? (
          <div className="phase-loading-badge" role="status" aria-live="polite">
            <CircleNotch className="spinner" size={19} />
            <span>正在执行{phaseTabMeta[activePhase]?.title}</span>
          </div>
        ) : activePhase === "experiment" && evidenceBackfill?.status === "stage3_backfill_required" ? (
          <div className="phase-handoff-badge"><ClockCounterClockwise size={17} /><span>阶段四要求补充 {evidenceBackfill.required_item_ids?.length || 0} 项证据</span></div>
        ) : null}
      </header>
      <div className="phase-window-layout is-inline">
          <PhaseStepList
            steps={activeSteps}
            currentStepIds={visibleCurrentStepIds}
            importedWorkflow={importedWorkflow}
            stepOutcomes={activePhase === "protocol" ? stageTwoStepOutcomes(stage2Data) : {}}
          />
        <main className="phase-workspace-content">
          {activePhase === "discovery" ? (
            <article className="phase-overview-message">
              <Lightbulb size={24} />
              <div>
                <small>阶段一记录</small>
                <h3>项目扫描、外部信号与研究范围收敛</h3>
                <p>这里展示已保存的发现步骤。候选方向与研究范围审批由阶段一主流程产生，外部热度不会被当作科学证据。</p>
              </div>
            </article>
          ) : null}
          {activePhase === "protocol" && stage2Data ? (
            <StageTwoDecisionPanel
              data={stage2Data}
              workflow={liveWorkflow}
              busy={stage2Busy}
              error={stage2Error}
              onChoose={chooseStage2Topic}
              onRevise={reviseStage2Protocol}
              onApprove={approveStage2}
              onRetryStep={retryStage2Step}
              openRevisionInitially={openContractRevision}
              initialRevisionReason={contractRevisionReason}
            />
          ) : null}
          {activePhase === "protocol" && !stage2Data ? (
            <PhaseWaitingState phase="protocol" currentPhase={currentPhase} loading={phaseLoading} />
          ) : null}
          {activePhase === "experiment" ? (
            <StageThreePanel
              data={stage3Data}
              backfillRequest={evidenceBackfill}
              busy={stage3Busy}
              error={stage3Error}
              onInitialize={initializeStage3}
              onInitializeBuild={initializeStage3Build}
              onAdvanceBuild={advanceStage3Build}
              onApprove={approveStage3}
              onReviseContract={requestStage3ContractRevision}
              onCompleteBoundary={completeStage3AsEvidenceBoundary}
              onRetry={retryStage3Cell}
            />
          ) : null}
          {activePhase === "paper" ? (
            <StageFourPanel
              data={stage4Data}
              busy={stage4Busy}
              error={stage4Error}
              onInitialize={initializeStage4}
              onDecideGate={decideStage4Gate}
              onRetryStep={retryStage4Step}
              onOpenStage3={() => onPhaseChange("experiment")}
            />
          ) : null}
        </main>
      </div>
    </section>
  );
}

function PhaseWaitingState({ phase, currentPhase, loading = false }) {
  const meta = phaseTabMeta[phase];
  const current = phaseTabMeta[currentPhase];
  return (
    <article className={`phase-waiting-state ${loading ? "is-loading" : ""}`}>
      {loading
        ? <CircleNotch className="spinner" size={28} aria-hidden="true" />
        : <LockKey size={25} />}
      <div>
        <span>{loading ? "正在接收并检查阶段交付" : `${meta?.title || "下一阶段"}尚未接收正式交付`}</span>
        <h2>{loading ? `正在准备${meta?.title || "当前阶段"}工作台。` : `先完成${current?.title || "上一阶段"}中需要你的确认。`}</h2>
        <p>{loading ? "系统正在保存步骤、读取冻结产物并检查进入条件。完成后页面会自动更新。" : "系统会保留当前研究状态。批准后，冻结产物将自动交付到下一阶段，并切换到对应阶段。"}</p>
      </div>
    </article>
  );
}

const stageFourGateLabel = {
  publication_narrative_contract: "论文主线",
  visual_argument_plan: "图表论证计划",
  final_visuals: "最终图表",
  humanized_manuscript: "作者语气稿",
  submission_package: "最终投稿包",
};

function StageFourEvidenceGate({ artifacts, onOpenStage3, boundaryReportMode = false }) {
  const [detailOpen, setDetailOpen] = useState(false);
  const register = artifacts.evaluation_transparency_register_v1;
  const decision = artifacts.stage4_evidence_backfill_request_v1;
  if (!register && !decision) return null;

  const registeredItems = register?.items || [];
  const items = boundaryReportMode
    ? registeredItems.filter((item) => item.category === "release_assets")
    : registeredItems;
  const itemById = new Map(items.map((item) => [item.item_id, item]));
  const requiredItems = (decision?.required_item_ids || []).map((id) => itemById.get(id)).filter(Boolean);
  const disclosureItems = (decision?.disclosure_item_ids || []).map((id) => itemById.get(id)).filter(Boolean);
  const availableCount = items.filter((item) => item.status === "available").length;
  const status = decision?.status || "checking";
  const statusCopy = {
    checking: {
      label: "检查中",
      title: "正在核对论文所需证据",
      body: "平台正在区分需要回到实验阶段补齐的证据，与可以在论文中如实披露的限制。",
    },
    sufficient: {
      label: "证据充分",
      title: "当前证据允许继续撰写",
      body: "论文可以继续生成，但仍不能扩大阶段三冻结的主张边界。",
    },
    disclosure_only: {
      label: "须披露",
      title: "可以继续写作，但必须披露证据限制",
      body: "这些缺口不要求重跑实验，系统会把它们写入方法、局限或补充材料。",
    },
    stage3_backfill_required: {
      label: "已回退阶段三",
      title: "论文暂停，先补齐关键证据",
      body: "平台已创建后续补证据请求。历史实验、研究结论与当前稿件保持不变，不会把证据不足误写成研究失败。",
    },
  }[status];

  return (
    <>
      <article className={`stage4-evidence-gate is-${status}`}>
        <div className="stage4-evidence-gate-icon">
          {status === "sufficient" ? <ShieldCheck size={22} weight="fill" /> : status === "stage3_backfill_required" ? <Wrench size={22} /> : <WarningCircle size={22} />}
        </div>
        <div className="stage4-evidence-gate-copy">
          <span>{statusCopy.label}</span>
          <strong>{statusCopy.title}</strong>
          <p>{statusCopy.body}</p>
        </div>
        <div className="stage4-evidence-gate-actions">
          {items.length ? <button type="button" onClick={() => setDetailOpen(true)}>打开证据窗口</button> : null}
          {status === "stage3_backfill_required" ? <button type="button" className="primary-action" onClick={onOpenStage3}>进入阶段三</button> : null}
        </div>
      </article>

      {detailOpen ? (
        <div className="evidence-window-backdrop" role="presentation" onMouseDown={() => setDetailOpen(false)}>
          <section className="evidence-backfill-window" role="dialog" aria-modal="true" aria-label="论文证据充分性" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
              <span>证据充分性</span>
                <h2>论文证据充分性</h2>
                <p>{statusCopy.body}</p>
              </div>
              <button type="button" onClick={() => setDetailOpen(false)} aria-label="关闭证据窗口"><X size={20} /></button>
            </header>

            <div className="evidence-window-metrics">
              <article><small>当前适用</small><strong>{items.length}</strong><span>{boundaryReportMode ? "边界报告检查" : "类评估信息"}</span></article>
              <article><small>已有证据</small><strong>{availableCount}</strong><span>可直接进入论文</span></article>
              <article><small>需补数据</small><strong>{requiredItems.length}</strong><span>返回阶段三</span></article>
              <article><small>仅需披露</small><strong>{disclosureItems.length}</strong><span>不触发重跑</span></article>
            </div>

            {boundaryReportMode ? (
              <aside className="evidence-contract-change">
                <LockKey size={19} />
                <div>
                  <strong>本研究已选择“证据边界报告”</strong>
                  <p>阶段三没有执行正式实验，因此评估器消融、第二评估器和语义保留实验不适用于当前稿件。它们不会再被显示成你漏做的任务；真正缺失的数据与执行绑定沿用阶段三已冻结的边界说明。</p>
                </div>
              </aside>
            ) : null}

            {requiredItems.length ? (
              <section className="evidence-gap-section is-required">
                <div className="evidence-gap-heading">
                  <div><strong>必须回到第三阶段</strong><small>这些缺口会影响科学结论或关键方法主张</small></div>
                  <em>{requiredItems.length} 项</em>
                </div>
                <div className="evidence-gap-list">
                  {requiredItems.map((item) => {
                    const copy = stageFourEvidenceCategory[item.category] || {};
                    return (
                      <article key={item.item_id}>
                        <span>{copy.label || humanizeIdentifier(item.category, "证据限制")}</span>
                        <strong>{copy.gap || humanizeUiError(item.missing_reason, "当前证据仍有一项限制。")}</strong>
                        <p>{copy.action || humanizeResearchText(item.follow_up_action, "在论文中如实说明。")}</p>
                        <small>写入位置：{evidenceDestinationLabel[item.required_destination] || humanizeIdentifier(item.required_destination, "论文限制部分")}</small>
                      </article>
                    );
                  })}
                </div>
              </section>
            ) : null}

            {disclosureItems.length ? (
              <section className="evidence-gap-section is-disclosure">
                <div className="evidence-gap-heading">
                  <div><strong>论文中必须披露</strong><small>不阻止当前写作，但不能省略或美化</small></div>
                  <em>{disclosureItems.length} 项</em>
                </div>
                <div className="evidence-gap-list">
                  {disclosureItems.map((item) => {
                    const copy = stageFourEvidenceCategory[item.category] || {};
                    return (
                      <article key={item.item_id}>
                        <span>{copy.label || humanizeIdentifier(item.category, "证据限制")}</span>
                        <strong>{copy.gap || humanizeUiError(item.missing_reason, "当前证据仍有一项限制。")}</strong>
                        <p>{copy.action || humanizeResearchText(item.follow_up_action, "在论文中如实说明。")}</p>
                        <small>写入位置：{evidenceDestinationLabel[item.required_destination] || humanizeIdentifier(item.required_destination, "论文限制部分")}</small>
                      </article>
                    );
                  })}
                </div>
              </section>
            ) : null}

            {decision?.changed_contract_fields?.length ? (
              <aside className="evidence-contract-change">
                <LockKey size={19} />
                <div>
                  <strong>需要负责人批准新的实验契约版本</strong>
                  <p>{decision.changed_contract_fields.map((field) => contractFieldLabel[field] || humanizeIdentifier(field, "其他研究设置")).join("、")}</p>
                </div>
              </aside>
            ) : null}

            <footer>
              <span><ClockCounterClockwise size={17} /> 历史实验与研究结论永久保留</span>
              {status === "stage3_backfill_required" ? <button type="button" className="primary-action" onClick={() => { setDetailOpen(false); onOpenStage3(); }}>打开第三阶段补数据窗口</button> : null}
            </footer>
          </section>
        </div>
      ) : null}
    </>
  );
}

function StageFourPanel({
  data,
  busy,
  error,
  onInitialize,
  onDecideGate,
  onRetryStep,
  onOpenStage3,
}) {
  const [venuePolicyId, setVenuePolicyId] = useState("generic-journal-v1");
  const [revisionGateId, setRevisionGateId] = useState("");
  const [revisionReason, setRevisionReason] = useState("");
  if (!data?.initialized) {
    return (
      <section className="stage4-panel">
        <div className="stage4-heading">
          <div>
            <span>阶段四</span>
            <h3>论文叙事、图表与投稿审计</h3>
            <p>先选择投稿类型。最低文献量、数字证据量、摘要结构和图表预算由该版本化规范决定。</p>
          </div>
          <label>
            投稿规范
            <select value={venuePolicyId} onChange={(event) => setVenuePolicyId(event.target.value)}>
              <option value="generic-journal-v1">通用期刊论文</option>
              <option value="generic-short-report-v1">通用短论文</option>
              <option value="acm-conference-v1">ACM 会议</option>
              <option value="ieee-conference-v1">IEEE 会议</option>
              <option value="springer-lncs-v1">Springer LNCS</option>
              <option value="elsevier-journal-v1">Elsevier 期刊</option>
              <option value="nature-style-v1">Nature 风格</option>
            </select>
          </label>
        </div>
        <button className="primary-action" disabled={busy === "initialize"} onClick={() => onInitialize(venuePolicyId)}>
          {busy === "initialize" ? "正在建立阶段四…" : "建立阶段四论文工作流"}
        </button>
        {error ? <p className="stage4-error">{humanizeUiError(error)}</p> : null}
      </section>
    );
  }

  const artifacts = data.artifacts || {};
  const stage4Claim = data.claim_authority?.claims?.[0];
  const boundaryReportMode = stage4Claim?.maximum_claim_tier === "evidence_boundary_report"
    || stage4Claim?.evidence_level === "L0_boundary_only";
  const narrative = artifacts.publication_narrative_contract_draft_v1 || artifacts.publication_narrative_contract_v1;
  const visualPlan = artifacts.visual_argument_plan_v1 || artifacts.visual_argument_plan_frozen_v1;
  const integrity = [
    ["科学叙事", artifacts.reviewer_attack_surface_report_v1?.passed],
    ["强制报告", artifacts.reporting_integrity_report_v1?.passed],
    ["作者语气", artifacts.humanization_integrity_report_v1?.passed],
    ["图表完整性", artifacts.visual_integrity_report_v1?.passed],
  ];
  const activeGateStepBySubject = {
    publication_narrative_contract: "publication_narrative_selection",
    visual_argument_plan: "visual_argument_plan_approval",
    final_visuals: "final_visual_approval",
    humanized_manuscript: "humanization_author_approval",
    submission_package: "author_final_review_and_approval",
  };
  const waitingStepTypes = new Set(
    (data.steps || [])
      .filter((item) => item.status === "waiting_for_user")
      .map((item) => item.step_type),
  );
  const pendingGates = (data.gates || []).filter(
    (item) => item.status === "awaiting_user"
      && waitingStepTypes.has(activeGateStepBySubject[item.subject_type]),
  );
  const recoverableStep = (data.steps || []).find(
    (item) => ["blocked", "failed"].includes(item.status)
      && ["manuscript_depth_failure", "model_timeout", "transient_model_failure"].includes(
        item.blocker?.kind,
      ),
  );
  return (
    <section className="stage4-panel">
      <div className="stage4-heading">
        <div>
          <span>阶段四 · 论文与投稿控制</span>
          <h3>{boundaryReportMode ? "把当前研究收束为证据边界报告" : "把科学结论组织成可投稿的论文"}</h3>
          <p>
            {boundaryReportMode
              ? "当前没有合格的正式实验结果。阶段四只会说明已完成的设计、证据缺口、不可验证结论和未来补证据计划，不会生成支持或反驳的效果主张。"
              : `当前完成 ${data.overview?.completed_steps || 0}/${data.overview?.total_steps || 30} 个已保存步骤。论文写作模块不能扩大阶段三主张边界。`}
          </p>
        </div>
        <em className={`stage4-state is-${data.overview?.status}`}>{data.overview?.status === "completed" ? "已完成" : data.overview?.status === "attention_required" ? "需要负责人处理" : "运行中"}</em>
      </div>

      <StageFourEvidenceGate
        artifacts={artifacts}
        onOpenStage3={onOpenStage3}
        boundaryReportMode={(data?.claim_authority?.claims || []).some(
          (claim) => claim.maximum_claim_tier === "evidence_boundary_report" || claim.evidence_level === "L0_boundary_only"
        )}
      />

      {recoverableStep ? (
        <article className="stage4-gate-card">
          <div>
            <small>平台规则或临时执行问题</small>
            <strong>{stageFourStepTitle[recoverableStep.step_type] || humanizeIdentifier(recoverableStep.step_type, "论文处理步骤")}</strong>
            <p>{recoverableStep.blocker?.message ? humanizeUiError(recoverableStep.blocker.message) : "该步骤可以在保留已有产物的前提下单独重试。"}</p>
          </div>
          <div className="gate-decision-actions">
            <button
              className="primary-action"
              disabled={busy === `retry-${recoverableStep.step_instance_id}`}
              onClick={() => onRetryStep(recoverableStep)}
            >
              {busy === `retry-${recoverableStep.step_instance_id}` ? "正在重试" : "按当前规则重试本步骤"}
            </button>
          </div>
        </article>
      ) : null}

      {narrative ? (
        <article className="stage4-contract-card">
          <small>待批准 / 已冻结的论文主线</small>
          <h4>{humanizeResearchText(narrative.reader_memory_point || narrative.central_thesis)}</h4>
          <p>{humanizeResearchText(narrative.reader_problem)}</p>
          <dl>
            <div><dt>核心贡献</dt><dd>{humanizeResearchText(narrative.primary_advantage)}</dd></div>
            <div><dt>必须保留的负面结果或限制</dt><dd>{(narrative.mandatory_negative_claim_ids || []).length} 项</dd></div>
            <div><dt>正文必须说明的对照方法</dt><dd>{(narrative.comparator_decisions || []).filter((item) => item.placement === "main_text").map((item) => humanizeResearchText(item.comparator_name)).join("、") || "无"}</dd></div>
          </dl>
        </article>
      ) : null}

      {visualPlan ? (
        <article className="stage4-visual-card">
          <small>图表论证计划</small>
          <strong>{humanizeResearchText(visualPlan.visual_thesis)}</strong>
          <ul>
            {(visualPlan.figures || []).map((item) => (
              <li key={item.figure_id}><span>{Number(String(item.figure_id).match(/\d+/)?.[0] || 0) ? `图 ${Number(String(item.figure_id).match(/\d+/)?.[0])}` : "示意图"}</span><p>{humanizeResearchText(item.reader_question)}</p><em>{item.evidence_status === "evidence" ? "科学证据图" : "解释性图表"}</em></li>
            ))}
          </ul>
        </article>
      ) : null}

      {pendingGates.map((gate) => (
        <article className="stage4-gate-card" key={gate.gate_id}>
          <div>
            <small>现在需要你确认</small>
            <strong>请审阅并决定：{stageFourGateLabel[gate.subject_type] || humanizeIdentifier(gate.subject_type, "当前论文产物")}</strong>
            <p>拒绝不会改写历史产物，而是要求生成可审计的修订版本。</p>
          </div>
          <div className="gate-decision-actions">
            <button className="primary-action" disabled={busy === gate.gate_id} onClick={() => onDecideGate(gate, true)}>批准并继续</button>
            <button disabled={busy === gate.gate_id} onClick={() => { setRevisionGateId(gate.gate_id); setRevisionReason(""); }}>提出修改</button>
            <button disabled={busy === gate.gate_id} onClick={() => { setRevisionGateId(""); setRevisionReason(""); }}>暂不决定</button>
          </div>
          {revisionGateId === gate.gate_id ? (
            <div className="gate-revision-editor">
              <label>
                需要怎样修改？
                <textarea
                  value={revisionReason}
                  onChange={(event) => setRevisionReason(event.target.value)}
                  placeholder="例如：摘要只保留一个自然段；把负面结果移到限制部分；图 2 缩小并移到下一页。"
                />
              </label>
              <p>修改意见会进入审批记录，并驱动新的论文产物版本；已生成版本不会被覆盖。</p>
              <button disabled={busy === gate.gate_id || !revisionReason.trim()} onClick={() => onDecideGate(gate, false, revisionReason)}>
                提交修改意见
              </button>
            </div>
          ) : null}
        </article>
      ))}

      <div className="stage4-integrity-grid">
        {integrity.map(([label, passed]) => (
          <span key={label} className={passed === true ? "is-pass" : passed === false ? "is-fail" : "is-pending"}>
            <i>{passed === true ? "✓" : passed === false ? "×" : "·"}</i>
            <strong>{label}</strong>
            <small>{passed === true ? "通过" : passed === false ? "阻塞" : "待运行"}</small>
          </span>
        ))}
      </div>
      {artifacts.completion_record ? <p className="stage4-complete">完成记录已生成并绑定 {Object.keys(artifacts.completion_record.artifact_hashes || {}).length} 个产物哈希。</p> : null}
      {error ? <p className="stage4-error">{humanizeUiError(error)}</p> : null}
    </section>
  );
}

const STAGE2_DIMENSION_LABELS = {
  scientific_value: "科学价值",
  novelty: "新颖性",
  falsifiability: "可证伪性",
  project_relevance: "项目相关性",
  data_availability: "数据可用性",
  baseline_reproducibility: "基线可复现性",
  compute_feasibility: "算力可行性",
  implementation_feasibility: "实现可行性",
  auditability: "可审计性",
  expected_evidence_strength: "预期证据强度",
  compliance: "合规性",
  time_cost: "时间成本",
  monetary_cost: "资金成本",
};

const STAGE2_EVIDENCE_LABELS = {
  verified: "已验证",
  reported: "负责人声明",
  inferred: "系统推断",
  unknown: "未知",
};

const STAGE2_ASSESSMENT_LABELS = {
  strong: "强",
  adequate: "可接受",
  weak: "较弱",
  blocked: "阻塞",
  unknown: "未知",
};

const STAGE2_ACTION_LABELS = {
  "edit protocol inputs": "补充或修改协议输入",
  "supply resources": "补充所需资源",
  "rerun baseline": "重新运行基线",
  "approve Research Contract": "批准实验契约",
  "freeze Stage 2": "冻结阶段二",
};

function stageTwoMessage(value) {
  const message = String(value || "");
  const count = message.match(/\d+/)?.[0];
  if (message.includes("Research Contract cannot compile:")) {
    return "实验契约还有可自动补全的科学定义，请在右侧采用推荐方案或自行修改";
  }
  if (message.includes("Stage 2 Gate conditions failed")) {
    return "阶段二检查尚未通过；接受系统建议或修改实验设计后即可重新检查";
  }
  if (message.includes("project owner must approve the Research Contract")) {
    return "实验设计已经就绪，等待你确认后冻结";
  }
  if (message.includes("NUMERIC_EFFECT_THRESHOLD_MISSING")) {
    return "尚未确定多大的变化才算有实际意义";
  }
  if (message.includes("METRIC_PROTOCOL_MISSING")) {
    return "尚未确定计时范围、预热、缓存和重复测量规则";
  }
  if (message.includes("SECONDARY_OUTCOME_TOLERANCE_MISSING")) {
    return "成功条件提到了次要指标，但没有定义相应的保护阈值";
  }
  if (message.includes("preflight checks failed")) return `${count || "若干"} 项实验前预检已执行，但未通过`;
  if (message.includes("preflight checks are unknown")) return `${count || "若干"} 项实验前预检尚无足够证据`;
  if (message.includes("passing preflight checks rely on reported or inferred evidence")) {
    return `${count || "若干"} 项预检依赖负责人声明或系统推断，需要负责人接受该证据边界`;
  }
  if (message === "baseline is not verified") return "基线检查已执行，但没有得到可验证结果";
  if (message.startsWith("protocol fields remain unknown:")) {
    const fields = message.split(":").slice(1).join(":").trim();
    if (fields.includes("metric direction")) return "主指标方向尚未冻结；请确认指标是越高越好还是越低越好";
    return `研究协议仍有未冻结内容：${humanizeContractFieldList(fields) || "请查看协议编辑区"}`;
  }
  if (message.startsWith("scientific design fields remain unknown:")) {
    const fields = message.split(":").slice(1).join(":").trim();
    if (fields.includes("metric direction")) return "主指标方向尚未冻结；当前课题建议设为“越高越好”";
    return `科学设计仍有未冻结内容：${humanizeContractFieldList(fields) || "请查看协议编辑区"}`;
  }
  if (message.includes("Stage 2 MVP is not verified")) {
    const environment = message.match(/environment=([^)]+)/)?.[1];
    return environment === "adapter_required"
      ? "阶段二尚未完成最小可行性验证：系统需要为已选资源建立输入适配，并运行 2–3 个冒烟案例"
      : "阶段二尚未完成最小可行性验证：需要验证最小环境、实验两组和指标计算";
  }
  if (message.includes("ExperimentPreflightError") && message.includes("data/evaluation.jsonl")) {
    return "所选数据尚未绑定到最小可行性验证输入；系统应自动生成适配映射";
  }
  if (message.startsWith("freeze conditions failed:")) return "阶段二冻结条件尚未全部满足，请查看下方具体条件";
  if (message.includes("Complete and verify the non-scientific Stage 2 MVP")) {
    return "由系统完成不产生科学结论的最小可行性验证后，再由负责人审批实验契约";
  }
  if (message === "Approve the Research Contract.") return "批准并冻结实验契约";
  if (message === "Resolve blockers and generate a protocol vNext.") return "解决阻塞项并生成下一版协议";
  return humanizeUiError(message, "阶段二仍有一项需要补充的科学条件。")
    || "阶段二存在尚未满足的条件";
}

function uniqueStageTwoMessages(values = []) {
  return [...new Set(values.map(stageTwoMessage).filter(Boolean))];
}

function stageTwoContractRepairProposal(protocol, compileReport) {
  const issues = compileReport?.blocking_issues || [];
  if (!issues.length) return null;
  const issueCodes = new Set(
    issues.map((item) => String(item).split(":")[0].trim()),
  );
  const metricName = String(protocol?.primary_metric?.name || "主指标");
  const direction = String(protocol?.primary_metric?.direction || "").toLowerCase();
  const isTimingMetric = ["latency", "runtime", "duration"].some(
    (token) => metricName.toLowerCase().includes(token),
  );
  const recommendations = [];
  const overrides = {};
  if (issueCodes.has("NUMERIC_EFFECT_THRESHOLD_MISSING")) {
    overrides.minimum_meaningful_effect = "0.05";
    overrides.threshold_basis = isTimingMetric
      ? "采用相对变化尺度；5% 是冻结前的保守最小实际差异，正式结果同时报告效应量与区间。"
      : "采用主指标原始尺度上的绝对变化；0.05 是冻结前的保守最小实际差异，正式结果同时报告效应量与区间。";
    overrides.statistical_rules = {
      effect_threshold: 0.05,
      effect_scale: isTimingMetric ? "relative" : "absolute",
      effect_unit: isTimingMetric ? "fractional change in elapsed time" : `${metricName} units`,
      missing_cell_policy: "inconclusive",
      confidence_level: 0.95,
    };
    recommendations.push({
      title: "最小有效差异",
      value: isTimingMetric ? "相对改善至少 5%" : "主指标至少变化 0.05",
      reason: "避免把几乎没有实际意义的微小波动判成成功；你之后仍可修改。",
    });
  }
  if (issueCodes.has("METRIC_PROTOCOL_MISSING")) {
    overrides.measurement_protocol = {
      unit: "milliseconds per eligible case",
      measured_boundary: "after validated input is ready and before result serialization",
      warm_up_runs: 1,
      caching_policy: "use the same frozen cache state in both arms",
      clock: "time.perf_counter_ns monotonic clock",
      repetitions_per_case: 5,
      aggregation: "median per case before paired comparison",
    };
    recommendations.push({
      title: "计时规则",
      value: "毫秒/样本 · 预热 1 次 · 每例重复 5 次取中位数",
      reason: "两组使用相同缓存状态和单调时钟，避免初始化噪声影响比较。",
    });
  }
  if (issueCodes.has("SECONDARY_OUTCOME_TOLERANCE_MISSING")) {
    const improvement = direction.includes("min")
      ? `${metricName} 相对基线降低至少 5%`
      : `${metricName} 相对基线提高至少 5%`;
    overrides.success_threshold = improvement;
    overrides.statistical_rules = {
      ...(overrides.statistical_rules || {}),
      protected_secondary_outcomes: [],
    };
    recommendations.push({
      title: "成功判定范围",
      value: `本轮只由 ${metricName} 决定`,
      reason: "当前没有登记次要指标，因此不虚构保护条件；需要时可在自定义修改中补充。",
    });
  }
  return {
    recommendations,
    overrides,
    rawIssues: issues,
  };
}

function StageTwoProtocolEditor({
  protocol,
  busy,
  onRevise,
  initialRevisionReason = "",
}) {
  const makeTemplate = () => {
    const declaredFeasibilityChecks = [
      "data_loadable",
      "data_schema_readable",
      "inclusion_exclusion_executable",
      "split_boundary_verified",
      "leakage_controls_verified",
      "metric_unit_test_passed",
      "statistical_synthetic_test_passed",
      "dependency_installable",
      "hardware_sufficient",
      "logging_validated",
      "seed_fixable",
      "artifact_preservation_validated",
      "binding_validated",
      "failure_detection_validated",
      "budget_reasonable",
      "sensitive_data_guard_passed",
      "compliance_cleared",
    ];
    const preflightAssertions = protocol?.preflight_assertions || {};
    return ({
    primary_metric: protocol?.primary_metric?.name === "primary_metric" ? "" : protocol?.primary_metric?.name || "",
    metric_direction: protocol?.primary_metric?.direction === "predeclare_before_freeze" ? "" : protocol?.primary_metric?.direction || "",
    denominator: protocol?.primary_metric?.denominator === "unknown" ? "" : protocol?.primary_metric?.denominator || "",
    sample_size: protocol?.sample_size === "unknown" ? "" : protocol?.sample_size || "",
    variance_unit: ["unknown", "computational run", "predeclared computational task, sample, or run"].includes(protocol?.variance_unit)
      ? ""
      : protocol?.variance_unit || "",
    statistical_power: protocol?.statistical_power === "unknown" ? "" : protocol?.statistical_power || "",
    statistical_test: protocol?.statistical_test === "unknown" ? "" : protocol?.statistical_test || "",
    confidence_interval: protocol?.confidence_interval === "unknown" ? "" : protocol?.confidence_interval || "",
    falsification_condition: protocol?.falsification_condition === "unknown" ? "" : protocol?.falsification_condition || "",
    success_threshold: protocol?.success_threshold === "unknown" ? "" : protocol?.success_threshold || "",
    minimum_meaningful_effect: protocol?.minimum_meaningful_effect === "unknown" ? "" : protocol?.minimum_meaningful_effect || "",
    threshold_basis: protocol?.scientific_validity_contract?.threshold_basis || "",
    metric_implementation: protocol?.metric_implementation === "unknown" ? "" : protocol?.metric_implementation || "",
    randomization_method: protocol?.randomization_method === "unknown" ? "" : protocol?.randomization_method || "",
    seeds: protocol?.seeds || [],
    splits: ["default"],
    repetitions: protocol?.repetitions || 1,
    experiment_profile: "computational_paired_comparison_v1",
    tasks: protocol?.tasks?.length ? protocol.tasks : ["formal-primary-task"],
    baseline_behavior: protocol?.baseline?.behavior || "",
    baseline_experiment_id: protocol?.baseline?.experiment_id || "",
    baseline_action_id: protocol?.baseline?.action_id || "",
    treatment_experiment_id: protocol?.treatment?.experiment_id || "treatment-v1",
    treatment_action_id: protocol?.treatment?.action_id || "action-treatment",
    treatment_behavior: protocol?.treatment?.behavior || "",
    timing_unit: protocol?.primary_metric?.measurement_protocol?.unit || "milliseconds per eligible case",
    timing_boundary: protocol?.primary_metric?.measurement_protocol?.measured_boundary || "",
    timing_warm_up_runs: protocol?.primary_metric?.measurement_protocol?.warm_up_runs ?? 1,
    timing_repetitions: protocol?.primary_metric?.measurement_protocol?.repetitions_per_case ?? 5,
    timing_cache_policy: protocol?.primary_metric?.measurement_protocol?.caching_policy || "use the same frozen cache state in both arms",
    output_schema: {
      format: "json",
      record_layout: "summary",
      metric_field: protocol?.primary_metric?.name || "",
      denominator_field: "denominator",
      sample_id_field: "sample_ids",
    },
    statistical_rules: {
      method: "paired_mean_difference",
      effect_threshold: 0.05,
      missing_cell_policy: "inconclusive",
      confidence_level: 0.95,
    },
    run_baseline: Boolean(protocol?.run_baseline),
    local_mvp_package_accepted: declaredFeasibilityChecks.every(
      (field) => Boolean(preflightAssertions[field]),
    ),
    dependency_lock: protocol?.dependency_lock === "unknown" ? "" : protocol?.dependency_lock || "",
    quality_control: protocol?.quality_control || [],
    reproduction_steps: protocol?.reproduction_steps || [],
    budget: protocol?.budget || {},
    ...preflightAssertions,
  });
  };
  const [draft, setDraft] = useState(() => makeTemplate());
  const [revisionReason, setRevisionReason] = useState(
    initialRevisionReason,
  );
  const [parseError, setParseError] = useState("");
  useEffect(() => {
    setDraft(makeTemplate());
    setRevisionReason(initialRevisionReason);
    setParseError("");
  }, [protocol?.research_contract_version, initialRevisionReason]);
  const update = (field, value) => setDraft((current) => ({ ...current, [field]: value }));
  const setLocalMvpPackage = (enabled) => setDraft((current) => ({
    ...current,
    local_mvp_package_accepted: enabled,
    data_loadable: enabled,
    data_schema_readable: enabled,
    inclusion_exclusion_executable: enabled,
    split_boundary_verified: enabled,
    leakage_controls_verified: enabled,
    metric_unit_test_passed: enabled,
    statistical_synthetic_test_passed: enabled,
    dependency_installable: enabled,
    hardware_sufficient: enabled,
    logging_validated: enabled,
    seed_fixable: enabled,
    artifact_preservation_validated: enabled,
    binding_validated: enabled,
    failure_detection_validated: enabled,
    budget_reasonable: enabled,
    sensitive_data_guard_passed: enabled,
    compliance_cleared: enabled,
    approved_resource_substitutions: enabled
      ? ["负责人仅接受冻结的本地合成资源用于阶段二最小可行性验证。"]
      : [],
  }));
  const submit = () => {
    try {
      const seeds = String(draft.seeds_text ?? (draft.seeds || []).join(","))
        .split(/[,，\s]+/)
        .filter(Boolean)
        .map((value) => Number(value));
      if (seeds.some((value) => !Number.isInteger(value))) throw new Error("种子必须是用逗号分隔的整数");
      const parsed = { ...draft, seeds };
      if (["latency", "runtime", "duration"].some(
        (token) => String(draft.primary_metric || "").toLowerCase().includes(token),
      )) {
        parsed.measurement_protocol = {
          unit: draft.timing_unit,
          measured_boundary: draft.timing_boundary,
          warm_up_runs: Number(draft.timing_warm_up_runs),
          caching_policy: draft.timing_cache_policy,
          clock: "time.perf_counter_ns monotonic clock",
          repetitions_per_case: Number(draft.timing_repetitions),
          aggregation: "median per case before paired comparison",
        };
      }
      delete parsed.seeds_text;
      setParseError("");
      onRevise(parsed, revisionReason.trim() || "负责人修改冻结前实验契约草稿。");
    } catch (error) {
      setParseError(error.message);
    }
  };
  return (
    <details className="stage2-protocol-editor">
      <summary>我想调整实验设计</summary>
      <p>下面是高级设置。只改你明确理解的项目；不修改时，系统会保留当前建议。保存后先重新检查，不会直接开始正式实验。</p>
      <div className="stage2-contract-form">
        <label>
          系统推荐的实验类型
          <select value={draft.experiment_profile || ""} onChange={(event) => update("experiment_profile", event.target.value)}>
            <option value="computational_paired_comparison_v1">同任务配对比较</option>
          </select>
          <small>阶段二根据“同一任务上的基线与实验组比较”自动选择；你不需要理解内部类型代码。</small>
        </label>
        <label>主要结果指标<small>实验最主要比较的数字，例如准确率或平均延迟</small><input value={draft.primary_metric || ""} onChange={(event) => update("primary_metric", event.target.value)} /></label>
        <label>怎样才算更好<select value={draft.metric_direction || ""} onChange={(event) => update("metric_direction", event.target.value)}><option value="">请选择</option><option value="maximize">数值越高越好</option><option value="minimize">数值越低越好</option></select></label>
        <label>哪些样本计入结果<small>说明最终比例或均值的分母</small><input value={draft.denominator || ""} onChange={(event) => update("denominator", event.target.value)} /></label>
        <label>计划使用多少样本<input value={draft.sample_size || ""} onChange={(event) => update("sample_size", event.target.value)} /></label>
        <label>什么算一个独立观察<small>例如：一个任务、一只股票或一个配对样本</small><input value={draft.variance_unit || ""} onChange={(event) => update("variance_unit", event.target.value)} placeholder="例如：一个配对样本" /></label>
        <label>达到什么结果才算成功<input value={draft.success_threshold || ""} onChange={(event) => update("success_threshold", event.target.value)} /></label>
        <label>小于多少差异可视为没有实际意义<input value={draft.minimum_meaningful_effect || ""} onChange={(event) => update("minimum_meaningful_effect", event.target.value)} /></label>
        <label>为什么选择这个阈值<textarea value={draft.threshold_basis || ""} onChange={(event) => update("threshold_basis", event.target.value)} placeholder="例如：指标范围为 0–1；0.40 足以区分常数基线与有效方法。" /></label>
        <label>重复实验使用的随机种子<small>用逗号分隔；更多种子更稳健，但运行更久</small><input value={draft.seeds_text ?? (draft.seeds || []).join(", ")} onChange={(event) => update("seeds_text", event.target.value)} placeholder="1, 2, 3" /></label>
        <label>比较结果的方法<small>不知道时保留系统建议</small><input value={draft.statistical_test || ""} onChange={(event) => update("statistical_test", event.target.value)} /></label>
        {["latency", "runtime", "duration"].some(
          (token) => String(draft.primary_metric || "").toLowerCase().includes(token),
        ) ? (
          <>
            <label>计时单位<input value={draft.timing_unit || ""} onChange={(event) => update("timing_unit", event.target.value)} /></label>
            <label>每个样本重复次数<input type="number" min="1" value={draft.timing_repetitions || 1} onChange={(event) => update("timing_repetitions", event.target.value)} /></label>
            <label>预热次数<input type="number" min="0" value={draft.timing_warm_up_runs || 0} onChange={(event) => update("timing_warm_up_runs", event.target.value)} /></label>
            <label>缓存规则<input value={draft.timing_cache_policy || ""} onChange={(event) => update("timing_cache_policy", event.target.value)} /></label>
            <label className="stage2-contract-editor-wide">
              从哪里开始、到哪里结束计时
              <textarea value={draft.timing_boundary || ""} onChange={(event) => update("timing_boundary", event.target.value)} />
            </label>
          </>
        ) : null}
        <label className="stage2-contract-editor-wide">
          正式评测任务
          <textarea
            value={(draft.tasks || [""])[0] || ""}
            onChange={(event) => update("tasks", [event.target.value])}
          />
          <small>写清系统要对什么输入做什么操作，以及用什么答案作为正确结果。</small>
        </label>
        <label className="stage2-contract-editor-wide">
          对照组怎么运行
          <textarea value={draft.baseline_behavior || ""} onChange={(event) => update("baseline_behavior", event.target.value)} />
        </label>
        <label className="stage2-contract-editor-wide">
          实验组怎么运行，以及它与对照组唯一的差别
          <textarea value={draft.treatment_behavior || ""} onChange={(event) => update("treatment_behavior", event.target.value)} />
        </label>
        <label className="stage2-checkbox-field">
          <input type="checkbox" checked={Boolean(draft.run_baseline)} onChange={(event) => update("run_baseline", event.target.checked)} />
          允许阶段二运行一次非科学基线冒烟验证
        </label>
        <label className="stage2-checkbox-field stage2-contract-editor-wide">
          <input
            type="checkbox"
            checked={Boolean(draft.local_mvp_package_accepted)}
            onChange={(event) => setLocalMvpPackage(event.target.checked)}
          />
          接受“本地合成的最小可行性验证条件包”
          <small>一次性声明：数据可读、划分固定、无外传、依赖与硬件可用，并授权系统验证指标、日志、失败检测和产物哈希。它只证明阶段三可执行，不作为科学结果。</small>
        </label>
      </div>
      <label>
        修改说明
        <textarea
          value={revisionReason}
          onChange={(event) => setRevisionReason(event.target.value)}
          placeholder="例如：把成功阈值改为 0.40，并把种子增加到 1、2、3；其他内容保持不变。"
        />
      </label>
      {parseError ? <small className="stage2-panel-error">{parseError}</small> : null}
      <button type="button" disabled={busy} onClick={submit}>
        {busy === "revise-protocol" ? <CircleNotch size={15} className="spin" /> : <ArrowRight size={15} />}
        保存新版本并重新检查
      </button>
    </details>
  );
}

const stageTwoGateStatusCopy = {
  PASS: "可以进入正式实验",
  CONDITIONAL_PASS: "满足条件后可进入正式实验",
  DESIGN_READY: "实验设计已就绪",
  FAIL: "还不能进入正式实验",
};

function stageTwoMvpAcceptanceOverrides() {
  return {
    run_baseline: true,
    local_mvp_package_accepted: true,
    data_loadable: true,
    data_schema_readable: true,
    inclusion_exclusion_executable: true,
    split_boundary_verified: true,
    leakage_controls_verified: true,
    metric_unit_test_passed: true,
    statistical_synthetic_test_passed: true,
    dependency_installable: true,
    hardware_sufficient: true,
    logging_validated: true,
    seed_fixable: true,
    artifact_preservation_validated: true,
    binding_validated: true,
    failure_detection_validated: true,
    budget_reasonable: true,
    sensitive_data_guard_passed: true,
    compliance_cleared: true,
    approved_resource_substitutions: [
      "负责人接受阶段二冻结的本地合成资源，仅用于最小可行性验证。",
    ],
  };
}

function StageTwoDecisionPanel({
  data,
  workflow,
  busy,
  error,
  onChoose,
  onRevise,
  onApprove,
  onRetryStep,
  openRevisionInitially = false,
  initialRevisionReason = "",
}) {
  const [revisionOpen, setRevisionOpen] = useState(false);
  useEffect(() => {
    if (openRevisionInitially) setRevisionOpen(true);
  }, [openRevisionInitially]);
  const artifacts = data?.artifacts || {};
  const topicArtifact = artifacts["candidate_topics.json"] || {};
  const candidates = topicArtifact.candidates || [];
  const resourceEvaluation = artifacts["resource_candidate_evaluation.json"];
  const resourceCandidates = resourceEvaluation?.candidates || [];
  const shortlists = resourceEvaluation?.shortlists || [];
  const gate = artifacts["stage2_gate_report.json"];
  const protocol = artifacts["protocol.draft.json"];
  const compileReport = artifacts["contract_compile_report.json"];
  const executionReadiness = artifacts["execution_readiness_gate.json"];
  const repairProposal = useMemo(
    () => stageTwoContractRepairProposal(protocol, compileReport),
    [protocol?.research_contract_version, compileReport?.compile_report_id],
  );
  const resourcePackages = useMemo(() => buildStageTwoResourcePackages({
    topics: candidates,
    resources: resourceCandidates,
    shortlists,
    recommendedTopicId: topicArtifact.recommended_topic_id,
  }), [topicArtifact.recommended_topic_id, resourceEvaluation?.generated_at]);
  const topicStep = workflow.steps.find((item) => item.step_type === "select_specific_topic");
  const reviewStep = workflow.steps
    .filter((item) => item.step_type === "research_contract_review")
    .sort((left, right) => String(left.updated_at).localeCompare(String(right.updated_at)))
    .at(-1);
  const awaitingTopic = topicStep?.status === "waiting_for_user";
  const contractDecisionRequested = [
    ...(gate?.required_user_decisions || []),
    ...(gate?.allowed_next_actions || []),
  ].some((item) => String(item).toLowerCase().includes("research contract"));
  const awaitingContract = gate?.status !== "FAIL" && (
    reviewStep?.status === "waiting_for_user"
    || (!reviewStep && contractDecisionRequested)
  );
  const failedStage2SystemStep = workflow.steps
    .filter((item) => (
      item.phase === "protocol"
      && item.status === "failed"
      && ["freeze_research_contract", "freeze_stage2_locks", "complete_stage2"].includes(item.step_type)
    ))
    .sort((left, right) => {
      const leftIsRootFailure = left.status === "failed" ? 1 : 0;
      const rightIsRootFailure = right.status === "failed" ? 1 : 0;
      return leftIsRootFailure - rightIsRootFailure
        || String(left.updated_at).localeCompare(String(right.updated_at));
    })
    .at(-1);
  if (!candidates.length && !gate && !compileReport) return null;
  return (
    <div className="stage2-decision-panel">
      <div className="stage2-decision-heading">
        <div>
          <span>{awaitingTopic || awaitingContract || repairProposal ? "现在需要你处理" : "阶段二进展"}</span>
          <strong>{awaitingTopic
            ? "选择一个研究方案，不再逐项挑资源"
            : repairProposal
              ? "实验契约还有几项定义需要补齐"
            : gate
              ? stageTwoGateStatusCopy[gate.status] || "阶段二状态已更新"
              : "正在形成实验协议"}</strong>
        </div>
        <LockKey size={20} />
      </div>
      {awaitingTopic ? (
        <>
          <section className="stage2-package-intro">
            <div>
              <span>系统已替你收敛</span>
              <strong>从 {resourceCandidates.length} 个底层候选中组合为 {resourcePackages.length} 个方案</strong>
              <p>你只需要选择想回答的问题。具体文件、工具和缺失资源会随方案一起冻结，不必逐项勾选。</p>
            </div>
            <ShieldCheck size={22} weight="fill" />
          </section>
          <div className="stage2-package-list">
            {resourcePackages.map((resourcePackage) => {
              const topic = resourcePackage.topic;
              return (
                <article key={resourcePackage.package_id} className={resourcePackage.recommended ? "is-recommended" : ""}>
                  <header>
                    <div>
                      <span>{resourcePackage.recommended ? "推荐方案" : "备选方案"}</span>
                      <strong>{resourcePackage.name}</strong>
                    </div>
                    <em>{topic.status === "ready" ? "可直接推进" : "有条件可推进"}</em>
                  </header>
                  <h3>{humanizeResearchText(topic.title)}</h3>
                  <p>{resourcePackage.effect}</p>
                  <div className="stage2-package-outcome">
                    <small>选它以后回答</small>
                    <strong>{humanizeResearchText(topic.research_question)}</strong>
                  </div>
                  <ul className="stage2-package-resources">
                    {resourcePackage.resources.map((item) => (
                      <li key={item.candidate_id}>
                        <Database size={15} />
                        <span>
                          <strong>{humanizeResearchText(item.name)}</strong>
                          <small>
                            {item.need_type === "dataset" ? "数据包" : item.need_type === "implementation" ? "实验工具" : "测评资源"}
                            {" · "}
                            {item.eligibility === "eligible" ? "已验证可用" : "最小可行性验证中需先检查"}
                          </small>
                        </span>
                      </li>
                    ))}
                    {resourcePackage.missingTypes.map((type) => (
                      <li key={type} className="is-planned">
                        <Wrench size={15} />
                        <span><strong>{type === "benchmark" ? "测评基准" : type}</strong><small>进入阶段三前获取或构建</small></span>
                      </li>
                    ))}
                  </ul>
                  <div className="stage2-package-meta">
                    <span>{resourcePackage.effort}</span>
                    <span>覆盖 {resourcePackage.requiredTypeLabels.length - resourcePackage.missingTypes.length}/{resourcePackage.requiredTypeLabels.length} 类资源</span>
                  </div>
                  <details className="stage2-topic-details">
                    <summary>查看科学边界、风险和替代设计</summary>
                    <dl>
                      <div><dt>与阶段一关系</dt><dd>{humanizeResearchText(topic.relation_to_direction, "未记录")}</dd></div>
                      <div><dt>主要风险</dt><dd>{resourcePackage.risks.map((item) => humanizeResearchText(item)).join("；") || "没有未解决的关键风险"}</dd></div>
                      <div><dt>替代设计</dt><dd>{(topic.alternative_designs || []).map((item) => humanizeResearchText(item)).join("；") || "无"}</dd></div>
                    </dl>
                  </details>
                  <button
                    type="button"
                    disabled={busy || topic.status === "blocked"}
                    onClick={() => onChoose(
                      topic,
                      resourcePackage.missingTypes.length ? undefined : resourcePackage.resourceIds,
                    )}
                  >
                    {busy === topic.topic_id ? <CircleNotch size={15} className="spin" /> : <ArrowRight size={15} />}
                    选择这个研究方案
                  </button>
                </article>
              );
            })}
          </div>
          <details className="stage2-resource-audit">
            <summary>查看系统如何从 {resourceCandidates.length} 个候选收敛为这些方案</summary>
            <p>系统按课题匹配、资源类型、可用性、版本、许可证和只读安全探测进行排序。这里只展示方案实际采用或计划补建的资源；完整候选仍保留在审计工件中。</p>
          </details>
        </>
      ) : null}
      {repairProposal ? (
        <section className="stage2-contract-repair-card">
          <header>
            <div>
              <span>系统已准备推荐方案</span>
              <strong>不用理解内部错误，也不用自己从空白开始填写。</strong>
              <p>
                阶段二在冻结前发现 {repairProposal.recommendations.length} 项科学定义不完整。
                采用推荐值后会生成实验契约 vNext，并自动重新编译；旧版本和审计记录仍然保留。
              </p>
            </div>
            <Wrench size={22} />
          </header>
          <ol>
            {repairProposal.recommendations.map((item) => (
              <li key={item.title}>
                <Check size={16} />
                <div>
                  <strong>{humanizeResearchText(item.title)}</strong>
                  <span>{humanizeResearchText(item.value)}</span>
                  <p>{humanizeResearchText(item.reason)}</p>
                </div>
              </li>
            ))}
          </ol>
          <div className="stage2-contract-repair-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => onRevise(
                repairProposal.overrides,
                "采用阶段二根据主指标与实验设计生成的保守推荐定义。",
              )}
            >
              {busy === "revise-protocol"
                ? <CircleNotch size={15} className="spin" />
                : <Check size={15} />}
              采用推荐值并重新检查
            </button>
            <button
              type="button"
              className="secondary-action"
              disabled={busy}
              onClick={() => setRevisionOpen((current) => !current)}
            >
              {revisionOpen ? "收起自定义修改" : "查看并自行修改"}
            </button>
          </div>
          <details className="stage2-contract-repair-audit">
            <summary>查看系统审计代码</summary>
            <ul>{repairProposal.rawIssues.map((item) => <li key={item}>{item}</li>)}</ul>
          </details>
        </section>
      ) : null}
      {executionReadiness ? (
        <section className={`stage2-readiness-packet is-${executionReadiness.status}`}>
          <div>
            <span>进入实验前的检查结果</span>
            <strong>{executionReadiness.status === "ready" ? "实验设计已编译并通过试运行" : "实验设计尚不可执行"}</strong>
            <p>这里验收的是阶段二科学设计，不是正式实验结果。</p>
          </div>
          <dl>
            <div><dt>可执行算法</dt><dd>{executionReadiness.executable_algorithms}</dd></div>
            <div><dt>运行规格</dt><dd>{executionReadiness.run_specification_count}</dd></div>
            <div><dt>数据语义</dt><dd>{executionReadiness.data_semantics_resolved ? "已解析" : "缺失"}</dd></div>
            <div><dt>指标公式</dt><dd>{executionReadiness.metric_resolved ? "已解析" : "缺失"}</dd></div>
            <div><dt>分析规则</dt><dd>{executionReadiness.analysis_resolved ? "已解析" : "缺失"}</dd></div>
            <div><dt>实验差异</dt><dd>{executionReadiness.scientific_contribution_resolved ? "已冻结" : "缺失"}</dd></div>
            <div><dt>试运行</dt><dd>{executionReadiness.dry_run_passed ? "通过" : "未通过"}</dd></div>
            <div><dt>科学证据</dt><dd>未产生</dd></div>
            <div><dt>阻塞项</dt><dd>{executionReadiness.blocker_count}</dd></div>
          </dl>
        </section>
      ) : null}
      {gate ? (
        <div className={`stage2-gate-summary is-${gate.status.toLowerCase()}`}>
          <strong>
            {gate.status === "PASS"
              ? "协议与基线满足进入正式实验的条件"
              : gate.status === "CONDITIONAL_PASS"
                ? "存在已记录的非阻塞条件"
                : gate.status === "DESIGN_READY"
                  ? "实验设计已就绪，等待负责人批准"
                  : "当前不能进入正式实验"}
          </strong>
          {uniqueStageTwoMessages(gate.blockers).length ? (
            <ul>{uniqueStageTwoMessages(gate.blockers).map((item) => <li key={item}>{item}</li>)}</ul>
          ) : null}
          {uniqueStageTwoMessages(gate.warnings).length ? (
            <p>{uniqueStageTwoMessages(gate.warnings).join("；")}</p>
          ) : null}
          {uniqueStageTwoMessages(gate.required_user_decisions).length ? (
            <p>需要负责人决定：{uniqueStageTwoMessages(gate.required_user_decisions).join("；")}</p>
          ) : null}
          {(gate.allowed_next_actions || []).length ? (
            <small>
              允许的下一步：
              {[...new Set(gate.allowed_next_actions.map((item) => STAGE2_ACTION_LABELS[item] || item))].join("；")}
            </small>
          ) : null}
          {gate.status === "FAIL" && protocol && !repairProposal ? (
            <div className="stage2-owner-boundary">
              <div>
                <strong>如果系统建议的最小可行性边界可以接受</strong>
                <p>系统会运行一次不产生科学结论的基线冒烟验证，确认数据可读、指标可算、日志和产物可保存。正式实验仍只在阶段三执行。</p>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => onRevise(
                  stageTwoMvpAcceptanceOverrides(),
                  "负责人接受系统建议的阶段二最小可行性边界，并授权不产生科学结论的基线冒烟验证。",
                )}
              >
                {busy === "revise-protocol" ? <CircleNotch size={15} className="spin" /> : <Check size={15} />}
                接受当前边界并重新检查
              </button>
            </div>
          ) : null}
          {awaitingContract ? (
            <div className="stage2-gate-actions">
              <button type="button" disabled={busy} onClick={onApprove}>
                {busy === "approve" ? <CircleNotch size={15} className="spin" /> : <Check size={15} />}
                批准并冻结
              </button>
              <button type="button" disabled={busy} onClick={() => setRevisionOpen(true)}>
                提出修改
              </button>
              <button type="button" disabled={busy} onClick={() => setRevisionOpen(false)}>
                暂不决定
              </button>
            </div>
          ) : null}
          {!awaitingContract && failedStage2SystemStep ? (
            <div className="stage2-retry-freeze">
              <div>
                <strong>负责人审批已经完成，不需要返回第 16 步。</strong>
                <p>
                  当前只是第 18 步写入版本化锁文件失败。旧版锁会继续保留；
                  重试只执行这个失败步骤及其尚未完成的下游步骤。
                </p>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => onRetryStep(failedStage2SystemStep)}
              >
                {busy === `retry-${failedStage2SystemStep.step_instance_id}`
                  ? <CircleNotch size={15} className="spin" />
                  : <ArrowClockwise size={15} />}
                只重试第 18 步
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      {(["FAIL", "BUILD_REQUIRED"].includes(gate?.status) || revisionOpen) && protocol ? (
        <StageTwoProtocolEditor
          protocol={protocol}
          busy={busy}
          onRevise={onRevise}
          initialRevisionReason={initialRevisionReason}
        />
      ) : null}
      {error ? <p className="stage2-panel-error">{humanizeUiError(error)}</p> : null}
      <small className="stage2-authority-note">阶段二只允许预检和负责人明确声明的基线；正式实验组只能在第三阶段运行。</small>
    </div>
  );
}

function StageThreeBackfillNotice({ request }) {
  if (request?.status !== "stage3_backfill_required") return null;
  return (
    <aside className="stage3-backfill-notice">
      <ClockCounterClockwise size={21} />
      <div>
        <small>来自阶段四的科学后继请求</small>
        <strong>补齐 {request.required_item_ids?.length || 0} 项关键证据后，再生成论文新版本</strong>
        <p>需要变更：{request.changed_contract_fields?.map((field) => contractFieldLabel[field] || humanizeIdentifier(field, "其他研究设置")).join("、") || "实验与评估证据"}。历史实验和研究结论不会被覆盖。</p>
      </div>
    </aside>
  );
}

function StageThreePanel({ data, backfillRequest, busy, error, onInitialize, onInitializeBuild, onAdvanceBuild, onApprove, onReviseContract, onCompleteBoundary, onRetry }) {
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [revisionReason, setRevisionReason] = useState("");
  if (!data?.initialized) {
    const status = data?.overview?.status || "not_initialized";
    const buildSteps = data?.build_steps || [];
    const handoffIssues = data?.handoff_issues || [];
    const handoffIssueCopy = {
      CONTRACT_PROFILE_MISSING: "尚未选择平台支持的实验类型",
      FORMAL_TASKS_MISSING: "尚未列出正式测评任务",
      BASELINE_ID_MISSING: "尚未声明基线实验",
      BASELINE_ACTION_MISSING: "尚未预留基线执行绑定",
      TREATMENT_ID_MISSING: "尚未声明实验组方案",
      TREATMENT_ACTION_MISSING: "尚未预留实验组执行绑定",
      NUMERIC_EFFECT_THRESHOLD_MISSING: "尚未冻结可计算的最小效果阈值",
      TASK_SEMANTICS_MISSING: "正式任务仍是占位名称，需说明计算操作、预测语义和权威目标",
      BASELINE_BEHAVIOR_MISSING: "需明确基线具体做什么、使用哪些参数与预处理",
      TREATMENT_BEHAVIOR_MISSING: "需明确实验组干预及其与基线唯一允许的差异",
      DATA_BOUNDARY_INCOMPLETE: "数据分母、分析单位、标签来源或时间边界尚未冻结",
      METRIC_PROTOCOL_MISSING: "延迟指标需冻结单位、计时范围、预热、缓存、时钟与重复规则",
      MISSING_DATA_POLICY_UNRESOLVED: "缺失数据规则仍是占位说明，需改成可执行规则",
      BOOTSTRAP_PROTOCOL_INCOMPLETE: "需冻结 Bootstrap 次数、种子、重采样单位、实现与区间算法",
      EFFECT_THRESHOLD_SEMANTICS_MISSING: "效果阈值需说明绝对或相对变化及其单位",
      SECONDARY_OUTCOME_TOLERANCE_MISSING: "成功规则引用了次要指标，但尚未定义指标及容忍阈值",
      RUN_BUDGET_CONFLICT: "正式实验矩阵所需运行次数超过当前预算上限",
    };
    const humanizeHandoffIssue = (issue) => {
      const [code] = String(issue).split(":");
      if (handoffIssueCopy[code]) return handoffIssueCopy[code];
      const normalized = String(issue).toLowerCase();
      const semanticCopy = [
        [["point-in-time prices"], "缺少带时间戳的价格、收益、行业归属、公司行动、停复牌、费用和滑点数据"],
        [["target population", "eligibility rules"], "目标总体、纳入规则、抽样方法、决策时间点和正式数据边界尚未冻结"],
        [["staging rows", "formal cases"], "当前暂存数据只能用于准备，不能冒充正式实验样本"],
        [["frozen baseline", "preprocessing"], "基线流程、预处理、预测语义、停止规则和缓存状态尚未形成可执行定义"],
        [["primary implementation", "operationally defined"], "实验组只有名称，尚未明确具体算法、参数以及与基线的唯一差异"],
        [["resolved resource binding", "pinned local snapshot"], "所选资源尚未绑定到带哈希和许可证记录的本地冻结快照"],
        [["task, split, seed, and replicate"], "任务、数据划分、种子和重复次数尚未绑定到两组的完整运行矩阵"],
        [["benchmark evaluator", "resolved implementation"], "基准评估器尚未解析为可执行且可核验的实现"],
        [["non-null targets"], "正式样本仍缺少权威且非空的目标值，暂时不能冻结统计分母"],
        [["formal admission", "hypothesis verdict"], "在上述条件补齐前，不能准入正式实验，也不能对假设作出结论"],
        [["executable operation", "prediction target"], "尚未冻结任务的可执行操作、预测类型与逐样本目标"],
        [["baseline", "implementation"], "尚未冻结基线的实现绑定、参数与复现实例"],
        [["treatment", "not defined"], "尚未冻结实验组干预及其可执行组件"],
        [["preprocessing", "inclusion"], "尚未冻结预处理、纳入排除规则与时间边界"],
        [["dataset", "snapshot", "hash"], "数据集尚未绑定到不可变文件快照与哈希"],
        [["latency", "measurement protocol"], "延迟指标尚未定义计时范围、单位、预热与重复规则"],
        [["missing-data policy"], "缺失数据处理规则尚未冻结"],
        [["bootstrap", "resamples"], "Bootstrap 重采样次数、随机种子与区间算法尚未冻结"],
        [["effect threshold", "unit"], "最小效果阈值尚未说明单位以及采用绝对差还是相对差"],
        [["secondary-outcome"], "保护性次要指标及容忍阈值尚未冻结"],
        [["maximum of 10 runs"], "实验矩阵需要 50 次运行，但当前预算上限只有 10 次"],
        [["arm behaviors", "unspecified"], "基线与实验组行为尚未明确，当前不能生成可判定的正式执行包"],
      ];
      return semanticCopy.find(([markers]) => markers.every((marker) => normalized.includes(marker)))?.[1]
        || humanizeUiError(issue, "阶段三准入仍有未说明的科学条件。");
    };
    const contractRevisionRequired = status === "contract_revision_required"
      || status === "unsupported_profile"
      || buildSteps.some(
        (step) => step?.blocker?.kind === "contract_revision_required",
      );
    const nextAction = {
      not_initialized: "接收阶段二交接",
      contract_revision_required: "返回阶段二补齐契约",
      handoff_validated: "创建实验资产建设计划",
      build_plan_created: data?.build_plan?.build_mode === "build_from_blueprint" ? "构建正式实验资产" : "验证并冻结现成实验",
      assets_materialized: "执行隔离冒烟测试",
      smoke_tested: "验证符合性并冻结执行包",
      execution_package_frozen: "执行正式准入",
      formal_execution_admitted: "生成并审批实验执行计划",
      resource_blocked: "重新检查冻结资源",
      license_blocked: "重新检查许可证",
      build_blocked: "仅重试实验资产构建",
      unsupported_profile: "查看不支持的实验设计",
    }[status] || "继续阶段三";
    const statusLabel = {
      not_initialized: "尚未接收",
      contract_revision_required: "阶段二契约需要补齐",
      handoff_validated: "交接已验证",
      build_plan_created: "建设计划已就绪",
      assets_materialized: "实验资产已物化",
      smoke_tested: "冒烟测试已通过",
      execution_package_frozen: "执行包已冻结",
      formal_execution_admitted: "正式执行已准入",
      resource_blocked: "实验资源阻塞",
      license_blocked: "许可证阻塞",
      build_blocked: "实验构建阻塞",
      unsupported_profile: "当前实验类型暂不支持",
    }[status] || humanizeIdentifier(status, "阶段三正在准备");
    return (
      <div className="stage3-panel">
        <StageThreeBackfillNotice request={backfillRequest} />
        <div className="stage3-heading">
          <div>
            <span>阶段三 · 实验资产、执行与判定</span>
            <strong>{statusLabel}</strong>
            <p>先把阶段二科学蓝图建设为正式实验资产，再冻结具体数据、代码、环境、评估器与执行清单。阶段二的最小可行性验证和冒烟测试都不能作为论文证据。</p>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (contractRevisionRequired) {
                setRevisionReason(
                  handoffIssues.map(humanizeHandoffIssue).join("；")
                  || "阶段三准入发现实验契约不完整。",
                );
                setRevisionOpen(true);
                return;
              }
              if (data?.build_plan) onAdvanceBuild();
              else onInitializeBuild();
            }}
          >
            {busy ? <CircleNotch size={15} className="spin" /> : <Play size={15} />}
            {nextAction}
          </button>
        </div>
        {contractRevisionRequired ? (
          <section className="stage3-contract-repair">
            <div>
              <span>为什么不能开始正式实验</span>
              <strong>阶段三没有收到一份可执行、可判定的完整契约。</strong>
              <p>这不是实验失败，也不是研究结论“不支持”。需要先创建新的实验契约版本，补齐下列设计字段。</p>
            </div>
            <ul>
              {(handoffIssues.length ? handoffIssues : ["当前实验类型尚未得到平台支持"]).map((issue) => (
                <li key={issue}>{humanizeHandoffIssue(issue)}</li>
              ))}
            </ul>
            <div className="stage3-contract-repair-actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setRevisionReason(
                    handoffIssues.map(humanizeHandoffIssue).join("；")
                    || "请按当前研究问题自动补齐正式实验所需的契约字段。",
                  );
                  setRevisionOpen(true);
                }}
              >
                <Wrench size={15} />
                自动补齐契约并继续正式实验
              </button>
              <button
                type="button"
                className="secondary-action"
                disabled={busy}
                onClick={() => onCompleteBoundary(handoffIssues)}
              >
                {busy === "complete-boundary" ? <CircleNotch size={15} className="spin" /> : <ArrowRight size={15} />}
                只生成证据边界报告
              </button>
              <button type="button" className="secondary-action" onClick={() => setRevisionOpen(false)}>暂不处理</button>
            </div>
            <p className="stage3-authority">
              这不会声称实验成功。系统会把当前结论记为“暂不可验证”，保留证据缺口与后续补证据计划，并进入阶段四。
            </p>
      {error ? <p className="stage3-error" role="alert">{humanizeUiError(error)}</p> : null}
            {revisionOpen ? (
              <div className="gate-revision-editor">
                <label>
                  告诉系统你希望怎样修改
                  <textarea
                    value={revisionReason}
                    onChange={(event) => setRevisionReason(event.target.value)}
                    placeholder="例如：采用同任务、同种子的配对计算实验；主指标提升至少 0.03；基线为传统质量筛选器。"
                  />
                </label>
                <p>提交后会返回阶段二并创建新的实验契约版本。旧的冻结契约仍保留，不会被覆盖。</p>
                <button
                  type="button"
                  disabled={busy || !revisionReason.trim()}
                  onClick={() => onReviseContract(revisionReason)}
                >
                  <ArrowLeft size={15} />
                  生成新版本并打开字段编辑器
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
        {data?.build_plan ? (
          <div className="stage3-gate">
            <div>
              <LockKey size={19} />
              <span>
                <strong>{experimentProfileLabel[data.build_plan.profile] || humanizeIdentifier(data.build_plan.profile, "受控计算实验")}</strong>
                <small>{data.build_plan.build_mode === "build_from_blueprint" ? "根据实验蓝图构建" : "复用并验证现成代码与数据"} · {data.build_plan.items?.length || 0} 项资产</small>
              </span>
            </div>
          </div>
        ) : null}
        {buildSteps.length ? (
          <ol className="stage3-build-flow">
            {buildSteps.map((step) => (
              <li key={step.step_instance_id} className={`is-${step.status}`}>
                <i><StepStatusGlyph status={step.status} /></i>
                <span>
                  <strong>{stageTwoStepTitle[step.step_type] || humanizeIdentifier(step.step_type, "系统处理步骤")}</strong>
                  <small>
                    {executorLabel[step.executor_type] || humanizeIdentifier(step.executor_type, "系统服务")} · 尝试 {step.attempt}
                    {step.status === "running" ? ` · 本次已运行 ${formatElapsed(step.updated_at)}` : ""}
                    {step.status === "running"
                      && elapsedSeconds(step.updated_at) >= (data?.build_limits?.model_generation_soft_notice_seconds || 300)
                      ? ` · 正在生成代码、数据与评估器，最长等待 ${Math.ceil((data?.build_limits?.model_generation_timeout_seconds || 1800) / 60)} 分钟`
                      : ""}
                  </small>
                  {step.status === "blocked" ? <small>{buildBlockerText(step)}</small> : null}
                </span>
                <em>{stepStatusLabel[step.status] || humanizeIdentifier(step.status, "处理中")}</em>
              </li>
            ))}
          </ol>
        ) : null}
      {error && !contractRevisionRequired ? <p className="stage3-error" role="alert">{humanizeUiError(error)}</p> : null}
        <small className="stage3-authority">资产构建失败、正式实验失败和研究假设未获支持是三种不同状态。系统不会为了得到支持结果而擅自修改协议并重跑。</small>
      </div>
    );
  }
  const overview = data.overview || {};
  const stage3BoundaryMode = data.completion?.evidence_level === "L0_boundary_only"
    || data.claim_envelope?.maximum_claim_tier === "evidence_boundary_report";
  const gateWaiting = data.gate?.status === "awaiting_user";
  const matrix = data.run_matrix || [];
  const groups = matrix.reduce((result, item) => {
    const key = JSON.stringify([item.task_id, item.split_id]);
    if (!result[key]) result[key] = [];
    result[key].push(item);
    return result;
  }, {});
  return (
    <div className="stage3-panel">
      <StageThreeBackfillNotice request={backfillRequest} />
      <div className="stage3-heading">
        <div>
          <span>阶段三研究执行内核</span>
          <strong>
            {stage3BoundaryMode
              ? "正式实验未执行；证据边界已记录"
              : `${overview.completed || 0}/${overview.total || 0} 个运行单元已完成`}
          </strong>
          <p>
            {stage3BoundaryMode
              ? "科学结论暂不可验证，未产生任何实验效果估计"
              : `${experimentProfileLabel[data.profile] || humanizeIdentifier(data.profile, "受控计算实验")} · 已尝试 ${overview.attempts || 0} 次`}
          </p>
        </div>
        <em className={`stage3-state is-${overview.status}`}>{stage3BoundaryMode ? "已诚实收束" : overview.status === "completed" ? "已完成" : overview.status === "execution_blocked" ? "正式执行阻塞" : gateWaiting ? "等待批准" : "执行中"}</em>
      </div>
      {gateWaiting ? (
        <>
          <div className="stage3-gate">
            <div><LockKey size={19} /><span><strong>正式实验需要负责人批准</strong><small>你批准的是已冻结的实验执行计划，系统无权自行改变实验设计。</small></span></div>
            <div className="gate-decision-actions">
              <button type="button" disabled={busy} onClick={onApprove}>
                {busy === "approve" ? <CircleNotch size={15} className="spin" /> : <Check size={15} />}
                批准并运行
              </button>
              <button type="button" disabled={busy} onClick={() => setRevisionOpen(true)}>提出修改</button>
              <button type="button" disabled={busy} onClick={() => setRevisionOpen(false)}>暂不决定</button>
            </div>
          </div>
          {revisionOpen ? (
            <div className="gate-revision-editor">
              <label>
                先说明为什么要修改实验契约
                <textarea
                  value={revisionReason}
                  onChange={(event) => setRevisionReason(event.target.value)}
                  placeholder="例如：当前 0.40 阈值依据不足；我希望在阶段二编辑器中改为 0.30，并增加随机种子。"
                />
              </label>
              <p>提交后会创建新的实验契约版本并自动打开完整字段编辑器。你可以直接改指标、分母、阈值、种子和统计方法；当前冻结版本和实验执行计划会保留。</p>
              <button type="button" disabled={busy || !revisionReason.trim()} onClick={() => onReviseContract(revisionReason)}>
                {busy === "revise-protocol" ? <CircleNotch size={15} className="spin" /> : <ArrowLeft size={15} />}
                生成新版本并编辑具体字段
              </button>
            </div>
          ) : null}
        </>
      ) : null}
      <div className="stage3-matrix">
        {Object.entries(groups).map(([name, cells], groupIndex) => (
          <details key={name} open={cells.some((item) => ["blocked", "failed", "running"].includes(item.execution_status))}>
            <summary>
              <strong>实验任务 {groupIndex + 1}</strong>
              <small>{cells.filter((item) => item.execution_status === "succeeded").length}/{cells.length} 完成</small>
            </summary>
            <div>
              {cells.map((item) => (
                <article key={item.run_cell_id} className={`is-${item.execution_status}`}>
                  <i><StepStatusGlyph status={item.execution_status} /></i>
                  <span>
                    <strong>{item.arm_id === "baseline" ? "基线" : "实验组"} · 随机种子 {item.seed} · 第 {item.replicate} 次重复</strong>
                    <small>已尝试 {item.attempt_count} 次</small>
                    {item.blocker ? <p>{humanizeUiError(item.blocker.message, "该实验单元暂时无法执行。")}</p> : null}
                  </span>
                  <em>{stepStatusLabel[item.execution_status] || humanizeIdentifier(item.execution_status, "处理中")}</em>
                  {["blocked", "failed"].includes(item.execution_status) ? (
                    <button type="button" disabled={busy} onClick={() => onRetry(item)}>重试</button>
                  ) : null}
                </article>
              ))}
            </div>
          </details>
        ))}
      </div>
      {data.evaluation ? (
        <div className="stage3-evaluation">
          <article><small>资格状态</small><strong>{humanizeIdentifier(data.evaluation.qualification_status, "等待确定性检查")}</strong></article>
          <article><small>配对数量</small><strong>{data.evaluation.pair_count}</strong></article>
          <article><small>基线</small><strong>{data.evaluation.baseline_estimate?.toFixed?.(4) ?? "—"}</strong></article>
          <article><small>实验组</small><strong>{data.evaluation.treatment_estimate?.toFixed?.(4) ?? "—"}</strong></article>
          <article><small>配对效应</small><strong>{data.evaluation.paired_effect?.toFixed?.(4) ?? "—"}</strong></article>
          <article><small>确定性判定</small><strong>{verdictCopy[data.evaluation.decision]?.label || humanizeIdentifier(data.evaluation.decision, "暂未形成结论")}</strong></article>
          <article><small>独立统计单元</small><strong>{data.profile_analysis?.independent_unit_count ?? "—"}</strong></article>
          <article><small>推断单位</small><strong>{humanizeIdentifier(data.profile_analysis?.variance_unit, "尚未确定")}</strong></article>
          <article><small>置信区间</small><strong>{data.profile_analysis?.confidence_interval?.map?.((value) => value.toFixed(4)).join(" – ") || "—"}</strong></article>
        </div>
      ) : null}
      {data.verdict ? (
        <div className="stage3-verdict">
          <ShieldCheck size={20} />
          <span><strong>研究结论：{verdictCopy[data.verdict.status]?.label || humanizeIdentifier(data.verdict.status, "暂未形成结论")}</strong><small>证据图 {data.evidence?.edge_count || 0} 条关系 · {data.evidence?.verified ? "完整证据链已成立" : "尚无完整证据链"}</small></span>
        </div>
      ) : null}
      {(data.diagnostics?.length || data.repair_lineage?.length || data.successors?.length) ? (
        <div className="stage3-repair-lineage">
          <header>
            <span>
              <Wrench size={18} />
              <strong>诊断、修复与后继运行</strong>
            </span>
            <small>历史计划和判定不会被覆盖</small>
          </header>
          <div>
            {(data.diagnostics || []).map((item) => (
              <article key={item.diagnostic_id}>
                <small>系统诊断 · {humanizeIdentifier(item.failure_class, "待分类问题")}</small>
                <strong>{stageTwoStepTitle[item.earliest_preventable_step_type] || stageFourStepTitle[item.earliest_preventable_step_type] || humanizeIdentifier(item.earliest_preventable_step_type, "待定位步骤")}</strong>
                <p>{item.system_findings?.map((finding) => humanizeUiError(finding, "已记录一项确定性失效范围。")).join("；") || "已记录确定性失效范围"}</p>
                {item.ai_root_cause_hypotheses?.length ? (
                  <em>AI 建议：{item.ai_root_cause_hypotheses.map((finding) => humanizeUiError(finding, "建议进一步核查该环节。")).join("；")}</em>
                ) : null}
              </article>
            ))}
            {(data.repair_lineage || []).map((item) => (
              <article key={item.repair_id}>
                <small>修复方案 · 第 {item.version} 版</small>
                <strong>{humanizeIdentifier(item.status, "等待处理")}</strong>
                <p>失效 {item.invalidated_artifact_ids?.length || 0} 个工件 · 可复用 {item.reusable_artifact_ids?.length || 0} 个工件</p>
                {item.successor_run_id ? <em>已创建后继实验计划</em> : null}
              </article>
            ))}
            {(data.successors || []).map((item) => (
              <article key={item.successor_id}>
                <small>修复后的后继运行</small>
                <strong>原运行 → 新运行</strong>
                <p>实际复用 {item.reused_artifact_ids?.length || 0} 个工件 · 明确失效 {item.invalidated_artifact_ids?.length || 0} 个工件</p>
              </article>
            ))}
          </div>
        </div>
      ) : null}
      {error ? <p className="stage3-error">{humanizeUiError(error)}</p> : null}
      <small className="stage3-authority">AI 审核和 NLI 只能提示风险，不能覆盖冻结规则、资格检查或确定性研究结论。</small>
    </div>
  );
}

function Hero({ mode }) {
  return (
    <section className="hero-card">
      <div className="hero-copy">
        <span className="eyebrow">{mode === "project" ? "已有项目 · 默认" : "只有想法 · 从验证开始"}</span>
        <h1>{mode === "project" ? "先判断什么值得写，\n再生成论文。" : "先把想法变成问题，\n再让证据回答。"}</h1>
        <p>{mode === "project"
          ? "选择项目文件夹，系统会只读提取主张、形成研究方案并带你逐阶段确认。"
          : "输入一个想法，系统会把它收敛成可验证的问题并形成研究方案。"}</p>
      </div>
      <div className="hero-principle">
        <ShieldCheck size={24} weight="fill" />
        <span><strong>证据先于写作</strong><small>关键决定都可以查看、修改或稍后处理</small></span>
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
          <div className="privacy-note"><LockKey size={16} /><span>只读分析 · 联网检索默认关闭 · 密钥和私密文件永不外发</span></div>
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
          <p className="eyebrow">{taskStatusCopy[status] || humanizeIdentifier(status, "状态已记录")}</p>
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
                : taskEventCopy[item.event] || [humanizeIdentifier(item.event, "任务更新"), taskStatusCopy[item.status] || humanizeIdentifier(item.status, "状态已记录")];
              return <li key={`${item.recorded_at}-${item.event}-${index}`} className={index === 0 ? "is-latest" : ""}><i /><div><strong>{copy[0]}</strong><p>{copy[1]}</p></div><time>{formatTime(item.recorded_at)}</time></li>;
            }) : <li className="is-latest"><i /><div><strong>正在读取任务状态</strong><p>执行记录将在研究引擎开始工作后显示。</p></div><time>刚刚</time></li>}
          </ol>
          <details className="task-meta">
            <summary>技术详情</summary>
            <span>任务 {task?.task_id}</span><span>尝试 {task?.attempt || 0} 次</span><span>更新于 {formatTime(task?.updated_at)}</span>
          </details>
        </article>
      </div>
    </section>
  );
}

function DirectionSelect({ inspection, selectedId, onSelect, onBack, onContinue }) {
  const directions = useMemo(
    () => uniqueDirections(inspection?.candidates || [], inspection?.discovery_portfolio),
    [inspection],
  );
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
          const directionId = direction.topic_id || direction.direction_id || direction.track_id;
          const selected = directionId === selectedId;
          return (
            <button key={directionId} type="button" className={`direction-card ${selected ? "is-selected" : ""}`} onClick={() => onSelect(directionId)}>
              <span className="direction-index">{String(index + 1).padStart(2, "0")}</span>
              <div className="direction-copy">
                <div><h2>{humanizeResearchText(paperTopicTitle(direction))}</h2>{index === 0 ? <em>推荐</em> : null}</div>
                {direction.internal_label && direction.internal_label !== direction.title ? (
                  <small className="internal-label">项目内称：{direction.internal_label}</small>
                ) : null}
                <p><span className="topic-label">要回答的问题</span>{humanizeResearchText(candidateQuestion(direction))}</p>
                <div className="direction-quick-facts">
                  <span><b>为什么值得做</b>{humanizeResearchText(direction.contribution, "把项目主张转化为可验证结论")}</span>
                  <span><b>现有基础</b>{direction.evidence_chain_count || 0} 条已验证证据链</span>
                </div>
                <details className="direction-evidence-details">
                  <summary>查看研究依据与边界</summary>
                  <dl>
                    <div><dt>研究范围</dt><dd>{humanizeResearchText(direction.scope)}</dd></div>
                    {direction.operational_definition ? <div><dt>如何测量</dt><dd>{humanizeResearchText(direction.operational_definition)}</dd></div> : null}
                    {direction.academic_concepts?.length ? <div><dt>相关学术概念</dt><dd>{direction.academic_concepts.map((item) => humanizeResearchText(item)).join("、")}</dd></div> : null}
                    {direction.relation_to_prior_work ? <div><dt>与已有工作的关系</dt><dd>{humanizeResearchText(direction.relation_to_prior_work)}</dd></div> : null}
                  </dl>
                  {direction.novelty_grounding ? (
                    <div className="direction-dimensions" aria-label="候选方向质量维度">
                      <span>新颖性依据 <b>{humanizeResearchText(direction.novelty_grounding)}</b></span>
                      <span>证据成熟度 <b>{humanizeResearchText(direction.evidence_readiness)}</b></span>
                      <span>实施可行性 <b>{humanizeResearchText(direction.feasibility)}</b></span>
                    </div>
                  ) : null}
                </details>
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
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [revisionReason, setRevisionReason] = useState("");
  const [revisionPreview, setRevisionPreview] = useState(false);
  const [scopeDraft, setScopeDraft] = useState({
    direction: humanizeResearchText(direction?.title || ""),
    research_question: humanizeResearchText(candidateQuestion(direction)),
    candidate_contribution: humanizeResearchText(direction?.contribution || ""),
    scope_in: humanizeResearchText(direction?.scope || ""),
    scope_out: "",
  });
  useEffect(() => {
    setScopeDraft({
      direction: humanizeResearchText(direction?.title || ""),
      research_question: humanizeResearchText(candidateQuestion(direction)),
      candidate_contribution: humanizeResearchText(direction?.contribution || ""),
      scope_in: humanizeResearchText(direction?.scope || ""),
      scope_out: "",
    });
  }, [direction?.direction_id, direction?.topic_id]);
  const rules = [
    ["研究问题", humanizeResearchText(candidateQuestion(direction))],
    ["候选贡献", humanizeResearchText(direction?.contribution, "待确认")],
    ["研究范围", humanizeResearchText(direction?.scope, "待确认")],
    ["项目材料来源", source],
    [
      "正式实验数据边界",
      "尚未冻结。阶段二将根据课题，从本地资源、获准的公开在线数据或可审计构造数据中选择，并明确样本、时间范围、分母及纳入排除规则。",
    ],
    ["证据类型", maturityCopy[direction?.evidence_maturity] || "待确认"],
    ["协议与输出", direction?.protocol_bound_to_output ? "精确绑定，可进入判定" : "未绑定，只能形成证据缺口报告"],
    ["禁止升级", derived ? "缺少冻结协议时保持“暂不可验证”" : "论文写作不能改变实验判定"],
  ];
  return (
    <section className="workflow-state contract-state">
      <div className="state-hero compact-hero">
        <button type="button" className="back-button" onClick={onBack}><ArrowLeft size={18} />返回候选选题</button>
        <div><p className="eyebrow">研究范围 · 第 1 版</p><h1>确认这次要研究什么</h1><p>接受建议即可继续；如果边界不合适，也可以先用一句话告诉系统怎么改。</p></div>
      </div>
      {derived ? <div className="protocol-alert"><WarningCircle size={21} weight="fill" /><div><strong>当前材料缺少完整协议链</strong><p>本轮可以收敛问题并生成证据缺口工作稿，但想法判定必须保持“暂不可验证”。</p></div></div> : null}
      <div className="contract-layout">
        <article className="surface-card contract-card">
          <div className="card-heading"><div><span>确认阶段一研究范围</span><h2>{humanizeResearchText(paperTopicTitle(direction))}</h2></div><LockKey size={23} /></div>
          <dl className="contract-rules">
            {rules.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
          </dl>
        </article>
        <aside className="surface-card contract-aside compact">
          <ShieldCheck size={24} weight="fill" />
          <h3>这一步只确认研究边界</h3>
          <p>假设、指标、数据和实验方法会在阶段二形成后再请你确认。</p>
          <details><summary>查看版本与冻结规则</summary><p>确认后保存只读快照；以后修改会生成新版本，不覆盖历史记录。</p></details>
        </aside>
      </div>
      <div className="sticky-action-bar">
        <div><strong>需要负责人批准阶段交付</strong><span>批准范围不代表假设已经成立</span></div>
        <div className="gate-decision-actions">
          <button type="button" className="primary-button" onClick={() => onConfirm(null, "负责人接受系统建议的研究边界。")} disabled={loading}>
            {loading ? <CircleNotch className="spinner" size={18} /> : <ArrowRight size={18} />}
            {loading ? "正在保存研究边界" : "接受建议并继续"}
          </button>
          <button type="button" onClick={() => { setRevisionOpen(true); setRevisionPreview(false); }} disabled={loading}>我想修改</button>
          <button type="button" onClick={onBack} disabled={loading}>暂不决定</button>
        </div>
      </div>
      {revisionOpen ? (
        <section className="scope-revision-editor surface-card">
          <div>
            <small>修改建议</small>
            <h3>用一句话说清你想改什么</h3>
            <p>例如：“只研究中文问答，把主问题改成模型是否能引用项目证据准确回答。”系统不会修改原始项目文件。</p>
          </div>
          <label className="revision-request-field">
            我希望这样修改
            <textarea value={revisionReason} onChange={(event) => { setRevisionReason(event.target.value); setRevisionPreview(false); }} placeholder="例如：只研究中文问答；不包含模型预训练；把准确率作为主要结果。" />
            <small>这段话会作为修改理由保存，方便之后追溯为什么改变了边界。</small>
          </label>
          <details>
            <summary>高级：逐项修改研究边界</summary>
            <div className="scope-advanced-fields">
              <label>研究主题<small>这项研究大致研究什么</small><input value={scopeDraft.direction} onChange={(event) => { setScopeDraft({ ...scopeDraft, direction: event.target.value }); setRevisionPreview(false); }} /></label>
              <label>最终要回答的问题<small>尽量写成可以被实验支持或否定的问题</small><textarea value={scopeDraft.research_question} onChange={(event) => { setScopeDraft({ ...scopeDraft, research_question: event.target.value }); setRevisionPreview(false); }} /></label>
              <label>预期贡献<small>如果结果成立，会新增什么认识</small><textarea value={scopeDraft.candidate_contribution} onChange={(event) => { setScopeDraft({ ...scopeDraft, candidate_contribution: event.target.value }); setRevisionPreview(false); }} /></label>
              <label>这次包含什么<small>每行一项研究对象或条件</small><textarea value={scopeDraft.scope_in} onChange={(event) => { setScopeDraft({ ...scopeDraft, scope_in: event.target.value }); setRevisionPreview(false); }} /></label>
              <label>这次明确不研究什么<small>每行一项，防止课题越写越大</small><textarea value={scopeDraft.scope_out} onChange={(event) => { setScopeDraft({ ...scopeDraft, scope_out: event.target.value }); setRevisionPreview(false); }} /></label>
            </div>
          </details>
          {!revisionPreview ? (
            <button type="button" className="primary-button" disabled={!revisionReason.trim()} onClick={() => setRevisionPreview(true)}>
              预览修改后的研究边界
            </button>
          ) : (
            <div className="revision-preview">
              <span>提交前预览</span>
              <strong>{scopeDraft.research_question}</strong>
              <p>{revisionReason}</p>
              <div>
                <button type="button" className="primary-button" disabled={loading} onClick={() => onConfirm(scopeDraft, revisionReason)}>
                  {loading ? <CircleNotch className="spinner" size={18} /> : <Check size={18} />}
                  确认修改并继续
                </button>
                <button type="button" onClick={() => setRevisionPreview(false)}>返回修改</button>
              </div>
            </div>
          )}
        </section>
      ) : null}
    </section>
  );
}

function AttentionState({ task, onResolve, onResume, onBackToContract }) {
  const inferredRequirement = inferRequirement(task);
  const transitioning = !inferredRequirement;
  const requirement = inferredRequirement || {
    requirement_id: "transitioning",
    kind: "configuration",
    accepted_inputs: [],
    alternatives: [],
  };
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
  if (transitioning) {
    return (
      <section className="workflow-state running-state" aria-live="polite">
        <div className="state-hero running-hero">
          <span className="state-icon"><CircleNotch className="spinner" size={28} /></span>
          <div><p className="eyebrow">任务已经完成</p><h1>正在打开阶段一结果。</h1><p>联网检索与候选方向已经保存，不需要补充条件或重新运行。</p></div>
        </div>
      </section>
    );
  }
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
            {requirement.requirement_id === "inferred" && !task?.resumable && !requirement.alternatives?.length ? (
              <button className="primary-button" type="button" onClick={onBackToContract}>返回负责该问题的阶段</button>
            ) : null}
          </div>
        </article>
        <aside className="surface-card completed-work-card">
          <span>已经保留</span>
          <h3>任务状态与运行记录</h3>
          <ul><li><CheckCircle size={17} weight="fill" />任务编号和输入参数</li><li><CheckCircle size={17} weight="fill" />失败类型与原始原因</li><li><CheckCircle size={17} weight="fill" />已完成的阶段产物</li></ul>
          <button type="button" className="text-button" onClick={onBackToContract}>返回负责该问题的阶段<ArrowRight size={16} /></button>
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
        <div><p className="eyebrow">研究边界已创建</p><h1>{result?.title || "研究任务已进入发现阶段"}</h1><p>{result?.message} 下一步是补充文献并确认研究范围。</p></div>
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

function formatElapsed(value) {
  if (!value) return "不到 1 分钟";
  const seconds = elapsedSeconds(value);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${seconds % 60} 秒`;
}

function elapsedSeconds(value) {
  if (!value) return 0;
  return Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
}

function buildBlockerText(step) {
  const kind = step?.blocker?.kind;
  if (kind === "model_timeout") {
    return "模型生成超过本轮时限；已保留契约和建设计划，可只重试本步骤。";
  }
  const reasons = step?.blocker?.reasons;
  const firstReason = Array.isArray(reasons) && reasons.length ? reasons[0] : "";
  if (kind === "build_blocked" && firstReason.includes("no structured rows")) {
    return "生成的数据分区为空；系统已保留契约，重试时会强制要求非空 JSONL 数据。";
  }
  if (kind === "build_blocked" && firstReason.includes("deterministic schema validation")) {
    return "生成资产未通过确定性结构校验；可只重试本构建步骤。";
  }
  return firstReason || "打开步骤详情查看阻塞原因。";
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
  const [retrievalReadiness, setRetrievalReadiness] = useState(null);
  const [activePhase, setActivePhase] = useState("discovery");
  const [runtime, setRuntime] = useState({ loading: true, ready: false });
  const [externalSetup, setExternalSetup] = useState({
    loading: true,
    choice: "unconfigured",
    components: [],
    dependencies_installed: false,
  });
  const [deploymentOpen, setDeploymentOpen] = useState(() => {
    const forced = new URLSearchParams(window.location.search).get("setup") === "1";
    return forced || window.localStorage.getItem("research-forge.deployment.v2") !== "complete";
  });
  const [deploymentBusy, setDeploymentBusy] = useState(false);
  const [deploymentError, setDeploymentError] = useState("");
  const pollRef = useRef(null);
  const directions = useMemo(
    () => uniqueDirections(state.inspection?.candidates || [], state.inspection?.discovery_portfolio),
    [state.inspection],
  );
  const selectedDirection = directions.find(
    (item) => (item.topic_id || item.direction_id || item.track_id) === state.selectedDirectionId,
  ) || directions[0] || null;
  const requestPending = state.request.status === "pending";
  const activeWorkflow = preferredWorkflowSnapshot({
    run: state.run,
    ideaResult: state.ideaResult,
    inspection: state.inspection,
    activeTask: state.activeTask,
  });

  const refreshRuntime = async () => {
    setDeploymentBusy(true);
    setDeploymentError("");
    try {
      const payload = await api("/api/runtime/status");
      setRuntime({ ...payload, loading: false });
      if (!payload.ready) setDeploymentOpen(true);
      return payload;
    } catch (error) {
      setRuntime({ loading: false, ready: false });
      setDeploymentError(error.message);
      setDeploymentOpen(true);
      return null;
    } finally {
      setDeploymentBusy(false);
    }
  };

  const configureRuntimeConnection = async (payload) => {
    setDeploymentBusy(true);
    setDeploymentError("");
    try {
      const next = await api("/api/runtime/configure", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setRuntime({ ...next, loading: false });
      return next;
    } catch (error) {
      setDeploymentError(error.message);
      return null;
    } finally {
      setDeploymentBusy(false);
    }
  };

  const refreshExternalSetup = async () => {
    try {
      const payload = await api("/api/runtime/external-research");
      setExternalSetup({ ...payload, loading: false });
      return payload;
    } catch (error) {
      setExternalSetup({
        loading: false,
        choice: "unconfigured",
        components: [],
        dependencies_installed: false,
      });
      setDeploymentError(error.message);
      return null;
    }
  };

  const configureExternalResearch = async (payload) => {
    setDeploymentBusy(true);
    setDeploymentError("");
    try {
      const next = await api("/api/runtime/external-research", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setExternalSetup({ ...next, loading: false });
      const readiness = await api("/retrieval/readiness?refresh=true");
      setRetrievalReadiness(readiness);
      return next;
    } catch (error) {
      setDeploymentError(error.message);
      return null;
    } finally {
      setDeploymentBusy(false);
    }
  };

  const completeDeployment = (nextRuntime) => {
    if (!nextRuntime?.ready) return;
    window.localStorage.setItem("research-forge.deployment.v2", "complete");
    setRuntime({ ...nextRuntime, loading: false });
    setDeploymentOpen(false);
  };

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
        } else if (taskId) {
          window.history.replaceState({}, "", window.location.pathname);
          dispatch({
            type: "PATCH",
            patch: { notice: "没有找到这个历史任务，已返回新研究入口。你仍可从右上角“研究任务”查看其他已保存任务。" },
          });
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
    refreshRuntime();
    refreshExternalSetup();
  }, []);

  useEffect(() => {
    if (state.activeTask?.stage) setActivePhase(phaseForTaskStage(state.activeTask.stage));
  }, [state.activeTask?.stage]);

  useEffect(() => {
    let cancelled = false;
    api("/retrieval/readiness")
      .then((report) => {
        if (!cancelled) setRetrievalReadiness(report);
      })
      .catch(() => {
        if (!cancelled) setRetrievalReadiness(null);
      });
    return () => {
      cancelled = true;
    };
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

  useEffect(() => {
    const task = state.activeTask;
    if (!task || ["pending", "running", "pause_requested", "paused"].includes(task.status)) return;
    const nextView = taskUiState(task);
    if (!nextView || nextView === state.view) return;
    handleTaskTransition(task).catch((error) => dispatch({ type: "PATCH", patch: { error: error.message } }));
  }, [state.activeTask?.task_id, state.activeTask?.status, state.activeTask?.updated_at]);

  async function handleTaskTransition(task) {
    const nextView = taskUiState(task);
    if (nextView === "directions") {
      let inspection = task.result || {};
      if (inspection.study_id) {
        try {
          const latestWorkflow = await api(`/api/study?id=${encodeURIComponent(inspection.study_id)}`);
          inspection = { ...inspection, workflow: latestWorkflow };
        } catch {
          // The immutable task result remains usable if the live Study snapshot
          // is temporarily unavailable.
        }
      }
      const livePhase = inspection.workflow?.study?.phase || "discovery";
      if (livePhase !== "discovery" || inspection.workflow?.study?.active_scope_version) {
        dispatch({
          type: "PATCH",
          patch: {
            view: "study",
            inspection,
            notice: livePhase === "protocol"
              ? "阶段一研究范围已冻结。阶段二正在收敛具体议题、资源与实验协议。"
              : "已打开当前研究任务的最新保存状态。",
          },
        });
        setActivePhase(livePhase);
        return;
      }
      const nextDirections = uniqueDirections(inspection.candidates || [], inspection.discovery_portfolio);
      const recommendedId = inspection.discovery_portfolio?.recommended_direction_id;
      const recommendedTopic = nextDirections.find(
        (topic) => (topic.topic_id || topic.direction_id) === recommendedId,
      ) || nextDirections.find(
        (topic) => topic.supporting_track_ids?.includes(inspection.recommended_track_id),
      ) || nextDirections[0];
      dispatch({ type: "PATCH", patch: { view: "directions", inspection, selectedDirectionId: recommendedTopic?.topic_id || recommendedTopic?.direction_id || recommendedTopic?.track_id || "", notice: "项目扫描完成。请先确认一个可以独立成文的候选选题。" } });
      setActivePhase("discovery");
    } else if (nextView === "idea_ready") {
      dispatch({ type: "PATCH", patch: { view: "idea_ready", ideaResult: task.result || {}, notice: "研究边界已创建，并进入发现阶段。" } });
      setActivePhase("discovery");
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
          setActivePhase("experiment");
        } else {
          window.history.replaceState({}, "", `?run=${encodeURIComponent(name)}`);
          dispatch({ type: "PATCH", patch: { view: "verdict", run, remediationPlan: null, notice: "研究判定和论文资格已经分别生成。" } });
          setActivePhase("paper");
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

  async function confirmContract(scopeOverrides = null, revisionReason = "") {
    dispatch({ type: "REQUEST", name: "close", status: "pending" });
    try {
      const directionId = selectedDirection?.direction_id || selectedDirection?.topic_id;
      if (state.inspection?.study_id && directionId?.startsWith("discovery-direction-")) {
        const approval = await api("/api/studies/discovery/select", {
          method: "POST",
          body: JSON.stringify({
            study_id: state.inspection.study_id,
            direction_id: directionId,
            decided_by: "project_owner",
            reason: revisionReason || "负责人在 Discovery Portfolio 中选择该方向并批准 Scope v1。",
            scope_overrides: scopeOverrides,
          }),
        });
        dispatch({
          type: "PATCH",
          patch: {
            view: "study",
            inspection: {
              ...state.inspection,
              workflow: approval.workflow,
              scope_contract: approval.scope_contract,
            },
          },
        });
        setActivePhase("protocol");
        dispatch({
          type: "PATCH",
          patch: {
            notice: "阶段一研究范围已冻结并交付到阶段二。正在打开协议与可行性工作台。",
          },
        });
        return;
      }
      await submitTask("bundle.close", { source: state.source, output_root: state.runsRoot, name: `${paperTopicTitle(selectedDirection)}-web`, track_id: selectedDirection.track_id, discover_claims: true }, "close");
    } catch (error) {
      // A Workflow v2 Study must never fall back to the legacy four-stage
      // bundle shortcut. That path imports pre-existing artifacts and cannot
      // stand in for protocol, experiment, review, or paper execution.
      if (state.inspection?.study_id) {
        dispatch({ type: "PATCH", patch: { error: error.message } });
        return;
      }
      // Old inspections without a Study id retain the read-only compatibility
      // endpoint, but their output is not presented as a live Workflow v2 run.
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

  async function resumeTaskById(taskId) {
    dispatch({ type: "REQUEST", name: "resume", status: "pending" });
    try {
      const task = await api("/api/tasks/resume", { method: "POST", body: JSON.stringify({ task_id: taskId }) });
      dispatch({ type: "TASK_UPDATED", task });
      await handleTaskTransition(task);
      return task;
    } catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
    finally { dispatch({ type: "REQUEST", name: null, status: "idle" }); }
  }

  async function resumeTask() {
    if (!state.activeTask?.task_id) return null;
    return resumeTaskById(state.activeTask.task_id);
  }

  async function resolveRequirement(requirement, resolution) {
    try {
      const task = await api("/api/tasks/requirements/resolve", { method: "POST", body: JSON.stringify({ task_id: state.activeTask.task_id, requirement_id: requirement.requirement_id, resolution }) });
      dispatch({ type: "TASK_UPDATED", task });
      if (task.status === "paused") {
        await resumeTaskById(task.task_id);
      } else {
        await handleTaskTransition(task);
      }
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
    if (!task) { window.history.replaceState({}, "", window.location.pathname); dispatch({ type: "RESET" }); setActivePhase("discovery"); return; }
    window.history.replaceState({}, "", `?task=${encodeURIComponent(task.task_id)}`);
    dispatch({ type: "TASK_UPDATED", task });
    dispatch({ type: "PATCH", patch: { mode: task.request?.operation === "idea.start" ? "idea" : "project", view: taskUiState(task) || "running", taskEvents: [], notice: "", error: "" } });
    try { await handleTaskTransition(task); }
    catch (error) { dispatch({ type: "PATCH", patch: { error: error.message } }); }
  }

  if (state.boot === "loading" || runtime.loading || externalSetup.loading) return <div className="boot-screen"><CircleNotch className="spinner" size={30} /><span>正在检查 Research Forge…</span></div>;

  if (deploymentOpen) {
    return (
      <DeploymentWizard
        runtime={runtime}
        externalSetup={externalSetup}
        busy={deploymentBusy}
        error={deploymentError}
        onRefresh={refreshRuntime}
        onConfigure={configureRuntimeConnection}
        onConfigureResearch={configureExternalResearch}
        onComplete={completeDeployment}
      />
    );
  }

  const showTransientWorkspace = ["running", "attention", "remediation"].includes(state.view);
  const resumableHomeTask = state.tasks.find((task) =>
    ["waiting_for_user", "failed", "blocked", "running", "pending", "pause_requested", "paused"].includes(task.status)
  );

  const returnToOwningPhase = () => {
    const phase = phaseForTaskStage(state.activeTask?.stage);
    setActivePhase(phase);
    dispatch({
      type: "PATCH",
      patch: {
        view: phase === "discovery" ? "contract" : "study",
        notice: `已返回${phase === "discovery" ? "方向发现" : phase === "protocol" ? "协议与可行性" : phase === "experiment" ? "实验与判定" : "论文与审计"}，请按页面提示处理卡点。`,
      },
    });
  };

  return (
    <div className="app-shell">
      <GlobalHeader
        connected={runtime.ready}
        runtime={runtime}
        tasks={state.tasks}
        activeTask={state.activeTask}
        onSelectTask={selectTask}
        onOpenDeployment={() => setDeploymentOpen(true)}
      />
      <main className="workspace">
        <ResearchStageTabs activePhase={activePhase} workflow={activeWorkflow} onChange={setActivePhase} />
        <Notice notice={state.notice} error={state.error} />
        {showTransientWorkspace && state.view === "running" ? <RunningState task={state.activeTask} events={state.taskEvents} onPause={pauseTask} onResume={resumeTask} /> : null}
        {showTransientWorkspace && state.view === "attention" ? <AttentionState task={state.activeTask} onResolve={resolveRequirement} onResume={resumeTask} onBackToContract={returnToOwningPhase} /> : null}
        {showTransientWorkspace && state.view === "remediation" ? <RemediationWorkspace plan={state.remediationPlan} onApprove={(selected) => approveRemediation(selected, false)} onConfirmContract={(selected) => approveRemediation(selected, true)} onArchive={archiveRemediation} loading={requestPending} /> : null}
        {!showTransientWorkspace && activePhase === "discovery" ? (
          <>
            {state.view === "intake" ? <ModeSelector mode={state.mode} onChange={(mode) => dispatch({ type: "SET_MODE", mode })} disabled={requestPending} /> : null}
            {state.view === "intake" ? <><Hero mode={state.mode} />{state.mode === "project" ? <ProjectIntake source={state.source} onSource={(source) => dispatch({ type: "PATCH", patch: { source } })} onChooseFolder={chooseFolder} onSubmit={inspectProject} loading={requestPending} /> : <IdeaIntake onSubmit={startIdea} loading={requestPending} />}</> : null}
            {state.view === "intake" ? <ResumeTaskCard task={resumableHomeTask} onOpen={selectTask} /> : null}
            {state.view === "intake" && !state.tasks.length ? <FirstRunGuide mode={state.mode} /> : null}
            <ExternalResearchStatus report={retrievalReadiness} workflow={activeWorkflow} compact={state.view === "intake" && !activeWorkflow?.study} />
            {state.view === "directions" ? <DirectionSelect inspection={state.inspection} selectedId={state.selectedDirectionId || selectedDirection?.topic_id || selectedDirection?.direction_id || selectedDirection?.track_id} onSelect={(selectedDirectionId) => dispatch({ type: "PATCH", patch: { selectedDirectionId } })} onBack={() => dispatch({ type: "RESET" })} onContinue={() => dispatch({ type: "PATCH", patch: { view: "contract" } })} /> : null}
            {state.view === "contract" ? <ContractReview direction={selectedDirection} source={state.source} onBack={() => dispatch({ type: "PATCH", patch: { view: "directions" } })} onConfirm={confirmContract} loading={requestPending} /> : null}
            {state.view === "idea_ready" ? <IdeaReady result={state.ideaResult} onOpenPath={openPath} onNew={() => dispatch({ type: "RESET" })} /> : null}
            {!["intake", "directions", "contract", "idea_ready"].includes(state.view) ? <StudyWorkflowPanel workflow={activeWorkflow} activePhase="discovery" onPhaseChange={setActivePhase} /> : null}
          </>
        ) : null}
        {!showTransientWorkspace && ["protocol", "experiment"].includes(activePhase) ? (
          <StudyWorkflowPanel workflow={activeWorkflow} activePhase={activePhase} onPhaseChange={setActivePhase} />
        ) : null}
        {!showTransientWorkspace && activePhase === "paper" ? (
          <>
            <StudyWorkflowPanel workflow={activeWorkflow} activePhase="paper" onPhaseChange={setActivePhase} />
            {state.view === "verdict" ? <VerdictState run={state.run} onOpenPath={openPath} onExpand={expandPaper} onRemediate={openRemediation} onNew={() => { dispatch({ type: "RESET" }); setActivePhase("discovery"); }} onInspectEvidence={setDrawerItem} /> : null}
          </>
        ) : null}
      </main>
      <EvidenceDrawer item={drawerItem} onClose={() => setDrawerItem(null)} />
    </div>
  );
}
