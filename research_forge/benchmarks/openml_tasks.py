"""Run frozen OpenML tasks through the narrow ``tabular_ml_v1`` kernel.

This module belongs to the acceptance benchmark, not to Workflow v2.  Product
workflow handlers must continue to use the Retrieval Gateway for external
resources.  The benchmark uses the official OpenML client so the external task
definition and split can serve as an independent acceptance surface.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ..profiles.contracts import TabularMLParameters
from ..profiles.tabular_ml import run_tabular_ml, validate_tabular_ml_result
from ..storage import sha256_file, write_json_atomic


MODULE = "research_forge.profiles.tabular_ml"


@dataclass(frozen=True)
class LoadedOpenMLTask:
    task_id: int
    dataset_id: int
    dataset_name: str
    dataset_version: int
    dataset_md5: str
    dataset_license: str | None
    task_type: Literal["classification", "regression"]
    target_name: str
    feature_names: list[str]
    sample_ids: list[str]
    x: np.ndarray
    y: np.ndarray
    train_indices: np.ndarray
    test_indices: np.ndarray
    estimation_procedure: dict[str, Any]


def load_official_openml_task(task_id: int, *, cache_dir: Path) -> LoadedOpenMLTask:
    try:
        import openml
        from openml.tasks import TaskType
    except ImportError as exc:  # pragma: no cover - deployment diagnostic
        raise RuntimeError(
            "OpenML benchmark requires the scientific-validation extra"
        ) from exc
    cache_dir.mkdir(parents=True, exist_ok=True)
    openml.config.cache_directory = str(cache_dir)
    task = openml.tasks.get_task(task_id, download_splits=True)
    if task.task_type_id is TaskType.SUPERVISED_CLASSIFICATION:
        task_type: Literal["classification", "regression"] = "classification"
    elif task.task_type_id is TaskType.SUPERVISED_REGRESSION:
        task_type = "regression"
    else:
        raise ValueError("benchmark supports only supervised tasks")
    dataset = task.get_dataset(download_data=True)
    x_frame, y_series, categorical, feature_names = dataset.get_data(
        target=task.target_name,
        dataset_format="dataframe",
    )
    if any(bool(item) for item in categorical):
        raise ValueError(
            "OpenML acceptance task contains categorical features; choose a "
            "numeric task or freeze a train-only preprocessing Profile"
        )
    numeric = x_frame.apply(lambda column: column.astype(float)).to_numpy(
        dtype=float
    )
    if not np.all(np.isfinite(numeric)):
        raise ValueError("OpenML acceptance task contains missing features")
    target = np.asarray(y_series)
    if task_type == "regression":
        target = target.astype(float)
        if not np.all(np.isfinite(target)):
            raise ValueError("OpenML regression target contains missing values")
    else:
        target = target.astype(str)
        if len(np.unique(target)) != 2:
            raise ValueError("OpenML classification acceptance requires two classes")
    train, test = task.get_train_test_split_indices(
        repeat=0, fold=0, sample=0
    )
    sample_ids = [f"openml-{task_id}-row-{index}" for index in range(len(target))]
    return LoadedOpenMLTask(
        task_id=int(task.task_id),
        dataset_id=int(task.dataset_id),
        dataset_name=str(dataset.name),
        dataset_version=int(dataset.version),
        dataset_md5=str(dataset.md5_checksum),
        dataset_license=(str(dataset.licence) if dataset.licence else None),
        task_type=task_type,
        target_name=str(task.target_name),
        feature_names=[str(item) for item in feature_names],
        sample_ids=sample_ids,
        x=numeric,
        y=target,
        train_indices=np.asarray(train, dtype=int),
        test_indices=np.asarray(test, dtype=int),
        estimation_procedure=dict(task.estimation_procedure),
    )


def _split_hash(task: LoadedOpenMLTask) -> str:
    payload = {
        "train": task.train_indices.tolist(),
        "test": task.test_indices.tolist(),
    }
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _materialize_csv(task: LoadedOpenMLTask, path: Path) -> None:
    train = set(int(item) for item in task.train_indices)
    test = set(int(item) for item in task.test_indices)
    if train.intersection(test) or train.union(test) != set(range(len(task.y))):
        raise ValueError("official OpenML split is overlapping or incomplete")
    fields = ["sample_id", *task.feature_names, "target", "partition"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, sample_id in enumerate(task.sample_ids):
            writer.writerow(
                {
                    "sample_id": sample_id,
                    **{
                        name: float(task.x[index, offset])
                        for offset, name in enumerate(task.feature_names)
                    },
                    "target": task.y[index],
                    "partition": "train" if index in train else "test",
                }
            )


def _parameters(task: LoadedOpenMLTask) -> TabularMLParameters:
    if task.task_type == "classification":
        labels = sorted(str(item) for item in np.unique(task.y))
        metrics = ["accuracy", "f1", "auroc"]
        baseline = f"{MODULE}:MajorityClassifier"
        treatment = f"{MODULE}:NearestCentroidClassifier"
        primary = "accuracy"
        positive_label = labels[-1]
    else:
        metrics = ["rmse", "mae"]
        baseline = f"{MODULE}:MeanRegressor"
        treatment = f"{MODULE}:LinearLeastSquaresRegressor"
        primary = "rmse"
        positive_label = None
    return TabularMLParameters(
        task_type=task.task_type,
        dataset_path="dataset.csv",
        id_field="sample_id",
        target_field="target",
        feature_fields=task.feature_names,
        split_strategy="fixed_split",
        split_field="partition",
        train_values=["train"],
        test_values=["test"],
        primary_metric=primary,  # type: ignore[arg-type]
        metrics=metrics,  # type: ignore[arg-type]
        positive_label=positive_label,
        baseline_estimator={"import_path": baseline},
        treatment_estimator={"import_path": treatment},
    )


def run_openml_task_benchmark(
    task_id: int,
    *,
    output_root: str | Path,
    cache_dir: str | Path,
    seed: int = 20260731,
) -> dict[str, Any]:
    root = Path(output_root) / f"openml-task-{task_id}"
    root.mkdir(parents=True, exist_ok=True)
    task = load_official_openml_task(task_id, cache_dir=Path(cache_dir))
    dataset_path = root / "dataset.csv"
    _materialize_csv(task, dataset_path)
    parameters = _parameters(task)
    baseline = run_tabular_ml(parameters, arm="baseline", seed=seed, root=root)
    treatment = run_tabular_ml(parameters, arm="treatment", seed=seed, root=root)
    baseline_audit = validate_tabular_ml_result(baseline, parameters)
    treatment_audit = validate_tabular_ml_result(treatment, parameters)
    if not baseline_audit["valid"] or not treatment_audit["valid"]:
        raise RuntimeError("independent metric revalidation failed")
    baseline_path = root / "baseline-result.json"
    treatment_path = root / "treatment-result.json"
    write_json_atomic(baseline_path, baseline.model_dump(mode="json"))
    write_json_atomic(treatment_path, treatment.model_dump(mode="json"))
    left = baseline.primary_metric_value
    right = treatment.primary_metric_value
    if task.task_type == "classification":
        effect = right - left
    else:
        effect = left - right
    tolerance = 1e-12
    verdict = (
        "supported"
        if effect > tolerance
        else "refuted"
        if effect < -tolerance
        else "inconclusive"
    )
    report = {
        "schema_version": 1,
        "benchmark": "openml_tabular_ml_v1",
        "task_id": task.task_id,
        "task_type": task.task_type,
        "dataset": {
            "dataset_id": task.dataset_id,
            "name": task.dataset_name,
            "version": task.dataset_version,
            "openml_md5": task.dataset_md5,
            "license": task.dataset_license,
            "materialized_sha256": sha256_file(dataset_path),
        },
        "protocol": {
            "target_name": task.target_name,
            "feature_names": task.feature_names,
            "estimation_procedure": task.estimation_procedure,
            "split": {"repeat": 0, "fold": 0, "sample": 0},
            "split_sha256": _split_hash(task),
            "seed": seed,
            "profile_parameters": parameters.model_dump(mode="json"),
        },
        "results": {
            "baseline": baseline.model_dump(mode="json"),
            "treatment": treatment.model_dump(mode="json"),
            "baseline_result_sha256": sha256_file(baseline_path),
            "treatment_result_sha256": sha256_file(treatment_path),
            "independent_validation": {
                "baseline": baseline_audit,
                "treatment": treatment_audit,
            },
        },
        "scientific_decision": {
            "metric": parameters.primary_metric,
            "beneficial_effect": effect,
            "threshold": 0.0,
            "verdict": verdict,
            "claim_boundary": (
                "Verdict applies only to the two frozen deterministic estimators "
                "on OpenML repeat 0, fold 0, sample 0."
            ),
        },
    }
    report_path = root / "benchmark-report.json"
    write_json_atomic(report_path, report)
    report["report_path"] = str(report_path)
    report["report_sha256"] = sha256_file(report_path)
    return report


__all__ = [
    "LoadedOpenMLTask",
    "load_official_openml_task",
    "run_openml_task_benchmark",
]
