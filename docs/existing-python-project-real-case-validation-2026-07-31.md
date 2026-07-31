# Existing Python project real-case validation — 2026-07-31

## Scope

This acceptance validates a narrow Research Forge capability: an owner can
allow-list a slice of an existing Python project, freeze the files and the
expected metric boundary, and replay that sealed package in fresh offline
Docker workspaces.  It does **not** claim that arbitrary Python repositories
can already be converted into a complete two-arm scientific experiment.

Runtime entrypoint:

```text
research_forge.existing_project_replay
scripts/run_existing_project_replay.py
```

Deterministic controls:

- only explicitly listed files enter the package;
- secret-bearing names, parent traversal and symlinks are rejected;
- the host source path is not written to the package or report;
- package identity is computed from the spec and file hashes, not time;
- the input mount and container root are read-only;
- the output mount is separate and writable;
- networking, added devices and Docker socket mounts are disabled;
- metrics come from a frozen parser, not from an agent interpretation;
- every case runs twice in separate workspaces;
- both replays must match the frozen historical expectation and each other.

## Real case 1 — stock disclosure extractor

Source class: pre-existing user stock-research repository.

Frozen slice:

- deterministic annual-report extraction implementation;
- its package initializer;
- its existing disclosure-extractor test module.

Observed result:

```json
{
  "case_id": "stock-disclosure-extractor-real-project-v1",
  "status": "verified",
  "tests_run": 5,
  "pass_rate": 1.0,
  "historical_match": true,
  "cross_replay_equal": true,
  "clean_workspace_count": 2,
  "package_sha256": "4ed6d143856ed736294d4048ab5b0f5230c4a232a3b7919db0150c6864e65964"
}
```

## Real case 2 — adviser adapter inventory

Source class: pre-existing user investment-adviser repository.

Frozen slice:

- the existing adapter inventory implementation;
- its package initializer;
- its existing adapter-inventory test module.

Observed result:

```json
{
  "case_id": "advisor-adapter-inventory-real-project-v1",
  "status": "verified",
  "tests_run": 1,
  "pass_rate": 1.0,
  "historical_match": true,
  "cross_replay_equal": true,
  "clean_workspace_count": 2,
  "package_sha256": "373717c5c465b19e28aa62e81e6b20167f663e864f1e05b8bb0e81be3fbb3bc9"
}
```

Both cases used image ID:

```text
sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
```

## Claim boundary

These are **real-case, same-host isolated replays**.  They establish C4 for
the narrow sealed-project replay utility.  They are not C5 independent
validation because the same Research Forge installation orchestrated both
containers.  They also do not yet prove that the full
`existing_python_project_v1` Stage 2 → Stage 3 contract/build/verdict path is
C4.  Independent workers, external receipt verification and scientific
baseline/treatment replay remain separate acceptance items.
