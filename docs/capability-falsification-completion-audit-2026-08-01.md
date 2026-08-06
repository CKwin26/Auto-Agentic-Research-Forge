# Capability falsification completion audit — 2026-08-01

This audit closes the capability-review requirements without requiring an
external independent researcher. Human adjudication remains an append-only
internal control; it is not an external-validation release gate.

## Requirement disposition

| # | Requirement | Disposition | Verifiable evidence |
|---:|---|---|---|
| 1 | Map the 22 product-facing features to machine capabilities | Complete | `research_forge/product_capability_map.yaml`; replay reports `22/22` mapped features. |
| 2 | Separate registry, evidence freshness, and replay status | Complete | `research_forge/capability_assurance.py`, `research_forge/capability_verification.yaml`, and `scripts/audit_capability_assurance.py`; replay reports `18/18` for all three layers. |
| 3 | Clarify completion-record and release-signature boundaries | Complete | `docs/capability-assurance.md` distinguishes local Ed25519 completion records from GitHub Sigstore/SLSA release provenance. Neither is described as third-party scientific certification. |
| 4 | Require semantic completeness in Contract Compiler v2 and regress an existing stock project | Complete | `research_forge/contract_compiler.py`, stock regression fixture, and `tests/test_contract_compiler.py`. Missing estimand, denominator, intervention, metric, threshold, or executable binding cannot compile as a formal contract. |
| 5 | Add a Scientific Contribution Gate distinct from the Nuwa panel | Complete | `research_forge/scientific_contribution_gate.py` and `tests/test_scientific_contribution_gate.py`. Scientific validity and contribution novelty are independently represented. |
| 6 | Formalize experiment profiles, task modes, fixtures, adapters, and non-formal boundaries | Complete for the declared bounded scope | `research_forge/profile_capability_matrix.yaml`, `docs/profile-capability-matrix.md`, and profile-matrix tests. Unsupported combinations are explicit extension points, never reported as successful execution. |
| 7 | Separate implementation maturity, evidence level, and scope | Complete | Capability manifest stores these as independent fields; assurance rejects invalid or stale evidence rather than inferring maturity from code presence. |
| 8 | Compare a second evaluator family and report disagreement | Complete | OpenML Task 39 comparison uses `isolated-csv-accuracy` and `profile-metric-recomputation`; 42 paired observations, zero disagreement, stable direction and verdict. Report hash and method are frozen in `docs/openml-evaluator-family-comparison-2026-08-01.md`. |
| 9 | Support disagreement adjudication without rewriting history | Complete | `EvaluatorAdjudicationRecord` is append-only, lineage-bound, and cannot mutate either evaluator result. Abstention and successor-required decisions remain blocking. |
| 10 | Cover positive, negative, mutation, replay, and real-case paths | Complete for release scope | Unit and workflow tests cover positive/negative/mutation/replay behavior; real cases cover OpenML, PaperQA, Draw.io, AIRS/RAD, and sealed-project replay. |
| 11 | Run a scaled PaperQA benchmark | Complete for the documented benchmark scope | `docs/paperqa-integration.md` and its fixtures cover 1/5/20/50 questions, 1/10/100 documents, latency percentiles, cache states, abstention, citation accuracy, and unsupported-statement rate. |
| 12 | Require an external independent researcher | Explicitly waived by the project owner | No release gate depends on an external researcher. The legacy external-validation compatibility field is retained only for old records and remains false. |

## Latest replay result

The explicit replay on 2026-08-01 reported:

- capability registry coverage: `18/18`;
- product-feature mapping: `22/22`;
- evidence freshness: `18/18`;
- replay passing: `18/18`;
- real-case validated capabilities: `6/18`;
- release evidence ceiling: `E3_real_case`;
- external validation required: `false`.

The `E3_real_case` ceiling is deliberate. It means several bounded workflows
have real-case evidence, while the platform does not claim universal
cross-domain autonomy or third-party scientific certification.

## Release interpretation

Passing this audit means that every declared capability has a registry entry,
fresh evidence, and a passing replay at its stated scope. It does not mean that
every extension point is implemented, that every scientific domain is
supported, or that an internal review substitutes for journal peer review.
