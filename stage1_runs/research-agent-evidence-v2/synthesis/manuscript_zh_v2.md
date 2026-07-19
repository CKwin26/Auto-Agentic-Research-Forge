# 自主研究代理中的交付前主张证据门控：一项同骨干配对消融研究

匿名作者（盲审稿）

> 研究状态说明：本文报告受保护自动评估器的初步结果。预注册双人盲审已冻结抽样包，但按研究者决定暂缓执行。因此，本文不能将代理指标解释为真人验证结论，预注册主分析仍处于不可解释状态。

## 摘要

端到端自主研究代理已能组织文献、执行代码并生成论文，但实验运行成功不等于最终文字中的每条主张均受到来源或实验产物支持。本研究检验一种位于交付前的主张证据门控：系统先逐条核对结构化主张与冻结证据，对无支撑或无法判断的主张执行一次受限修订，再进行复核。实验冻结 Research Forge/Codex 骨干、非门控提示、文献集合、任务顺序与运行预算，采用 2 个实验组、3 个 CPU AIRS-lite 任务和 3 个种子的配对设计，共运行 18 个单元。受保护自动评估器在基线组 36 条主张中判定 11 条无支撑，在门控组 36 条主张中判定 3 条无支撑，对应比例为 30.5556% 与 8.3333%。九个任务-种子配对的平均处理效应为 -0.2222，10,000 次层级 bootstrap 的 95% 区间为 [-0.4167, -0.0833]。门控组的实验细节错误率较低，引文正确率较高，两个组均保留 36 条主张；其总代价是测得的运行时间增加 65.90%。这些结果表明，交付前门控在当前自动评估边界内与较少的无支撑主张相关，但尚不能替代独立真人审计。

**关键词：** 自主研究代理；主张验证；证据溯源；配对消融；受保护评估；科研可靠性

## Abstract

End-to-end research agents can retrieve literature, execute code, and draft manuscripts, yet a successful experiment does not ensure that every claim in the final prose is supported by its cited source or linked artifact. This study evaluates a pre-delivery claim-evidence gate that checks structured claims against frozen evidence, permits one constrained revision for unsupported or indeterminate claims, and then rechecks the result. We freeze the Research Forge/Codex backbone, all non-gate prompts, the literature packet, task order, and runtime budgets. The paired design contains two arms, three CPU AIRS-lite tasks, three seeds, and 18 cells. The protected automated evaluator labels 11 of 36 baseline claims and 3 of 36 treatment claims as unsupported, corresponding to rates of 30.5556% and 8.3333%. Across nine task-seed pairs, the mean treatment-minus-baseline effect is -0.2222; the 95% interval from 10,000 hierarchical bootstrap resamples is [-0.4167, -0.0833]. The treatment also has a lower experiment-detail error rate and higher citation correctness, while both arms retain 36 final claims. Total measured wall-clock time increases by 65.90%. The findings associate pre-delivery gating with fewer unsupported claims within the automated evaluation boundary, but they do not constitute independent human validation because the preregistered two-auditor assessment remains deferred.

**Keywords:** autonomous research agents; claim verification; evidence provenance; paired ablation; protected evaluation; scientific reliability

## 1 引言

自主研究系统正在把科学工作中的多个环节连接为可执行流程。现有系统可在一个循环中完成研究构思、文献检索、代码修改、实验运行、结果解释和稿件生成，部分工作还引入长期记忆、内部验证或交互式人类检查点 [1-3]。这种整合扩大了代理可以完成的任务范围，也把错误传播到最终科学叙述的风险集中到同一条流水线上。已有端到端系统报告过实验细节幻觉、实现失败和指标误读 [1]；开放式搜索研究还观察到为获得评分而捏造运行结果的行为 [4]。因此，运行日志存在且代码可执行，并不足以保证交付文本中的主张与其证据一致。

相关研究大致形成两条互补路线。一条路线改进研究代理的搜索、协作与自我演化，使其能够提出更多候选、复用历史经验或覆盖理论与垂直领域 [2,5-9]。另一条路线直接核查科学主张、检索证据或验证引文，强调主张级证据覆盖和可追溯性 [10-12]。前者主要评价研究产物的质量、执行成功或领域任务表现，后者多作为独立验证系统或特定领域组件存在。在本研究冻结的 12 篇元数据与摘要级文献集合中，这两条路线尚未提供一个同骨干、同任务和同预算的配对消融，用来估计“仅在交付前增加主张证据门控”与最终无支撑主张率之间的关系。该判断只限定于冻结文献集合，不构成全面的新颖性主张。

