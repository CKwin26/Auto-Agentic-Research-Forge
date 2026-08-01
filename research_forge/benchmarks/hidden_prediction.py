"""Real hidden-target acceptance for ``benchmark_prediction_v1``.

The candidate receives labelled training rows and unlabelled test features.
Formal test targets are materialized only in a disjoint evaluator bundle.  Both
candidate and evaluator execute in separate network-disabled, read-only-root
containers.  The host then recomputes the registered metrics with the frozen
reference evaluator and freezes a report outside the product repository.
"""

from __future__ import annotations

import csv
import inspect
import json
import shutil
from pathlib import Path
from typing import Any, Callable

from ..container_execution import (
    ContainerExecutionPolicy,
    ContainerExecutionResult,
    run_isolated_command,
)
from ..evaluator_comparison import (
    EvaluatorFamilyResult,
    EvaluatorObservation,
    compare_evaluator_families,
)
from ..profiles.benchmark_prediction import evaluate_csv_submission
from ..profiles.contracts import BenchmarkPredictionParameters
from ..storage import sha256_file, write_json_atomic, write_text_atomic


IsolatedRunner = Callable[..., ContainerExecutionResult]


_CANDIDATE_PROGRAM = r'''import csv, math, sys
from collections import Counter, defaultdict
from pathlib import Path

mode = sys.argv[1]
output = Path(sys.argv[2])
train_path = Path('/workspace/input/task/train.csv')
test_path = Path('/workspace/input/task/test.csv')
with train_path.open(encoding='utf-8', newline='') as handle:
    train = list(csv.DictReader(handle))
with test_path.open(encoding='utf-8', newline='') as handle:
    test = list(csv.DictReader(handle))
features = [name for name in train[0] if name not in {'sample_id', 'target'}]
labels = sorted({row['target'] for row in train})
positive = 'Rock'
counts = Counter(row['target'] for row in train)

def vector(row):
    return [float(row[name]) for name in features]

if mode == 'baseline':
    prediction = max(labels, key=lambda label: (counts[label], label))
    positive_score = counts[positive] / len(train)
    rows = [(row['sample_id'], prediction, positive_score) for row in test]
elif mode == 'treatment':
    sums = defaultdict(lambda: [0.0] * len(features))
    for row in train:
        values = vector(row)
        for index, value in enumerate(values):
            sums[row['target']][index] += value
    centroids = {
        label: [value / counts[label] for value in sums[label]]
        for label in labels
    }
    rows = []
    for row in test:
        values = vector(row)
        distances = {
            label: math.sqrt(sum((a - b) ** 2 for a, b in zip(values, center)))
            for label, center in centroids.items()
        }
        prediction = min(labels, key=lambda label: (distances[label], label))
        similarities = {
            label: 1.0 / max(distances[label], 1e-12) for label in labels
        }
        score = similarities[positive] / sum(similarities.values())
        rows.append((row['sample_id'], prediction, score))
else:
    raise SystemExit('unknown arm')

output.parent.mkdir(parents=True, exist_ok=True)
with output.open('w', encoding='utf-8', newline='') as handle:
    writer = csv.writer(handle)
    writer.writerow(['sample_id', 'prediction', 'score'])
    writer.writerows(rows)
'''


_EVALUATOR_PROGRAM = r'''import csv, json, sys
from pathlib import Path

output = Path(sys.argv[1])
with Path('/workspace/input/submission.csv').open(encoding='utf-8', newline='') as handle:
    submission = list(csv.DictReader(handle))
with Path('/workspace/input/targets.csv').open(encoding='utf-8', newline='') as handle:
    targets = list(csv.DictReader(handle))
target_by_id = {row['sample_id']: row['target'] for row in targets}
submission_ids = [row['sample_id'] for row in submission]
if len(submission_ids) != len(set(submission_ids)) or set(submission_ids) != set(target_by_id):
    raise SystemExit('submission IDs do not exactly match hidden targets')
correct = sum(row['prediction'] == target_by_id[row['sample_id']] for row in submission)
report = {
    'primary_metric': 'accuracy',
    'primary_metric_value': correct / len(submission),
    'denominator': len(submission),
    'sample_ids': submission_ids,
    'observations': [
        {
            'item_id': row['sample_id'],
            'score': float(row['prediction'] == target_by_id[row['sample_id']]),
            'label': 'correct' if row['prediction'] == target_by_id[row['sample_id']] else 'incorrect',
        }
        for row in submission
    ],
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, sort_keys=True, indent=2), encoding='utf-8')
'''


