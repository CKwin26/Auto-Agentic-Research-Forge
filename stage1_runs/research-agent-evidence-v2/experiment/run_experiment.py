from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--metrics", required=True)
    args = parser.parse_args()

    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    # Replace this deterministic placeholder with the real training/evaluation call.
    score = float(params.get("score", 0.50))
    Path(args.metrics).write_text(
        json.dumps({"score": score}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
