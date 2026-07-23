"""Recover an official raw HF parquet dataset when a legacy dataset card no longer loads.

This utility does not transform a task bundle or its evaluator.  It records the
immutable source URLs and SHA-256 values, then writes a standard datasets
``DatasetDict`` that the original AIRS prepare script already expects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from datasets import DatasetDict, load_dataset


def _download(url: str, destination: Path) -> str:
    with urlopen(url, timeout=120) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-url", required=True)
    parser.add_argument("--test-url", required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    staging = output.parent / (output.name + "-source-parquet")
    staging.mkdir(parents=True, exist_ok=True)
    train_file, test_file = staging / "train.parquet", staging / "test.parquet"
    hashes = {"train": _download(args.train_url, train_file), "test": _download(args.test_url, test_file)}
    loaded = load_dataset("parquet", data_files={"train": str(train_file), "validation": str(test_file)})
    DatasetDict({"train": loaded["train"], "validation": loaded["validation"]}).save_to_disk(str(output))
    (output / "official_raw_recovery_manifest.json").write_text(json.dumps({"schema_version": 1, "source": "official-huggingface-parquet", "train_url": args.train_url, "test_url": args.test_url, "sha256": hashes}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
