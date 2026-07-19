---
name: nuwa-scientist-panel
description: Run blinded scientific claim review through isolated Codex personas inspired by Feynman, Tukey, Shannon, and Popper, then aggregate verdicts with deterministic veto and abstention rules. Use when reviewing research claims, evidence packets, experiment conclusions, statistical statements, novelty claims, manuscripts, or AI-for-science outputs; when the user asks for scientist personas, a reviewer committee, 女娲科学家人格, multi-perspective peer review, or an auditable same-model reviewer ensemble.
---

# Nuwa Scientist Panel

Use isolated reviewer contexts to expose different scientific failure modes. This is behavioral prompt distillation from public epistemic practices, not identity simulation, quotation, or a substitute for independent human or cross-model review.

## Required inputs

Read `references/personas.json` and `references/review-contract.md` completely before reviewing. Freeze the input packet and record its SHA-256 hash. Each item must have `audit_id`, `claim_type`, `claim_text`, and `linked_evidence`.

## Workflow

1. Blind the packet. Remove treatment arm, task and seed metadata, prior verdicts, effect labels, and author identity.
2. Build routed jobs with `scripts/build_jobs.py`. The router assigns exactly three relevant personas per claim.
3. Run every persona in a fresh context. Give it only its persona card, the review contract, and its job file. Never expose another reviewer's output.
4. Require exact JSON matching the contract. A reviewer must abstain when the packet is insufficient.
5. Aggregate with `scripts/aggregate_reviews.py`. Do not let a meta-agent override the hard-coded rules.
6. Report the input hash, persona/job hashes, votes, veto reasons, final verdict, disagreement flag, human-adjudication queue, and limitations.

## Routing

- `experiment`: Feynman, Tukey, Shannon
- `literature`: Feynman, Shannon, Popper
- `novelty`: Popper, Shannon, Feynman

If a new claim type is introduced, stop and add an explicit route rather than guessing.

## Independence boundary

Persona contexts are operationally isolated but share the same underlying Codex model and can have correlated blind spots. Label results `same-model persona ensemble`. Never describe them as human validation, cross-model validation, or independent replication.

A hard-veto label is still proposed by a model reviewer unless a separate structural checker verifies it. Preserve the provisional deterministic verdict, but send every mixed-vote claim to human adjudication.

## Commands

```powershell
python scripts/build_jobs.py --input sample.json --personas references/personas.json --output-dir panel-run --panel-id panel-a
python scripts/aggregate_reviews.py --manifest panel-run/manifest.json --reviews-dir panel-run/reviews --output panel-run/final.json
```

Use `--limit 1` for a forward smoke test before a full packet.
