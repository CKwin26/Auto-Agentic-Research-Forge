# Paired Ablation of Pre-Delivery Claim-Evidence Gating in a Research Forge/Codex Autonomous Research Agent

## Status

**Provisional automated-evaluation manuscript. Human validation is deferred. This artifact is pipeline-complete but not publication-ready.**

- Analysis status: `provisional_human_validation_deferred`
- Human validation: `deferred`
- Preregistered primary analysis interpretable: `false`

## Abstract

End-to-end autonomous research agents can execute experiments and draft scientific conclusions, but their final claims may overstate source support or experimental evidence. We evaluated a pre-delivery claim-evidence gate in a frozen same-backbone paired ablation. The study used two arms, three CPU AIRS-lite tasks, three seeds, and exactly 18 cells. The protected automated evaluator estimated unsupported-claim rates of 30.5556% for baseline and 8.3333% for treatment, a treatment-minus-baseline difference of -22.22 percentage points. Across nine paired task-seed cells, the mean unsupported-claim-rate effect was -0.2222; the preregistered 10,000-resample hierarchical bootstrap interval was [-0.4167, -0.0833]. Total measured wall-clock time was 2721.13 seconds for baseline and 4514.38 seconds for treatment, a relative increase of 65.90%. Because the preregistered two-human audit is deferred, these effects remain provisional.

## Introduction

Autonomous research systems combine literature retrieval, experiment proposal, code execution, evaluation, and scientific writing. A central reliability problem is that a valid experiment run does not guarantee that every final prose claim is supported by the cited source or linked artifact.

Research question: On the fixed CPU AIRS-lite task suite and fixed Research Forge/Codex backbone, does adding a pre-delivery claim-evidence rejection/revision gate reduce unsupported factual conclusion claims in final outputs versus the same backbone without the gate, when model, non-gate prompts, retrieval set, runtime cap, iteration budget, and task order are held fixed?

Frozen hypothesis: Relative to the no-gate baseline, the gated treatment will lower unsupported_claim_rate on the pooled frozen claim registry and in at least 2 of 3 task packs, increase citation correctness and evidence coverage, lower experiment-detail error rate, and keep task-native score within an absolute 0.02 of baseline in each task pack.

The frozen Stage 1 review registered 12 verified sources spanning autonomous research agents, evidence verification, and research-agent reliability.

## Related Work and Registered Sources

The Stage 1 evidence boundary is a verified metadata/abstract review rather than a full-text systematic review. Sources below are the exact frozen registry used to frame the experiment.

- [paper-2a1fa8b99c181771] Nova: An Iterative Planning and Search Approach to Enhance Novelty and Diversity of LLM Generated Ideas (2024); verified by Exact Semantic Scholar paper-ID lookup matched the normalized title.
- [paper-2a7771f1bcf8adb2] ReasFlow: Assisting Reasoning-Centric Scientific Discovery in Applied Mathematics via a Knowledge-Based Multi-Agent System (2026); verified by Exact Semantic Scholar paper-ID lookup matched the normalized title.
- [paper-35ad838f0ac171a7] Retrieval augmented scientific claim verification (2024); verified by Exact Crossref DOI lookup matched the normalized title.
- [paper-3b6399a19264a862] Bayes-Entropy Collaborative Driven Agents for Research Hypotheses Generation and Optimization (2025); verified by Exact Semantic Scholar paper-ID lookup matched the normalized title.
- [paper-49947e5b991037d5] Heuresis: Search Strategies for Autonomous AI Research Agents Across Quality, Diversity and Novelty (2026); verified by Exact Semantic Scholar paper-ID lookup matched the normalized title.
- [paper-69c6e5faa8ef37c2] Evidence Coverage Evaluator: A Comprehensive Framework for Assessing Grounding Quality in Retrieval-Augmented Generation Systems (2026); verified by Exact Crossref DOI lookup matched the normalized title.
- [paper-6c621b0f059e2d3d] TriAgent: Automated Biomarker Discovery with Deep Research Grounding for Triage in Acute Care by LLM-Based Multi-Agent Collaboration (2025); verified by Exact Semantic Scholar paper-ID lookup matched the normalized title.
- [paper-8259991dea6aaecd] Autonomous Research Loops: An LLM-Agent Framework for End-to-End ML Experimentation, Manuscripting, and Self-Evaluation (2026); verified by Exact Crossref DOI lookup matched the normalized title.
- [paper-879e6a747eb8ceb6] Rethinking the AI Scientist: Interactive Multi-Agent Workflows for Scientific Discovery (2026); verified by Exact Semantic Scholar paper-ID lookup matched the normalized title.
- [paper-96217ebf4f10bd68] MedAgent: A Retrieval-Augmented Clinical Decision Support Agent with Verifiable Evidence Grounding for Evidence-Based Medicine (2026); verified by Exact Crossref DOI lookup matched the normalized title.
- [paper-965b07ca55b0b420] EvoScientist: Towards Multi-Agent Evolving AI Scientists for End-to-End Scientific Discovery (2026); verified by Exact Semantic Scholar paper-ID lookup matched the normalized title.
- [paper-e48872d23c4d9e23] RefLens: End-to-End Evidence-Grounded Citation Verification with LLM Agents (2026); verified by Exact Crossref DOI lookup matched the normalized title.

