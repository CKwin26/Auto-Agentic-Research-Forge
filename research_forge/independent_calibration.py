from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .counterfactual_rebranch import LocalNLIEngine, ensure_nli_assets
from .storage import read_json, sha256_file, write_json_atomic


CALIBRATION_SCHEMA_VERSION = 2
MIN_EVALUATION_MACRO_F1 = 0.70
MIN_EVALUATION_COVERAGE = 0.80
THRESHOLD_GRID = tuple(round(0.50 + 0.05 * index, 2) for index in range(10))


def _split(example_id: str) -> str:
    bucket = int(hashlib.sha256(example_id.encode("utf-8")).hexdigest()[:8], 16) % 2
    return "threshold_selection" if bucket == 0 else "locked_evaluation"


def _verdict(
    scores: dict[str, float], entailment_threshold: float, contradiction_threshold: float
) -> str:
    # The downstream construct is binary support versus contradiction.  Neutral
    # is therefore treated as out-of-construct mass and we condition on the two
    # preregistered construct labels before applying the abstention thresholds.
    raw_entailment = float(scores["entailment"])
    raw_contradiction = float(scores["contradiction"])
    construct_mass = raw_entailment + raw_contradiction
    if construct_mass <= 0.0:
        return "abstain"
    entailment = raw_entailment / construct_mass
    contradiction = raw_contradiction / construct_mass
    if entailment >= entailment_threshold and entailment > contradiction:
        return "supported"
    if contradiction >= contradiction_threshold and contradiction > entailment:
        return "unsupported"
    return "abstain"


