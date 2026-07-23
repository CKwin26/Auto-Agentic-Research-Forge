# 出版版人工双盲审闭环

适用项目：`stage1_runs/research-agent-evidence-publication-v1`，协议
`stage2-1bc711575552`。这一流程只消费真实人工判断；自动 evaluator、科学家
persona 或论文润色都不能代替两名独立审计员。

## 1. 生成两份独立数据包

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study prepare-publication-manual-audit research-agent-evidence-publication-v1
```

输出目录：

```text
stage2/protected_nli_evaluation/manual-audit/independent-packets/
  auditor_1.json
  auditor_2.json
  packet_manifest.json
  README.txt
```

把两份 JSON 分别交给不同的人。每人必须独立完成 `auditor_attestation`、全部
128 条 `response.verdict` 和非空 `response.rationale`。允许的 verdict 是
`supported`、`unsupported`、`abstain`。不要修改原始 `sample.json`，也不要
查看另一名审计员答案、arm/task 映射、protected evaluator 输出或揭盲文件。

## 2. 冻结两份答案

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study submit-publication-manual-audit research-agent-evidence-publication-v1 `
  --auditor-1 C:\path\to\auditor_1_completed.json `
  --auditor-2 C:\path\to\auditor_2_completed.json
```

命令会 fail closed 地检查：样本哈希、128 条完整性、不可变字段、两名不同
auditor ID、带时区完成时间和三项独立性声明。成功后，两份答案会被哈希冻结，
并生成只含分歧条目的盲态 `manual-audit/adjudication.json`。

## 3. 盲态裁决并终结

如果两名审计员没有分歧，直接运行：

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study finalize-publication-manual-audit research-agent-evidence-publication-v1
```

如果存在分歧，先复制 `adjudication.json` 为新的 completed 文件，填写
`adjudicator_attestation` 以及每条 `response`，再运行：

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study finalize-publication-manual-audit research-agent-evidence-publication-v1 `
  --adjudication C:\path\to\adjudication_completed.json
```

最终统计包括双人一致率、Cohen's kappa、裁决数量、protected evaluator 的
人工假阳性率和假阴性率。预注册停止条件是
`audit_false_positive_rate <= 0.15`：

- 通过：写出 `publication_human_audit_complete_primary_analysis_unlocked`；
- 未通过：写出 `publication_human_audit_complete_primary_analysis_invalid`，论文必须报告验证失败，不能把自动效果当作主结论。

## 4. 完整性检查与论文重算

```powershell
.venv\Scripts\research-forge.exe --home stage1_runs study audit-publication-manual-audit research-agent-evidence-publication-v1
.venv\Scripts\research-forge.exe --home stage1_runs study synthesize-publication research-agent-evidence-publication-v1
.venv\Scripts\research-forge.exe --home stage1_runs study audit-publication-synthesis research-agent-evidence-publication-v1
```

论文生成器会根据真实结果切换为 `HUMAN_AUDIT_COMPLETE` 或
`PRIMARY_ANALYSIS_INVALID`，不会继续保留 `HUMAN_GATE_PENDING`，也不会把
人工门槛完成误写成期刊接受或外部投稿授权。
