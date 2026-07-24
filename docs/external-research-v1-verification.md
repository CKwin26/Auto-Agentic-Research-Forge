# External Research V1 verification report

Verified on 2026-07-24. The runtime readiness report remains the authoritative
source; this document records the implementation and release evidence without
turning missing external authorization into a false `READY`.

## Capability state

| Capability | State | Evidence |
|---|---|---|
| Academic search | `READY` | Recent live policy-controlled Paper Search MCP run, normalized resources, provider coverage, and passing release tests |
| GitHub research | `READY` | Recent live official-API Gateway run plus pinned-commit and metadata tests |
| Hugging Face research | `READY` | Recent live official-Hub Gateway run plus revision, card, gated-resource, and metadata tests |
| Public web | `READY` | A live policy-controlled Codex-native search returned five official OpenAI sources, generated immutable artifacts and a CoverageReport, and passed release tests; Synapai is not used for web search |
| Open access | `READY` | Unpaywall resolution and a live policy-controlled PLOS acquisition produced a 1,675,943-byte CC BY PDF, immutable SHA-256-bound artifact, snapshot, access decision, acquisition report, and audit event |
| Institutional access | `DEGRADED` | Local browser/session broker and security tests pass; a user-authenticated, hash-bound single-document handoff has not been demonstrated |
| Evidence analysis | `READY` | Recent rights-approved, hash-bound PDF/PaperQA evidence result with source spans plus release tests |
| External Research V1 aggregate | `DEGRADED` | The aggregate is true only when every component above is `READY` |

Run:

```powershell
research-forge retrieval readiness --refresh
```

## Final implementation audit

1. **Previous architecture problems.** External provider logic was fragmented,
   readiness could be inferred too loosely, formal web search had no real live
   proof, repeated immutable writes failed on timestamps, retry coverage could
   collide, and retrieval artifacts lacked direct Project/policy context.
2. **Domain objects.** RetrievalRequest, RetrievalRun, QueryPlan,
   CanonicalResource, ProviderRecord, IdentifierGraph, ResourceRelation,
   ResourceSnapshot, ResourceUseBinding, ResourceSet, CoverageReport,
   AccessDecision, CorpusManifest, EvidenceResult, readiness objects, and
   institutional-session records are implemented.
3. **Migrations.** `003_external_research_v1` installs the V1 store and
   `004_retrieval_artifact_context` adds schema-v2 artifact context without
   rewriting historical artifacts.
4. **Paper Search MCP.** Upstream `openags/paper-search-mcp` is pinned to
   version 0.1.4 / commit `c8b642183bb725f0a7faec89e58b558df09079d1`;
   only the audited public metadata search surface is exposed. Unsafe fallback
   downloads and Google Scholar are not callable.
5. **GitHub.** Official API search and repository/commit/release/issue/tree,
   README, license, citation, dependency, and configuration retrieval are
   available under policy and budget control.
6. **Hugging Face.** Official Hub access covers models, datasets, Spaces,
   cards, files, licenses, metadata, and pinned revisions; unauthorized gated
   content blocks rather than falling through.
7. **Public web search.** Codex-native Web Search is the authoritative local
   backend and reuses local Codex authentication without a separate API key.
   Automatic Workflow routing does not select Synapai or the optional formal
   Responses adapter. The optional server adapter remains explicitly callable
   for deployments that deliberately configure it.
8. **Institutional security.** Credentials, password fields, cookies, MFA and
   Duo secrets never enter prompts, artifacts, audit rows, ordinary storage or
   the Research Forge UI. The user performs authentication in the institution
   page; handoff is local, owner-bound, revocable, expiring, and single-document.
9. **PaperQA.** `paper-qa==2026.3.18` indexes only rights-approved snapshots;
   the corpus index is content-hash bound, answers require EvidenceSpan
   provenance, and PaperQA has no verdict authority. Synthesis now creates its
   own cross-phase bindings and frozen corpus, then maps registered hypotheses
   to source spans instead of returning the former stale runtime placeholder.
10. **Workflow v2.** All 47 required External Research nodes are registered
    across Discovery, Protocol, Experimentation, Synthesis and Repair. They use
    the existing persistent DAG, StepInstance, pause/resume, explicit retry,
    bounded transient retry, upstream blocking, Artifact Store and audit ledger.
11. **Interfaces.** The required readiness, policy, run, coverage, resource,
    institution and corpus API/CLI surfaces exist. The minimal UI shows network
    mode, exact capability state, phase/step state, provider purpose, coverage,
    resources and institutional actions without password fields.
12. **Coverage.** CoverageReport records executed queries, used/unused/failed
    providers, counts by resource type and access mode, license/metadata gaps,
    blind spots and prohibited coverage claims.
13. **Security and rights.** New Workflow handlers cannot bypass the Gateway;
    default mode is offline; sanitized-query digests, secret scanning, budgets,
    allowed providers/domains/methods, rights decisions, SSRF protection and
    immutable source snapshots are tested.
14. **Readiness.** It is derived from migration state, release evidence,
    provider health and recent real artifacts/runs. A package import, API key or
    fixture cannot make a capability ready. A real policy-controlled
    Codex-native search can satisfy `PUBLIC_WEB_READY`.
15. **Targeted tests.** The External Research, Gateway, Workflow scheduler,
    PaperQA synthesis and Web API suites pass. The release validator writes
    time-stamped test evidence instead of relying on a hard-coded README count.
16. **Full regression.** The repository CI collects the current test suite on
    every pull request and reports environment-conditional skips explicitly.
17. **Performance and limits.** Query/result/byte/cost budgets are enforced.
    Transient network and rate-limit errors receive at most three retries after
    the first attempt; auth, policy, license, schema and integrity failures do
    not auto-retry. Explicit retries retain old CoverageReports and audit rows.
    Failed OA acquisitions now append a bounded provider/domain/classification
    audit event without creating a full-text snapshot.
18. **Open-access acquisition.** A real PLOS PDF was resolved through Unpaywall,
    downloaded through the owner-approved loopback proxy fake-IP route, checked
    as `application/pdf`, frozen at SHA-256
    `0b42031ee41e2aa7be3e324136cbbc07e3a432a3cf56f3286e42088d2f1ea081`,
    and recorded with CC BY rights. The narrowly scoped proxy exception does not
    permit literal IPs, local/private hostnames, non-HTTPS targets, or unchecked
    redirects.
19. **Implementation surface.** Changes cover `research_forge/retrieval/`,
    Workflow v2 handlers/scheduler, CLI/Web API, the minimal UI, dependency
    extras, release tests, validation tooling and the External Research docs.
20. **Git state.** The worktree contains unrelated historical experiment,
    manuscript, image and skill changes. They are intentionally not included in
    this verification and must not be staged with a broad `git add -A`.
21. **Release recommendation.** Publish with an explicit file allowlist on the
    existing Research Forge update branch. Exclude `.rfab`, runs, provider
    responses, PDFs, screenshots, caches, credentials and local configuration.
22. **Explicitly deferred component.** The owner chose to defer institutional
    access. It therefore remains `DEGRADED` until a real SSO/MFA and
    single-document handoff is completed; this does not block public search,
    open-access acquisition, or PaperQA. Synapai/OpenAI Responses authorization
    is not a web-search blocker.

## Verification commands

```powershell
python scripts/validate_external_research_v1.py --workflow-root .rfab
python -m pytest -q
python -m mypy research_forge/retrieval research_forge/workflow_scheduler.py --follow-imports=skip --ignore-missing-imports
pnpm --dir research-forge-ui run build
git diff --check
```
