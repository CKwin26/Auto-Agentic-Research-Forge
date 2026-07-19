from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import utc_now
from .storage import read_json, sha256_file, write_json_atomic
from .study_models import (
    ClaimVerdict,
    Stage2Protocol,
    StudyArm,
    StudyClaimRegistry,
    StudyRegistryEvaluation,
)


ALLOWED_VERDICTS = [item.value for item in ClaimVerdict]
AUDITOR_INSTRUCTIONS = [
    "Judge every claim only against the linked evidence in this packet.",
    "Use supported only when the evidence directly supports the complete substantive claim.",
    "Use unsupported when a substantive part is missing, contradicted, or stronger than the evidence.",
    "Use abstain only when the supplied evidence is unreadable or genuinely insufficient to decide.",
    "Write a concise evidence-based rationale for every verdict.",
    "Do not inspect the other auditor packet, evaluator output, arm labels, task labels, or unblinding files.",
    "Do not change audit_id, claim text, linked evidence, item order, or packet metadata.",
]
ADJUDICATOR_INSTRUCTIONS = [
    "Adjudicate every listed disagreement using only the claim, linked evidence, and two blinded auditor rationales.",
    "Use supported only when the evidence directly supports the complete substantive claim.",
    "Use unsupported when a substantive part is missing, contradicted, or stronger than the evidence.",
    "Use abstain only when the supplied evidence is unreadable or genuinely insufficient to decide.",
    "Do not inspect evaluator output, arm labels, task labels, unblinding files, or primary-effect summaries.",
    "Do not change immutable fields or item order; edit only response.verdict and response.rationale.",
]


class ManualAuditError(ValueError):
    pass


