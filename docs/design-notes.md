# Design notes and prior-art mapping

Research Forge reimplements architectural patterns; it does not copy the orchestration code of the referenced projects.

| Prior system | Useful pattern | How it appears here | What is intentionally changed |
|---|---|---|---|
| [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw) | Explicit stages, contracts, gates, rollback, verified artifacts | State graph, frozen manifest, validation gates, ledgers | Scientific success is never decided by an LLM |
| [Karpathy autoresearch](https://github.com/karpathy/autoresearch) | Fixed evaluator, scalar objective, repeated propose/run/keep loop | Frozen execution contract and primary-metric comparison | Protected files and budgets are enforced in Python, not only in a prompt |
| [AI Scientist v2](https://github.com/SakanaAI/AI-Scientist-v2) | Search nodes and experiment journal | Proposal/run IDs and durable evidence history | No LLM reward is accepted as ground truth; tree search is deferred |
| [DeepScientist](https://github.com/ResearAI/DeepScientist) | Durable quest/artifact state, metric contract, baseline gate | Project state, execution contract, verified baseline | Reduced to a local single-user core before distributed infrastructure |
| [Agent Laboratory](https://github.com/SamuelSchmidgall/AgentLaboratory) | Fixed phase ownership and iterative edits | Narrow planning and experiment-design roles | No free-form edit/execute loop and no model-authored scientific score |

## Two independent ledgers

The code lineage ledger answers “what implementation was promoted?” The evidence ledger answers “what was tried and what happened?” Reverting or superseding code never erases a failed or negative result.

## Why full-file replacements

The experiment agent may return complete replacements only for allow-listed files under `experiment/`. The engine applies those replacements to a per-run copy, validates the result by executing the frozen command, and keeps canonical code unchanged until a human confirms an improving run ID. This is less flexible than unrestricted shell access, but the provenance is simple and auditable.

## Why paper writing is phase four

Generating a paper-shaped document is easy to demo and hard to trust. Manuscript generation therefore runs only after a frozen source registry, a verified baseline, and a promoted improving experiment exist. Numerical claims carry structured metric values and run IDs; background claims carry frozen source IDs. The audit fails closed when those bindings are absent or disagree with the ledger. Pipeline completion and publication readiness are separate: a local or calibration loop can be complete while still carrying explicit publication blockers.
