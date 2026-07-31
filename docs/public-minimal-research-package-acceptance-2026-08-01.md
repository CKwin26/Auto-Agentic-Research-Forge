# Public minimal research package acceptance (2026-08-01)

## Outcome

Research Forge generated and independently rechecked a deterministic minimal
research package for the real OpenML Task 39 hidden-target acceptance case.
The package contains the frozen candidate-visible data, evaluator-only target,
baseline and treatment source, both submissions, isolated evaluator reports,
the final acceptance report, a hash manifest, and a standalone standard-library
`verify.py`.

The package was extracted into a fresh directory and verified without the
Research Forge package, the original Codex conversation, or network access.
The verifier returned `passed: true`, profile `benchmark_prediction_v1`, and
verdict `supported`.

## Package facts

- Package SHA-256:
  `8c788149ac3a892e8e83d6f3d217d7671d50d507094611317ca273d7943a0eab`
- Public release:
  <https://github.com/CKwin26/Auto-Agentic-Research-Forge/releases/tag/research-forge-v1-capability-acceptance-2026-08-01>
- Public asset:
  <https://github.com/CKwin26/Auto-Agentic-Research-Forge/releases/download/research-forge-v1-capability-acceptance-2026-08-01/research-forge-openml-39-hidden-target-v1.zip>
- Source task: OpenML Task 39, Sonar dataset 40 version 1
- Result: baseline accuracy 0.5714285714; treatment accuracy 0.9047619048;
  paired effect +0.3333333333; verdict `supported`

## Correctly blocked public example

`examples/public-blocked-contract-case` is the corresponding negative case.
Its generated public ZIP freezes the contract input and actual compile report;
its standalone verifier confirms that the contract is blocked before formal
execution because resources, arm delta, metric formula, sampling frame, and
target rule are missing. It emits no run specifications and no scientific
verdict.

- Public asset:
  <https://github.com/CKwin26/Auto-Agentic-Research-Forge/releases/download/research-forge-v1-capability-acceptance-2026-08-01/research-forge-correctly-blocked-contract-v1.zip>
- Package SHA-256:
  `7bb31c15bf45e6b832cbdc299315b4922b40d8524d75bf715677a88d4a78d227`

Both assets were downloaded back from the public release into a fresh
directory. Their GitHub asset digests matched the local digests, and both
standard-library verifiers returned `passed: true`.

The same public assets were subsequently replayed on a GitHub-hosted runner.
The successful run and GitHub/Sigstore provenance verification are frozen in
`docs/github-hosted-public-replay-2026-08-01.md`. The signed receipt is also a
public release asset.

## Claim boundary

Permitted claim: a real hidden-target case can be packaged deterministically
and verified from a fresh extraction directory without Agent-session context.

Not yet claimed: no external independent scientific operator has signed a
reproduction receipt. Public availability, self-verification, external-host
execution, and GitHub/Sigstore provenance establish a portable C4 artifact and
a signed hosted replay, but do not establish C5 independent reproduction.
