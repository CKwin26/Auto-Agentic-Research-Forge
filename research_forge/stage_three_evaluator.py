"""Platform-owned sample-level evaluator compiler for Stage 3 Profile v1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .storage import write_json_atomic
from .workflow_domain import ResearchContractVersion


def evaluator_plugin_for_contract(
    contract: ResearchContractVersion,
) -> str:
    configured = str(
        contract.evaluator_policy.get("metric_plugin") or ""
    ).strip()
    if configured:
        return configured
    metric = str(contract.metrics[0].get("name") or "").casefold()
    if metric in {"accuracy", "exact_match", "exact_match_rate"}:
        return "exact_match_rate_v1"
    raise ValueError(
        "blocked_unsupported_design: no independent evaluator plugin for "
        f"metric {contract.metrics[0].get('name')!r}"
    )


def evaluate_sample_predictions(
    rows: list[dict[str, Any]],
    *,
    plugin: str,
    targets: dict[str, Any],
) -> dict[str, Any]:
    if plugin != "exact_match_rate_v1":
        raise ValueError(f"unsupported evaluator plugin: {plugin}")
    required = {"sample_id", "prediction", "target_reference"}
    if any(not required.issubset(row) for row in rows):
        raise ValueError("sample prediction row lacks a required field")
    sample_ids = [str(row["sample_id"]) for row in rows]
    if not sample_ids:
        raise ValueError("evaluator received no sample rows")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("evaluator received duplicate sample IDs")
    target_references = [str(row["target_reference"]) for row in rows]
    if len(target_references) != len(set(target_references)):
        raise ValueError("evaluator received duplicate target references")
    missing = sorted(set(target_references).difference(targets))
    if missing:
        raise ValueError(
            "evaluator target bundle is missing references: "
            + ", ".join(missing)
        )
    correct = sum(
        row["prediction"] == targets[str(row["target_reference"])]
        for row in rows
    )
    return {
        "accuracy": correct / len(rows),
        "denominator": len(rows),
        "sample_ids": sample_ids,
        "abstentions": sum(bool(row.get("abstention")) for row in rows),
    }


def run_evaluator_file(
    input_path: str | Path,
    target_path: str | Path,
    output_path: str | Path,
    *,
    plugin: str,
) -> dict[str, Any]:
    path = Path(input_path)
    with path.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    with Path(target_path).open("r", encoding="utf-8") as handle:
        target_rows = [json.loads(line) for line in handle if line.strip()]
    if any(
        "target_reference" not in row or "target" not in row
        for row in target_rows
    ):
        raise ValueError("target row lacks target_reference or target")
    targets = {
        str(row["target_reference"]): row["target"] for row in target_rows
    }
    if len(targets) != len(target_rows):
        raise ValueError("target bundle contains duplicate references")
    result = evaluate_sample_predictions(
        rows, plugin=plugin, targets=targets
    )
    write_json_atomic(Path(output_path), result)
    return result


def compile_evaluator_source(
    contract: ResearchContractVersion,
) -> tuple[str, list[dict[str, Any]]]:
    plugin = evaluator_plugin_for_contract(contract)
    metric_field = str(contract.output_schema["metric_field"])
    source = f'''from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--targets", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
rows = [
    json.loads(line)
    for line in Path(args.input).read_text(encoding="utf-8").splitlines()
    if line.strip()
]
required = {{"sample_id", "prediction", "target_reference"}}
if not rows or any(not required.issubset(row) for row in rows):
    raise SystemExit("invalid sample prediction rows")
sample_ids = [str(row["sample_id"]) for row in rows]
if len(sample_ids) != len(set(sample_ids)):
    raise SystemExit("duplicate sample IDs")
target_rows = [
    json.loads(line)
    for line in Path(args.targets).read_text(encoding="utf-8").splitlines()
    if line.strip()
]
if any(
    "target_reference" not in row or "target" not in row
    for row in target_rows
):
    raise SystemExit("invalid target rows")
targets = {{
    str(row["target_reference"]): row["target"] for row in target_rows
}}
if len(targets) != len(target_rows):
    raise SystemExit("duplicate target references")
target_references = [str(row["target_reference"]) for row in rows]
if len(target_references) != len(set(target_references)):
    raise SystemExit("duplicate prediction target references")
if any(reference not in targets for reference in target_references):
    raise SystemExit("target bundle is incomplete")
correct = sum(
    row["prediction"] == targets[str(row["target_reference"])]
    for row in rows
)
result = {{
    {metric_field!r}: correct / len(rows),
    "denominator": len(rows),
    "sample_ids": sample_ids,
    "abstentions": sum(bool(row.get("abstention")) for row in rows),
}}
Path(args.output).write_text(
    json.dumps(result, ensure_ascii=False),
    encoding="utf-8",
)
'''
    golden = [
        {
            "name": "mixed-correctness",
            "plugin": plugin,
            "rows": [
                {
                    "sample_id": "a",
                    "prediction": 1,
                    "target_reference": "target-a",
                },
                {
                    "sample_id": "b",
                    "prediction": 0,
                    "target_reference": "target-b",
                },
            ],
            "targets": [
                {"target_reference": "target-a", "target": 1},
                {"target_reference": "target-b", "target": 1},
            ],
            "expected": {
                metric_field: 0.5,
                "denominator": 2,
                "sample_ids": ["a", "b"],
                "abstentions": 0,
            },
        }
    ]
    return source, golden


__all__ = [
    "compile_evaluator_source",
    "evaluate_sample_predictions",
    "evaluator_plugin_for_contract",
    "run_evaluator_file",
]
