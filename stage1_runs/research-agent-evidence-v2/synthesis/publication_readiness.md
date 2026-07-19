# Research Forge 投稿就绪硬门

- 目标：**Research Integrity and Peer Review**（`research-integrity-and-peer-review`）
- 内部投稿就绪度：**50.5%** / 门槛 **60.0%**
- 投稿就绪：**阻断**
- 科学硬门：**失败**
- 当前录用概率估计：**5.4%** [2.3%, 11.0%]
- 概率校准：`uncalibrated_heuristic_v1`

> 60% 是 Research Forge 可控制的投稿就绪门，不是期刊录用率。系统禁止为了达到 60% 而修改录用概率模型。

## 九维就绪度

| 维度 | 得分 | 目标 | 硬下限 | 权重 | 状态 | 回流阶段 |
|---|---:|---:|---:|---:|---|---|
| 目标 venue 范围匹配 | 88% | 75% | 60% | 10% | `meets_target` | `stage_1_discovery` |
| 方法与因果识别 | 18% | 72% | 55% | 16% | `hard_fail` | `stage_2_protocol` |
| 独立测量与校准 | 35% | 75% | 60% | 15% | `hard_fail` | `stage_2_protocol` |
| 证据广度与稳定性 | 53% | 70% | 55% | 11% | `hard_fail` | `stage_3_experimentation` |
| 科学构念覆盖 | 30% | 72% | 55% | 10% | `hard_fail` | `stage_2_protocol` |
| 新颖性与领域定位 | 44% | 65% | 50% | 11% | `hard_fail` | `stage_1_discovery` |
| 复现与外部材料 | 68% | 78% | 60% | 9% | `below_target` | `stage_3_experimentation` |
| 报告完整性与诚信 | 99% | 85% | 75% | 8% | `meets_target` | `stage_4_synthesis` |
| 投稿格式与周期合规 | 60% | 82% | 55% | 10% | `below_target` | `stage_4_synthesis` |

## 当前科学硬阻塞

- **RC-COUNTERFACTUAL-NONISOLATION**（stage_2_protocol）：原始两组没有隔离同一实验产物上的反事实效应。 需要：做前瞻性的同产物分支实验，或采用随机化、交错执行的对照设计。
- **RC-MATURITY-TARGET-MISMATCH**（stage_4_synthesis）：当前证据仍是临时性 pilot，而投稿需要可解释的验证结果。 需要：在进入投稿路由前完成预注册的验证门。
- **RC-MEASUREMENT-CIRCULARITY**（stage_2_protocol）：生成器、干预和主评估器没有完成独立校准。 需要：完成盲态人工校准或真正跨模型家族的校准，并报告一致性和错误结构。
- **READINESS-CONSTRUCT-COVERAGE-BELOW-HARD-MINIMUM**（stage_2_protocol）：科学构念覆盖得分 0.30，低于硬下限 0.55。 需要：同时测量支持性、保留/删除、语义变化与科学信息性，禁止单指标偷换构念。
- **READINESS-EVIDENCE-BREADTH-BELOW-HARD-MINIMUM**（stage_3_experimentation）：证据广度与稳定性得分 0.53，低于硬下限 0.55。 需要：至少覆盖冻结计划要求的异质任务、重复种子、原子主张与敏感性分析。
- **READINESS-INDEPENDENT-VALIDATION-BELOW-HARD-MINIMUM**（stage_2_protocol）：独立测量与校准得分 0.35，低于硬下限 0.60。 需要：主评估器须有跨家族或盲态人工金标准校准，并报告混淆矩阵。
- **READINESS-NOVELTY-POSITIONING-BELOW-HARD-MINIMUM**（stage_1_discovery）：新颖性与领域定位得分 0.44，低于硬下限 0.50。 需要：冻结实验文献边界，并在投稿前独立刷新领域定位与负面新颖性证据。
- **READINESS-SCIENTIFIC-IDENTIFICATION-BELOW-HARD-MINIMUM**（stage_2_protocol）：方法与因果识别得分 0.17，低于硬下限 0.55。 需要：协议必须隔离目标干预，冻结分支点、随机化、分析单位与停止规则。
- **RC-CONSTRUCT-UNDERCOVERAGE**（stage_1_discovery）：代理指标没有完整测量信息性和科学用途。 需要：冻结并增加主张保留、语义变化、信息性和用途指标。
- **VENUE-QUALITY-BAR-NOT-MET**（stage_1_discovery）：锁定 venue 的加权质量分 0.4767 低于冻结质量线 0.6800。 需要：不得靠换 venue 或加文字过门；必须提升该 venue 权重下的方法、证据、复现与成熟度，然后用同一 venue id 重新推荐和审计。

## 失败后的系统设计修复

失败指纹：`pubfail-3786930890c17d72`。旧实验保持不可变；以下规则对下一版协议和未来运行生效。

