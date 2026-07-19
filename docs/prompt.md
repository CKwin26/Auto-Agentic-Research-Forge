# Prompt and control contract

Research Forge deliberately does not use one giant end-to-end prompt. It currently uses five narrow Codex contracts and a larger deterministic control plane. Codex runs in a read-only ephemeral thread; its final response is constrained by JSON Schema and then revalidated with Pydantic. Stage-four manuscript generation is deterministic in the first closed-loop version; later prose agents may rewrite bounded sections but cannot create or alter claim bindings.

## Model-owned decisions

- `research_forge/prompts/literature_search.md`: bounded scholarly queries plus explicit inclusion, exclusion, and scope criteria; it cannot name papers or claim that anything was found.
- `research_forge/prompts/literature_screen.md`: one conservative relevance label per supplied candidate ID, exactly once and in order; it cannot add or alter records.
- `research_forge/prompts/literature_synthesis.md`: related-work themes and falsifiable novelty hypotheses using only exact included source IDs; it cannot claim full-text review or proven novelty.
- `research_forge/prompts/planner.md`: research question, falsifiable hypothesis, scope, baseline concept, metrics, confounders, stop conditions, and blocking questions.
- `research_forge/prompts/experimenter.md`: exactly one bounded next experiment, expressed as scalar parameters and/or complete replacements for allow-listed files under `experiment/`.

Both calls use strict Pydantic structured outputs. Their output is a proposal, never canonical state.

## Code-owned decisions

- legal stage transitions and explicit human gates;
- scholarly API requests, raw-response storage, metadata normalization, identifier verification, deduplication, hard exclusions, query quotas, and final source registration;
- latest search-plan/discovery binding, screening/source consistency, review citation resolution, exact review-ID approval, Stage 1 hashes, and plan-to-review/source binding;
- execution command, timeout, repeat count, and total run budget;
- frozen-contract hashes before and after every run;
- path containment, file suffixes, size limits, and secret-marker rejection;
- `shell=False` subprocess execution with API keys removed from the child environment;
- metric-file existence, required names, numeric type, and finite-value checks;
- baseline comparison, minimum delta, evidence recording, and promotion eligibility;
- deterministic diagnosis, target-state fingerprints, duplicate rejection, candidate-pool ranking, patience and failure budgets;
- Docker phase separation, mount visibility, network/resource policy, and runtime attestation;
- append-only evidence and lineage ledgers, including failed and negative runs;
- source-registry freezing, claim-to-source/run binding, manuscript audit, and completion certification.

This split is the key design decision: language models explore; deterministic code owns truth and side effects.
