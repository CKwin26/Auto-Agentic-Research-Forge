# 第四阶段评估透明度与证据回补

Research Forge 的第四阶段不能只检查论文有没有“写到”某项结果，还要检查支撑论文解释所需的操作证据是否存在。平台现在在正式写作前新增两个持久化步骤：

1. `evaluation_transparency_register`
2. `stage4_evidence_sufficiency_gate`

因此，标准第四阶段 DAG 由 27 步扩展为 29 步。

## 评估透明度登记表

`EvaluationTransparencyRegister` 从冻结的 Research Contract、Run Plan、Result Envelope、Evaluation Record、Repair 和已登记工件中确定性生成。它覆盖：

- 样本或运行单元流转；
- 资格判定和排除原因；
- 弃权数量；
- 评估器阈值及其选择来源；
- 数值精度和相等性容差；
- 第二评估器与阈值敏感性；
- deterministic-only、learned-only、hybrid verifier 消融；
- 修复前后 evidence packet 示例；
- 修复后的前瞻性验证；
- 故障定位基线；
- 信息量或语义保持检查；
- packet schema、evaluator manifest 和 regression fixtures 等发布资产。

每项只能处于：

- `available`
- `missing`
- `planned`
- `unverified`
- `not_applicable`

`available` 必须绑定冻结工件路径。缺失项目必须记录缺失原因和补救动作。Codex 只能转述登记表，不能补造数字、阈值来源、消融结果或人工评价。

## 第四阶段回退第三阶段

`stage4_evidence_sufficiency_gate` 将缺口分成两类。

### 仅披露

不改变当前科学结论、但会限制解释范围的缺口进入论文的 Methods、Results 或 Limitations。它们同时进入 Mandatory Reporting Register 和 Evidence–Claim Map，最终 reporting audit 会检查是否真的披露。

### 必须补数据

影响科学解释资格的缺口产生：

1. `Stage4EvidenceBackfillRequest`
2. `DiagnosticReport`
3. `ScientificSuccessorRequest`
4. 阻塞的 `stage4_evidence_sufficiency_gate`
5. `redirect_phase=experiment`

Study 随后回到第三阶段。原 Run、Evaluation、Verdict 和论文版本全部保留；负责人批准新的 Research Contract 后，平台才能执行 successor run。

默认会触发第三阶段回补的缺口包括：

- 排除记录无法按原因完整核算；
- 已使用阈值，但没有冻结阈值选择或校准来源；
- 已发生修复，但没有新的前瞻 successor 验证。

项目可以在 `scientific_validity_contract.stage4_backfill_required_categories` 中增加要求，例如：

```json
{
  "stage4_backfill_required_categories": [
    "eligibility",
    "threshold_provenance",
    "prospective_validation",
    "evaluator_sensitivity",
    "verifier_ablation",
    "semantic_preservation"
  ]
}
```

这些要求必须在阶段二冻结，不能在看到阶段三结果后为了得到更好叙事而临时降低。

## 回补边界

- 排版、措辞和标题问题留在第四阶段修复。
- 缺少披露但原始数据充分时，优先创建分析型 successor。
- 需要新样本、新评估器、新消融或新人工标注时，创建实验型 successor。
- 第四阶段不得修改历史 Verdict。
- 回补完成后生成新的 Stage 3 Completion，再启动新的 Stage 4 workflow revision。

## 代码位置

- `research_forge/paper_evaluation_transparency.py`
- `research_forge/stage_four.py`
- `research_forge/paper_reporting_compliance.py`
- `tests/test_paper_evaluation_transparency.py`
- `tests/test_stage_four_publication_control.py`
