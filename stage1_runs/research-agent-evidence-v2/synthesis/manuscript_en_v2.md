# Pre-Delivery Claim-Evidence Gating in Autonomous Research Agents: A Same-Family Protected-Evaluator Pilot

Anonymous Author (blind-review manuscript)

> Study status: This manuscript reports provisional results from a protected automated evaluator. The preregistered two-auditor sample is frozen, but human auditing has been deferred by the researcher. The proxy outcomes therefore cannot be interpreted as human validation, and the preregistered primary analysis remains uninterpretable.

## Abstract

End-to-end research agents can retrieve literature, execute code, and draft manuscripts, yet successful execution does not ensure that each claim in the delivered prose is supported by its cited source or linked artifact. We evaluate a pre-delivery claim-evidence gate that checks structured claims against frozen evidence, permits one constrained revision for unsupported or indeterminate claims, and then rechecks the result. The experiment freezes the Research Forge/Codex backbone, all non-gate prompts, the literature packet, task order, and runtime budgets. Its paired design contains two arms, three CPU AIRS-lite tasks, three seeds, and 18 cells. The protected automated evaluator labels 11 of 36 baseline claims and 3 of 36 gated claims as unsupported, corresponding to rates of 30.5556% and 8.3333%. Across nine task-seed pairs, the mean gated-minus-baseline effect is -0.2222; the 95% interval from 10,000 hierarchical bootstrap resamples is [-0.4167, -0.0833]. The gated arm also has a lower experiment-detail error rate and higher citation correctness, while both arms retain 36 final claims. Total measured wall-clock time increases by 65.90%. Because the arms came from separate stochastic runs executed in fixed order and the gate and evaluator share a model family, these estimates are internal-proxy associations rather than an identified causal effect. They also do not establish an independently human-validated reliability gain because the preregistered two-auditor assessment remains deferred.

**Keywords:** autonomous research agents; claim verification; evidence provenance; paired ablation; protected evaluation; claim-evidence fidelity

## 1 Introduction

Autonomous research systems increasingly connect several stages of scientific work into executable workflows. Existing systems can combine idea generation, literature retrieval, code modification, experiment execution, result interpretation, and manuscript drafting in one loop; some also introduce persistent memory, internal validation, or interactive human checkpoints [1-3]. This integration expands the range of work an agent can attempt, but it also concentrates the risk that an upstream error will propagate into the final scientific narrative. End-to-end systems have reported hallucinated experimental details, implementation failures, and metric misinterpretation [1]. Open-ended search work has also observed fabricated runs produced to obtain favorable scores [4]. Executable code and available run logs therefore do not, by themselves, establish that the claims in a delivered manuscript match their evidence.

Prior work follows two broadly complementary directions. One improves search, collaboration, and self-evolution so that research agents can generate more candidates, reuse prior experience, or operate in theoretical and domain-specific settings [2,5-9]. The other verifies scientific claims, retrieves supporting evidence, or checks citations at claim level [10-12]. The first direction typically evaluates artifact quality, execution success, or domain task performance; the second often appears as a standalone verifier or a domain-specific component. Within the frozen set of 12 metadata- and abstract-level sources used in this study, these lines of work do not provide a same-backbone, same-task, same-budget paired ablation that isolates a claim-evidence gate immediately before delivery. This statement is limited to the frozen source set and is not a comprehensive novelty claim.

We ask the following question: on CPU AIRS-lite tasks and a fixed Research Forge/Codex backbone, is a pre-delivery reject-revise-recheck gate associated with a lower protected-evaluator rate of unsupported factual claims than a no-gate version when the model, non-gate prompts, retrieval set, runtime limits, iteration budget, and task order are held fixed? The study does not introduce a new foundation model or reasoning architecture. It treats one delivery component as the planned intervention. The primary outcome is the proportion of unsupported claims in the final claim registry. Secondary outcomes include experiment-detail error rate, citation correctness, evidence coverage, task-native score, claim retention, and wall-clock time. Because the arms were not generated as counterfactual branches of identical run artifacts, the analysis does not identify a causal gate effect.

## 2 Related Work

### 2.1 End-to-end research agents and open-ended search

