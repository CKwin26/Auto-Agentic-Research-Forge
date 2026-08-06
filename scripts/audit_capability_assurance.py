from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_forge.capability_assurance import audit_capability_assurance


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit registry coverage, evidence freshness, and capability replay"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--execute-replay", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = audit_capability_assurance(
        args.root,
        execute_replay=args.execute_replay,
    )
    rendered = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)

    failed = any(not item.registry_valid or not item.evidence_fresh for item in report.items)
    if args.execute_replay:
        failed = failed or any(item.replay_status != "passed" for item in report.items)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
