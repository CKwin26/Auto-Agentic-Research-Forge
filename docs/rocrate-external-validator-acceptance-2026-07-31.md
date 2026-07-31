# RO-Crate external validator acceptance (2026-07-31)

## Outcome

Research Forge's Stage 3 interchange metadata was checked by the maintained
CRS4 `roc-validator` package, not only by an internal JSON shape assertion.
The reusable fixture under `examples/rocrate-validation/stage3-minimal` passed
the required checks of the `ro-crate-1.1` profile.

The first external run exposed four actual required defects in the previous
export: missing `conformsTo` on the metadata descriptor, and missing
`description`, `license`, and `datePublished` on the root dataset. The Stage 3
exporter now emits those properties and explicit File entities. The fixture
then passed both an operator cache-warming run and a production-style offline
run.

## Frozen acceptance facts

- Checker: `crs4/rocrate-validator`
- Distribution: `roc-validator`
- Version output: `rocrate-validator 0.11.3_b01f630+0-dirty`
- Profile: `ro-crate-1.1`
- Mode of final acceptance: offline, cached context only
- Exit code: 0
- Required issues: 0
- Metadata SHA-256: `51b9c6724541966a016efa88553ad33a984daa4b21f3f0d6543a7409891084d1`
- Raw validator report SHA-256: `deee65b48cc26fdf6f2c98048c63dc06d69bc1eb8d2b4a3560e16ac66c0bdff1`

The platform freezes the validator's JSON report outside the crate so the
checker cannot modify the object being validated. Production validation
defaults to offline mode; context cache warming remains an explicitly operated
deployment action rather than an ungoverned Workflow v2 network call.

## Claim boundary

Permitted claim: Research Forge invokes the maintained external checker and a
Stage 3 minimal crate passes all required RO-Crate 1.1 checks.

Not claimed: Workflow Run Crate 0.5 conformance, Provenance Run Crate
conformance, third-party certification, external scientific reproduction, or
validation of every historical package.
