# Publication-readiness contract

Research Forge uses two terminal states:

1. `pipeline_complete`: the four-stage workflow produced its required artifacts.
2. `publication_submission_ready`: a frozen venue-specific readiness contract passed and no scientific veto remains.

The second state is intentionally stricter. A complete or well-written paper can still be a provisional pilot.

## What the 60% threshold means

The default threshold is `0.60` on an internal, deterministic submission-readiness score. It measures properties the workflow can control before submission. It is not an acceptance probability, a venue acceptance rate, or a guarantee.

The venue router separately reports an estimated acceptance interval. That estimate uses explicit priors and becomes locally calibrated only after at least five outcomes from the same venue. Changing the readiness threshold never changes that probability.

## Weighted dimensions and hard minima

| Dimension | Weight | Target | Hard minimum | Owning stage |
|---|---:|---:|---:|---|
| Venue scope fit | 10% | 75% | 60% | Stage 1 discovery |
| Scientific identification | 16% | 72% | 55% | Stage 2 protocol |
| Independent validation | 15% | 75% | 60% | Stage 2 protocol |
| Evidence breadth | 11% | 70% | 55% | Stage 3 experimentation |
| Construct coverage | 10% | 72% | 55% | Stage 2 protocol |
| Novelty positioning | 11% | 65% | 50% | Stage 1 discovery |
| Reproducibility release | 9% | 78% | 60% | Stage 3 experimentation |
| Reporting integrity | 8% | 85% | 75% | Stage 4 synthesis |
| Venue compliance | 10% | 82% | 55% | Stage 4 synthesis |

Passing requires both:

- weighted readiness at or above the frozen threshold; and
- zero project-level critical/high scientific vetoes and zero dimension below its hard minimum.

This prevents a polished manuscript from compensating numerically for circular evaluation, a non-isolated counterfactual, missing construct measures, or an unavailable conference cycle.

The locked venue's own weighted quality bar is an additional veto. For the current RIPP target, the manuscript quality score is `0.4767` and the frozen venue bar is `0.6800`; the final gate cannot pass until the same venue recommendation is rerun at or above that bar.

## Venue-first experiment contract

`venue freeze-experiment-target` converts an existing strict venue recommendation into `publication_experiment_contract.json` before a new publication-intent experiment. It freezes:

- venue id, track, registry version, and recommendation-report hash;
- the venue's quality bar and scientific dimension weights;
- at least 8 heterogeneous tasks and 5 seeds per task;
- shared-artifact counterfactual isolation and independent-calibration requirements;
- claim retention/deletion, semantic-change, and informativeness/usefulness constructs;
- contextual novelty refresh before protocol freeze;
- a final retest against the same venue, quality bar, and 60% readiness threshold.

Stage 2 defaults to `publication`; there is no silent fallback. Publication intent fails before execution when any frozen requirement is missing. The `pilot` route exists only for internal engineering regression tests or historical reproduction, requires a written exception reason, writes a protected `pilot_intent.json`, and is permanently ineligible for submission. It cannot be promoted in place: publication requires a new venue-bound, prospectively gated experiment. Changing venue or track likewise requires a new contract and a new experiment audit. The legacy 3-task × 3-seed Stage 2 design is therefore pilot-only and cannot satisfy the frozen 8-task × 5-seed publication minimum.

## Back-propagation rule

A failed final gate does not ask the writing agent to add generic prose. It returns each defect to the latest stage that could have prevented it:

- Stage 1: change the target contribution, venue, literature boundary, or construct definition.
- Stage 2: change identification, randomization, independent calibration, sample breadth, telemetry, or reporting contracts before freeze.
- Stage 3: run the required heterogeneous tasks, independent evaluation, and release-grade artifact chain.
- Stage 4: complete integrity review, claim-evidence audit, venue template, anonymity, checklist, and external links.

Projected scores are planning scenarios only. No projected improvement is added to the current readiness score until its required artifact exists and the full gate is rerun.

## Scientific failure repairs the system

Every failed scientific hard gate generates three project artifacts under `design_revisions/`:

- `publication_design_repair.json`: machine-readable root causes, permanent rule changes, enforcement stages, and regression tests;
- `publication_design_repair.md`: human-readable design repair sheet;
- `publication_failure_ledger.jsonl`: append-only occurrences keyed by a stable failure fingerprint.

The next Stage 2 freeze consumes the repair JSON. It fails before execution when a repaired root cause is still open, when the default publication matrix is below 8 heterogeneous tasks × 5 seeds, or when a required contextual novelty refresh is absent. This closes the learning loop from final scientific review back into discovery and protocol design.

The repair is prospective. Existing frozen protocols, experiment cells, analyses, and manuscripts are not edited to make the historical run appear better. A repeated fingerprint means the previous system change failed to prevent recurrence; the response is an implementation and regression-test audit, not a new prose recommendation.

## Current V4 result

The English V4 manuscript passes the narrative-depth gate at 6,562 words, but its publication-readiness result is `50.51% / 60%`. Its writing is no longer the bottleneck. The hard gate also records that its RIPP-weighted quality score (`47.67%`) is below the locked venue bar (`68%`). The largest remaining gaps are prospective same-artifact identification, independent calibrated measurement, complete information-preservation constructs, broader heterogeneous evidence, and refreshed novelty positioning.

The current estimated acceptance center for the selected specialist journal remains `5.44%` under an uncalibrated heuristic. The remediation scenario projects readiness to `81.25%`; it does not project or promise acceptance.
