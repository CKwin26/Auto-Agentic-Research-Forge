# Controlled clean-room replay acceptance — 2026-07-31

## Scope

This acceptance run validates the operational clean-room launcher and two
sealed reproduction packages. It does **not** claim an external organization,
an independent cloud account, KMS/HSM receipt signing, RF-E2, C5, or scientific
generalization beyond the controlled paired-comparison fixture.

Each candidate worker was a newly created `python:3.12-slim` Docker container
with:

- `--network none`;
- a read-only root filesystem;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- a temporary `/tmp` filesystem;
- only the signed reproduction package mounted read-only as scientific input;
- one empty evidence directory mounted writeable;
- no repository, source project, Workflow database, cache, credential,
  signing key, Docker socket, or original control-plane endpoint mounted.

The embedded runner independently recomputed the paired baseline mean,
treatment mean, effect, interval, verdict, sample IDs, and completed RunCell
counts from a hashed input asset. The host comparator then applied the frozen
policy to the unsigned worker result.

## Results

| Case | Sealed archive SHA-256 | Manifest SHA-256 | Worker report SHA-256 | Result |
|---|---|---|---|---|
| A | `802bde8c18bfe53e3db706ed9e6fc1ec1a882bb1a6142705cd0ec7d86ffb226f` | `93a40f094b4290ad612c842d398e4c21b7aa7d6a7b6866b283ad8992f3e373d6` | `1b54aace238375a577d13c42154926ea713be32d7a93581e67bcb4569a884c81` | comparison passed; coverage full; all clean-room invariants true |
| B | `8195a67ce99ab57f090ef58fdc0c7abe00162b99746cadca7d34e5a7c54f4d40` | `a2c2e1450dda1ef7767c4f3aa3232960478695f1f92f661100ff9253fddf7d17` | `01b1437dc4800870ec3c361d40f3d33df8aedf34fe57d5329c61b9170acc0dae` | comparison passed; coverage full; all clean-room invariants true |

The acceptance summary was written to an operator-selected directory outside
the repository. Generated packages, logs, reports, and candidate outputs are
intentionally not committed.

## Evidence grade

Both runs remain `RF-E1_package_verified` and `rf_e2_awarded=false`. The
explicit blocker is: no separately operated verifier receipt backed by an
independent KMS, HSM, or external organization. The worker report is unsigned
by design, and the local control plane cannot promote it by self-attestation.

## Re-run

```powershell
python scripts/run_clean_room_acceptance.py <stage3-completion.zip> <external-output-directory>
```

The reusable launcher is `research_forge.clean_room_replay`, and the sealed
example runner is `examples/clean-room-replay/replay.py`.
