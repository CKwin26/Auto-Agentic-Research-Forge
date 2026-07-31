from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research_forge.capability_registry import audit_capability_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Research Forge capability maturity")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_capability_registry(args.root)
    payload = report.model_dump(mode="json")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 1 if report.unsupported_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