## Methods

### Study design

The preregistered study used a frozen 2-arm by 3-task by 3-seed paired design, for exactly 18 cells.

The treatment changed only the pre-delivery claim-evidence path by applying one reject-revise-recheck cycle; the baseline used the shared finalizer without verifier revision.

The three task packs were:

- `textualclassificationsickaccuracy`: Accuracy, baseline 0.568691, one candidate iteration, 120-second task timeout.
- `textualsimilaritysickspearmancorrelation`: SpearmanCorrelation, baseline 0.575719, one candidate iteration, 120-second task timeout.
- `coreferenceresolutionsupergluewscaccuracy`: Accuracy, baseline 0.634615, one candidate iteration, 120-second task timeout.

### Evidence and evaluation

Every final output was represented as a structured claim registry. Experiment claims carried run IDs, exact metric values, and artifact paths; literature and novelty claims carried exact source IDs. All 18 registries were assigned random blind IDs before hybrid structural and Codex semantic evaluation.

### Analysis

The primary proxy metric was unsupported-claim rate. Treatment-minus-baseline effects were paired by task and seed. The frozen analysis used a 10,000-resample hierarchical bootstrap and paired Cohen's dz. These statistics are reported as automated-evaluator estimates, not as human-validated truth.

## Results

### Arm-level automated-evaluator metrics

| Metric | Baseline | Treatment |
|---|---:|---:|
| Unsupported claim rate | 30.56% | 8.33% |
| Experiment-detail error rate | 33.33% | 5.56% |
| Citation correctness | 66.67% | 88.89% |
| Evidence coverage | 100.00% | 100.00% |
| Mean task-native score | 0.60057 | 0.62213 |
| Total wall-clock seconds | 2721.13 | 4514.38 |

The protected automated evaluator estimated unsupported-claim rates of 30.5556% for baseline and 8.3333% for treatment, a treatment-minus-baseline difference of -22.22 percentage points.

Across nine paired task-seed cells, the mean unsupported-claim-rate effect was -0.2222; the preregistered 10,000-resample hierarchical bootstrap interval was [-0.4167, -0.0833].

The automated experiment-detail error rate was 33.3333% for baseline and 5.5556% for treatment.

Automated citation correctness was 66.6667% for baseline and 88.8889% for treatment.

Mean task-native score was 0.60057 for baseline and 0.62213 for treatment in this bounded matrix.

Total measured wall-clock time was 2721.13 seconds for baseline and 4514.38 seconds for treatment, a relative increase of 65.90%.

Both arms retained all 36 final claims in the protected evaluation, so the observed automated reliability difference was not produced by lower final claim count.

### Task-level paired effects

| Task | Unsupported-claim-rate effect | Task-native-score effect |
|---|---:|---:|
| `textualclassificationsickaccuracy` | -0.2500 | 0.0647 |
| `textualsimilaritysickspearmancorrelation` | -0.2500 | 0.0000 |
| `coreferenceresolutionsupergluewscaccuracy` | -0.1667 | 0.0000 |

