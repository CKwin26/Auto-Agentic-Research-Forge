# Retrieval Gateway 网络与外发策略

默认模式为 `offline`。可选模式：

- `academic_read`
- `public_web_read`
- `authenticated_read`
- `external_write`

非离线策略必须记录批准人和批准时间。策略明确限制 Provider、域名、HTTP
方法、资源类型、元数据/摘要/全文权限、仓库或数据集下载、认证访问、外部
写入、查询数、结果数、字节、成本以及允许和禁止外发的数据类别。

## 确定性决策

Policy Engine 同时检查 Project Policy 和 StageProfile。Prompt 或模型不能
推断阶段、授权或预算。以下情况直接拒绝且不调用 Provider：

- offline；
- Provider、域名、HTTP 方法或资源类型未授权；
- purpose 不属于当前阶段；
- 查询未完成脱敏；
- Experimentation 缺少契约授权；
- Repair 缺少 Repair Contract；
- 开放式结果导向实验搜索。

只有 `transient_network_error` 和 `rate_limit` 自动重试。认证、许可、策略、
响应格式和永久 Provider 错误不自动重试。

## 外发脱敏

```text
raw query
→ secret scan
→ internal identifier scan
→ local path removal
→ project/customer/private name removal
→ email and credential removal
→ sanitized query
→ policy decision
→ provider adapter
```

普通日志不保存敏感原文，只保存原始查询摘要哈希、脱敏查询和 redaction
report。Provider API Key 只由 Adapter 内部 CredentialResolver 读取。

## 权威策略

`RetrievalNetworkPolicy` 是检索授权的唯一事实来源。Workflow
`ProjectRecord.network_policy` 只是供旧界面读取的兼容投影，不得绕过 Gateway
单独授权网络访问。所有 Workflow v2 外部读取都必须先经过查询脱敏和确定性策略判定。
