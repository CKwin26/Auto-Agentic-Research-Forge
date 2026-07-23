from __future__ import annotations

from pathlib import Path

from .models import utc_now
from .storage import read_json, sha256_file, write_json_atomic
from .study import (
    audit_stage2_protocol,
    load_stage2_protocol_for_audit,
    stage2_audit_allows_completed_historical_read,
)
from .study_models import StudyArm
from .study_runner import _stable_tree_hash


def audit_publication_pairs(project: Path, *, persist: bool = False) -> dict[str, object]:
    project = project.resolve()
    stage2 = project / "stage2"
    violations: list[str] = []
    protocol_audit = audit_stage2_protocol(project)
    historical_read = stage2_audit_allows_completed_historical_read(protocol_audit)
    if not protocol_audit.passed and not historical_read:
        violations.append("frozen Stage 2 protocol failed audit")
    protocol, legacy_protocol_profile = load_stage2_protocol_for_audit(project)
    baseline_cells = [cell for cell in protocol.cells if cell.arm == StudyArm.BASELINE]
    treatments = {
        (cell.task_id, cell.seed): cell
        for cell in protocol.cells
        if cell.arm == StudyArm.TREATMENT
    }
    shared_complete = 0
    branch_complete = 0
    pair_rows: list[dict[str, object]] = []
    if list(stage2.rglob("invalid.json")):
        violations.append("one or more formal artifacts are marked invalid")

    for baseline in baseline_cells:
        sequence = baseline.sequence
        treatment = treatments[(baseline.task_id, baseline.seed)]
        pair_key = f"{baseline.task_id}--seed-{baseline.seed}"
        pair_dir = stage2 / "shared" / "pairs" / f"{sequence:02d}"
        row: dict[str, object] = {
            "pair_sequence": sequence,
            "pair_key": pair_key,
            "shared_complete": False,
            "baseline_complete": False,
            "treatment_complete": False,
            "checks_passed": True,
        }
        local: list[str] = []
        shared_path = pair_dir / "complete.json"
        if shared_path.is_file():
            shared = read_json(shared_path)
            shared_complete += 1
            row["shared_complete"] = True
            required = {
                "controller_report.json": shared.get("controller_report_sha256"),
                "shared_registry.json": shared.get("shared_registry_sha256"),
                "shared_normalization.json": shared.get("shared_normalization_sha256"),
                "structural_audit.json": shared.get("structural_audit_sha256"),
                "pair_manifest.json": shared.get("pair_manifest_sha256"),
                "shared_telemetry.json": shared.get("telemetry_sha256"),
            }
            for name, expected in required.items():
                path = pair_dir / name
                if not path.is_file() or sha256_file(path) != expected:
                    local.append(f"shared hash mismatch: {name}")
            evidence = pair_dir / "evidence"
            if not evidence.is_dir() or _stable_tree_hash(evidence) != shared.get(
                "evidence_tree_sha256"
            ):
                local.append("shared evidence tree hash mismatch")
            if shared.get("pair_key") != pair_key:
                local.append("shared pair key mismatch")
            expected_output = (
                Path(protocol and read_json(stage2 / "backbone_manifest.json")["controller_run_root"])
                / f"p{sequence:02d}"
            ).resolve()
            if Path(str(shared.get("controller_output"))).resolve() != expected_output:
                local.append("shared controller output path mismatch")
        else:
            shared = {}

        binding_hashes: list[tuple[str, str]] = []
        execution_completed: list[str] = []
        for arm, cell, code in (
            ("baseline", baseline, "b"),
            ("treatment", treatment, "t"),
        ):
            cell_dir = stage2 / "r" / code / f"{sequence:02d}"
            binding_path = cell_dir / "shared_artifact_binding.json"
            if binding_path.is_file():
                binding = read_json(binding_path)
                binding_hashes.append(
                    (
                        str(binding.get("shared_registry_sha256")),
                        str(binding.get("claim_payload_sha256")),
                    )
                )
                if shared and binding.get("shared_registry_sha256") != shared.get(
                    "shared_registry_sha256"
                ):
                    local.append(f"{arm} shared registry binding mismatch")
            complete_path = cell_dir / "complete.json"
            if not complete_path.is_file():
                continue
            complete = read_json(complete_path)
            branch_complete += 1
            row[f"{arm}_complete"] = True
            execution_completed.append(arm)
            telemetry_path = cell_dir / "telemetry.json"
            if (
                not telemetry_path.is_file()
                or sha256_file(telemetry_path) != complete.get("telemetry_hash")
                or int(complete.get("token_count", 0)) <= 0
                or int(complete.get("model_call_count", 0)) <= 0
                or float(complete.get("monetary_cost_usd", -1.0)) < 0.0
            ):
                local.append(f"{arm} telemetry contract failed")
            if shared:
                if complete.get("controller_report_hash") != shared.get(
                    "controller_report_sha256"
                ):
                    local.append(f"{arm} controller report hash mismatch")
                if complete.get("source_controller_report_hash") != shared.get(
                    "source_controller_report_sha256"
                ):
                    local.append(f"{arm} source controller report hash mismatch")

        if len(binding_hashes) == 2 and len(set(binding_hashes)) != 1:
            local.append("baseline and treatment shared claim bindings differ")
        execution_path = pair_dir / "branch_execution.json"
        if execution_path.is_file():
            execution = read_json(execution_path)
            expected_order = protocol.pair_branch_order[pair_key]
            if execution.get("frozen_order") != expected_order:
                local.append("branch execution frozen order mismatch")
            events = [
                str(item.get("arm"))
                for item in execution.get("events", [])
                if item.get("status") == "completed"
            ]
            expected_events = (
                ["baseline", "treatment"]
                if expected_order == "baseline_first"
                else ["treatment", "baseline"]
            )
            if len(execution_completed) == 2 and events != expected_events:
                local.append("completed branch events violate frozen order")
        elif execution_completed:
            local.append("branch completion exists without execution trace")

        if local:
            row["checks_passed"] = False
            row["violations"] = local
            violations.extend(f"pair {sequence:02d}: {item}" for item in local)
        pair_rows.append(row)

    complete = shared_complete == 40 and branch_complete == 80
    result = {
        "schema_version": 1,
        "audited_at": utc_now(),
        "protocol_id": protocol.protocol_id,
        "protocol_audit_passed": protocol_audit.passed,
        "historical_live_controller_drift": historical_read,
        "legacy_protocol_profile": legacy_protocol_profile,
        "passed": not violations,
        "complete": complete and not violations,
        "shared_complete": shared_complete,
        "expected_shared": 40,
        "branch_complete": branch_complete,
        "expected_branches": 80,
        "violations": violations,
        "pairs": pair_rows,
    }
    if persist:
        write_json_atomic(stage2 / "publication_pair_audit.json", result)
    return result
