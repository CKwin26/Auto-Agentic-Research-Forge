# OpenML capability benchmark — 2026-07-31

## Scope

This record covers five official OpenML supervised tasks executed through the
`tabular_ml_v1` benchmark kernel. The official repeat 0 / fold 0 / sample 0
split was used. The baseline and treatment were frozen deterministic
estimators; metrics were recomputed independently from per-sample predictions.

This is controlled external-task evidence for the narrow numeric-tabular
Profile. It is not evidence of general ML autonomy, model selection quality,
or independent clean-room reproduction.

## Environment

- OpenML Python: 0.15.1
- scikit-learn: 1.9.0
- Research Forge profile: `tabular_ml_v1`
- Seed: 20260731
- Classification estimators: majority class vs nearest centroid
- Regression estimators: training mean vs linear least squares
- Decision threshold: zero beneficial difference

## Results

| Task | Dataset | Type | Metric | Baseline | Treatment | Beneficial effect | Verdict |
|---:|---|---|---|---:|---:|---:|---|
| 37 | diabetes v1 | classification | accuracy | 0.6493506494 | 0.5974025974 | -0.0519480520 | refuted |
| 39 | sonar v1 | classification | accuracy | 0.5714285714 | 0.9047619048 | 0.3333333333 | supported |
| 52 | heart-statlog v1 | classification | accuracy | 0.5555555556 | 0.5925925926 | 0.0370370370 | supported |
| 2282 | wisconsin v1 | regression | RMSE | 36.5465607573 | 35.0428053568 | 1.5037554005 | supported |
| 2300 | quake v1 | regression | RMSE | 0.1805605917 | 0.1809633701 | -0.0004027783 | refuted |

For regression, beneficial effect is `baseline RMSE - treatment RMSE`; for
classification it is `treatment accuracy - baseline accuracy`.

## Frozen identifiers and hashes

| Task | OpenML dataset / MD5 | Materialized data SHA-256 | Official split SHA-256 | Report SHA-256 |
|---:|---|---|---|---|
| 37 | 37 / `3cbaa3e54586aa88cf6aacb4033e4470` | `4aab1b7ee5beaee77f0ed0fba0f250bf6b4606b65074a9c2c07d34048d805f62` | `1f9a52f2c8c752c1cb7dedd38231cf436366316c700f039d19f47fad720b976b` | `f06d1cbfecca68d70a6760afa61e8ff3e24fa2e9113029b6e02a4c2c8cf92583` |
| 39 | 40 / `3ab630fbbfe25ab48b9bb47ce5759203` | `3765885e439f4576574f6de266b9179e807601eae4f671ca4cea3d6190b3768e` | `89b46ecb7b02ac2da8377a9e41de01a863f3107eb2ac36cc7841280b65e557b8` | `e45a899cb1a5850e152e9a7704f39ad08b33d55a4ba62b6f8ba01230f2e3ee4c` |
| 52 | 53 / `65dd57335c2c0e458cf83b22af8db16b` | `da4f6f43fc9879ef4a04e0c5b9a45a7e6295421f405bdb410888c7834b19ee22` | `cc4a16b4c1a53da76b0ae1955b5bf78e7f9c1b73001355464c8870407ce808e1` | `75268aca8de06a422b77998ef3b8add5423f506779611436c687259959dca65b` |
| 2282 | 191 / `2d2241b4081fc0cb7f68b9a0a2104b05` | `61790ad7fbe496ab4fd4454841d2ab9c3d8e83cb7cc57609374b9ad27b9e68a9` | `19333aada316890b7d88b8a7f3920ad79f1ca35eb09fa6ffa099b20df649ee57` | `e8092026e733d3b112631809dc36ee3723d537208cb7d5f839a6b475f1202d5a` |
| 2300 | 209 / `7ede4fd775db9eae5586b2f55c6d98c6` | `77ed8992959c7b34c9083b746bf2161c4468de2905d7a62ee12f50e184635448` | `ecf5dd63c2652f0a865f17d9854815a5a1b51eb241505aefa183f7dccc4a5176` | `92ccd971295f1212d95d712dafcae52a320d587fa58aefdf462285e68116e1ec` |

Each report also freezes baseline and treatment result hashes. Both arms in
all five tasks passed independent metric revalidation. Generated datasets,
OpenML cache files, predictions, and full reports remain in ignored benchmark
directories and are not committed.

## Interpretation

- Five official external tasks completed successfully.
- The system emitted both supported and refuted outcomes.
- The evidence raises the narrow OpenML benchmark cases to controlled E2E.
- It does not raise the whole Research Forge platform to C4 or C5.

## Product Retrieval Gateway acceptance

The product gateway now resolves exact `task:<id>` and
`openml-dataset:<id>:v<version>` identities through the official OpenML REST
metadata API. Stage 3 acquisition requires an explicit Research Contract
dataset-field binding, an owner-approved dataset-download policy, official
domains, and a positive byte budget. It freezes raw bytes before use and
rejects an OpenML MD5 mismatch.

Live acceptance on task 37 succeeded through the product gateway:

- dataset identity: `openml-dataset:37:v1`;
- raw ARFF bytes: `37,419`;
- provider MD5: `3cbaa3e54586aa88cf6aacb4033e4470` (verified);
- raw dataset SHA-256:
  `4eddd5b2b64679e8888348e306520a393d6a28e1ddc9643cfb76fc5d912d6d40`;
- snapshot id: `snapshot-26fb19a5a0f680f1`;
- metadata verification: `verified`;
- bytes frozen before consumption: `true`;
- experiment executed by retrieval: `false`.

The live acceptance used a temporary workflow root, and downloaded bytes were
not retained in the repository. Research Forge still does not publish OpenML
Runs or Flows and does not claim general OpenML compatibility.
