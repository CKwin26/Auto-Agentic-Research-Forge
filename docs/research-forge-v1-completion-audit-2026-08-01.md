# Research Forge v1 completion audit — 2026-08-01

## Decision

**Result: the bounded Research Forge v1 product claim is achieved and the v1
release acceptance is complete. C5 independent validation is an optional
future maturity promotion, not a v1 release requirement.**

Research Forge now has a verified workflow-control loop, deterministic
executable-contract core, three typed experiment profiles, positive and
negative capability benchmarks, real hidden-target execution, external
metadata/provenance validation, public machine-verifiable acceptance packages,
and controlled clean-room replay. It must not claim arbitrary-domain autonomous
science or external independent reproduction.

## Requirement-by-requirement evidence

| Requirement | Status | Authoritative evidence | Claim boundary |
|---|---|---|---|
| C0–C5 capability manifests | Pass | `research_forge/capability_manifest.yaml`; `scripts/audit_capability_registry.py`; `tests/test_capability_registry.py` | 18 scoped product capabilities; maturity is not inherited across domains |
| 12 core benchmark cases + 2 replay extensions | Pass | `research_forge/capability_benchmark.yaml`; `tests/test_capability_benchmark.py` | component, controlled, real-case, and independent evidence remain distinct |
| Workflow loop vs scientific completion loop | Pass | manifests `workflow.control_loop`, `scientific_completion.empirical_loop`, and `scientific_completion.cross_domain_autonomy` | four visible phases do not imply arbitrary scientific completion |
| Contract Compiler and execution-readiness gate | C2/C3 pass | `research_forge/contract_compiler.py`; compiler positive/negative tests | deterministic only for supported profiles; `FROZEN_EXECUTABLE` requires a complete DAG and dry run |
| Resource lifecycle, labels, leakage, and semantic gates | C2/C3 pass | resource validation, Great Expectations, profile, and compiler tests | broad scientific semantics remain profile-specific |
| Protocol Amendment loop | C2/C3 pass | amendment models, Workflow tests, Stage 2→3 repair tests | historical Contract/Run/Verdict objects remain immutable; owner approval is required |
| Scientific Contribution Gate | C2 pass | `research_forge/scientific_contribution_gate.py`; positive and negative fixtures | advisory screening is not external peer review |
| `tabular_ml_v1` | Controlled pass | five OpenML task benchmark report and profile tests | bounded numeric classification/regression support |
| `benchmark_prediction_v1` | C4 real-case pass | OpenML Task 39 hidden-target acceptance report and public package | one real narrow task; not universal benchmark compatibility |
| `existing_python_project_v1` | C4 narrow pass | two non-prebuilt project replay reports and tests | same-operator isolation; not external scientific replication |
| Five OpenML tasks | Pass | three classification and two regression task reports | exact Task/Dataset freezing; no OpenML Run/Flow publication claim |
| Great Expectations controlled validator | C3 pass | hash-bound offline validation report and tests | structural qualification only; no scientific-semantic authority |
| AIRS formal tasks | C4 narrow pass | four official RAD tasks × ten seeds = forty isolated cells | exceeds the three-task minimum; no leaderboard or external acceptance claim |
| Workflow Run RO-Crate external validation | C3 pass | `docs/workflow-run-rocrate-external-validation-2026-08-01.md`; CRS4 validator report | Workflow Run 0.5 plus inherited required profiles; no Provenance Run or certification claim |
| W3C PROV external validation | C3 bounded pass | PySHACL 0.40.1 report and frozen SHACL rules | bounded Stage 3 projection; not full PROV-CONSTRAINTS certification |
| Two clean-room replays | C3/RF-E1 pass | two sealed-package offline Docker replay reports | satisfies the v1 controlled-replay requirement; RF-E2/C5 is optional and is not claimed |
| Public successful research package | C4 pass | public GitHub release asset, SHA-256, fresh public redownload, standalone verifier | portable real-case evidence; not an independent replication receipt |
| Public correctly blocked case | Pass | public GitHub release asset, frozen contract and compile report, standalone verifier | proves safe refusal, not a refuted hypothesis |
| GitHub-hosted signed replay | v1 release pass, non-C5 | run 30652669858; public receipt; verified Sigstore/SLSA attestation | external hosted infrastructure, but same repository owner is not an independent scientific operator |
| Evidence-bound Stage 4 | C3 pass | publication-control, claim, number, citation, and evidence-boundary tests | cannot promote missing evidence or failed execution into an effect claim |

