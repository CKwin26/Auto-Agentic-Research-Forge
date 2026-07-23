# Dual-channel claim discovery

Research Forge discovers possible paper claims before it writes prose. The discovery contract has two independent channels:

1. **Project-authored claims** are extracted from the selected local project bundle. Conclusion, finding, contribution, novelty, report, and structured JSON fields are read with file path, line span, and content hash provenance.
2. **External attention signals** are normalized from RedFox WeChat search and scholarly metadata providers. They describe what a relevant audience is discussing or citing; they rank validation opportunities but do not prove a scientific statement.

The channels meet only in the recommender. A trend signal can raise a project claim's attention score when their structured concepts overlap. It is never added to the experiment evidence registry, never changes an idea verdict, and never satisfies a frozen protocol-output binding.

## Stage contract

| Stage | Claim-discovery responsibility |
|---|---|
| 01 — Discover | Inventory project resources, extract author claims, construct a project research fingerprint, query external providers, and rank recommended claims. |
| 02 — Freeze | Select a recommendation as a hypothesis candidate and freeze the literature, protocol, metrics, baselines, and required evidence independently. |
| 03 — Validate | Test the frozen hypothesis. Only bound project outputs and protected evaluations may support or contradict the scientific claim. |
| 04 — Synthesize | Write only to the maximum defensible claim tier and preserve the claim-to-evidence bindings in the audit artifacts. |

Stage 1 writes `stage_1_discovery/claim_discovery.json`. The file is included in the run completion certificate and contains:

- exact author-claim source spans and hashes;
- provider/query/status records, including degraded providers;
- normalized trend signals with stable source URLs and publication metadata;
- the project research fingerprint;
- ranked recommendations, score components, match reasons, missing context, and integrity warnings.

## Matching and quality gates

Matching uses structured research concepts rather than title equality. A recommendation needs at least two canonical concept overlaps and at least one distinctive concept from the author claim itself. Generic words such as “portfolio”, “return”, or “baseline” cannot create a match on their own. Scholarly results must also be relevant to the query that produced them.

Each recommendation exposes four scores separately:

- **project grounding** — how directly the statement is anchored in a local, hashed project span;
- **external attention** — relevant discussion/citation signal strength;
- **novelty opportunity** — whether the combination suggests a useful research gap to test;
- **evidence readiness** — whether the project already contains resources that may support a proper validation plan.

These are discovery scores, not truth probabilities or publication-readiness scores.

## Provider behavior

The default external channel uses:

- RedFox WeChat article search when `REDFOX_API_KEY` is available;
- Semantic Scholar scholarly search;
- Crossref recent-works search as the scholarly fallback.

Provider failures are isolated. Timeout, TLS, authentication, and rate-limit failures are recorded as degraded status; local author-claim extraction and the remaining providers continue. Secrets are read from environment files for the source project and are neither copied into the run nor serialized into the report.

## Commands

Inspect a project and include claim discovery:

```powershell
& .\.venv\Scripts\python.exe main.py bundle inspect "C:\path\to\project" --discover-claims
```

Persist claim discovery inside a certified four-stage run:

```powershell
& .\.venv\Scripts\python.exe main.py bundle close-loop "C:\path\to\project" `
  --output-root bundle_runs `
  --track auto `
  --discover-claims
```

The web interface enables this option by default for project-to-paper analysis and renders the results on the same primary page. Choosing a recommended claim still starts a separate freeze-and-validation decision; it does not silently promote the recommendation into evidence.
