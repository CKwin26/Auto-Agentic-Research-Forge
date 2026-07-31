# PaperQA retrieval runtime revalidation — 2026-07-31

## Scope

This is a current local runtime acceptance record for the pinned PaperQA
retrieval primitive used by Research Forge. It revalidates corpus loading,
index loading, and `Docs.aget_evidence` on a previously frozen corpus. It does
not claim a new end-to-end model-synthesis acceptance or general superiority
over PaperQA.

## Environment and frozen inputs

- PaperQA version: `2026.3.18`
- Corpus ID: `paperqa-corpus-3d47efc0f8f11929`
- Corpus manifest SHA-256:
  `591fc48a07d6d123413e8be788703456816d483f9acfc7e17e27f283e4953cd1`
- PaperQA index SHA-256:
  `881a4ee75f05ac42f933971c210d69f2501ad3785e24ca0de78faf7395e04ebc`
- Cases: three preregistered answerable questions and one deliberately
  unsupported question
- Operation: upstream `Docs.aget_evidence`

## Result

- All four cases returned ten ranked contexts.
- Required fact-group coverage was `1.0` for each of the three answerable
  cases.
- Retrieval latency was `0.513967`, `0.014506`, `0.012583`, and `0.012951`
  seconds. The first case includes loading the persisted verified index.
- No upstream full answer was run. The selected product route uses local Codex
  synthesis, and no separate PaperQA LLM credential was configured.
- Result: passed for current PaperQA retrieval runtime.

The uncommitted raw benchmark record had SHA-256
`22316e4d0fbad44c29f278860ad8b79302d6c4fb2620ffff230299e22f84d6ab`.
It remains a local runtime artifact and is intentionally excluded from the
repository. The durable evidence is this bounded summary plus the frozen
corpus and index identifiers.

## Reproduction command

```powershell
python scripts/benchmark_paperqa_comparison.py `
  --workflow-root .rfab `
  --corpus-id paperqa-corpus-3d47efc0f8f11929 `
  --cases tests/fixtures/paperqa-comparison-cases.json `
  --output "$env:TEMP/paperqa-runtime-revalidation.json"
```
