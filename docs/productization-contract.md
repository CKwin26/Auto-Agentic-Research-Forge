# Research Forge productization contract

Research Forge is a general research-to-publication control plane. A paper is a
project instance, not a feature flag in the core.

## Boundary

`ProjectSpec` holds a project's question, evidence mode, publication intent and
project-specific stage extensions. It must contain tasks, metrics, seeds,
reviewer sample sizes and venue contracts when they matter. The core must not
contain those values.

The core owns the following non-negotiable invariants:

- protocol and evaluator freeze before treatment execution;
- evidence-bound claims and immutable audit references;
- stage-specific roles selected by a hard-coded stage-to-skill map;
- isolated persona opinions written before an aggregate debate decision;
- bounded, quoted project material in every model prompt;
- durable, deduplicated failure experience and explicit degraded execution.

## Stage-to-skill governance map

| Stage | Required skills | Roles |
|---|---|---|
| Discovery | research question; literature discovery | research architect, devil's advocate, bibliography specialist, source verifier |
| Protocol | protocol design; integrity gate | methodologist, statistician, reproducibility engineer, integrity verifier |
| Experimentation | experiment design; execution audit | experiment designer, failure analyst, reproducibility engineer |
| Synthesis | claim audit; manuscript writing; publication integrity | claim auditor, domain reviewer, argument builder, editor, integrity verifier |

Projects may add adapters, but publication projects cannot remove the integrity
roles. This keeps hard process guarantees separate from model personality.

## Auditable multi-agent debate

Every debate uses at least two roles. Each role is bound to an explicit
distilled epistemic profile (empirical integrity, statistical skepticism,
formal precision, or falsification/novelty), not a simulated historical person.
Each role writes one independent JSON opinion under
`audits/persona_debates/<debate-id>/opinions/`, while `persona_profiles.json`
records the exact review card. Only after all required opinions exist may an
aggregate `decision.json` be written. The manifest includes a SHA-256 for every
opinion, preserving dissent for audit. Outputs are always labelled
`same-model persona ensemble`, never human validation or independent review.

## Prompt-injection boundary

Project text, evidence and historic failures are untrusted data. A prompt may
contain at most 8 fragments, each at most 96,000 characters, with 128,000 total
characters. The common envelope quotes each fragment and declares that embedded
instructions have no authority. Inputs above the budget fail before a model is
called rather than being silently truncated.

## Failure learning and degradation

`failure_memory.jsonl`, stored at the chosen pipeline root, records a hashed
root cause once and is available to later runs only as bounded read-only context.
If a worker fails, the stage returns `completed`, `degraded`, or `blocked`; it
does not crash orchestration. A deterministic or manual fallback may complete a
non-gating task. A mandatory integrity gate remains blocked until its required
evidence is available.

## Golden and unrelated cases

The current research-agent evidence paper is a golden regression case. A
tabular-classification replication is the first unrelated case. Both must be
materialized only through `ProjectSpec`; their stage-to-skill manifests must be
identical without editing `research_forge/`.

The automated second-case test also materializes and executes a protected
generic computational benchmark after attaching the unrelated specification.
It proves contract portability and core execution only; it is not presented as
scientific validation of the tabular study or as a publication-ready paper.

`scripts/run_second_case_validation.py` is the stronger end-to-end regression:
it runs a separate SICK textual-inference working-paper case through frozen
inputs, protected evaluation, promotion, evidence-bound synthesis and
completion verification, while asserting that no `publication_*` stage is used.
Its output remains explicitly non-submission-ready unless the normal isolation,
literature, manuscript-depth and human-review gates are independently met.
