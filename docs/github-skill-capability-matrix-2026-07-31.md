# Research Forge GitHub / Skill 能力矩阵（2026-07-31）

本表按 Research Forge 的产品能力汇总上游 GitHub、Skill、工具与标准。2026-07-31
起，营销式“已达到/部分达到”只作摘要，权威状态改由
`research_forge/capability_manifest.yaml` 的 C0–C5 证据等级决定：

- **C0**：只有概念或设计；**C1**：有实现；**C2**：组件验证；
- **C3**：受控端到端；**C4**：真实案例验证；**C5**：独立信任域验证。
- 只有 C3 以上可称平台能力“可用”，只有 C4 才能称真实案例已验证，C5 才能称独立验证。
- **未达到**：仅做过架构参考、尚无平台实现，或明确未采用。

“已达到”不表示兼容或嵌入了上游项目，只表示 Research Forge 已独立实现对应能力。

## 能力总表

| 能力领域 | 参考来源 | 当前状态 | Research Forge 已有能力 | 尚未达到的部分 |
|---|---|---|---|---|
| 四阶段工作流控制闭环 | AutoResearchClaw、AI Scientist、Agent Laboratory、AI-Researcher | **C3 受控端到端** | 项目/想法入口、四阶段 Study、持久步骤、Gate 和阶段交接已通过受控测试 | 只证明控制流闭环，不证明任意课题都能完成科学实验 |
| 实证科学完成闭环 | OpenML、AIRS、CodeScientist | **C2 组件验证** | 有限类型化 Profile 的资产、执行、评估与 Verdict 链已有组件测试 | 缺覆盖正负案例的统一能力基准、真实案例和独立复现 |
| 跨领域自主科研闭环 | AIRA Dojo、AI Scientist | **C1 已有扩展点，未达到** | 未知 Profile 会显式阻断，不会伪装成功 | 尚不能把任意研究设计自动编译并完成为有效实验 |
| Study、StepInstance 与持久 DAG | Nextflow、Snakemake、MLflow、DeepScientist | **已达到** | Study 隔离、步骤依赖、并行节点、暂停、恢复、重试及持久状态 | 不解析 Nextflow/Snakemake DSL，也不使用 MLflow 作为状态存储 |
| 本地项目包只读扫描 | Research Forge 自有实现 | **已达到** | 扫描代码、数据、报告、Office 文档、HDF5 元数据；排除缓存、驱动和共享二进制包 | 对所有专有二进制科研格式的语义解析仍不完整 |
| Claim 与候选方向发现 | AutoResearchClaw、Academic Research Suite | **已达到** | 作者主张、工件链、学术概念规范化、外部趋势与 Discovery Portfolio | 模型发现链仍需通过显式哈希绑定才能升级为正式证据 |
| Scope 与 Research Contract 版本化 | OSF、XScientist | **已达到** | Scope/Contract 冻结、vNext、字段差异、负责人审批、历史版本保留 | 未连接 OSF 注册服务，也不兼容 ARA 协议 |
| 阶段二可行性与 MVP | experiment-agent、OpenML | **C2 组件验证** | 形成假设、指标、基线/实验组、资源需求、最小冒烟验证和契约编译检查 | Contract Compiler 仍需更严格的可执行公式、资源语义和 Profile 覆盖 |
| 通用实验资产生成与执行 | CodeScientist、AIRA Dojo、autoresearch | **部分达到** | 受控执行、实验 Profile、生成代码/数据/评估器、运行矩阵、失败与构建状态分离 | 不能自动覆盖任意科学领域；无 Slurm/大规模集群和千 Agent 调度 |
| 独立评估与资格门 | OpenML、MLPerf、AIRS-Bench | **部分达到** | 机器可读结果、独立 evaluator、资格检查、AIRS 官方格式适配、确定性 Verdict | 没有 OpenML/MLPerf 官方兼容或排行榜验收；AIRS 缺完整官方矩阵验收 |
| 证据链与 provenance | XScientist、W3C PROV、RO-Crate | **部分达到** | Protocol→Run→Output→Evaluation→Claim 绑定；生成 PROV JSON-LD 与 RO-Crate 元数据 | 尚无外部标准 validator 验收，不宣称完整标准兼容 |
| 故障责任定位、Repair 与 successor run | AutoResearchClaw、XScientist | **已达到** | append-only 诊断、最早故障阶段、Repair Contract、受影响范围、successor run、历史 Verdict 不覆盖 | AI 只能建议故障来源；复杂跨系统依赖仍需负责人批准 |
| 产物依赖图与有界回滚 | DVC、Nextflow、Snakemake | **已达到** | 依据哈希、契约版本和依赖路径失效下游产物，只重跑受影响节点 | 不兼容 DVC/Nextflow/Snakemake 的缓存或远端存储 |
| PaperQA 全文证据检索 | PaperQA2 | **已达到** | 固定 PaperQA 版本、冻结 corpus/index、全文检索、EvidenceSpan、引用与哈希绑定；2026-07-31 实跑通过 | 不宣称普遍优于 PaperQA；完整答案仍走 Research Forge 的 Codex 合成层 |
| 公共网页、GitHub、开放全文检索 | Retrieval Gateway、ccf-deadlines | **部分达到** | 统一 Gateway、网络审批、查询清洗、响应冻结、GitHub/公开网页/开放全文 Provider | Provider 可用性依赖部署验收；机构资源与部分论文 Provider 仍受环境限制 |
| 批量证据合成与严格缓存 | PaperQA2 | **已达到** | 持久 synthesis worker、批量问题、Evidence Bundle、证据感知缓存键、句级引用检查、弃权 | 大规模真实多文献质量 benchmark 仍需继续扩充 |
| 科学有效性外部报告适配 | Evidently、Great Expectations | **部分达到** | 可读取并规范化负责人提供的冻结验证报告 | 平台没有安装或执行这两个上游工具 |
| ML 数据/模型通用验证套件 | Deepchecks | **未达到** | 仅完成能力与许可证审查 | 未安装、未执行；AGPL 边界尚未批准 |
| 盲化 AI 科学家面板 | Nuwa Scientist Panel | **部分达到** | 主张包盲化、三角色路由、哈希、veto/abstention 聚合与追加裁决队列 | 缺近期真实模型完整验收和专门的人类裁决 UI |
| 自然学术论文生成 | sci-ssci-skills、academic-humanizer、awesome-ai-research-writing | **已达到** | 自然学术标题、分层大纲、单段摘要、章节写作、claim-preserving 润色、内部代码词过滤 | 不能自动保证达到具体期刊录用质量；领域专家终审仍有价值 |
| 论文结构、数字和引用审计 | Academic Research Suite、sci-ssci-skills | **已达到** | 标题/摘要/章节结构、数字、引用、主张强度、内部标识泄漏和 evidence sufficiency 审计 | PRISMA、RAISE 等特定规范只有在正式配置并执行时才能声称 |
| AI 写作披露与作者声音 | academic-humanizer、Academic Research Suite | **部分达到** | AI disclosure、作者声音画像、语义差异与不改变证据的润色 | 尚未覆盖所有期刊的动态披露政策 |
| 可编辑科研图与导出 | draw.io | **已达到** | 生成和审计 `.drawio`，通过 draw.io Desktop 导出 SVG/PDF；2026-07-31 实跑通过 | 不提供 draw.io 实时多人协作 |
| 自然语言 AI 绘图编辑 | next-ai-draw-io | **部分达到** | MCP 配置、可编辑图产物和编辑后审计 | 缺近期真实 MCP 对话编辑端到端验收 |
| 会议推荐与截止日期提示 | ccf-deadlines | **部分达到** | 读取负责人提供的本地 checkout，叠加等级、周期和 deadline 元数据 | 社区数据不能替代官方 CFP，也不能自动授权投稿 |
| 可移植复现包 | Code Ocean、ACM Artifact Review | **部分达到** | 代码、数据、环境、运行入口、哈希、验证器、completion record 和本地 replay | 尚无 Code Ocean Capsule 或真正独立信任域复现；不能授予 ACM badge |
| PROV/RO-Crate 交换格式 | W3C PROV、Workflow Run RO-Crate | **部分达到** | 已生成受测试的 JSON-LD 映射 | 尚无外部 validator 和完整 Profile 认证 |
| 软件供应链签名与证明 | SLSA、Sigstore | **部分达到** | 自有 Ed25519 不可变记录、签名与验证器、签发和 worker 分离 | 不是 SLSA provenance；未接 Fulcio、Rekor、Cosign 或透明日志 |
| 跨运行研究记忆 | AutoResearchClaw、DeepScientist | **未达到** | 当前有项目内失败记忆、审计账本和历史 Study | 没有通用 Findings Memory、跨项目 lesson injection 或研究地图优化 |
| 实验树搜索与自动分支探索 | AI Scientist v2、DeepScientist | **未达到** | 有 successor、repair 和有界重试 | 没有 progressive tree search、Bayesian research map 或大规模候选搜索 |
| 通用 Skill loader | AutoResearchClaw | **未达到** | Codex 当前会话可以使用已安装 Skill | Research Forge 平台本身不会动态发现、校验和执行任意 `SKILL.md` |
| 微信公众号浏览 Skill | read-wechat-articles | **未达到** | Retrieval Provider 与该 Skill 是分开的平台能力 | 该 Skill 的 `SKILL.md` 仍是 TODO，没有可执行规范 |
| Self-play 科研 Skill | self-play | **未达到** | 只有 bounded self-play 示例 | 没有已安装、可复用、可审计的 self-play Skill |
| Manubot 文稿处理 | Manubot | **未达到** | 仅完成工具调研 | 无安装、无适配器、无运行验收 |
| 统计报告自动一致性检查 | statcheck | **未达到** | 仅完成工具调研 | 无 R 运行时集成或冻结报告适配器 |
| 外部开放科学注册 | OSF | **未达到** | 本地冻结契约采用相似注册语义 | 没有 OSF API、在线注册或公开存档 |
| 实验追踪平台兼容 | MLflow | **未达到** | Research Forge 有自有 Run/Artifact/Lineage | 无 MLflow Tracking、Model Registry 或数据互操作 |

