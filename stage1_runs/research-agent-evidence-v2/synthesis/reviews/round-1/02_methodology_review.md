# Methodology Review Report (Peer Reviewer 1)

## Reviewer Identity

Quantitative ML evaluation researcher specializing in paired experiments, stochastic agent systems, hierarchical resampling, and evaluator reliability.

## Overall Recommendation

**Major Revision**

## Confidence Score

**5/5**

## Summary Assessment

The paper reports a preregistered 2-by-3-by-3 paired systems experiment and a protected automated evaluation of 72 final claims. Several design choices are strong: the stopping rule was fixed, task-seed pairs are explicit, structural evidence links are checked deterministically, and the analysis reports both a paired effect size and a hierarchical bootstrap interval. The core methodological limitation is that the measured outcome is not independent of the intervention. Generator, gate verifier, and semantic evaluator use the same model family, while the human audit that would calibrate the evaluator is deferred. The arm contrast is also contaminated by separate stochastic upstream runs and a fixed baseline-then-gated execution order. The positive SICK classification difference confirms that the arms differed upstream despite the gate being downstream of task execution. With only four claims per registry, each cell outcome is highly discrete, and task-mean plots conceal the nine raw paired values. The study is a credible engineering pilot, but it cannot currently identify a reliability effect of the gate. A revised design using shared run artifacts, interleaved or randomized delivery conditions, independent evaluation, and complete pair-level reporting would substantially strengthen the inference.

## Strengths

1. **Frozen stopping and pairing rules:** The study stops after exactly 18 cells and pairs arms by task and seed (p. 3), limiting optional stopping and selective reruns.
2. **Explicit outcome definitions:** Unsupported-claim rate, citation correctness, experiment-detail error, and evidence coverage are operationally defined (p. 4).
3. **Appropriate emphasis on effect estimation:** The paper reports the mean paired effect, Cohen's $d_z$, and a bootstrap interval rather than relying on a binary significance claim (p. 5).
4. **Validity threats are disclosed:** Fixed execution order, small registries, bundled intervention, and model-family dependence are acknowledged (p. 8).

## Weaknesses

### W1: Same-family outcome measurement

**Problem:** The protected semantic evaluator, intervention verifier, and research backbone all rely on Codex-family reasoning, and the independent two-auditor audit has not begun (pp. 5 and 8).  
**Why it matters:** A lower unsupported label rate may reflect family-specific semantic preference or familiarity rather than improved evidential support. This is the primary outcome, so correlated measurement error directly affects the central inference.  
**Suggestion:** Complete the frozen human audit and report evaluator confusion metrics; add an evaluator from another model family; retain the current result only as a proxy until calibration is available.  
**Severity:** Major. TMLR's official criterion requires claims to be supported by accurate, convincing, and clear evidence; the issue crosses that boundary because the manuscript's main estimate depends on the uncalibrated evaluator itself. [TMLR Acceptance Criteria](https://jmlr.org/tmlr/acceptance-criteria.html)

### W2: The paired contrast does not isolate the gate

**Problem:** Baseline and gated cells are separate stochastic executions, and all baseline runs precede all gated runs (pp. 3 and 8).  
**Why it matters:** The measured difference can include time-order effects, backend nondeterminism, different upstream task outputs, and different initial claims. The +0.0647 SICK classification difference is direct evidence that upstream outcomes differed (pp. 6-7).  
**Suggestion:** For each task-seed run artifact, branch into ungated and gated conclusion paths. If new execution is impossible, weaken causal language and report the study as a sequential paired implementation comparison.  
**Severity:** Major because it affects identification of the intervention, not merely reporting style.

### W3: Pair-level resolution is hidden

**Problem:** The paper shows three task means and one overall interval, but not the nine raw paired effects. Each registry has only four claims, so effects occur in coarse 0.25 increments (p. 5).  
**Why it matters:** With three tasks and three seeds, readers need the complete discrete outcome pattern to judge heterogeneity and bootstrap stability.  
**Suggestion:** Add a table of all nine baseline rates, gated rates, and paired differences; report a leave-one-task-out sensitivity analysis and the bootstrap seed.  
**Severity:** Major for transparency, but fixable without new experiments.

### W4: The treatment is bundled

**Problem:** Verification, one constrained revision, and rechecking are introduced together (p. 4).  
**Why it matters:** The effect cannot be attributed to detection, revision, or rechecking.  
**Suggestion:** Preserve the current result as a package-level effect and preregister a component ablation for the next study. Avoid implying that “verification” alone caused the difference.  
**Severity:** Minor in the current manuscript because the limitation is stated.

## Detailed Comments

### Research Question and Hypothesis

The research question is answerable in principle, but the current design answers whether two sequential implementations differ under the protected evaluator, not whether the gate alone causes the difference.

### Research Design

The paired matrix is sensible for an engineering pilot. True within-artifact branching would provide a stronger counterfactual and remove most upstream variation.

### Sampling Strategy

The sample is exhaustive with respect to the frozen matrix but small for inference: three tasks, three seeds, and four claims per cell. The manuscript appropriately avoids population-level generalization. A prospective power calculation is not meaningful for the already frozen pilot; a sensitivity or precision analysis is more useful.

### Data Collection

Artifacts and registry requirements are described clearly. The paper should state whether initial claim registries are content-matched across arms or only equal in count.

### Analysis Methods

The hierarchical bootstrap respects task and seed nesting conceptually. With only three clusters at the task level, however, its interval has limited resolution and should remain descriptive. Report all resampling details and raw pairs.

### Statistical Reporting Adequacy

**Needs Improvement (approximately 65/100).** Effect size and interval are present, and the stopping rule is explicit. Missing elements are pair-level descriptive values, sensitivity to individual tasks, and independent calibration uncertainty. Conventional normality testing is not useful for nine coarse paired values; nonparametric transparency is preferable.

### Results Presentation

Figures and tables are clear. Table 2 should be supplemented with the nine paired observations, not only arm-level aggregates.

### Reproducibility

The local artifact map is unusually detailed. For external review, provide an anonymized archive or persistent repository link and an environment manifest accessible to reviewers.

### Methodological Fallacies Detected

- **Confounding by execution order and upstream stochasticity:** the downstream intervention is compared across different upstream runs.
- **Measurement dependence:** same-family semantic judgments may share preferences with the generated claims.
- **Potential construct drift:** a lower unsupported rate may reflect semantic contraction rather than factual correction.

## Questions for Authors

1. Are the 36 baseline and 36 gated claims matched by underlying proposition, or only by count?
2. What are the nine raw task-seed paired effects, and how does the interval change when each task is omitted?
3. Did the semantic evaluator ever receive text or metadata that could reveal the arm despite blind identifiers?
4. Can the already frozen two-auditor assessment be completed before publication review?

## Minor Issues

- Use one term consistently for the intervention arm: “gated” is clearer than alternating with “treatment.”
- State the exact definition of wall-clock start and end times in the manuscript or appendix.

## Dimension Scores

| Dimension | Score | Descriptor | Notes |
|---|---:|---|---|
| Originality | 70 | Adequate | Useful placement of a known verification idea |
| Methodological rigor | 55 | Weak | Identification and measurement dependence |
| Evidence sufficiency | 52 | Weak | Central proxy not independently calibrated |
| Argument coherence | 84 | Strong | Careful scope control |
| Writing quality | 86 | Strong | Clear methods and limitations |
| **Weighted average** | **66.3** | **Minor range numerically** | Core identification and measurement issues require major revision |
