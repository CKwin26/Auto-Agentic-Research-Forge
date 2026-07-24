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

Project scan accepts source code, text and structured files, plus read-only
OOXML materials (`.docx`, `.pptx`, `.xlsx`). Office lock files and translated
duplicates are excluded when the original is present. Legacy binary `.xls`
and archives are not interpreted. Office materials can support boundary
discovery and author-Claim extraction, but without a frozen protocol-output
binding they remain `derived_materials` and cannot support a scientific
verdict.

Cache and dependency directories are excluded before resource hashing,
Claim extraction and candidate discovery. The protected set includes common
generic and provider-specific locations such as `cache`, `.cache`,
`torch_cache`, `hf_cache`, `huggingface_cache`, `.huggingface` and `.torch`.
If a historical candidate references one of these locations, the Discovery
audit records `cache_boundary_contamination`, assigns the earliest affected
step to `project_scan`, and permits a bounded non-scientific successor repair.

Large `.h5` and `.hdf5` files use a separate metadata-only adapter. It records
file size and modification time, root groups, demonstration and sample counts,
mask names, and the first demonstration's dataset names, shapes and dtypes.
It never materializes dataset arrays and does not represent its structural
digest as a full-file content hash. HDF5 metadata can establish dataset
coverage and suggest paired experimental designs, but without a frozen
training protocol and machine-readable evaluation outputs the direction
remains an `inferred_chain` and its verdict remains `unverifiable`.

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

## Bounded automatic repair

Every completed Discovery DAG is checked for deterministic output-integrity
defects. The first automatic checks cover procedural instructions incorrectly
emitted as author Claims and duplicate author Claims. A finding appends a
`WorkflowDiagnostic`, identifies the earliest affected step and creates a
versioned `RepairContract`.

Safe parsing, filtering and deduplication repairs run automatically. Research
Forge creates a successor Study, rebinds only immutable outputs outside the
affected DAG descendants, reruns the affected steps, and evaluates the
contract's regression checks. The predecessor result, diagnostic, repair
revisions and artifact hashes remain available. The predecessor is marked
`superseded` only after the successor passes regression.

Repairs that may change the research question, academic interpretation,
experimental design or incur a high-cost run are never automatic. They stop at
the owner repair Gate. This mechanism is therefore a bounded successor, not an
in-place rewrite of history.

## Current acceptance case

The stock-research project is the first acceptance case. The current
deterministic scan found 224 eligible local resources and 46 author Claims,
generated a bounded multi-purpose query matrix, and produced project-bound
directions with exact report/protocol/output provenance. Live academic
retrieval is allowed to remain `external_grounding_incomplete` when returned
papers do not match a candidate Claim; irrelevant search results are never
presented as novelty evidence.

The StereoPolicy dataset is the HDF5 acceptance case. A historical Stage-1 run
incorrectly selected a DINOv2 model card under `torch_cache`. The cache audit
localized the defect to `project_scan` and generated a successor Study. The
successor excluded the cache, inspected 29/29 HDF5 files through metadata
only, and recorded 4,102 demonstrations and 1,476,244 trajectory steps. It
produced a normalized paired-evaluation direction for stereo vision, depth
features and point-cloud representations in robotic imitation learning. This
establishes a testable dataset boundary, not a trained-policy result.
