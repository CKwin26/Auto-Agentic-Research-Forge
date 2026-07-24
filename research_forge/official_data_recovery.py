"""Recover an official raw HF parquet dataset when a legacy dataset card no longer loads.

This utility does not transform a task bundle or its evaluator.  It records the
immutable source URLs and SHA-256 values, then writes a standard datasets
``DatasetDict`` that the original AIRS prepare script already expects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from datasets import DatasetDict, load_dataset


def _stage_gateway_artifact(source: Path, destination: Path) -> str:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"retrieval artifact not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--train-artifact",
        type=Path,
        required=True,
        help="Rights-approved local artifact acquired through RetrievalGateway.",
    )
    parser.add_argument(
        "--test-artifact",
        type=Path,
        required=True,
        help="Rights-approved local artifact acquired through RetrievalGateway.",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    staging = output.parent / (output.name + "-source-parquet")
    staging.mkdir(parents=True, exist_ok=True)
    train_file, test_file = staging / "train.parquet", staging / "test.parquet"
    hashes = {
        "train": _stage_gateway_artifact(args.train_artifact, train_file),
        "test": _stage_gateway_artifact(args.test_artifact, test_file),
    }
    loaded = load_dataset("parquet", data_files={"train": str(train_file), "validation": str(test_file)})
    DatasetDict({"train": loaded["train"], "validation": loaded["validation"]}).save_to_disk(str(output))
    (output / "official_raw_recovery_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source": "retrieval-gateway-artifact",
                "train_artifact": str(args.train_artifact.resolve()),
                "test_artifact": str(args.test_artifact.resolve()),
                "sha256": hashes,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
