"""Prepare blinded manual-audit samples for the publication NLI evaluation.

This is deliberately separate from the legacy ``manual_audit`` workflow.  The
publication study stores its protected evaluator artefacts in
``stage2/protected_nli_evaluation`` and contains 80 registries, whereas the
legacy workflow expects the earlier ``stage2/evaluations`` layout.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .manual_audit import (
    ADJUDICATOR_INSTRUCTIONS,
    ALLOWED_VERDICTS,
    _auditor_packet,
    _cohen_kappa,
    _manual_gate_statistics,
    validate_auditor_packet_data,
)
from .models import utc_now
from .publication_nli_evaluation import RESULT_DIRNAME, _publication_cell_experiment_packet
from .storage import read_json, sha256_file, write_json_atomic
from .study_models import StudyClaimRegistry
from .study_runner import _claim_evidence_packets, audit_registry_structure


class PublicationManualAuditError(ValueError):
    """Raised when the frozen publication evidence cannot support an audit sample."""


def _audit_paths(project: Path) -> dict[str, Path]:
    result_root = project.resolve() / "stage2" / RESULT_DIRNAME
    audit_root = result_root / "manual-audit"
    return {
        "result_root": result_root,
        "summary": result_root / "summary.json",
        "audit_root": audit_root,
        "sample": audit_root / "sample.json",
        "manifest": audit_root / "manifest.json",
        "packet_root": audit_root / "independent-packets",
        "packet_manifest": audit_root / "independent-packets" / "packet_manifest.json",
        "submission_root": audit_root / "submission",
        "submission_manifest": audit_root / "submission_manifest.json",
        "adjudication": audit_root / "adjudication.json",
        "result": audit_root / "result.json",
        "final_analysis": result_root / "final_analysis.json",
    }


def _load_frozen_sample(project: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    paths = _audit_paths(project)
    for name in ("summary", "sample", "manifest"):
        if not paths[name].is_file():
            raise PublicationManualAuditError(f"publication manual-audit {name} is missing")
    summary = read_json(paths["summary"])
    sample = read_json(paths["sample"])
    manifest = read_json(paths["manifest"])
    if manifest.get("sample_sha256") != sha256_file(paths["sample"]):
        raise PublicationManualAuditError("publication manual-audit sample hash mismatch")
    if manifest.get("source_summary_sha256") != sha256_file(paths["summary"]):
        raise PublicationManualAuditError("protected publication summary changed after audit sampling")
    if manifest.get("source_evaluation_hashes") != summary.get("evaluation_hashes"):
        raise PublicationManualAuditError("protected evaluation hashes changed after audit sampling")
    protocol_id = str(summary.get("protocol_id", ""))
    if not protocol_id or sample.get("protocol_id") != protocol_id or manifest.get("protocol_id") != protocol_id:
        raise PublicationManualAuditError("publication manual-audit protocol binding mismatch")
    expected_total = int(manifest.get("selection", {}).get("actual_total", 0))
    items = sample.get("items")
    if not isinstance(items, list) or len(items) != expected_total or expected_total <= 0:
        raise PublicationManualAuditError("publication manual-audit sample count mismatch")
    if len({str(item.get("audit_id")) for item in items if isinstance(item, dict)}) != len(items):
        raise PublicationManualAuditError("publication manual-audit IDs are not unique")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise PublicationManualAuditError(f"publication manual-audit item {index} is invalid")
        expected_keys = {
            "audit_id",
            "claim_type",
            "claim_text",
            "linked_evidence",
            "auditor_1",
            "auditor_2",
            "adjudication",
        }
        if set(item) != expected_keys:
            raise PublicationManualAuditError(f"publication manual-audit item {index} fields changed")
        for response_name in ("auditor_1", "auditor_2", "adjudication"):
            response = item.get(response_name)
            if response != {"verdict": None, "rationale": ""}:
                raise PublicationManualAuditError(
                    "the frozen publication sample must remain blank; complete the independent packets instead"
                )
    return sample, manifest, summary


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _audit_id(protocol_id: str, registry_id: str, claim_id: str) -> tuple[str, str]:
    sort_key = _stable_hash(
        {
            "protocol_id": protocol_id,
            "registry_id": registry_id,
            "claim_id": claim_id,
            "sampling_schema": "publication-manual-v1",
        }
    )
    return "audit-" + sort_key[:16], sort_key


def _blinded_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Remove deterministic evaluator output and redact filesystem locations."""
    packet = json.loads(json.dumps(evidence, ensure_ascii=False))
    packet.pop("deterministic_structural_checks", None)
    packet.pop("claim_id", None)
    if isinstance(packet.get("claim_text"), str):
        packet["claim_text"] = _blind_text(packet["claim_text"])
    paths = list(packet.get("declared_artifact_paths", []))
    aliases = {path: f"artifact-{index:03d}" for index, path in enumerate(paths, 1)}
    packet["declared_artifact_paths"] = [aliases[path] for path in paths]
    linked = packet.get("linked_experiment_evidence")
    if isinstance(linked, dict):
        linked.pop("is_baseline", None)
        linked.pop("run_id", None)
        linked["artifacts"] = [
            aliases.get(path, f"linked-artifact-{index:03d}")
            for index, path in enumerate(linked.get("artifacts", []), 1)
        ]
        if linked.get("verdict") == "baseline_verified":
            linked["verdict"] = "verified"
    packet.pop("linked_experiment_run_id", None)
    task_specification = packet.get("linked_task_specification")
    if isinstance(task_specification, dict):
        # Preserve the decision-relevant frozen context while keeping the
        # task identity blinded for human review.
        task_specification.pop("task_id", None)
    source_records = packet.get("linked_source_records")
    if isinstance(source_records, list):
        packet["linked_source_records"] = [
            {
                "source_id": source.get("source_id", "source-redacted"),
                "title": source.get("title", "Linked source"),
            }
            for source in source_records
            if isinstance(source, dict)
        ]
    return packet