### Supplementary same-model adversarial review

A supplementary same-model scientist-persona panel reviewed 48 blinded claims and returned 37 supported, 11 unsupported, and 4 mixed-vote cases.

This panel is retained as an internal error-finding aid. Shared-model correlation prevents it from serving as independent validation.

## Discussion

Within the automated evaluation boundary, the gate was associated with fewer unsupported claims, fewer erroneous experimental details, and higher citation correctness. The retained claim count was unchanged, which argues against simple claim suppression as the sole explanation in this matrix. The main observed trade-off was runtime: verifier and revision work increased total wall-clock cost.

The paired mean effect was -0.2222, with bootstrap interval [-0.4167, -0.0833]. This numerical result is descriptive until the frozen human audit gate is resumed and completed.

## Limitations

The preregistered two-human blinded claim audit is deferred; consequently the primary analysis remains uninterpretable under the frozen protocol and every effect reported here is provisional.

The evidence comes from three CPU AIRS-lite task packs, three seeds, one frozen Codex backbone, and one claim-gate implementation; it does not establish universal generality.

Additional limitations:

- The protected semantic evaluator and experiment backbone both use Codex-family reasoning, so correlated model error is possible.
- Stage 1 is a bounded metadata/abstract review; full-text novelty assessment remains outstanding.
- The absolute 0.02 task-quality check treats positive improvements as deviations, so its frozen boolean is false even though no mean task-level degradation was observed.
- The study evaluates one implementation of a pre-delivery gate and does not isolate every verifier component.

## Conclusion

Research Forge completed a frozen 18-cell paired ablation and an evidence-bound provisional paper without converting automated judgments into a claim of human validation. The automated evidence is consistent with a reliability benefit from pre-delivery claim-evidence gating, accompanied by a substantial runtime cost. The next scientific step is external human review of this manuscript and, if desired, later resumption of the preregistered blinded claim audit.

## References

