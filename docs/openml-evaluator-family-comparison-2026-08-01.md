# OpenML Task 39 双评估器家族验收

日期：2026-08-01

## 结论

Research Forge 在真实 OpenML Task 39（Sonar v1）隐藏标签任务上，使用两个
实现来源不同的评估器家族重新计算同一冻结 Accuracy 指标：

- 主评估器：隔离容器中的独立 CSV Accuracy 程序，家族
  `isolated-csv-accuracy`；
- 第二评估器：Profile 内冻结的通用指标重算实现，家族
  `profile-metric-recomputation`。

两个评估器读取相同的冻结 submission 和隐藏 target，但没有共享指标实现。
这验证的是测量实现稳健性，不是外部研究员复现。

## 真实运行结果

| 项目 | 结果 |
|---|---:|
| OpenML task | 39 |
| baseline accuracy | 0.5714285714 |
| treatment accuracy | 0.9047619048 |
| treatment effect | +0.3333333333 |
| scientific verdict | supported |
| 双家族共同逐行项目 | 42 |
| 逐行分歧 | 0 |
| mean absolute difference | 0.0 |
| max absolute difference | 0.0 |
| 方向结论一致 | true |
| verdict stable | true |
| 需要人工裁决 | false |

报告 ID：`evaluator-disagreement-93ce02ca3c38cf9e`。

完整验收报告 SHA-256：
`fb7fefc97a942547608b03a1fabbec564198461784c205e1db636d2a864979a3`。
运行产物保存在工作区之外，不提交仓库。

## 能力边界

允许声明：在 OpenML Task 39 的冻结隐藏标签案例中，两个不同实现家族对
42 个 arm-sample 项逐行一致，并产生相同的方向结论和稳定 Verdict。

不能声明：这一结果不证明所有 OpenML 任务、所有指标或所有评估器选择均
稳健；也不构成外部独立研究员复现。Research Forge v1 不要求外部独立
研究员，后者不是发布门。
