# Research Forge 正规期刊与学术会议投稿分析

- 论文：**Pre-Delivery Claim-Evidence Gating in Autonomous Research Agents: A Protected-Evaluator Pilot with Same-Artifact Cross-Family Rebranching**
- 生成时间：`2026-07-18T16:31:21.731082+00:00`
- 校准状态：`uncalibrated_heuristic_v1`
- 当前投稿门：**未通过**
- 最高可辩护结论层级：`internal_proxy_association`
- 白名单政策：Default recommendations include only established peer-reviewed journals or archival full-paper conference tracks verified from official venue sources. Workshops, posters, competitions, non-archival tracks, and broad commercial fallback journals are excluded.

> 概率是 Research Forge 的宽区间决策估计，不是 venue 公布的录用率，也不是录用保证。会议的“本周期概率”为 0 代表截止日期已过，不代表研究质量为零。

## 正规同行评审期刊

| 排名 | 期刊 / 栏目 | 范围匹配 | 初筛通过 | 送审后成功 | 当前综合 | 修完已知缺陷后 | 路由 |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | [Research Integrity and Peer Review](https://link.springer.com/journal/41073/aims-and-scope) / Research Article | 88% | 56.3% [40.3%, 70.3%] | 18.6% [1.6%, 35.6%] | 5.4% [2.3%, 11.0%] | 30.2% [12.7%, 54.3%] | `do_not_submit_yet` |
| 2 | [Transactions on Machine Learning Research](https://www.jmlr.org/tmlr/editorial-policies.html) / Research Article | 64% | 41.3% [25.3%, 55.3%] | 8.2% [0.0%, 25.1%] | 1.8% [0.7%, 4.6%] | 18.4% [7.7%, 33.8%] | `do_not_submit_yet` |
| 3 | [Journal of Artificial Intelligence Research](https://jair.org/index.php/jair/about) / Research Article | 100% | 52.1% [36.1%, 66.1%] | 4.2% [0.0%, 21.2%] | 1.1% [0.5%, 3.5%] | 11.9% [5.0%, 22.3%] | `do_not_submit_yet` |
| 4 | [Transactions of the Association for Computational Linguistics](https://direct.mit.edu/tacl/pages/submission-guidelines) / Resource and Evaluation / Empirical Methods | 63% | 36.1% [20.1%, 50.1%] | 4.2% [0.0%, 21.2%] | 0.8% [0.3%, 2.9%] | 10.4% [4.4%, 19.8%] | `do_not_submit_yet` |
| 5 | [Artificial Intelligence](https://www.sciencedirect.com/journal/artificial-intelligence/about/aims-and-scope) / Regular Paper | 100% | 49.8% [33.8%, 63.8%] | 2.5% [0.0%, 19.5%] | 0.7% [0.3%, 2.6%] | 7.7% [3.2%, 14.9%] | `do_not_submit_yet` |
| 6 | [Journal of Machine Learning Research](https://www.jmlr.org/author-info.html) / Research Article | 49% | 26.3% [10.3%, 40.3%] | 3.5% [0.0%, 20.5%] | 0.5% [0.2%, 2.3%] | 6.8% [2.9%, 13.5%] | `do_not_submit_yet` |

## 为什么现在不能直接投

- **CRITICAL · RC-MEASUREMENT-CIRCULARITY**：生成器、干预和主评估器没有完成独立校准。 需要：完成盲态人工校准或真正跨模型家族的校准，并报告一致性和错误结构。
- **CRITICAL · RC-COUNTERFACTUAL-NONISOLATION**：原始两组没有隔离同一实验产物上的反事实效应。 需要：做前瞻性的同产物分支实验，或采用随机化、交错执行的对照设计。
- **HIGH · RC-CONSTRUCT-UNDERCOVERAGE**：代理指标没有完整测量信息性和科学用途。 需要：冻结并增加主张保留、语义变化、信息性和用途指标。
- **CRITICAL · RC-MATURITY-TARGET-MISMATCH**：当前证据仍是临时性 pilot，而投稿需要可解释的验证结果。 需要：在进入投稿路由前完成预注册的验证门。
- **MEDIUM · RC-REPORTING-CONTRACT-GAP**：当前报告契约不足以支撑目标结论层级。 需要：把每个核心结论绑定到冻结分析，并披露全部协议偏差。
- **MEDIUM · RC-TELEMETRY-SCHEMA-GAP**：执行遥测不足以支撑部分机制层面的结论。 需要：在前瞻性实验中冻结并采集缺失的遥测字段。
- **MEDIUM · RC-LITERATURE-SCOPE-COUPLING**：新颖性边界依赖于范围有限的冻结文献包。 需要：在提出宽泛新颖性主张前刷新并独立核验领域检索。

## 最有价值的下一步

- 完成盲态人工校准或真正跨模型家族的校准，并报告一致性和错误结构。
- 做前瞻性的同产物分支实验，或采用随机化、交错执行的对照设计。
- 冻结并增加主张保留、语义变化、信息性和用途指标。
- 在进入投稿路由前完成预注册的验证门。
- 把每个核心结论绑定到冻结分析，并披露全部协议偏差。
- 在前瞻性实验中冻结并采集缺失的遥测字段。
- 在提出宽泛新颖性主张前刷新并独立核验领域检索。
- 发布匿名、带版本的软件/数据补充材料，并在论文中绑定发布哈希。
- 在冻结协议下，把验证集从 3 个任务扩展到至少 8 个异质任务。

## 评分依据

写作/报告 0.99；方法严谨度 0.17；证据 0.53；新颖性 0.44；复现性 0.75；软件就绪度 0.53；投稿成熟度 0.16。

Venue 官方资料核对日期：`2026-07-18`。
- 警告：AAAI-27 Main Technical Track full-paper deadline is in 10 days (2026-07-28).
- 警告：AAAI-27 abstract deadline is in 3 days (2026-07-21).
