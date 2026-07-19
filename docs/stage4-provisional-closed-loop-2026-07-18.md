# Stage 4 provisional closed loop — 2026-07-18

## Outcome

Project `research-agent-evidence-v2` completed the four-stage engineering loop under frozen protocol `stage2-22e124e44294`.

- Stage 1: 138 candidates, 22 semantic-screening decisions, 12 verified registered papers, approved direction `novelty-02`.
- Stage 2: frozen 2-arm × 3-task × 3-seed protocol and controlled Docker backbone.
- Stage 3: 18/18 experiment cells and 18/18 protected blinded evaluations completed.
- Stage 4: 13 evidence-bound claims, deterministic Markdown and LaTeX papers, synthesis audit, closed-loop status, and completion certificate.

The project state is `completed`. This means pipeline completion, not publication readiness.

## Provisional result

The protected automated evaluator estimated:

| Metric | Baseline | Treatment |
|---|---:|---:|
| Unsupported claim rate | 30.56% | 8.33% |
| Experiment-detail error rate | 33.33% | 5.56% |
| Citation correctness | 66.67% | 88.89% |
| Evidence coverage | 100.00% | 100.00% |
| Mean task-native score | 0.60057 | 0.62213 |
| Total wall-clock seconds | 2721.13 | 4514.38 |

The paired treatment-minus-baseline unsupported-claim-rate effect was `-0.2222`; the preregistered 10,000-resample hierarchical bootstrap interval was `[-0.4167, -0.0833]`. Treatment runtime increased by `65.90%`.

These values remain automated-evaluator estimates. The preregistered two-human blinded audit is deferred, `primary_analysis_interpretable=false`, and `publication_ready=false`.

## Paper artifacts

- LaTeX manuscript: `stage1_runs/research-agent-evidence-v2/synthesis/manuscript.tex`
- Markdown manuscript: `stage1_runs/research-agent-evidence-v2/synthesis/manuscript.md`
- Claim registry: `stage1_runs/research-agent-evidence-v2/synthesis/claims.json`
- Provisional analysis: `stage1_runs/research-agent-evidence-v2/stage2/provisional_analysis.json`
- Synthesis audit: `stage1_runs/research-agent-evidence-v2/synthesis/audit.json`
- Completion certificate: `stage1_runs/research-agent-evidence-v2/completion_certificate.json`
- Closed-loop status: `stage1_runs/research-agent-evidence-v2/stage2/closed_loop_status.json`

The LaTeX paper is deterministically regenerated from the frozen analysis and claim registry. The synthesis audit checks its required sections and byte-exact reconstruction. The completion certificate binds its SHA-256 together with the protocol, protected evaluation, claim registry, both manuscript formats, synthesis audit, operator decision, and same-model persona-panel output.

## Verification

The final synthesis audit passed 20/20 checks with no violations. The completion audit passed all six checks:

- completion certificate valid;
- LaTeX manuscript certified;
- project state completed;
- closed-loop status valid;
- analysis remains provisional;
- synthesis audit passed without claiming publication readiness.

No TeX compiler is installed on the current machine, so the audited deliverable is the `.tex` source rather than a locally compiled PDF. A human reviewer can compile it with a standard TeX Live or MiKTeX installation.
