# `llm_evaluation_v1` controlled end-to-end acceptance

The certified boundary is a paired multiple-choice prompt experiment. It is
not a generic LLM judge or open-ended generation Profile.

## Standard case

The official idea intake creates a Study for:

> Does Verify-Before-Answer Prompting Improve Multiple-Choice Accuracy on a
> Frozen Science Question-Answering Benchmark? A Paired Item-Level Study

The controlled acceptance fixture freezes 150 license-free science-format
items, evaluator-only answers, two prompts, decoding settings, and a versioned
backend. It executes five paired dry-run items outside the scientific evidence
set, then 150 items in each formal arm (300 calls).

The primary endpoint is exact accuracy. The formal analysis reports the paired
accuracy difference, exact McNemar test, and a seeded paired bootstrap 95%
interval. Invalid outputs remain in the denominator with score zero.

## Lifecycle and artifacts

The acceptance runner records Workflow v2 steps across all four phases and
creates:

`task_brief.json`, `profile_qualification_report.json`,
`research_contract.json`, `execution_supplement.json`,
`dataset_manifest.json`, `prompt_manifest.json`, `model_manifest.json`,
`literature_manifest.json`, `run_plan.json`, `responses.jsonl`,
`evaluation.json`, `statistics.json`, `verdict.json`,
`stage_four_evidence_handoff.json`, `mandatory_reporting_register.json`,
`evidence_claim_map.json`,
`completion_record.json`, and HTML/JSON acceptance reports.

The Profile acceptance runner deliberately does **not** create
`manuscript.tex` or `manuscript.pdf`. Its formal output stops at the shared
Stage 4 evidence handoff. A paper exists only after the canonical Stage 4 DAG
has consumed that handoff, completed its reporting/depth/integrity audits, and
passed the ordinary owner gates.

Run it from the deployment/settings interface or locally:

```powershell
python -m research_forge.profiles.llm_acceptance run --output-root tmp/llm-acceptance
```

## Authority and maturity

Scientific verdict and Profile acceptance are independent. A supported,
refuted, or inconclusive result can all pass platform acceptance when the
frozen process is complete and auditable.

The packaged acceptance report derives C3 only while its Profile, contract,
prompt/evaluator, bundle, and test source hashes match. Source changes
invalidate the old report automatically.

The current C3 evidence uses a deterministic controlled backend and a
license-free controlled benchmark. It does not establish effects for a
deployed foundation model, ARC-Challenge, open-ended generation, or
LLM-as-judge. Those require a new authorized resource binding and successor
acceptance run.