## 总体判断

### 已达到的控制闭环与尚未完成的科学闭环

Research Forge 的四阶段**工作流控制闭环**达到 C3：Study、持久步骤、Gate、
阶段交接与失败保留能够在受控环境闭环。PaperQA 与 draw.io 各有真实运行记录，
达到其限定声明下的 C4。

Research Forge 的**实证科学完成闭环**目前只有 C2：有限实验 Profile 的组件链
存在，但还不能保证一个默认批准的真实项目会获得完整、可执行、可判定的实验。
**跨领域自主科研闭环**仅 C1，不能作为已经实现的产品能力宣传。

### 达到部分、最值得继续补齐

1. Contract Compiler、可执行指标公式与类型化实验 Profile；
2. Nuwa 真实模型验收和人工裁决 UI；
3. next-ai-draw-io 的真实 MCP 编辑闭环；
4. PROV/RO-Crate 外部 validator；
5. 真正独立信任域复现；
6. 多文献、大规模 PaperQA 质量 benchmark；
7. Provider 部署时的真实联网验收。

### 尚未达到、但不应立即全部实现

跨运行研究记忆、实验树搜索、通用 Skill loader、OSF/MLflow 官方集成、
Manubot、statcheck 与 Deepchecks 都有价值，但不是当前四阶段主闭环的必要条件。
在没有明确产品收益和许可证判断前，应继续保持“未达到”，避免为了功能数量
引入重型依赖。

机器审计命令：

```powershell
python scripts/audit_capability_registry.py --root .
```
