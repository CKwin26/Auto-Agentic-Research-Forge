# 研究审稿根因预检

- 项目：`research-agent-evidence-v2`
- 目标：`publication`
- 闸门结论：**阻断**
- 最大允许结论层级：`internal_proxy_association`
- 预测编辑结论：`major_revision_or_reject`

## 根结论

本轮大修的主因不是文字质量，而是测量同源、反事实不隔离，以及尚未完成人工校准时就进入发表审批语境。稿件修订能改善透明度，但不能补造缺失的实验识别条件。

## 因果链

设计/测量合同 → 可识别性与构念覆盖 → 可支持的主张上限 → 审稿结论

## 根因清单

| 编号 | 严重度 | 根因 | 最晚预防阶段 | 永久规则 |
|---|---|---|---|---|
| RC-MEASUREMENT-CIRCULARITY | critical | 测量回路同源 | before protocol freeze | 只要 generator/intervention/evaluator 同源且无独立校准，结论上限自动降为 internal_proxy_association；禁止 publication-ready。 |
| RC-COUNTERFACTUAL-NONISOLATION | critical | 反事实对照未隔离 | before protocol freeze | 下游干预必须从内容寻址的同一上游 artifact 生成两条分支；若做不到，必须随机化或按 pair 交错执行，并自动禁止 causal-effect 措辞。 |
| RC-CONSTRUCT-UNDERCOVERAGE | high | 指标没有覆盖完整科学构念 | before plan approval | 任何以“可靠性/质量改善”为目标的 gate 实验必须同时冻结支持性、语义变化类型和信息性指标；只测支持性时，构念名称强制改为 claim-evidence fidelity proxy。 |
| RC-MATURITY-TARGET-MISMATCH | critical | 研究成熟度与审批目标不匹配 | before external review submission | 若 primary_analysis_interpretable=false 或 publication_ready=false，外部 AI 审稿只能标记为 developmental critique；系统不得把它路由为发表审批。 |
| RC-REPORTING-CONTRACT-GAP | medium | 报告合同缺少逐配对数据 | before plan approval | 当配对单元数不超过 20 时，报告合同自动要求逐单元表、聚合结果与 leave-one-group-out 敏感性分析。 |
| RC-TELEMETRY-SCHEMA-GAP | medium | 成本遥测在运行前没有冻结 | before execution | 凡比较 agent 工作流成本，执行合同至少冻结 wall-clock、model-call count、token usage 和 escalation count；缺字段时在开跑前失败。 |
| RC-LITERATURE-SCOPE-COUPLING | medium | 实验冻结文献与投稿定位文献被混为一层 | before manuscript synthesis | 分离 frozen experimental corpus 与 post-freeze contextual review；后者可更新，但不得反向改变已冻结实验假设。 |

### RC-MEASUREMENT-CIRCULARITY · 测量回路同源

**根因：** 生成器、干预 gate 与主结果评估器共享同一模型家族，且独立人工校准未完成。系统因此同时参与了优化目标和目标测量。

**后果：** unsupported_claim_rate 的下降最多证明内部代理指标下降，不能区分真实证据改进与同源评估器偏好。

**系统修复：** 增加跨模型家族评估器或完成冻结的双人盲审，并报告评估器混淆矩阵；否则只按内部 pilot 解释。

**证据：**

- `backbone.model=codex:gpt-5.4`
- `protocol.treatment_gate=structural_plus_frozen_codex_gate`
- `protocol.protected_evaluator=hybrid_structural_plus_arm_blinded_codex`
- `human_validation_complete=False`

**对应审稿症状：**

- 审稿人质疑 same-family evaluator
- 审稿人提出评估器偏好替代解释
- 审稿人要求独立校准

### RC-COUNTERFACTUAL-NONISOLATION · 反事实对照未隔离

**根因：** 两组由独立随机运行产生，而不是从同一上游运行产物分叉；协议还按整块顺序先执行全部 baseline，再执行 treatment。

**后果：** 组间差异混合了 gate 效果、上游随机差异和时间顺序效应；结果只能表述为观察到的组间关联，不能识别 gate 的因果效果。

