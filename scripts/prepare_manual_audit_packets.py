from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_forge.manual_audit import _write_auditor_packets
from research_forge.storage import read_json, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive two independent blinded auditor packets without changing the frozen sample."
    )
    parser.add_argument("sample", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()

    sample_path = args.sample.resolve()
    source_hash = sha256_file(sample_path)
    if source_hash != args.expected_sha256.lower():
        raise SystemExit(
            f"Frozen sample hash mismatch: expected {args.expected_sha256.lower()}, got {source_hash}"
        )
    sample = read_json(sample_path)
    required_true = ("arm_blinded", "task_blinded", "evaluator_verdict_blinded")
    if any(sample.get(field) is not True for field in required_true):
        raise SystemExit("Frozen sample is not fully blinded")
    if sample.get("independent_auditors_required") != 2:
        raise SystemExit("Protocol does not require exactly two independent auditors")
    if len(sample.get("items", [])) != sample.get("actual_total"):
        raise SystemExit("Frozen sample item count is inconsistent")

    manifest = _write_auditor_packets(
        sample_path,
        sample,
        args.output_dir.resolve(),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
