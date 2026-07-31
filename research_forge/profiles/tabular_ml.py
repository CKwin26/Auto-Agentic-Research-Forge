"""Deterministic execution kernel for the certified ``tabular_ml_v1`` Profile.

The module deliberately supports a narrow surface: numeric CSV features,
classification or regression, a frozen split or deterministic K-fold CV, and
estimators that implement the sklearn ``fit``/``predict`` protocol.  Model
selection, preprocessing, imputation, and feature discovery are outside this
Profile and must be frozen as a successor Profile or contract version.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import inspect
import json
import math
from pathlib import Path
from typing import Any, Iterable, Literal

import numpy as np
from pydantic import Field, model_validator

from ..experiment_execution import (
    ExperimentArtifactSpec,
    ExperimentManifest,
    ExperimentSpec,
)
from ..models import StrictModel
from ..storage import read_json, write_json_atomic
from .contracts import TabularEstimatorBinding, TabularMLParameters


class TabularPrediction(StrictModel):
    sample_id: str
    y_true: str | float
    y_pred: str | float
    y_score: float | None = None
    fold: int = Field(ge=0)


class TabularMLRunResult(StrictModel):
    schema_version: int = 1
    profile_id: Literal["tabular_ml_v1"] = "tabular_ml_v1"
    arm: Literal["baseline", "treatment"]
    seed: int
    task_type: Literal["classification", "regression"]
    metrics: dict[str, float]
    accuracy: float | None = None
    f1: float | None = None
    auroc: float | None = None
    rmse: float | None = None
    mae: float | None = None
    primary_metric: str
    primary_metric_value: float
    denominator: int = Field(ge=1)
    sample_ids: list[str] = Field(min_length=1)
    predictions: list[TabularPrediction] = Field(min_length=1)
    analysis_rows: list[dict[str, Any]] = Field(min_length=1)

    @model_validator(mode="after")
    def result_is_self_consistent(self) -> "TabularMLRunResult":
        if len(self.predictions) != self.denominator:
            raise ValueError("prediction count must equal denominator")
        if self.sample_ids != [item.sample_id for item in self.predictions]:
            raise ValueError("sample_ids must preserve prediction order")
        if self.primary_metric not in self.metrics:
            raise ValueError("primary metric is absent from metrics")
        for name, value in self.metrics.items():
            if getattr(self, name, None) is None or not math.isclose(
                float(getattr(self, name)),
                value,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(f"top-level {name} does not match metrics")
        if not math.isclose(
            self.primary_metric_value,
            self.metrics[self.primary_metric],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("primary metric value does not match metrics")
        return self


class MajorityClassifier:
    """Small deterministic baseline implementing the sklearn protocol."""

    def fit(self, x: np.ndarray, y: np.ndarray) -> "MajorityClassifier":
        labels, counts = np.unique(y.astype(str), return_counts=True)
        self.classes_ = labels
        self.class_probabilities_ = counts.astype(float) / np.sum(counts)
        self.label_ = str(labels[int(np.argmax(counts))])
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.asarray([self.label_] * len(x), dtype=str)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return np.tile(self.class_probabilities_, (len(x), 1))


class NearestCentroidClassifier:
    """Dependency-free treatment estimator for bounded acceptance fixtures."""

    def fit(
        self, x: np.ndarray, y: np.ndarray
    ) -> "NearestCentroidClassifier":
        labels = np.unique(y.astype(str))
        self.labels_ = labels
        self.centroids_ = np.vstack(
            [np.mean(x[y.astype(str) == label], axis=0) for label in labels]
        )
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        distances = np.sum(
            (x[:, None, :] - self.centroids_[None, :, :]) ** 2, axis=2
        )
        return self.labels_[np.argmin(distances, axis=1)].astype(str)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        distances = np.sqrt(
            np.sum(
                (x[:, None, :] - self.centroids_[None, :, :]) ** 2,
                axis=2,
            )
        )
        similarities = 1.0 / np.maximum(distances, 1e-12)
        return similarities / np.sum(similarities, axis=1, keepdims=True)


class MeanRegressor:
    def fit(self, x: np.ndarray, y: np.ndarray) -> "MeanRegressor":
        self.mean_ = float(np.mean(y.astype(float)))
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.full(len(x), self.mean_, dtype=float)


class LinearLeastSquaresRegressor:
    def fit(
        self, x: np.ndarray, y: np.ndarray
    ) -> "LinearLeastSquaresRegressor":
        design = np.column_stack([np.ones(len(x)), x])
        self.coefficients_ = np.linalg.lstsq(
            design, y.astype(float), rcond=None
        )[0]
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        design = np.column_stack([np.ones(len(x)), x])
        return design @ self.coefficients_


def _load_estimator(binding: TabularEstimatorBinding) -> Any:
    module_name, class_name = binding.import_path.split(":", 1)
    module = importlib.import_module(module_name)
    estimator_type = getattr(module, class_name, None)
    if estimator_type is None or not inspect.isclass(estimator_type):
        raise ValueError(
            f"estimator import is not a class: {binding.import_path}"
        )
    estimator = estimator_type(**binding.parameters)
    if not callable(getattr(estimator, "fit", None)) or not callable(
        getattr(estimator, "predict", None)
    ):
        raise ValueError("estimator must implement fit and predict")
    return estimator


def _read_csv(
    path: Path, spec: TabularMLParameters
) -> tuple[list[str], np.ndarray, np.ndarray, list[str] | None]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        required = {
            spec.id_field,
            spec.target_field,
            *spec.feature_fields,
        }
        if spec.split_field:
            required.add(spec.split_field)
        missing = sorted(required.difference(fields))
        if missing:
            raise ValueError("dataset is missing columns: " + ", ".join(missing))
        rows = list(reader)
    if not rows:
        raise ValueError("dataset contains no rows")
    ids = [str(row[spec.id_field]) for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("id_field must be non-empty and unique")
    try:
        x = np.asarray(
            [[float(row[field]) for field in spec.feature_fields] for row in rows],
            dtype=float,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "tabular_ml_v1 supports only complete numeric feature columns"
        ) from exc
    if not np.all(np.isfinite(x)):
        raise ValueError("feature matrix contains missing or non-finite values")
    if spec.task_type == "regression":
        try:
            y: np.ndarray = np.asarray(
                [float(row[spec.target_field]) for row in rows], dtype=float
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("regression target must be numeric") from exc
        if not np.all(np.isfinite(y)):
            raise ValueError("regression target contains non-finite values")
    else:
        y = np.asarray([str(row[spec.target_field]) for row in rows], dtype=str)
        if any(not item for item in y) or len(np.unique(y)) != 2:
            raise ValueError("classification requires exactly two non-empty labels")
        if spec.positive_label not in set(y):
            raise ValueError("positive_label does not occur in the target")
    split_values = (
        [str(row[spec.split_field]) for row in rows]
        if spec.split_field
        else None
    )
    return ids, x, y, split_values


def _folds(
    spec: TabularMLParameters,
    *,
    row_count: int,
    split_values: list[str] | None,
    seed: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    if spec.split_strategy == "fixed_split":
        assert split_values is not None
        train = np.asarray(
            [i for i, value in enumerate(split_values) if value in spec.train_values]
        )
        test = np.asarray(
            [i for i, value in enumerate(split_values) if value in spec.test_values]
        )
        unknown = sorted(
            set(split_values).difference({*spec.train_values, *spec.test_values})
        )
        if unknown:
            raise ValueError(
                "split column contains values outside the frozen boundary: "
                + ", ".join(unknown)
            )
        if not len(train) or not len(test):
            raise ValueError("fixed split has an empty train or test partition")
        return [(train, test)]
    assert spec.folds is not None
    if row_count < spec.folds:
        raise ValueError("row count is smaller than folds")
    indices = np.arange(row_count)
    np.random.default_rng(seed).shuffle(indices)
    buckets = [np.asarray(item, dtype=int) for item in np.array_split(indices, spec.folds)]
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    for test in buckets:
        train = np.asarray([i for i in indices if i not in set(test)], dtype=int)
        pairs.append((train, test))
    return pairs


def _binary_auc(y_true: list[str], scores: list[float], positive: str) -> float:
    positives = [i for i, value in enumerate(y_true) if value == positive]
    negatives = [i for i, value in enumerate(y_true) if value != positive]
    if not positives or not negatives:
        raise ValueError("AUROC requires both classes in formal predictions")
    wins = 0.0
    for p in positives:
        for n in negatives:
            wins += 1.0 if scores[p] > scores[n] else 0.5 if scores[p] == scores[n] else 0.0
    return wins / (len(positives) * len(negatives))


def compute_metrics(
    predictions: Iterable[TabularPrediction],
    spec: TabularMLParameters,
) -> dict[str, float]:
    rows = list(predictions)
    if not rows:
        raise ValueError("cannot evaluate an empty prediction set")
    metrics: dict[str, float] = {}
    if spec.task_type == "classification":
        positive = str(spec.positive_label)
        truth = [str(item.y_true) for item in rows]
        predicted = [str(item.y_pred) for item in rows]
        if "accuracy" in spec.metrics:
            metrics["accuracy"] = sum(
                left == right for left, right in zip(truth, predicted)
            ) / len(rows)
        if "f1" in spec.metrics:
            tp = sum(t == positive and p == positive for t, p in zip(truth, predicted))
            fp = sum(t != positive and p == positive for t, p in zip(truth, predicted))
            fn = sum(t == positive and p != positive for t, p in zip(truth, predicted))
            metrics["f1"] = 0.0 if 2 * tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)
        if "auroc" in spec.metrics:
            if any(item.y_score is None for item in rows):
                raise ValueError("AUROC requires a score for every prediction")
            metrics["auroc"] = _binary_auc(
                truth, [float(item.y_score) for item in rows], positive
            )
    else:
        truth_f = np.asarray([float(item.y_true) for item in rows])
        predicted_f = np.asarray([float(item.y_pred) for item in rows])
        residual = truth_f - predicted_f
        if "rmse" in spec.metrics:
            metrics["rmse"] = float(np.sqrt(np.mean(residual**2)))
        if "mae" in spec.metrics:
            metrics["mae"] = float(np.mean(np.abs(residual)))
    return metrics


def _positive_scores(
    estimator: Any,
    x: np.ndarray,
    positive_label: str,
) -> np.ndarray | None:
    if callable(getattr(estimator, "predict_proba", None)):
        probabilities = np.asarray(estimator.predict_proba(x), dtype=float)
        classes = [str(item) for item in getattr(estimator, "classes_", [])]
        if not classes and hasattr(estimator, "labels_"):
            classes = [str(item) for item in estimator.labels_]
        if probabilities.ndim != 2 or positive_label not in classes:
            raise ValueError(
                "predict_proba must expose classes_ including positive_label"
            )
        return probabilities[:, classes.index(positive_label)]
    if callable(getattr(estimator, "decision_function", None)):
        return np.asarray(estimator.decision_function(x), dtype=float).reshape(-1)
    return None


def run_tabular_ml(
    spec: TabularMLParameters,
    *,
    arm: Literal["baseline", "treatment"],
    seed: int,
    root: str | Path = ".",
) -> TabularMLRunResult:
    dataset = (Path(root).resolve() / spec.dataset_path).resolve()
    dataset.relative_to(Path(root).resolve())
    if not dataset.is_file():
        raise ValueError(f"dataset is unavailable: {spec.dataset_path}")
    ids, x, y, split_values = _read_csv(dataset, spec)
    binding = (
        spec.baseline_estimator if arm == "baseline" else spec.treatment_estimator
    )
    predictions: list[TabularPrediction] = []
    for fold_index, (train, test) in enumerate(
        _folds(
            spec,
            row_count=len(ids),
            split_values=split_values,
            seed=seed,
        )
    ):
        estimator = _load_estimator(binding)
        estimator.fit(x[train], y[train])
        predicted = np.asarray(estimator.predict(x[test])).reshape(-1)
        if len(predicted) != len(test):
            raise ValueError("estimator returned the wrong prediction count")
        scores = (
            _positive_scores(estimator, x[test], str(spec.positive_label))
            if spec.task_type == "classification" and "auroc" in spec.metrics
            else None
        )
        for offset, row_index in enumerate(test):
            predictions.append(
                TabularPrediction(
                    sample_id=ids[int(row_index)],
                    y_true=(
                        str(y[int(row_index)])
                        if spec.task_type == "classification"
                        else float(y[int(row_index)])
                    ),
                    y_pred=(
                        str(predicted[offset])
                        if spec.task_type == "classification"
                        else float(predicted[offset])
                    ),
                    y_score=(float(scores[offset]) if scores is not None else None),
                    fold=fold_index,
                )
            )
    predictions.sort(key=lambda item: (item.sample_id, item.fold))
    if len({item.sample_id for item in predictions}) != len(predictions):
        raise ValueError("formal prediction set contains repeated sample IDs")
    metrics = compute_metrics(predictions, spec)
    primary = metrics[spec.primary_metric]
    return TabularMLRunResult(
        arm=arm,
        seed=seed,
        task_type=spec.task_type,
        metrics=metrics,
        **metrics,
        primary_metric=spec.primary_metric,
        primary_metric_value=primary,
        denominator=len(predictions),
        sample_ids=[item.sample_id for item in predictions],
        predictions=predictions,
        analysis_rows=[
            {
                "pair_id": item.sample_id,
                "value": (
                    float(str(item.y_true) == str(item.y_pred))
                    if spec.task_type == "classification"
                    else abs(float(item.y_true) - float(item.y_pred))
                ),
            }
            for item in predictions
        ],
    )


def validate_tabular_ml_result(
    result: TabularMLRunResult | dict[str, Any],
    spec: TabularMLParameters,
    *,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    parsed = (
        result
        if isinstance(result, TabularMLRunResult)
        else TabularMLRunResult.model_validate(result)
    )
    recomputed = compute_metrics(parsed.predictions, spec)
    mismatches = {
        metric: {"reported": parsed.metrics.get(metric), "recomputed": value}
        for metric, value in recomputed.items()
        if metric not in parsed.metrics
        or not math.isclose(
            parsed.metrics[metric], value, rel_tol=0.0, abs_tol=tolerance
        )
    }
    return {
        "valid": not mismatches,
        "profile_id": "tabular_ml_v1",
        "denominator": parsed.denominator,
        "recomputed_metrics": recomputed,
        "mismatches": mismatches,
    }


def tabular_ml_experiment_manifest(
    spec: TabularMLParameters,
    *,
    config_path: str,
) -> ExperimentManifest:
    experiments: list[ExperimentSpec] = []
    for arm in ("baseline", "treatment"):
        action = f"action-tabular-ml-{arm}"
        experiments.append(
            ExperimentSpec(
                experiment_id=f"tabular-ml-{arm}",
                action_ids=[action],
                title=f"tabular_ml_v1 {arm}",
                command=[
                    "{python}",
                    "-m",
                    "research_forge.profiles.tabular_ml",
                    "run",
                    "--config",
                    config_path,
                    "--arm",
                    arm,
                    "--seed",
                    "{seed}",
                    "--root",
                    "{source_root}",
                    "--output",
                    "{evidence_dir}/result.json",
                ],
                cwd=".",
                required_inputs=[spec.dataset_path, config_path],
                artifacts=[
                    ExperimentArtifactSpec(
                        path="result.json",
                        format="json",
                        required_keys=[
                            "profile_id",
                            "metrics",
                            "primary_metric_value",
                            "denominator",
                            "sample_ids",
                            "predictions",
                        ],
                    )
                ],
            )
        )
    return ExperimentManifest(experiments=experiments)


def materialize_tabular_ml_package(
    spec: TabularMLParameters,
    root: str | Path,
    *,
    config_path: str = ".research-forge/tabular_ml_v1.json",
    manifest_path: str = ".research-forge/experiments.json",
) -> tuple[Path, Path]:
    root_path = Path(root).resolve()
    config = root_path / config_path
    manifest = root_path / manifest_path
    config.resolve().relative_to(root_path)
    manifest.resolve().relative_to(root_path)
    write_json_atomic(config, spec.model_dump(mode="json"))
    write_json_atomic(
        manifest,
        tabular_ml_experiment_manifest(
            spec, config_path=config_path
        ).model_dump(mode="json"),
    )
    return config, manifest


_CANDIDATE_SCRIPT = r'''import argparse, json, math

def centroid_fit(rows):
    labels = sorted({str(row["target"]) for row in rows})
    centers = {}
    for label in labels:
        selected = [row["features"] for row in rows if str(row["target"]) == label]
        centers[label] = [sum(values) / len(values) for values in zip(*selected)]
    return labels, centers

def solve_linear(matrix, vector):
    n = len(vector)
    aug = [list(map(float, matrix[i])) + [float(vector[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("singular least-squares system")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        aug[col] = [value / scale for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            aug[row] = [left - factor * right for left, right in zip(aug[row], aug[col])]
    return [aug[i][-1] for i in range(n)]

def linear_fit(rows):
    design = [[1.0] + list(map(float, row["features"])) for row in rows]
    target = [float(row["target"]) for row in rows]
    width = len(design[0])
    gram = [[sum(row[i] * row[j] for row in design) for j in range(width)] for i in range(width)]
    rhs = [sum(row[i] * value for row, value in zip(design, target)) for i in range(width)]
    return solve_linear(gram, rhs)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--algorithm", required=True)
    parser.add_argument("--prediction", required=True)
    args = parser.parse_args()
    data = json.load(open(args.data, encoding="utf-8"))
    train, test = data["train"], data["test"]
    output = []
    if args.algorithm == "majority_classifier":
        labels = sorted({str(row["target"]) for row in train})
        counts = {label: sum(str(row["target"]) == label for row in train) for label in labels}
        predicted = max(labels, key=lambda label: (counts[label], label))
        positive = str(data["positive_label"])
        score = counts[positive] / len(train)
        output = [{"sample_id": row["sample_id"], "prediction": predicted, "score": score} for row in test]
    elif args.algorithm == "nearest_centroid_classifier":
        labels, centers = centroid_fit(train)
        positive = str(data["positive_label"])
        for row in test:
            distances = {label: math.sqrt(sum((a-b)**2 for a,b in zip(row["features"], centers[label]))) for label in labels}
            similarities = {label: 1.0 / max(distance, 1e-12) for label, distance in distances.items()}
            output.append({"sample_id": row["sample_id"], "prediction": min(labels, key=lambda label: distances[label]), "score": similarities[positive] / sum(similarities.values())})
    elif args.algorithm == "mean_regressor":
        mean = sum(float(row["target"]) for row in train) / len(train)
        output = [{"sample_id": row["sample_id"], "prediction": mean} for row in test]
    elif args.algorithm == "linear_least_squares_regressor":
        coefficients = linear_fit(train)
        output = [{"sample_id": row["sample_id"], "prediction": coefficients[0] + sum(a*b for a,b in zip(coefficients[1:], row["features"]))} for row in test]
    else:
        raise ValueError("unsupported frozen tabular algorithm")
    with open(args.prediction, "w", encoding="utf-8") as handle:
        json.dump({"predictions": output}, handle, ensure_ascii=False, sort_keys=True)

if __name__ == "__main__":
    main()
'''


_EVALUATOR_SCRIPT = r'''import argparse, json, math

def auc(truth, scores, positive):
    p = [i for i, value in enumerate(truth) if value == positive]
    n = [i for i, value in enumerate(truth) if value != positive]
    if not p or not n:
        raise ValueError("AUROC requires both classes")
    wins = 0.0
    for left in p:
        for right in n:
            wins += 1.0 if scores[left] > scores[right] else 0.5 if scores[left] == scores[right] else 0.0
    return wins / (len(p) * len(n))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--metrics", required=True)
    args = parser.parse_args()
    predictions = json.load(open(args.prediction, encoding="utf-8"))["predictions"]
    target_payload = json.load(open(args.target, encoding="utf-8"))
    targets = {str(row["sample_id"]): row["target"] for row in target_payload["targets"]}
    ids = [str(row["sample_id"]) for row in predictions]
    if len(ids) != len(set(ids)) or set(ids) != set(targets):
        raise ValueError("submission IDs do not exactly match frozen targets")
    truth = [targets[item] for item in ids]
    predicted = [row["prediction"] for row in predictions]
    metrics = {}
    if target_payload["task_type"] == "classification":
        truth = [str(item) for item in truth]
        predicted = [str(item) for item in predicted]
        positive = str(target_payload["positive_label"])
        if "accuracy" in target_payload["metrics"]:
            metrics["accuracy"] = sum(a == b for a,b in zip(truth, predicted)) / len(ids)
        if "f1" in target_payload["metrics"]:
            tp = sum(a == positive and b == positive for a,b in zip(truth, predicted))
            fp = sum(a != positive and b == positive for a,b in zip(truth, predicted))
            fn = sum(a == positive and b != positive for a,b in zip(truth, predicted))
            metrics["f1"] = 0.0 if 2*tp+fp+fn == 0 else 2*tp/(2*tp+fp+fn)
        if "auroc" in target_payload["metrics"]:
            metrics["auroc"] = auc(truth, [float(row["score"]) for row in predictions], positive)
        values = [float(a == b) for a,b in zip(truth, predicted)]
    else:
        residuals = [float(a)-float(b) for a,b in zip(truth, predicted)]
        if "rmse" in target_payload["metrics"]:
            metrics["rmse"] = math.sqrt(sum(value*value for value in residuals) / len(ids))
        if "mae" in target_payload["metrics"]:
            metrics["mae"] = sum(abs(value) for value in residuals) / len(ids)
        values = [abs(value) for value in residuals]
    primary = target_payload["primary_metric"]
    output = {"profile_id": "tabular_ml_v1", "primary_metric": primary, "primary_metric_value": metrics[primary], "denominator": len(ids), "sample_ids": ids, "metrics": metrics, "analysis_rows": [{"pair_id": sample_id, "value": value} for sample_id, value in zip(ids, values)]}
    output.update(metrics)
    with open(args.metrics, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, sort_keys=True)

if __name__ == "__main__":
    main()
'''


_SMOKE_SCRIPT = r'''import argparse, json, subprocess, sys
parser = argparse.ArgumentParser()
parser.add_argument("--candidate", required=True)
parser.add_argument("--evaluator", required=True)
parser.add_argument("--data", required=True)
parser.add_argument("--target", required=True)
parser.add_argument("--algorithm", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
prediction = args.output + ".predictions"
subprocess.run([sys.executable, args.candidate, "--data", args.data, "--algorithm", args.algorithm, "--prediction", prediction], check=True)
subprocess.run([sys.executable, args.evaluator, "--prediction", prediction, "--target", args.target, "--metrics", args.output], check=True)
'''


_FORMAL_ALGORITHMS = {
    f"{__name__}:MajorityClassifier": "majority_classifier",
    f"{__name__}:NearestCentroidClassifier": "nearest_centroid_classifier",
    f"{__name__}:MeanRegressor": "mean_regressor",
    f"{__name__}:LinearLeastSquaresRegressor": "linear_least_squares_regressor",
}


def _formal_rows(
    spec: TabularMLParameters, root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if spec.split_strategy != "fixed_split":
        raise ValueError(
            "formal isolated tabular_ml_v1 currently requires fixed_split; "
            "K-fold is available only for Stage 2 feasibility"
        )
    path = (root / spec.dataset_path).resolve()
    path.relative_to(root)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for row in rows:
        split = str(row[str(spec.split_field)])
        features = [float(row[field]) for field in spec.feature_fields]
        target: str | float = (
            str(row[spec.target_field])
            if spec.task_type == "classification"
            else float(row[spec.target_field])
        )
        if split in spec.train_values:
            train.append(
                {
                    "sample_id": str(row[spec.id_field]),
                    "features": features,
                    "target": target,
                }
            )
        elif split in spec.test_values:
            sample_id = str(row[spec.id_field])
            test.append({"sample_id": sample_id, "features": features})
            targets.append({"sample_id": sample_id, "target": target})
        else:
            raise ValueError("dataset contains an unregistered split value")
    if not train or not test:
        raise ValueError("formal split has an empty train or test partition")
    return train, test, targets


def materialize_isolated_tabular_ml_package(
    spec: TabularMLParameters,
    root: str | Path,
    *,
    container_image: str = "python:3.12-slim",
    manifest_path: str = ".research-forge/experiments.json",
) -> tuple[Path, Path]:
    """Write one target-isolated Stage 3 package for the fixed formal split."""

    root_path = Path(root).resolve()
    train, test, targets = _formal_rows(spec, root_path)
    for binding in (spec.baseline_estimator, spec.treatment_estimator):
        if binding.parameters or binding.import_path not in _FORMAL_ALGORITHMS:
            raise ValueError(
                "formal tabular_ml_v1 currently supports only frozen built-in "
                "estimators without free parameters"
            )
    package_root = root_path / ".research-forge" / "tabular_ml_v1"
    candidate_data = package_root / "candidate-data.json"
    evaluator_targets = package_root / "evaluator-targets.json"
    smoke_data = package_root / "smoke-data.json"
    smoke_targets = package_root / "smoke-targets.json"
    candidate_script = package_root / "candidate.py"
    evaluator_script = package_root / "evaluator.py"
    smoke_script = package_root / "smoke.py"
    candidate_payload = {
        "task_type": spec.task_type,
        "positive_label": spec.positive_label,
        "train": train,
        "test": test,
    }
    target_payload = {
        "task_type": spec.task_type,
        "positive_label": spec.positive_label,
        "primary_metric": spec.primary_metric,
        "metrics": spec.metrics,
        "targets": targets,
    }
    write_json_atomic(candidate_data, candidate_payload)
    write_json_atomic(evaluator_targets, target_payload)
    # Smoke uses a disjoint file identity and remains engineering-only.  It
    # exercises the same code without granting evidentiary status.
    write_json_atomic(smoke_data, candidate_payload)
    write_json_atomic(smoke_targets, target_payload)
    candidate_script.parent.mkdir(parents=True, exist_ok=True)
    candidate_script.write_text(_CANDIDATE_SCRIPT, encoding="utf-8")
    evaluator_script.write_text(_EVALUATOR_SCRIPT, encoding="utf-8")
    smoke_script.write_text(_SMOKE_SCRIPT, encoding="utf-8")
    relative = lambda path: path.relative_to(root_path).as_posix()
    experiments: list[ExperimentSpec] = []
    for arm, binding in (
        ("baseline", spec.baseline_estimator),
        ("treatment", spec.treatment_estimator),
    ):
        algorithm = _FORMAL_ALGORITHMS[binding.import_path]
        experiments.append(
            ExperimentSpec(
                experiment_id=f"tabular-ml-{arm}",
                action_ids=[f"action-tabular-ml-{arm}"],
                title=f"target-isolated tabular_ml_v1 {arm}",
                command=[
                    "{python}",
                    "/workspace/input/" + relative(candidate_script),
                    "--data",
                    "{data_file}",
                    "--algorithm",
                    algorithm,
                    "--prediction",
                    "{prediction_file}",
                ],
                smoke_command=[
                    "{python}",
                    "/workspace/input/" + relative(smoke_script),
                    "--candidate",
                    "/workspace/input/" + relative(candidate_script),
                    "--evaluator",
                    "/workspace/input/" + relative(evaluator_script),
                    "--data",
                    "/workspace/input/" + relative(smoke_data),
                    "--target",
                    "/workspace/input/" + relative(smoke_targets),
                    "--algorithm",
                    algorithm,
                    "--output",
                    "{evidence_dir}/result.json",
                ],
                smoke_required_inputs=[
                    relative(smoke_script),
                    relative(candidate_script),
                    relative(evaluator_script),
                    relative(smoke_data),
                    relative(smoke_targets),
                ],
                execution_backend="isolated_candidate_evaluator",
                container_image=container_image,
                required_inputs=[relative(candidate_data)],
                candidate_code_paths=[relative(candidate_script)],
                evaluator_command=[
                    "{python}",
                    "/workspace/input/" + relative(evaluator_script),
                    "--prediction",
                    "{prediction_file}",
                    "--target",
                    "{target_file}",
                    "--metrics",
                    "{metrics_file}",
                ],
                evaluator_code_paths=[relative(evaluator_script)],
                evaluator_required_inputs=[relative(evaluator_targets)],
                prediction_artifact_path="predictions.json",
                network_access=False,
                artifacts=[
                    ExperimentArtifactSpec(
                        path="result.json",
                        format="json",
                        required_keys=[
                            spec.primary_metric,
                            "denominator",
                            "sample_ids",
                            "analysis_rows",
                        ],
                    )
                ],
            )
        )
    manifest = root_path / manifest_path
    write_json_atomic(
        manifest,
        ExperimentManifest(experiments=experiments).model_dump(mode="json"),
    )
    return package_root, manifest


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--arm", choices=["baseline", "treatment"], required=True)
    run_parser.add_argument("--seed", type=int, required=True)
    run_parser.add_argument("--root", default=".")
    run_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    config = Path(args.config)
    config = config.resolve() if config.is_absolute() else (root / config).resolve()
    config.relative_to(root)
    spec = TabularMLParameters.model_validate(read_json(config))
    result = run_tabular_ml(spec, arm=args.arm, seed=args.seed, root=root)
    write_json_atomic(Path(args.output).resolve(), result.model_dump(mode="json"))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(_main())


__all__ = [
    "LinearLeastSquaresRegressor",
    "MajorityClassifier",
    "MeanRegressor",
    "NearestCentroidClassifier",
    "TabularMLRunResult",
    "TabularPrediction",
    "compute_metrics",
    "materialize_tabular_ml_package",
    "materialize_isolated_tabular_ml_package",
    "run_tabular_ml",
    "tabular_ml_experiment_manifest",
    "validate_tabular_ml_result",
]
