# Scientific identification gates

Research Forge separates three questions that a manuscript must not collapse:

1. Did the registered bundle change the measured outcome?
2. Which component or mechanism caused that change?
3. Does the result generalize beyond the frozen generator, task family, or split?

The platform records the intended answer in
`ResearchContractVersion.scientific_validity_contract`. A successful run may
still receive a lower claim ceiling when its design or evidence cannot answer
the requested question.

## Stage 2: identify before executing

Stage 2 freezes an `identification_target`:

- `bundled_intervention_effect`
- `curriculum_ordering_effect`
- `rule_supervision_effect`
- `feature_representation_effect`
- `generalization_effect`

A bundled intervention may change several dimensions, but it may only support
a claim about the bundle. A curriculum-order claim requires the same examples,
labels, feature representation, task coverage, sample count, objective, model,
and budget in every arm. The principal comparison changes order only:
easy-to-hard versus randomized order, with hard-to-easy as an optional
diagnostic control.

Stage 2 also records:

- every dimension that varies and every dimension held invariant;
- matched controls and component or feature ablations;
- relationships between engineered features and the target rule;
- real-world, synthetic, or mixed data provenance;
- generator distributions, scenario families, split-overlap checks, seed
  roles, tie rules, and boundary-margin analysis for synthetic data;
- prevalence, confusion matrix, balanced accuracy, MCC, sensitivity,
  specificity, and task-conditional analysis for classification outcomes;
- cluster-aware uncertainty, threshold sensitivity, and reproducibility
  release plans.

Confirmatory contracts cannot freeze when a required identification condition
is absent. Exploratory contracts remain runnable but receive an explicit
finding and a reduced maximum claim tier.

## Stage 3: evidence must match the frozen design

Stage 3 does not accept an aggregate score as proof that the registered
analysis was completed. It verifies:

- matched-control and ablation arm completion;
- generator disclosure against produced samples;
- generator, split, model, and resampling seed roles;
- duplicate and train/test overlap checks;
- tie and decision-boundary analyses;
- per-seed, per-family, leave-one-family-out, cluster-permutation, wild
  bootstrap, or other registered robust analyses;
- threshold-sensitivity results;
- exact numerators and denominators for safeguard rates;
- a reproducibility package verified outside the author workspace.

When result rows expose binary truth and prediction fields, Research Forge
deterministically derives prevalence, the confusion matrix, sensitivity,
specificity, balanced accuracy, Matthews correlation coefficient, action rate,
and the same diagnostics per task or scenario family. Missing evidence creates
a successor-analysis or successor-experiment route; historical runs and
verdicts remain immutable.

Adapter-supplied completion fields have no authority by themselves. A declared
analysis is accepted only when its `evidence_bindings` name output artifact IDs
from the qualified run, or when a deterministic evaluator emits the matching
passed check. This prevents a protocol-side `completed: true` flag from
substituting for a produced analysis.

## Stage 4: prose cannot exceed identification

Stage 4 passes the active scientific contract and the latest claim envelope to
the manuscript audit. It blocks publication readiness when the paper:

- attributes a bundled effect to curriculum learning without an order-only
  comparison;
- omits known target-feature overlap;
- presents adaptive follow-up evidence as independent confirmation;
- omits a registered scientific limitation or reproducibility statement;
- repeatedly uses an internal field name as if it were a validated construct.

Audit and repair mechanics belong in the reproducibility statement or
appendix. The main Results section should report scientific estimates,
uncertainty, heterogeneity, conditional behavior, and sensitivity.

## Example: delayed-gratification training

Suppose one arm adds persistence tasks, extends delay ranges, changes labels,
and orders examples by difficulty. A comparison with an untreated arm
identifies the effect of the **bundled training scheme**, not curriculum
learning.

To request `curriculum_ordering_effect`, Stage 2 must register at least:

```json
{
  "identification_target": "curriculum_ordering_effect",
  "arm_variation_dimensions": ["sample_order"],
  "invariant_dimensions": [
    "task_coverage",
    "label_rule",
    "feature_representation",
    "sample_composition",
    "training_objective"
  ],
  "matched_control_ids": ["random-order"],
  "feature_ablation_ids": [
    "raw-features",
    "engineered-features",
    "without-expected-advantage"
  ]
}
```

If these arms are not executed, Stage 3 lowers the claim ceiling. If the paper
still calls the result a curriculum-learning mechanism, Stage 4 marks
publication readiness as blocked.

## Compatibility

Older contracts remain readable. Missing version-2 fields use conservative
defaults and do not rewrite historical runs. New confirmatory studies must
explicitly populate the relevant design and release fields before freezing.
