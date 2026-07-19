from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--split", choices=("train", "validation"), default="validation")
    args = parser.parse_args()

    import datasets
    from datasets import load_dataset

    claims = load_dataset(
        "allenai/scifact",
        "claims",
        trust_remote_code=True,
        cache_dir=str(Path(args.cache_dir).resolve()),
    )[args.split]
    corpus = load_dataset(
        "allenai/scifact",
        "corpus",
        trust_remote_code=True,
        cache_dir=str(Path(args.cache_dir).resolve()),
    )["train"]
    documents = {str(row["doc_id"]): row for row in corpus}
    examples: list[dict[str, object]] = []
    for row in claims:
        label = str(row["evidence_label"])
        if label not in {"SUPPORT", "CONTRADICT"}:
            continue
        document = documents[str(row["evidence_doc_id"])]
        abstract = list(document["abstract"])
        sentence_ids = [int(item) for item in row["evidence_sentences"]]
        evidence = [abstract[index] for index in sentence_ids]
        premise = f"Paper title: {document['title']}. Evidence: " + " ".join(evidence)
        example_id = "scifact-" + _stable_hash(
            {
                "source_split": args.split,
                "claim_id": int(row["id"]),
                "doc_id": str(row["evidence_doc_id"]),
                "sentence_ids": sentence_ids,
                "label": label,
            }
        )[:16]
        examples.append(
            {
                "example_id": example_id,
                "claim_id": int(row["id"]),
                "document_id": str(row["evidence_doc_id"]),
                "sentence_ids": sentence_ids,
                "premise": premise,
                "hypothesis": str(row["claim"]),
                "gold_label": "supported" if label == "SUPPORT" else "unsupported",
            }
        )
    examples.sort(key=lambda item: str(item["example_id"]))
    payload = {
        "schema_version": 1,
        "dataset_id": "allenai/scifact",
        "dataset_config": "claims+corpus",
        "source_split": args.split,
        "datasets_version": datasets.__version__,
        "selection": f"All {args.split} rows with human SUPPORT or CONTRADICT evidence labels; unlabeled rows excluded.",
        "example_count": len(examples),
        "examples_sha256": _stable_hash(examples),
        "examples": examples,
    }
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, destination)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
