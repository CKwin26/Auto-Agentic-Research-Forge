# Review contract

## Evidence boundary

Judge only the supplied `claim_text` and `linked_evidence`. Do not search the web, inspect the project, infer missing experimental details, or use another reviewer's output. The question is whether this packet supports this claim, not whether the claim sounds plausible.

## Verdicts

- `supported`: the linked evidence directly supports the material content and precision of the claim.
- `unsupported`: a specific evidence, attribution, experimental, metric, statistical, contradiction, scope, novelty, or falsifiability failure is present.
- `abstain`: the packet is ambiguous or insufficient to decide safely.

Allowed failure modes are defined in `personas.json`. `supported` requires an empty `failure_modes` list. `unsupported` requires at least one non-ambiguity failure mode. `abstain` requires exactly `ambiguous_or_insufficient_packet`. A `supported` or `unsupported` vote must identify at least one decisive packet field or excerpt.

## Exact response schema

Return JSON only:

```json
{
  "schema_version": 1,
  "panel_id": "panel-a",
  "persona_id": "feynman",
  "source_sample_sha256": "...",
  "blinded": true,
  "items": [
    {
      "audit_id": "...",
      "verdict": "supported",
      "rationale": "Concise, evidence-specific reasoning.",
      "failure_modes": [],
      "decisive_evidence": ["Exact packet field or short excerpt used to decide."]
    }
  ]
}
```

Every assigned `audit_id` must appear exactly once; no extra IDs are allowed.

## Deterministic aggregation

For each claim:

1. Any vote containing a hard-veto failure mode makes the final verdict `unsupported`.
2. Otherwise, at least two `unsupported` votes make it `unsupported`.
3. Otherwise, exactly one `unsupported` vote makes it `abstain`.
4. Otherwise, at least two `supported` votes make it `supported`.
5. All remaining combinations make it `abstain`.

No agent may override these rules. Preserve all votes in the final trace.

Any mixed-vote claim, and every final `abstain`, must be marked `requires_human_adjudication`. A hard-veto label is model-proposed unless a separate structural checker verifies it; do not hide this limitation.

## Reporting boundary

Always label the result `same-model persona ensemble`. This is an internal adversarial review aid, not independent human auditing, cross-model validation, or replication.
