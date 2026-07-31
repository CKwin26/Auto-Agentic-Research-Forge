# W3C PROV 外部语义引擎验收

日期：2026-07-31

## 结论

Research Forge 的受限 Stage 3 provenance 投影已通过 PySHACL 0.40.1 的外部语义校验。校验同时执行确定性引用检查和 SHACL 约束，报告冻结在被校验来源之外。

## 验收证据

- 示例来源：`examples/prov-validation/stage3-prov.jsonld`
- 来源 SHA-256：`bef910c04215eda0a31c88f4d5e5231fdf8205d8bdc5b757542866a1f011c39c`
- 外部报告：验收运行生成的 `prov-report.json`（运行产物不提交到仓库）
- 报告文件 SHA-256：`74e01e246cb4dfd282ac9a95b81d92505406a84f9be0e82584736daf7e242b2e`
- 规范化报告主体 SHA-256：`b442197c2c2e8dc0ef4edce267222b6eae2da2b9cdaf83d6a523ad1c5a6969`
- RDF 三元组数：7
- 结果：`conforms=true`。

## 平台落点

- `research_forge/prov_validation.py`：把受限 Research Forge 投影转换为 RDF，执行确定性检查与 PySHACL。
- `scripts/validate_prov_external.py`：独立验收入口。
- `tests/test_prov_validation.py`：覆盖真实导出、缺失生成实体和证据写入源目录等负例。

## 允许声称与边界

允许声称：Research Forge 的受限 Stage 3 PROV 投影已通过外部 PySHACL 语义引擎。

不能声称：这不是 W3C 官方认证，也不是对全部 PROV-CONSTRAINTS 的覆盖；Research Forge 内部契约、工件依赖图和 Verdict 记录仍是平台事实来源。
