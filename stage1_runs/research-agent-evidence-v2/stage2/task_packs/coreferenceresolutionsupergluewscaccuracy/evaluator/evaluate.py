from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + end - 1) / 2.0 + 1.0
        for index in ordered[start:end]:
            result[index] = rank
        start = end
    return result


def spearman(predictions: list[float], labels: list[float]) -> float:
    left = ranks(predictions)
    right = ranks(labels)
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_norm = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_norm = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("Spearman correlation is undefined for constant values")
    return numerator / (left_norm * right_norm)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True)
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    labels = json.loads((Path(__file__).parent / "labels.json").read_text(encoding="utf-8"))
    with Path(args.submission).open("r", encoding="utf-8", newline="") as handle:
        predictions = [row[0] for row in list(csv.reader(handle))[1:]]
    if len(predictions) != len(labels):
        raise ValueError(f"submission row count {len(predictions)} != {len(labels)}")
    if 'Accuracy' == "Accuracy":
        value = sum(str(prediction) == str(label) for prediction, label in zip(predictions, labels)) / len(labels)
    elif 'Accuracy' == "SpearmanCorrelation":
        value = spearman([float(item) for item in predictions], [float(item) for item in labels])
    else:
        raise ValueError("unsupported AIRS-lite metric: " + 'Accuracy')
    Path(args.metrics).write_text(json.dumps({'Accuracy': value}), encoding="utf-8")


if __name__ == "__main__":
    main()
