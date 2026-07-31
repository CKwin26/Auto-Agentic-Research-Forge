# Yila-AI sci-ssci-skills adaptation

Research Forge Stage 4 adapts selected workflow concepts from
[Yila-AI/sci-ssci-skills](https://github.com/Yila-AI/sci-ssci-skills):

- Evidence-Preserving Draft Contract
- Claim-Strength Contract
- Section Function Map
- Title promise checking
- Rhetorical routing for titles, abstracts, results, and discussion
- Deterministic invariant checking for academic polishing

The upstream repository is licensed under Apache-2.0. Research Forge does not
execute the upstream Node package at runtime. It implements a bounded local
policy in `research_forge/sci_ssci_writing.py`, records the upstream Git blob
identifiers used during adaptation, and preserves Research Forge's authority
rules: writing and polishing may not change frozen claims, evidence, experiment
results, or verdicts.

Suggested credit, adapted from the upstream project:

> Adapted from Yila-AI/sci-ssci-skills, including the Evidence-Preserving Draft
> Contract and Claim-Strength Contract.