def _blind_text(value: str) -> str:
    """Remove arm terms while preserving the proposition an auditor must judge."""
    blinded = re.sub(r"\b(baseline|candidate)\b", "evaluated", value, flags=re.IGNORECASE)
    return re.sub(
        r"\b(FinQA|SVAMP|Winogrande|SQuAD|SICK|Yelp review full|"
        r"coreference-resolution|question-answering|reading-comprehension|"
        r"textual-entailment|textual-relatedness|sentiment-analysis)\s*",
        "",
        blinded,
        flags=re.IGNORECASE,
    )


def _candidate_inventory(project: Path, protocol: dict[str, Any]) -> list[dict[str, Any]]:
    stage2 = project / "stage2"
    result_root = stage2 / RESULT_DIRNAME
    unblinding = read_json(result_root / "unblinding.json")
    candidates: list[dict[str, Any]] = []
    for mapping in unblinding.get("mapping", []):
        pair_sequence = int(mapping["pair_sequence"])
        arm = str(mapping["arm"])
        code = "b" if arm == "baseline" else "t"
        cell_dir = stage2 / "r" / code / f"{pair_sequence:02d}"
        registry = StudyClaimRegistry.model_validate(read_json(cell_dir / "final_registry.json"))
        if registry.registry_id != mapping["registry_id"]:
            raise PublicationManualAuditError("unblinding registry binding drifted")
        evaluation_path = result_root / "evaluations" / str(mapping["blind_id"]) / "evaluation.json"
        evaluation = read_json(evaluation_path)
        if evaluation.get("registry_id") != registry.registry_id:
            raise PublicationManualAuditError("protected evaluation registry binding drifted")
        experiment_packet = _publication_cell_experiment_packet(project, stage2, cell_dir)
        structural = audit_registry_structure(
            project, stage2, registry, experiment_packet, require_experiment_claim=False
        )
        if structural.get("passed") is not True:
            raise PublicationManualAuditError("registry structural audit failed")
        evidence_by_id = {
            str(item["claim_id"]): item
            for item in _claim_evidence_packets(stage2, registry, experiment_packet, structural)
        }
        claims = {claim.claim_id: claim for claim in registry.claims}
        for judgment in evaluation.get("claims", []):
            claim = claims.get(str(judgment["claim_id"]))
            if claim is None:
                raise PublicationManualAuditError("protected evaluation references a missing claim")
            # Novelty is explicitly outside the evaluator's supported/unsupported construct.
            if claim.claim_type.value == "novelty":
                continue
            audit_id, sort_key = _audit_id(str(protocol["protocol_id"]), registry.registry_id, claim.claim_id)
            candidates.append(
                {
                    "audit_id": audit_id,
                    "sort_key": sort_key,
                    "task_id": str(mapping["task_id"]),
                    "arm": arm,
                    "registry_id": registry.registry_id,
                    "claim_id": claim.claim_id,
                    "claim_type": claim.claim_type.value,
                    "claim_text": _blind_text(claim.claim_text),
                    "linked_evidence": _blinded_evidence(evidence_by_id[claim.claim_id]),
                    "evaluator_class": "unsupported"
                    if judgment.get("verdict") == "unsupported"
                    else "non_unsupported",
                }
            )
    if len({item["audit_id"] for item in candidates}) != len(candidates):
        raise PublicationManualAuditError("manual-audit identifiers are not unique")
    return candidates


