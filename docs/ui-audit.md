# Research Forge 前端产品审计

> 审计日期：2026-07-23
> 范围：`research-forge-ui` 前端、前端调用到的后端对象与接口、现有《Research Forge 用户使用手册（截图版）》
> 方法：源码只读检查、业务对象映射、真实页面状态截图、1024 / 1280 / 1440 视口检查、键盘与基础可访问性检查
> 本文不修改业务逻辑、接口、路由或视觉实现。

## 1. 结论

当前产品已经具备一条可运行的研究闭环，但前端仍然更像“带大幅宣传标题的任务演示页”，而不是让用户持续管理项目、契约、运行、证据与后继运行的研究工作台。

最严重的问题不是美观，而是研究对象的关系没有被稳定呈现：

1. 用户无法持续确认“我在哪个项目、哪个契约版本、哪次运行、哪个判定中”。
2. 生命周期阶段、异步任务状态、科学判定被混在同一个页面状态机里。
3. 选题、契约、证据、补证据计划都缺少适合比较和审计的信息密度。
4. 页面首屏被 290–410 px 的大 Hero 占据，关键操作普遍落到折叠线以下。
5. 选项目后前端默认传 `discover_claims: true`，后端会访问 Semantic Scholar、Crossref，并在配置后访问 RedFox；页面没有在请求前明确告知或征得同意。这与“外部服务不得静默调用”冲突。

建议先完成信息架构与状态拆分，再做视觉压缩。仅调字号和卡片间距无法解决对象身份、历史不可变和安全暂停的问题。

## 2. 审计依据与业务真值

业务主链以用户手册和持久化对象为准：

```text
Project
  → Research Candidate
  → Research Contract Version
  → Research Run
  → Evidence / Artifact
  → Scientific Verdict
  → Evidence Follow-up Plan
  → Successor Run
```

必须保持的约束：

- 项目材料只读扫描。
- 外部服务调用必须可见、可选择、可审计。
- UI 只展示可审计的“正在做什么 / 为什么 / 产物 / 下一步”，不展示模型内部思维链。
- 契约冻结后当前运行不得改变契约；设计变化必须创建新版本并由用户确认。
- 原运行、原证据、原判定不可覆盖。
- `mixed`、`inconclusive`、`unverifiable` 是进入补证据闭环的中间状态，不是研究失败。
- 缺少数据、环境、配置、API 或权限时安全暂停；只有用户确认的补证据任务可以运行。
- 不伪造进度、置信度、材料或实验结果。

## 3. 当前前端架构地图

### 3.1 技术结构

| 项目 | 当前实现 | 审计判断 |
|---|---|---|
| 框架 | React 19 + Vite 6 | 足够，无需换栈 |
| 路由 | 无路由库；用 `?task=`、`?run=`、`?remediation=` 恢复部分状态 | URL 可恢复性不完整 |
| 状态 | `App.jsx` 单一 `useReducer`，外加多个 `useState/useEffect` | 生命周期、任务、科学状态耦合 |
| 组件 | 14 个页面/局部组件全部位于 `App.jsx` | 难测试、难复用、改动风险高 |
| 样式 | 单一 `styles.css` | Token 有雏形，但页面级样式耦合 |
| 组件库 | 无；图标使用 Phosphor | 可保留，不需要引入重型 UI 库 |
| 测试 | 未发现前端组件、路由或可访问性测试 | 回归主要依赖人工截图 |

关键文件：

- `research-forge-ui/src/App.jsx`：全局状态机、页面组件、接口调用与 URL 同步。
- `research-forge-ui/src/workflow.js`：阶段、判定、任务文案和状态派生。
- `research-forge-ui/src/styles.css`：全部 Token、布局、响应式与动效。
- `research-forge-ui/src/main.jsx`：React 入口。
- `research-forge-ui/index.html`：当前 `lang="en"`、标题为 `Prototype`。

### 3.2 当前逻辑页面与入口

产品没有真正的页面路由，下表是用户实际感知的逻辑页面：

