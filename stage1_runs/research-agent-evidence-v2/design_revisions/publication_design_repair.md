# Research Forge 科学失败 → 系统设计修复单

- 失败指纹：`pubfail-3786930890c17d72`
- 科学硬门失败：**是**
- 历史冻结产物保持不可变：**是**

科学硬门失败必须修订最早可预防阶段的系统合同和回归测试，不能只润色当前论文。

> 同一 failure fingerprint 再次出现时，系统必须审计上次修复的执行点和回归测试；不得把同一建议换一种文字后再次关闭问题。

## repair-5cdb0f34200c

- 触发：`RC-CONSTRUCT-UNDERCOVERAGE, READINESS-CONSTRUCT-COVERAGE-BELOW-HARD-MINIMUM`
- 最早修复阶段：`stage_1_discovery`
- 执行门：`stage_2_protocol`
- 状态：`active_for_future_runs`
- 系统缺口：计划批准时只冻结支持性代理指标，没有冻结信息性、删除/保留和语义变化。
- 新规则：质量/可靠性研究若缺少支持性与信息性双轨构念，计划或协议不得进入 publication 路线。
- 生效范围：新建实验和基于本项目启动的下一版协议；历史冻结协议与结果保持不可变。
- 实现点：ResearchPlanDraft.metrics；root_cause_preflight.analyze_root_causes；study.freeze_stage2_protocol
- 回归测试：仅 unsupported_claim_rate 必须失败；加入 semantic_change 与 informativeness 指标后构念检查通过

## repair-4bd7ed50f4ed

- 触发：`RC-COUNTERFACTUAL-NONISOLATION, READINESS-SCIENTIFIC-IDENTIFICATION-BELOW-HARD-MINIMUM`
- 最早修复阶段：`stage_2_protocol`
- 执行门：`stage_2_protocol`
- 状态：`active_for_future_runs`
- 系统缺口：协议把独立随机运行误当成配对反事实，没有冻结同一上游 artifact 的分支点与顺序随机化。
- 新规则：publication 目标的下游干预比较必须声明内容寻址的共享 artifact 分支；否则冻结失败。
- 生效范围：新建实验和基于本项目启动的下一版协议；历史冻结协议与结果保持不可变。
- 实现点：study_models.Stage2Protocol；root_cause_preflight.analyze_root_causes；study.freeze_stage2_protocol
- 回归测试：baseline-then-treatment 整块顺序必须失败；共享 artifact 加随机分支顺序必须通过识别检查

## repair-052787ae61f0

- 触发：`RC-MATURITY-TARGET-MISMATCH`
- 最早修复阶段：`stage_4_synthesis`
- 执行门：`stage_4_synthesis`
- 状态：`deferred_external_validation`
- 系统缺口：流程完成状态曾可能被误解为 publication 审批资格。
- 新规则：primary_analysis_interpretable=false 时只能进入 developmental review；投稿证书必须拒发。
- 生效范围：新建实验和基于本项目启动的下一版协议；历史冻结协议与结果保持不可变。
- 实现点：root_cause_preflight；publication_readiness；review routing
- 回归测试：延期人审时 publication_submission_ready 必须为 false；persona panel 不得改变人工验证字段

## repair-973cda589e37

- 触发：`RC-MEASUREMENT-CIRCULARITY, READINESS-INDEPENDENT-VALIDATION-BELOW-HARD-MINIMUM`
- 最早修复阶段：`stage_2_protocol`
- 执行门：`stage_2_protocol`
- 状态：`active_for_future_runs`
- 系统缺口：协议冻结允许生成器、干预器和主评估器同源，却没有独立校准合同。
- 新规则：publication 目标的协议只要仍存在同源测量且无冻结校准，就必须在执行前失败。
- 生效范围：新建实验和基于本项目启动的下一版协议；历史冻结协议与结果保持不可变。
- 实现点：root_cause_preflight.analyze_root_causes；study.freeze_stage2_protocol
- 回归测试：同源且无校准必须阻断下一版 Stage 2 freeze；跨家族评估器也必须提供校准证据而非仅换模型名

## repair-a7487fc44a87

- 触发：`READINESS-EVIDENCE-BREADTH-BELOW-HARD-MINIMUM`
- 最早修复阶段：`stage_2_protocol`
- 执行门：`stage_2_protocol`
- 状态：`active_for_future_runs`
- 系统缺口：计划允许少量任务和种子完成 pipeline，却没有按投稿目标反推证据广度下限。
- 新规则：publication 修复版协议在冻结前至少声明 8 个异质任务和每任务 5 个种子，或记录有依据的等效功效方案。
- 生效范围：新建实验和基于本项目启动的下一版协议；历史冻结协议与结果保持不可变。
- 实现点：publication target contract；study.freeze_stage2_protocol
- 回归测试：3 tasks x 3 seeds 在激活修复单后必须失败；8 tasks x 5 seeds 应满足默认广度合同

## repair-c83614b6539a

- 触发：`READINESS-NOVELTY-POSITIONING-BELOW-HARD-MINIMUM`
- 最早修复阶段：`stage_1_discovery`
- 执行门：`stage_2_protocol`
- 状态：`active_for_future_runs`
- 系统缺口：冻结实验文献包曾同时承担投稿定位，缺少投稿前独立 contextual refresh。
- 新规则：进入修复版 Stage 2 前必须存在独立的 novelty_refresh.json，绑定检索式、筛选账本和贡献矩阵。
- 生效范围：新建实验和基于本项目启动的下一版协议；历史冻结协议与结果保持不可变。
- 实现点：Stage 1 literature refresh；study.freeze_stage2_protocol
- 回归测试：激活 novelty 修复且缺 refresh artifact 时冻结失败；有效 refresh artifact 必须绑定来源与生成时间

