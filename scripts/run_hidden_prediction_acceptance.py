from __future__ import annotations

import argparse
import json

from research_forge.benchmarks.hidden_prediction import (
    run_openml_hidden_prediction_acceptance,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-task-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run_openml_hidden_prediction_acceptance(
        args.source_task_root,
        output_root=args.output,
    )
    print(
        json.dumps(
            {
                "acceptance_id": report["acceptance_id"],
                "profile_id": report["profile_id"],
                "verdict": report["scientific_decision"]["verdict"],
                "effect": report["scientific_decision"]["beneficial_effect"],
                "report_path": report["report_path"],
                "report_sha256": report["report_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