- [paper-2a1fa8b99c181771] Xiang Hu, Hongyu Fu, Jinge Wang, Yifeng Wang, Zhikun Li, Renjun Xu, Yu Lu, Yaochu Jin, Lili Pan, Zhenzhong Lan (2024). *Nova: An Iterative Planning and Search Approach to Enhance Novelty and Diversity of LLM Generated Ideas*. https://doi.org/10.48550/arxiv.2410.14255
- [paper-2a7771f1bcf8adb2] Yutong He, Daibo Li, Guohong Li, Jiahe Geng, Zhengyang Huang, Can Ren, Zekun Zhang, Yifan Liu, Shuchen Zhu, Hengrui Zhang, Boao Kong, Ming Sun, Shu Li, Chenyi Li, Jiang Hu, Kun Yuan, Zaiwen Wen, Pingwen Zhang (2026). *ReasFlow: Assisting Reasoning-Centric Scientific Discovery in Applied Mathematics via a Knowledge-Based Multi-Agent System*. https://www.semanticscholar.org/paper/82f8c2296bba3d06dfc1dc9291963cc0a30359a0
- [paper-35ad838f0ac171a7] Hao Liu, Ali Soroush, Jordan G Nestor, Elizabeth Park, Betina Idnay, Yilu Fang, Jane Pan, Stan Liao, Marguerite Bernard, Yifan Peng, Chunhua Weng (2024). *Retrieval augmented scientific claim verification*. https://doi.org/10.1093/jamiaopen/ooae021
- [paper-3b6399a19264a862] Shiyang Duan, Yuan Tian, Qi-Tao Bing, Xiaowei Shao (2025). *Bayes-Entropy Collaborative Driven Agents for Research Hypotheses Generation and Optimization*. https://doi.org/10.48550/arxiv.2508.01746
- [paper-49947e5b991037d5] Antonis Antoniades, Deepak Nathani, Ritam Saha, Alfonso Amayuelas, I. Bercovich, Zhaotian Weng, Vignesh Baskaran, Kunal Bhatia, W. Wang (2026). *Heuresis: Search Strategies for Autonomous AI Research Agents Across Quality, Diversity and Novelty*. https://www.semanticscholar.org/paper/ce30a6d54ba9a1bec69ca1a4e2d744b6be1479be
- [paper-69c6e5faa8ef37c2] Goutam Adwant, Manjari Srivastav (2026). *Evidence Coverage Evaluator: A Comprehensive Framework for Assessing Grounding Quality in Retrieval-Augmented Generation Systems*. https://doi.org/10.36227/techrxiv.176784505.56675782/v1
- [paper-6c621b0f059e2d3d] Kerem Delikoyun, Qianyu Chen, W. Kuan, J. Soong, M. Cove, Oliver Hayden (2025). *TriAgent: Automated Biomarker Discovery with Deep Research Grounding for Triage in Acute Care by LLM-Based Multi-Agent Collaboration*. https://doi.org/10.48550/arxiv.2510.16080
- [paper-8259991dea6aaecd] Alicem Koyun (2026). *Autonomous Research Loops: An LLM-Agent Framework for End-to-End ML Experimentation, Manuscripting, and Self-Evaluation*. https://doi.org/10.1145/3802133.3802134
- [paper-879e6a747eb8ceb6] L. Weidener, Marko Brki'c, Mihailo R. Jovanovic, Ritvik Singh, Chiara Baccin, E. Ulgac, A. Dobrin, A. Meduri (2026). *Rethinking the AI Scientist: Interactive Multi-Agent Workflows for Scientific Discovery*. https://doi.org/10.48550/arxiv.2601.12542
- [paper-96217ebf4f10bd68] Fuqiang Wang, Zhicai Guo, Zhikang Ye (2026). *MedAgent: A Retrieval-Augmented Clinical Decision Support Agent with Verifiable Evidence Grounding for Evidence-Based Medicine*. https://doi.org/10.64898/2026.06.15.26355735
- [paper-965b07ca55b0b420] Yougang Lyu, Xi Zhang, Xinhao Yi, Yuyue Zhao, Shuyu Guo, Wenxiang Hu, Jan Piotrowski, Jakub Kaliski, Jacopo Urbani, Zaiqiao Meng, Lu Zhou, Xiaohu Yan (2026). *EvoScientist: Towards Multi-Agent Evolving AI Scientists for End-to-End Scientific Discovery*. https://doi.org/10.48550/arxiv.2603.08127
- [paper-e48872d23c4d9e23] SeungHoo Lee, JuneHyoung Kwon, Jooweon Choi, JungMin Yun, Seunguk Yu, Yoonji Lee, Jinhee Jang, YoungBin Kim (2026). *RefLens: End-to-End Evidence-Grounded Citation Verification with LLM Agents*. https://doi.org/10.1609/aaai.v40i48.42361

## Reproducibility

- Protocol: `stage2-22e124e44294` revision 4
- Model/backend: `codex:gpt-5.4`
- Frozen contract digest: `aff2ee30de391ce867af1a3f5307d16764720964ac85fc79339e3c3d5123272c`
- Protected evaluation summary: `stage2/evaluations/summary.json`
- Provisional analysis: `stage2/provisional_analysis.json`
- Claim registry: `synthesis/claims.json`
- Synthesis audit: `synthesis/audit.json`
- Input hashes: `{"literature/stage1_manifest.json":"8f17b59446d9aab23e4266545724705cef5c39db3c7bc7e27d87681aa07f039f","stage2/backbone_manifest.json":"6ab9e5595188dacaa9b9b05c771c55254398b75b32985d34aa945ef32b738110","stage2/evaluations/summary.json":"e1502a4c59e57c01b64178c51069174e4522b755a7f24bb34f0d422447f72c59","stage2/manual_audit_manifest.json":"c40afad623de9624f7a780bc1fa73d893560644c2750429dfa67cfafa96187a5","stage2/operator_decisions.json":"dc9e53fd376e9bc070db60b82d54626b74b818f981a969911c8d138c29c9b2c5","stage2/persona_panel/panel-v1-full/final.json":"83b88309107359f4e86eab2bea19939bbf041d3a930ce1655539ce0bee077d2e","stage2/protocol.json":"265e1d9b3afb84e3dc680dbf350943123affcb7fee8a3bd8f0825620a911fcb4"}`