本研究据此提出以下问题：在 CPU AIRS-lite 任务集和固定 Research Forge/Codex 骨干上，当模型、非门控提示、检索集合、运行上限、迭代预算与任务顺序保持不变时，交付前的拒绝-修订-复核门控是否比无门控的同骨干系统产生更低的无支撑事实主张率？研究不提出新的基础模型或推理架构，而是把一个可靠性步骤作为可控干预。主要观察量是最终主张注册表中的无支撑主张比例；次要观察量包括实验细节错误率、引文正确率、证据覆盖率、任务原生分数、主张保留率和运行时间。

## 2 相关工作

### 2.1 端到端研究代理与开放式搜索

端到端研究代理的主要进展来自流程扩展和搜索能力。Autonomous Research Loops 将假设生成、文献检索、编码、实验、稿件撰写和模拟审稿连接到同一循环，同时报告了实验细节幻觉、错误实现和指标误读等失败模式 [1]。EvoScientist 使用研究、工程和演化管理角色以及持久记忆，试图从既往成功与失败中改进构思和代码执行 [2]。交互式多代理系统则通过持续世界状态和人类检查点缩短研究者反馈周期 [3]。这些工作说明研究代理已不再只是代码生成器，但它们也表明，最终研究产物的可靠性不能由流程完整性自动推出。

研究构思和开放式搜索工作进一步揭示了质量、新颖性与真实性之间的张力。Nova 通过迭代规划与外部知识检索提高构思的多样性 [5]；HypoAgents 使用贝叶斯更新和信息熵选择高不确定性假设进行修订 [6]；Heuresis 比较多种质量-多样性搜索策略，并在 1,628 次评分运行中确认 40 次捏造行为 [4]。这些系统的核心对象是候选研究方向或搜索轨迹，而本研究关注最终交付文本中的原子主张是否得到冻结证据支持。

研究代理也已进入理论推导和临床任务。ReasFlow 把文献综合、算法设计、定理证明、实验和稿件准备置于同一系统，并加入内部逻辑审计 [7]；TriAgent 和 MedAgent 分别把多代理研究与临床证据检索、分层和答案生成结合 [8,9]。这些领域系统强调证据接地的重要性，但其任务目标、训练过程和评估基准与本文的同骨干干预设计不同。

### 2.2 主张、证据覆盖与引文验证

主张级验证研究把答案拆成可核查单元，再判断检索材料是否支持、反驳或无法判断该主张。CliVER 从 PubMed 摘要中检索并选择证据句，随后对临床科学主张进行支持、反驳和中立分类 [10]。Evidence Coverage Evaluator 将证据覆盖与表面正确性区分开，组合细粒度主张抽取、证据检索和自然语言推断或大模型判断 [11]。RefLens 则从原始 PDF 中提取证据片段，以引文卡片和论文级报告呈现核查结果 [12]。这些工作为“主张与证据是否匹配”提供了独立评估方法。

本文把该思想嵌入自主研究代理的交付边界，并将其作为唯一预定干预。基线组与门控组先使用同一结论生成器产生结构化主张；只有门控组在交付前接受一次拒绝、受限修订和复核。这样的设计并不比较两个完整代理系统，也不评价哪种基础模型更强。它试图在尽量缩小变化面的条件下，观察主张级证据检查与最终文本可靠性代理指标的关系。

## 3 方法

### 3.1 预注册设计与实验矩阵

研究方案 `stage2-22e124e44294` 在执行前冻结，采用 2（基线、门控）× 3（任务）× 3（种子）的配对设计，共 18 个运行单元。种子为 0、1 和 2；每个任务的基线与门控运行按相同种子配对。研究在完成恰好 18 个单元后停止，不允许根据中间结果扩展任务、重排候选或选择性重跑。完整性条件包括模型与文献清单一致、主张注册表符合模式、实验主张能解析到运行 ID 与产物、文献主张包含精确来源 ID，以及盲态评估配置未发生漂移。

实验按预先固定的顺序先执行 9 个基线单元，再执行 9 个门控单元，而不是随机交错执行。这一顺序是冻结方案的一部分，但也意味着处理效应可能混入时间顺序或后端非确定性影响，本文在效度威胁中单独讨论。