Recent end-to-end research agents have primarily expanded workflow coverage and search capability. Autonomous Research Loops links hypothesis generation, literature retrieval, coding, experimentation, manuscript preparation, and simulated review, while also reporting hallucinated experimental details, incorrect implementations, and metric misreadings [1]. EvoScientist uses research, engineering, and evolution-management roles with persistent memory to learn from prior successes and failures [2]. Interactive multi-agent workflows maintain a persistent world state and add human checkpoints to shorten the researcher feedback cycle [3]. Together, these systems show that research agents are no longer only code generators. They also show why workflow completeness cannot be assumed to imply reliable final claims.

Several influential systems identified during manuscript review were not part of the 12-source packet frozen for the experiment. The AI Scientist connects idea generation, coding, experimentation, paper writing, and simulated review [13], while The AI Scientist-v2 replaces hand-authored templates with agentic tree search and adds iterative visual review [14]. Agent Laboratory organizes literature review, experimentation, and report writing while allowing structured human feedback [15]. We cite these systems to provide post-freeze field context; they were not used to define the preregistered intervention, novelty statement, or evidence packet. Their placement of review at the paper or workflow level complements the claim-level delivery gate studied here.

Work on ideation and open-ended search further exposes the tension among quality, novelty, and truthfulness. Nova uses iterative planning and external knowledge retrieval to increase idea diversity [5]. HypoAgents applies Bayesian updating and information entropy to select uncertain hypotheses for revision [6]. Heuresis compares quality-diversity search strategies and reports 40 fabricated behaviors across 1,628 scoring runs [4]. These systems focus on candidate research directions or search trajectories. Our study instead focuses on whether atomic claims in the delivered text are supported by frozen evidence.

Research agents have also been applied to mathematical reasoning and clinical tasks. ReasFlow integrates literature synthesis, algorithm design, theorem proving, experiments, manuscript preparation, and internal logical audits [7]. TriAgent and MedAgent combine multi-agent research with clinical evidence retrieval, stratification, and answer generation [8,9]. These domain systems underscore the need for evidence grounding, but their objectives, training procedures, and evaluation settings differ from the same-backbone intervention studied here.

### 2.2 Claims, evidence coverage, and citation verification

Claim-level verification decomposes an answer into checkable units and classifies whether retrieved material supports, contradicts, or fails to determine each unit. CliVER retrieves and selects sentences from PubMed abstracts before classifying clinical scientific claims as supported, contradicted, or neutral [10]. Evidence Coverage Evaluator separates evidence coverage from surface correctness and combines fine-grained claim extraction, evidence retrieval, and natural-language inference or language-model judgment [11]. RefLens extracts passages from original PDFs and presents citation cards and paper-level verification reports [12]. These systems provide independent ways to assess whether a claim matches its evidence.

The present study moves that idea to the delivery boundary of an autonomous research workflow and makes it the only planned intervention. Both arms use the same conclusion-generator specification to produce structured claims, although they operate on separate stochastic upstream runs. Only the gated arm then receives one cycle of rejection, constrained revision, and rechecking. The design does not compare two complete agent systems or test which foundation model is stronger. It narrows the intervention surface to examine the relationship between claim-level evidence checking and a proxy measure of claim-evidence fidelity.

## 3 Methods

### 3.1 Preregistered design and experiment matrix

Protocol `stage2-22e124e44294` was frozen before execution. The paired design was 2 arms (baseline and gated) x 3 tasks x 3 seeds, for 18 cells. Seeds were 0, 1, and 2; baseline and gated runs were paired by task and seed. The study stopped after exactly 18 cells. It did not permit task expansion, candidate reordering, or selective reruns in response to interim results. Integrity conditions required consistent model and literature manifests, schema-valid claim registries, resolvable run IDs and artifacts for experimental claims, exact source IDs for literature claims, and an unchanged blind-evaluation configuration.

The frozen execution order ran all nine baseline cells before the nine gated cells rather than interleaving arms at random. This order was part of the protocol, but it allows time-order or backend nondeterminism to contribute to the measured arm difference. We treat this as an internal-validity limitation.

![Study design](../../../output/pdf/figures/study_design.png)

Figure 1. Paired ablation pathway. Both arms share the research backbone, task configuration, literature packet, and initial conclusion generator. The only planned difference occurs in the pre-delivery claim-evidence path. Final registries from both arms enter protected evaluation under randomized blind identifiers. The preregistered two-auditor sample is frozen, but auditing is deferred at this stage.

