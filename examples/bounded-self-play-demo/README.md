# Bounded self-play claim verification demo

This synthetic, offline project is a small Research Forge fixture inspired by
the Academic Research Suite's preference for bounded and inspectable critique
over open-ended hidden self-play.

Research question: does one bounded proposer–critic–revision round improve the
accuracy of claim-delivery decisions over a single-pass proposer on a frozen
synthetic claim–evidence set?

The paired computational design uses:

- baseline: a single-pass proposer accepts every candidate claim;
- treatment: a bounded critic exposes one registered support signal and the
  proposer revises the delivery decision once;
- primary metric: decision accuracy over all eight frozen claim–evidence rows;
- independent statistical unit: registered task–seed pair;
- formal matrix: two tasks, two seeds, and two arms;
- formal isolation: candidate containers receive `formal_candidate.jsonl`,
  while a separate evaluator container alone receives
  `formal_targets.jsonl`;
- minimum meaningful paired improvement: `0.40`;
- network: disabled.

The fixture is deliberately synthetic. It can validate the Research Forge
workflow and the bounded protocol implementation, but it cannot establish that
self-play improves real scientific agents or real manuscripts.
