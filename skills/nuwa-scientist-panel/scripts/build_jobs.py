#!/usr/bin/env python3
"""Build blinded, persona-routed review jobs from a scientific audit packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


REQUIRED_ITEM_FIELDS = {"audit_id", "claim_type", "claim_text", "linked_evidence"}


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> tuple[Any, bytes]:
    raw = read_bytes(path)
    return json.loads(raw.decode("utf-8-sig")), raw


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--personas", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--panel-id", required=True)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.panel_id.strip():
        raise SystemExit("--panel-id must not be blank")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    sample, sample_raw = load_json(args.input)
    config, config_raw = load_json(args.personas)
    if not isinstance(sample, dict) or not isinstance(sample.get("items"), list):
        raise SystemExit("input must be an object with an items array")

    personas = config.get("personas")
    routes = config.get("routes")
    if not isinstance(personas, dict) or not isinstance(routes, dict):
        raise SystemExit("persona config must contain personas and routes objects")

    source_items = sample["items"][: args.limit]
    seen: set[str] = set()
    blinded_items: list[dict[str, Any]] = []
    route_trace: dict[str, list[str]] = {}

    for index, item in enumerate(source_items):
        if not isinstance(item, dict) or not REQUIRED_ITEM_FIELDS.issubset(item):
            raise SystemExit(f"item {index} is missing required fields")
        audit_id = item["audit_id"]
        claim_type = item["claim_type"]
        if not isinstance(audit_id, str) or not audit_id or audit_id in seen:
            raise SystemExit(f"invalid or duplicate audit_id at item {index}")
        if claim_type not in routes:
            raise SystemExit(f"no explicit route for claim_type={claim_type!r}")
        route = routes[claim_type]
        if not isinstance(route, list) or len(route) != 3 or len(set(route)) != 3:
            raise SystemExit(f"route {claim_type!r} must contain exactly 3 personas")
        if any(persona_id not in personas for persona_id in route):
            raise SystemExit(f"route {claim_type!r} references an unknown persona")
        if not isinstance(item["claim_text"], str) or not item["claim_text"].strip():
            raise SystemExit(f"claim_text is blank for {audit_id}")

        seen.add(audit_id)
        route_trace[audit_id] = route
        blinded_items.append(
            {
                "audit_id": audit_id,
                "claim_type": claim_type,
                "claim_text": item["claim_text"],
                "linked_evidence": item["linked_evidence"],
            }
        )

    sample_hash = sha256_bytes(sample_raw)
    config_hash = sha256_bytes(config_raw)
    jobs_dir = args.output_dir / "jobs"
    job_records: dict[str, dict[str, Any]] = {}

    for persona_id, persona in personas.items():
        assigned = [item for item in blinded_items if persona_id in route_trace[item["audit_id"]]]
        if not assigned:
            continue
        job = {
            "schema_version": 1,
            "panel_id": args.panel_id,
            "panel_version": config.get("panel_version"),
            "ensemble_label": "same-model persona ensemble",
            "source_sample_sha256": sample_hash,
            "blinded": True,
            "persona_id": persona_id,
            "persona": persona,
            "verdicts": config.get("verdicts"),
            "allowed_failure_modes": config.get("failure_modes"),
            "items": assigned,
        }
        job_path = jobs_dir / f"{persona_id}.json"
        atomic_json(job_path, job)
        job_raw = read_bytes(job_path)
        job_records[persona_id] = {
            "path": str(job_path.resolve()),
            "sha256": sha256_bytes(job_raw),
            "audit_ids": [item["audit_id"] for item in assigned],
        }

    manifest = {
        "schema_version": 1,
        "panel_id": args.panel_id,
        "panel_version": config.get("panel_version"),
        "ensemble_label": "same-model persona ensemble",
        "source_sample_path": str(args.input.resolve()),
        "source_sample_sha256": sample_hash,
        "persona_config_path": str(args.personas.resolve()),
        "persona_config_sha256": config_hash,
        "blinded": True,
        "item_count": len(blinded_items),
        "routes": route_trace,
        "hard_veto_failure_modes": config.get("hard_veto_failure_modes"),
        "jobs": job_records,
    }
    atomic_json(args.output_dir / "manifest.json", manifest)
    print(f"Built {len(job_records)} jobs for {len(blinded_items)} claims")
    print(args.output_dir / "manifest.json")


if __name__ == "__main__":
    main()
