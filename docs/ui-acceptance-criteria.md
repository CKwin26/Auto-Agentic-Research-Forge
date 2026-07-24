# Research Forge UI 验收标准

> 适用于 `ui-redesign-plan.md` 的实现。所有标准必须基于真实接口返回或明确测试 fixture；不得用伪造生产数据证明通过。

## 1. 总体验

- [ ] 已选择项目后，任意核心页面持续显示 Project、Contract version、Run、Verdict/Task status；缺失项明确显示“尚未创建”，不得猜测。
- [ ] 1440×900 首屏同时看见页面标题、当前状态、核心内容和唯一主操作。
- [ ] 页面标题计算值不大于 32 px；正文与关键元数据不小于 14 px。
- [ ] 不存在高度超过 160 px 的营销 Hero。
- [ ] 核心页面不使用渐变、插画或强卡片阴影；层级主要由边框、间距和底色表达。
- [ ] 页面没有虚假线性百分比、置信度或不存在的材料/实验结果。

## 2. 信息架构与导航

- [ ] 生命周期导航明确区分：概览、候选、契约、运行、证据、判定、补证据。
- [ ] 运行页内的四阶段不与生命周期导航混用。
- [ ] Task status 与 Scientific verdict 使用不同组件语义或显式 domain 参数。
- [ ] `supported/refuted/mixed/inconclusive/unverifiable` 五个原始判定均有唯一中文标签。
- [ ] `refuted` 显示为“反驳”，不使用“尚未验证成功”。
- [ ] 浏览器后退/前进能恢复 `?task=`、`?run=`、`?remediation=` 对应页面。
- [ ] 刷新上述 URL 后，已知对象 ID 保留；加载失败显示重试，不静默回首页。

## 3. 起点与外部服务

- [ ] 已有项目和只有想法两个入口在 1440×900 首屏完整可见。
- [ ] 扫描前明确显示是否会使用外部趋势来源。
- [ ] 未获得用户明确选择或确认时，前端不得提交 `discover_claims: true`。
- [ ] 启用外部来源时，页面列出 Semantic Scholar、Crossref，以及配置后可能使用的 RedFox/微信公众号来源。
- [ ] 页面明确说明发送的数据类别；不声称会发送或不会发送后端无法保证的内容。
- [ ] 外部来源不可用时，本地扫描仍可完成，并明确显示 provider status，不把降级伪装成成功。

## 4. 候选选题

- [ ] 1440×900 首屏至少同时看见 5 个候选的题目与核心比较字段；候选不足 5 个时显示全部。
- [ ] 每个候选至少展示：研究问题/题目、成熟度、证据链状态、阻塞项数量、是否推荐。
- [ ] 选中候选的详情展示推荐理由、现有材料、阻塞项和下一步。
- [ ] 推荐理由完全来自现有后端字段，不生成无来源的热度分、置信度或排名解释。
- [ ] 候选选择具备 radio group 或等价可访问语义；选中状态不只依靠边框颜色。
- [ ] 用户可以键盘完成选择并进入契约。

## 5. 契约与版本

- [ ] 契约页明确显示草稿/冻结状态、版本号、来源 candidate/plan。
- [ ] 确认前展示研究问题、假设、范围、指标/基线/门槛、协议输入输出中接口已返回的字段。
- [ ] 当前版本与 vNext 同时存在时，所有可比较字段显示 old/new 值和是否属于设计变化。
- [ ] 无法比较的字段标明“未返回可比较值”，不得推测。
- [ ] 样本、指标、基线、门槛等设计变化必须进入新版本确认。
- [ ] 冻结按钮附近明确说明：当前运行不会修改该契约。
- [ ] 1440×900 首屏可见“冻结并创建运行”主操作。

## 6. 执行中、暂停与恢复

- [ ] 执行页首屏显示：当前工作、原因、预期/已产生的产物、下一步、四阶段状态。
- [ ] 上述文字只来自 TaskEvent/TaskRecord 等可审计字段，不显示模型内部思维链。
- [ ] 没有事件时显示“正在初始化运行”，不得默认显示 synthesis 或其他后期阶段。
- [ ] 点击暂停后立即显示 `pause_requested`，并说明“将在安全检查点暂停”。
- [ ] `paused`、`waiting_for_user`、`failed`、`blocked` 分别有不同标题、说明和操作。
- [ ] `failed/blocked` 不通过 `inferRequirement` 合成为虚构的配置需求。
- [ ] 页面显示 attempt、最近更新时间、是否可恢复和最近事件。
- [ ] 刷新或重启服务后，当前任务及事件能恢复。

## 7. 结构化条件处理

- [ ] requirement 展示 kind、title、reason、required、accepted inputs、alternatives、changes protocol、external disclosure。
- [ ] 每项用户输入有“未校验 / 校验中 / 通过 / 不通过”状态。
- [ ] 校验不通过时给出后端错误，不丢失用户已填内容。
- [ ] `changes_protocol=true` 时主流程进入契约 vNext，不直接恢复原运行。
- [ ] `changes_protocol=false` 时可沿用当前契约创建/恢复后继执行。
- [ ] 缺少 API、凭据、环境或权限时不继续执行相关动作。
- [ ] 用户选择替代方案或跳过时，结果写入可见审计事件。

## 8. 证据与判定

