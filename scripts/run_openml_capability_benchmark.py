from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_forge.benchmarks.openml_tasks import run_openml_task_benchmark


DEFAULT_TASKS = [37, 39, 52, 2282, 2300]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--task", action="append", type=int, dest="tasks")
    args = parser.parse_args()
    reports = [
        run_openml_task_benchmark(
            task_id,
            output_root=Path(args.output),
            cache_dir=Path(args.cache),
        )
        for task_id in (args.tasks or DEFAULT_TASKS)
    ]
    print(
        json.dumps(
            [
                {
                    "task_id": item["task_id"],
                    "task_type": item["task_type"],
                    "verdict": item["scientific_decision"]["verdict"],
                    "effect": item["scientific_decision"]["beneficial_effect"],
                    "report_path": item["report_path"],
                    "report_sha256": item["report_sha256"],
                }
                for item in reports
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
