# Retrieval operations

## Defaults

Every new Project starts offline. Enabling network access requires an immutable
owner-approved policy with explicit providers, infrastructure domains, methods,
resource types and budgets.

For machines whose local HTTPS proxy uses `198.18.0.0/15` fake-IP DNS, the owner
may add `--allow-proxy-fake-ip --approved-by <owner>` to `retrieval policy-set`.
This does not disable SSRF checks: it applies only to public hostnames routed
through a loopback proxy. Literal IP targets, local/private names, non-HTTPS
targets and unsafe redirects remain blocked.

## Failure behavior

- transient network and rate-limit failures: first attempt plus at most three
  retries;
- authentication, policy, license, schema and integrity failures: no automatic
  retry;
- one provider fails while another succeeds: run is `degraded`;
- every requested provider fails: run is `failed`;
- policy denies before execution: run is `blocked` and no provider is called.

Completed provider attempts and artifacts remain available after pause, restart,
or retry. Idempotency keys prevent duplicate external operations.
Failed and cancelled runs are terminal for ordinary `run` calls: only the
explicit retry operation starts another attempt. Each explicit retry produces a
new immutable CoverageReport identity while retaining the previous report and
audit events. Identical artifact content is reused only when producer and input
lineage are also identical; equal bytes with different provenance receive
different artifact identities.

ProviderRecord and ResourceSnapshot identities include their immutable source
artifact lineage. Repeating identical metadata under a successor policy creates
new provenance-bound records without mutating or colliding with the historical
records.

`cache_only` is a strict offline read of frozen provider responses. A miss fails
explicitly and never calls an adapter. Reuse creates a new ResourceUseBinding
for the requesting Study and Step while retaining the original immutable
response and snapshot.

## Interfaces

The primary API includes:

- `GET /retrieval/readiness`
- `GET|PUT /projects/{project_id}/retrieval-policy`
- `POST /studies/{study_id}/retrieval-runs`
- `GET /retrieval-runs/{run_id}`
- `GET /retrieval-runs/{run_id}/coverage`
- `GET /studies/{study_id}/resources`
- `GET /resources/{resource_id}`
- `GET /resources/{resource_id}/relations`
- institution-session create/status/reauthenticate/revoke routes
- corpus create/index/query routes

The CLI mirrors readiness, policy, search, status, coverage, resources,
institution-session and corpus operations. PaperQA indexing/querying uses the
pinned 2026.3.18 runtime and remains blocked when the runtime, rights-approved
snapshots, or Codex synthesis backend are unavailable.

Preferred nested CLI forms are `retrieval policy show|set` and
`retrieval corpus build|query`; the earlier hyphenated commands remain
read-compatible aliases.

## Artifact lifecycle

A planned retrieval writes both the legacy `query_plan.json` and the V1
contract name `retrieval_plan.json`. Execution records policy, routing,
sanitized queries, redaction, raw responses, normalized resources, identifier
and relation graphs, deduplication, `metadata_verification_report.json`, access
decisions, and coverage. Full-text acquisition and PaperQA add
`acquisition_report.json`, `paperqa_corpus_manifest.json`, and
`evidence_results.jsonl` only when those operations genuinely occur. The
explicit freeze operation writes `frozen_resource_set.json`; the system never
labels a draft set as frozen merely to satisfy an artifact checklist.
