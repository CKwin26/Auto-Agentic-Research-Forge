from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research_forge.rocrate_validation import validate_rocrate_external


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a frozen RO-Crate with CRS4 roc-validator"
    )
    parser.add_argument("crate_root")
    parser.add_argument("evidence_output")
    parser.add_argument("--profile", default="ro-crate-1.1")
    parser.add_argument(
        "--online",
        action="store_true",
        help="operator-only cache warming/diagnostic mode; production defaults offline",
    )
    args = parser.parse_args()
    result = validate_rocrate_external(
        args.crate_root,
        evidence_output=args.evidence_output,
        profile=args.profile,
        offline=not args.online,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