- [ ] Evidence Viewer 默认以结构化字段展示结论、数值、来源运行、契约/协议绑定、产物路径和限制。
- [ ] 原始 JSON 只在“查看原始记录”内展开。
- [ ] 可打开的产物使用现有 `/api/open-path` 或 artifact 能力；不存在的路径不显示为可用链接。
- [ ] 判定页分别显示想法门、证据门、文献门，不把三者汇总成一个模糊成功/失败。
- [ ] supported/refuted 的主操作进入论文资格审查。
- [ ] mixed/inconclusive/unverifiable 的主操作为生成/打开补证据计划。
- [ ] “结束并归档”存在但为次操作。
- [ ] 判定页能追溯到 run、contract version 和证据/产物。

## 9. 补证据闭环

- [ ] 计划页显示 plan version、source run、source verdict、contract version 和 plan status。
- [ ] 每个动作显示 gap、method、artifact、priority、cost、execution mode、contract impact、dependencies、required inputs、status。
- [ ] 用户可勾选部分动作；页面实时显示已选数量和执行摘要。
- [ ] 批准请求只包含已选动作及后端定义的必要依赖。
- [ ] 未勾选动作不得进入 queued/running。
- [ ] 有 `new_version` 动作时必须先进入契约确认，确认前不得执行。
- [ ] 全部 `same_contract` 时沿用当前契约，但创建独立后继运行。
- [ ] 页面显示 `predecessor run → remediation plan → successor run`。
- [ ] 原运行、原判定和原 artifact hash 在补证据后保持不变。
- [ ] 仍证据不足时显示 next plan version，但不自动批准或无限执行。
- [ ] 用户可归档当前计划和研究；该操作不是默认主操作。

## 10. 任务恢复

- [ ] 全局待处理入口按 waiting、paused、failed、running、history 分组。
- [ ] 每项至少显示所属项目、run/task、状态、更新时间。
- [ ] 任务切换后上下文条与页面对象一致。
- [ ] 任务列表不因数量增加而遮挡主操作；支持滚动并保持标题区固定。
- [ ] 网络错误时不清空任务列表；显示上次成功加载时间和重试。

## 11. 响应式

以下视口必须逐页截图回归：1440×900、1280×800、1024×768。

- [ ] 所有核心页面 `document.documentElement.scrollWidth <= window.innerWidth`。
- [ ] 1440：左侧导航展开，主内容宽度可读，主操作首屏可见。
- [ ] 1280：比较表和详情区无内容裁切；上下文条不换成多行大块。
- [ ] 1024：导航可收窄；表格允许受控横向容器滚动，但整个页面不得横向滚动。
- [ ] 1024：主操作仍可见或固定在可见操作条。
- [ ] 长项目名、run id、路径和 hash 使用截断 + 完整值访问方式，不撑破布局。
- [ ] 不以隐藏关键字段作为适配手段；可把次要字段移入详情面板。

## 12. 可访问性

- [ ] `html lang="zh-CN"`，页面标题包含 Research Forge 与当前对象。
- [ ] 所有交互可用键盘完成，Tab 顺序与视觉顺序一致。
- [ ] 所有可交互目标最小 40×40 CSS px；紧凑表格中的行操作可通过整行或菜单达到该目标。
- [ ] 每个控件有可访问名称；纯图标按钮有 `aria-label`。
- [ ] Dialog/Drawer 打开时焦点进入，Tab 被限制在浮层内，Escape 关闭，背景不可操作，关闭后焦点回到触发器。
- [ ] Task switcher 采用 menu 或 dialog 的完整语义与键盘模型。
- [ ] checkbox/radio 的焦点环在高对比下清晰可见。
- [ ] 正文对比度 ≥4.5:1；大文本和非文本控件边界 ≥3:1。
- [ ] 状态不用颜色作为唯一信息；同时有文本和图标。
- [ ] `prefers-reduced-motion: reduce` 下无非必要动画。
- [ ] 自动化 axe/WAVE 等扫描无 critical/serious 问题；人工键盘检查通过。

## 13. 性能与可靠性

- [ ] `/api/bootstrap`、task polling、event polling 不因组件拆分产生重复并发请求。
- [ ] 页面不可见或任务终态后停止对应 polling。
- [ ] 请求竞态不会让旧 task/run 覆盖新选择。
- [ ] 加载中保留当前对象标识和布局骨架，不闪回首页。
- [ ] 任一 API 返回缺字段时页面可降级，控制台无未处理异常。

## 14. 测试矩阵

至少覆盖以下 fixture/真实样例：

| 领域 | 必测状态 |
|---|---|
| Task | pending、running、pause_requested、paused、waiting_for_user、failed、blocked、succeeded |
| Verdict | supported、refuted、mixed、inconclusive、unverifiable |
| Requirement | dataset、credential、permission、environment、configuration、protocol_change |
| Remediation | draft、awaiting_approval、approved、executing、awaiting_user、completed、superseded、archived |
| Action | proposed、selected、queued、running、waiting_for_user、completed、failed、skipped |
| Contract impact | same_contract、new_version |
| External provider | not_requested、not_configured、ok、degraded |
| Data | 空候选、单候选、多候选、长标题、缺可选字段、长路径 |

## 15. 发布门槛

每个独立 PR 必须：

- [ ] 不改变现有 API 请求/响应语义，除非该 PR 明确只调整已有布尔参数的用户确认时机。
- [ ] 通过对应状态单元/组件测试。
- [ ] 附 1440、1280、1024 三档前后截图。
- [ ] 附键盘路径检查结果。
- [ ] 说明 feature flag 或组件级回滚方法。

整体验收只有在 P0 全部关闭、P1 无阻断项、核心闭环全状态通过后完成。
