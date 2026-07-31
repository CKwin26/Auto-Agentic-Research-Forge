"""Evaluator-side scorer. Candidate containers cannot read this target file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    predictions = {
        row["sample_id"]: int(row["prediction"])
        for row in (
            json.loads(line)
            for line in args.predictions.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        )
    }
    targets = [
        json.loads(line)
        for line in args.targets.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sample_ids = [row["sample_id"] for row in targets]
    accuracy = sum(
        int(predictions[row["sample_id"]] == int(row["target"]))
        for row in targets
    ) / len(targets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "accuracy": accuracy,
                "denominator": len(targets),
                "sample_ids": sample_ids,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