def _sha256_value(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_protocol(project: Path) -> tuple[Path, Stage2Protocol]:
    project = project.resolve()
    stage2 = project / "stage2"
    protocol_path = stage2 / "protocol.json"
    if not protocol_path.is_file():
        raise ManualAuditError("Stage 2 protocol is missing")
    return stage2, Stage2Protocol.model_validate(read_json(protocol_path))


def _sample_from_summary(stage2: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    summary_path = stage2 / "evaluations" / "summary.json"
    if not summary_path.is_file():
        raise ManualAuditError("protected evaluation summary is missing")
    summary = read_json(summary_path)
    manifest = summary.get("manual_audit_manifest")
    if not isinstance(manifest, dict):
        raise ManualAuditError("evaluation summary has no manual-audit manifest")
    sample_path = Path(str(manifest.get("sample_path", ""))).resolve()
    if not sample_path.is_file():
        raise ManualAuditError("frozen manual-audit sample is missing")
    expected_hash = str(manifest.get("sample_sha256", ""))
    if sha256_file(sample_path) != expected_hash:
        raise ManualAuditError("frozen manual-audit sample hash mismatch")
    sample = read_json(sample_path)
    return sample_path, sample, summary


def _audit_id(protocol_id: str, registry_id: str, claim_id: str) -> tuple[str, str]:
    sort_key = hashlib.sha256(
        f"{protocol_id}|{registry_id}|{claim_id}|manual-v1".encode("utf-8")
    ).hexdigest()
    return "audit-" + sort_key[:16], sort_key


def _candidate_inventory(
    project: Path, stage2: Path, protocol: Stage2Protocol
) -> list[dict[str, object]]:
    from .study_runner import (
        _cell_dir,
        _cell_experiment_packet,
        _claim_evidence_packets,
        audit_registry_structure,
    )

    unblinding = read_json(stage2 / "evaluations" / "unblinding.json")
    by_cell = {str(item["cell_id"]): item for item in unblinding.get("mapping", [])}
    candidates: list[dict[str, object]] = []
    for cell in protocol.cells:
        mapping = by_cell.get(cell.cell_id)
        if not mapping:
            raise ManualAuditError(f"unblinding entry is missing for {cell.cell_id}")
        cell_dir = _cell_dir(stage2, cell)
        registry = StudyClaimRegistry.model_validate(read_json(cell_dir / "final_registry.json"))
        evaluation = StudyRegistryEvaluation.model_validate(
            read_json(stage2 / "evaluations" / str(mapping["blind_id"]) / "evaluation.json")
        )
        if evaluation.registry_id != registry.registry_id:
            raise ManualAuditError(f"evaluation/registry mismatch for {cell.cell_id}")
        experiment_packet = _cell_experiment_packet(project, cell_dir)
        structural = audit_registry_structure(
            project,
            stage2,
            registry,
            experiment_packet,
            require_experiment_claim=False,
        )
        if structural.get("passed") is not True:
            raise ManualAuditError(f"registry integrity failed for {cell.cell_id}")
        evidence_by_id = {
            str(item["claim_id"]): item
            for item in _claim_evidence_packets(
                stage2, registry, experiment_packet, structural
            )
        }
        claims = {claim.claim_id: claim for claim in registry.claims}
        for judgment in evaluation.claims:
            claim = claims.get(judgment.claim_id)
            if claim is None:
                raise ManualAuditError(f"evaluated claim is missing: {judgment.claim_id}")
            evidence = json.loads(
                json.dumps(evidence_by_id[claim.claim_id], ensure_ascii=False)
            )
            evidence.pop("deterministic_structural_checks", None)
            paths = list(evidence.get("declared_artifact_paths", []))
            aliases = {path: f"artifact-{index:03d}" for index, path in enumerate(paths, 1)}
            evidence["declared_artifact_paths"] = [aliases[path] for path in paths]
            linked = evidence.get("linked_experiment_evidence")
            if isinstance(linked, dict):
                linked["artifacts"] = [
                    aliases.get(path, f"linked-artifact-{index:03d}")
                    for index, path in enumerate(linked.get("artifacts", []), 1)
                ]
            audit_id, sort_key = _audit_id(
                protocol.protocol_id, registry.registry_id, claim.claim_id
            )
            candidates.append(
                {
                    "audit_id": audit_id,
                    "sort_key": sort_key,
                    "task_id": cell.task_id,
                    "arm": cell.arm.value,
                    "registry_id": registry.registry_id,
                    "claim_id": claim.claim_id,
                    "claim_type": claim.claim_type.value,
                    "claim_text": claim.claim_text,
                    "linked_evidence": evidence,
                    "evaluator_verdict": judgment.verdict.value,
                    "evaluator_class": (
                        "unsupported"
                        if judgment.verdict == ClaimVerdict.UNSUPPORTED
                        else "non_unsupported"
                    ),
                }
            )
    if len(candidates) != 72:
        raise ManualAuditError(
            f"expected 72 evaluated claims before sampling, found {len(candidates)}"
        )
    if len({str(item["audit_id"]) for item in candidates}) != len(candidates):
        raise ManualAuditError("manual-audit IDs are not unique")
    return candidates


def _select_candidate_sample(
    candidates: list[dict[str, object]],
    group_order: list[tuple[str, str]],
    *,
    claims_per_group: int,
    target_unsupported_per_group: int,
    target_non_unsupported_per_group: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    planned_unsupported = target_unsupported_per_group * len(group_order)
    available_unsupported = sum(
        item.get("evaluator_class") == "unsupported" for item in candidates
    )
    audit_all_available_unsupported = available_unsupported < planned_unsupported
    selected: list[dict[str, object]] = []
    group_counts: dict[str, dict[str, int]] = {}
    for task_id, arm in group_order:
        group = sorted(
            (
                item
                for item in candidates
                if item.get("task_id") == task_id and item.get("arm") == arm
            ),
            key=lambda item: str(item["sort_key"]),
        )
        unsupported = [
            item for item in group if item.get("evaluator_class") == "unsupported"
        ]
        other = [
            item for item in group if item.get("evaluator_class") != "unsupported"
        ]
        if audit_all_available_unsupported:
            if len(unsupported) > claims_per_group:
                raise ManualAuditError(
                    f"cannot audit all available unsupported claims in {task_id}|{arm} "
                    f"within the frozen {claims_per_group}-claim stratum"
                )
            chosen = list(unsupported)
            chosen.extend(other[: claims_per_group - len(chosen)])
        else:
            chosen = unsupported[:target_unsupported_per_group]
            chosen.extend(other[:target_non_unsupported_per_group])
            if len(chosen) < claims_per_group:
                remaining = [item for item in group if item not in chosen]
                chosen.extend(remaining[: claims_per_group - len(chosen)])
        if len(chosen) != claims_per_group:
            raise ManualAuditError(
                f"manual-audit stratum {task_id}|{arm} has {len(chosen)} claims, "
                f"expected {claims_per_group}"
            )
        selected.extend(chosen)
        group_counts[f"{task_id}|{arm}"] = {
            "total": len(chosen),
            "evaluator_unsupported": sum(
                item.get("evaluator_class") == "unsupported" for item in chosen
            ),
            "evaluator_non_unsupported": sum(
                item.get("evaluator_class") != "unsupported" for item in chosen
            ),
            "available_evaluator_unsupported": len(unsupported),
        }
    audit = {
        "planned_unsupported_quota": planned_unsupported,
        "available_evaluator_unsupported": available_unsupported,
        "audit_all_available_unsupported": audit_all_available_unsupported,
        "selected_evaluator_unsupported": sum(
            item.get("evaluator_class") == "unsupported" for item in selected
        ),
        "selected_evaluator_non_unsupported": sum(
            item.get("evaluator_class") != "unsupported" for item in selected
        ),
        "group_counts": group_counts,
    }
    if (
        audit_all_available_unsupported
        and audit["selected_evaluator_unsupported"] != available_unsupported
    ):
        raise ManualAuditError(
            "manual sample does not include every available evaluator-unsupported claim"
        )
    return selected, audit


def _sample_value(protocol: Stage2Protocol, selected: list[dict[str, object]]) -> dict[str, object]:
    items = [
        {
            "audit_id": item["audit_id"],
            "claim_type": item["claim_type"],
            "claim_text": item["claim_text"],
            "linked_evidence": item["linked_evidence"],
            "auditor_1": {"verdict": None, "rationale": ""},
            "auditor_2": {"verdict": None, "rationale": ""},
            "adjudication": {"verdict": None, "rationale": ""},
        }
        for item in sorted(selected, key=lambda value: str(value["sort_key"]))
    ]
    return {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "arm_blinded": True,
        "task_blinded": True,
        "evaluator_verdict_blinded": True,
        "independent_auditors_required": protocol.manual_audit.independent_auditors,
        "adjudication_required": protocol.manual_audit.adjudication_required,
        "planned_total": protocol.manual_audit.total_claims,
        "actual_total": len(items),
        "items": items,
    }


def _auditor_packet(sample: dict[str, object], role: str, source_hash: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol_id": sample["protocol_id"],
        "packet_role": role,
        "source_sample_sha256": source_hash,
        "arm_blinded": True,
        "task_blinded": True,
        "evaluator_verdict_blinded": True,
        "allowed_verdicts": ALLOWED_VERDICTS,
        "instructions": AUDITOR_INSTRUCTIONS,
        "auditor_attestation": {
            "auditor_id": "",
            "completed_at": "",
            "worked_independently": None,
            "did_not_view_other_answers": None,
            "did_not_view_unblinding_or_evaluator_output": None,
        },
        "items": [
            {
                "audit_id": item["audit_id"],
                "claim_type": item["claim_type"],
                "claim_text": item["claim_text"],
                "linked_evidence": item["linked_evidence"],
                "response": {"verdict": None, "rationale": ""},
            }
            for item in sample["items"]
        ],
    }


def _write_auditor_packets(
    sample_path: Path, sample: dict[str, object], output_dir: Path
) -> dict[str, object]:
    source_hash = sha256_file(sample_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for role in ("auditor_1", "auditor_2"):
        path = output_dir / f"{role}.json"
        write_json_atomic(path, _auditor_packet(sample, role, source_hash))
        paths.append(path)
    readme = output_dir / "README.txt"
    readme.write_text(
        "Independent manual audit\n"
        "========================\n\n"
        "1. Give auditor_1.json and auditor_2.json to different people.\n"
        "2. Each person completes the attestation and all 48 response fields alone.\n"
        "3. Do not share answers, evaluator output, arm/task labels, or unblinding files.\n"
        "4. Return both files unchanged except for auditor_attestation and response fields.\n"
        "5. Only after both are returned may the blinded disagreement packet be created.\n"
        "6. Keep sample.json unchanged; its SHA-256 is recorded in packet_manifest.json.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 2,
        "protocol_id": sample["protocol_id"],
        "source_sample": str(sample_path),
        "source_sample_sha256": source_hash,
        "items_per_auditor": len(sample["items"]),
        "independent_auditors": 2,
        "status": "awaiting_two_independent_auditors",
        "derived_files": {path.name: sha256_file(path) for path in [*paths, readme]},
    }
    write_json_atomic(output_dir / "packet_manifest.json", manifest)
    return manifest


def prepare_manual_audit(project: Path) -> dict[str, object]:
    project = project.resolve()
    stage2, protocol = _load_protocol(project)
    from .study_runner import audit_stage2_evaluation

    evaluation_audit = audit_stage2_evaluation(project, persist=False)
    if not evaluation_audit.passed or not evaluation_audit.complete:
        raise ManualAuditError("protected evaluation must pass before manual-audit preparation")
    sample_path, current_sample, summary = _sample_from_summary(stage2)
    submission_root = stage2 / "manual_audit"
    if (submission_root / "submission_manifest.json").is_file():
        raise ManualAuditError("auditor submissions already exist; sample amendment is forbidden")

    candidates = _candidate_inventory(project, stage2, protocol)
    group_order = [
        (task.task_id, arm.value)
        for task in protocol.tasks
        for arm in (StudyArm.BASELINE, StudyArm.TREATMENT)
    ]
    selected, selection_audit = _select_candidate_sample(
        candidates,
        group_order,
        claims_per_group=protocol.manual_audit.claims_per_task_arm,
        target_unsupported_per_group=protocol.manual_audit.target_unsupported_per_task_arm,
        target_non_unsupported_per_group=(
            protocol.manual_audit.target_non_unsupported_per_task_arm
        ),
    )
    corrected_sample = _sample_value(protocol, selected)
    if len(corrected_sample["items"]) != protocol.manual_audit.total_claims:
        raise ManualAuditError("corrected manual sample is not exactly 48 claims")

    old_hash = sha256_file(sample_path)
    old_ids = {str(item["audit_id"]) for item in current_sample["items"]}
    new_ids = {str(item["audit_id"]) for item in corrected_sample["items"]}
    changed = old_ids != new_ids
    amendment_id: str | None = None
    if changed:
        history_root = sample_path.parent / "history" / old_hash
        history_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sample_path, history_root / "sample.json")
        packet_dir = sample_path.parent / "independent-packets"
        if packet_dir.is_dir():
            shutil.copytree(
                packet_dir,
                history_root / "independent-packets",
                dirs_exist_ok=True,
            )
        write_json_atomic(sample_path, corrected_sample)
        new_hash = sha256_file(sample_path)
        amendment_id = "manual-audit-amendment-01"
        amendment_path = stage2 / "manual_audit_amendments.json"
        amendment_document = (
            read_json(amendment_path)
            if amendment_path.is_file()
            else {"schema_version": 1, "protocol_id": protocol.protocol_id, "amendments": []}
        )
        amendments = list(amendment_document.get("amendments", []))
        record = {
            "amendment_id": amendment_id,
            "amended_at": utc_now(),
            "phase": "after_protected_evaluation_before_any_human_audit",
            "reason": (
                "The frozen stop condition requires auditing all available evaluator-unsupported "
                "claims when the planned unsupported quota is not met. The v1 sampler capped each "
                "task-arm stratum at four unsupported claims and omitted one of fourteen available claims."
            ),
            "behavior_change": (
                "Keep 48 total and 8 per task-arm stratum, include all 14 available "
                "evaluator-unsupported claims, and fill the remaining 34 positions from the "
                "deterministic non-unsupported order. Arm, task, and evaluator verdict remain blinded."
            ),
            "previous_sample_sha256": old_hash,
            "current_sample_sha256": new_hash,
            "removed_audit_ids": sorted(old_ids - new_ids),
            "added_audit_ids": sorted(new_ids - old_ids),
            "human_audits_started": 0,
            "implementation_sha256": sha256_file(Path(__file__)),
        }
        existing = [item for item in amendments if item.get("amendment_id") == amendment_id]
        if existing and existing[0] != record:
            raise ManualAuditError("manual-audit amendment record drifted")
        if not existing:
            amendments.append(record)
        amendment_document["amendments"] = amendments
        write_json_atomic(amendment_path, amendment_document)
    else:
        new_hash = old_hash
        corrected_sample = current_sample

    group_counts = {
        key: int(value["total"])
        for key, value in dict(selection_audit["group_counts"]).items()
    }
    manual_manifest = {
        "schema_version": 2,
        "protocol_id": protocol.protocol_id,
        "sample_path": str(sample_path),
        "sample_sha256": new_hash,
        "planned_total": protocol.manual_audit.total_claims,
        "actual_total": len(corrected_sample["items"]),
        "group_counts": group_counts,
        "selection_compliance": selection_audit,
        "amendment_id": amendment_id or "manual-audit-amendment-01",
        "status": "awaiting_two_independent_auditors",
    }
    write_json_atomic(stage2 / "manual_audit_manifest.json", manual_manifest)
    summary["manual_audit_manifest"] = manual_manifest
    write_json_atomic(stage2 / "evaluations" / "summary.json", summary)
    write_json_atomic(stage2 / "manual_audit_selection_audit.json", selection_audit)
    packet_manifest = _write_auditor_packets(
        sample_path,
        corrected_sample,
        sample_path.parent / "independent-packets",
    )
    return {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "sample_amended": changed,
        "sample_sha256": new_hash,
        "sample_items": len(corrected_sample["items"]),
        "selection_compliance": selection_audit,
        "packet_manifest": packet_manifest,
    }


def _parse_completed_at(value: object, *, label: str) -> str:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManualAuditError(f"{label} completed_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ManualAuditError(f"{label} completed_at must include a timezone")
    return text


def validate_auditor_packet_data(
    sample: dict[str, object],
    packet: dict[str, object],
    role: str,
    source_hash: str,
) -> tuple[str, dict[str, dict[str, str]]]:
    expected = _auditor_packet(sample, role, source_hash)
    immutable_top = set(expected) - {"auditor_attestation", "items"}
    if set(packet) != set(expected):
        raise ManualAuditError(f"{role} packet top-level fields changed")
    for key in immutable_top:
        if packet.get(key) != expected.get(key):
            raise ManualAuditError(f"{role} packet immutable field changed: {key}")
    attestation = packet.get("auditor_attestation")
    if not isinstance(attestation, dict) or set(attestation) != set(expected["auditor_attestation"]):
        raise ManualAuditError(f"{role} attestation fields changed")
    auditor_id = str(attestation.get("auditor_id", "")).strip()
    if not auditor_id:
        raise ManualAuditError(f"{role} auditor_id is required")
    _parse_completed_at(attestation.get("completed_at"), label=role)
    for field in (
        "worked_independently",
        "did_not_view_other_answers",
        "did_not_view_unblinding_or_evaluator_output",
    ):
        if attestation.get(field) is not True:
            raise ManualAuditError(f"{role} must attest {field}=true")

    items = packet.get("items")
    expected_items = expected["items"]
    if not isinstance(items, list) or len(items) != len(expected_items):
        raise ManualAuditError(f"{role} must contain all {len(expected_items)} items")
    decisions: dict[str, dict[str, str]] = {}
    for index, (item, expected_item) in enumerate(zip(items, expected_items, strict=True)):
        if not isinstance(item, dict) or set(item) != set(expected_item):
            raise ManualAuditError(f"{role} item {index} fields changed")
        for key in set(expected_item) - {"response"}:
            if item.get(key) != expected_item.get(key):
                raise ManualAuditError(f"{role} item {index} immutable field changed: {key}")
        response = item.get("response")
        if not isinstance(response, dict) or set(response) != {"verdict", "rationale"}:
            raise ManualAuditError(f"{role} item {index} response fields changed")
        verdict = str(response.get("verdict", ""))
        rationale = str(response.get("rationale", "")).strip()
        if verdict not in ALLOWED_VERDICTS:
            raise ManualAuditError(f"{role} item {index} has an invalid verdict")
        if not rationale:
            raise ManualAuditError(f"{role} item {index} rationale is required")
        decisions[str(item["audit_id"])] = {"verdict": verdict, "rationale": rationale}
    return auditor_id, decisions


def _cohen_kappa(
    first: dict[str, dict[str, str]], second: dict[str, dict[str, str]]
) -> float | None:
    ids = list(first)
    if not ids or set(ids) != set(second):
        return None
    observed = sum(first[item]["verdict"] == second[item]["verdict"] for item in ids) / len(ids)
    first_counts = Counter(first[item]["verdict"] for item in ids)
    second_counts = Counter(second[item]["verdict"] for item in ids)
    expected = sum(
        (first_counts[label] / len(ids)) * (second_counts[label] / len(ids))
        for label in ALLOWED_VERDICTS
    )
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else None
    return (observed - expected) / (1.0 - expected)


def submit_manual_audits(
    project: Path, auditor_1_path: Path, auditor_2_path: Path
) -> dict[str, object]:
    project = project.resolve()
    stage2, protocol = _load_protocol(project)
    sample_path, sample, _ = _sample_from_summary(stage2)
    source_hash = sha256_file(sample_path)
    packet_1 = read_json(auditor_1_path.resolve())
    packet_2 = read_json(auditor_2_path.resolve())
    auditor_1_id, decisions_1 = validate_auditor_packet_data(
        sample, packet_1, "auditor_1", source_hash
    )
    auditor_2_id, decisions_2 = validate_auditor_packet_data(
        sample, packet_2, "auditor_2", source_hash
    )
    if auditor_1_id == auditor_2_id:
        raise ManualAuditError("the two independent auditors must have different auditor_id values")

    submission_root = stage2 / "manual_audit"
    manifest_path = submission_root / "submission_manifest.json"
    input_hashes = {
        "auditor_1": sha256_file(auditor_1_path.resolve()),
        "auditor_2": sha256_file(auditor_2_path.resolve()),
    }
    if manifest_path.is_file():
        existing = read_json(manifest_path)
        if existing.get("input_packet_sha256") == input_hashes:
            return existing
        raise ManualAuditError("different auditor submissions were already frozen")

    write_json_atomic(submission_root / "auditor_1.json", packet_1)
    write_json_atomic(submission_root / "auditor_2.json", packet_2)
    disagreements = [
        audit_id
        for audit_id in decisions_1
        if decisions_1[audit_id]["verdict"] != decisions_2[audit_id]["verdict"]
    ]
    sample_by_id = {str(item["audit_id"]): item for item in sample["items"]}
    adjudication_packet = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
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
    adjudication_path = sample_path.parent / "adjudication.json"
    write_json_atomic(adjudication_path, adjudication_packet)
    agreement_count = len(decisions_1) - len(disagreements)
    manifest = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "submitted_at": utc_now(),
        "source_sample_sha256": source_hash,
        "input_packet_sha256": input_hashes,
        "stored_packet_sha256": {
            "auditor_1": sha256_file(submission_root / "auditor_1.json"),
            "auditor_2": sha256_file(submission_root / "auditor_2.json"),
        },
        "auditor_ids_sha256": {
            "auditor_1": hashlib.sha256(auditor_1_id.encode("utf-8")).hexdigest(),
            "auditor_2": hashlib.sha256(auditor_2_id.encode("utf-8")).hexdigest(),
        },
        "items_per_auditor": len(decisions_1),
        "agreement_count": agreement_count,
        "agreement_rate": agreement_count / len(decisions_1),
        "cohen_kappa": _cohen_kappa(decisions_1, decisions_2),
        "disagreement_count": len(disagreements),
        "disagreement_audit_ids": disagreements,
        "adjudication_packet": str(adjudication_path),
        "adjudication_packet_sha256": sha256_file(adjudication_path),
        "status": "awaiting_blinded_adjudication" if disagreements else "ready_to_finalize",
    }
    write_json_atomic(manifest_path, manifest)
    return manifest


def _validate_adjudication(
    expected: dict[str, object], submitted: dict[str, object]
) -> tuple[str, dict[str, dict[str, str]]]:
    if set(submitted) != set(expected):
        raise ManualAuditError("adjudication packet top-level fields changed")
    for key in set(expected) - {"adjudicator_attestation", "items"}:
        if submitted.get(key) != expected.get(key):
            raise ManualAuditError(f"adjudication immutable field changed: {key}")
    attestation = submitted.get("adjudicator_attestation")
    if not isinstance(attestation, dict) or set(attestation) != set(expected["adjudicator_attestation"]):
        raise ManualAuditError("adjudicator attestation fields changed")
    adjudicator_id = str(attestation.get("adjudicator_id", "")).strip()
    if not adjudicator_id:
        raise ManualAuditError("adjudicator_id is required")
    _parse_completed_at(attestation.get("completed_at"), label="adjudicator")
    if attestation.get("did_not_view_unblinding_or_evaluator_output") is not True:
        raise ManualAuditError("adjudicator must attest that protected outputs were not viewed")
    items = submitted.get("items")
    expected_items = expected["items"]
    if not isinstance(items, list) or len(items) != len(expected_items):
        raise ManualAuditError("adjudication must preserve every disagreement")
    decisions: dict[str, dict[str, str]] = {}
    for index, (item, expected_item) in enumerate(zip(items, expected_items, strict=True)):
        if not isinstance(item, dict) or set(item) != set(expected_item):
            raise ManualAuditError(f"adjudication item {index} fields changed")
        for key in set(expected_item) - {"response"}:
            if item.get(key) != expected_item.get(key):
                raise ManualAuditError(f"adjudication item {index} immutable field changed: {key}")
        response = item.get("response")
        if not isinstance(response, dict) or set(response) != {"verdict", "rationale"}:
            raise ManualAuditError(f"adjudication item {index} response fields changed")
        verdict = str(response.get("verdict", ""))
        rationale = str(response.get("rationale", "")).strip()
        if verdict not in ALLOWED_VERDICTS or not rationale:
            raise ManualAuditError(f"adjudication item {index} is incomplete")
        decisions[str(item["audit_id"])] = {"verdict": verdict, "rationale": rationale}
    return adjudicator_id, decisions


def _evaluator_verdicts(stage2: Path, protocol: Stage2Protocol) -> dict[str, str]:
    unblinding = read_json(stage2 / "evaluations" / "unblinding.json")
    result: dict[str, str] = {}
    for item in unblinding.get("mapping", []):
        evaluation = StudyRegistryEvaluation.model_validate(
            read_json(stage2 / "evaluations" / str(item["blind_id"]) / "evaluation.json")
        )
        registry_id = str(item["registry_id"])
        for judgment in evaluation.claims:
            audit_id, _ = _audit_id(protocol.protocol_id, registry_id, judgment.claim_id)
            result[audit_id] = judgment.verdict.value
    if len(result) != 72:
        raise ManualAuditError("could not reconstruct all 72 protected evaluator verdicts")
    return result


def _manual_gate_statistics(
    evaluator: dict[str, str],
    final_decisions: dict[str, dict[str, str]],
    *,
    planned_unsupported_quota: int,
    threshold: float,
) -> dict[str, object]:
    available_unsupported = {
        audit_id for audit_id, verdict in evaluator.items() if verdict == "unsupported"
    }
    sampled_ids = set(final_decisions)
    sampled_unsupported = available_unsupported & sampled_ids
    if len(available_unsupported) < planned_unsupported_quota:
        missing = available_unsupported - sampled_ids
        if missing:
            raise ManualAuditError(
                "manual audit omitted available evaluator-unsupported claims: "
                + ", ".join(sorted(missing))
            )
    false_positive_ids = sorted(
        audit_id
        for audit_id in sampled_unsupported
        if final_decisions[audit_id]["verdict"] != "unsupported"
    )
    sampled_non_unsupported = sampled_ids - sampled_unsupported
    false_negative_ids = sorted(
        audit_id
        for audit_id in sampled_non_unsupported
        if final_decisions[audit_id]["verdict"] == "unsupported"
    )
    false_positive_rate = (
        len(false_positive_ids) / len(sampled_unsupported)
        if sampled_unsupported
        else None
    )
    threshold_passed = (
        false_positive_rate is not None and false_positive_rate <= threshold
    )
    return {
        "planned_unsupported_quota": planned_unsupported_quota,
        "available_evaluator_unsupported": len(available_unsupported),
        "audited_evaluator_unsupported": len(sampled_unsupported),
        "audit_false_positive_count": len(false_positive_ids),
        "audit_false_positive_rate": false_positive_rate,
        "audit_false_positive_ids": false_positive_ids,
        "audited_evaluator_non_unsupported": len(sampled_non_unsupported),
        "audit_false_negative_count": len(false_negative_ids),
        "audit_false_negative_rate": (
            len(false_negative_ids) / len(sampled_non_unsupported)
            if sampled_non_unsupported
            else None
        ),
        "audit_false_negative_ids": false_negative_ids,
        "false_positive_threshold": threshold,
        "threshold_passed": threshold_passed,
    }


def finalize_manual_audit(project: Path, adjudication_path: Path | None = None) -> dict[str, object]:
    project = project.resolve()
    stage2, protocol = _load_protocol(project)
    sample_path, sample, summary = _sample_from_summary(stage2)
    submission_root = stage2 / "manual_audit"
    manifest_path = submission_root / "submission_manifest.json"
    if not manifest_path.is_file():
        raise ManualAuditError("two independent auditor submissions have not been frozen")
    submission = read_json(manifest_path)
    packet_1_path = submission_root / "auditor_1.json"
    packet_2_path = submission_root / "auditor_2.json"
    if sha256_file(packet_1_path) != submission["stored_packet_sha256"]["auditor_1"]:
        raise ManualAuditError("stored auditor_1 packet changed")
    if sha256_file(packet_2_path) != submission["stored_packet_sha256"]["auditor_2"]:
        raise ManualAuditError("stored auditor_2 packet changed")
    source_hash = sha256_file(sample_path)
    auditor_1_id, decisions_1 = validate_auditor_packet_data(
        sample, read_json(packet_1_path), "auditor_1", source_hash
    )
    auditor_2_id, decisions_2 = validate_auditor_packet_data(
        sample, read_json(packet_2_path), "auditor_2", source_hash
    )
    if auditor_1_id == auditor_2_id:
        raise ManualAuditError("auditor identities are not independent")
    disagreements = list(submission.get("disagreement_audit_ids", []))
    adjudicated: dict[str, dict[str, str]] = {}
    adjudication_hash: str | None = None
    adjudicator_hash: str | None = None
    if disagreements:
        if adjudication_path is None:
            raise ManualAuditError("a completed blinded adjudication packet is required")
        expected_path = Path(str(submission["adjudication_packet"]))
        expected = read_json(expected_path)
        if sha256_file(expected_path) != submission["adjudication_packet_sha256"]:
            raise ManualAuditError("frozen adjudication template changed")
        submitted = read_json(adjudication_path.resolve())
        adjudicator_id, adjudicated = _validate_adjudication(expected, submitted)
        adjudication_hash = sha256_file(adjudication_path.resolve())
        adjudicator_hash = hashlib.sha256(adjudicator_id.encode("utf-8")).hexdigest()
    final_decisions: dict[str, dict[str, str]] = {}
    for audit_id in decisions_1:
        if decisions_1[audit_id]["verdict"] == decisions_2[audit_id]["verdict"]:
            final_decisions[audit_id] = {
                "verdict": decisions_1[audit_id]["verdict"],
                "rationale": "Independent auditors agreed. "
                + decisions_1[audit_id]["rationale"],
                "resolution": "auditor_agreement",
            }
        else:
            decision = adjudicated.get(audit_id)
            if decision is None:
                raise ManualAuditError(f"missing adjudication for {audit_id}")
            final_decisions[audit_id] = {
                **decision,
                "resolution": "blinded_adjudication",
            }
    evaluator = _evaluator_verdicts(stage2, protocol)
    planned_quota = (
        protocol.manual_audit.target_unsupported_per_task_arm
        * len(protocol.tasks)
        * 2
    )
    gate = _manual_gate_statistics(
        evaluator,
        final_decisions,
        planned_unsupported_quota=planned_quota,
        threshold=protocol.manual_audit.false_positive_threshold,
    )
    agreement_count = sum(
        decisions_1[item]["verdict"] == decisions_2[item]["verdict"]
        for item in decisions_1
    )
    result = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
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
        "primary_analysis_interpretable": gate["threshold_passed"],
        "analysis_status": (
            "manual_audit_complete_primary_analysis_unlocked"
            if gate["threshold_passed"]
            else "manual_audit_complete_primary_analysis_invalid"
        ),
        "final_decisions": [
            {"audit_id": audit_id, **final_decisions[audit_id]}
            for audit_id in sorted(final_decisions)
        ],
    }
    result_path = stage2 / "manual_audit_result.json"
    write_json_atomic(result_path, result)
    final_analysis: dict[str, Any] = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "created_at": utc_now(),
        "analysis_status": result["analysis_status"],
        "primary_analysis_interpretable": result["primary_analysis_interpretable"],
        "manual_audit_result_sha256": sha256_file(result_path),
        "manual_gate": gate,
        "protected_evaluation_summary_sha256": sha256_file(
            stage2 / "evaluations" / "summary.json"
        ),
    }
    if gate["threshold_passed"]:
        final_analysis["primary_metric"] = "unsupported_claim_rate"
        final_analysis["arm_metrics"] = summary["arm_metrics"]
        final_analysis["paired_analysis"] = summary["paired_analysis"]
    else:
        final_analysis["reason"] = (
            "The adjudicated audit false-positive rate exceeded the preregistered 0.15 threshold."
        )
    write_json_atomic(stage2 / "final_analysis.json", final_analysis)
    manual_manifest = read_json(stage2 / "manual_audit_manifest.json")
    manual_manifest.update(
        {
            "status": "complete",
            "completed_at": result["completed_at"],
            "manual_audit_result_sha256": sha256_file(result_path),
            "primary_analysis_interpretable": result["primary_analysis_interpretable"],
        }
    )
    write_json_atomic(stage2 / "manual_audit_manifest.json", manual_manifest)
    return result


def audit_manual_audit(project: Path) -> dict[str, object]:
    project = project.resolve()
    stage2, protocol = _load_protocol(project)
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = passed
        if not passed:
            violations.append(message)

    try:
        sample_path, sample, _ = _sample_from_summary(stage2)
        check("sample_hash_frozen", True, "manual sample is missing or changed")
        check(
            "sample_exactly_48",
            len(sample.get("items", [])) == protocol.manual_audit.total_claims,
            "manual sample is not exactly 48 claims",
        )
        selection = read_json(stage2 / "manual_audit_selection_audit.json")
        check(
            "all_available_unsupported_sampled",
            not selection.get("audit_all_available_unsupported")
            or selection.get("selected_evaluator_unsupported")
            == selection.get("available_evaluator_unsupported"),
            "not all available evaluator-unsupported claims were sampled",
        )
        packet_manifest = read_json(sample_path.parent / "independent-packets" / "packet_manifest.json")
        check(
            "auditor_packets_match_sample",
            packet_manifest.get("source_sample_sha256") == sha256_file(sample_path),
            "auditor packets do not match the frozen sample",
        )
    except Exception as exc:
        check("sample_loads", False, str(exc))

    result_path = stage2 / "manual_audit_result.json"
    if result_path.is_file():
        result = read_json(result_path)
        status = str(result.get("analysis_status"))
        complete = True
        check(
            "result_has_48_claims",
            result.get("audited_claims") == protocol.manual_audit.total_claims,
            "manual result does not contain 48 audited claims",
        )
        check(
            "final_analysis_present",
            (stage2 / "final_analysis.json").is_file(),
            "final analysis artifact is missing",
        )
    elif (stage2 / "manual_audit" / "submission_manifest.json").is_file():
        submission = read_json(stage2 / "manual_audit" / "submission_manifest.json")
        status = str(submission.get("status"))
        complete = False
    else:
        status = "awaiting_two_independent_auditors"
        complete = False
    return {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "audited_at": utc_now(),
        "passed": bool(checks) and all(checks.values()),
        "complete": complete,
        "status": status,
        "checks": checks,
        "violations": violations,
    }
