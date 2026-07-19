# Stage 1: evidence-bound discovery and research planning

Stage 1 turns a research idea into an approved, source-bound research plan. It is not one agent call. It is a sequence of model judgments surrounded by deterministic retrieval, validation, provenance, and human gates.

## Substages and ownership

| Substage | Owner | Required evidence |
|---|---|---|
| 1. Scope the review | Codex | Review question, 3-6 unique English queries, key concepts, inclusion/exclusion criteria, scope limitations |
| 2. Retrieve records | Python | Raw Crossref and Semantic Scholar JSON stored under the discovery ID and hashed |
| 3. Normalize and deduplicate | Python | DOI, arXiv ID, or normalized-title identity; merged provider IDs and query provenance |
| 4. Verify metadata | Python | Cross-provider match or exact Crossref DOI / Semantic Scholar paper-ID lookup |
| 5. Apply hard filters | Python | Fixed type, author, year, title-pattern, relevance, and verification rules |
| 6. Screen relevance | Codex | Exactly one conservative `core`, `supporting`, or `exclude` decision for every supplied candidate ID, in order |
| 7. Select the bounded set | Python | At least two core papers, at least five accepted papers, fixed query-coverage quotas, then deterministic ranking |
| 8. Synthesize related work | Codex | Themes and falsifiable novelty candidates using only exact approved source IDs, at least two distinct sources per item |
| 9. Validate and freeze provenance | Python | Source resolution, latest-plan/discovery binding, screening consistency, raw-response hashes, review hashes, and source hashes |
| 10. Review the evidence | Human | Exact review-ID confirmation; the tool cannot approve its own literature review |
| 11. Draft the research contract | Codex + Python | Planner sees only the approved source set; the resulting plan is bound to the review hash and exact source IDs |

Codex never owns network requests, source registration, thresholds, quotas, hashes, acceptance, or state transitions. Python never pretends to make semantic relevance or research-gap judgments. The human gate distinguishes a generated novelty hypothesis from an accepted direction for the next stage.

## Commands

```powershell
$py = ".\.venv\Scripts\python.exe"

& $py main.py init `
  --name "my-study" `
  --idea "A concrete scientific question"

& $py main.py literature plan my-study `
  --focus "The method family, baseline family, and evaluation concern to cover"

& $py main.py literature discover my-study `
  --rows-per-query 20 `
  --include 12 `
  --from-year 2020 `
  --min-relevance 0.15

& $py main.py literature screen my-study `
  --max-candidates 40 `
  --include 12

& $py main.py literature synthesize my-study
& $py main.py literature audit my-study
```

At this point, open `literature/review.md`. Check whether the selected papers really cover the question, whether the proposed gaps are defensible, and whether important prior work is missing. The full Stage 1 audit is expected to fail the approval and research-plan checks because neither exists yet:

```powershell
& $py main.py literature approve my-study `
  --confirm "review-..." `
  --novelty "novelty-..." `
  --note "I reviewed the shortlist and bounded novelty map."

& $py main.py literature audit my-study
```

Approval closes the literature gate but does not complete Stage 1. The planner is allowed to use that approved gate even while the full Stage 1 audit still reports the missing plan:

```powershell
& $py main.py plan my-study `
  --message "Turn the approved novelty candidate into the smallest credible first study."

& $py main.py literature audit my-study
```

The final audit passes only when the generated plan is freeze-ready, has no blocking questions, and is hash-bound to the approved review and exact source set.

Creating a newer search plan, changing a screened source, changing the review, or changing a raw provider response invalidates the approval and/or plan binding. Re-run the affected downstream substages instead of reusing stale artifacts.

## Provider configuration

The adapters use the public [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) and [Semantic Scholar Academic Graph API](https://www.semanticscholar.org/product/api). No API key is required for the basic local path, but these optional environment variables improve provider etiquette or rate-limit capacity:

```powershell
$env:CROSSREF_MAILTO = "you@example.com"
$env:S2_API_KEY = "..."  # optional; never persisted in Stage 1 artifacts
```

A provider may be recorded as degraded when only part of the query plan succeeds. Stage 1 still requires evidence from both configured providers and records the degradation as a warning. It never silently substitutes model memory for failed retrieval.

## Durable artifact chain

```text
research idea
  -> literature/search_plans/<search-plan-id>.json
  -> literature/discoveries/<discovery-id>/raw/*.json
  -> literature/discoveries/<discovery-id>/record.json
  -> literature/screening.json
  -> literature/sources/<source-id>.json
  -> literature/review.json + review.md
  -> literature/approval.json
  -> literature/stage1_manifest.json
  -> plans/<plan-id>.json
  -> plans/<plan-id>-evidence.json + plan_evidence_binding.json
```

`stage1_manifest.json` hashes the search strategy, discovery record, every raw provider response, screening record, selected source records, review, and approval. Contract freezing rechecks these hashes and also freezes the plan-to-review binding.

## Audit pass conditions

A scientific Stage 1 passes only when all of the following are true:

- the latest search plan is valid and is the plan used by the latest discovery;
- the candidate pool contains at least eight normalized records;
- at least two providers contributed successful requests;
- semantic decisions cover the exact evaluated candidates, with at least two core and five accepted records;
- the included candidates, source records, screening record, and discovery record agree;
- at least five included paper sources have verified canonical metadata;
- every theme and novelty candidate cites at least two distinct included source IDs;
- the review, screening, approval, and discovery identifiers agree;
- the approval hashes match the current review and screening files;
- every file listed in the Stage 1 manifest exists under the project and matches its frozen hash.

## Current scientific boundary

This stage supports a bounded scoping review and a defensible first-study decision. It does not yet download and parse every full paper, search every scholarly index, perform forward/backward citation chasing, assess risk of bias, or establish publication-level novelty. The generated gap wording therefore stays conditional: the bounded reviewed set may fail to contain relevant prior art. Full-text and domain-expert review remain required before making priority or novelty claims in a paper.

## Verified real run

The first live Stage 1 run is stored at `stage1_runs/research-agent-evidence-v2`. It executed six search queries against both adapters, normalized 138 candidates, semantically screened 22 verified candidates, retained 12 papers, and generated five related-work themes plus four novelty candidates. The operator approved exact review ID `review-2026-07-17T153937.084280+0000-3d8078` and selected `novelty-02`. The resulting freeze-ready plan is bound by its own hash, the review hash, and the exact 12-source set. All 23 scientific Stage 1 checks pass. See [the verified run record](real-stage1-2026-07-17.md).