def _metrics(rows: list[dict[str, Any]], thresholds: tuple[float, float]) -> dict[str, Any]:
    entailment_threshold, contradiction_threshold = thresholds
    predictions = [
        _verdict(dict(row["scores"]), entailment_threshold, contradiction_threshold)
        for row in rows
    ]
    gold = [str(row["gold_label"]) for row in rows]
    labels = ("supported", "unsupported")
    f1: dict[str, float] = {}
    recalls: dict[str, float] = {}
    for label in labels:
        tp = sum(p == label and g == label for p, g in zip(predictions, gold, strict=True))
        fp = sum(p == label and g != label for p, g in zip(predictions, gold, strict=True))
        fn = sum(p != label and g == label for p, g in zip(predictions, gold, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1[label] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        recalls[label] = recall
    decided = [index for index, prediction in enumerate(predictions) if prediction != "abstain"]
    matrix = Counter((gold[index], predictions[index]) for index in range(len(rows)))
    return {
        "count": len(rows),
        "entailment_threshold": entailment_threshold,
        "contradiction_threshold": contradiction_threshold,
        "coverage": len(decided) / len(rows) if rows else 0.0,
        "accuracy_all": (
            sum(p == g for p, g in zip(predictions, gold, strict=True)) / len(rows)
            if rows
            else 0.0
        ),
        "accuracy_decided": (
            sum(predictions[index] == gold[index] for index in decided) / len(decided)
            if decided
            else 0.0
        ),
        "macro_f1": sum(f1.values()) / len(f1),
        "class_f1": f1,
        "class_recall": recalls,
        "prediction_counts": dict(Counter(predictions)),
        "confusion_matrix": {
            f"gold={gold_label}|pred={prediction}": matrix[(gold_label, prediction)]
            for gold_label in labels
            for prediction in (*labels, "abstain")
        },
    }


def calibrate_independent_evaluator(
    dataset_path: str | Path,
    *,
    report_path: str | Path,
    contract_path: str | Path,
) -> dict[str, Any]:
    dataset_file = Path(dataset_path).resolve()
    dataset = read_json(dataset_file)
    examples = [dict(item) for item in dataset.get("examples", [])]
    if len(examples) < 200:
        raise ValueError("independent calibration requires at least 200 labeled examples")
    assets = ensure_nli_assets()
    engine = LocalNLIEngine(
        Path(str(assets["model_path"])),
        Path(str(assets["tokenizer_path"])),
        batch_size=16,
    )
    pairs = [(str(item["premise"]), str(item["hypothesis"])) for item in examples]
    scores = engine.score(pairs)
    rows = [
        {
            "example_id": item["example_id"],
            "gold_label": item["gold_label"],
            "split": _split(str(item["example_id"])),
            "scores": score,
        }
        for item, score in zip(examples, scores, strict=True)
    ]
    threshold_rows = [item for item in rows if item["split"] == "threshold_selection"]
    evaluation_rows = [item for item in rows if item["split"] == "locked_evaluation"]
    candidates = []
    for thresholds in itertools.product(THRESHOLD_GRID, repeat=2):
        metrics = _metrics(threshold_rows, thresholds)
        if metrics["coverage"] >= MIN_EVALUATION_COVERAGE:
            candidates.append(metrics)
    if not candidates:
        raise ValueError("no threshold pair satisfies the preregistered minimum coverage")
    selected = max(
        candidates,
        key=lambda item: (
            item["macro_f1"],
            item["accuracy_all"],
            item["coverage"],
            -item["entailment_threshold"],
            -item["contradiction_threshold"],
        ),
    )
    thresholds = (
        float(selected["entailment_threshold"]),
        float(selected["contradiction_threshold"]),
    )
    evaluation = _metrics(evaluation_rows, thresholds)
    passed = bool(
        evaluation["macro_f1"] >= MIN_EVALUATION_MACRO_F1
        and evaluation["coverage"] >= MIN_EVALUATION_COVERAGE
    )
    report = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "path": str(dataset_file),
            "sha256": sha256_file(dataset_file),
            "dataset_id": dataset.get("dataset_id"),
            "source_split": dataset.get("source_split"),
            "example_count": len(examples),
            "gold_provenance": "Public SciFact human SUPPORT/CONTRADICT evidence labels; not the deferred project-specific human audit.",
        },
        "split_rule": "sha256(example_id) modulo 2; bucket 0 selects thresholds and bucket 1 is locked evaluation",
        "threshold_grid": list(THRESHOLD_GRID),
        "decision_rule": "Condition entailment and contradiction probabilities on their sum; neutral is out-of-construct mass; abstain unless a class meets its selected threshold.",
        "acceptance_rule": {
            "minimum_locked_evaluation_macro_f1": MIN_EVALUATION_MACRO_F1,
            "minimum_locked_evaluation_coverage": MIN_EVALUATION_COVERAGE,
        },
        "model": assets,
        "threshold_selection": selected,
        "locked_evaluation": evaluation,
        "runtime": {
            "providers": engine.providers,
            "inference_calls": engine.inference_calls,
            "encoded_pair_count": engine.encoded_pair_count,
            "encoded_token_count": engine.encoded_token_count,
        },
        "passed": passed,
        "limitations": [
            "SciFact is scientific claim verification but does not reproduce the exact Research Forge claim distribution.",
            "This cross-family calibration does not complete or replace the deferred two-human project audit.",
            "Novelty and absence-of-prior-work claims remain forced to abstain under pairwise NLI.",
        ],
    }
    write_json_atomic(Path(report_path).resolve(), report)
    if not passed:
        raise ValueError(
            "independent evaluator failed locked calibration: "
            f"macro_f1={evaluation['macro_f1']:.4f}, coverage={evaluation['coverage']:.4f}"
        )
    contract_seed = {
        "dataset_sha256": report["dataset"]["sha256"],
        "model_sha256": assets["model_sha256"],
        "tokenizer_sha256": assets["tokenizer_sha256"],
        "thresholds": thresholds,
        "acceptance_rule": report["acceptance_rule"],
    }
    contract_id = "calibration-" + hashlib.sha256(
        json.dumps(contract_seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    contract = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "contract_id": contract_id,
        "created_at_utc": report["created_at_utc"],
        "status": "completed_cross_family_public_gold_calibration",
        "evaluator": "external_evaluator_cross_family_deberta_v3_nli",
        "model": assets,
        "entailment_threshold": thresholds[0],
        "contradiction_threshold": thresholds[1],
        "calibration_report_path": str(Path(report_path).resolve()),
        "calibration_report_sha256": sha256_file(Path(report_path).resolve()),
        "locked_evaluation_macro_f1": evaluation["macro_f1"],
        "locked_evaluation_coverage": evaluation["coverage"],
        "human_validation_complete": False,
        "project_specific_human_audit": "pending",
        "passed": True,
    }
    write_json_atomic(Path(contract_path).resolve(), contract)
    return contract
