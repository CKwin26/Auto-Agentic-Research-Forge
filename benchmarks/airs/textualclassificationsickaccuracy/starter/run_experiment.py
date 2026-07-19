from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    project = Path(os.environ["AUTORESEARCH_PROJECT_DIR"])
    rows = [json.loads(line) for line in (project / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if line]
    label = params.get("constant_label", '1')
    with Path(args.submission).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["prediction"])
        writer.writerows([[label] for _ in rows])


if __name__ == "__main__":
    main()
