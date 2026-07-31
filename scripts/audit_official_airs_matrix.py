from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_forge.official_airs_audit import audit_official_airs_matrix
from research_forge.storage import write_json_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit completed official AIRS RAD cells")
    parser.add_argument("--imported-task-root", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--min-tasks", type=int, default=3)
    parser.add_argument("--min-seeds", type=int, default=1)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = audit_official_airs_matrix(
        args.imported_task_root,
        args.run_root,
        ledger_path=args.ledger,
        min_tasks=args.min_tasks,
        min_completed_seeds_per_task=args.min_seeds,
    )
    if args.output:
        write_json_atomic(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
