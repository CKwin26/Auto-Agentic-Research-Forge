# Codex 通宵运行 Prompt：Research Forge 正式版 publication 闭环

将下面整段复制到一个新的 Codex 任务中。它授权的是本地、可逆、证据可审计的工作；不授权投稿、发邮件、推送 GitHub、付费 API 调用或伪造人工审核。

---

你现在是 Research Forge 的通宵 publication closure owner。请创建一个持续目标（goal），然后自主工作到下一次 Asia/Bangkok 时区上午 08:00，或者更早满足成功条件。不要只给计划、建议或代码草案；要检查真实工作区、修改真实系统、运行真实测试和正式实验，并留下可复核产物。一个 turn 结束不代表任务完成；只要安全且仍能推进，就继续执行。

## 0. 固定范围

- 工作区：当前 Research Forge 仓库根目录
- 当前历史项目：`stage1_runs\research-agent-evidence-v2`
- 产品目标：端到端生成可提交学术论文的 Research Forge 正式版。
- 研究目标：在不削弱任何科学门槛的前提下，使固定 venue 合同下的确定性 `publication_readiness >= 0.60`，通过该 venue 自身的质量线，并清除全部自动化可解决的 critical/high scientific blockers。
- 当前选择的 venue 和 track 默认保持冻结；除非现有合同文件本身损坏或与用户明确决定矛盾，不得换 venue 来刷分。
- `0.60` 是内部、确定性的投稿准备度阈值，不是期刊真实录取概率。真实 acceptance estimate 必须单独报告，不得把它写成 60%，不得承诺录取。
- 正常路径只有 `publication`。禁止将 `pilot`、历史 3-task x 3-seed 结果或 provisional 结果升级、重命名或包装成正式证据。
- 人工验证目前延期而非取消。不得招募、代填或伪造两位真人审计；不得把 scientist persona、Codex 自审或同模型复核说成人类验证。只要真人门仍未完成，保持 `primary_analysis_interpretable=false`，并在最终状态中明确写 `HUMAN_GATE_PENDING`，不能谎称 `publication_submission_ready=true`。

## 1. 你拥有的本地权限

你可以自主执行以下工作，不必逐项询问：

- 阅读整个工作区、历史运行、审计报告、测试和文档；
- 修改 Research Forge 源代码、Prompt、schema、确定性控制器、测试和文档；
- 新建带时间戳的夜间运行目录、正式 prospective protocol 和实验产物；
- 运行 Python 测试、CLI、Docker、Codex backend、LaTeX/PDF 构建和只读网络检索；
- 为独立检查使用 Codex 子代理或不同 scientist personas，但最终判定必须由确定性门控聚合；同模型 persona 只能作为补充证据；
- 对互不写同一文件、不会破坏实验独立性的任务做有界并行。

你没有以下权限：

- 投稿、发送邮件、联系审稿人、招募人工审计员；
- push GitHub、创建公开仓库或上传未公开论文；
- 调用付费第三方 API、购买服务或改变账户配置；
- 删除、覆盖、重写或美化历史 frozen protocol、实验结果、失败账本；
- 降低阈值、权重、hard minimum、样本规模、独立验证要求或 venue 质量线；
- 在看过正式 holdout 结果后继续针对该结果调参，却仍把同一 holdout 称作无偏正式证据。

若某个动作需要以上新权限，先完成所有不需要新权限的工作，再把它作为唯一明确 blocker 报告。

## 2. 今晚的成功条件

只有同时满足以下条件，才可标记 `AUTOMATED_PUBLICATION_GATE_PASS`：

1. 正式运行的 protocol 明确为 `study_intent=publication`，并绑定当前 frozen `publication_experiment_contract_id`；
2. pre-experiment publication gate 在任何正式实验开始前已经通过；
3. 使用 prospectively frozen、shared-artifact counterfactual，至少 8 个异质任务 x 每任务 5 个 seeds；
4. evaluator、calibration set、construct metrics 和 stopping rules 在查看 treatment 结果前冻结并 hash 绑定；
5. 至少覆盖 claim retention/deletion、semantic change type、informativeness/usefulness，以及 unsupported claim 与 task-native performance；
6. 独立校准/评估链真实存在，不能由生成同一产物的 agent 自证；
7. 所有正式 cells 在受控 Docker 环境真实运行，产物、stdout/stderr、运行记录、hash、失败和重试均可审计；
8. 正式分析、claim registry、英文 LaTeX、PDF、参考文献、补充材料和匿名发布包均通过各自审计；
9. 固定合同下 `publication_readiness >= 0.60`，所有 readiness 维度达到各自 hard minimum，critical/high scientific blockers 为 0；
10. 同一冻结 venue 的最终 recommendation quality score 达到合同质量线，当前 RIPP 合同若仍有效则必须 `>= 0.68`；
11. 完整测试套件通过，且至少完成一次从干净 prospective run 开始的正式版端到端 dogfood；
12. 最终报告清楚区分：pipeline complete、automated publication gate、human validation、submission ready、estimated acceptance probability。

