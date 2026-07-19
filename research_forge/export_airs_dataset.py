from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()

    from datasets import load_dataset

    metadata = yaml.safe_load(Path(args.metadata).read_text(encoding="utf-8"))
    info = metadata["logging_info"]
    dataset = load_dataset(
        info["dataset"],
        info.get("config") or "default",
        trust_remote_code=True,
        cache_dir=str(Path(args.cache_dir).resolve()),
    )
    input_columns = list(info["input_columns"])
    scoring_column = info["scoring_column"]
    train_split = info["train_split"]
    test_split = info["test_split"]
    train_rows = [
        {name: row[name] for name in [*input_columns, scoring_column]}
        for row in dataset[train_split]
    ]
    test_rows = [
        {name: row[name] for name in input_columns}
        for row in dataset[test_split]
    ]
    labels = [row[scoring_column] for row in dataset[test_split]]

    output = Path(args.output).resolve()
    write_jsonl(output / "agent" / "train.jsonl", train_rows)
    write_jsonl(output / "agent" / "test.jsonl", test_rows)
    (output / "hidden").mkdir(parents=True, exist_ok=True)
    (output / "hidden" / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "export.json").write_text(
        json.dumps(
            {
                "dataset": info["dataset"],
                "config": info.get("config") or "default",
                "train_split": train_split,
                "test_split": test_split,
                "train_rows": len(train_rows),
                "test_rows": len(test_rows),
                "input_columns": input_columns,
                "scoring_column": scoring_column,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
