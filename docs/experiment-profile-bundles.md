# Experiment Profile Bundles

Stage 3 consists of a generic evidence-governance kernel and certified,
versioned Experiment Profile Bundles. A Profile is not an application domain.
It freezes a compatible combination of:

`Design + Outcome + Estimand + Estimator + Inference + Missingness +
Multiplicity + Verdict policy`.

Reusable components live under `research_forge/profiles/components`. Formal
execution accepts only combinations registered as a certified Bundle. Agents
cannot assemble an untested statistical procedure at runtime.

## Current support

| Profile | Outcome and inference | Formal status |
|---|---|---|
| `computational_paired_comparison_v1` | Historical paired summary mean and normal approximation | Legacy frozen |
| `computational_paired_comparison_v2` | Sample-level paired continuous effect; frozen cluster bootstrap | Certified |
| `paired_binary_independent_v1` | Paired risk difference; exact McNemar over independent pairs | Certified |
| `paired_binary_clustered_v1` | Paired risk difference; frozen cluster bootstrap | Certified |

The v1 Profile is immutable. Moving from v1 to v2 requires a new Research
Contract and Scientific Successor; historical runs, results and verdicts are
not silently reinterpreted.

## Modern paired result schema

Modern Profiles require `record_layout=summary_with_analysis_rows`. Each arm
still reports its aggregate metric, denominator and sample IDs, and also emits
the sample-level rows used by the independent evaluator:

```json
{
  "accuracy": 0.75,
  "denominator": 2,
  "sample_ids": ["u1", "u2"],
  "analysis_rows": [
    {"sample_id": "u1", "value": 1, "task_id": "task-a"},
    {"sample_id": "u2", "value": 0, "task_id": "task-a"}
  ]
}
```

For clustered Profiles, both arms must bind every pair to the same non-empty
cluster. Pair omissions, duplicate identifiers, non-binary values and cluster
disagreement block qualification.

## Frozen inference and verdicts

Bootstrap method, resampling unit, number of resamples, random seed and
confidence level are Research Contract inputs. They cannot be chosen after
results are visible. Modern superiority verdicts use the registered confidence
interval boundary, not only a favorable point estimate.

Each certified Profile has typed contract validation, a deterministic Run Plan
compiler, sample-level analysis construction and qualification, an estimator,
inference, golden-vector assurance, an independent effect cross-check, a
reproduction comparator and explicit cross-version reuse rules.

The reproduction control plane remains Profile-agnostic. It verifies and
executes a frozen package, then delegates scientific result equivalence to the
Profile comparator. RF-E1/RF-E2 evidence grades describe Research Forge
assurance, not an international certification standard.
