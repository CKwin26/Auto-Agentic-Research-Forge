# Four-stage closed-loop contract

Research Forge exposes eleven fine-grained states, grouped into four macro stages. The fine states preserve recovery and review semantics; the macro stages describe the product loop.

| Macro stage | Fine states | Required gate | Durable output |
|---|---|---|---|
| 1. Discovery and planning | `scoping`, `plan_review` | Latest query plan, real multi-provider discovery, canonical verification, exact semantic screening, source-bound novelty map, human review-ID approval, and a plan bound to that approved evidence | search plan, raw responses, discovery, screening, sources, review, approval, Stage 1 manifest, plan evidence binding |
| 2. Protocol | `contract_frozen`, `baseline_pending`, `baseline_verified` | Source, research, execution, evaluator, and baseline checks pass | frozen manifests and verified baseline run |
| 3. Experimentation | `experiment_design`, `experiment_running`, `result_review` | At least one valid improving run is explicitly or automatically promoted | evidence ledger and promotion lineage |
| 4. Synthesis | `synthesis`, `completed` | Every result claim matches a valid run; every background claim cites a verified source; required manuscript sections exist | claims, manuscript, audit, completion certificate |

`paused` remains an operational state outside the four-stage numbering.

## Completion is not publication readiness

`completed` means the four-stage artifact chain is internally complete and hash-audited. `publication_ready` is stricter. It additionally requires isolation-verified cited runs, at least one verified paper source, and no benchmark-calibration limitation. This prevents a successful harness test from being described as a publishable scientific result.

## Deterministic stage-four policy

The first closed-loop implementation renders the manuscript from frozen fields and structured claims. A result claim contains its run ID and reported metric values; the audit compares them numerically with `runs/<run-id>/record.json` and the append-only evidence ledger. A background claim contains frozen source IDs. Free-form agent prose is intentionally deferred until it can be constrained by this registry.

## Evidence-bound stage-one policy

Stage 1 is an eleven-substage workflow rather than a single planning prompt. Codex designs bounded queries, judges relevance on a fixed candidate set, synthesizes related-work themes, and proposes falsifiable novelty hypotheses. Deterministic code performs real Crossref and Semantic Scholar retrieval, stores and hashes raw responses, normalizes and deduplicates records, verifies identifiers, applies hard filters and query quotas, registers the final source set, and audits the complete artifact chain. A human must approve the exact review ID before the research-contract planner can run. The planner then receives only that approved source set, and its draft is bound to the review hash and source IDs. See [the Stage 1 workflow](stage1-literature-workflow.md).

## Runnable path

The human-driven path is:

```text
init -> literature plan -> literature discover -> literature screen
     -> literature synthesize -> literature approve -> plan -> configure -> freeze -> baseline
     -> propose -> run -> promote -> synthesize -> audit-synthesis -> complete
```

Manually registered sources remain available as supplemental evidence but do not bypass the scientific Stage 1 gate. RF-Bench uses a separate calibration path: its packaged task specification and protected evaluator satisfy the benchmark-mode Stage 1 audit without pretending to establish a literature-backed novelty claim. `synthesize` and `complete` then close stage 4. These calibration drafts remain non-publication-ready because a benchmark specification is not scientific literature or novelty evidence.

## Verified vertical run

On 2026-07-17 the full path was executed at `four_stage_runs/rf-quadratic-max-grid-20260717T145404Z-1b2e95`. The baseline and candidate each ran twice in the controlled Docker environment. The protected score improved from `0.0` to `0.5555555556`; both run records passed isolation and integrity checks. The project then produced five claims, a manuscript, a synthesis audit with all eleven checks passing, and a completion certificate whose artifact hashes verify. All four macro-stage gates report `passed: true`.

The run correctly reports `publication_ready: false`: it uses a benchmark specification rather than a verified paper source, and RF-Bench calibration does not establish novelty. This is the intended distinction between a working end-to-end pipeline and a publishable scientific study.

The first live scientific Stage 1 run is stored at `stage1_runs/research-agent-evidence-v2`. It produced a six-query search strategy, 138 normalized candidates, a 22-candidate semantic screen, 12 verified included papers, five related-work themes, and four bounded novelty candidates. The operator approved the exact review ID and selected `novelty-02`; a freeze-ready 18-run paired-ablation plan was then bound to the review and source set. All 23 Stage 1 checks pass. See [the verified Stage 1 record](real-stage1-2026-07-17.md).
