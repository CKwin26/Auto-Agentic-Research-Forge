# Research Forge v1 completion audit — 2026-07-31

## Decision

**Result: conditionally complete for a bounded computational-research v1; not
complete for the stronger “independently validated scientific operating
system” claim.**

The platform now has a verified workflow control loop, executable-contract
core, three typed profiles, controlled and real-case benchmarks, evidence-bound
authoring, external base RO-Crate validation, and two controlled clean-room
replays. It must not claim C5 independent validation, universal contract
compilation, or cross-domain autonomous science.

## Evidence gates

| Requirement | Status | Durable evidence | Boundary |
|---|---|---|---|
| C0–C5 capability registry | Pass | `research_forge/capability_manifest.yaml`; `tests/test_capability_registry.py` | 16 registered product capabilities; maturity is scoped per capability |
| 12 core benchmark cases + 2 replay extensions | Pass with mixed maturity | `research_forge/capability_benchmark.yaml`; `tests/test_capability_benchmark.py` | component, controlled, and real-case evidence remain visibly distinct |
| Workflow loop vs scientific loop split | Pass | `workflow.control_loop`, `scientific_completion.empirical_loop`, `scientific_completion.cross_domain_autonomy` manifests | no claim that four visible phases prove arbitrary scientific completion |
| Contract Compiler | C2 pass | `research_forge/contract_compiler.py`; `tests/test_contract_compiler.py` | deterministic for supported schemas; not a universal scientific compiler |
| Resource lifecycle and semantic gates | C2 pass | resource domain/compiler tests and Stage 2 documentation | broad non-tabular scientific semantics remain profile-specific |
| Contract amendment loop | C2/C3 pass | Workflow/contract amendment tests | historical contracts remain immutable; owner approval is required |
| Scientific Contribution Gate | C2 pass | positive/negative compiler fixtures | advisory scientific value screening is not external peer review |
| `tabular_ml_v1` | Controlled pass | five OpenML task report and tests | numeric binary classification/regression only |
| `benchmark_prediction_v1` | Component pass | profile source and tests | no real hidden-label external task has reached C4 |
| `existing_python_project_v1` | Component pass; replay utility C4 | two non-prebuilt project replay report | complete Stage 2 → Stage 3 dual-arm science path is not C4 |
| OpenML tasks | Pass | 3 classification + 2 regression tasks | exact Task/Dataset gateway binding; not OpenML Run/Flow publication |
| Great Expectations | C3 controlled pass | hash-bound offline gate, resource-lifecycle binding, real acceptance report | structural tabular qualification only; no semantic or verdict authority |
| AIRS formal tasks | C4 narrow pass | 4 official RAD tasks × 10 seeds = 40 cells | exceeds the requested minimum of 3 tasks; no leaderboard/external acceptance |
| RO-Crate external checker | C3 pass | CRS4 `roc-validator` 0.11.3 acceptance report | base RO-Crate 1.1 required checks only; no Workflow Run profile claim |
| W3C PROV external validation | C3 bounded pass | PySHACL 0.40.1 external semantic-engine acceptance | bounded Stage 3 projection only; no official W3C certification or full PROV-CONSTRAINTS claim |
| Two clean-room replays | C3 controlled pass | two sealed-package Docker reports and acceptance hashes | same operator host; no independent KMS/HSM/external receipt, so no RF-E2/C5 |
| Two non-prebuilt real Python projects | C4 narrow pass | stock and advisor replay packages | same-host isolated Docker replay, not independent science replication |
| Evidence-bound Stage 4 | C3 pass | paper authoring/publication-control tests | cannot upgrade `unverifiable` or failed execution into a supported claim |

## Quantitative acceptance

- Product capability audit: **16/16 supported, 0 overclaims**.
- Upstream learning/integration audit: **42/42 supported, 0 evidence gaps**.
- Upstream status distribution: 6 verified runtime, 4 partial runtime,
  3 adapter only, 22 architecture only, 2 Codex-skill only, 4 missing/stub,
  and 1 not adopted.
- Full Python test collection: **659 tests**.
- Final full-suite run: **100% completed with no failures**; one optional
  environment-conditioned test was skipped.
- RO-Crate external required issues: **0**.
- AIRS: **40/40 official task-seed cells passed deterministic audit**.
- Controlled clean-room packages: **2/2 comparison passed, coverage full,
  clean-room invariants true**.

## Clean-room evidence boundary

The candidate containers satisfy operational isolation: fresh and ephemeral,
network disabled, read-only root, no development-directory mount, no original
database, no reused cache, sealed assets only, no candidate credentials, no
signing key, and no control-plane endpoint. The reports are unsigned by design.

Research Forge therefore records these as controlled C3 replays while keeping
`rf_e2_awarded=false`. RF-E2/C5 requires a receipt from a separately operated
verifier whose key is held by KMS, HSM, or an external organization. The local
control plane is deliberately unable to self-promote the evidence.

## Remaining v1-exit gaps under the strongest interpretation

1. Run one real hidden-target `benchmark_prediction_v1` study.
2. Validate a Workflow Run/Provenance Run RO-Crate profile, not only base 1.1.
3. Publish one minimal research package and one correctly blocked case without
   committing generated runs to the product repository.
4. Obtain an independently operated replay receipt before claiming RF-E2/C5.

## Permitted v1 claim

> Research Forge v1 is a bounded computational-research platform that can
> compile supported research contracts into controlled experiments, preserve
> machine-verifiable evidence and verdict authority, and replay sealed packages
> in isolated workers. Its strongest validated profiles are explicitly listed;
> cross-domain autonomy and independent external reproduction are not claimed.

## Reproduction commands

```powershell
python -m pytest -q
python scripts/audit_capability_registry.py --root .
python scripts/audit_upstream_capabilities.py --json-output upstream-audit.json --markdown-output upstream-audit.md
python scripts/validate_rocrate_external.py <crate-root> <report-output>
python scripts/run_clean_room_acceptance.py <stage3-completion.zip> <outside-repository-output>
```
