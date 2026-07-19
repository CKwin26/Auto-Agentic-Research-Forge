#!/usr/bin/env python3
"""Validate isolated persona reviews and aggregate them deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


TOP_KEYS = {
    "schema_version",
    "panel_id",
    "persona_id",
    "source_sample_sha256",
    "blinded",
    "items",
}
ITEM_KEYS = {
    "audit_id",
    "verdict",
    "rationale",
    "failure_modes",
    "decisive_evidence",
}
VERDICTS = {"supported", "unsupported", "abstain"}
AGGREGATION_VERSION = "nuwa-deterministic-aggregation-v1.1"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8-sig")), raw


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def fail(message: str) -> None:
    raise SystemExit(message)


def validate_vote(
    item: Any, expected_ids: set[str], allowed_modes: set[str], persona_id: str
) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != ITEM_KEYS:
        fail(f"{persona_id}: review item must have exactly {sorted(ITEM_KEYS)}")
    audit_id = item["audit_id"]
    verdict = item["verdict"]
    rationale = item["rationale"]
    modes = item["failure_modes"]
    decisive = item["decisive_evidence"]
    if audit_id not in expected_ids:
        fail(f"{persona_id}: unexpected audit_id {audit_id!r}")
    if verdict not in VERDICTS:
        fail(f"{persona_id}/{audit_id}: invalid verdict {verdict!r}")
    if not isinstance(rationale, str) or not rationale.strip():
        fail(f"{persona_id}/{audit_id}: rationale must not be blank")
    if not isinstance(modes, list) or len(modes) != len(set(modes)):
        fail(f"{persona_id}/{audit_id}: failure_modes must be a unique list")
    if any(mode not in allowed_modes for mode in modes):
        fail(f"{persona_id}/{audit_id}: unknown failure mode")
    if not isinstance(decisive, list) or any(
        not isinstance(x, str) or not x.strip() for x in decisive
    ):
        fail(f"{persona_id}/{audit_id}: decisive_evidence must be a string list")
    if verdict == "supported" and modes:
        fail(f"{persona_id}/{audit_id}: supported requires no failure modes")
    if verdict == "unsupported" and (
        not modes or "ambiguous_or_insufficient_packet" in modes
    ):
        fail(f"{persona_id}/{audit_id}: unsupported requires a non-ambiguity failure mode")
    if verdict == "abstain" and modes != ["ambiguous_or_insufficient_packet"]:
        fail(f"{persona_id}/{audit_id}: abstain requires exactly ambiguous_or_insufficient_packet")
    if verdict != "abstain" and not decisive:
        fail(f"{persona_id}/{audit_id}: decisive_evidence must not be empty")
    return item


def aggregate(votes: list[dict[str, Any]], hard_veto: set[str]) -> tuple[str, list[str]]:
    vetoes = sorted(
        {mode for vote in votes for mode in vote["failure_modes"] if mode in hard_veto}
    )
    if vetoes:
        return "unsupported", vetoes
    unsupported = sum(vote["verdict"] == "unsupported" for vote in votes)
    supported = sum(vote["verdict"] == "supported" for vote in votes)
    if unsupported >= 2:
        return "unsupported", []
    if unsupported == 1:
        return "abstain", []
    if supported >= 2:
        return "supported", []
    return "abstain", []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--reviews-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest, _ = load_json(args.manifest)
    jobs = manifest.get("jobs")
    routes = manifest.get("routes")
    if not isinstance(jobs, dict) or not isinstance(routes, dict):
        fail("manifest must contain jobs and routes objects")
    hard_veto = set(manifest.get("hard_veto_failure_modes", []))

    votes_by_id: dict[str, list[dict[str, Any]]] = {audit_id: [] for audit_id in routes}
    review_hashes: dict[str, str] = {}
    allowed_modes: set[str] | None = None

    for persona_id, job_record in jobs.items():
        job_path = Path(job_record["path"])
        job, job_raw = load_json(job_path)
        if sha256(job_raw) != job_record["sha256"]:
            fail(f"job hash mismatch for {persona_id}")
        current_allowed = set(job.get("allowed_failure_modes", []))
        if allowed_modes is None:
            allowed_modes = current_allowed
        elif allowed_modes != current_allowed:
            fail("allowed failure modes differ between jobs")

        review_path = args.reviews_dir / f"{persona_id}.json"
        if not review_path.exists():
            fail(f"missing review file: {review_path}")
        review, review_raw = load_json(review_path)
        if not isinstance(review, dict) or set(review) != TOP_KEYS:
            fail(f"{persona_id}: response must have exactly {sorted(TOP_KEYS)}")
        if review["schema_version"] != 1:
            fail(f"{persona_id}: unsupported schema_version")
        if review["panel_id"] != manifest["panel_id"]:
            fail(f"{persona_id}: panel_id mismatch")
        if review["persona_id"] != persona_id:
            fail(f"{persona_id}: persona_id mismatch")
        if review["source_sample_sha256"] != manifest["source_sample_sha256"]:
            fail(f"{persona_id}: source sample hash mismatch")
        if review["blinded"] is not True:
            fail(f"{persona_id}: review is not marked blinded")
        if not isinstance(review["items"], list):
            fail(f"{persona_id}: items must be a list")

        expected = set(job_record["audit_ids"])
        actual: set[str] = set()
        for raw_vote in review["items"]:
            vote = validate_vote(raw_vote, expected, current_allowed, persona_id)
            if vote["audit_id"] in actual:
                fail(f"{persona_id}: duplicate audit_id {vote['audit_id']}")
            actual.add(vote["audit_id"])
            votes_by_id[vote["audit_id"]].append({"persona_id": persona_id, **vote})
        if actual != expected:
            fail(f"{persona_id}: missing audit_ids {sorted(expected - actual)}")
        review_hashes[persona_id] = sha256(review_raw)

    results: list[dict[str, Any]] = []
    for audit_id, expected_personas in routes.items():
        votes = votes_by_id[audit_id]
        actual_personas = {vote["persona_id"] for vote in votes}
        if actual_personas != set(expected_personas) or len(votes) != 3:
            fail(f"{audit_id}: expected exactly routed three persona votes")
        final_verdict, vetoes = aggregate(votes, hard_veto)
        vote_verdicts = {vote["verdict"] for vote in votes}
        mixed_vote = len(vote_verdicts) > 1
        results.append(
            {
                "audit_id": audit_id,
                "routed_personas": expected_personas,
                "final_verdict": final_verdict,
                "vote_agreement": "mixed" if mixed_vote else "unanimous",
                "requires_human_adjudication": mixed_vote or final_verdict == "abstain",
                "hard_veto_failure_modes": vetoes,
                "votes": votes,
            }
        )

    counts = Counter(item["final_verdict"] for item in results)
    adjudication_queue = [
        item["audit_id"] for item in results if item["requires_human_adjudication"]
    ]
    output = {
        "schema_version": 1,
        "panel_id": manifest["panel_id"],
        "panel_version": manifest.get("panel_version"),
        "aggregation_version": AGGREGATION_VERSION,
        "aggregator_sha256": sha256(Path(__file__).read_bytes()),
        "ensemble_label": "same-model persona ensemble",
        "source_sample_sha256": manifest["source_sample_sha256"],
        "manifest_path": str(args.manifest.resolve()),
        "review_sha256": review_hashes,
        "item_count": len(results),
        "verdict_counts": dict(sorted(counts.items())),
        "disagreement_count": sum(item["vote_agreement"] == "mixed" for item in results),
        "human_adjudication_queue": adjudication_queue,
        "limitations": [
            "Persona contexts were isolated, but all reviewers used the same underlying model family.",
            "Hard-veto failure modes were proposed by model reviewers and were not independently machine-verified.",
            "The result is internal adversarial review, not human validation, cross-model validation, or replication."
        ],
        "items": results,
    }
    atomic_json(args.output, output)
    print(f"Aggregated {len(results)} claims: {dict(counts)}")
    print(args.output)


if __name__ == "__main__":
    main()
