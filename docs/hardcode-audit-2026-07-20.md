# Publication-specific hard-code audit

## Rule

No paper-specific number, benchmark name, venue, claim wording, filesystem path
or reviewer sample size may be a Research Forge core default. It belongs in a
`ProjectSpec`, frozen protocol, evaluator adapter or publication contract.

## Current findings and disposition

| Finding | Current location | Disposition |
|---|---|---|
| `8 tasks × 5 seeds × 2 arms` | `publication_*`, Stage 2 protocol | keep only as the current paper's frozen protocol; never use as generic default |
| `128` manual-audit claims | protocol and `publication_manual_audit.py` | retain as project-specific audit contract; future projects configure it |
| DeBERTa-v3 NLI | `publication_nli_evaluation.py` | evaluator adapter for the current case, not a core evaluator requirement |
| Current paper claim wording and manuscript sections | `publication_synthesis.py` | golden-case adapter; generic pipeline only requires evidence-bound claims |
| Venue score / 60% readiness policy | `publication_readiness.py` and venue contract | publication-target adapter; never a universal scientific-validity score |
| Project paths and stage IDs | `stage1_runs/research-agent-evidence-publication-v1` | immutable case-study evidence; excluded from core configuration |

## Core invariants retained as code

These are intentionally hard-coded because they are process safety rules, not
paper conclusions: protocol freezing, evidence hashes, protected evaluation,
claim binding, human-validation boundaries, independent debate records, prompt
budgets, explicit degradation status and failure-memory persistence.

## Regression rule

The two specifications in `examples/project-specs/` must initialize the same
stage/skill governance map with no edit under `research_forge/`. Any future
case that requires an edit to the core for its task count, metrics, reviewer
count, venue or manuscript wording is an abstraction defect and must be added
to this audit before implementation.

The SICK lexical-overlap working-paper regression is the required independent
case: it uses its own SICK task specification, train/test artifacts, protected
accuracy evaluator and project specification. Its runner is
`scripts/run_second_case_validation.py`; it does not import a `publication_*`
module.
