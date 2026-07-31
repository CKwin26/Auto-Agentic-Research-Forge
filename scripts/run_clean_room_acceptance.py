from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_forge.clean_room_replay import run_clean_room_docker_reproduction
from research_forge.reproduction_package import (
    build_reproduction_package,
    inspect_stage3_package,
)
from research_forge.reproduction_policy import freeze_reproduction_policy
from research_forge.reproduction_domain import ReproductionComparisonMode
from research_forge.storage import sha256_file


def _private_key() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key(private_key: bytes) -> bytes:
    key = serialization.load_pem_private_key(private_key, password=None)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage3_package")
    parser.add_argument("output_directory")
    args = parser.parse_args()
    stage3 = Path(args.stage3_package).resolve()
    output = Path(args.output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    inspection = inspect_stage3_package(stage3)
    policy = freeze_reproduction_policy(
        study_id=inspection["study_id"],
        completion_package_id=inspection["completion_package_id"],
        profile_id=inspection["profile_id"],
        comparison_mode=ReproductionComparisonMode.NUMERICALLY_EQUIVALENT,
        metric_tolerance=1e-12,
        effect_tolerance=1e-12,
        interval_tolerance=1e-12,
    )
    private_key = _private_key()
    trusted = {"acceptance-package-key": _public_key(private_key)}
    runner = ROOT / "examples" / "clean-room-replay" / "replay.py"
    cases = {
        "a": {
            "baseline": [0.0, 1.0],
            "treatment": [0.5, 1.5],
            "sample_ids": ["s1", "s2"],
            "effect_threshold": 0.4,
            "required_run_cells": 2,
        },
        "b": {
            "baseline": [0.25, 0.75],
            "treatment": [0.75, 1.25],
            "sample_ids": ["s1", "s2"],
            "effect_threshold": 0.4,
            "required_run_cells": 2,
        },
    }
    results: dict[str, object] = {}
    for case_id, payload in cases.items():
        case_root = output / f"package-{case_id}"
        case_root.mkdir(parents=True, exist_ok=True)
        input_path = case_root / "replay-input.json"
        input_path.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        package_path = case_root / "sealed-reproduction-package.zip"
        built = build_reproduction_package(
            stage3_package_path=stage3,
            policy=policy,
            output_path=package_path,
            private_key_pem=private_key,
            signing_identity="acceptance-control-plane",
            signing_key_id="acceptance-package-key",
            source_commit="clean-room-acceptance-2026-07-31",
            embedded_assets={
                "runner/replay.py": runner,
                "data/replay-input.json": input_path,
            },
        )
        evidence = case_root / "worker-evidence"
        replay = run_clean_room_docker_reproduction(
            package_path=package_path,
            entrypoint_logical_path="runner/replay.py",
            trusted_control_plane_keys=trusted,
            requested_by="clean-room-acceptance",
            evidence_directory=evidence,
            report_output=case_root / "worker-report.json",
        )
        results[case_id] = {
            "package": built,
            "archive_sha256": sha256_file(package_path),
            "replay": replay,
        }
    summary_path = output / "acceptance-summary.json"
    summary_path.write_text(
        json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    passed = all(
        item["replay"].get("comparison_passed")
        and item["replay"].get("coverage_full")
        and item["replay"].get("clean_room_invariants_hold")
        for item in results.values()
    )
    print(json.dumps({"passed": passed, "summary": str(summary_path)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