## Public release evidence

Release:
<https://github.com/CKwin26/Auto-Agentic-Research-Forge/releases/tag/research-forge-v1-capability-acceptance-2026-08-01>

1. `research-forge-openml-39-hidden-target-v1.zip`
   - SHA-256:
     `8c788149ac3a892e8e83d6f3d217d7671d50d507094611317ca273d7943a0eab`
   - Fresh public redownload verification: `passed: true`
   - Recomputed verdict: `supported`
2. `research-forge-correctly-blocked-contract-v1.zip`
   - SHA-256:
     `7bb31c15bf45e6b832cbdc299315b4922b40d8524d75bf715677a88d4a78d227`
   - Fresh public redownload verification: `passed: true`
   - Run Specification count: `0`
   - Scientific Verdict created: `false`

GitHub's recorded asset digests matched the locally frozen SHA-256 values.
Both public assets were extracted into a fresh directory and verified with
Python's standard library without Research Forge, an Agent session, or an
unrecorded local file.

GitHub-hosted run 30652669858 then repeated both checks on `ubuntu-latest` and
attested `external-host-replay-receipt.json`. The public receipt digest is
`2917e281a1ad241290a3fe43642e2e0458a3a4b1ccd4ece89d1a9f924909e7d4`.
The attestation was independently verified with repository and exact signer
workflow identity constraints; details are in
`docs/github-hosted-public-replay-2026-08-01.md`.

## Quantitative acceptance

- Product capability audit: **18/18 supported, 0 unsupported claims**.
- Upstream adoption audit: **42/42 evidence-located, 0 unsupported claims**.
- AIRS: **40/40 official task-seed cells passed deterministic audit**.
- Workflow Run RO-Crate 0.5: **24/24 required requirements and 55/55 required
  checks passed; 0 required issues**.
- Controlled clean-room packages: **2/2 replay comparisons passed**.
- Public acceptance packages: **2/2 fresh-download verifiers passed**.
- GitHub-hosted public replay: **1/1 workflow passed; receipt provenance and
  signer identity verified; C5 is not required for v1 and remains unclaimed**.
- Python test collection at this policy update: **682 tests**.
- The release acceptance suite and subsequent main-branch CI runs passed; the
  environment-conditioned AIRS integration remains explicitly optional.

## Contract and scientific-safety invariants

For supported profiles, a contract is labelled `FROZEN_EXECUTABLE` only when
schema lint, materialized authorized resources, executable arm entrypoints,
metric formula, split/denominator binding, run matrix, evaluator binding, and
non-formal dry run all pass. Incomplete contracts remain `BLOCKED` and produce
structured amendment proposals rather than Run Specifications or Verdicts.

The benchmark fixtures cover supported, refuted, mixed, and inconclusive
scientific outcomes; missing execution and missing evidence remain distinct
from refutation. Stage 4 cannot mutate a frozen scientific Verdict, and its
numeric claims must bind to result artifacts before publication readiness can
be satisfied.

## Optional future promotion (not a v1 blocker)

Research Forge v1 exits at the bounded C4 product claim documented above. An
independently operated scientific replay receipt is not required for release,
publication-pipeline use, or ordinary deployment. The public package, hosted
replay, signed provenance, and controlled clean-room runs are the authoritative
v1 reproducibility evidence.

If a later release chooses to claim C5 or RF-E2, it must still obtain a receipt
from a separately controlled operator. The existing verifier remains available
for that optional promotion, but `rf_e2_awarded=false` is not a v1 failure and
does not leave the v1 acceptance incomplete.

## Permitted v1 claim

> Research Forge v1 is a bounded computational-research platform that compiles
> supported research contracts into controlled experiments, preserves
> machine-verifiable evidence and verdict authority, publishes portable
> verification packages, and replays sealed packages in isolated workers. Its
> supported profiles are explicit; cross-domain autonomy and independent
> external reproduction are not claimed.

## Reproduction commands

```powershell
python -m pytest -q
python scripts/audit_capability_registry.py --root .
python scripts/audit_upstream_capabilities.py --json-output upstream-audit.json --markdown-output upstream-audit.md
python scripts/validate_rocrate_external.py examples/rocrate-validation/stage3-minimal workflow-run-validation.json --profile workflow-run-crate-0.5
python examples/public-blocked-contract-case/verify.py
```
