# Stage 2 受保护评估阶段报告（2026-07-18）

## 结论状态

- 协议：`stage2-22e124e44294`，revision 4。
- 基线臂 9/9、处理臂 9/9 均已完成并通过完整性审计。
- 18/18 份输出已按随机 blind ID 完成受保护语义评估；所有评估结束后才生成解盲映射。
- 完整测试套件：58/58 通过（含人工双审收口流程的 4 项新增测试）。
- 当前分析状态：`protected_evaluator_complete_manual_audit_pending`。
- 下面的处理效应是代理评估器结果，尚不能作为最终主分析结论。预注册的 48 条 claim、两名独立人工审计及分歧裁决完成前，`primary_analysis_interpretable=false`。

## 代理评估结果

| 指标 | 基线臂 | 处理臂 | 处理臂变化 |
|---|---:|---:|---:|
| unsupported claim rate | 30.56%（11/36） | 8.33%（3/36） | -22.22 个百分点；相对下降 72.73% |
| experiment detail error rate | 33.33% | 5.56% | -27.78 个百分点 |
| citation correctness | 66.67% | 88.89% | +22.22 个百分点 |
| evidence coverage | 100% | 100% | 0 |
| claim retention | 100%（36/36） | 100%（36/36） | 0 |
| mean task-native score | 0.60057 | 0.62213 | +0.02156 |
| total wall-clock | 2721.13 s | 4514.38 s | +65.90% |

配对的 unsupported-claim-rate 效应定义为 `treatment - baseline`，负值有利于处理臂：

- 9 对 cell 的平均效应：-0.22222。
- 分层 bootstrap：10,000 次，95% CI `[-0.41667, -0.08333]`，中位数 -0.22222。
- 配对 Cohen's dz：-0.84327。
- 3/3 个任务的任务级平均效应均低于 0：SICK Accuracy -0.25、SICK Spearman -0.25、SuperGLUE WSC Accuracy -0.16667。

## 任务质量与速度解释

- SICK Accuracy 的处理臂任务分数平均提高 0.06468；SICK Spearman 和 WSC 的平均差值均为 0。因此当前没有观察到任务质量下降。
- 冻结实现中的 `task_native_score_within_absolute_0_02` 为 `false`，原因是它对正向提升也使用绝对值阈值；这是对称检查的结果，不是性能退化。
- 处理臂总耗时比基线高 65.90%。因此当前证据支持“减少 unsupported claims”，但不支持“保持吞吐不变”。速度是下一轮需要优化或单独消融的主要代价。

## 处理臂门控行为

- 9 个处理臂 cell 共生成 36 条初始 claim，最终保留 36 条，没有 claim 在唯一一次复检后被删除。
- 多数首轮失败来自把“在当前六篇材料中未看到”写成“现有领域不存在”的过强新颖性否定陈述；门控将其收窄到材料可直接支持的范围。
- t06 还拦截了一条把无证据目标分数混入已验证基线事实的 claim。
- 所有实验控制器结果、任务分数、初始模型输出和 gate verdict 均保留在 cell 目录中。

## 透明实现修正

处理臂执行期间发生两次确定性、可审计的编排修正：

1. v1 → v2：实验 claim 的模型判定偶尔填入无关的 literature `supporting_source_ids`。修正仅对实验 claim 清空该辅助字段，不改 verdict、rationale、claim 或证据。
2. v2 → v3：finalizer 把运行记录顶层的精确 `improvement` 值重复放进 `metric_values`，触发机械结构误报。修正仅在该值与链接记录完全一致时移除重复键。

两次修正均写入 `stage2/orchestration_amendments.json`；旧 manifest、失败现场和修正前文件哈希均已归档。控制器重跑数和 finalizer 重跑数均为 0。

## 人工双审包

冻结样本：

- `C:\Users\austa\.research-forge-blind\stage2-22e124e44294\manual-audit\sample.json`
- SHA-256：`0ef6aff20a330b8e247554971f58999c85290726a949005becc8feffbb30ef6e`
- 48 条 claim；每个 task × arm 分层抽取 8 条。
- 因预注册的 unsupported 配额 24 条无法满足，样本按停止规则纳入全部 14 条 evaluator-unsupported claim，并以 34 条其他 claim 补足 48 条。
- arm、task 和代理评估器 verdict 均已盲化。

独立填写包：

- `manual-audit/independent-packets/auditor_1.json`
- `manual-audit/independent-packets/auditor_2.json`
- `manual-audit/independent-packets/packet_manifest.json`
- `manual-audit/independent-packets/README.txt`

两名审计员应各自完成身份/独立性 attestation 及全部 48 条，只修改 `auditor_attestation`、`response.verdict` 与 `response.rationale`，不得查看另一人的答案、解盲映射或代理评估结果。两份均交回后，才生成盲态分歧裁决包，并计算代理评估器的人工假阳性率；预注册阈值为 0.15。人工最终 verdict 为 `abstain` 时，不视为确认 evaluator 的 unsupported 判定，因此在该停止门中保守计作假阳性。

## 人工样本修正记录

第一次生成的样本遵循“每个 task × arm 最多四条 evaluator-unsupported”的机械配额，在 SICK Spearman baseline 组有五条可用 unsupported 时漏掉了一条。预注册停止条件同时要求：总计 24 条 unsupported 配额无法满足时，必须审计所有可用的 evaluator-unsupported claim。因此在人类审计开始前执行 `manual-audit-amendment-01`：

- 旧样本 SHA-256 `3142881d...62f62` 已按哈希归档；
- 新样本保持 48 条与每组 8 条不变；
- 从确定性顺序中移除一条 non-unsupported、加入遗漏的一条 unsupported；
- evaluator-unsupported 覆盖从 13/14 修正为 14/14；
- human audits started = 0，arm/task/evaluator verdict 仍保持盲化；
- amendment、旧文件、新旧哈希及 added/removed audit ID 均已落盘。

收回人工结果后的固定命令为：

```powershell
research-forge --home stage1_runs study submit-manual-audit research-agent-evidence-v2 --auditor-1 <auditor_1_completed.json> --auditor-2 <auditor_2_completed.json>
research-forge --home stage1_runs study finalize-manual-audit research-agent-evidence-v2 --adjudication <adjudication_completed.json>
research-forge --home stage1_runs study audit-manual-audit research-agent-evidence-v2
```

## 下一道门

当前无需再跑模型实验。下一步需要两名真正独立的人工审计员完成两个填写包。完成前，自动评估结果只能报告为暂定结果，不能宣称主假设已得到最终验证。

## 后续操作决定

2026-07-18，操作者明确决定现阶段暂缓真人验证。该决定不修改预注册协议，也不把同模型科学家人格审稿视为真人替代；两份人工填写包继续保持未填写状态，`primary_analysis_interpretable=false`。恢复真人验证必须由操作者另行明确指示。结构化决定记录见 `stage2/operator_decisions.json`。
