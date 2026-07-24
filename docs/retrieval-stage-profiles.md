# Retrieval Stage Profiles

## Discovery

允许 novelty、related work、negative result、dataset discovery 和 closest
prior work。输出 Discovery SourceSet、Coverage 和候选方向绑定。所有 Binding
均为背景、注意力或新颖性依据，`verdict_eligible=false`。

## Protocol

允许指标、基线、数据版本、评估方法、统计方法、官方实现和已知失效模式。
来源必须绑定 Research Contract 具体字段。若 Contract 已冻结，只产生
`potential_contract_change`，不得原地修改。

## Experimentation

只允许取得已批准数据、模型、固定 commit、官方文档或受控故障诊断。禁止
开放式 novelty 和结果导向的新指标/基线搜索。释放到 Sandbox 前必须检查
固定版本、哈希、来源、许可和 Contract Binding。

## Synthesis

只允许引用/DOI/元数据/撤稿/更正核验、参考补全和投稿规则。发现冲突时产生
warning 或 `evidence_conflict` 并建议 Repair，不修改历史 Verdict。

## Repair

只允许针对明确 failure 的方法、证据绑定、文献依据和运行环境诊断。必须
绑定 failure、diagnostic owner、earliest preventable phase、受影响 Artifact
和 Repair Contract。

当前真实纵切完整接通 Discovery。其他阶段已有正式 StepDefinition、领域接口
和策略；未接业务 Adapter 的节点确定性进入 `blocked/not_implemented`。