| 逻辑页面 | 进入方式 | 核心数据 | 当前缺口 |
|---|---|---|---|
| 选择起点 | `/` | `/api/bootstrap`、最近任务 | 没有项目列表/项目身份；Hero 抢占首屏 |
| 已有项目录入 | `/` 项目模式 | source、runs root | 外部检索默认开启但未预告 |
| 想法录入 | `/` 想法模式 | idea、可选来源 | 与已有项目共用首页，语义尚清楚 |
| 执行中 | `?task=<id>` | TaskRecord、TaskEvent[] | 有具体事件是优点；缺稳定对象上下文 |
| 候选选题 | 成功的 inspect task | BundleInspection、NoveltyCandidate[] | 纵向大卡，不适合横向比较 |
| 契约确认 | 候选页继续 | 前端临时拼装的方向信息 | URL 不可恢复；固定写“v1”；无 diff |
| 等待用户处理 | waiting task | TaskRequirement | 结构化输入较好；失败也可能被误判成缺条件 |
| 想法结果 | 成功的 idea task | ideaResult | 不在已有项目主链重点内 |
| 研究判定 | `?run=<name>` | run detail、IdeaVerdict、artifacts | 三门分开是优点；运行谱系与证据入口弱 |
| 补证据计划 | `?remediation=<name>` | RemediationPlan、Action[] | 缺依赖/所需输入，首屏看不到确认区 |
| 证据抽屉 | 判定页内打开 | 任意对象 JSON | 原始 JSON，不是证据阅读器；键盘关闭失效 |
| 任务切换器 | 全局头部 | TaskRecord[] | 只显示任务，不显示项目/运行谱系；无菜单语义 |

### 3.3 前端状态与后端状态的混合点

`taskUiState()` 把以下不同维度折叠为一个 `view`：

- 研究生命周期：发现、契约、实验、论文。
- 异步执行状态：pending、running、pause_requested、paused、waiting_for_user、failed、succeeded。
- 操作类型：inspect、close、expand、remediate、idea。
- 科学判定：supported、refuted、mixed、inconclusive、unverifiable。

直接后果：

- `failed` / `blocked` 被导向“等待用户处理”，并由 `inferRequirement()` 合成一个“配置问题”，可能掩盖真实错误。
- `bundle.close` 在尚未收到事件时默认显示 synthesis，可能短暂把正在开始的运行标成第 4 阶段。
- `refuted` 页面主标题为“未获支持”，摘要仍用“尚未验证成功”，把明确反驳弱化成一般失败。
- 契约确认是前端瞬时 view，不是可恢复的契约版本页面。

### 3.4 后端已有、前端未充分利用的数据

无需新增后端字段即可改善：

| 对象 | 已有字段 | 前端现状 |
|---|---|---|
| ProjectMeta / ProjectState | slug、name、stage、revision、run ids/count | 未形成项目上下文 |
| NoveltyCandidate | research material、maturity、paperability、blockers、artifact chain | 展示分散，难比较 |
| ScopeContract / ProtocolLock | research question、hypothesis、scope、protocol/output hash、binding | 契约页只展示少量文本，版本和冻结关系弱 |
| TaskRecord / TaskEvent | status、attempt、resumable、requirement、title/detail/reason/output/next/stage | 事件展示较好，但状态语义被 UI view 吞掉 |
| IdeaVerdict | 五种 verdict、结论、数值证据、路径、限制、下一步 | 三门卡片存在，但证据与判定链不够可追踪 |
| RemediationAction | dependencies、required_inputs、execution_mode、contract_impact、experiment command | 页面未显示 dependencies / required_inputs |
| RemediationPlan | source run、version、contract version、selected actions、successor run、next plan | 未形成计划版本与后继运行谱系 |
| Run detail / artifacts | stages、resources、claims、manuscripts、audit、artifacts | 主要压进原始 JSON 抽屉 |

## 4. 页面证据

### 4.1 首页

![首页 1440 审计截图](ui-audit-assets/01-intake-1440x900.png)

问题：

- 1440×900 下，Hero 几乎占满首屏，用户尚未看到核心“选择项目”操作。
- 主标题 48–78 px，更像营销首页；科研工具的对象和动作应优先。
- “四阶段闭环”作为宣传信息重复出现，却没有回答当前项目、运行和待处理事项。

### 4.2 候选选题

![候选选题 1440 审计截图](ui-audit-assets/04-candidates-1440x900.png)

问题：

- 首屏只看到“找到了多少方向”和第一张卡片边缘。
- 每个候选按长卡片纵向堆叠，用户无法同时比较问题、证据成熟度、阻塞项与成本。
- “推荐”缺少清楚、可审计的推荐理由聚合。

### 4.3 契约确认

![契约确认 1440 审计截图](ui-audit-assets/06-contract-1440x900.png)

问题：

- “确认契约”关键动作不在 1440×900 首屏。
- 页面硬编码为 v1，无法表达现有版本、待确认版本与差异。
- 没有把“本次改动是否改变研究设计”作为显著的不可逆分流点。

### 4.4 判定与补证据

![混合判定 1440 审计截图](ui-audit-assets/08-verdict-mixed-1440x900.png)

