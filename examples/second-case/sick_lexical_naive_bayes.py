from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path


TOKEN = re.compile(r"[a-z]+")


NEGATIONS = {"no", "not", "never", "none", "nobody", "nothing", "without"}


def profile(row: dict[str, object]) -> tuple[int, int]:
    left = set(TOKEN.findall(str(row.get("sentence_A", "")).lower()))
    right = set(TOKEN.findall(str(row.get("sentence_B", "")).lower()))
    union = left | right
    overlap = len(left & right) / len(union) if union else 0.0
    negation_mismatch = int(bool(left & NEGATIONS) != bool(right & NEGATIONS))
    return negation_mismatch, min(int(overlap * 10), 9)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    del args.seed
    project = Path(os.environ["AUTORESEARCH_PROJECT_DIR"])
    train = [json.loads(line) for line in (project / "data" / "train.jsonl").read_text(encoding="utf-8").splitlines() if line]
    test = [json.loads(line) for line in (project / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if line]
    labels = sorted({str(row["label"]) for row in train})
    document_count = Counter(str(row["label"]) for row in train)
    bins: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    for row in train:
        bins[profile(row)][str(row["label"])] += 1
    majority = max(labels, key=lambda label: (document_count[label], label))
    predictions: list[str] = []
    for row in test:
        counts = bins.get(profile(row))
        predictions.append(max(labels, key=lambda label: (counts[label], document_count[label], label)) if counts else majority)
    with Path(args.submission).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["prediction"])
        writer.writerows([[label] for label in predictions])


if __name__ == "__main__":
    main()