- **repair-5cdb0f34200c · stage_1_discovery**：计划批准时只冻结支持性代理指标，没有冻结信息性、删除/保留和语义变化。 系统规则改为：质量/可靠性研究若缺少支持性与信息性双轨构念，计划或协议不得进入 publication 路线。 回归测试：仅 unsupported_claim_rate 必须失败；加入 semantic_change 与 informativeness 指标后构念检查通过
- **repair-4bd7ed50f4ed · stage_2_protocol**：协议把独立随机运行误当成配对反事实，没有冻结同一上游 artifact 的分支点与顺序随机化。 系统规则改为：publication 目标的下游干预比较必须声明内容寻址的共享 artifact 分支；否则冻结失败。 回归测试：baseline-then-treatment 整块顺序必须失败；共享 artifact 加随机分支顺序必须通过识别检查
- **repair-052787ae61f0 · stage_4_synthesis**：流程完成状态曾可能被误解为 publication 审批资格。 系统规则改为：primary_analysis_interpretable=false 时只能进入 developmental review；投稿证书必须拒发。 回归测试：延期人审时 publication_submission_ready 必须为 false；persona panel 不得改变人工验证字段
- **repair-973cda589e37 · stage_2_protocol**：协议冻结允许生成器、干预器和主评估器同源，却没有独立校准合同。 系统规则改为：publication 目标的协议只要仍存在同源测量且无冻结校准，就必须在执行前失败。 回归测试：同源且无校准必须阻断下一版 Stage 2 freeze；跨家族评估器也必须提供校准证据而非仅换模型名
- **repair-a7487fc44a87 · stage_2_protocol**：计划允许少量任务和种子完成 pipeline，却没有按投稿目标反推证据广度下限。 系统规则改为：publication 修复版协议在冻结前至少声明 8 个异质任务和每任务 5 个种子，或记录有依据的等效功效方案。 回归测试：3 tasks x 3 seeds 在激活修复单后必须失败；8 tasks x 5 seeds 应满足默认广度合同
- **repair-c83614b6539a · stage_1_discovery**：冻结实验文献包曾同时承担投稿定位，缺少投稿前独立 contextual refresh。 系统规则改为：进入修复版 Stage 2 前必须存在独立的 novelty_refresh.json，绑定检索式、筛选账本和贡献矩阵。 回归测试：激活 novelty 修复且缺 refresh artifact 时冻结失败；有效 refresh artifact 必须绑定来源与生成时间

## 最短过线路线（必须用真实产物复核）

1. **完成独立盲态校准**：回到 `stage_2_protocol`，在 `stage_3_experimentation` 完成；累计情景就绪度 59.1%。产物：冻结样本、两名独立审阅者或独立金标准、混淆矩阵、阈值判定与解盲记录。
2. **运行前瞻性同产物随机分支实验**：回到 `stage_2_protocol`，在 `stage_3_experimentation` 完成；累计情景就绪度 68.8%。产物：预注册协议、内容寻址分支点、随机顺序、运行清单和冻结分析。
3. **补齐信息性与语义变化构念**：回到 `stage_1_discovery`，在 `stage_3_experimentation` 完成；累计情景就绪度 73.3%。产物：支持性、删除/保留、语义收缩、事实纠正和科学信息性的冻结双轨量表。
4. **扩展异质任务与稳定性分析**：回到 `stage_2_protocol`，在 `stage_3_experimentation` 完成；累计情景就绪度 74.2%。产物：至少 8 个异质任务、每任务至少 5 个冻结种子、逐任务效应与 leave-one-domain-out 分析。
5. **刷新领域定位和负面新颖性检索**：回到 `stage_1_discovery`，在 `stage_4_synthesis` 完成；累计情景就绪度 76.8%。产物：可复跑检索式、筛选账本、canonical systems 对照和逐贡献 novelty matrix。
6. **发布匿名可访问的复现材料**：回到 `stage_3_experimentation`，在 `stage_4_synthesis` 完成；累计情景就绪度 78.5%。产物：匿名仓库/归档 DOI、版本标签、环境锁、原始结果、完整成本遥测与哈希绑定。
7. **按目标 venue 模板完成投稿包**：回到 `stage_4_synthesis`，在 `stage_4_synthesis` 完成；累计情景就绪度 81.2%。产物：目标模板 PDF、逐项 checklist、匿名化报告、claim audit 和最终独立完整性复核。

全部动作完成后的情景投影：**81.2%**。

## 四阶段反向约束

### stage_1_discovery

在批准研究问题前冻结 venue、贡献、新颖性边界和完整构念。

- 补齐信息性与语义变化构念
- 刷新领域定位和负面新颖性检索

### stage_2_protocol

在协议冻结前反推识别条件、独立校准、任务/种子下限和报告合同。

- 完成独立盲态校准
- 运行前瞻性同产物随机分支实验
- 扩展异质任务与稳定性分析

### stage_3_experimentation

只执行能生成投稿门所需证据的冻结实验，并保持逐单元审计链。

- 完成独立盲态校准
- 运行前瞻性同产物随机分支实验
- 补齐信息性与语义变化构念
- 扩展异质任务与稳定性分析
- 发布匿名可访问的复现材料

### stage_4_synthesis

写作、完整性复核、目标模板和投稿清单均通过后才颁发投稿就绪状态。

- 刷新领域定位和负面新颖性检索
- 发布匿名可访问的复现材料
- 按目标 venue 模板完成投稿包

## 解释边界

- 投影只是假设每项要求的证据真实生成并通过复核；它不会自动清除任何根因。
- 投影不改变录用概率估计，也不能用作未来结果、人工验证或投稿保证。
- 每次稿件、协议、实验或 venue 周期变化后都必须重新运行硬门。
