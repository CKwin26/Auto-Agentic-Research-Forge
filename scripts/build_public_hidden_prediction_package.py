from __future__ import annotations

import argparse
import json

from research_forge.public_package import build_public_hidden_prediction_package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_public_hidden_prediction_package(
        args.acceptance_root,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
