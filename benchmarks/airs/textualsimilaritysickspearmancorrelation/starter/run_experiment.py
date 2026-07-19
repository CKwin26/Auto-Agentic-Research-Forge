from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path


def tokens(value: object) -> set[str]:
    return {token.strip(".,!?;:\"'()[]{}").lower() for token in str(value).split() if token.strip()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    alpha = float(params.get("alpha", 1.0))
    project = Path(os.environ["AUTORESEARCH_PROJECT_DIR"])
    rows = [json.loads(line) for line in (project / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if line]
    predictions = []
    for row in rows:
        left = tokens(row['sentence_A'])
        right = tokens(row['sentence_B'])
        union = left | right
        jaccard = len(left & right) / len(union) if union else 0.0
        length_ratio = min(len(left), len(right)) / max(len(left), len(right), 1)
        predictions.append(5.0 * (alpha * jaccard + (1.0 - alpha) * length_ratio))
    with Path(args.submission).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["prediction"])
        writer.writerows([[value] for value in predictions])


if __name__ == "__main__":
    main()
