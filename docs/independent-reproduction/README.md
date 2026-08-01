# Independent scientific replay receipts

Research Forge v1 does not require an external independent operator for release.
Its release target is the bounded C4 claim documented in the v1 completion
audit. C5 remains an optional future maturity promotion.

If a later release chooses to claim C5, Research Forge awards it only after a
separately controlled operator replays a frozen public package, recomputes its
registered result, and signs the receipt with a key that the project owner does
not control.

The machine verifier checks the package digest, registered numbers, verdict,
operational declarations, trusted-key fingerprint, and Ed25519 signature. It
does **not** infer that an operator is independent from a self-asserted JSON
field. A maintainer must verify the public identity/key relationship and pass
`--approve-independent-identity` explicitly.

```text
research-forge verify-independent-replay-receipt receipt.json \
  --expectation docs/independent-reproduction/openml-39-expectation.json \
  --trusted-key operator-key=operator-public-key.pem \
  --project-owner-identity CKwin26 \
  --approve-independent-identity
```

Until all checks pass, the result reports `c5_eligible: false` and no capability
manifest may be upgraded to C5. This does not block Research Forge v1 release or
change its accepted C4 capability claims.