![补证据计划 1440 审计截图](ui-audit-assets/10-remediation-1440x900.png)

问题：

- 判定头部占据过多空间，证据门、缺口和下一步在下方。
- 补证据动作虽显示缺口、方法、产物、成本与契约影响，但没有显示依赖和所需输入。
- 勾选与确认区不在首屏，用户不容易理解“系统只会执行已勾选动作”。
- 未形成“来源运行 → 计划版本 → 后继运行”的可见谱系。

### 4.5 安全暂停

![等待用户处理 1440 审计截图](ui-audit-assets/11-requirement-1440x900.png)

优点：

- 已使用结构化 requirement，能表达原因、输入类型、协议变化与外发数据说明。
- 有“补充后恢复”和“返回契约”两类恢复路径。

问题：

- failed / blocked 也可能被前端合成为 requirement，真实错误与缺少条件没有分开。
- 输入值的校验成功、失败、可继续状态不够明确。
- 页面仍被大 Hero 占据，恢复动作和历史事件被推后。

### 4.6 执行中

![执行中手册参考截图](ui-audit-assets/13-running-manual-reference.png)

说明：当前审计会话中没有正在运行的任务；此图来自用户明确提供的现有手册素材，仅作为执行中页面参考，不冒充本次重新运行的截图。

优点：

- 页面已经开始显示具体工作、阶段、事件和安全暂停。
- 未伪造百分比进度，这是正确方向。

问题：

- “执行四阶段研究闭环”仍然占据最大视觉权重，真正的当前工作在下方。
- 应把当前步骤、原因、已产生的产物、下一步和最近事件放到首屏中心。
- 暂停按钮需要同时说明暂停边界：等待当前安全检查点，不承诺瞬时中断。

### 4.7 全局任务切换器与证据抽屉

![任务切换器 1440 审计截图](ui-audit-assets/12-task-switcher-1440x900.png)

![证据抽屉 1440 审计截图](ui-audit-assets/09-evidence-drawer-1440x900.png)

问题：

- 任务切换器覆盖右侧约 390 px，只列任务标题和状态，无法确认所属项目、契约或运行。
- 任务切换器没有 menu/dialog 语义；打开后焦点仍停留在触发按钮，Escape 不关闭。
- 证据抽屉虽有 `role="dialog"`，但不移动焦点、不锁焦、不使背景失活，Escape 不关闭。
- 抽屉直接展示原始 JSON，不适合人类阅读证据、哈希、路径和限制。

## 5. 问题清单

严重度定义：

- P0：会让用户误解研究对象、授权边界、科学结论或历史不可变性。
- P1：明显阻碍核心任务完成、恢复或审计。
- P2：降低效率、一致性、响应式或可访问性质量。

### P0

| ID | 问题 | 证据/文件 | 用户影响 |
|---|---|---|---|
| P0-01 | 项目、契约版本、运行、判定没有持久上下文条 | `App.jsx` 的 `GlobalHeader` 只显示连接和任务 | 用户可能在错误运行上确认、补证据或解读结论 |
| P0-02 | 外部趋势检索被默认静默开启 | `App.jsx` 两处传 `discover_claims: true`；`claim_discovery.py` 会请求 Semantic Scholar / Crossref / RedFox | 违背明确授权边界；用户不知道查询内容被发送到哪里 |
| P0-03 | 任务失败与缺少条件被合并 | `workflow.js::taskUiState()`、`inferRequirement()` | 真实失败可能被伪装成“补一个配置即可”，恢复路径错误 |
| P0-04 | 契约不是稳定、可恢复、可比较的页面对象 | 契约 view 依赖内存中的 selected direction；文案固定 v1 | 刷新后丢失上下文；无法确认新版本差异；冻结约束不可信 |
| P0-05 | 科学判定、执行状态、生命周期混用 | 单一 reducer + view 推导 | “反驳”“证据不足”“执行失败”“等待输入”可能被用户当成同一种未成功 |

### P1