def _select_blinded_sample(
    candidates: list[dict[str, Any]], protocol: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manual = dict(protocol["manual_audit"])
    group_order = [
        (str(task["task_id"]), arm)
        for task in list(protocol["tasks"])
        for arm in ("baseline", "treatment")
    ]
    claims_per_group = int(manual["claims_per_task_arm"])
    planned_unsupported = int(manual["target_unsupported_per_task_arm"]) * len(group_order)
    available_unsupported = sum(item["evaluator_class"] == "unsupported" for item in candidates)
    select_all_unsupported = available_unsupported < planned_unsupported
    selected: list[dict[str, Any]] = []
    group_counts: dict[str, dict[str, int]] = {}
    for task_id, arm in group_order:
        group = sorted(
            (item for item in candidates if item["task_id"] == task_id and item["arm"] == arm),
            key=lambda item: item["sort_key"],
        )
        unsupported = [item for item in group if item["evaluator_class"] == "unsupported"]
        other = [item for item in group if item["evaluator_class"] != "unsupported"]
        if select_all_unsupported:
            chosen = unsupported + other[: claims_per_group - len(unsupported)]
        else:
            chosen = (
                unsupported[: int(manual["target_unsupported_per_task_arm"])]
                + other[: int(manual["target_non_unsupported_per_task_arm"])]
            )
            if len(chosen) < claims_per_group:
                chosen += [item for item in group if item not in chosen][: claims_per_group - len(chosen)]
        if len(chosen) != claims_per_group:
            raise PublicationManualAuditError(
                f"insufficient eligible claims in blinded stratum {task_id}|{arm}: "
                f"{len(chosen)} < {claims_per_group}"
            )
        selected.extend(chosen)
        group_counts[f"{task_id}|{arm}"] = {
            "selected": len(chosen),
            "available": len(group),
            "selected_evaluator_unsupported": sum(
                item["evaluator_class"] == "unsupported" for item in chosen
            ),
            "available_evaluator_unsupported": len(unsupported),
        }
    if len(selected) != int(manual["total_claims"]):
        raise PublicationManualAuditError("selected audit count does not match frozen protocol")
    if select_all_unsupported and sum(
        item["evaluator_class"] == "unsupported" for item in selected
    ) != available_unsupported:
        raise PublicationManualAuditError("sample omitted an available evaluator-unsupported claim")
    return selected, {
        "planned_total": int(manual["total_claims"]),
        "actual_total": len(selected),
        "planned_unsupported_quota": planned_unsupported,
        "available_evaluator_unsupported": available_unsupported,
        "all_available_unsupported_selected": select_all_unsupported,
        "group_counts": group_counts,
    }


def prepare_publication_manual_audit(project: Path) -> dict[str, Any]:
    """Create the immutable 128-item blinded sample without changing evaluator output."""
    project = project.resolve()
    stage2 = project / "stage2"
    protocol = read_json(stage2 / "protocol.json")
    result_root = stage2 / RESULT_DIRNAME
    summary_path = result_root / "summary.json"
    summary = read_json(summary_path)
    if summary.get("primary_analysis_interpretable") is not False:
        raise PublicationManualAuditError("manual audit must be prepared before primary interpretation")
    if int(summary.get("pair_count", 0)) != 40:
        raise PublicationManualAuditError("publication evaluation is not complete for all 40 pairs")
    output_root = result_root / "manual-audit"
    output_root.mkdir(parents=True, exist_ok=True)
    sample_path = output_root / "sample.json"
    manifest_path = output_root / "manifest.json"
    if sample_path.is_file() or manifest_path.is_file():
        raise PublicationManualAuditError("publication manual-audit sample already exists; amendment is forbidden")
    candidates = _candidate_inventory(project, protocol)
    selected, selection = _select_blinded_sample(candidates, protocol)
    sample = {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "arm_blinded": True,
        "task_blinded": True,
        "evaluator_verdict_blinded": True,
        "independent_auditors_required": protocol["manual_audit"]["independent_auditors"],
        "adjudication_required": protocol["manual_audit"]["adjudication_required"],
        "planned_total": protocol["manual_audit"]["total_claims"],
        "actual_total": len(selected),
        "items": [
            {
                "audit_id": item["audit_id"],
                "claim_type": item["claim_type"],
                "claim_text": item["claim_text"],
                "linked_evidence": item["linked_evidence"],
                "auditor_1": {"verdict": None, "rationale": ""},
                "auditor_2": {"verdict": None, "rationale": ""},
                "adjudication": {"verdict": None, "rationale": ""},
            }
            for item in sorted(selected, key=lambda item: item["sort_key"])
        ],
    }
    write_json_atomic(sample_path, sample)
    manifest = {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "source_summary_sha256": sha256_file(summary_path),
        "source_evaluation_hashes": summary.get("evaluation_hashes", {}),
        "sample_sha256": sha256_file(sample_path),
        "status": "awaiting_two_independent_auditors",
        "selection": selection,
    }
    write_json_atomic(manifest_path, manifest)
    return manifest


def prepare_publication_manual_audit_packets(project: Path) -> dict[str, Any]:
    """Create two immutable, independently completable packets for the frozen sample.

    The sample itself remains blank and hash-bound.  Re-running this function is
    idempotent only when every previously generated packet still matches its
    recorded hash; partial or changed packet directories fail closed.
    """
    project = project.resolve()
    paths = _audit_paths(project)
    if not paths["sample"].is_file() and not paths["manifest"].is_file():
        prepare_publication_manual_audit(project)
    sample, _, _ = _load_frozen_sample(project)
    source_hash = sha256_file(paths["sample"])
    packet_root = paths["packet_root"]
    packet_manifest_path = paths["packet_manifest"]
    if packet_manifest_path.is_file():
        existing = read_json(packet_manifest_path)
        if existing.get("source_sample_sha256") != source_hash:
            raise PublicationManualAuditError("publication auditor packets target another sample")
        for filename, expected_hash in dict(existing.get("derived_files", {})).items():
            derived = packet_root / filename
            if not derived.is_file() or sha256_file(derived) != expected_hash:
                raise PublicationManualAuditError(f"publication auditor packet changed: {filename}")
        return existing
    if packet_root.exists() and any(packet_root.iterdir()):
        raise PublicationManualAuditError("partial publication auditor packet directory exists")
    packet_root.mkdir(parents=True, exist_ok=True)
    packet_paths: list[Path] = []
    for role in ("auditor_1", "auditor_2"):
        packet_path = packet_root / f"{role}.json"
        write_json_atomic(packet_path, _auditor_packet(sample, role, source_hash))
        packet_paths.append(packet_path)
    readme_path = packet_root / "README.txt"
    readme_path.write_text(
        "Independent publication manual audit\n"
        "====================================\n\n"
        "1. Give auditor_1.json and auditor_2.json to two different people.\n"
        f"2. Each person completes the attestation and all {len(sample['items'])} responses alone.\n"
        "3. Do not share answers, evaluator output, arm/task labels, or unblinding files.\n"
        "4. Return each file unchanged except for auditor_attestation and response fields.\n"
        "5. Keep sample.json unchanged; its SHA-256 is recorded here.\n"
        "6. Submit both packets before creating or viewing the disagreement packet.\n",
        encoding="utf-8",
        newline="\n",
    )
    packet_manifest = {
        "schema_version": 1,
        "protocol_id": sample["protocol_id"],
        "source_sample": str(paths["sample"]),
        "source_sample_sha256": source_hash,
        "items_per_auditor": len(sample["items"]),
        "independent_auditors": 2,
        "status": "awaiting_two_independent_auditors",
        "derived_files": {
            path.name: sha256_file(path) for path in [*packet_paths, readme_path]
        },
    }
    write_json_atomic(packet_manifest_path, packet_manifest)
    return packet_manifest


def _adjudication_packet(
    sample: dict[str, Any],
    source_hash: str,
    input_hashes: dict[str, str],
    decisions_1: dict[str, dict[str, str]],
    decisions_2: dict[str, dict[str, str]],
) -> dict[str, Any]:
    sample_by_id = {str(item["audit_id"]): item for item in sample["items"]}
    disagreements = [
        audit_id
        for audit_id in decisions_1
        if decisions_1[audit_id]["verdict"] != decisions_2[audit_id]["verdict"]
    ]
    return {
        "schema_version": 1,
        "protocol_id": sample["protocol_id"],
        "source_sample_sha256": source_hash,
        "source_auditor_packet_sha256": input_hashes,
        "arm_blinded": True,
        "task_blinded": True,
        "evaluator_verdict_blinded": True,
        "allowed_verdicts": ALLOWED_VERDICTS,
        "instructions": ADJUDICATOR_INSTRUCTIONS,
        "adjudicator_attestation": {
            "adjudicator_id": "",
            "completed_at": "",
            "did_not_view_unblinding_or_evaluator_output": None,
        },
        "items": [
            {
                "audit_id": audit_id,
                "claim_type": sample_by_id[audit_id]["claim_type"],
                "claim_text": sample_by_id[audit_id]["claim_text"],
                "linked_evidence": sample_by_id[audit_id]["linked_evidence"],
                "auditor_1": decisions_1[audit_id],
                "auditor_2": decisions_2[audit_id],
                "response": {"verdict": None, "rationale": ""},
            }
            for audit_id in disagreements
        ],
    }


def submit_publication_manual_audits(
    project: Path, auditor_1_path: Path, auditor_2_path: Path
) -> dict[str, Any]:
    """Validate and freeze two completed, independent publication-audit packets."""
    project = project.resolve()
    paths = _audit_paths(project)
    sample, _, _ = _load_frozen_sample(project)
    prepare_publication_manual_audit_packets(project)
    source_hash = sha256_file(paths["sample"])
    packet_1 = read_json(auditor_1_path.resolve())
    packet_2 = read_json(auditor_2_path.resolve())
    try:
        auditor_1_id, decisions_1 = validate_auditor_packet_data(
            sample, packet_1, "auditor_1", source_hash
        )
        auditor_2_id, decisions_2 = validate_auditor_packet_data(
            sample, packet_2, "auditor_2", source_hash
        )
    except ValueError as exc:
        raise PublicationManualAuditError(str(exc)) from exc
    if auditor_1_id == auditor_2_id:
        raise PublicationManualAuditError("the two publication auditors must have different IDs")
    input_hashes = {
        "auditor_1": sha256_file(auditor_1_path.resolve()),
        "auditor_2": sha256_file(auditor_2_path.resolve()),
    }
    if paths["submission_manifest"].is_file():
        existing = read_json(paths["submission_manifest"])
        if existing.get("input_packet_sha256") == input_hashes:
            return existing
        raise PublicationManualAuditError("different publication auditor submissions were already frozen")
    paths["submission_root"].mkdir(parents=True, exist_ok=False)
    stored_1 = paths["submission_root"] / "auditor_1.json"
    stored_2 = paths["submission_root"] / "auditor_2.json"
    write_json_atomic(stored_1, packet_1)
    write_json_atomic(stored_2, packet_2)
    adjudication = _adjudication_packet(
        sample, source_hash, input_hashes, decisions_1, decisions_2
    )
    write_json_atomic(paths["adjudication"], adjudication)
    agreement_count = len(decisions_1) - len(adjudication["items"])
    submission = {
        "schema_version": 1,
        "protocol_id": sample["protocol_id"],
        "submitted_at": utc_now(),
        "source_sample_sha256": source_hash,
        "input_packet_sha256": input_hashes,
        "stored_packet_sha256": {
            "auditor_1": sha256_file(stored_1),
            "auditor_2": sha256_file(stored_2),
        },
        "auditor_ids_sha256": {
            "auditor_1": hashlib.sha256(auditor_1_id.encode("utf-8")).hexdigest(),
            "auditor_2": hashlib.sha256(auditor_2_id.encode("utf-8")).hexdigest(),
        },
        "items_per_auditor": len(decisions_1),
        "agreement_count": agreement_count,
        "agreement_rate": agreement_count / len(decisions_1),
        "cohen_kappa": _cohen_kappa(decisions_1, decisions_2),
        "disagreement_count": len(adjudication["items"]),
        "disagreement_audit_ids": [item["audit_id"] for item in adjudication["items"]],
        "adjudication_packet": str(paths["adjudication"]),
        "adjudication_packet_sha256": sha256_file(paths["adjudication"]),
        "status": "awaiting_blinded_adjudication" if adjudication["items"] else "ready_to_finalize",
    }
    write_json_atomic(paths["submission_manifest"], submission)
    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "status": submission["status"],
            "submission_manifest_sha256": sha256_file(paths["submission_manifest"]),
        }
    )
    write_json_atomic(paths["manifest"], manifest)
    return submission