### 3.2 Research backbone and controlled runtime

The experimental backbone was the Research Forge controller with a `codex:gpt-5.4` backend. The controller enabled candidate deduplication and failure diagnosis, used a candidate-pool size of 1, allowed at most 2 proposals per iteration, and imposed a one-iteration task limit. The runtime limit was 120 seconds per task. Experimental code ran in the `rf-airs-cpu:v1` Docker image with one CPU, 2,048 MB memory, a read-only root filesystem, no network access, and no-new-privileges enabled. The manifest froze the Python version and principal dependencies. The document-production environment was separate from the experimental runtime.

The three AIRS-lite tasks were SICK text classification accuracy, SICK semantic similarity measured by Spearman correlation, and SuperGLUE WSC coreference accuracy. Table 1 reports the reference baseline and resource limit frozen in each task pack. Because the native metrics are not commensurate across tasks, their cross-task average is descriptive and is not used to infer general task capability.

| Task | Native metric | Frozen reference baseline | Cell limit (s) |
|---|---:|---:|---:|
| SICK text classification | Accuracy | 0.568691 | 120 |
| SICK semantic similarity | SpearmanCorrelation | 0.575719 | 120 |
| SuperGLUE WSC coreference | Accuracy | 0.634615 | 120 |

### 3.3 Pre-delivery claim-evidence gate

The shared conclusion generator received only completed runs, protected run records, frozen task descriptions, and the frozen literature packet. It produced a structured claim registry. Experimental claims had to include an exact run ID, metric name, value, and artifact path. Literature and novelty claims had to include frozen source IDs, and novelty comparisons were restricted to the reviewed source set. The generator was prohibited from inferring unreported metrics, causal mechanisms, statistical significance, or generality.

After generation, the baseline arm delivered its registry without verifier-driven rejection or revision. In the gated arm, a semantic verifier labeled each claim as supported, unsupported, or indeterminate using only the linked frozen evidence. Unsupported or indeterminate claims could be revised once. A revision could not add sources, runs, metrics, artifacts, or claims. The system then rechecked the revision; a claim that still failed was removed according to protocol. Because judgment, constrained revision, and rechecking were bundled, the present experiment cannot attribute the observed effect to one subcomponent.

### 3.4 Protected evaluation and outcomes

The 18 final registries received blind identifiers before the arms were revealed. The evaluator first performed deterministic structural checks, including whether source IDs, run IDs, metric values, and artifact paths resolved. A claim that failed a required structural check was labeled unsupported. If the structure passed, a Codex semantic evaluator viewed only the frozen evidence packet and labeled the complete atomic claim as supported, unsupported, or indeterminate. Its prompt prohibited external knowledge and inference of the study arm from metadata.

The term *protected evaluation* refers to specific access and masking controls, not to statistical or model-family independence. The protection properties are summarized below.

| Property | Status | Residual limitation |
|---|---|---|
| Arm labels replaced by blind IDs | Implemented | Final wording may still carry arm-correlated style cues |
| Semantic evaluator restricted to frozen evidence | Implemented | Frozen evidence may be incomplete or scientifically weak |
| Source, run, metric, and artifact references checked deterministically | Implemented | Structural validity does not imply semantic support |
| Evaluator independent of the generator's model family | Not implemented | Generator, gate, and evaluator use Codex-family reasoning |
| Human calibration of unsupported labels | Deferred | Frozen two-auditor sample has not been judged |
| Gated and ungated conclusions branch from identical run artifacts | Not implemented | Arms use separate stochastic runs in fixed order |

Let $N$ denote the number of final claims and $N_u$ the number labeled unsupported. The primary proxy outcome was $N_u/N$. Citation correctness was the proportion of literature and novelty claims labeled supported. Experiment-detail error rate was the proportion of experimental claims labeled unsupported. Evidence coverage was the proportion of claims that passed every deterministic structural check. Task-native scores came directly from task records, and runtime was the sum of cell wall-clock times. These outcomes are estimates from the protected automated evaluator, not human ground truth.

### 3.5 Statistical analysis and deferred human audit