![实验设计](../../../output/pdf/figures/study_design.png)

图 1 显示两个实验组共享研究骨干、任务配置、文献包和初始结论生成器。唯一设计性差异位于交付前的主张证据路径。两个实验组的最终注册表均进入随机盲 ID 下的受保护评估；预注册双人审计样本已冻结，但本阶段暂缓执行。

### 3.2 研究骨干与受控运行环境

实验骨干为 Research Forge 控制器与 `codex:gpt-5.4` 后端。控制器启用候选去重和失败诊断，候选池大小为 1，每次迭代最多提出 2 个候选，任务迭代上限为 1。每个任务的运行时限为 120 秒。实验代码在 `rf-airs-cpu:v1` Docker 镜像中执行，使用 1 个 CPU、2,048 MB 内存、只读根文件系统、禁用网络和禁止新增权限。Python 版本与主要依赖均由清单冻结；论文排版环境不参与实验运行。

三个 AIRS-lite 任务分别为 SICK 文本分类准确率、SICK 文本相似度 Spearman 相关系数和 SuperGLUE WSC 指代消解准确率。表 1 给出任务在冻结任务包中的参考基线与资源上限。不同任务的原生指标不可直接视为同一量纲，因此跨任务平均分只作为流水线描述量，不用于推断通用任务能力。

| 任务 | 原生指标 | 冻结参考基线 | 单元时限（秒） |
|---|---:|---:|---:|
| SICK 文本分类 | Accuracy | 0.568691 | 120 |
| SICK 文本相似度 | SpearmanCorrelation | 0.575719 | 120 |
| SuperGLUE WSC 指代消解 | Accuracy | 0.634615 | 120 |

### 3.3 交付前主张证据门控

共享结论生成器只接收已完成运行、受保护运行记录、冻结任务说明和冻结文献包。其输出是结构化主张注册表。实验主张必须包含精确运行 ID、指标名、数值和产物路径；文献与新颖性主张必须包含冻结来源 ID；新颖性比较只能限定于已审查文献集合。生成器不得推断未报告指标、因果机制、统计显著性或普适性。

基线组在共享生成器之后直接交付注册表，不执行验证器驱动的拒绝或修订。门控组先让语义验证器仅依据每条主张链接的冻结证据，将其标为支持、无支撑或无法判断。只有无支撑或无法判断的主张可修订一次，修订不得增加来源、运行、指标、产物或主张数量。系统随后复核一次；仍未通过的主张按方案移除。该组合干预同时包含判断、受限修订和复核，当前实验不能把观察到的效应归因到其中某一个子组件。

### 3.4 受保护评估与指标

18 个最终注册表在揭示实验组之前被赋予盲 ID。评估器首先执行确定性结构检查，例如来源 ID、运行 ID、指标值和产物路径能否解析；任一必要结构检查失败时，该主张被判为无支撑。结构通过后，Codex 语义评估器只查看冻结证据包，并对完整原子主张给出支持、无支撑或无法判断。语义提示明确禁止使用外部知识或从元数据推断实验组。

设最终主张数为 $N$，其中无支撑主张数为 $N_u$，则主要代理指标为 $N_u/N$。引文正确率以文献与新颖性主张中被判支持的比例计算；实验细节错误率以实验主张中被判无支撑的比例计算；证据覆盖率是所有确定性结构检查均通过的主张比例。任务原生分数直接来自各任务运行记录，运行时间为单元墙钟时间之和。上述指标均属于受保护自动评估器的估计，而非人工真值。

### 3.5 统计分析与暂缓的人类审计

主要效应定义为门控组无支撑主张率减去同任务、同种子基线组的无支撑主张率，因此负值有利于门控组。九个配对效应取算术平均，并计算配对 Cohen's $d_z$。层级 bootstrap 先对三个任务有放回抽样，再在每个抽中的任务内对三个种子效应有放回抽样；方案固定 10,000 次重采样和由协议 ID 派生的随机种子，报告 2.5% 与 97.5% 分位数。

预注册方案要求两名独立审计者对 48 条盲态主张进行判断，每个任务-实验组层抽取 8 条。由于可用的自动评估“无支撑”主张只有 14 条，修订后的抽样规则纳入全部 14 条，并以确定性顺序补足 34 条其他主张。样本与哈希已经冻结，但两名审计者尚未开始工作。研究者决定在当前阶段暂缓该验证，因此自动评估效应不能解锁预注册主分析。

