# Stage 3 completion audit

Audit date: 2026-07-26

Scope: the first platform profile,
`computational_paired_comparison_v1`. This audit does not claim that arbitrary
computational or non-computational studies are supported.

## Acceptance audit

| # | Requirement | Status | Authoritative evidence |
|---:|---|---|---|
| 1 | A Stage 2 scientific specification plus MVP receipt can produce a complete Execution Package | Pass | `stage3_build_admission`, `create_experiment_build_plan`, `materialize_generated_profile_v1_package`, `freeze_generated_profile_v1_execution_package`; `test_blueprint_generated_source_is_materialized_without_model_write_access` runs the generated package through formal evaluation |
| 2 | A ready-made experiment can bypass reconstruction and be imported, verified, and frozen | Pass | `freeze_ready_made_execution_package`; `test_ready_made_package_passes_second_admission_into_same_kernel` |
| 3 | Both entry modes reach the same execution kernel | Pass | Both paths create `ExecutionPackageSeal` and `formal_execution` handoffs consumed by `ensure_stage_three_dag`; generated and ready-made tests assert this boundary |
| 4 | Stage 2 MVP artifacts cannot enter the formal evidence chain | Pass | `MVPFeasibilityReceipt` hard-codes `evidence_eligible=false` and `formal_run_eligible=false`; ready-made and generated tests check feasibility-artifact exclusion |
| 5 | Formal baseline and treatment rerun under the same frozen package | Pass | deterministic matrix compiler binds both arms to one Plan and execution seal; `test_profile_v1_compiles_deterministic_matrix` and full completion tests |
| 6 | The builder cannot mutate the Research Contract | Pass | scientific seal binds the contract hash; generation is schema-only with no materialization write access; package freeze rechecks the hash and exact declared arm delta |
| 7 | Smoke tests cannot view or compare formal results | Pass | separate smoke/formal partitions, candidate/target separation, mount checks, and `test_execution_freeze_blocks_smoke_on_formal_inputs`; generated smoke test asserts no formal mount or arm comparison |
| 8 | The concrete implementation proves conformance to the Stage 2 specification | Pass for Profile v1 | build admission checks estimand, data, arm, environment, resource, and budget requirements; package validation requires exact `allowed_arm_delta`, structurally matched arm commands, schemas, manifests, locks, provenance, golden evaluator vectors, and conformance records |
| 9 | A negative scientific result does not trigger repair | Pass | `test_refuted_hypothesis_is_completed_science_not_repair_failure` proves operational completion, a `refuted` verdict, and no diagnostic or repair |
| 10 | Metric, threshold, or statistical-rule changes create a Scientific Successor | Pass | `propose_stage3_repair` routes scientific design changes to `ScientificSuccessorRequest`; `test_metric_or_threshold_change_requires_scientific_successor` |
| 11 | Generated or remotely acquired code executes only in isolation | Pass | read-only bounded archive staging, no raw execution, mandatory container policy, and candidate/evaluator separation; `test_pinned_third_party_code_is_read_only_input_and_output_is_isolated`, online acquisition test, and real Docker boundary test |
| 12 | A second different project needs no core-code change | Pass within Profile v1 | `test_second_different_profile_v1_project_needs_no_platform_code` runs a lower-is-better latency study with different tasks through the same kernel |
| 13 | Every formal verdict traces to specification, package, Plan, run, output, evaluator, and evaluation | Pass | explicit hashed EvidenceEdges and verified EvidenceChain; full completion and generated-package lineage assertions |
| 14 | Build failure, execution failure, and scientific non-support have separate semantics | Pass | Read Model exposes distinct operational and scientific state spaces; persisted build blockers include resource/licence/unsupported/build, formal faults use execution/repair states, and refuted science remains completed |

## Verification results

Commands executed in the audited worktree:

```text
python -m py_compile \
  research_forge/workflow_domain.py \
  research_forge/stage_two.py \
  research_forge/stage_three_build.py \
  research_forge/stage_three.py \
  research_forge/retrieval/interfaces/service.py \
  research_forge/web_app.py

python -m pytest \
  tests/test_stage_two.py \
  tests/test_stage_three.py \
  tests/test_stage_three_generation.py \
  tests/test_stage_three_evaluator.py \
  tests/test_container_execution.py \
  tests/test_experiment_execution.py \
  tests/test_workflow_domain.py \
  tests/test_retrieval_gateway.py \
  tests/test_web_app.py -q

109 passed

python -m pytest \
  tests/test_container_execution.py::test_real_two_container_candidate_evaluator_boundary \
  -vv -rs

1 passed

pnpm run build

Vite production build succeeded; 4,574 modules transformed.

python -m pytest -q

430 passed, 1 skipped; 431 tests collected and no failures.
```

The Docker result is an actual pass, not a skip. It proves that the candidate
and evaluator run in separate network-disabled containers and that candidate
inputs do not contain formal targets. The one full-suite skip is the
opt-in official AIRS Docker-image integration, which requires
`RUN_DOCKER_AIRS_TEST=1` and a separately built AIRS image; it is not a Stage 3
Profile v1 test.

## Honest limitations

- Profile v1 covers paired computational comparisons only.
- Ready-made legacy manifests can preserve their existing summary-output
  adapter for compatibility. The build-from-blueprint path uses the stronger
  sample-level independent platform evaluator. A future migration can require
  sample-level outputs for every imported legacy experiment.
- Specification conformance is mechanically enforceable for declared fields,
  hashes, schemas, command structure, arm delta, and golden tests. No general
  system can prove arbitrary program semantics; unsupported or ambiguous
  designs remain blocked rather than being certified by model assertion.
- A passed Stage 3 audit means that formal evidence is internally qualified.
  It does not imply publication approval or external replication.

## Profile v1.1/v1.2 hardening addendum

The following controls were added after the original completion audit:

| Control | Current enforcement |
|---|---|
| Formal-result exposure | Target hashes are tracked across Studies; first use is `confirmatory_used`, later use is `adaptive_reuse` |
| Model selection and arm fairness | Frozen typed contracts; formal confirmation cannot serve as a multi-trial selection split |
| Canonical attempts | First qualified success only; rerunning a successful RunCell is blocked and requires a successor |
| Pair scheduling | Deterministic AB/BA pair blocks with an actual second-arm dependency |
| Variance unit | The evaluator aggregates at registered-pair or task level as frozen and reports both raw pairs and independent units |
| Statistical assurance | Boundary/simulation checks, explicit limitations, and an independent primary-effect implementation |
| Leakage | Structured audit with pass, not-applicable, requires-review, and blocking-failure states |
| Stage 4 claim boundary | `ScientificClaimEnvelope` restricts allowed prose and prohibited generalizations |
| Worker takeover | Lease and fencing token reject stale result publication |
| Portable package | Ed25519-signed ZIP/tar.zst plus PROV, RO-Crate, HTML, and standalone offline verification |
| Clean-room level | L2 requires a valid fresh-worker attestation; local evidence verification remains L1 |
| Shared defects | Component notice, global impact analysis, and append-only per-Study erratum overlay |

This addendum does not claim external replication. L3 and L4 remain evidence
levels that can only be reached by genuinely independent or external work.
