# Great Expectations 受控数据质量门验收

日期：2026-07-31

## 结论

Research Forge 已把 Great Expectations 1.19.1 接成一个离线、哈希绑定的表格结构质量门。冻结的数据集与冻结的 expectation contract 在临时上下文中执行，规范化结果被冻结在数据集目录之外，并可进入资源生命周期的 `semantic_validated` 资格判断。

## 验收证据

- 数据集：`examples/great-expectations-gate/frozen-dataset.csv`
- 数据集 SHA-256：`35797f40101955bd5ee268db08bd3a64f36bba46431578ba247b0d8ed6123c04`
- 冻结契约：`examples/great-expectations-gate/quality-contract.json`
- 外部运行报告：验收运行生成的 `quality-report.json`（运行产物不提交到仓库）
- 报告文件 SHA-256：`47cf85d5b4824dfea65ec43c8ca452cb35288794fe88188d75568680792fb328`
- 规范化报告主体 SHA-256：`625902cb621ef2a6d47139ba8a70ecb2eb8717d48a12604389054cf3dafbb715`
- 结果：通过；执行上下文为 `ephemeral_offline`。

## 平台落点

- `research_forge/great_expectations_gate.py`：冻结契约、真实 GX 执行与规范化报告。
- `research_forge/resource_validation.py`：把报告绑定到同一数据集哈希；失败、缺失或错绑报告不得获得结构资格。
- `scripts/validate_great_expectations_gate.py`：可独立运行的验收入口。
- `tests/test_great_expectations_gate.py` 与 `tests/test_resource_validation.py`：覆盖通过、唯一性失败、数据篡改、证据写入源目录和错绑报告。

## 允许声称与边界

允许声称：Research Forge 能对冻结表格资源执行可审计的 Great Expectations 结构质量门。

不能声称：通过该门不证明标签语义正确、研究设计有效、因果识别成立或科学假设获得支持；这些仍由冻结契约、实验、确定性评估器和 Verdict 规则决定。