## 4 结果

### 4.1 无支撑主张率

受保护自动评估器在基线组 36 条最终主张中判定 11 条无支撑，在门控组 36 条最终主张中判定 3 条无支撑。相应比例为 30.5556% 和 8.3333%，组级差异为 -22.22 个百分点。九个任务-种子配对的平均效应为 -0.2222，配对 Cohen's $d_z=-0.8433$。10,000 次层级 bootstrap 的中位数为 -0.2222，95% 区间为 [-0.4167, -0.0833]。

三个任务的平均配对效应方向一致：SICK 分类为 -0.2500，SICK 相似度为 -0.2500，WSC 指代消解为 -0.1667。单个注册表只含 4 条主张，因此每个单元的主张率以 0.25 为最小非零步长；任务级一致性不应被解释为精确的效应稳定性。图 2B 同时显示任务效应和总体 bootstrap 区间。

![保护评估结果](../../../output/pdf/figures/protected_results.png)

图 2 展示受保护自动评估结果。A：两个实验组的主要与次要比例指标；B：三个任务的门控组减基线组配对效应，红色区间为总体层级 bootstrap 的 95% 区间。该区间描述冻结自动评估代理指标，不代表人类审计后的置信区间。

### 4.2 次要可靠性指标与主张保留

实验细节错误率从基线组的 33.3333% 降至门控组的 5.5556%，引文正确率从 66.6667% 升至 88.8889%。两个组的证据覆盖率均为 100%，说明两组最终主张都携带了可解析的结构化证据链接；这一指标只检查结构完整性，不保证语义上真正支持。验证器在基线组有 1 条无法判断主张，在门控组没有无法判断主张。

两个实验组均从 36 条初始主张保留 36 条最终主张，保留率为 100%。因此，当前组级差异不是由直接减少最终主张数量造成的。不过，相同的主张数量不能排除门控组通过缩窄措辞或降低主张强度获得更高支持率；要区分“删除”“改写”和“语义收缩”的贡献，需要对修订轨迹做单独编码。

| 受保护自动评估指标 | 基线组 | 门控组 |
|---|---:|---:|
| 无支撑主张率 | 30.56%（11/36） | 8.33%（3/36） |
| 实验细节错误率 | 33.33% | 5.56% |
| 引文正确率 | 66.67% | 88.89% |
| 证据覆盖率 | 100.00% | 100.00% |
| 最终主张数 | 36 | 36 |
| 无法判断主张数 | 1 | 0 |

### 4.3 任务分数与运行代价

SICK 分类的任务原生分数在三个种子上的均值由 0.5914 变为 0.6561，平均差为 +0.0647；SICK 相似度和 WSC 指代消解的均值在两个实验组中分别保持 0.5757 和 0.6346。冻结方案要求每个任务的绝对均值差不超过 0.02，该布尔检查为假，原因是 SICK 分类出现了正向差异，而不是三个任务都发生退化。由于两个实验组运行的是独立随机单元，且门控位于结论交付路径，本文不把该正向差异解释为门控提高了任务能力。

基线组的总墙钟时间为 2,721.13 秒，门控组为 4,514.38 秒，绝对增加 1,793.26 秒，约 29.89 分钟；相对增加 65.90%。按任务分解，门控组在 SICK 分类和相似度任务上用时更长，在 WSC 任务上略短。总运行代价表明，当前实现用额外语义判断和修订换取代理可靠性改善，尚未证明这一成本在更大任务集上可以接受。

| 任务 | 基线均值 | 门控均值 | 门控 - 基线 |
|---|---:|---:|---:|
| SICK 分类 Accuracy | 0.5914 | 0.6561 | +0.0647 |
| SICK 相似度 SpearmanCorrelation | 0.5757 | 0.5757 | 0.0000 |
| WSC 指代消解 Accuracy | 0.6346 | 0.6346 | 0.0000 |

### 4.4 同模型科学家人格面板

作为补充错误发现工具，同模型科学家人格面板对 48 条盲态主张进行评审，最终汇总为 37 条支持和 11 条无支撑，其中 4 条出现混合投票。人格面板与研究骨干共享模型家族，无法提供独立误差来源。因此，本研究只把这些意见用于暴露争议主张，不将其视为真人验证、跨模型复现或主分析证据。