The treatment effect was defined as the gated unsupported-claim rate minus the baseline rate for the same task and seed, so negative values favor the gate. We averaged the nine paired effects and calculated paired Cohen's $d_z$. The hierarchical bootstrap first resampled the three tasks with replacement and then resampled the three seed effects within each sampled task. The protocol fixed 10,000 resamples, a random seed derived from the protocol identifier, and the 2.5th and 97.5th percentiles as the reported interval.

The preregistered audit requires two independent auditors to judge 48 blinded claims, with eight claims sampled from each task-by-arm stratum. Only 14 automated-unsupported claims were available, so the amended frozen rule includes all 14 and fills the remaining 34 slots in deterministic order. The sample and its hashes are frozen, but neither auditor has begun work. Human validation is deferred at the researcher's direction; the automated effect therefore does not unlock the preregistered primary analysis.

## 4 Results

### 4.1 Unsupported-claim rate

The protected evaluator labeled 11 of 36 final baseline claims and 3 of 36 final gated claims as unsupported. The corresponding rates were 30.5556% and 8.3333%, a group-level difference of -22.22 percentage points. Across the nine task-seed pairs, the mean effect was -0.2222 and paired Cohen's $d_z=-0.8433$. The median of the 10,000 hierarchical bootstrap estimates was -0.2222, with a 95% interval of [-0.4167, -0.0833].

Mean paired effects had the same direction in all three tasks: -0.2500 for SICK classification, -0.2500 for SICK similarity, and -0.1667 for WSC coreference. Each registry contained only four claims, so a cell-level rate changed in steps of 0.25. The agreement in direction should not be interpreted as precise stability of task-level effects. Figure 2B displays the task effects and the overall bootstrap interval.

![Protected evaluation results](../../../output/pdf/figures/protected_results.png)

Figure 2. Protected automated evaluation. Panel A compares primary and secondary rate outcomes by arm. Panel B shows gated-minus-baseline paired effects for the three tasks; the red interval is the 95% hierarchical bootstrap interval for the overall effect. This interval describes the frozen automated proxy and is not a confidence interval for a human-audited effect.

All nine paired observations are reported below because the cell-level outcome is discrete. Leave-one-task-out mean effects were -0.2083 without SICK classification, -0.2083 without SICK similarity, and -0.2500 without WSC. The direction is unchanged, but three task clusters are too few to treat this check as evidence of precise generalization.

| Task | Seed | Baseline rate | Gated rate | Paired effect |
|---|---:|---:|---:|---:|
| SICK classification | 0 | 0.25 | 0.25 | 0.00 |
| SICK classification | 1 | 0.25 | 0.00 | -0.25 |
| SICK classification | 2 | 0.50 | 0.00 | -0.50 |
| SICK similarity | 0 | 0.25 | 0.25 | 0.00 |
| SICK similarity | 1 | 0.25 | 0.25 | 0.00 |
| SICK similarity | 2 | 0.75 | 0.00 | -0.75 |
| WSC coreference | 0 | 0.25 | 0.00 | -0.25 |
| WSC coreference | 1 | 0.25 | 0.00 | -0.25 |
| WSC coreference | 2 | 0.00 | 0.00 | 0.00 |

### 4.2 Secondary proxy outcomes and claim retention

Experiment-detail error rate decreased from 33.3333% in the baseline arm to 5.5556% in the gated arm, while citation correctness increased from 66.6667% to 88.8889%. Evidence coverage was 100% in both arms. That measure establishes only that final claims carried resolvable structured evidence links; it does not establish semantic support. The evaluator returned one indeterminate claim in the baseline arm and none in the gated arm.

Both arms retained all 36 initial claims as final claims, for a retention rate of 100%. The group-level difference was therefore not produced by a direct reduction in final claim count. Equal counts do not, however, rule out narrower wording or reduced claim strength in the gated arm. Distinguishing deletion, correction, and semantic contraction would require separate coding of the revision traces.

| Protected automated outcome | Baseline | Gated |
|---|---:|---:|
| Unsupported-claim rate | 30.56% (11/36) | 8.33% (3/36) |
| Experiment-detail error rate | 33.33% | 5.56% |
| Citation correctness | 66.67% | 88.89% |
| Evidence coverage | 100.00% | 100.00% |
| Final claim count | 36 | 36 |
| Indeterminate claim count | 1 | 0 |

