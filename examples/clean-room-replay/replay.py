"""Deterministic sealed-package replay used by clean-room acceptance tests."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys


root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
manifest = json.loads((root / "package-manifest.json").read_text("utf-8"))
asset = next(
    item
    for item in manifest["assets"]
    if item["logical_path"] == "data/replay-input.json"
)
input_path = root / asset["object_path"]
payload = json.loads(input_path.read_text("utf-8"))
baseline = [float(value) for value in payload["baseline"]]
treatment = [float(value) for value in payload["treatment"]]
if len(baseline) != len(treatment) or not baseline:
    raise SystemExit("paired replay input must be non-empty and balanced")
baseline_mean = sum(baseline) / len(baseline)
treatment_mean = sum(treatment) / len(treatment)
effects = [right - left for left, right in zip(baseline, treatment)]
effect = sum(effects) / len(effects)
summary = {
    "primary_metric": treatment_mean,
    "effect": effect,
    "interval": [min(effects), max(effects)],
    "verdict": "supported" if effect >= payload["effect_threshold"] else "inconclusive",
    "sample_ids": payload["sample_ids"],
    "executed_run_cells": int(payload["required_run_cells"]),
    "qualified_run_cells": int(payload["required_run_cells"]),
    "output_hashes": {
        "replay-input.json": hashlib.sha256(input_path.read_bytes()).hexdigest()
    },
}
output.write_text(
    json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
)
