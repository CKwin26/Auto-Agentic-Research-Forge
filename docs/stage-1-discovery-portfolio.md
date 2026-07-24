# Stage 1: project-grounded discovery

Stage 1 starts from the local project, not from an unconstrained web search.
Its canonical order is:

```text
read-only project scan
  -> author Claim Registry
  -> Academic Concept Normalization
  -> structured Research Fingerprint
  -> purpose-specific external Query Plan
  -> normalized and frozen external sources
  -> Claim-Source matches
  -> Discovery Portfolio
  -> owner direction selection
  -> frozen Scope Contract
```

## Research Fingerprint

The fingerprint preserves the project domains, problems, methods, metrics,
canonical concepts, evidence assets and source research tracks. Every term is
derived from a local, hashed project resource. It is a discovery aid and does
not become scientific evidence.

Before the fingerprint is used for retrieval, project-internal labels are
separated from scholarly terminology:

```text
internal label
  -> operational definition
  -> academic concepts
  -> academic title and query terms
```

For example, an internal label such as `极端赢家` is not submitted as a
research field. When the project defines it as a future 20-trading-day return
that is both industry-top-decile and at least 10% in absolute terms, the
retrieval layer uses concepts such as cross-sectional equity return
prediction, rare high-return event ranking, learning-to-rank for stock
selection and walk-forward evaluation. The internal label remains visible as
provenance. An unresolved mapping is marked `needs_owner_review`; it cannot be
silently promoted into an academic direction.

The query matrix separates:

- closest prior work;
- methods and baselines;
- contradictory or negative evidence;
- recent attention and research trends;
- datasets and model resources.

Queries are bounded, sanitized and executed only through the Retrieval
Gateway. A provider result is not enough to complete external grounding:
at least one source must be matched to a candidate Claim before the Portfolio
can report `ready_for_scope_selection`.

## Discovery Portfolio

The Portfolio contains at most five distinct directions. Each direction
records:

- the research question and falsifiable hypothesis;
- originating author Claims and project paths;
- the primary and supporting project research tracks;
- closest prior work, conflicting context and attention signals;
- separate novelty-grounding, evidence-readiness, feasibility and
  external-attention assessments;
- evidence gaps and prohibited conclusions.

These dimensions are not collapsed into a single opaque score. Literature and
trend signals have discovery authority only and cannot decide a Study verdict.

## Owner Gate

The UI consumes the backend Portfolio directly. It must not infer project
topics from track names or contain project-specific topic families.

When the owner selects a direction, Research Forge updates the draft Scope
Contract with that exact `direction_id`, approves the Scope Gate, freezes
`ScopeContract v1`, and only then freezes the Discovery ResourceSet. Selecting
a different direction after freezing requires Scope vNext.

## Current acceptance case

The stock-research project is the first acceptance case. The current
deterministic scan found 224 eligible local resources and 46 author Claims,
generated a bounded multi-purpose query matrix, and produced project-bound
directions with exact report/protocol/output provenance. Live academic
retrieval is allowed to remain `external_grounding_incomplete` when returned
papers do not match a candidate Claim; irrelevant search results are never
presented as novelty evidence.
