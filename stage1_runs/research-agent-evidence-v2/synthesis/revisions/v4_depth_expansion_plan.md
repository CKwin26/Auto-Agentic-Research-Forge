# V4 manuscript depth and evidence plan

Status: implemented on 2026-07-18.

## Purpose

Repair the over-compressed v3 manuscript without increasing its scientific claim tier. The revision may add explanation, provenance, full paired-result reporting, mechanism alternatives, and validity boundaries already supported by frozen artifacts. It must not add unmeasured outcomes, present proxy evaluation as human validation, or claim causal identification.

## Deterministic finalization targets

- English journal profile: at least 6,000 narrative words, all section minima, at least 15 references, at least 8 related-work citation commands, at least 15 numeric result tokens, and no more than 0.08 exact duplicate-paragraph ratio.
- Chinese journal profile: at least 10,000 Han characters with the corresponding section, paragraph, subsection, reference, citation, result-grounding, and duplication checks.
- A short report is allowed only through the separately named `short-report` profile.
- Passing the depth gate is necessary for official PDF finalization but does not establish claim support or scientific validity.

## Evidence-bound expansion map

| Section | Permitted expansion | Bound evidence or limitation |
|---|---|---|
| Introduction | Separate execution validity from claim-evidence fidelity; state bounded contributions and claim ceiling | Frozen protocol, artifact schema, deferred human audit |
| Related work | Add paper-level versus claim-level review placement and evaluator-dependence context | Verified citations; post-freeze sources labeled as context only |
| Methods | Define atomic claim unit, type-specific provenance contract, failure taxonomy, decision trace, denominator and retention rules | Claim registries, protected evaluator schema, rebranch traces |
| Results | Report the nine-pair discrete distribution, proxy uncertainty boundary, retention behavior, edit shape, and panel role | `stage2/evaluations/summary.json`, rebranch summary, persona panel artifacts |
| Discussion | Compare correction, qualification, contraction, and evaluator accommodation; derive layered architecture only as an implication | Observed edit traces and known measurement limits |
| Conclusion | Restate the narrow engineering result and unresolved human-calibrated question | No human audit; `primary_analysis_interpretable=false` |

## Corrections made during expansion

- The nine original paired effects are four `0.00`, three `-0.25`, one `-0.50`, and one `-0.75`; the manuscript must not report a ten-item distribution.
- Rebranch files have recorded hashes and freeze/completion timestamps, but no separate inference-start attestation. The paper therefore describes a content-addressed snapshot, not independent proof that every hash preceded the first model call.
- DeBERTa NLI is described as a cross-family proxy evaluator, not an independent evaluator or human validation.
- The scientist-persona panel remains an adversarial error-finding instrument and is not counted as human or cross-model validation.

## Finalization artifacts

The official compiler path is `research-forge manuscript finalize-pdf`. It first emits a depth report, compiles in temporary staging only after the report passes, and then publishes a PDF and SHA-256 finalization manifest. Direct LaTeX compilation is a development action and does not create an official Research Forge finalization manifest.