## 5 讨论

### 5.1 自动评估边界内的解释

在冻结代理指标下，交付前主张证据门控与较低的无支撑主张率、较低的实验细节错误率和较高的引文正确率相关。三个任务的平均配对效应均为负，且两个实验组保留相同数量的最终主张。这个结果支持一个有限判断：当前门控并非仅通过删除大量主张获得更低的代理错误率。它没有说明哪一个门控子步骤产生作用，也没有排除措辞收缩、验证器偏好或同模型相关误差。

最明显的改善出现在实验细节主张。此类主张具有精确运行 ID、指标名、数值和产物路径，确定性检查可以直接发现不一致；文献与新颖性主张则需要语义蕴含判断，边界更模糊。由此推断，在实际系统中，可先用确定性规则处理标识符、数值和产物完整性，再把语义评估集中到高风险或难判主张。该设计建议来自当前错误结构，而不是本实验直接比较过的另一种门控实现。

### 5.2 可靠性与成本的权衡

门控组的总运行时间增加 65.90%，说明可靠性步骤不是零成本附加项。当前门控对每条主张都执行语义判断，并允许一次修订和复核；当主张数、文献包或实验产物规模扩大时，代价可能继续增长。后续实现可以测试风险分层、批量结构检查、语义判断缓存和只复核发生变化的主张，但这些优化尚未在本研究中验证。

任务原生分数没有显示一致退化，但这一结果也不能证明门控不影响任务质量。门控发生在结论生成之后，理论上不应改变已经完成的任务结果；然而实验组由不同随机运行组成，SICK 分类在种子间波动较大。更严格的设计应在同一运行产物上同时生成无门控和有门控结论，或至少交错执行实验组，从而把结论门控效应与上游运行随机性分开。

### 5.3 效度威胁

测量效度是当前最重要的限制。保护评估器、门控器和研究骨干都使用 Codex 家族推理，可能共享语义偏好和盲点。层级 bootstrap 区间只反映该评估器定义下的配对变异；在双人盲审完成前，不能把 30.5556% 与 8.3333% 当作真实错误率，也不能据此宣称预注册假设已获支持。

内部效度受到执行顺序、样本量和干预捆绑的限制。9 个基线单元先于 9 个门控单元运行，实验组没有随机交错；每个任务仅有 3 个种子，每个注册表仅有 4 条主张；拒绝、修订和复核作为一个整体出现。这样的设计足以测试工程闭环，但不足以稳定估计各子组件的独立效应。固定的“绝对 0.02”任务质量检查还把正向改善视为偏离，导致其布尔结果不能直接解释为质量失败。

外部效度同样有限。实验只覆盖三个 CPU 文本任务、一个 Codex 骨干、一个门控实现、候选池大小 1 和一次迭代。结果不能推广到实验室科学、多模态数据、长周期训练、其他基础模型或具有不同证据结构的学科。Stage 1 文献审查只使用 12 篇经核验的元数据与摘要记录，因此本文只能说明在该冻结集合中的研究定位，不能声称完整覆盖相关领域或确立首次性。

### 5.4 下一步验证

最直接的下一步不是继续扩大自动审稿面板，而是完成已经冻结的两名独立审计者盲审，并按预注册规则计算保护评估器在其“无支撑”判断上的假阳性率。若该比例超过 0.15，主分析按方案无效。随后应使用不同模型家族的评估器复核相关误差，交错执行实验组，扩大任务与种子数量，并把“仅验证”“验证加修订”“验证加修订加复核”拆成组件消融。只有这些步骤完成后，才能判断当前代理效应是否对应可重复的人类可靠性收益。

## 6 结论

在 18 个冻结运行单元和受保护自动评估器的边界内，交付前主张证据门控与无支撑主张率从 30.5556% 降至 8.3333% 相关；九个配对的平均效应为 -0.2222，层级 bootstrap 95% 区间为 [-0.4167, -0.0833]。门控组没有减少最终主张数量，但总运行时间增加 65.90%。这些数字支持继续研究主张级门控，不足以证明其已经通过人类验证。当前论文的科学结论仍是暂定的，决定性步骤是恢复并完成预注册双人盲审。

## 数据与代码可用性