若分数达到 0.60 但仍有 hard blocker、维度低于 hard minimum、venue quality 未达标、协议非 prospective、或正式数据被开发循环污染，均不算通过。

由于人工验证已延期，今晚通常只能达到 `AUTOMATED_PUBLICATION_GATE_PASS + HUMAN_GATE_PENDING`。只有真实人工门以后完成，系统才可以宣称完整 `publication_submission_ready`。

## 3. 启动时必须先做

1. 读取适用的 `AGENTS.md`、项目 README、四阶段闭环、publication readiness 合同、现有 failure ledger、design repair、venue contract、protocol、state、completion certificate 和最新审稿报告。
2. 不信任聊天摘要；以磁盘真实文件和命令结果为准。
3. 运行并记录：
   - `.\.venv\Scripts\python.exe main.py doctor`
   - 全量 `pytest`
   - 当前项目 `study audit`、`study audit-publication-design`、`study root-cause-preflight --target publication --persist`
   - 当前 manuscript depth、synthesis、completion、venue readiness 审计
4. 创建 `overnight_runs\<UTC时间戳>\`，至少维护：
   - `state.json`：当前 phase、attempt、正式/开发数据边界、开始时间、截止时间；
   - `commands.jsonl`：命令、开始/结束时间、退出码、输出文件；
   - `failure_ledger.jsonl`：稳定 failure fingerprint 与每次出现；
   - `repair_ledger.jsonl`：根因、系统改动、测试、受影响产物和重跑范围；
   - `score_history.jsonl`：每次 readiness 分数、合同 hash、manuscript hash、protocol id；
   - `final_report.md`。
5. 给出一次不超过 12 行的初始状态更新，然后立即继续执行，不等待非必要确认。

## 4. 两层测试纪律，防止刷 benchmark

### A. 开发/回归层

可以反复运行单元测试、集成测试、smoke tasks、synthetic fixtures 和明确标记为 development 的实验。它们用于修代码、控制器和 schema，不用于最终科学结论。

### B. 正式 prospective 层

正式 task set、seeds、evaluator、calibration contract、construct metrics、analysis plan 和 stopping rules 必须先冻结再运行。正式 holdout 仅在系统通过开发层和 pre-experiment gate 后启动。

一旦查看正式 holdout 的 arm-level 结果：

- 不得再根据结果修改 evaluator、评分权重、任务选择、排除规则或主分析；
- 若发现实现 bug，必须将受影响 run 明确标为 invalid，修复系统，创建新 protocol revision，并重跑所有受影响 cells；
- 若根据正式结果产生新假设，该假设只能进入下一轮 prospective study；本轮结果转为 exploratory，不能继续称为 confirmatory；
- 禁止多次尝试后只报告最好的一次。

## 5. 每次失败都执行“根因 -> 系统修复 -> 回归 -> 重跑”

任何 gate、测试、实验、审稿或 readiness 失败，都必须生成一个结构化 failure record：

```json
{
  "failure_id": "稳定指纹",
  "observed_at_gate": "失败发生的门",
  "symptom": "可复现症状",
  "evidence_paths": ["日志或产物"],
  "failure_class": "environment|transient|project_data|implementation|scientific_design|measurement|reporting",
  "root_cause": "最小充分根因",
  "earliest_preventable_stage": "discovery|protocol|experimentation|synthesis",
  "system_invariant": "以后必须始终成立的规则",
  "implementation_points": ["代码/schema/prompt/CLI"],
  "regression_tests": ["先失败后通过的测试"],
  "artifact_invalidation": ["必须作废/重跑的产物"],
  "status": "open|fixed|verified|external_blocked"
}
```

处理顺序：

1. 用最小复现确认症状，禁止凭感觉改；
2. 找到最早能防止该错误的阶段，而不是只修最终论文文字；
3. 如果系统本可自动预防，先增加一个会失败的回归测试或确定性审计；
4. 优先硬编码 schema、状态机、hash binding、统计检查和 deterministic gate；只有语义判断才交给 agent；
5. 实现最小系统级修复；不得为当前样本写特殊分支；
6. 运行 targeted tests，再运行相关 stage gate；
7. 修复通过后运行全量 tests；
8. 根据 artifact invalidation 精确重跑，不得让旧产物继承新系统的通过状态；
9. 相同 fingerprint 再出现时，视为上次系统修复失败，审计实现点和 regression test，而不是再写一份建议。

科学设计失败必须回到 protocol 前修。测量同源、反事实未隔离、构念覆盖不足、证据规模不足、novelty 过期、人工校准缺失等问题，不能靠增加 Discussion、润色摘要或修改评分解释解决。

## 6. 正式版优先修复顺序

按阻断链的最上游工作，不按“最容易涨分”工作：

1. publication protocol builder：解除旧 3x3 pilot 写死，支持合同驱动的至少 8x5 正式矩阵；
2. prospective venue-bound contract 与 protocol/hash 绑定；
3. shared-artifact randomized branching，确保 baseline/treatment 从同一冻结产物分支；
4. evaluator independence 与 calibration contract，隔离候选生成、判分和聚合；
5. 信息保留构念与 telemetry schema；
6. contextual novelty refresh 和来源验证；
7. 受控 Docker 正式运行、失败恢复、重复检测和不可变证据账本；
8. 盲态统计分析、效应量、不确定性、负结果和限制披露；
9. claim-to-evidence registry、引用存在性/语义一致性和 figure/table trace；
10. venue 体裁、深度、LaTeX、PDF、匿名补充材料和最终 package；
11. 固定 venue 下重新计算 readiness 与 quality score。

不要因为写作分容易提高就先堆文字；当前主要瓶颈是方法、独立测量、证据广度和成熟度。

## 7. 运行策略

- 始终使用当前 Codex 登录/backend；不要为了方便改成直接 OpenAI API 调用。
- 长任务应持续运行并定期轮询，不要用长时间无输出的 sleep。至少每 45 分钟给一条简短状态更新：当前 gate、已修根因、真实测试结果、下一步。
- Docker/实验可安全恢复时使用幂等 resume；每个 cell 最多按冻结 retry policy 重试。不得静默丢弃失败 cell。
- 优先复用真实已有来源、任务、模型资产和模板；不足时可以添加通用、许可证兼容、可复现的新任务，但必须记录选择标准，不能按预期有利结果挑任务。
- 不要修改 frozen 历史目录。新正式实验使用新的 run/protocol id；旧项目只读引用或显式 supersede，保留完整 lineage。
- 不要用 persona review 代替独立 empirical measurement。Nuwa/scientist panel 可用于补充审稿，但其意见不能直接改变数值结果或清除 human gate。
- 修改源代码必须用小步、可审计 patch；保留用户已有无关改动。
- 每次系统改动后检查是否影响已有 frozen snapshot；controller drift 必须记录，不得把它解释成历史结果造假，也不得忽略。

## 8. 截止与停止规则

不要因为“工作很多”“一个测试失败”“需要再跑一次”而停止。只在以下情况结束：

### PASS

满足第 2 节全部自动化成功条件，写出 `AUTOMATED_PUBLICATION_GATE_PASS`；若真人门仍延期，同时写 `HUMAN_GATE_PENDING`。

### EXTERNAL_BLOCKED

只有真实需要用户新授权、人工审计、不可获得的私有数据/付费资源、外部服务恢复时才可用。结束前必须已经完成所有不依赖该资源的修复与验证，并提供一个最小、明确、可执行的解阻请求。

### DEADLINE_REACHED

到下一次 Asia/Bangkok 08:00 仍未通过时停止启动新长实验，但完成当前原子写入、保存可恢复状态、验证工作区未损坏。不得因为截止时间伪造 PASS。

### SCIENTIFIC_NONCONVERGENCE

只有同一根因经过 3 次不同的系统修复仍以同一 fingerprint 复现，或证据表明当前假设在冻结设计下不成立时使用。负结果不是系统失败；如实保留并调整论文结论，不得改实验直到变正。

## 9. 最终交付

最终只给一个紧凑但证据充分的 dashboard，并链接真实文件：

- 终态：上述四种之一；
- publication readiness：当前值 / 0.60，合同 ID/hash；
- venue quality：当前值 / 冻结质量线；
- acceptance estimate：单独列出并注明是否校准；
- hard blockers：剩余数量和 ID；
- 正式 protocol：intent、任务数、seeds、cells、独立 evaluator/calibration 状态；
- 实验：计划/完成/有效/作废/失败数；
- 系统修复：按根因列出代码、测试和防复发硬门；
- 测试：targeted/full/E2E 的真实命令与结果；
- 论文产物：LaTeX、PDF、claim registry、supplement、submission package；
- 人工验证：必须明确 `completed` 或 `HUMAN_GATE_PENDING`；
- 下一步：最多 3 项，只列真正仍需做的事。

绝对不要把“预计修复后可达 81%”、scenario projection、写作分、persona 同意票或历史 pilot 成绩计入当前通过分数。只承认真实存在、hash 绑定、通过审计的产物。

现在开始：先创建持续 goal，读取真实状态，建立 overnight ledger，跑基线审计，然后直接进入第一个最上游系统修复。不要等待我回复。

---
