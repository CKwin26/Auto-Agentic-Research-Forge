# Stage 2 人工双审操作流程

本流程适用于协议 `stage2-22e124e44294`。人工审计完成前，不得把代理评估器的处理效应当作最终主分析结论。

## 当前冻结输入

- 样本：`C:\Users\austa\.research-forge-blind\stage2-22e124e44294\manual-audit\sample.json`
- SHA-256：`0ef6aff20a330b8e247554971f58999c85290726a949005becc8feffbb30ef6e`
- 总数：48 条；全部 14 条 evaluator-unsupported claim 加 34 条其他 claim。
- auditor 1：`manual-audit\independent-packets\auditor_1.json`
- auditor 2：`manual-audit\independent-packets\auditor_2.json`

旧 SHA-256 为 `3142881d...62f62` 的两个填写包已作废，不得分发。

## 两名审计员分别填写

两人必须是不同的人，并各自完成全部 48 条。每人只能修改：

- `auditor_attestation.auditor_id`：可使用稳定的匿名代号；两人不能相同；
- `auditor_attestation.completed_at`：带时区的 ISO-8601 时间；
- 三个独立性声明：全部填写 `true`；
- 每条 `response.verdict`：`supported`、`unsupported` 或 `abstain`；
- 每条 `response.rationale`：不能为空。

不得修改 audit ID、claim、evidence、顺序或其他 metadata。不得查看另一位审计员答案、arm/task 映射、代理评估 verdict、解盲文件或处理效应汇总。

## 收回并冻结两份答案

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study submit-manual-audit research-agent-evidence-v2 `
  --auditor-1 C:\path\to\auditor_1_completed.json `
  --auditor-2 C:\path\to\auditor_2_completed.json
```

该命令会 fail-closed 地检查：

- 样本哈希与 packet source hash；
- 两名 auditor ID 不同且独立性声明完整；
- 48 条全部作答且 rationale 非空；
- 除 attestation 与 response 外无任何改动；
- item 数量、ID 和顺序完全一致。

验证成功后，两份答案会冻结到 `stage2/manual_audit/`，并生成只包含分歧项的盲态 `manual-audit/adjudication.json`。先复制该模板为新的 completed 文件，再填写；不要原地覆盖模板，因为模板哈希需要保留。

## 分歧裁决

裁决者可以看到两名审计员对分歧项的 verdict/rationale，但仍不能查看 evaluator verdict、arm/task、解盲映射或处理效应。裁决者填写 `adjudicator_attestation` 以及每条分歧的 `response`。

若两名审计员没有任何分歧，finalize 命令不需要 `--adjudication`。

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study finalize-manual-audit research-agent-evidence-v2 `
  --adjudication C:\path\to\adjudication_completed.json
```

## 最终停止门

finalize 只在裁决完成后才重建 evaluator verdict 映射并计算：

- 两名审计员的一致率和 Cohen's kappa；
- evaluator-unsupported 的人工假阳性率；
- evaluator-non-unsupported 的人工假阴性率；
- 预注册 `audit_false_positive_rate <= 0.15` 是否通过。

当 evaluator 判为 unsupported、人工最终 verdict 不是 unsupported（包括 abstain）时，保守计作一次假阳性。若假阳性率大于 0.15，系统写出 `manual_audit_complete_primary_analysis_invalid`，不会解锁主分析；否则才生成包含主效应的 `stage2/final_analysis.json`。

最后运行：

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study audit-manual-audit research-agent-evidence-v2
```
