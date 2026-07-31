# GitHub-hosted public replay acceptance — 2026-08-01

## Outcome

The public replay workflow ran on a fresh GitHub-hosted `ubuntu-latest`
runner, downloaded both public Research Forge v1 acceptance packages, checked
their frozen SHA-256 digests, executed both standalone standard-library
verifiers, emitted a machine-readable receipt, and created a GitHub artifact
provenance attestation for that receipt.

- Workflow run:
  <https://github.com/CKwin26/Auto-Agentic-Research-Forge/actions/runs/30652669858>
- Source commit:
  `28ee2ffbb688528b70b3bf505876177e595863c6`
- Workflow:
  `.github/workflows/public-replay-verification.yml`
- Runner environment: `github-hosted ubuntu-latest`
- Run result: success

## Replayed results

Successful package:

- SHA-256:
  `8c788149ac3a892e8e83d6f3d217d7671d50d507094611317ca273d7943a0eab`
- Verifier: `passed: true`
- Profile: `benchmark_prediction_v1`
- Verdict: `supported`

Correctly blocked package:

- SHA-256:
  `7bb31c15bf45e6b832cbdc299315b4922b40d8524d75bf715677a88d4a78d227`
- Verifier: `passed: true`
- Protocol status: `blocked`
- Run Specification count: `0`
- Scientific Verdict created: `false`

## Durable receipt and attestation

- Public receipt:
  <https://github.com/CKwin26/Auto-Agentic-Research-Forge/releases/download/research-forge-v1-capability-acceptance-2026-08-01/external-host-replay-receipt.json>
- Receipt SHA-256:
  `2917e281a1ad241290a3fe43642e2e0458a3a4b1ccd4ece89d1a9f924909e7d4`
- Public offline attestation bundle:
  <https://github.com/CKwin26/Auto-Agentic-Research-Forge/releases/download/research-forge-v1-capability-acceptance-2026-08-01/sha256-2917e281a1ad241290a3fe43642e2e0458a3a4b1ccd4ece89d1a9f924909e7d4.jsonl>
- Bundle SHA-256:
  `44787a156462ef604cb5e6c3e6872e1d3662c09ec4f32af5ff1581480b28b372`

`gh attestation verify` succeeded while enforcing repository
`CKwin26/Auto-Agentic-Research-Forge` and signer workflow
`CKwin26/Auto-Agentic-Research-Forge/.github/workflows/public-replay-verification.yml`.
The verified certificate records the GitHub-hosted runner, `main` ref, source
commit, run URL, workflow identity, and a Sigstore transparency-log timestamp.

## Claim boundary

This is real external-host execution and cryptographically verifiable artifact
provenance. It is not an independent scientific-operator review because the
workflow belongs to the same repository owner. The frozen receipt therefore
states:

```json
{
  "independent_scientific_operator": false,
  "c5_awarded": false
}
```

Research Forge may claim a signed GitHub-hosted replay receipt. It may not
claim C5 independent reproduction until a separately controlled operator
reviews the package, recomputes the metric, and signs an independent receipt.
The independent handoff is published as
<https://github.com/CKwin26/Auto-Agentic-Research-Forge/issues/6>.
