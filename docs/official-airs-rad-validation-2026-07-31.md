# Official AIRS RAD local runtime validation (2026-07-31)

## Outcome

Research Forge completed a bounded real-case validation against four imported
official AIRS RAD task bundles. Each task ran ten frozen seeds, for 40 completed
cells and zero failed cells. Candidate programs were executed in a network-disabled,
read-only Docker boundary; protected evaluator data remained outside the candidate
mount. Scores were produced by the unchanged official `prepare.py`,
`evaluate_prepare.py`, and `evaluate.py` sequence.

This is a local runtime validation, not an AIRS leaderboard submission and not
external acceptance.

## Frozen task matrix

| Official task | Metric | Observed score | Seeds | Valid cells |
|---|---|---:|---:|---:|
| TextualClassificationSickAccuracy | Accuracy | 0.5686913982878108 | 10 | 10 |
| TextualSimilaritySickSpearmanCorrelation | SpearmanCorrelation | 0.5661807710476306 | 10 | 10 |
| SentimentAnalysisYelpReviewFullAccuracy | Accuracy | 0.2 | 10 | 10 |
| MathQuestionAnsweringSVAMPAccuracy | Accuracy | 0.07333333333333333 | 10 | 10 |

The scores are acceptance evidence for the runtime and evaluator binding. They
are not presented as competitive performance claims.

## Deterministic audit

The audit rechecked official source hashes, adapter-manifest binding, unique
task/seed cells, submission shape and hash, Docker isolation flags, candidate /
evaluator path separation, official stage return codes, metric equality, and the
absence of a leaderboard claim.

- Status: `verified`
- Tasks: 4
- Completed cells: 40
- Failed cells: 0
- Leaderboard submitted: `false`
- Audit SHA-256: `ecbdf43e45cb07eb81fb6f114242941778777cd3b1449fc6612ebb38617b8628`

During the 2026-07-31 revalidation, representative seed-0 submissions for SICK
classification, SICK similarity, and SVAMP were rescored through the unchanged
official evaluator in Docker. All returned code 0 and reproduced the registered
metrics.

## Evidence boundary

The durable repository contains the adapter, auditor, tests, and this acceptance
record. Large run outputs, imported benchmark data, provider responses, caches,
and local machine configuration remain outside version control. The authoritative
local ledger used for the audit was
`D:\AIRSBenchRuns\official-cpu-validation-api\official-matrix-ledger.jsonl`.

Permitted claim: four official AIRS RAD tasks completed 40 isolated local cells
with the unchanged official evaluator.

Forbidden claims: official leaderboard performance, complete AIRS coverage,
external acceptance, or independent trust-domain reproduction.
