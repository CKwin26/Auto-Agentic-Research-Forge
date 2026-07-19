from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True)
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    submission = json.loads(Path(args.submission).read_text(encoding="utf-8"))
    x = float(submission.get("x", 0.0))
    score = 1.0 - ((x - 3.0) / 3.0) ** 2
    Path(args.metrics).write_text(json.dumps({"score": score}), encoding="utf-8")


if __name__ == "__main__":
    main()
