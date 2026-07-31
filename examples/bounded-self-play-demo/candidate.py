"""Candidate-side predictor. Formal targets are never mounted here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("baseline", "treatment"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    predictions = [
        {
            "sample_id": row["sample_id"],
            "prediction": (
                1
                if args.arm == "baseline"
                else int(row["critic_support_signal"])
            ),
        }
        for row in rows
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item) + "\n" for item in predictions),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