### 4.3 Task scores and runtime cost

Mean SICK classification score across three seeds changed from 0.5914 to 0.6561, a difference of +0.0647. SICK similarity and WSC coreference means remained 0.5757 and 0.6346 in both arms. The frozen protocol required the absolute mean difference for every task to be no greater than 0.02. That Boolean check was false because SICK classification had a positive difference, not because all three tasks deteriorated. The arms consisted of independent stochastic runs, and the gate acted only at conclusion delivery; we therefore do not interpret the positive difference as improved task capability caused by the gate.

Total wall-clock time was 2,721.13 seconds in the baseline arm and 4,514.38 seconds in the gated arm. The increase was 1,793.26 seconds, or about 29.89 minutes, corresponding to 65.90%. The gated arm took longer on SICK classification and similarity and slightly less time on WSC. The aggregate cost indicates that the current implementation trades additional semantic judgment and revision work for lower error rates under the protected proxy evaluator. The experiment does not establish whether that cost remains acceptable at larger scale.

Wall-clock time is defined from each `cell_manifest.created_at` timestamp to the corresponding `complete.completed_at` timestamp and therefore includes the controller, conclusion-generation, and, when applicable, gate path within a cell. The frozen records do not provide complete token, model-call, monetary, energy, or human-labor accounting, so the 65.90% difference should not be interpreted as a full cost estimate.

| Task | Baseline mean | Gated mean | Gated - baseline |
|---|---:|---:|---:|
| SICK classification Accuracy | 0.5914 | 0.6561 | +0.0647 |
| SICK similarity SpearmanCorrelation | 0.5757 | 0.5757 | 0.0000 |
| WSC coreference Accuracy | 0.6346 | 0.6346 | 0.0000 |

### 4.4 Same-model scientist-persona panel

As a supplementary error-finding instrument, a same-model scientist-persona panel reviewed 48 blinded claims. Its aggregate verdicts were 37 supported and 11 unsupported, with mixed votes on 4 claims. Because the panel and the research backbone share a model family, the panel does not provide an independent error source. We use its findings only to surface disputed claims, not as human validation, cross-model replication, or primary-analysis evidence.

## 5 Discussion

### 5.1 Interpretation within the automated evaluation boundary

Under the frozen proxy evaluator, pre-delivery claim-evidence gating was associated with a lower unsupported-claim rate, lower experiment-detail error rate, and higher citation correctness. Mean paired effects were negative for all three tasks, and both arms retained the same number of final claims. This supports a limited conclusion: the lower proxy error rate was not achieved solely by deleting a large number of claims. It does not show that informativeness was preserved. Equal counts are compatible with factual correction, appropriate qualification, or semantic contraction, and the present traces have not been coded to distinguish them. The experiment also does not identify which gate subcomponent produced the difference or exclude verifier preference and correlated same-model errors.

Two upstream design choices set a hard ceiling on that interpretation. First, generation, gating, and semantic evaluation use Codex-family reasoning without completed independent calibration. Second, the two arms are separate stochastic runs rather than alternative delivery branches from identical upstream artifacts. Together, these choices limit the result to an internal proxy association: manuscript wording can disclose that ceiling, but it cannot convert the estimate into a causal or independently validated reliability effect.

The largest change concerned experimental-detail claims. These claims carried exact run IDs, metric names, values, and artifact paths, which made inconsistencies amenable to deterministic checks. Literature and novelty claims required less determinate semantic entailment judgments. This error pattern suggests a practical architecture in which deterministic rules first handle identifiers, values, and artifact integrity, while semantic evaluation is reserved for high-risk or ambiguous claims. This is a design implication from the observed error structure, not a tested comparison with another gate implementation.

### 5.2 Proxy-fidelity and cost trade-off

The 65.90% increase in total runtime shows that the claim-evidence gate was not a cost-free addition. The present gate performs semantic judgment for every claim and allows one revision and recheck. Cost may continue to grow with the number of claims, literature sources, and experimental artifacts. Future implementations could test risk-based routing, batched structural checks, cached semantic judgments, and rechecking only modified claims. None of these optimizations was evaluated here.

