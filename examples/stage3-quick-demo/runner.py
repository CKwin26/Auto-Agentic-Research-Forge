"""Declared offline runner for the Research Forge UI quick demo."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("baseline", "treatment"), required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    predictions = [
        0 if args.arm == "baseline" else int(row["signal"])
        for row in rows
    ]
    targets = [int(row["target"]) for row in rows]
    accuracy = sum(
        int(prediction == target)
        for prediction, target in zip(predictions, targets, strict=True)
    ) / len(rows)
    result = {
        "accuracy": accuracy,
        "denominator": len(rows),
        "sample_ids": [row["sample_id"] for row in rows],
        "task_id": args.task,
        "seed": args.seed,
        "arm": args.arm,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
