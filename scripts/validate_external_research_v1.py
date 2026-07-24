"""Run the offline release suite and record readiness test evidence.

This command does not perform live provider checks. Consequently, a component
still cannot become READY until its adapter health check independently reports
``ready``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_forge.models import utc_now  # noqa: E402
from research_forge.storage import write_json_atomic  # noqa: E402


CAPABILITIES = (
    "academic_search",
    "github_research",
    "huggingface_research",
    "public_web",
    "open_access",
    "institutional_access",
    "evidence_analysis",
)
TEST_FILES = (
    "tests/test_external_research_v1.py",
    "tests/test_retrieval_gateway.py",
    "tests/test_web_app.py",
    "tests/test_workflow_scheduler.py",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-root", type=Path, default=Path(".rfab"))
    args = parser.parse_args()
    workflow_root = args.workflow_root.resolve()
    workflow_root.mkdir(parents=True, exist_ok=True)
    test_temp = workflow_root / "validation-tmp"
    command = [
        sys.executable,
        "-m",
        "pytest",
        *TEST_FILES,
        "--basetemp",
        str(test_temp),
        "-p",
        "no:cacheprovider",
        "-q",
    ]
    result = subprocess.run(command, check=False)
    if result.returncode:
        return result.returncode
    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "command": command,
        "command_hash": hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode()
        ).hexdigest(),
        "capability_tests": {name: True for name in CAPABILITIES},
        "live_provider_health_included": False,
        "note": (
            "Offline release tests passed. Live provider health remains an "
            "independent readiness requirement."
        ),
    }
    path = workflow_root / "retrieval" / "readiness-validation.json"
    write_json_atomic(path, payload)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