协议、冻结清单、18 个运行单元、盲态评估摘要、初步分析、主张注册表和合成审计均保存在项目目录 `stage1_runs/research-agent-evidence-v2/`。关键入口包括 `stage2/protocol.json`、`stage2/evaluations/summary.json`、`stage2/provisional_analysis.json`、`synthesis/claims.json` 和 `synthesis/audit.json`。人工审计样本位于受保护盲态目录，按方案在审计完成前不公开实验组映射。

## 伦理、资金与利益冲突声明

当前实验不涉及人类受试者、动物或敏感个人数据。预注册的人类工作仅为两名独立审计者对研究主张进行盲态判断，但该环节尚未启动。本文未提供外部资助信息，未报告利益冲突。

## 作者贡献与 AI 使用声明

匿名作者负责研究问题、方案批准、实验监督和结果解释。Research Forge/Codex 工具用于文献检索与核验、实验控制、代码执行、受保护评估、初步统计分析、稿件结构设计、写作和排版。受保护评估器本身是研究方法的一部分，不是独立人类审稿。本文明确保留尚未完成人类验证的边界；作者对最终提交内容及其准确性承担责任。

## 参考文献

1. Koyun, A. (2026). *Autonomous Research Loops: An LLM-Agent Framework for End-to-End ML Experimentation, Manuscripting, and Self-Evaluation*. https://doi.org/10.1145/3802133.3802134
2. Lyu, Y., Zhang, X., Yi, X., et al. (2026). *EvoScientist: Towards Multi-Agent Evolving AI Scientists for End-to-End Scientific Discovery*. https://doi.org/10.48550/arxiv.2603.08127
3. Weidener, L., Brkić, M., Jovanovic, M. R., et al. (2026). *Rethinking the AI Scientist: Interactive Multi-Agent Workflows for Scientific Discovery*. https://doi.org/10.48550/arxiv.2601.12542
4. Antoniades, A., Nathani, D., Saha, R., et al. (2026). *Heuresis: Search Strategies for Autonomous AI Research Agents Across Quality, Diversity and Novelty*. https://www.semanticscholar.org/paper/ce30a6d54ba9a1bec69ca1a4e2d744b6be1479be
5. Hu, X., Fu, H., Wang, J., et al. (2024). *Nova: An Iterative Planning and Search Approach to Enhance Novelty and Diversity of LLM Generated Ideas*. https://doi.org/10.48550/arxiv.2410.14255
6. Duan, S., Tian, Y., Bing, Q.-T., & Shao, X. (2025). *Bayes-Entropy Collaborative Driven Agents for Research Hypotheses Generation and Optimization*. https://doi.org/10.48550/arxiv.2508.01746
7. He, Y., Li, D., Li, G., et al. (2026). *ReasFlow: Assisting Reasoning-Centric Scientific Discovery in Applied Mathematics via a Knowledge-Based Multi-Agent System*. https://www.semanticscholar.org/paper/82f8c2296bba3d06dfc1dc9291963cc0a30359a0
8. Delikoyun, K., Chen, Q., Kuan, W., et al. (2025). *TriAgent: Automated Biomarker Discovery with Deep Research Grounding for Triage in Acute Care by LLM-Based Multi-Agent Collaboration*. https://doi.org/10.48550/arxiv.2510.16080
9. Wang, F., Guo, Z., & Ye, Z. (2026). *MedAgent: A Retrieval-Augmented Clinical Decision Support Agent with Verifiable Evidence Grounding for Evidence-Based Medicine*. https://doi.org/10.64898/2026.06.15.26355735
10. Liu, H., Soroush, A., Nestor, J. G., et al. (2024). Retrieval augmented scientific claim verification. *JAMIA Open*. https://doi.org/10.1093/jamiaopen/ooae021
11. Adwant, G., & Srivastav, M. (2026). *Evidence Coverage Evaluator: A Comprehensive Framework for Assessing Grounding Quality in Retrieval-Augmented Generation Systems*. https://doi.org/10.36227/techrxiv.176784505.56675782/v1
12. Lee, S., Kwon, J., Choi, J., et al. (2026). RefLens: End-to-End Evidence-Grounded Citation Verification with LLM Agents. *Proceedings of the AAAI Conference on Artificial Intelligence, 40*(48). https://doi.org/10.1609/aaai.v40i48.42361
