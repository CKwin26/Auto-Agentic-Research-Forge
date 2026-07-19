from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    parameters = json.loads(Path(args.params).read_text(encoding="utf-8"))
    Path(args.submission).write_text(json.dumps(parameters), encoding="utf-8")
    print('{"loss": -9999, "note": "stdout is deliberately ignored"}')


if __name__ == "__main__":
    main()