| ID | 问题 | 证据/文件 | 用户影响 |
|---|---|---|---|
| P1-01 | 关键动作在 1440×900 折叠线下 | 首页、候选、契约、判定、补证据截图 | 核心路径每步都需额外滚动，页面像展示页而非工具 |
| P1-02 | 候选选题不能横向比较 | `DirectionSelect` 使用纵向大卡 | 难比较研究问题、成熟度、证据链、阻塞项与工作量 |
| P1-03 | 补证据计划缺依赖与所需输入 | `RemediationWorkspace` 未渲染已有字段 | 用户确认后才发现缺数据/API/环境，无法做知情选择 |
| P1-04 | 运行谱系不可见 | 页面未串联 predecessor、plan、contract version、successor | 用户难以判断新运行是否覆盖历史、为何开启 |
| P1-05 | 证据抽屉是原始 JSON | `EvidenceDrawer` | 无法快速区分结论、数值、来源、哈希、限制和可打开产物 |
| P1-06 | 当前执行阶段可能先显示错误默认值 | `RunningState` 对 close 的缺省 stage 为 synthesis | 首次加载或事件延迟时误导用户 |
| P1-07 | URL/浏览器历史恢复不完整 | 只有 replaceState，无 popstate；契约/方向无独立 URL | 刷新、前进后退、分享链接时状态不可靠 |
| P1-08 | 全局恢复入口弱 | 任务菜单只显示有限任务，缺项目/运行筛选与待处理分区 | 用户难找回 waiting、paused、failed、历史运行 |

### P2

| ID | 问题 | 证据/文件 | 用户影响 |
|---|---|---|---|
| P2-01 | 92 处字体为 8–13 px | `styles.css` | 元数据与标签难读，不满足正文最小 14 px 目标 |
| P2-02 | 次要文字对比不足 | `#86868b` 在白色上约 3.62:1，在画布上约 3.33:1 | 小字号文本达不到 WCAG AA |
| P2-03 | 抽屉/任务菜单键盘行为不完整 | 实测 Escape 不关闭、焦点不进入 | 键盘和读屏用户容易迷失 |
| P2-04 | 隐藏 checkbox 缺清晰焦点态 | `.action-check input` 透明 | 无法可靠判断当前选中控件 |
| P2-05 | 文档语言与标题错误 | `index.html` 为 `lang="en"`、`Prototype` | 读屏发音与浏览器页签错误 |
| P2-06 | 组件全部集中在单文件 | `App.jsx` 约千行 | 难做单组件测试、状态故事和渐进迁移 |
| P2-07 | 视觉语言偏营销展示 | 大标题、较大圆角、明显阴影、宽松留白 | 信息密度与研究工作台定位不符 |

## 6. 响应式与可访问性结果

### 6.1 响应式

| 页面 | 1440 | 1280 | 1024 | 结论 |
|---|---|---|---|---|
| 首页 | 无横向溢出，CTA 下折 | 无横向溢出，CTA 下折 | 无横向溢出，入口卡仅部分进入首屏 | 布局不破，但层级失衡 |
| 候选 | 页面高约 1918 px | 同类表现 | 页面高约 1822 px | 比较效率低 |
| 契约 | 页面高约 1312 px | 同类表现 | 可用但需滚动 | 确认动作不在首屏 |
| 判定 | 页面高约 1313 px | 约 1339 px | 约 1324 px | 下一步与证据下沉 |
| 补证据 | 页面高约 1329 px | 约 1329 px | 约 1407 px | 任务确认下沉 |
| 安全暂停 | 900 px 左右 | 900 px 左右 | 约 912 px | 结构可用，层级仍松 |

没有观察到 1024/1280/1440 的水平滚动；主要问题是首屏利用率和纵向信息密度。

### 6.2 可访问性

- 正面：原生 button/input 使用较多；全局有 `:focus-visible`；支持 `prefers-reduced-motion`。
- 阻断项：证据抽屉和任务菜单未实现焦点进入、焦点圈、Escape、关闭后焦点恢复。
- 阻断项：任务菜单没有 menu/dialog 语义；候选选择没有 `aria-pressed` / radio 组语义。
- 阻断项：页面语言错误。
- 风险项：大量 8–13 px 文本和低对比 `ink-faint`。
- 风险项：状态颜色不能成为唯一信息通道，应始终同时显示文本和图标。

## 7. 当前设计中值得保留的部分

- 执行中使用可审计事件，而不是暴露内部推理。
- 没有伪造线性百分比。
- 支持安全暂停、补充条件、恢复和返回契约。
- 判定页把想法门、证据门、文献门分开。
- 补证据动作已经具备缺口、方法、产物、成本、执行模式、契约影响等结构化字段。
- 后端已保存原运行、计划版本、契约版本与后继运行关系，前端可直接利用。

这些能力应迁移到更紧凑的工作台结构中，而不是重写业务语义。

## 8. 审计边界

- 没有创建新任务、运行、契约或补证据计划。
- 没有调用外部检索服务。
- 没有修改业务代码、接口、路由或样式。
- “执行中”截图来自既有用户手册参考；其他截图来自本次本地只读页面检查。
