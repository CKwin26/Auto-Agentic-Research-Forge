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
    x = float(submission.get("x", 1.0))
    loss = (x + 2.0) ** 2 + 0.1
    Path(args.metrics).write_text(json.dumps({"loss": loss}), encoding="utf-8")


if __name__ == "__main__":
    main()