Task-native scores did not show consistent deterioration, but they also cannot establish that the gate has no effect on task quality. The gate occurs after task execution and should not alter completed task results in principle. In practice, the arms used separate stochastic runs, and SICK classification varied across seeds. A stronger design would generate gated and ungated conclusions from the same run artifacts, or at least interleave study arms, to separate conclusion-gating effects from upstream run variability.

### 5.3 Threats to validity

Measurement validity is the principal limitation. The protected evaluator, gate verifier, and research backbone all use Codex-family reasoning and may share semantic preferences or blind spots. Empirical work has documented self-preference in LLM-as-a-judge settings [16], although that finding does not demonstrate the same bias in this experiment. The hierarchical bootstrap interval reflects paired variation under this evaluator's definition. Until the two-auditor assessment is complete, the rates of 30.5556% and 8.3333% cannot be treated as true error rates, and the preregistered hypothesis cannot be declared supported.

The measured construct is claim-evidence fidelity, not scientific validity. A claim can be faithful to a frozen source while the source is incomplete, methodologically weak, or irrelevant to a broader scientific inference. Atomic support checks also do not detect omitted counterevidence or failures in argument-level coherence. The gate should therefore be treated as one manuscript control rather than a substitute for scientific review.

Internal validity is limited by execution order, sample size, and the bundled intervention. All nine baseline cells ran before the nine gated cells rather than in randomized interleaving. Each task had only three seeds, each registry only four claims, and rejection, revision, and rechecking appeared as one package. This design is sufficient to exercise the engineering loop, but not to estimate stable effects for individual components. The fixed absolute-0.02 task-quality check also treats a positive improvement as a deviation, so its Boolean output cannot be interpreted directly as a quality failure.

External validity is similarly narrow. The experiment covers three CPU text tasks, one Codex backbone, one gate implementation, a candidate-pool size of 1, and one iteration. The findings do not generalize to laboratory science, multimodal data, long-horizon training, other foundation models, or disciplines with different evidence structures. The Stage 1 review used only 12 verified metadata and abstract records. It therefore supports positioning within that frozen collection, not exhaustive coverage or a claim of priority.

### 5.4 Next validation step

The immediate next step is not a larger automated review panel. It is completion of the frozen blind audit by two independent auditors and calculation of the protected evaluator's false-positive rate for unsupported labels under the preregistered rule. If that rate exceeds 0.15, the primary analysis is invalid under the protocol. Subsequent work should use an evaluator from a different model family, interleave arms, expand the task and seed set, and separate verification-only, verification-plus-revision, and verification-plus-revision-plus-recheck conditions. Only then can the observed proxy difference be tested as a reproducible human reliability gain.

## 6 Conclusion

Across 18 frozen cells, pre-delivery claim-evidence gating was associated with a reduction in the protected evaluator's unsupported-claim rate from 30.5556% to 8.3333%. The mean effect across nine pairs was -0.2222, with a 95% hierarchical bootstrap interval of [-0.4167, -0.0833]. The gated arm did not reduce final claim count, but total runtime increased by 65.90%. These results justify further study of claim-level gating as an internal proxy intervention. They do not identify a causal gate effect or establish human-validated reliability. The scientific conclusion remains provisional until a same-artifact design and independent evaluation are available and the preregistered two-auditor blind assessment is resumed and completed.

## Data and Code Availability

The protocol, frozen manifests, 18 run cells, blind-evaluation summary, provisional analysis, claim registry, and synthesis audit are stored under `stage1_runs/research-agent-evidence-v2/`. Principal entry points are `stage2/protocol.json`, `stage2/evaluations/summary.json`, `stage2/provisional_analysis.json`, `synthesis/claims.json`, and `synthesis/audit.json`. The human-audit sample resides in a protected blind directory; the arm mapping remains undisclosed until auditing is complete.

## Ethics, Funding, and Competing Interests

The current experiment involved no human participants, animals, or sensitive personal data. The preregistered human work consists only of blinded judgments by two independent claim auditors, which has not begun. No external funding information was provided, and no competing interests were reported.

## Author Contributions and AI Use Disclosure

The anonymous author formulated the research question, approved the protocol, supervised the experiment, and interpreted the results. Research Forge/Codex tools supported literature retrieval and verification, experimental control, code execution, protected evaluation, provisional statistical analysis, manuscript structuring, writing, and typesetting. The protected evaluator is part of the study method, not an independent human reviewer. The author retains responsibility for the submitted content and its accuracy.

