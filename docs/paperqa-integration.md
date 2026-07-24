# PaperQA integration

- Upstream: `Future-House/paper-qa`
- Release pin: `paper-qa==2026.3.18`
- License: Apache-2.0

Only frozen snapshots whose AccessDecision permits model processing may enter a
Corpus. The corpus manifest binds snapshot hashes, parser version, embedding
model hash and LLM configuration hash. The local adapter uses PaperQA's real PDF
parser and sparse index. Multimodal enrichment is disabled because PaperQA's
default would make an implicit external LLM call while indexing figures. It does
not treat package availability or a placeholder runner as a successful index.

The parsed `Docs` state is persisted as an immutable binary artifact linked to
the source snapshot artifacts. Before loading it, Forge verifies the index
manifest, PaperQA version, corpus manifest, producer, input lineage, file
extension, content hash, object type, corpus name, document keys, and resource
bindings. A service instance caches only the most recently verified corpus. Old
corpora without a persisted state remain readable through the slower legacy
reparse path.

Every persisted factual answer requires at least one EvidenceSpan inside the
frozen corpus. The only span-free result that may be persisted is a bounded
`unanswerable` abstention with an empty answer, an abstention reason, and a
frozen Evidence Bundle hash. PaperQA results always have
`verdict_authority=false` and cannot modify a Contract, Study verdict, or
historical evidence record.

The product adapter exposes the complete V1 corpus lifecycle:

- `create_corpus`, `add_document`, and `remove_document`;
- `build_index`, `get_index_status`, and `invalidate_index`;
- `query_evidence`, `synthesize_related_work`, and
  `find_conflicting_evidence`.

Document additions and removals produce a new manifest-derived corpus identity;
they never mutate a corpus whose index may already be cited. Index invalidation
preserves the old manifest, index hash, and index artifacts, records the reason,
and prevents further queries against that corpus. Related-work and conflict
operations remain ordinary evidence queries with source spans and no verdict
authority.

Answer synthesis is delegated to the local Codex SDK over only the contexts
retrieved from the frozen PaperQA index. Returned context identifiers must be a
subset of those contexts; page, offsets, snapshot identifier, index hash and
quote hash are preserved in the EvidenceResult.

## Synthesis execution v2

The synthesis layer now keeps one Codex client warm in a persistent worker.
The worker is an execution optimization, not a conversational memory:

- every batch starts a new `ephemeral=True` thread;
- every question carries its own explicit Evidence Bundle;
- evidence from another question is forbidden;
- source text is treated as untrusted data with no instruction authority;
- the default batch limit is 20 questions and larger requests are split;
- the total serialized evidence limit is 240,000 characters per batch.

The v2 answer schema records `answerability`, exact sentence-level evidence
citations, conflicts, unsupported statements, limitations and an abstention
reason. Deterministic validation rejects evidence IDs outside the submitted
bundle, internal ID leakage, unsupported statements and any factual answer
sentence without an exact cited `claim_span`.

Answer reuse is keyed by a SHA-256 digest over:

- normalized question hash;
- ordered Evidence Bundle IDs and hashes;
- Corpus manifest and PaperQA index hashes;
- synthesis prompt version;
- effective Codex provider/model configuration hash;
- output schema version;
- evidence policy version.

Changing any authority input invalidates the answer cache. Cache reuse still
requires an immutable EvidenceResult with valid source spans.

Each uncached synthesis writes a `synthesis_latency` artifact with
`queue_wait_ms`, worker/client initialization, thread start, PaperQA retrieval,
evidence and request serialization, model round trip, schema/evidence
validation and persistence. The current Codex SDK does not expose transport
upload time or time-to-first-token separately, so those fields are recorded as
`null` rather than fabricated. A deterministic diagnosis identifies the
dominant measurable component, such as cold start, worker queue, retrieval or
model round trip.

## PaperQA comparison

A paired same-machine diagnostic was rerun on 2026-07-24 against the installed
`paper-qa==2026.3.18` release (upstream tag `v2026.03.18`, commit
`ac4ff91ad703e6816cb620ea579a98ca0c42c36f`). It used one legally acquired
16-page PLOS paper, one persisted index, three answerable questions, and one
deliberately unsupported clinical-trial question. The fact groups were fixed
before the measured run. This is an adapter regression benchmark, not evidence
of general question-answering superiority.

| Measurement | Upstream PaperQA retrieval | Research Forge end to end |
|---|---:|---:|
| Required fact-group coverage, 3 answerable cases | 1.00 | 1.00 |
| Median latency | 0.021 s | 11.627 s |
| Mean latency | 0.023 s | 42.666 s |
| Maximum latency | 0.034 s | 140.723 s |
| Unsupported-question abstention | not an answer operation | 1/1 |
| Cited contexts inside upstream top-10 | n/a | 4/4 |
| Hash-bound EvidenceSpans | not a Forge object | 4/4 |
| Internal `pqac-*` marker leakage after repair | n/a | 0/4 |

The upstream column measures `Docs.aget_evidence`, which is the exact retrieval
primitive consumed by Forge. A full upstream `Docs.aquery` answer was not run:
the machine has no separately configured PaperQA LLM credential, and the
selected product route intentionally reuses local Codex authentication. The
comparison therefore does **not** claim that Forge is faster or more accurate
than upstream PaperQA end to end.

