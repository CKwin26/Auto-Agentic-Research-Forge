# Profile capability matrix

Research Forge distinguishes four things that were previously mixed together:

1. A **formal Profile Bundle** owns a frozen contract schema, run-plan compiler,
   builder, evaluator, statistical adjudicator, repair policy, and assurance
   suite. Only `certified` and `legacy_frozen` bundles may enter formal Stage 3.
2. A **task mode** is a bounded behavior inside a Profile, such as fixed-split
   numeric classification inside `tabular_ml_v1`.
3. A **domain adapter** adds semantic validation or defaults but does not supply
   a complete runner. `finance_backtest` is currently such an adapter.
4. A **fixture or non-formal extension** may support feasibility or component
   testing but cannot produce a formal scientific Verdict.

The machine-readable source is
`research_forge/profile_capability_matrix.yaml`; CI checks it against the
`Stage3Profile` enum and the live Profile Bundle registry.

| Profile | Registry state | Formal Stage 3 | Supported mode / boundary |
|---|---|---:|---|
| `tabular_ml_v1` | certified | yes | Numeric CSV classification/regression; formal fixed split |
| `benchmark_prediction_v1` | certified | yes | Candidate submission isolated from hidden targets |
| `existing_python_project_v1` | certified | yes | Explicit two-arm command replay; not automatic domain semantics |
| `computational_paired_comparison_v1` | legacy frozen | yes | Compatibility-only paired continuous semantics |
| `computational_paired_comparison_v2` | certified | yes | Cluster-aware paired continuous analysis |
| `paired_binary_independent_v1` | certified | yes | Independent paired binary units |
| `paired_binary_clustered_v1` | certified | yes | Clustered paired binary units |
| `paired_multi_arm_ablation_v1` | certified | yes | Three-or-more-arm paired ablation |
| `deterministic_simulation_v1` | development | no | Component fixture only |
| `paired_multi_endpoint_v1` | unregistered | no | Reserved enum; no bundle/runner |
| `unpaired_two_group_continuous_v1` | unregistered | no | Reserved enum; no bundle/runner |

Finance, retrieval, survey, and other domain labels are not silently promoted
to formal Profiles. Unsupported designs fail closed or return a design/build
route; they never inherit maturity from `existing_python_project_v1` merely
because Python can execute them.