def _validate_publication_adjudication(
    expected: dict[str, Any], submitted: dict[str, Any]
) -> tuple[str, dict[str, dict[str, str]]]:
    if set(submitted) != set(expected):
        raise PublicationManualAuditError("publication adjudication top-level fields changed")
    for key in set(expected) - {"adjudicator_attestation", "items"}:
        if submitted.get(key) != expected.get(key):
            raise PublicationManualAuditError(f"publication adjudication field changed: {key}")
    attestation = submitted.get("adjudicator_attestation")
    expected_attestation = expected["adjudicator_attestation"]
    if not isinstance(attestation, dict) or set(attestation) != set(expected_attestation):
        raise PublicationManualAuditError("publication adjudicator attestation fields changed")
    adjudicator_id = str(attestation.get("adjudicator_id", "")).strip()
    if not adjudicator_id:
        raise PublicationManualAuditError("publication adjudicator_id is required")
    from datetime import datetime

    try:
        completed = datetime.fromisoformat(str(attestation.get("completed_at", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublicationManualAuditError("publication adjudicator completed_at must be ISO-8601") from exc
    if completed.tzinfo is None:
        raise PublicationManualAuditError("publication adjudicator completed_at needs a timezone")
    if attestation.get("did_not_view_unblinding_or_evaluator_output") is not True:
        raise PublicationManualAuditError("publication adjudicator must attest protected blinding")
    items = submitted.get("items")
    expected_items = expected["items"]
    if not isinstance(items, list) or len(items) != len(expected_items):
        raise PublicationManualAuditError("publication adjudication must preserve every disagreement")
    decisions: dict[str, dict[str, str]] = {}
    for index, (item, expected_item) in enumerate(zip(items, expected_items, strict=True)):
        if not isinstance(item, dict) or set(item) != set(expected_item):
            raise PublicationManualAuditError(f"publication adjudication item {index} fields changed")
        for key in set(expected_item) - {"response"}:
            if item.get(key) != expected_item.get(key):
                raise PublicationManualAuditError(
                    f"publication adjudication item {index} immutable field changed: {key}"
                )
        response = item.get("response")
        if not isinstance(response, dict) or set(response) != {"verdict", "rationale"}:
            raise PublicationManualAuditError(f"publication adjudication item {index} response changed")
        verdict = str(response.get("verdict", ""))
        rationale = str(response.get("rationale", "")).strip()
        if verdict not in ALLOWED_VERDICTS or not rationale:
            raise PublicationManualAuditError(f"publication adjudication item {index} is incomplete")
        decisions[str(item["audit_id"])] = {"verdict": verdict, "rationale": rationale}
    return adjudicator_id, decisions


def _publication_evaluator_verdicts(project: Path, protocol: dict[str, Any]) -> dict[str, str]:
    candidates = _candidate_inventory(project, protocol)
    verdicts = {
        str(item["audit_id"]): (
            "unsupported" if item["evaluator_class"] == "unsupported" else "supported"
        )
        for item in candidates
    }
    if len(verdicts) != len(candidates):
        raise PublicationManualAuditError("publication evaluator audit IDs are not unique")
    return verdicts


def finalize_publication_manual_audit(
    project: Path, adjudication_path: Path | None = None
) -> dict[str, Any]:
    """Resolve blinded disagreements and evaluate the preregistered human gate."""
    project = project.resolve()
    paths = _audit_paths(project)
    sample, _, protected_summary = _load_frozen_sample(project)
    protocol = read_json(project / "stage2" / "protocol.json")
    if paths["result"].is_file():
        audit = audit_publication_manual_audit(project)
        if audit["passed"] and audit["complete"]:
            return read_json(paths["result"])
        raise PublicationManualAuditError("existing publication manual-audit result failed integrity checks")
    if not paths["submission_manifest"].is_file():
        raise PublicationManualAuditError("publication auditor submissions have not been frozen")
    submission = read_json(paths["submission_manifest"])
    stored_1 = paths["submission_root"] / "auditor_1.json"
    stored_2 = paths["submission_root"] / "auditor_2.json"
    for role, stored in (("auditor_1", stored_1), ("auditor_2", stored_2)):
        if not stored.is_file() or sha256_file(stored) != submission["stored_packet_sha256"][role]:
            raise PublicationManualAuditError(f"stored publication {role} packet changed")
    source_hash = sha256_file(paths["sample"])
    try:
        auditor_1_id, decisions_1 = validate_auditor_packet_data(
            sample, read_json(stored_1), "auditor_1", source_hash
        )
        auditor_2_id, decisions_2 = validate_auditor_packet_data(
            sample, read_json(stored_2), "auditor_2", source_hash
        )
    except ValueError as exc:
        raise PublicationManualAuditError(str(exc)) from exc
    if auditor_1_id == auditor_2_id:
        raise PublicationManualAuditError("publication auditor identities are not independent")
    disagreements = list(submission.get("disagreement_audit_ids", []))
    adjudicated: dict[str, dict[str, str]] = {}
    adjudication_hash: str | None = None
    adjudicator_hash: str | None = None
    if disagreements:
        if adjudication_path is None:
            raise PublicationManualAuditError("a completed blinded publication adjudication is required")
        if sha256_file(paths["adjudication"]) != submission["adjudication_packet_sha256"]:
            raise PublicationManualAuditError("frozen publication adjudication template changed")
        adjudicator_id, adjudicated = _validate_publication_adjudication(
            read_json(paths["adjudication"]), read_json(adjudication_path.resolve())
        )
        adjudication_hash = sha256_file(adjudication_path.resolve())
        adjudicator_hash = hashlib.sha256(adjudicator_id.encode("utf-8")).hexdigest()
    final_decisions: dict[str, dict[str, str]] = {}
    for audit_id in decisions_1:
        if decisions_1[audit_id]["verdict"] == decisions_2[audit_id]["verdict"]:
            final_decisions[audit_id] = {
                "verdict": decisions_1[audit_id]["verdict"],
                "rationale": "Independent auditors agreed. " + decisions_1[audit_id]["rationale"],
                "resolution": "auditor_agreement",
            }
        else:
            decision = adjudicated.get(audit_id)
            if decision is None:
                raise PublicationManualAuditError(f"missing publication adjudication for {audit_id}")
            final_decisions[audit_id] = {**decision, "resolution": "blinded_adjudication"}
    evaluator = _publication_evaluator_verdicts(project, protocol)
    planned_quota = (
        int(protocol["manual_audit"]["target_unsupported_per_task_arm"])
        * len(protocol["tasks"])
        * 2
    )
    try:
        gate = _manual_gate_statistics(
            evaluator,
            final_decisions,
            planned_unsupported_quota=planned_quota,
            threshold=float(protocol["manual_audit"]["false_positive_threshold"]),
        )
    except ValueError as exc:
        raise PublicationManualAuditError(str(exc)) from exc
    agreement_count = sum(
        decisions_1[audit_id]["verdict"] == decisions_2[audit_id]["verdict"]
        for audit_id in decisions_1
    )
    threshold_passed = gate["threshold_passed"] is True
    analysis_status = (
        "publication_human_audit_complete_primary_analysis_unlocked"
        if threshold_passed
        else "publication_human_audit_complete_primary_analysis_invalid"
    )
    result = {
        "schema_version": 1,
        "protocol_id": sample["protocol_id"],
        "completed_at": utc_now(),
        "source_sample_sha256": source_hash,
        "auditor_packet_sha256": submission["stored_packet_sha256"],
        "adjudication_packet_sha256": adjudication_hash,
        "adjudicator_id_sha256": adjudicator_hash,
        "audited_claims": len(final_decisions),
        "agreement_count": agreement_count,
        "agreement_rate": agreement_count / len(final_decisions),
        "cohen_kappa": _cohen_kappa(decisions_1, decisions_2),
        "adjudicated_disagreements": len(adjudicated),
        "final_verdict_counts": dict(
            Counter(item["verdict"] for item in final_decisions.values())
        ),
        "manual_gate": gate,
        "human_validation": "COMPLETE",
        "primary_analysis_interpretable": threshold_passed,
        "analysis_status": analysis_status,
        "final_decisions": [
            {"audit_id": audit_id, **final_decisions[audit_id]}
            for audit_id in sorted(final_decisions)
        ],
    }
    write_json_atomic(paths["result"], result)
    final_analysis = {
        "schema_version": 1,
        "protocol_id": sample["protocol_id"],
        "created_at": utc_now(),
        "analysis_status": analysis_status,
        "human_validation": "COMPLETE",
        "primary_analysis_interpretable": threshold_passed,
        "manual_audit_result_sha256": sha256_file(paths["result"]),
        "manual_gate": gate,
        "protected_summary_sha256": sha256_file(paths["summary"]),
        "pair_count": protected_summary["pair_count"],
        "primary_metric": protected_summary["primary_metric"],
        "arm_metrics": protected_summary["arm_metrics"],
        "paired_analysis": protected_summary["paired_analysis"],
        "leave_one_task_out": protected_summary["leave_one_task_out"],
        "construct_boundary": protected_summary["construct_boundary"],
    }
    if not threshold_passed:
        final_analysis["reason"] = (
            "The adjudicated project-specific human audit exceeded the preregistered "
            "automated-evaluator false-positive threshold."
        )
    write_json_atomic(paths["final_analysis"], final_analysis)
    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "status": "complete",
            "completed_at": result["completed_at"],
            "manual_audit_result_sha256": sha256_file(paths["result"]),
            "primary_analysis_interpretable": threshold_passed,
        }
    )
    write_json_atomic(paths["manifest"], manifest)
    return result


def audit_publication_manual_audit(project: Path) -> dict[str, Any]:
    """Audit the publication human-review state without changing it."""
    project = project.resolve()
    paths = _audit_paths(project)
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    try:
        sample, manifest, _ = _load_frozen_sample(project)
        check("sample_hash_frozen", True, "publication audit sample is missing or changed")
        check(
            "sample_count_matches_protocol",
            len(sample["items"]) == int(manifest["selection"]["actual_total"]),
            "publication audit sample count changed",
        )
        selection = manifest["selection"]
        check(
            "all_available_unsupported_sampled",
            not selection.get("all_available_unsupported_selected")
            or sum(
                int(group.get("selected_evaluator_unsupported", 0))
                for group in selection.get("group_counts", {}).values()
            )
            == int(selection.get("available_evaluator_unsupported", -1)),
            "publication audit omitted an available evaluator-unsupported claim",
        )
        if paths["packet_manifest"].is_file():
            packet_manifest = read_json(paths["packet_manifest"])
            check(
                "auditor_packets_match_sample",
                packet_manifest.get("source_sample_sha256") == sha256_file(paths["sample"]),
                "publication auditor packets target another sample",
            )
            for filename, expected_hash in dict(packet_manifest.get("derived_files", {})).items():
                derived = paths["packet_root"] / filename
                check(
                    f"packet_hash_{filename}",
                    derived.is_file() and sha256_file(derived) == expected_hash,
                    f"publication auditor packet changed: {filename}",
                )
    except Exception as exc:
        check("sample_loads", False, str(exc))

    complete = False
    status = "awaiting_two_independent_auditors"
    if paths["result"].is_file():
        result = read_json(paths["result"])
        status = str(result.get("analysis_status"))
        complete = True
        check(
            "result_has_all_claims",
            int(result.get("audited_claims", -1))
            == int(read_json(paths["manifest"])["selection"]["actual_total"]),
            "publication manual-audit result is incomplete",
        )
        check(
            "result_hash_bound",
            read_json(paths["manifest"]).get("manual_audit_result_sha256")
            == sha256_file(paths["result"]),
            "publication manual-audit result hash mismatch",
        )
        check("final_analysis_present", paths["final_analysis"].is_file(), "final analysis is missing")
        if paths["final_analysis"].is_file():
            final_analysis = read_json(paths["final_analysis"])
            check(
                "final_analysis_bound_to_result",
                final_analysis.get("manual_audit_result_sha256") == sha256_file(paths["result"]),
                "publication final analysis is not bound to the manual-audit result",
            )
    elif paths["submission_manifest"].is_file():
        status = str(read_json(paths["submission_manifest"]).get("status"))
    return {
        "schema_version": 1,
        "protocol_id": (
            read_json(paths["manifest"]).get("protocol_id") if paths["manifest"].is_file() else None
        ),
        "audited_at": utc_now(),
        "passed": bool(checks) and all(checks.values()),
        "complete": complete,
        "status": status,
        "checks": checks,
        "violations": violations,
    }