def _write_rows(
    path: Path, fieldnames: list[str], rows: list[dict[str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _materialize_hidden_boundary(
    source_dataset: Path,
    source_report: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    expected = str(source_report["dataset"]["materialized_sha256"])
    if sha256_file(source_dataset) != expected:
        raise ValueError("OpenML materialized dataset hash does not match report")
    parameters = dict(source_report["protocol"]["profile_parameters"])
    if parameters.get("task_type") != "classification":
        raise ValueError("hidden-target acceptance currently requires classification")
    id_field = str(parameters["id_field"])
    target_field = str(parameters["target_field"])
    split_field = str(parameters["split_field"])
    features = [str(item) for item in parameters["feature_fields"]]
    with source_dataset.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    train = [row for row in rows if row[split_field] in parameters["train_values"]]
    test = [row for row in rows if row[split_field] in parameters["test_values"]]
    if not train or not test or len(train) + len(test) != len(rows):
        raise ValueError("frozen split is empty or incomplete")
    candidate = root / "candidate-input"
    evaluator = root / "evaluator-source"
    write_text_atomic(candidate / "candidate.py", _CANDIDATE_PROGRAM)
    _write_rows(
        candidate / "task" / "train.csv",
        [id_field, *features, target_field],
        [
            {field: row[field] for field in [id_field, *features, target_field]}
            for row in train
        ],
    )
    _write_rows(
        candidate / "task" / "test.csv",
        [id_field, *features],
        [{field: row[field] for field in [id_field, *features]} for row in test],
    )
    write_text_atomic(evaluator / "evaluate.py", _EVALUATOR_PROGRAM)
    _write_rows(
        evaluator / "targets.csv",
        [id_field, target_field],
        [{id_field: row[id_field], target_field: row[target_field]} for row in test],
    )
    target = evaluator / "targets.csv"
    target_hash = sha256_file(target)
    candidate_hashes = {
        str(path.relative_to(candidate)).replace("\\", "/"): sha256_file(path)
        for path in sorted(candidate.rglob("*"))
        if path.is_file()
    }
    if target_hash in candidate_hashes.values():
        raise ValueError("hidden target bytes leaked into the candidate bundle")
    manifest = {
        "schema_version": 1,
        "source_benchmark": source_report["benchmark"],
        "openml_task_id": source_report["task_id"],
        "dataset": source_report["dataset"],
        "source_dataset_sha256": expected,
        "source_report_sha256": sha256_file(
            source_dataset.parent / "benchmark-report.json"
        ),
        "train_rows": len(train),
        "hidden_test_rows": len(test),
        "candidate_visible_files": candidate_hashes,
        "hidden_target_sha256": target_hash,
        "target_present_in_candidate_bundle": False,
    }
    write_json_atomic(root / "hidden-boundary-manifest.json", manifest)
    return manifest


def run_openml_hidden_prediction_acceptance(
    source_task_root: str | Path,
    *,
    output_root: str | Path,
    policy: ContainerExecutionPolicy | None = None,
    isolated_runner: IsolatedRunner = run_isolated_command,
) -> dict[str, Any]:
    """Run baseline and treatment against a real hidden OpenML test target."""

    source = Path(source_task_root).resolve()
    dataset = source / "dataset.csv"
    source_report_path = source / "benchmark-report.json"
    if not dataset.is_file() or not source_report_path.is_file():
        raise FileNotFoundError("source task lacks dataset.csv or benchmark-report.json")
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("acceptance output must be a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    boundary = _materialize_hidden_boundary(dataset, source_report, root)
    selected = policy or ContainerExecutionPolicy(timeout_seconds=300)
    parameters = BenchmarkPredictionParameters.model_validate(
        {
            "task_type": "classification",
            "candidate_input_path": "task/test.csv",
            "evaluator_target_path": "evaluator/targets.csv",
            "id_field": "sample_id",
            "prediction_field": "prediction",
            "target_field": "target",
            "score_field": "score",
            "primary_metric": "accuracy",
            "metrics": ["accuracy", "f1", "auroc"],
            "positive_label": str(
                source_report["protocol"]["profile_parameters"]["positive_label"]
            ),
            "prediction_artifact_path": "submission.csv",
        }
    )
    arm_reports: dict[str, Any] = {}
    isolated_observations: list[EvaluatorObservation] = []
    reference_observations: list[EvaluatorObservation] = []
    for arm in ("baseline", "treatment"):
        candidate_output = root / "candidate-output" / arm
        candidate_run = isolated_runner(
            ["python", "candidate.py", arm, "/workspace/output/submission.csv"],
            input_dir=root / "candidate-input",
            output_dir=candidate_output,
            policy=selected,
        )
        submission = candidate_output / "submission.csv"
        if candidate_run.exit_code != 0 or not submission.is_file():
            raise RuntimeError(f"{arm} candidate execution failed: {candidate_run.stderr}")
        evaluator_input = root / "evaluator-input" / arm
        evaluator_input.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / "evaluator-source" / "evaluate.py", evaluator_input)
        shutil.copy2(root / "evaluator-source" / "targets.csv", evaluator_input)
        shutil.copy2(submission, evaluator_input / "submission.csv")
        evaluator_output = root / "evaluator-output" / arm
        evaluator_run = isolated_runner(
            ["python", "evaluate.py", "/workspace/output/result.json"],
            input_dir=evaluator_input,
            output_dir=evaluator_output,
            policy=selected,
        )
        evaluator_result_path = evaluator_output / "result.json"
        if evaluator_run.exit_code != 0 or not evaluator_result_path.is_file():
            raise RuntimeError(f"{arm} evaluator execution failed: {evaluator_run.stderr}")
        isolated_result = json.loads(evaluator_result_path.read_text(encoding="utf-8"))
        reference = evaluate_csv_submission(
            parameters,
            submission_path=submission,
            target_path=root / "evaluator-source" / "targets.csv",
        )
        if abs(
            float(isolated_result["primary_metric_value"])
            - float(reference["primary_metric_value"])
        ) > 1e-12:
            raise RuntimeError("isolated evaluator disagrees with frozen reference")
        arm_reports[arm] = {
            "submission_sha256": sha256_file(submission),
            "isolated_evaluator_result_sha256": sha256_file(evaluator_result_path),
            "metrics": reference["metrics"],
            "primary_metric_value": reference["primary_metric_value"],
            "denominator": reference["denominator"],
            "candidate_isolation": candidate_run.isolation_attestation,
            "evaluator_isolation": evaluator_run.isolation_attestation,
            "independent_recalculation_match": True,
        }
        isolated_observations.extend(
            EvaluatorObservation(
                item_id=f"{arm}:{item['item_id']}",
                score=float(item["score"]),
                label=str(item["label"]),
            )
            for item in isolated_result["observations"]
        )
        reference_observations.extend(
            EvaluatorObservation(
                item_id=f"{arm}:{item['pair_id']}",
                score=float(item["value"]),
                label=(
                    "correct" if float(item["value"]) == 1.0 else "incorrect"
                ),
            )
            for item in reference["analysis_rows"]
        )
    effect = (
        float(arm_reports["treatment"]["primary_metric_value"])
        - float(arm_reports["baseline"]["primary_metric_value"])
    )
    verdict = "supported" if effect > 0 else "refuted" if effect < 0 else "inconclusive"
    isolated_evaluator_path = root / "evaluator-source" / "evaluate.py"
    reference_source = inspect.getsourcefile(evaluate_csv_submission)
    if reference_source is None:
        raise RuntimeError("frozen reference evaluator source cannot be located")
    evaluator_disagreement = compare_evaluator_families(
        study_id="openml-39-hidden-target-benchmark-prediction-v1",
        primary=EvaluatorFamilyResult(
            evaluator_id="isolated-hidden-target-accuracy-v1",
            family_id="isolated-csv-accuracy",
            implementation_digest=sha256_file(isolated_evaluator_path),
            metric_name="accuracy",
            aggregate_score=sum(item.score for item in isolated_observations)
            / len(isolated_observations),
            direction="higher_is_better",
            decision=verdict,
            observations=isolated_observations,
        ),
        secondary=EvaluatorFamilyResult(
            evaluator_id="benchmark-profile-reference-v1",
            family_id="profile-metric-recomputation",
            implementation_digest=sha256_file(Path(reference_source)),
            metric_name="accuracy",
            aggregate_score=sum(item.score for item in reference_observations)
            / len(reference_observations),
            direction="higher_is_better",
            decision=verdict,
            observations=reference_observations,
        ),
        row_tolerance=1e-12,
        aggregate_tolerance=1e-12,
    )
    report = {
        "schema_version": 1,
        "acceptance_id": "openml-39-hidden-target-benchmark-prediction-v1",
        "profile_id": "benchmark_prediction_v1",
        "maturity_evidence": "C4_real_case_validated",
        "source": {
            "openml_task_id": source_report["task_id"],
            "dataset": source_report["dataset"],
            "official_split": source_report["protocol"]["estimation_procedure"],
        },
        "hidden_target_boundary": boundary,
        "container_policy": {
            "image": selected.image,
            "network": "none",
            "read_only_rootfs": True,
            "separate_candidate_and_evaluator_mounts": True,
        },
        "arms": arm_reports,
        "scientific_decision": {
            "primary_metric": "accuracy",
            "beneficial_effect": effect,
            "threshold": 0.0,
            "verdict": verdict,
            "claim_boundary": (
                "Applies only to frozen majority-class and nearest-centroid "
                "candidates on the official OpenML task 39 test split."
            ),
        },
        "evaluator_disagreement_report": evaluator_disagreement.model_dump(
            mode="json"
        ),
        "independent_validation": {
            "candidate_never_received_hidden_target": True,
            "isolated_evaluator_ran_after_submission": True,
            "reference_metrics_recomputed_from_per_sample_predictions": True,
            "distinct_evaluator_families_compared": True,
            "evaluator_verdict_stable": evaluator_disagreement.verdict_stable,
            "external_independent_operator": False,
        },
    }
    report_path = root / "acceptance-report.json"
    write_json_atomic(report_path, report)
    return {
        **report,
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path),
    }


__all__ = ["run_openml_hidden_prediction_acceptance"]
