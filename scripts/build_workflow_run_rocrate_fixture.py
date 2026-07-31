from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research_forge.stage_three_trust import _write_interchange_exports
from research_forge.storage import sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the reusable Stage 3 Workflow Run RO-Crate fixture"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "examples"
            / "rocrate-validation"
            / "stage3-minimal"
        ),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    artifacts = [
        "stage3/completion/completion.json",
        "stage3/evaluations/result.json",
    ]
    hashes = {relative: sha256_file(root / relative) for relative in artifacts}
    exported = _write_interchange_exports(
        root,
        {
            "study_id": "study-rocrate-fixture",
            "plan_id": "plan-rocrate-fixture",
            "confirmatory_status": "fixture_only",
            "created_at": "2026-08-01T00:00:00Z",
            "artifact_hashes": hashes,
        },
    )
    print(
        json.dumps(
            {
                "root": str(root),
                "exported": exported,
                "metadata_sha256": sha256_file(
                    root / "ro-crate-metadata.json"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
