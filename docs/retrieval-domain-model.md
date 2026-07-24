# Retrieval Gateway 领域模型

## 请求与运行

- `QueryPlan`：保存研究需求、目的、脱敏查询、Provider、资源类型、范围和预算。
  原始查询只保存 SHA-256 安全引用。
- `RetrievalRequest`：显式绑定 Project、Study、phase、StepInstance、purpose、
  NetworkPolicy、contract refs、预算和幂等键。
- `RetrievalRun`：保存策略决定、Provider attempts、错误分类、重试、预算使用、
  输出 Artifact 和 Coverage。

## 资源三层

1. `ExternalResource`：资源的客观规范身份，例如 DOI、仓库 URL + commit 或
   数据集 identifier + version。
2. `ResourceSnapshot`：某次真实取得且不可变的内容、哈希、MIME、字节数、
   Provider、许可和访问状态。
3. `ResourceUseBinding`：Snapshot 在具体 Study、阶段、步骤、目的、target
   field 和 relation 下的用途。

资源身份与用途分开，避免 Discovery 的背景来源在没有再审批的情况下变成
实验输入或决定性证据。

## 检索工件

新写入的 `RetrievalArtifact` schema v2 直接固化 Project、Study、
StepInstance、producer、内容哈希、输入谱系、项目检索策略上下文和不可变
版本。相同内容只有在 producer、策略与输入谱系也相同时才复用；相同字节但
来源不同会生成不同工件身份。迁移
`004_retrieval_artifact_context` 不改写历史 schema-v1 工件，旧记录继续可读。

## 集合与覆盖

`ResourceSet` 是阶段级版本集合。`frozen` 集合不可原地修改；新增来源必须
创建新版本或 successor。`RetrievalCoverageReport` 记录 Provider、查询、
原始/去重/验证数量、仅元数据数量、未覆盖数据库、许可限制、元数据冲突、
盲区以及不得声称的结论。

## 去重主键

- publication：DOI → 正式 identifier → 规范标题 + 首作者 + 年份；
- code repository：canonical repository URL + commit SHA；
- dataset：registry identifier + version + file hash。

模糊标题只产生候选，不自动合并有冲突的高风险记录。元数据冲突标记为
`conflict` 并进入人工复核。