## References

1. Koyun, A. (2026). *Autonomous Research Loops: An LLM-Agent Framework for End-to-End ML Experimentation, Manuscripting, and Self-Evaluation*. https://doi.org/10.1145/3802133.3802134
2. Lyu, Y., Zhang, X., Yi, X., et al. (2026). *EvoScientist: Towards Multi-Agent Evolving AI Scientists for End-to-End Scientific Discovery*. https://doi.org/10.48550/arxiv.2603.08127
3. Weidener, L., Brkić, M., Jovanovic, M. R., et al. (2026). *Rethinking the AI Scientist: Interactive Multi-Agent Workflows for Scientific Discovery*. https://doi.org/10.48550/arxiv.2601.12542
4. Antoniades, A., Nathani, D., Saha, R., et al. (2026). *Heuresis: Search Strategies for Autonomous AI Research Agents Across Quality, Diversity and Novelty*. https://www.semanticscholar.org/paper/ce30a6d54ba9a1bec69ca1a4e2d744b6be1479be
5. Hu, X., Fu, H., Wang, J., et al. (2024). *Nova: An Iterative Planning and Search Approach to Enhance Novelty and Diversity of LLM Generated Ideas*. https://doi.org/10.48550/arxiv.2410.14255
6. Duan, S., Tian, Y., Bing, Q.-T., & Shao, X. (2025). *Bayes-Entropy Collaborative Driven Agents for Research Hypotheses Generation and Optimization*. https://doi.org/10.48550/arxiv.2508.01746
7. He, Y., Li, D., Li, G., et al. (2026). *ReasFlow: Assisting Reasoning-Centric Scientific Discovery in Applied Mathematics via a Knowledge-Based Multi-Agent System*. https://www.semanticscholar.org/paper/82f8c2296bba3d06dfc1dc9291963cc0a30359a0
8. Delikoyun, K., Chen, Q., Kuan, W., et al. (2025). *TriAgent: Automated Biomarker Discovery with Deep Research Grounding for Triage in Acute Care by LLM-Based Multi-Agent Collaboration*. https://doi.org/10.48550/arxiv.2510.16080
9. Wang, F., Guo, Z., & Ye, Z. (2026). *MedAgent: A Retrieval-Augmented Clinical Decision Support Agent with Verifiable Evidence Grounding for Evidence-Based Medicine*. https://doi.org/10.64898/2026.06.15.26355735
10. Liu, H., Soroush, A., Nestor, J. G., et al. (2024). Retrieval augmented scientific claim verification. *JAMIA Open*. https://doi.org/10.1093/jamiaopen/ooae021
11. Adwant, G., & Srivastav, M. (2026). *Evidence Coverage Evaluator: A Comprehensive Framework for Assessing Grounding Quality in Retrieval-Augmented Generation Systems*. https://doi.org/10.36227/techrxiv.176784505.56675782/v1
12. Lee, S., Kwon, J., Choi, J., et al. (2026). RefLens: End-to-End Evidence-Grounded Citation Verification with LLM Agents. *Proceedings of the AAAI Conference on Artificial Intelligence, 40*(48). https://doi.org/10.1609/aaai.v40i48.42361
13. Lu, C., Lu, C., Lange, R. T., Foerster, J., Clune, J., & Ha, D. (2024). *The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery*. https://doi.org/10.48550/arXiv.2408.06292
14. Yamada, Y., Lange, R. T., Lu, C., Hu, S., Lu, C., Foerster, J., Clune, J., & Ha, D. (2025). *The AI Scientist-v2: Workshop-Level Automated Scientific Discovery via Agentic Tree Search*. https://doi.org/10.48550/arXiv.2504.08066
15. Schmidgall, S., Su, Y., Wang, Z., et al. (2025). *Agent Laboratory: Using LLM Agents as Research Assistants*. https://doi.org/10.48550/arXiv.2501.04227
16. Wataoka, K., Takahashi, T., & Ri, R. (2024). *Self-Preference Bias in LLM-as-a-Judge*. https://doi.org/10.48550/arXiv.2410.21819
