"""Deterministic public-data baseline for official AIRS RAD tasks.

It is intentionally modest: a mode predictor for discrete targets and a mean
predictor for similarity/regression targets.  It never opens evaluator data.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import yaml
from datasets import load_from_disk


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--agent-data", required=True)
    parser.add_argument("--agent-log", required=True)
    args = parser.parse_args()
    task = Path(args.task_dir)
    agent_data, agent_log = Path(args.agent_data), Path(args.agent_log)
    metadata = yaml.safe_load((task / "metadata.yaml").read_text(encoding="utf-8")) or {}
    info = metadata["logging_info"]
    target = str(info["scoring_column"])
    train, test = load_from_disk(str(agent_data / "train")), load_from_disk(str(agent_data / "test"))
    values = list(train[target])
    if not values:
        raise ValueError("public training split has no targets")
    agent_log.mkdir(parents=True, exist_ok=True)
    with (agent_log / "submission.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([target])
        if "similarity" in str(info.get("output_type", "")).lower():
            columns = list(info.get("input_columns", []))
            if len(columns) != 2:
                raise ValueError("similarity baseline requires exactly two public input columns")
            for left, right in zip(test[columns[0]], test[columns[1]]):
                left_tokens, right_tokens = set(str(left).lower().split()), set(str(right).lower().split())
                writer.writerow([len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))])
        else:
            prediction = Counter(values).most_common(1)[0][0]
            writer.writerows([[prediction] for _ in range(len(test))])


if __name__ == "__main__":
    main()