**系统修复：** 把每个 task-seed 的实验产物只运行一次，再将同一 artifact 同时送入无 gate 与有 gate 的结论生成分支；分支顺序随机化。

**证据：**

- `cell_arm_order=baseline -> baseline -> baseline -> baseline -> baseline -> baseline -> baseline -> baseline -> baseline -> treatment -> treatment -> treatment -> treatment -> treatment -> treatment -> treatment -> treatment -> treatment`
- `blocked_baseline_then_treatment=True`
- `shared_artifact_branching_declared=False`

**对应审稿症状：**

- 审稿人指出独立随机运行
- 审稿人指出固定整块顺序
- 审稿人要求同一运行产物分叉
- 审稿人观察到上游结果差异

### RC-CONSTRUCT-UNDERCOVERAGE · 指标没有覆盖完整科学构念

**根因：** 主指标只测 supplied evidence 下的 unsupported 标签，没有同时测量主张是否被删除、弱化、限定，以及输出对科学用户是否仍有信息价值。

**后果：** 即使 unsupported 标签下降，也无法判断是事实纠正，还是通过把主张写得更弱来规避判错。

**系统修复：** 增加 revision-trace 编码：删除、事实纠正、限定、语义收缩；再增加成对的信息性/有用性判断。

**证据：**

- `primary_metric=unsupported_claim_rate`
- `construct_metrics=[]`

**对应审稿症状：**

- 审稿人提出语义收缩替代解释
- 审稿人指出相同数量不代表信息量相同
- 审稿人要求测量信息性或有用性

### RC-MATURITY-TARGET-MISMATCH · 研究成熟度与审批目标不匹配

**根因：** 协议把独立人工审计定义为主分析解释条件，但当前流程主动延期该审计，却仍把论文送入publication-level 审批语境。

**后果：** 在不改变证据的情况下，发表评审给出大修是可预测结果，而不是写作润色能够消除的意外。

**系统修复：** 本阶段保留 provisional pilot 身份；等独立审计完成后再开启 publication target。

**证据：**

- `human_validation=deferred`
- `manual_audit_is_preregistered=true`
- `primary_analysis_interpretable=False`

**对应审稿症状：**

- 审稿人把延期人审视为发表关键缺口
- 审稿人区分工程 pilot 与发表证据
- 编辑结论为大修

### RC-REPORTING-CONTRACT-GAP · 报告合同缺少逐配对数据

**根因：** 计划要求 paired comparison，但没有硬性规定在论文中列出每个 task-seed 的原始配对效应。

**后果：** 综合均值和 bootstrap 区间掩盖了每个 cell 以 0.25 为步长的离散性。

**系统修复：** 在协议的 reporting_requirements 中加入 pair_level_table=true。

**证据：**

- `raw_pair_reporting_preregistered=False`

**对应审稿症状：**

- 审稿人要求公开九个原始配对效应

### RC-TELEMETRY-SCHEMA-GAP · 成本遥测在运行前没有冻结

**根因：** 运行只强制记录 wall-clock，没有同步记录 token、模型调用次数、货币成本和人工升级工作量。

**后果：** 论文阶段无法从不存在的日志恢复完整成本，只能披露缺失。

**系统修复：** 扩展 cell completion schema 并在 runner 中逐调用累计遥测。

**证据：**

- `wall_clock_runtime_seconds=true`
- `additional_cost_metrics=[]`

**对应审稿症状：**

- 审稿人指出成本口径不完整

### RC-LITERATURE-SCOPE-COUPLING · 实验冻结文献与投稿定位文献被混为一层

**根因：** 同一份冻结短名单同时承担实验内证据边界和最终论文的领域定位，导致新近 canonical systems 与 evaluator-bias 文献没有进入投稿版 related work。

**后果：** 冻结新颖性主张可以保持有效，但投稿版领域定位不完整。

**系统修复：** 在 synthesis 前执行 contextual literature refresh，并显式标注 post-freeze 来源。

**证据：**

- `round-1 review corpus contains canonical-literature omission`

**对应审稿症状：**

- 审稿人指出 canonical autonomous-science 与 judge-bias 文献缺失

## 路由规则

- 允许继续运行工程实验：`true`
- 允许发展性外部审稿：`true`
- 允许标记为投稿就绪：`false`
