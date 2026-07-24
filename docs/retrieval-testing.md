# Retrieval Gateway 测试策略

## 单元测试

- 默认 offline 不调用 Provider；
- 阶段 purpose、资源类型和契约授权；
- secret、token、私钥、绝对路径、email 和内部标识脱敏；
- DOI 优先去重和元数据冲突；
- frozen ResourceSet 不可修改；
- promotion 创建新 Binding 并复用 Snapshot；
- 只有瞬时/限流错误重试。

## 集成测试

- 授权 Discovery 保存 Provider 原始响应；
- 归一化、去重、验证、Snapshot、Binding、Coverage 和 ResourceSet；
- 相同幂等键不产生重复外部调用或资源；
- 凭证不出现在 Artifact 和 Audit；
- Web API 默认 offline，并返回结构化 blocked run。

## Workflow 测试

- 离线项目扫描没有外部调用，仍形成 Scope 草稿并停在 Gate；
- 学术读取通过 Gateway 到达 Scope Gate，SourceSet 在批准前为 draft；
- Scope 批准后才冻结 Discovery SourceSet；
- 成功节点不重复，暂停不启动新节点，上游失败阻塞下游；
- Protocol、Experimentation、Synthesis、Repair 未接 Adapter 时明确 blocked。

## 回归分组

```text
pytest tests/test_retrieval_gateway.py
pytest tests/test_workflow_scheduler.py tests/test_workflow_domain.py
pytest tests/test_web_app.py tests/test_orchestration.py
pytest
```

前端未在本次网关工作中改造，仍执行现有 Vite production build 作为兼容检查。
# External Research V1 coverage

`tests/test_external_research_v1.py` covers truthful readiness, credential-field
rejection, user-bound institutional sessions, revocation, content-right checks,
hash-bound PaperQA corpora, mandatory evidence spans, domain filters,
experimentation search restrictions, and adoption-signal separation.

Together with `test_retrieval_gateway.py`, `test_workflow_scheduler.py`, and
`test_web_app.py`, the focused external-research suite currently contains 43
passing tests. CI remains offline and uses injected provider runners.

Online smoke tests are intentionally separate and disabled by default. Until
they prove live provider health, the associated readiness state remains
`DEGRADED` or `UNAVAILABLE`.
