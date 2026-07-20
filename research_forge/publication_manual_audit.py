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
from pathlib import Path
from typing import Any

from .publication_nli_evaluation import RESULT_DIRNAME, _publication_cell_experiment_packet
from .storage import read_json, sha256_file, write_json_atomic
from .study_models import StudyClaimRegistry
from .study_runner import _claim_evidence_packets, audit_registry_structure


class PublicationManualAuditError(ValueError):
    """Raised when the frozen publication evidence cannot support an audit sample."""


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
