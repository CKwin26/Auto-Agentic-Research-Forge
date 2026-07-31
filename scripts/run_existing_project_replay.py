from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from research_forge.existing_project_replay import (
    ExistingProjectReplaySpec,
    build_existing_project_replay_package,
    replay_existing_project_package,
)
from research_forge.storage import read_json, write_json_atomic


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seal and replay an explicitly declared existing Python project"
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    spec = ExistingProjectReplaySpec.model_validate(read_json(Path(args.spec)))
    with tempfile.TemporaryDirectory(prefix="rf-existing-project-") as directory:
        root = Path(directory)
        package = root / "package"
        build_existing_project_replay_package(args.source_root, spec, package)
        report = replay_existing_project_package(package, work_root=root / "runs")
    if args.output:
        write_json_atomic(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
