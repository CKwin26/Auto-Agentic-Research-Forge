# Forge Retrieval Gateway 架构

Forge Retrieval Gateway 是与 Workflow Scheduler、Artifact Store、Audit
Ledger 平级的横向基础设施，不是第五个研究阶段。Discovery、Protocol、
Experimentation、Synthesis 和 Repair 都通过同一个受控入口访问外部资源。

## 分层

```text
Workflow v2 StepInstance
        ↓
Workflow Adapter
        ↓
Retrieval Gateway Service
        ├── Domain
        ├── Deterministic Policy + Sanitizer
        ├── Provider Registry
        ├── Normalize / Deduplicate / Verify Pipeline
        └── Retrieval Repository + Audit Ledger
```

领域对象描述资源客观身份和使用上下文；策略层决定是否允许执行；Provider
层独占凭证和 HTTP；Pipeline 只处理不可信数据；Workflow Adapter 将结果注册
为 Step Artifact。

## 为什么不是第五阶段

检索不产生独立科学结论。它服务于不同阶段中不同且受限的目的：

- Discovery：发现相关工作和候选空白；
- Protocol：为冻结指标、基线和方法提供依据；
- Experimentation：只取得契约批准且固定版本的资源；
- Synthesis：核验引用、撤稿和投稿规则；
- Repair：针对明确 failure 和 Repair Contract 查找诊断依据。

检索的科学权限来自调用阶段和冻结契约，而不是检索系统本身。

## 不可变数据流

Provider 原始响应先成为不可变 Artifact，再归一化为 `ExternalResource`。
实际取得的内容由 `ResourceSnapshot` 表示。资源在某个研究上下文中的用途由
`ResourceUseBinding` 表示。阶段来源集合由版本化 `ResourceSet` 冻结。

一个资源可以拥有多个 Binding，因为“同一篇论文客观上是什么”与“它在
Discovery 中作为背景、在 Protocol 中为 baseline 提供依据”是不同事实。
跨阶段必须显式 promotion，才能重新检查阶段策略、目的和契约授权，并保留
lineage。

## 阶段权威边界

- Discovery 检索可以改变候选 Scope，但不能成为 Idea Verdict 的决定性证据。
- Protocol 只接受可核验的权威来源，并绑定 Research Contract 具体字段。
- 只有固定版本、哈希、许可和契约批准齐全的资源可进入实验 Sandbox。
- Synthesis 只能增加背景、核验引用或产生 `evidence_conflict`，不能修改
  历史 Verdict。
- Repair 必须绑定 failure、diagnostic owner、最早可预防阶段和 Repair
  Contract。

## 兼容边界

旧 `claim_discovery` Provider 函数保留给直接单元测试和历史读取；新的
Workflow v2 handler 不再调用它们。缺少 Study/phase/StepInstance 上下文的
兼容项目扫描仅执行本地主张提取，不允许外部访问。

## 持久化与迁移

当前实现使用 Workflow v2 根目录下的文件型仓库，而不是 SQL 数据库。仓库以
`retrieval/schema.json` 记录 schema 版本，并通过显式迁移器升级；历史 Workflow
记录和旧 `claim_discovery` 产物保持只读兼容。新写入只进入 Retrieval Gateway
的 append-oriented 记录、不可变快照和版本化 ResourceSet。
