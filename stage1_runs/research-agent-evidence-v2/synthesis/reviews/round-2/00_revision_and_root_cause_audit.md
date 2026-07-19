# Round 2 Revision and Root-Cause Audit

## Bottom line

The revision resolves every manuscript-level item from round 1. It does not and cannot resolve the publication-critical design limitations without new evidence. The paper is now suitable for a developmental external AI review, but it is not publication-ready.

## Round-1 action closure

| Action | Status | Round-2 evidence |
|---|---|---|
| R1: Add canonical research-agent and judge-bias context | Complete | The AI Scientist, The AI Scientist-v2, Agent Laboratory, and self-preference work are cited as post-freeze context without altering the frozen novelty claim. |
| R2: Define the protected-evaluation threat model | Complete | Section 3.4 tabulates arm masking, evidence restriction, deterministic references, model-family independence, human calibration, and shared-artifact branching. |
| R3: Report all nine paired values and sensitivity summaries | Complete | Section 4.1 reports every task-seed pair and all three leave-one-task-out means. |
| R4: Narrow the measured construct and address semantic contraction | Complete | Title, abstract, research question, interpretation, and conclusion now use an internal proxy association ceiling; claim-evidence fidelity is distinguished from scientific validity. |
| R5: Expand cost disclosure | Complete within recorded evidence | The manuscript defines wall-clock endpoints and lists unavailable token, model-call, monetary, energy, and human-labor fields. |
| R6: Academic Humanizer fidelity pass | Complete | See `01_academic_humanizer_fidelity.md`. Numerical values, citations, limitations, and author responsibility were preserved while stock or overbroad reliability language was narrowed. |

## Root-cause diagnosis

The round-1 symptoms reduce to three publication-critical upstream causes:

1. **Measurement circularity.** The generator, gate, and semantic evaluator use Codex-family reasoning without completed independent calibration.
2. **Counterfactual non-isolation.** Baseline and treatment are separate stochastic runs in a fixed block order, not two conclusion branches from identical upstream artifacts.
3. **Maturity-target mismatch.** The preregistered human audit is a condition for interpreting the primary analysis, but it is deferred while the manuscript is being shown to a publication-style reviewer.

These causes are now encoded in the deterministic root-cause preflight. For this artifact it assigns the maximum claim tier `internal_proxy_association`, predicts `major_revision_or_reject` under a publication target, permits a developmental review, and blocks a publication-ready label.

## Remaining work that requires new evidence

| Blocker | Earliest real repair point | Current treatment |
|---|---|---|
| Independent calibration absent | Stage 2 measurement design / deferred audit | Explicitly disclosed; not represented as human validation |
| Separate stochastic arm runs | Next protocol design | Current estimate described as noncausal association |
| Same-family evaluator | Next evaluator contract | Current outcome described as internal proxy only |
| Semantic change unmeasured | Next metric contract | Equal counts not used to claim preserved informativeness |
| Component attribution unavailable | Next ablation protocol | Gate described as a bundled intervention |

## Routing decision

- Developmental external AI review: **READY**
- Publication submission: **BLOCKED**
- Expected publication-style decision before new evidence: **Major Revision or Reject**