The original measured overhead was almost entirely Codex synthesis. Three
isolated calls completed in 6.7--14.7 seconds, while one took 140.7 seconds.
PaperQA sparse retrieval itself remained in the 10--35 ms range after the
verified index had loaded. The v2 worker and batch implementation directly
target that repeated initialization cost; changing the retriever would not
address the observed tail.

The benchmark harness now also supports:

- question matrices of 1, 5, 20 and 50 questions, automatically bounded to
  20-question synthesis batches;
- explicitly supplied 1-, 10- and 100-document frozen corpora;
- P50, P90, P95 and P99 uncached and cached latency;
- answerability, abstention, citation, hash-binding and unsupported-statement
  rates;
- a 16-case v2 suite covering single-document, multi-document and adversarial
  evidence scenarios;
- PaperQA `Docs.aquery` full-answer execution through a Codex-to-LMI adapter.

The last option allows both answer paths to use the same configured Codex model.
The report marks an end-to-end comparison as fair only when the recorded model
labels match; otherwise its claim ceiling is diagnostic only.

### V2 diagnostic results

A synthetic frozen-corpus run on the same Windows workstation exercised the
new path with the local Codex `gpt-5.6-terra` backend. These measurements are
engineering diagnostics, not a scientific comparison of answer quality.

- Reusing the worker reduced measured client initialization from 282 ms on the
  first job to 0 ms on the second. The second uncached job still took 7.98 s:
  1.78 s to start its isolated ephemeral thread and 6.10 s in model round trip.
- A two-question batch correctly returned one hash-bound answer and one bounded
  abstention. Repeating both questions from the evidence-aware cache took
  0.055 s total.
- A same-model upstream `Docs.aquery` smoke case completed in 6.35 s with full
  required-fact coverage. Because both paths used the same Codex model, that
  single case is a fair functional comparison; it is far too small to establish
  general accuracy or speed superiority.

The bounded scale matrix produced the following wall-clock results:

| Documents | Questions | Uncached total | Uncached/question | Cached total |
|---:|---:|---:|---:|---:|
| 1 | 1 | 39.48 s | 39.48 s | 0.031 s |
| 1 | 5 | 21.28 s | 4.26 s | 0.026 s |
| 1 | 20 | 83.72 s | 4.19 s | 0.071 s |
| 1 | 50 | 190.32 s | 3.81 s | 1.008 s |
| 10 | 1 | 43.41 s | 43.41 s | 0.076 s |
| 10 | 5 | 31.11 s | 6.22 s | 0.234 s |
| 10 | 20 | 51.94 s | 2.60 s | 0.400 s |
| 10 | 50 | 152.50 s | 3.05 s | 0.741 s |
| 100 | 1 | 11.15 s | 11.15 s | 0.169 s |
| 100 | 5 | 19.55 s | 3.91 s | 0.446 s |
| 100 | 20 | 42.50 s | 2.13 s | 0.383 s |
| 100 | 50 | 163.98 s | 3.28 s | 0.825 s |

All scale rows had stable cache keys, a 1.0 hash-bound span rate for factual
answers, and a 0 unsupported-statement rate. Corpus size did not monotonically
control total latency because the runs were sequential and shared a warm remote
model service. The component telemetry is more informative: warm retrieval was
typically 10--15 ms, first verified index loading reached 0.78 s for 10
documents and 3.73 s for 100 documents, while each synthesis job spent roughly
31.6--74.2 s in model round trip. The remaining long tail is therefore primarily
model/session transport, not PaperQA retrieval or local persistence.

Ordinary repeated queries reuse an existing immutable EvidenceResult only when
all authority inputs listed in the cache-key section match. The first v1 repeat
lookup completed in about 60 ms; v2 scale-run cache totals are reported
separately above. The benchmark disables this reuse for uncached rows so cached
output is never reported as fresh synthesis speed.

The run also exposed user-facing `pqac-*` and private citation characters in
some answers. Forge now instructs synthesis to return citations only through
`cited_context_ids`, strips any leaked internal markers, and renders provenance
from separately validated EvidenceSpans.

Reproduce the comparison without an external API key:

```powershell
python scripts/benchmark_paperqa_comparison.py `
  --workflow-root .rfab `
  --corpus-id paperqa-corpus-3d47efc0f8f11929 `
  --cases tests/fixtures/paperqa-comparison-cases.json `
  --run-forge-synthesis `
  --output "$env:TEMP/research-forge-paperqa-comparison.json"
```

For a bounded scale matrix and same-model upstream answer run:

```powershell
python scripts/benchmark_paperqa_comparison.py `
  --workflow-root .rfab `
  --corpus-id <one-document-corpus-id> `
  --cases tests/fixtures/paperqa-comparison-suite-v2.json `
  --run-forge-synthesis `
  --scale-corpus <one-document-corpus-id>:1 `
  --scale-corpus <ten-document-corpus-id>:10 `
  --scale-corpus <hundred-document-corpus-id>:100 `
  --scale-question-counts 1,5,20,50 `
  --upstream-settings-json tests/fixtures/paperqa-upstream-codex-settings.json `
  --upstream-use-codex `
  --output paperqa-comparison-v2.json
```

The adapter has also been exercised end to end with a synthetic PDF: PaperQA
parsed and indexed the document, Codex synthesized an answer from the retrieved
context, and Research Forge persisted a page-bound EvidenceSpan. Readiness still
requires a recent successful corpus and evidence result in the active
installation.
