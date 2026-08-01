# Capability assurance

Research Forge separates a product feature list from machine capability claims.
The human catalog has 22 features; the registry has 18 capability IDs. A product
feature may compose several registered capabilities, and several product
features may reuse the same capability. Therefore `22` and `18` are not two
competing scores.

The authoritative mapping is
`research_forge/product_capability_map.yaml`. The assurance command rejects the
mapping unless features 1-22 are consecutive, every referenced capability ID
exists, and the union of mapped IDs is exactly the 18-item registry.

## Three audit layers

```text
registry audit
  schema, claim boundary, evidence locators, declared maturity

evidence audit
  verified commit, verification time, environment digest, expiry,
  source/test drift, positive/negative/mutation coverage

capability replay
  rerun the registered positive, negative, and mutation suites
```

Run the first two layers and reuse the latest still-fresh CI replay:

```powershell
python scripts/audit_capability_assurance.py --root .
```

Force a new local replay of every registered falsification suite:

```powershell
python scripts/audit_capability_assurance.py --root . --execute-replay
```

Evidence becomes stale when its expiry passes, a registered source/test/evidence
file changes after the verified commit, a locator disappears, or replay fails.
The command then exits non-zero instead of preserving a green capability label.

## Three independent dimensions

Each capability reports three dimensions rather than combining them into one
ambiguous label:

- **Implementation maturity C0-C4 for release acceptance**: concept,
  implementation, component validation, controlled E2E, and real-case
  validation.
- **Evidence level E0-E3 for release acceptance**: documented, component
  tested, controlled replay, and real-case evidence.
- **Scope**: `component`, `bounded`, `limited_real_case`, or `extension_only`.

For example, controlled clean-room replay is implementation maturity C3,
evidence level E2, scope `limited_real_case`. C5/E4 values remain readable only
for historical-schema compatibility. Research Forge does not require an
external independent researcher, and C5/E4 is not a product gap or release
milestone.

## Product-to-registry mapping

| Product feature | Registered capability ID(s) |
|---|---|
| 1. Four-stage Study workflow | `workflow.control_loop` |
| 2. Project scan and claim discovery | `workflow.control_loop`, `retrieval.gateway` |
| 3. External retrieval | `retrieval.gateway` |
| 4. PaperQA evidence analysis | `retrieval.paperqa_fulltext` |
| 5. Discovery portfolio | `workflow.control_loop`, `retrieval.gateway` |
| 6. Stage 2 contract | `research_contract.compiler` |
| 7. Typed profiles and unsupported routing | `scientific_execution.typed_profiles`, `scientific_completion.cross_domain_autonomy` |
| 8. OpenML hidden-target experiment | `scientific_execution.hidden_target_benchmark` |
| 9. AIRS RAD | `benchmark.official_airs_rad` |
| 10. Existing-project replay | `reproduction.sealed_existing_project_replay` |
| 11. Structural data qualification | `data_quality.great_expectations_gate` |
| 12. Evidence and verdict chain | `scientific_completion.empirical_loop` |
| 13. PROV projection | `interchange.prov_external_validation` |
| 14. Workflow Run RO-Crate | `interchange.rocrate_external_validation` |
| 15. Repair and rollback | `workflow.control_loop`, `research_contract.compiler` |
| 16. Controlled clean-room replay | `reproduction.controlled_clean_room_replay` |
| 17. Public reproduction package | `reproduction.public_minimal_research_package` |
| 18. Evidence-bound paper authoring | `authoring.evidence_bound` |
| 19. SCI/SSCI writing constraints | `authoring.evidence_bound` |
| 20. Nuwa panel | `review.nuwa_panel` |
| 21. Editable scientific diagrams | `visualization.drawio_export` |
| 22. Completion/public replay record | `workflow.control_loop`, `reproduction.public_minimal_research_package` |

## Signature and provenance boundary

Research Forge uses two different objects:

1. A Research Forge Completion Record uses the project-defined schema and
   Ed25519 verification. It is not Sigstore or SLSA provenance.
2. A public GitHub-hosted build or replay artifact may carry GitHub artifact
   attestation, a Sigstore-issued certificate, and an SLSA provenance predicate.
   This proves the artifact's hosted build origin and workflow identity, not an
   independent scientific conclusion.

Neither object is described as external independent scientific reproduction.

## Scientific Contribution Gate

The gate is implemented separately from Nuwa review in
`research_forge/scientific_contribution_gate.py`. It checks baseline weakness,
arm identity, mechanism isolation, expected information value, sample adequacy,
novelty, and claim identifiability before contract freeze. Nuwa remains an
advisory same-model review panel and cannot override the deterministic gate.

## Evaluator-family robustness

When a contract uses a learned evaluator, Contract Compiler v2 requires a
second, genuinely distinct evaluator family plus a frozen disagreement policy.
Stage 3 stores append-only family results and reports aggregate metric
agreement, row-level disagreement, directional agreement, and verdict
stability. An unstable or unadjudicated comparison blocks formal completion;
it never rewrites either evaluator's historical output.
