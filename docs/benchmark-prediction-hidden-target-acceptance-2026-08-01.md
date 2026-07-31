# benchmark_prediction_v1 真实隐藏目标验收

日期：2026-08-01

## 结论

`benchmark_prediction_v1` 已在真实外部任务 OpenML Task 39（Sonar v1）上完成隐藏目标验收。候选容器只接收 187 个带标签训练样本和 21 个不带标签测试样本；测试标签只进入随后启动的独立评估容器。两个容器均断网、只读根文件系统、非 root 运行，并使用彼此分离的只读输入挂载。

该结果把 `benchmark_prediction_v1` 的“候选提交与隐藏目标隔离”能力提升为窄范围 C4 真实案例证据。执行者仍为同一运营方，因此不构成 C5 外部独立验证。

## 数据与边界

- OpenML Task：39
- Dataset：Sonar，dataset 40，version 1
- OpenML MD5：`3ab630fbbfe25ab48b9bb47ce5759203`
- 冻结材料化数据 SHA-256：`3765885e439f4576574f6de266b9179e807601eae4f671ca4cea3d6190b3768e`
- 训练样本：187
- 隐藏测试样本：21
- 隐藏目标 SHA-256：`de1586e5aba2001cb03aeef357f95b3016384da06469ffd69fc62379d9375c3d`
- 候选输入中存在目标文件：否

## 结果

| Arm | 冻结算法 | Accuracy | Submission SHA-256 |
|---|---|---:|---|
| baseline | majority class | 0.5714285714 | `20e6fb56ed159f71101cb3214775f3ff59fb677cde5470ba8b6be52cb98856f8` |
| treatment | nearest centroid | 0.9047619048 | `e526c6f189fadf52a5a1adfe3edc3bab13e9ed3c0f776351bb428b38bd358184` |

- Beneficial effect：`+0.3333333333`
- 冻结阈值：`0.0`
- Verdict：`supported`
- 独立评估容器与冻结参考评估器：两个 arm 均完全一致
- 报告：验收运行生成的 `acceptance-report.json`（运行产物不提交到仓库）
- 报告 SHA-256：`4dac07e63beb960f840f753a5c90974df9c627882afb626379fb251a3a90ca36`

## 平台落点

- `research_forge/benchmarks/hidden_prediction.py`
- `scripts/run_hidden_prediction_acceptance.py`
- `tests/test_hidden_prediction_acceptance.py`
- `research_forge/profiles/benchmark_prediction.py`

## 允许声称与边界

允许声称：Research Forge 能在一个真实 OpenML 分类任务中，把候选提交与隐藏测试标签隔离，并由独立评估器重新计算主指标。

不能声称：该结果不证明所有 benchmark、回归任务或外部排行榜均受支持，也不是由外部组织执行的 C5 独立复现。科学结论只适用于冻结的 Task 39 split、两种确定性算法和已登记指标。
