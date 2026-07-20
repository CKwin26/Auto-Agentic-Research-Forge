from __future__ import annotations

import json
import hashlib
import math
import os
import random
import shutil
import statistics
import time
from datetime import datetime
from pathlib import Path

from .agent_runtime import (
    agent_telemetry_summary,
    backend_status,
    configure_agent_telemetry,
    generate_study_finalizer,
    judge_study_claims,
    model_name,
)
from .benchmark_models import BenchmarkReport
from .contracts import transition
from .models import RunRecord, Stage, utc_now
from .research_loop import run_loop_benchmark
from .runtime import ExecutionRuntime, RuntimeOptions, build_runtime
from .storage import (
    load_jsonl,
    load_state,
    read_json,
    save_state,
    sha256_file,
    write_json_atomic,
)
from .study import audit_stage2_protocol
from .study_models import (
    Stage2BackboneManifest,
    Stage2BaselineAudit,
    Stage2Cell,
    Stage2EvaluationAudit,
    Stage2Protocol,
    Stage2TreatmentAudit,
    ClaimFailureMode,
    ClaimVerdict,
    SemanticClaimJudgment,
    SemanticClaimJudgmentBatch,
    StudyArm,
    StudyClaim,
    StudyClaimEvaluation,
    StudyClaimRegistry,
    StudyClaimType,
    StudyGateTrace,
    StudyRegistryEvaluation,
)


def _cell_dir(stage2: Path, cell: Stage2Cell) -> Path:
    arm = "b" if cell.arm == StudyArm.BASELINE else "t"
    pair_count = len(read_json(stage2 / "protocol.json")["cells"]) // 2
    arm_sequence = (
        cell.sequence if cell.arm == StudyArm.BASELINE else cell.sequence - pair_count
    )
    return stage2 / "r" / arm / f"{arm_sequence:02d}"


def _runtime_from_backbone(backbone: Stage2BackboneManifest) -> ExecutionRuntime:
    limits = backbone.runtime.limits
    runtime = build_runtime(
        RuntimeOptions(
            kind="docker",
            image=backbone.runtime.image,
            cpus=float(limits.get("cpus", 1.0)),
            memory_mb=int(limits.get("memory_mb", 2048)),
            pids_limit=int(limits.get("pids_limit", 256)),
            tmpfs_mb=int(limits.get("tmpfs_mb", 512)),
            max_output_mb=int(limits.get("max_output_mb", 256)),
        )
    )
    attestation = runtime.attestation(evaluator_separated=True)
    if attestation.get("image_id") != backbone.runtime.image_id:
        raise ValueError("current Docker image ID differs from the frozen Stage 2 image")
    if attestation.get("capabilities") != backbone.runtime.capabilities:
        raise ValueError("current Docker capability manifest differs from the frozen Stage 2 image")
    return runtime


def _verify_codex_backbone(backbone: Stage2BackboneManifest) -> None:
    status: dict[str, object] = {}
    for _ in range(3):
        status = backend_status()
        if status.get("codex_authenticated") is True:
            break
    if status.get("codex_authenticated") is not True:
        raise ValueError("Codex authentication is unavailable")
    if model_name() != backbone.model:
        raise ValueError("current Codex model differs from the frozen Stage 2 model")
    if status.get("codex_sdk_version") != backbone.codex_sdk_version:
        raise ValueError("current openai-codex SDK differs from the frozen Stage 2 version")
    if status.get("codex_account_type") != backbone.codex_account_type:
        raise ValueError("current Codex account type differs from the frozen Stage 2 binding")
    if status.get("provider_name") != backbone.provider_name:
        raise ValueError("current Codex provider differs from the frozen Stage 2 binding")
    if status.get("provider_base_url") != backbone.provider_base_url:
        raise ValueError("current Codex provider base URL differs from the frozen Stage 2 binding")
    if status.get("provider_config_hash") != backbone.provider_config_hash:
        raise ValueError("current Codex provider config differs from the frozen Stage 2 binding")
    if status.get("codex_account_type") != backbone.credential_mode:
        raise ValueError("current Codex credential mode differs from the frozen Stage 2 binding")
    if (
        status.get("provider_billing_contract_hash")
        != backbone.provider_billing_contract_hash
    ):
        raise ValueError("current provider billing contract differs from the frozen binding")
    if status.get("provider_billing_group") != backbone.provider_billing_group:
        raise ValueError("current provider billing group differs from the frozen binding")


def _controller_output(backbone: Stage2BackboneManifest, cell: Stage2Cell) -> Path | None:
    arm = "b" if cell.arm == StudyArm.BASELINE else "t"
    pair_count = len(backbone.task_order) * len(backbone.seeds)
    arm_sequence = (
        cell.sequence if cell.arm == StudyArm.BASELINE else cell.sequence - pair_count
    )
    shared_output = Path(backbone.controller_run_root) / f"p{arm_sequence:02d}"
    if (shared_output / "loop_manifest.json").is_file():
        return shared_output
    output = Path(backbone.controller_run_root) / f"{arm}{arm_sequence:02d}"
    if not (output / "loop_manifest.json").is_file():
        return None
    return output


def _shared_pair_dir(stage2: Path, protocol: Stage2Protocol, cell: Stage2Cell) -> Path:
    pair_count = len(protocol.tasks) * len(protocol.seeds)
    sequence = cell.sequence if cell.arm == StudyArm.BASELINE else cell.sequence - pair_count
    return stage2 / "shared" / "pairs" / f"{sequence:02d}"


def _rebind_registry_to_cell(
    registry: StudyClaimRegistry, protocol: Stage2Protocol, cell: Stage2Cell
) -> StudyClaimRegistry:
    run_id = f"agent-{cell.cell_id}"
    claims = [
        claim.model_copy(
            deep=True,
            update={
                "run_id": run_id,
                "arm": cell.arm,
                "task_pack": cell.task_id,
                "seed": cell.seed,
            },
        )
        for claim in registry.claims
    ]
    digest = sha256_file_from_value([claim.model_dump(mode="json") for claim in claims])
    return StudyClaimRegistry(
        registry_id=f"registry-{digest[:12]}",
        protocol_id=protocol.protocol_id,
        cell_id=cell.cell_id,
        run_id=run_id,
        arm=cell.arm,
        task_pack=cell.task_id,
        seed=cell.seed,
        final_output_text="\n".join(claim.claim_text for claim in claims),
        claims=claims,
    )


def _copy_evidence_artifact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        if sha256_file(destination) != sha256_file(source):
            raise ValueError(f"ingested evidence changed: {destination}")
        return
    shutil.copy2(source, destination)


def _experiment_packet(
    project: Path,
    benchmark_project: Path,
    evidence_dir: Path,
) -> list[dict[str, object]]:
    packet: list[dict[str, object]] = []
    for evidence in load_jsonl(benchmark_project / "evidence.jsonl"):
        run_id = str(evidence["run_id"])
        run_dir = benchmark_project / "runs" / run_id
        record_path = run_dir / "record.json"
        record = RunRecord.model_validate(read_json(record_path))
        ingested_record = evidence_dir / run_id / "record.json"
        _copy_evidence_artifact(record_path, ingested_record)
        artifacts = [ingested_record.relative_to(project).as_posix()]
        for trial in record.trials:
            metrics_path = run_dir / f"trial-{trial.index:03d}" / "metrics.json"
            if metrics_path.is_file():
                ingested_metrics = evidence_dir / run_id / f"trial-{trial.index:03d}-metrics.json"
                _copy_evidence_artifact(metrics_path, ingested_metrics)
                artifacts.append(ingested_metrics.relative_to(project).as_posix())
        packet.append(
            {
                "run_id": run_id,
                "is_baseline": record.is_baseline,
                "valid": record.valid,
                "verdict": record.verdict,
                "aggregate_metrics": record.aggregate_metrics,
                "metric_stddev": record.metric_stddev,
                "improvement": record.improvement,
                "isolation_verified": record.isolation_verified,
                "artifacts": artifacts,
            }
        )
    if not packet:
        raise ValueError("controller project has no experiment evidence")
    return packet


def _source_packet(stage2: Path) -> list[dict[str, object]]:
    return [
        read_json(path)
        for path in sorted((stage2 / "evidence" / "sources").glob("*.json"))
    ]


def _selected_novelty(stage2: Path) -> dict[str, object]:
    review = read_json(stage2 / "evidence" / "review.json")
    synthesis = review.get("synthesis")
    if not isinstance(synthesis, dict):
        raise ValueError("frozen review has no synthesis")
    candidates = synthesis.get("novelty_candidates")
    if not isinstance(candidates, list):
        raise ValueError("frozen review has no novelty candidates")
    selected = [item for item in candidates if item.get("novelty_id") == "novelty-02"]
    if len(selected) != 1:
        raise ValueError("frozen review does not resolve novelty-02 exactly once")
    return dict(selected[0])


def _finalizer_prompt(
    project: Path,
    stage2: Path,
    protocol: Stage2Protocol,
    cell: Stage2Cell,
    report: BenchmarkReport,
    benchmark_project: Path,
    evidence_dir: Path,
    *,
    present_treatment_as_baseline: bool = False,
) -> tuple[str, list[dict[str, object]]]:
    experiment_packet = _experiment_packet(project, benchmark_project, evidence_dir)
    task = read_json(stage2 / "task_packs" / cell.task_id / "task.json")
    payload = {
        "protocol_id": protocol.protocol_id,
        "cell": {
            "run_id": f"agent-{cell.cell_id}",
            "arm": cell.arm.value,
            "task_pack": cell.task_id,
            "seed": cell.seed,
        },
        "task_specification": {
            "task_id": task["task_id"],
            "description": task["description"],
            "primary_metric": task["primary_metric"],
            "direction": task["direction"],
            "baseline_score": task["baseline_score"],
            "target_score": task["target_score"],
        },
        "controller_report": report.model_dump(mode="json"),
        "allowed_experiment_evidence": experiment_packet,
        "frozen_literature_sources": _source_packet(stage2),
        "selected_bounded_novelty": _selected_novelty(stage2),
        "instruction": (
            "Draft the initial factual conclusion registry. This is the shared initial finalizer; "
            "do not run or anticipate a verification gate."
        ),
    }
    if present_treatment_as_baseline:
        if cell.arm != StudyArm.TREATMENT:
            raise ValueError("only a treatment cell can use baseline-shaped finalizer presentation")
        arm_sequence = cell.sequence - (len(protocol.tasks) * len(protocol.seeds))
        treatment_prefix = f"stage2/r/t/{arm_sequence:02d}/"
        baseline_prefix = f"stage2/r/b/{arm_sequence:02d}/"
        treatment_output = str(benchmark_project)
        baseline_output = treatment_output.replace(
            f"{os.sep}t{arm_sequence:02d}{os.sep}",
            f"{os.sep}b{arm_sequence:02d}{os.sep}",
        )

        def mask(value: object) -> object:
            if isinstance(value, dict):
                return {key: mask(item) for key, item in value.items()}
            if isinstance(value, list):
                return [mask(item) for item in value]
            if isinstance(value, str):
                return value.replace(treatment_prefix, baseline_prefix).replace(
                    treatment_output, baseline_output
                )
            return value

        payload = mask(payload)
        assert isinstance(payload, dict)
        payload["cell"] = {
            "run_id": f"agent-baseline--{cell.task_id}--seed-{cell.seed}",
            "arm": StudyArm.BASELINE.value,
            "task_pack": cell.task_id,
            "seed": cell.seed,
        }
    return json.dumps(payload, ensure_ascii=False, indent=2), experiment_packet


def _canonical_finalizer_metric_values(
    claim: object,
    experiment_packet: list[dict[str, object]] | None,
    *,
    normalization_log: list[dict[str, object]] | None = None,
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for item in getattr(claim, "metric_values"):
        grouped.setdefault(item.name, []).append(float(item.value))
    experiment_run_id = getattr(claim, "experiment_run_id")
    experiments = {
        str(item.get("run_id")): item for item in (experiment_packet or [])
    }
    expected = _verifiable_metric_values(experiments.get(experiment_run_id or ""))
    resolved: dict[str, float] = {}
    for name, values in grouped.items():
        unique: list[float] = []
        for value in values:
            if not any(math.isclose(value, prior, rel_tol=1e-12, abs_tol=1e-12) for prior in unique):
                unique.append(value)
        if len(unique) == 1:
            resolved[name] = unique[0]
            continue
        expected_value = expected.get(name)
        matches = (
            [
                value
                for value in unique
                if math.isclose(value, expected_value, rel_tol=1e-12, abs_tol=1e-12)
            ]
            if expected_value is not None
            else []
        )
        if len(matches) != 1:
            raise ValueError(
                f"conflicting finalizer metric {name!r} cannot be uniquely resolved "
                "from the linked protected experiment run"
            )
        resolved[name] = matches[0]
        if normalization_log is not None:
            normalization_log.append(
                {
                    "claim_id": getattr(claim, "claim_id"),
                    "experiment_run_id": experiment_run_id,
                    "metric_name": name,
                    "action": "retain_unique_value_matching_linked_protected_run",
                    "retained_value": matches[0],
                    "removed_cross_run_values": [value for value in unique if value != matches[0]],
                    "claim_text_changed": False,
                }
            )
    return resolved


def _registry_from_output(
    protocol: Stage2Protocol,
    cell: Stage2Cell,
    output: object,
    experiment_packet: list[dict[str, object]] | None = None,
    *,
    normalization_log: list[dict[str, object]] | None = None,
) -> StudyClaimRegistry:
    run_id = f"agent-{cell.cell_id}"
    draft_claims = getattr(output, "claims")
    claims = [
        StudyClaim(
            claim_id=claim.claim_id,
            run_id=run_id,
            arm=cell.arm,
            task_pack=cell.task_id,
            seed=cell.seed,
            claim_type=claim.claim_type,
            claim_text=claim.claim_text,
            source_ids=claim.source_ids,
            experiment_run_id=claim.experiment_run_id,
            metric_values=_canonical_finalizer_metric_values(
                claim,
                experiment_packet,
                normalization_log=normalization_log,
            ),
            artifact_paths=claim.artifact_paths,
        )
        for claim in draft_claims
    ]
    digest = sha256_file_from_value([claim.model_dump(mode="json") for claim in claims])
    return StudyClaimRegistry(
        registry_id=f"registry-{digest[:12]}",
        protocol_id=protocol.protocol_id,
        cell_id=cell.cell_id,
        run_id=run_id,
        arm=cell.arm,
        task_pack=cell.task_id,
        seed=cell.seed,
        final_output_text="\n".join(claim.claim_text for claim in claims),
        claims=claims,
    )


def _unblind_treatment_registry_artifacts(
    registry: StudyClaimRegistry,
    cell: Stage2Cell,
    protocol: Stage2Protocol,
) -> StudyClaimRegistry:
    if cell.arm != StudyArm.TREATMENT:
        return registry
    arm_sequence = cell.sequence - (len(protocol.tasks) * len(protocol.seeds))
    baseline_prefix = f"stage2/r/b/{arm_sequence:02d}/"
    treatment_prefix = f"stage2/r/t/{arm_sequence:02d}/"
    changed = False
    claims: list[StudyClaim] = []
    for claim in registry.claims:
        updated = claim.model_copy(deep=True)
        paths = [path.replace(baseline_prefix, treatment_prefix) for path in claim.artifact_paths]
        if paths != claim.artifact_paths:
            updated.artifact_paths = paths
            changed = True
        claims.append(updated)
    return _registry_with_claims(registry, claims) if changed else registry


def sha256_file_from_value(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verifiable_metric_values(experiment: dict[str, object] | None) -> dict[str, float]:
    if not experiment:
        return {}
    values = {
        str(name): float(value)
        for name, value in dict(experiment.get("aggregate_metrics", {})).items()
    }
    improvement = experiment.get("improvement")
    if improvement is not None:
        values["improvement"] = float(improvement)
    return values


def audit_registry_structure(
    project: Path,
    stage2: Path,
    registry: StudyClaimRegistry,
    experiment_packet: list[dict[str, object]],
    *,
    require_experiment_claim: bool = True,
) -> dict[str, object]:
    sources = {item["source_id"]: item for item in _source_packet(stage2)}
    novelty_source_ids = set(_selected_novelty(stage2).get("source_ids", []))
    experiments = {str(item["run_id"]): item for item in experiment_packet}
    claim_checks: dict[str, dict[str, bool]] = {}
    violations: list[str] = []
    has_experiment_claim = False

    for claim in registry.claims:
        checks: dict[str, bool] = {}
        if claim.claim_type in {StudyClaimType.LITERATURE, StudyClaimType.NOVELTY}:
            checks["source_ids_present"] = bool(claim.source_ids)
            checks["source_ids_resolve"] = bool(claim.source_ids) and all(
                source_id in sources for source_id in claim.source_ids
            )
            checks["experiment_support_absent"] = (
                claim.experiment_run_id is None
                and not claim.metric_values
                and not claim.artifact_paths
            )
            if claim.claim_type == StudyClaimType.NOVELTY:
                checks["novelty_sources_bound"] = bool(claim.source_ids) and set(
                    claim.source_ids
                ).issubset(novelty_source_ids)
        else:
            has_experiment_claim = True
            experiment = experiments.get(claim.experiment_run_id or "")
            checks["run_id_present"] = bool(claim.experiment_run_id)
            checks["run_resolves"] = experiment is not None
            run_valid = bool(experiment and experiment.get("valid") is True)
            run_invalid = bool(experiment and experiment.get("valid") is False)
            checks["run_status_present"] = run_valid or run_invalid
            checks["run_isolated"] = bool(
                experiment and experiment.get("isolation_verified") is True
            )
            if run_valid:
                checks["metrics_present"] = bool(claim.metric_values)
                expected_metrics = _verifiable_metric_values(experiment)
                checks["metrics_exact"] = bool(claim.metric_values) and all(
                    name in expected_metrics
                    and math.isclose(
                        value,
                        float(expected_metrics[name]),
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                    for name, value in claim.metric_values.items()
                )
            else:
                checks["invalid_status_stated"] = run_invalid and (
                    "invalid" in claim.claim_text.casefold()
                )
                checks["invalid_metrics_absent"] = run_invalid and not claim.metric_values
            allowed_artifacts = set(experiment.get("artifacts", [])) if experiment else set()
            checks["artifacts_present"] = bool(claim.artifact_paths)
            checks["artifacts_allowed"] = bool(claim.artifact_paths) and set(
                claim.artifact_paths
            ).issubset(allowed_artifacts)
            checks["artifacts_exist"] = bool(claim.artifact_paths) and all(
                (project / relative).is_file() for relative in claim.artifact_paths
            )
            checks["source_support_absent"] = not claim.source_ids
        claim_checks[claim.claim_id] = checks
        for name, passed in checks.items():
            if not passed:
                violations.append(f"{claim.claim_id}: {name}")

    if require_experiment_claim and not has_experiment_claim:
        violations.append("registry has no experiment-grounded conclusion claim")
    return {
        "schema_version": 1,
        "audited_at": utc_now(),
        "passed": not violations,
        "cell_id": registry.cell_id,
        "registry_id": registry.registry_id,
        "claim_checks": claim_checks,
        "violations": violations,
    }


STUDY_ORCHESTRATION_VERSION = "prospective-shared-artifact-pairs-v8"
STUDY_ORCHESTRATION_FILES = ("study_runner.py", "study_models.py")


def _orchestration_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    return {name: sha256_file(root / name) for name in STUDY_ORCHESTRATION_FILES}


def _ensure_orchestration_manifest(stage2: Path, protocol: Stage2Protocol) -> dict[str, object]:
    path = stage2 / "orchestration_manifest.json"
    expected = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "implementation_version": STUDY_ORCHESTRATION_VERSION,
        "file_hashes": _orchestration_hashes(),
    }
    if path.is_file():
        current = read_json(path)
        if current != expected:
            treatment_started = bool(
                list(stage2.glob("r/t/*/cell_manifest.json"))
                or list(stage2.glob("r/t/*/complete.json"))
                or list(stage2.glob("r/t/*/invalid.json"))
            )
            if treatment_started:
                raise ValueError(
                    "Stage 2 orchestration implementation differs from its first-use hash"
                )
            write_json_atomic(path, expected)
            return expected
        return current
    if list(stage2.glob("r/t/*/complete.json")) or list(stage2.glob("r/t/*/invalid.json")):
        raise ValueError("treatment evidence exists without an orchestration implementation manifest")
    write_json_atomic(path, expected)
    return expected


def _cell_orchestration_hash_is_accepted(
    stage2: Path,
    cell: Stage2Cell,
    observed_hash: object,
) -> bool:
    current = sha256_file(stage2 / "orchestration_manifest.json")
    if observed_hash == current:
        return True
    amendments_path = stage2 / "orchestration_amendments.json"
    if not amendments_path.is_file():
        return False
    amendments = read_json(amendments_path).get("amendments", [])
    return any(
        observed_hash == item.get("previous_manifest_sha256")
        and cell.sequence < int(item.get("effective_from_sequence", 0))
        for item in amendments
    )


def _registry_with_claims(
    template: StudyClaimRegistry,
    claims: list[StudyClaim],
) -> StudyClaimRegistry:
    digest = sha256_file_from_value([claim.model_dump(mode="json") for claim in claims])
    return StudyClaimRegistry(
        registry_id=f"registry-{digest[:12]}",
        protocol_id=template.protocol_id,
        cell_id=template.cell_id,
        run_id=template.run_id,
        arm=template.arm,
        task_pack=template.task_pack,
        seed=template.seed,
        final_output_text="\n".join(claim.claim_text for claim in claims),
        claims=claims,
    )


def _normalize_registry_artifacts(
    registry: StudyClaimRegistry,
    experiment_packet: list[dict[str, object]],
) -> tuple[StudyClaimRegistry, list[dict[str, object]]]:
    experiments = {str(item["run_id"]): item for item in experiment_packet}
    claims: list[StudyClaim] = []
    changes: list[dict[str, object]] = []
    for claim in registry.claims:
        normalized = claim.model_copy(deep=True)
        if claim.claim_type == StudyClaimType.EXPERIMENT:
            experiment = experiments.get(claim.experiment_run_id or "")
            # Experiment provenance is carried exclusively by the protected run
            # identifier and artifact paths.  Source IDs are literature-only;
            # removing them here prevents a model from treating a run ID as a
            # citation while preserving the exact empirical evidence binding.
            if normalized.source_ids:
                removed_sources = list(normalized.source_ids)
                normalized.source_ids = []
                changes.append(
                    {
                        "claim_id": claim.claim_id,
                        "normalization": "remove_experiment_source_ids",
                        "removed_source_ids": removed_sources,
                        "claim_text_changed": False,
                        "metric_values_changed": False,
                    }
                )
            # A valid-run conclusion is only evidence-bearing when it declares
            # exact protected metrics.  Model-produced status-only claims add no
            # independent result, cannot satisfy the registry contract, and are
            # removed deterministically instead of making a formal pair depend
            # on stochastic finalizer phrasing.  Invalid-run limitation claims
            # are intentionally retained and audited by their separate rule.
            if experiment and experiment.get("valid") is True and not claim.metric_values:
                changes.append(
                    {
                        "claim_id": claim.claim_id,
                        "normalization": "drop_metricless_valid_experiment_claim",
                        "reason": "valid experiment conclusions require exact protected metrics",
                        "claim_text_changed": True,
                        "metric_values_changed": False,
                    }
                )
                continue
            allowed = set(experiment.get("artifacts", [])) if experiment else set()
            kept = [path for path in claim.artifact_paths if path in allowed]
            removed = [path for path in claim.artifact_paths if path not in allowed]
            if removed:
                normalized.artifact_paths = kept
                changes.append(
                    {
                        "claim_id": claim.claim_id,
                        "normalization": "remove_redundant_cross_run_artifact_links",
                        "removed_artifact_paths": removed,
                        "claim_text_changed": False,
                        "metric_values_changed": False,
                    }
                )
        claims.append(normalized)
    if not changes:
        return registry, []
    return _registry_with_claims(registry, claims), changes


def _claim_evidence_packets(
    stage2: Path,
    registry: StudyClaimRegistry,
    experiment_packet: list[dict[str, object]],
    structural: dict[str, object],
) -> list[dict[str, object]]:
    sources = {str(item["source_id"]): item for item in _source_packet(stage2)}
    experiments = {str(item["run_id"]): item for item in experiment_packet}
    checks = dict(structural.get("claim_checks", {}))
    packets: list[dict[str, object]] = []
    for claim in registry.claims:
        packets.append(
            {
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type.value,
                "claim_text": claim.claim_text,
                "linked_source_ids": claim.source_ids,
                "linked_source_records": [sources[item] for item in claim.source_ids if item in sources],
                "linked_experiment_run_id": claim.experiment_run_id,
                "declared_metric_values": claim.metric_values,
                "linked_experiment_evidence": experiments.get(claim.experiment_run_id or ""),
                "declared_artifact_paths": claim.artifact_paths,
                "deterministic_structural_checks": checks.get(claim.claim_id, {}),
            }
        )
    return packets


def _validate_judgments(
    registry: StudyClaimRegistry,
    batch: SemanticClaimJudgmentBatch,
) -> list[SemanticClaimJudgment]:
    expected = [claim.claim_id for claim in registry.claims]
    actual = [judgment.claim_id for judgment in batch.judgments]
    if actual != expected:
        raise ValueError("claim judgments must preserve every claim ID exactly once and in order")
    claims = {claim.claim_id: claim for claim in registry.claims}
    validated: list[SemanticClaimJudgment] = []
    for judgment in batch.judgments:
        claim = claims[judgment.claim_id]
        normalized = judgment
        if claim.claim_type == StudyClaimType.EXPERIMENT and judgment.supporting_source_ids:
            normalized = judgment.model_copy(update={"supporting_source_ids": []})
        if len(normalized.supporting_source_ids) != len(set(normalized.supporting_source_ids)):
            raise ValueError(f"duplicate supporting source ID in judgment: {judgment.claim_id}")
        if not set(normalized.supporting_source_ids).issubset(claim.source_ids):
            raise ValueError(f"judgment introduced an unlinked source ID: {judgment.claim_id}")
        if (
            normalized.verdict == ClaimVerdict.SUPPORTED
            and claim.claim_type in {StudyClaimType.LITERATURE, StudyClaimType.NOVELTY}
            and not normalized.supporting_source_ids
        ):
            raise ValueError(f"supported literature judgment has no supporting source: {judgment.claim_id}")
        validated.append(normalized)
    return validated


def _apply_structural_verdicts(
    judgments: list[SemanticClaimJudgment],
    structural: dict[str, object],
) -> list[SemanticClaimJudgment]:
    checks = dict(structural.get("claim_checks", {}))
    result: list[SemanticClaimJudgment] = []
    for judgment in judgments:
        claim_checks = dict(checks.get(judgment.claim_id, {}))
        failed = sorted(name for name, passed in claim_checks.items() if passed is not True)
        if failed:
            result.append(
                SemanticClaimJudgment(
                    claim_id=judgment.claim_id,
                    verdict=ClaimVerdict.UNSUPPORTED,
                    rationale="Deterministic evidence checks failed: " + ", ".join(failed),
                    supporting_source_ids=[],
                )
            )
        else:
            result.append(judgment)
    return result


async def _judge_registry(
    stage2: Path,
    registry: StudyClaimRegistry,
    experiment_packet: list[dict[str, object]],
    structural: dict[str, object],
    *,
    prompt_name: str,
    cwd: Path,
    arm_blinded_evidence: bool = False,
) -> list[SemanticClaimJudgment]:
    evidence_packets = _claim_evidence_packets(
        stage2, registry, experiment_packet, structural
    )
    if arm_blinded_evidence:
        blinded_packets: list[dict[str, object]] = []
        for packet in evidence_packets:
            blinded = json.loads(json.dumps(packet, ensure_ascii=False))
            actual_paths = list(blinded.get("declared_artifact_paths", []))
            aliases = {path: f"artifact-{index:03d}" for index, path in enumerate(actual_paths, 1)}
            blinded["declared_artifact_paths"] = [aliases[path] for path in actual_paths]
            experiment = blinded.get("linked_experiment_evidence")
            if isinstance(experiment, dict):
                experiment["artifacts"] = [
                    aliases.get(path, f"linked-artifact-{index:03d}")
                    for index, path in enumerate(experiment.get("artifacts", []), 1)
                ]
            blinded_packets.append(blinded)
        evidence_packets = blinded_packets
    payload = {
        "schema_version": 1,
        "blinding": {
            "arm_removed": True,
            "cell_id_removed": True,
            "task_seed_removed": True,
        },
        "claims": evidence_packets,
        "instruction": "Return one evidence verdict for every claim ID exactly once and in order.",
    }
    batch = await judge_study_claims(
        json.dumps(payload, ensure_ascii=False, indent=2),
        instructions=(stage2 / "prompts" / prompt_name).read_text(encoding="utf-8"),
        cwd=cwd,
    )
    return _apply_structural_verdicts(_validate_judgments(registry, batch), structural)


def _validate_revision(
    gate_input: StudyClaimRegistry,
    revised: StudyClaimRegistry,
    first_pass: list[SemanticClaimJudgment],
) -> None:
    if [claim.claim_id for claim in revised.claims] != [claim.claim_id for claim in gate_input.claims]:
        raise ValueError("treatment revision changed claim IDs, order, or count")
    verdicts = {item.claim_id: item.verdict for item in first_pass}
    original = {claim.claim_id: claim for claim in gate_input.claims}
    for claim in revised.claims:
        before = original[claim.claim_id]
        if claim.claim_type != before.claim_type:
            raise ValueError(f"treatment revision changed claim type: {claim.claim_id}")
        if verdicts[claim.claim_id] == ClaimVerdict.SUPPORTED:
            if claim.model_dump(mode="json") != before.model_dump(mode="json"):
                raise ValueError(f"treatment revision changed a supported claim: {claim.claim_id}")
            continue
        if not set(claim.source_ids).issubset(before.source_ids):
            raise ValueError(f"treatment revision added a source: {claim.claim_id}")
        if claim.experiment_run_id not in {None, before.experiment_run_id}:
            raise ValueError(f"treatment revision added an experiment run: {claim.claim_id}")
        if not set(claim.metric_values).issubset(before.metric_values):
            raise ValueError(f"treatment revision added a metric: {claim.claim_id}")
        if not set(claim.artifact_paths).issubset(before.artifact_paths):
            raise ValueError(f"treatment revision added an artifact: {claim.claim_id}")


async def _revise_registry(
    protocol: Stage2Protocol,
    stage2: Path,
    cell: Stage2Cell,
    gate_input: StudyClaimRegistry,
    experiment_packet: list[dict[str, object]],
    structural: dict[str, object],
    first_pass: list[SemanticClaimJudgment],
    *,
    cwd: Path,
) -> StudyClaimRegistry:
    payload = {
        "schema_version": 1,
        "claims": [claim.model_dump(mode="json") for claim in gate_input.claims],
        "gate_verdicts": [item.model_dump(mode="json") for item in first_pass],
        "linked_evidence": _claim_evidence_packets(stage2, gate_input, experiment_packet, structural),
        "instruction": "Revise unsupported or abstained claims once; preserve all supported claims byte-for-byte.",
    }
    output = await generate_study_finalizer(
        json.dumps(payload, ensure_ascii=False, indent=2),
        instructions=(stage2 / "prompts" / "study_gate_reviser.md").read_text(encoding="utf-8"),
        cwd=cwd,
    )
    metric_normalizations: list[dict[str, object]] = []
    revised = _registry_from_output(
        protocol,
        cell,
        output,
        experiment_packet,
        normalization_log=metric_normalizations,
    )
    write_json_atomic(
        _cell_dir(stage2, cell) / "revision_metric_normalization.json",
        {"schema_version": 1, "changes": metric_normalizations},
    )
    _validate_revision(gate_input, revised, first_pass)
    return revised


async def _ensure_publication_shared_pair(
    project: Path,
    stage2: Path,
    protocol: Stage2Protocol,
    backbone: Stage2BackboneManifest,
    baseline_cell: Stage2Cell,
    treatment_cell: Stage2Cell,
    runtime: ExecutionRuntime,
) -> None:
    """Run the upstream controller and initial finalizer exactly once per pair."""
    if protocol.study_intent != "publication":
        raise ValueError("shared-pair preparation is restricted to publication protocols")
    if baseline_cell.arm != StudyArm.BASELINE or treatment_cell.arm != StudyArm.TREATMENT:
        raise ValueError("shared-pair preparation requires baseline and treatment cells")
    if (baseline_cell.task_id, baseline_cell.seed) != (
        treatment_cell.task_id,
        treatment_cell.seed,
    ):
        raise ValueError("shared-pair cells do not have the same task and seed")
    pair_dir = _shared_pair_dir(stage2, protocol, baseline_cell)
    telemetry_path = pair_dir / "agent_telemetry.jsonl"
    configure_agent_telemetry(telemetry_path, scope="shared_upstream")
    complete_path = pair_dir / "complete.json"
    pair_dir.mkdir(parents=True, exist_ok=True)
    pair_key = f"{baseline_cell.task_id}--seed-{baseline_cell.seed}"
    branch_order = protocol.pair_branch_order[pair_key]
    manifest_path = pair_dir / "pair_manifest.json"
    if not manifest_path.is_file():
        write_json_atomic(
            manifest_path,
            {
                "schema_version": 1,
                "created_at": utc_now(),
                "protocol_id": protocol.protocol_id,
                "pair_key": pair_key,
                "task_id": baseline_cell.task_id,
                "seed": baseline_cell.seed,
                "branch_order": branch_order,
                "counterfactual_source": "shared_run_artifact",
                "upstream_execution_count": 1,
                "initial_finalizer_count": 1,
            },
        )
    if complete_path.is_file():
        complete = read_json(complete_path)
        required = {
            "controller_report.json": complete.get("controller_report_sha256"),
            "shared_registry.json": complete.get("shared_registry_sha256"),
            "structural_audit.json": complete.get("structural_audit_sha256"),
            "pair_manifest.json": complete.get("pair_manifest_sha256"),
            "shared_telemetry.json": complete.get("telemetry_sha256"),
        }
        if all(
            (pair_dir / name).is_file()
            and sha256_file(pair_dir / name) == expected_hash
            for name, expected_hash in required.items()
        ):
            return
        raise ValueError(f"shared pair artifact hash drift: {pair_key}")

    pair_sequence = baseline_cell.sequence
    output = Path(backbone.controller_run_root) / f"p{pair_sequence:02d}"
    if not (output / "report.json").is_file():
        previous_prompt_root = os.environ.get("RESEARCH_FORGE_PROMPT_ROOT")
        os.environ["RESEARCH_FORGE_PROMPT_ROOT"] = str(stage2 / "prompts")
        try:
            output, report, _ = await run_loop_benchmark(
                stage2 / "task_packs" / baseline_cell.task_id,
                strategy="codex",
                seeds=[baseline_cell.seed],
                iterations=1,
                output_root=Path(backbone.controller_run_root),
                runtime=runtime,
                candidate_pool_size=1,
                proposal_attempts_per_iteration=2,
                patience=1,
                max_invalid_runs=1,
                deduplicate_candidates=True,
                failure_diagnosis=True,
                resume=(output if output.exists() else None),
                output_id=f"p{pair_sequence:02d}",
                short_paths=True,
            )
        finally:
            if previous_prompt_root is None:
                os.environ.pop("RESEARCH_FORGE_PROMPT_ROOT", None)
            else:
                os.environ["RESEARCH_FORGE_PROMPT_ROOT"] = previous_prompt_root
    else:
        report = BenchmarkReport.model_validate(read_json(output / "report.json"))
    if len(report.seeds) != 1 or report.seeds[0].seed != baseline_cell.seed:
        raise ValueError(f"shared controller report seed mismatch: {pair_key}")
    seed_report = report.seeds[0]
    if seed_report.errors or not seed_report.integrity.passed or not seed_report.integrity.isolation_verified:
        write_json_atomic(
            pair_dir / "invalid.json",
            {
                "schema_version": 1,
                "invalid_at": utc_now(),
                "pair_key": pair_key,
                "reason": "shared controller integrity failure",
                "errors": seed_report.errors,
                "integrity": seed_report.integrity.model_dump(mode="json"),
            },
        )
        raise ValueError(f"shared controller failed integrity: {pair_key}")
    controller_report_path = pair_dir / "controller_report.json"
    _copy_evidence_artifact(output / "report.json", controller_report_path)
    benchmark_project = Path(seed_report.project).resolve()
    prompt, experiment_packet = _finalizer_prompt(
        project,
        stage2,
        protocol,
        baseline_cell,
        report,
        benchmark_project,
        pair_dir / "evidence",
    )
    registry_path = pair_dir / "shared_registry.json"
    if registry_path.is_file():
        registry = StudyClaimRegistry.model_validate(read_json(registry_path))
    else:
        finalizer = await generate_study_finalizer(
            prompt,
            instructions=(stage2 / "prompts" / "study_finalizer.md").read_text(
                encoding="utf-8"
            ),
            cwd=benchmark_project,
        )
        metric_normalizations: list[dict[str, object]] = []
        raw_registry = _registry_from_output(
            protocol,
            baseline_cell,
            finalizer,
            experiment_packet,
            normalization_log=metric_normalizations,
        )
        registry, normalizations = _normalize_registry_artifacts(
            raw_registry, experiment_packet
        )
        write_json_atomic(registry_path, registry)
        write_json_atomic(
            pair_dir / "shared_normalization.json",
            {
                "schema_version": 1,
                "source_registry_id": raw_registry.registry_id,
                "shared_registry_id": registry.registry_id,
                "changes": normalizations,
                "metric_changes": metric_normalizations,
                "claim_text_changed": False,
                "model_called_again": False,
            },
        )
    structural = audit_registry_structure(
        project, stage2, registry, experiment_packet
    )
    write_json_atomic(pair_dir / "structural_audit.json", structural)
    if structural["passed"] is not True:
        write_json_atomic(
            pair_dir / "invalid.json",
            {
                "schema_version": 1,
                "invalid_at": utc_now(),
                "pair_key": pair_key,
                "reason": "shared initial registry structural integrity failure",
                "violations": structural["violations"],
            },
        )
        raise ValueError(f"shared registry failed integrity: {pair_key}")
    score = seed_report.best_score if seed_report.best_score is not None else seed_report.baseline_score
    if score is None:
        raise ValueError(f"shared controller has no task-native score: {pair_key}")
    shared_telemetry = agent_telemetry_summary(
        telemetry_path, scopes={"shared_upstream"}
    )
    write_json_atomic(pair_dir / "shared_telemetry.json", shared_telemetry)
    write_json_atomic(
        complete_path,
        {
            "schema_version": 1,
            "completed_at": utc_now(),
            "protocol_id": protocol.protocol_id,
            "pair_key": pair_key,
            "branch_order": branch_order,
            "controller_output": str(output),
            "controller_report_sha256": sha256_file(controller_report_path),
            "source_controller_report_sha256": sha256_file(output / "report.json"),
            "shared_registry_sha256": sha256_file(registry_path),
            "shared_normalization_sha256": sha256_file(
                pair_dir / "shared_normalization.json"
            ),
            "structural_audit_sha256": sha256_file(pair_dir / "structural_audit.json"),
            "pair_manifest_sha256": sha256_file(manifest_path),
            "evidence_tree_sha256": _stable_tree_hash(pair_dir / "evidence"),
            "task_native_score": float(score),
            "upstream_execution_count": 1,
            "initial_finalizer_count": 1,
            "telemetry_sha256": sha256_file(pair_dir / "shared_telemetry.json"),
            "token_count": shared_telemetry["token_count"],
            "model_call_count": shared_telemetry["model_call_count"],
            "monetary_cost_usd": shared_telemetry["monetary_cost_usd"],
        },
    )


def _stable_tree_hash(root: Path) -> str:
    entries = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }
    return sha256_file_from_value(entries)


def _materialize_publication_pair_inputs(
    stage2: Path,
    protocol: Stage2Protocol,
    baseline_cell: Stage2Cell,
    treatment_cell: Stage2Cell,
) -> None:
    pair_dir = _shared_pair_dir(stage2, protocol, baseline_cell)
    shared = StudyClaimRegistry.model_validate(read_json(pair_dir / "shared_registry.json"))
    shared_hash = sha256_file(pair_dir / "shared_registry.json")
    for cell, registry in (
        (baseline_cell, shared),
        (treatment_cell, _rebind_registry_to_cell(shared, protocol, treatment_cell)),
    ):
        cell_dir = _cell_dir(stage2, cell)
        cell_dir.mkdir(parents=True, exist_ok=True)
        initial_path = cell_dir / "initial_registry.json"
        if not initial_path.is_file():
            write_json_atomic(initial_path, registry)
        binding_path = cell_dir / "shared_artifact_binding.json"
        if not binding_path.is_file():
            write_json_atomic(
                binding_path,
                {
                    "schema_version": 1,
                    "protocol_id": protocol.protocol_id,
                    "pair_key": f"{cell.task_id}--seed-{cell.seed}",
                    "shared_registry_path": pair_dir.relative_to(stage2.parent).as_posix()
                    + "/shared_registry.json",
                    "shared_registry_sha256": shared_hash,
                    "claim_payload_sha256": sha256_file_from_value(
                        [
                            {
                                "claim_id": claim.claim_id,
                                "claim_type": claim.claim_type.value,
                                "claim_text": claim.claim_text,
                                "source_ids": claim.source_ids,
                                "experiment_run_id": claim.experiment_run_id,
                                "metric_values": claim.metric_values,
                                "artifact_paths": claim.artifact_paths,
                            }
                            for claim in registry.claims
                        ]
                    ),
                    "branch_order": protocol.pair_branch_order[
                        f"{cell.task_id}--seed-{cell.seed}"
                    ],
                },
            )
    baseline_final = _cell_dir(stage2, baseline_cell) / "final_registry.json"
    if not baseline_final.is_file():
        write_json_atomic(baseline_final, shared)
    treatment_presentation = _cell_dir(stage2, treatment_cell) / "finalizer_presentation.json"
    if not treatment_presentation.is_file():
        write_json_atomic(
            treatment_presentation,
            {
                "schema_version": 2,
                "arm_masked": True,
                "presented_arm": "shared_pre_intervention",
                "actual_arm": "treatment",
                "shared_artifact": True,
                "shared_model_call_count": 1,
                "branch_specific_initial_finalizer_call_count": 0,
                "actual_cell_id": treatment_cell.cell_id,
            },
        )


async def _run_baseline_cell(
    project: Path,
    stage2: Path,
    protocol: Stage2Protocol,
    backbone: Stage2BackboneManifest,
    cell: Stage2Cell,
    runtime: ExecutionRuntime,
) -> None:
    cell_dir = _cell_dir(stage2, cell)
    telemetry_path = (
        _shared_pair_dir(stage2, protocol, cell) / "agent_telemetry.jsonl"
        if protocol.study_intent == "publication"
        else cell_dir / "agent_telemetry.jsonl"
    )
    configure_agent_telemetry(telemetry_path, scope="baseline")
    complete_path = cell_dir / "complete.json"
    if complete_path.is_file():
        return
    if (cell_dir / "invalid.json").is_file():
        raise ValueError(f"baseline cell was marked invalid and cannot be rerun: {cell.cell_id}")
    cell_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cell_dir / "cell_manifest.json"
    if not manifest_path.is_file():
        write_json_atomic(
            manifest_path,
            {
                "schema_version": 1,
                "created_at": utc_now(),
                "protocol_id": protocol.protocol_id,
                "backbone_manifest_hash": protocol.backbone_manifest_hash,
                "cell": cell.model_dump(mode="json"),
                "status": "started",
            },
        )
    started = time.monotonic()
    output = _controller_output(backbone, cell)
    if output is None or not (output / "report.json").is_file():
        previous_prompt_root = os.environ.get("RESEARCH_FORGE_PROMPT_ROOT")
        os.environ["RESEARCH_FORGE_PROMPT_ROOT"] = str(stage2 / "prompts")
        try:
            output, report, _ = await run_loop_benchmark(
                stage2 / "task_packs" / cell.task_id,
                strategy="codex",
                seeds=[cell.seed],
                iterations=1,
                output_root=Path(backbone.controller_run_root),
                runtime=runtime,
                candidate_pool_size=1,
                proposal_attempts_per_iteration=2,
                patience=1,
                max_invalid_runs=1,
                deduplicate_candidates=True,
                failure_diagnosis=True,
                resume=output,
                output_id=f"b{cell.sequence:02d}",
                short_paths=True,
            )
        finally:
            if previous_prompt_root is None:
                os.environ.pop("RESEARCH_FORGE_PROMPT_ROOT", None)
            else:
                os.environ["RESEARCH_FORGE_PROMPT_ROOT"] = previous_prompt_root
    else:
        report = BenchmarkReport.model_validate(read_json(output / "report.json"))

    if len(report.seeds) != 1 or report.seeds[0].seed != cell.seed:
        raise ValueError(f"controller report does not match cell seed: {cell.cell_id}")
    seed_report = report.seeds[0]
    if seed_report.errors or not seed_report.integrity.passed or not seed_report.integrity.isolation_verified:
        invalid = {
            "schema_version": 1,
            "invalid_at": utc_now(),
            "cell_id": cell.cell_id,
            "reason": "controller integrity failure",
            "errors": seed_report.errors,
            "integrity": seed_report.integrity.model_dump(mode="json"),
        }
        write_json_atomic(cell_dir / "invalid.json", invalid)
        raise ValueError(f"baseline cell controller failed integrity: {cell.cell_id}")

    controller_report_path = cell_dir / "controller_report.json"
    _copy_evidence_artifact(output / "report.json", controller_report_path)
    benchmark_project = Path(seed_report.project).resolve()
    prompt, experiment_packet = _finalizer_prompt(
        project,
        stage2,
        protocol,
        cell,
        report,
        benchmark_project,
        (
            _shared_pair_dir(stage2, protocol, cell) / "evidence"
            if protocol.study_intent == "publication"
            else cell_dir / "evidence"
        ),
    )
    registry_path = cell_dir / "final_registry.json"
    if registry_path.is_file():
        registry = StudyClaimRegistry.model_validate(read_json(registry_path))
    else:
        finalizer = await generate_study_finalizer(
            prompt,
            instructions=(stage2 / "prompts" / "study_finalizer.md").read_text(encoding="utf-8"),
            cwd=benchmark_project,
        )
        metric_normalizations: list[dict[str, object]] = []
        registry = _registry_from_output(
            protocol,
            cell,
            finalizer,
            experiment_packet,
            normalization_log=metric_normalizations,
        )
        write_json_atomic(
            cell_dir / "finalizer_metric_normalization.json",
            {"schema_version": 1, "changes": metric_normalizations},
        )
        write_json_atomic(cell_dir / "initial_registry.json", registry)
        write_json_atomic(registry_path, registry)

    structural = audit_registry_structure(project, stage2, registry, experiment_packet)
    write_json_atomic(cell_dir / "structural_audit.json", structural)
    if structural["passed"] is not True:
        write_json_atomic(
            cell_dir / "invalid.json",
            {
                "schema_version": 1,
                "invalid_at": utc_now(),
                "cell_id": cell.cell_id,
                "reason": "claim registry structural integrity failure",
                "violations": structural["violations"],
            },
        )
        raise ValueError(f"baseline cell claim registry failed integrity: {cell.cell_id}")

    score = seed_report.best_score if seed_report.best_score is not None else seed_report.baseline_score
    if score is None:
        raise ValueError(f"baseline cell has no task-native score: {cell.cell_id}")
    completed_at = utc_now()
    cell_manifest = read_json(manifest_path)
    wall_clock_seconds = max(
        0.0,
        (
            datetime.fromisoformat(completed_at)
            - datetime.fromisoformat(str(cell_manifest["created_at"]))
        ).total_seconds(),
    )
    telemetry_scopes = (
        {"shared_upstream", "baseline"}
        if protocol.study_intent == "publication"
        else {"baseline"}
    )
    telemetry = agent_telemetry_summary(telemetry_path, scopes=telemetry_scopes)
    write_json_atomic(cell_dir / "telemetry.json", telemetry)
    write_json_atomic(
        complete_path,
        {
            "schema_version": 1,
            "completed_at": completed_at,
            "protocol_id": protocol.protocol_id,
            "cell_id": cell.cell_id,
            "arm": cell.arm.value,
            "task_id": cell.task_id,
            "seed": cell.seed,
            "controller_output": str(output),
            "controller_report_hash": sha256_file(controller_report_path),
            "source_controller_report_hash": sha256_file(output / "report.json"),
            "registry_hash": sha256_file(registry_path),
            "structural_audit_hash": sha256_file(cell_dir / "structural_audit.json"),
            "task_native_score": float(score),
            "wall_clock_seconds": wall_clock_seconds,
            "wall_clock_definition": "cell_manifest.created_at_to_complete.completed_at",
            "active_attempt_seconds": time.monotonic() - started,
            "telemetry_hash": sha256_file(cell_dir / "telemetry.json"),
            "token_count": telemetry["token_count"],
            "model_call_count": telemetry["model_call_count"],
            "monetary_cost_usd": telemetry["monetary_cost_usd"],
        },
    )


def audit_stage2_baseline(project: Path, *, persist: bool = False) -> Stage2BaselineAudit:
    project = project.resolve()
    stage2 = project / "stage2"
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    protocol_audit = audit_stage2_protocol(project)
    check("stage2_protocol_valid", protocol_audit.passed, "frozen Stage 2 protocol failed audit")
    try:
        protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
    except Exception as exc:
        protocol = None
        check("baseline_cells_integral", False, f"cannot load Stage 2 protocol: {exc}")

    expected = [cell for cell in protocol.cells if cell.arm == StudyArm.BASELINE] if protocol else []
    completed_ids: list[str] = []
    scores: dict[str, list[float]] = {}
    wall_clock = 0.0
    cells_integral = True
    no_invalid = True
    for cell in expected:
        cell_dir = _cell_dir(stage2, cell)
        if (cell_dir / "invalid.json").is_file():
            no_invalid = False
        complete_path = cell_dir / "complete.json"
        if not complete_path.is_file():
            continue
        try:
            complete = read_json(complete_path)
            registry_path = cell_dir / "final_registry.json"
            structural_path = cell_dir / "structural_audit.json"
            telemetry_path = cell_dir / "telemetry.json"
            output = Path(str(complete["controller_output"]))
            report_path = cell_dir / "controller_report.json"
            registry = StudyClaimRegistry.model_validate(read_json(registry_path))
            structural = read_json(structural_path)
            report = BenchmarkReport.model_validate(read_json(report_path))
            valid = (
                complete.get("protocol_id") == protocol.protocol_id
                and complete.get("cell_id") == cell.cell_id
                and complete.get("task_id") == cell.task_id
                and complete.get("seed") == cell.seed
                and complete.get("arm") == "baseline"
                and registry.cell_id == cell.cell_id
                and registry.arm == StudyArm.BASELINE
                and structural.get("passed") is True
                and sha256_file(report_path) == complete.get("controller_report_hash")
                and (output / "report.json").is_file()
                and sha256_file(output / "report.json")
                == complete.get("source_controller_report_hash")
                and sha256_file(registry_path) == complete.get("registry_hash")
                and sha256_file(structural_path) == complete.get("structural_audit_hash")
                and telemetry_path.is_file()
                and sha256_file(telemetry_path) == complete.get("telemetry_hash")
                and int(complete.get("token_count", 0)) > 0
                and int(complete.get("model_call_count", 0)) > 0
                and float(complete.get("monetary_cost_usd", -1.0)) >= 0.0
                and len(report.seeds) == 1
                and report.seeds[0].integrity.passed
                and report.seeds[0].integrity.isolation_verified
            )
            if not valid:
                cells_integral = False
                violations.append(f"baseline cell evidence is inconsistent: {cell.cell_id}")
                continue
            completed_ids.append(cell.cell_id)
            scores.setdefault(cell.task_id, []).append(float(complete["task_native_score"]))
            wall_clock += float(complete["wall_clock_seconds"])
        except Exception as exc:
            cells_integral = False
            violations.append(f"cannot audit baseline cell {cell.cell_id}: {exc}")

    expected_prefix = [cell.cell_id for cell in expected[: len(completed_ids)]]
    check("baseline_cells_integral", cells_integral, "one or more baseline cells failed evidence audit")
    check("baseline_order_is_prefix", completed_ids == expected_prefix, "baseline cells are out of order")
    check("no_invalid_baseline_cells", no_invalid, "a baseline cell is marked invalid")
    expected_count = len(expected)
    complete_matrix = len(completed_ids) == expected_count
    check(
        "baseline_matrix_exact_when_complete",
        len(completed_ids) <= expected_count,
        "baseline matrix exceeds preregistered cells",
    )
    audit = Stage2BaselineAudit(
        passed=bool(checks) and all(checks.values()),
        complete=complete_matrix and bool(checks) and all(checks.values()),
        checks=checks,
        violations=violations,
        protocol_id=protocol.protocol_id if protocol else None,
        expected_cells=expected_count,
        completed_cells=len(completed_ids),
        task_native_scores=scores,
        total_wall_clock_seconds=wall_clock,
    )
    if persist and stage2.is_dir():
        write_json_atomic(stage2 / "baseline_audit.json", audit)
    return audit


async def run_stage2_baseline(
    project: Path,
    *,
    max_cells: int | None = None,
) -> Stage2BaselineAudit:
    project = project.resolve()
    stage2 = project / "stage2"
    protocol_audit = audit_stage2_protocol(project, persist=True)
    if not protocol_audit.passed:
        raise ValueError("Stage 2 protocol failed audit: " + "; ".join(protocol_audit.violations))
    state = load_state(project)
    if state.stage not in {Stage.BASELINE_PENDING, Stage.BASELINE_VERIFIED}:
        raise ValueError("Stage 2 baseline requires baseline_pending or baseline_verified state")
    protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
    backbone = Stage2BackboneManifest.model_validate(read_json(stage2 / "backbone_manifest.json"))
    _verify_codex_backbone(backbone)
    runtime = _runtime_from_backbone(backbone)
    baseline_cells = [cell for cell in protocol.cells if cell.arm == StudyArm.BASELINE]
    remaining = [
        cell
        for cell in baseline_cells
        if not (_cell_dir(stage2, cell) / "complete.json").is_file()
    ]
    if max_cells is not None:
        if max_cells < 1 or max_cells > len(baseline_cells):
            raise ValueError(f"max_cells must be between 1 and {len(baseline_cells)}")
        remaining = remaining[:max_cells]
    for cell in remaining:
        await _run_baseline_cell(project, stage2, protocol, backbone, cell, runtime)
        partial = audit_stage2_baseline(project, persist=True)
        if not partial.passed:
            raise ValueError("baseline matrix failed audit: " + "; ".join(partial.violations))

    audit = audit_stage2_baseline(project, persist=True)
    if audit.complete and state.stage == Stage.BASELINE_PENDING:
        transition(
            project,
            state,
            Stage.BASELINE_VERIFIED,
            "stage2_baseline_matrix_verified",
            protocol_id=protocol.protocol_id,
            completed_cells=len(
                [cell for cell in protocol.cells if cell.arm == StudyArm.BASELINE]
            ),
        )
        state.baseline_run_id = f"baseline-matrix-{protocol.protocol_id}"
        state.run_count = len(baseline_cells)
        save_state(project, state)
    return audit


async def _run_treatment_cell(
    project: Path,
    stage2: Path,
    protocol: Stage2Protocol,
    backbone: Stage2BackboneManifest,
    cell: Stage2Cell,
    runtime: ExecutionRuntime,
) -> None:
    if cell.arm != StudyArm.TREATMENT:
        raise ValueError("treatment runner received a non-treatment cell")
    cell_dir = _cell_dir(stage2, cell)
    telemetry_path = (
        _shared_pair_dir(stage2, protocol, cell) / "agent_telemetry.jsonl"
        if protocol.study_intent == "publication"
        else cell_dir / "agent_telemetry.jsonl"
    )
    configure_agent_telemetry(telemetry_path, scope="treatment")
    complete_path = cell_dir / "complete.json"
    if complete_path.is_file():
        return
    if (cell_dir / "invalid.json").is_file():
        raise ValueError(f"treatment cell was marked invalid and cannot be rerun: {cell.cell_id}")
    cell_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cell_dir / "cell_manifest.json"
    if not manifest_path.is_file():
        write_json_atomic(
            manifest_path,
            {
                "schema_version": 1,
                "created_at": utc_now(),
                "protocol_id": protocol.protocol_id,
                "backbone_manifest_hash": protocol.backbone_manifest_hash,
                "orchestration_manifest_hash": sha256_file(
                    stage2 / "orchestration_manifest.json"
                ),
                "cell": cell.model_dump(mode="json"),
                "status": "started",
            },
        )
    started = time.monotonic()
    output = _controller_output(backbone, cell)
    arm_sequence = cell.sequence - (len(protocol.tasks) * len(protocol.seeds))
    if output is None or not (output / "report.json").is_file():
        previous_prompt_root = os.environ.get("RESEARCH_FORGE_PROMPT_ROOT")
        os.environ["RESEARCH_FORGE_PROMPT_ROOT"] = str(stage2 / "prompts")
        try:
            output, report, _ = await run_loop_benchmark(
                stage2 / "task_packs" / cell.task_id,
                strategy="codex",
                seeds=[cell.seed],
                iterations=1,
                output_root=Path(backbone.controller_run_root),
                runtime=runtime,
                candidate_pool_size=1,
                proposal_attempts_per_iteration=2,
                patience=1,
                max_invalid_runs=1,
                deduplicate_candidates=True,
                failure_diagnosis=True,
                resume=output,
                output_id=f"t{arm_sequence:02d}",
                short_paths=True,
            )
        finally:
            if previous_prompt_root is None:
                os.environ.pop("RESEARCH_FORGE_PROMPT_ROOT", None)
            else:
                os.environ["RESEARCH_FORGE_PROMPT_ROOT"] = previous_prompt_root
    else:
        report = BenchmarkReport.model_validate(read_json(output / "report.json"))

    if len(report.seeds) != 1 or report.seeds[0].seed != cell.seed:
        raise ValueError(f"controller report does not match treatment cell seed: {cell.cell_id}")
    seed_report = report.seeds[0]
    if seed_report.errors or not seed_report.integrity.passed or not seed_report.integrity.isolation_verified:
        write_json_atomic(
            cell_dir / "invalid.json",
            {
                "schema_version": 1,
                "invalid_at": utc_now(),
                "cell_id": cell.cell_id,
                "reason": "treatment controller integrity failure",
                "errors": seed_report.errors,
                "integrity": seed_report.integrity.model_dump(mode="json"),
            },
        )
        raise ValueError(f"treatment cell controller failed integrity: {cell.cell_id}")

    controller_report_path = cell_dir / "controller_report.json"
    _copy_evidence_artifact(output / "report.json", controller_report_path)
    benchmark_project = Path(seed_report.project).resolve()
    prompt, experiment_packet = _finalizer_prompt(
        project,
        stage2,
        protocol,
        cell,
        report,
        benchmark_project,
        (
            _shared_pair_dir(stage2, protocol, cell) / "evidence"
            if protocol.study_intent == "publication"
            else cell_dir / "evidence"
        ),
        present_treatment_as_baseline=True,
    )
    presentation_path = cell_dir / "finalizer_presentation.json"
    if not presentation_path.is_file():
        write_json_atomic(
            presentation_path,
            {
                "schema_version": 1,
                "arm_masked": True,
                "presented_arm": "baseline",
                "actual_arm": "treatment",
                "presented_cell_id": f"baseline--{cell.task_id}--seed-{cell.seed}",
                "actual_cell_id": cell.cell_id,
                "artifact_prefix_remapped_after_output": True,
            },
        )

    initial_path = cell_dir / "initial_registry.json"
    if initial_path.is_file():
        initial = StudyClaimRegistry.model_validate(read_json(initial_path))
    else:
        finalizer = await generate_study_finalizer(
            prompt,
            instructions=(stage2 / "prompts" / "study_finalizer.md").read_text(
                encoding="utf-8"
            ),
            cwd=benchmark_project,
        )
        metric_normalizations: list[dict[str, object]] = []
        initial = _unblind_treatment_registry_artifacts(
            _registry_from_output(
                protocol,
                cell,
                finalizer,
                experiment_packet,
                normalization_log=metric_normalizations,
            ),
            cell,
            protocol,
        )
        write_json_atomic(
            cell_dir / "finalizer_metric_normalization.json",
            {"schema_version": 1, "changes": metric_normalizations},
        )
        write_json_atomic(initial_path, initial)

    normalized, normalizations = _normalize_registry_artifacts(initial, experiment_packet)
    gate_input_path = cell_dir / "gate_input_registry.json"
    if gate_input_path.is_file():
        gate_input = StudyClaimRegistry.model_validate(read_json(gate_input_path))
        if gate_input.model_dump(mode="json", exclude={"created_at"}) != normalized.model_dump(
            mode="json", exclude={"created_at"}
        ):
            raise ValueError("persisted gate input differs from deterministic normalization")
    else:
        gate_input = normalized
        write_json_atomic(gate_input_path, gate_input)
    normalization_path = cell_dir / "normalization.json"
    if not normalization_path.is_file():
        write_json_atomic(
            normalization_path,
            {
                "schema_version": 1,
                "initial_registry_id": initial.registry_id,
                "gate_input_registry_id": gate_input.registry_id,
                "changes": normalizations,
                "claim_text_changed": False,
                "model_called_again": False,
            },
        )

    initial_structural = audit_registry_structure(
        project, stage2, gate_input, experiment_packet
    )
    initial_structural_path = cell_dir / "initial_structural_audit.json"
    write_json_atomic(initial_structural_path, initial_structural)
    if initial_structural["passed"] is not True:
        write_json_atomic(
            cell_dir / "invalid.json",
            {
                "schema_version": 1,
                "invalid_at": utc_now(),
                "cell_id": cell.cell_id,
                "reason": "treatment initial registry structural integrity failure",
                "violations": initial_structural["violations"],
            },
        )
        raise ValueError(f"treatment initial registry failed integrity: {cell.cell_id}")

    first_pass_path = cell_dir / "gate_first_pass.json"
    if first_pass_path.is_file():
        first_pass = _validate_judgments(
            gate_input,
            SemanticClaimJudgmentBatch.model_validate(read_json(first_pass_path)),
        )
        first_pass = _apply_structural_verdicts(first_pass, initial_structural)
    else:
        first_pass = await _judge_registry(
            stage2,
            gate_input,
            experiment_packet,
            initial_structural,
            prompt_name="study_gate_verifier.md",
            cwd=benchmark_project,
        )
        write_json_atomic(
            first_pass_path, SemanticClaimJudgmentBatch(judgments=first_pass)
        )

    revised_ids = [
        item.claim_id for item in first_pass if item.verdict != ClaimVerdict.SUPPORTED
    ]
    revision_count = 1 if revised_ids else 0
    revised: StudyClaimRegistry
    recheck: list[SemanticClaimJudgment]
    if revised_ids:
        revised_path = cell_dir / "revised_registry.json"
        if revised_path.is_file():
            revised = StudyClaimRegistry.model_validate(read_json(revised_path))
            _validate_revision(gate_input, revised, first_pass)
        else:
            revised = await _revise_registry(
                protocol,
                stage2,
                cell,
                gate_input,
                experiment_packet,
                initial_structural,
                first_pass,
                cwd=benchmark_project,
            )
            write_json_atomic(revised_path, revised)
        revised_structural = audit_registry_structure(
            project,
            stage2,
            revised,
            experiment_packet,
            require_experiment_claim=False,
        )
        write_json_atomic(cell_dir / "revised_structural_audit.json", revised_structural)
        revised_claims = [claim for claim in revised.claims if claim.claim_id in revised_ids]
        recheck_registry = _registry_with_claims(revised, revised_claims)
        recheck_structural = audit_registry_structure(
            project,
            stage2,
            recheck_registry,
            experiment_packet,
            require_experiment_claim=False,
        )
        recheck_path = cell_dir / "gate_recheck.json"
        if recheck_path.is_file():
            recheck = _validate_judgments(
                recheck_registry,
                SemanticClaimJudgmentBatch.model_validate(read_json(recheck_path)),
            )
            recheck = _apply_structural_verdicts(recheck, recheck_structural)
        else:
            recheck = await _judge_registry(
                stage2,
                recheck_registry,
                experiment_packet,
                recheck_structural,
                prompt_name="study_gate_verifier.md",
                cwd=benchmark_project,
            )
            write_json_atomic(recheck_path, SemanticClaimJudgmentBatch(judgments=recheck))
    else:
        revised = gate_input
        recheck = []

    first_by_id = {item.claim_id: item for item in first_pass}
    recheck_by_id = {item.claim_id: item for item in recheck}
    retained_ids = [
        claim.claim_id
        for claim in revised.claims
        if (
            first_by_id[claim.claim_id].verdict == ClaimVerdict.SUPPORTED
            or recheck_by_id.get(claim.claim_id, first_by_id[claim.claim_id]).verdict
            == ClaimVerdict.SUPPORTED
        )
    ]
    removed_ids = [claim.claim_id for claim in revised.claims if claim.claim_id not in retained_ids]
    final_path = cell_dir / "final_registry.json"
    if final_path.is_file():
        final_registry = StudyClaimRegistry.model_validate(read_json(final_path))
        if [claim.claim_id for claim in final_registry.claims] != retained_ids:
            raise ValueError("persisted treatment final registry differs from gate decisions")
    else:
        final_registry = _registry_with_claims(
            revised, [claim for claim in revised.claims if claim.claim_id in retained_ids]
        )
        write_json_atomic(final_path, final_registry)

    final_structural = audit_registry_structure(
        project,
        stage2,
        final_registry,
        experiment_packet,
        require_experiment_claim=False,
    )
    final_structural_path = cell_dir / "structural_audit.json"
    write_json_atomic(final_structural_path, final_structural)
    if final_structural["passed"] is not True:
        write_json_atomic(
            cell_dir / "invalid.json",
            {
                "schema_version": 1,
                "invalid_at": utc_now(),
                "cell_id": cell.cell_id,
                "reason": "treatment final registry structural integrity failure",
                "violations": final_structural["violations"],
            },
        )
        raise ValueError(f"treatment final registry failed integrity: {cell.cell_id}")

    trace_path = cell_dir / "gate_trace.json"
    if trace_path.is_file():
        trace = StudyGateTrace.model_validate(read_json(trace_path))
    else:
        trace = StudyGateTrace(
            protocol_id=protocol.protocol_id,
            cell_id=cell.cell_id,
            initial_registry_id=initial.registry_id,
            gate_input_registry_id=gate_input.registry_id,
            revised_registry_id=revised.registry_id if revision_count else None,
            final_registry_id=final_registry.registry_id,
            first_pass=first_pass,
            recheck=recheck,
            revised_claim_ids=revised_ids,
            removed_claim_ids=removed_ids,
            retained_claim_ids=retained_ids,
            revision_count=revision_count,
        )
        write_json_atomic(trace_path, trace)

    score = seed_report.best_score if seed_report.best_score is not None else seed_report.baseline_score
    if score is None:
        raise ValueError(f"treatment cell has no task-native score: {cell.cell_id}")
    completed_at = utc_now()
    cell_manifest = read_json(manifest_path)
    wall_clock_seconds = max(
        0.0,
        (
            datetime.fromisoformat(completed_at)
            - datetime.fromisoformat(str(cell_manifest["created_at"]))
        ).total_seconds(),
    )
    gate_artifact_names = [
        "initial_registry.json",
        "finalizer_presentation.json",
        "normalization.json",
        "gate_input_registry.json",
        "initial_structural_audit.json",
        "gate_first_pass.json",
        "revised_registry.json",
        "revised_structural_audit.json",
        "gate_recheck.json",
        "final_registry.json",
        "structural_audit.json",
        "gate_trace.json",
    ]
    gate_hashes = {
        name: sha256_file(cell_dir / name)
        for name in gate_artifact_names
        if (cell_dir / name).is_file()
    }
    telemetry_scopes = (
        {"shared_upstream", "treatment"}
        if protocol.study_intent == "publication"
        else {"treatment"}
    )
    telemetry = agent_telemetry_summary(telemetry_path, scopes=telemetry_scopes)
    write_json_atomic(cell_dir / "telemetry.json", telemetry)
    write_json_atomic(
        complete_path,
        {
            "schema_version": 1,
            "completed_at": completed_at,
            "protocol_id": protocol.protocol_id,
            "cell_id": cell.cell_id,
            "arm": cell.arm.value,
            "task_id": cell.task_id,
            "seed": cell.seed,
            "controller_output": str(output),
            "controller_report_hash": sha256_file(controller_report_path),
            "source_controller_report_hash": sha256_file(output / "report.json"),
            "orchestration_manifest_hash": sha256_file(
                stage2 / "orchestration_manifest.json"
            ),
            "gate_artifact_hashes": gate_hashes,
            "initial_claim_count": len(initial.claims),
            "final_claim_count": len(final_registry.claims),
            "removed_claim_count": len(removed_ids),
            "task_native_score": float(score),
            "wall_clock_seconds": wall_clock_seconds,
            "wall_clock_definition": "cell_manifest.created_at_to_complete.completed_at",
            "active_attempt_seconds": time.monotonic() - started,
            "telemetry_hash": sha256_file(cell_dir / "telemetry.json"),
            "token_count": telemetry["token_count"],
            "model_call_count": telemetry["model_call_count"],
            "monetary_cost_usd": telemetry["monetary_cost_usd"],
        },
    )


def audit_stage2_treatment(project: Path, *, persist: bool = False) -> Stage2TreatmentAudit:
    project = project.resolve()
    stage2 = project / "stage2"
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    protocol_audit = audit_stage2_protocol(project)
    check("stage2_protocol_valid", protocol_audit.passed, "frozen Stage 2 protocol failed audit")
    baseline = audit_stage2_baseline(project)
    check("baseline_matrix_complete", baseline.complete, "baseline matrix is not complete")
    try:
        protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
        _ensure_orchestration_manifest(stage2, protocol)
        check("orchestration_implementation_frozen", True, "")
    except Exception as exc:
        protocol = None
        check("orchestration_implementation_frozen", False, str(exc))

    expected = [cell for cell in protocol.cells if cell.arm == StudyArm.TREATMENT] if protocol else []
    completed_ids: list[str] = []
    scores: dict[str, list[float]] = {}
    initial_claims = 0
    final_claims = 0
    removed_claims = 0
    wall_clock = 0.0
    cells_integral = True
    no_invalid = True
    for cell in expected:
        cell_dir = _cell_dir(stage2, cell)
        if (cell_dir / "invalid.json").is_file():
            no_invalid = False
        complete_path = cell_dir / "complete.json"
        if not complete_path.is_file():
            continue
        try:
            complete = read_json(complete_path)
            report_path = cell_dir / "controller_report.json"
            output = Path(str(complete["controller_output"]))
            report = BenchmarkReport.model_validate(read_json(report_path))
            initial = StudyClaimRegistry.model_validate(read_json(cell_dir / "initial_registry.json"))
            final_registry = StudyClaimRegistry.model_validate(
                read_json(cell_dir / "final_registry.json")
            )
            trace = StudyGateTrace.model_validate(read_json(cell_dir / "gate_trace.json"))
            structural = read_json(cell_dir / "structural_audit.json")
            telemetry_path = cell_dir / "telemetry.json"
            gate_hashes = dict(complete.get("gate_artifact_hashes", {}))
            hashes_match = bool(gate_hashes) and all(
                (cell_dir / name).is_file()
                and sha256_file(cell_dir / name) == expected_hash
                for name, expected_hash in gate_hashes.items()
            )
            required_hashes = {
                "initial_registry.json",
                "finalizer_presentation.json",
                "normalization.json",
                "gate_input_registry.json",
                "initial_structural_audit.json",
                "gate_first_pass.json",
                "final_registry.json",
                "structural_audit.json",
                "gate_trace.json",
            }
            valid = (
                complete.get("protocol_id") == protocol.protocol_id
                and complete.get("cell_id") == cell.cell_id
                and complete.get("task_id") == cell.task_id
                and complete.get("seed") == cell.seed
                and complete.get("arm") == "treatment"
                and _cell_orchestration_hash_is_accepted(
                    stage2, cell, complete.get("orchestration_manifest_hash")
                )
                and initial.cell_id == cell.cell_id
                and initial.arm == StudyArm.TREATMENT
                and final_registry.cell_id == cell.cell_id
                and final_registry.arm == StudyArm.TREATMENT
                and trace.cell_id == cell.cell_id
                and trace.final_registry_id == final_registry.registry_id
                and trace.retained_claim_ids
                == [claim.claim_id for claim in final_registry.claims]
                and structural.get("passed") is True
                and required_hashes.issubset(gate_hashes)
                and hashes_match
                and sha256_file(report_path) == complete.get("controller_report_hash")
                and (output / "report.json").is_file()
                and sha256_file(output / "report.json")
                == complete.get("source_controller_report_hash")
                and len(report.seeds) == 1
                and report.seeds[0].integrity.passed
                and report.seeds[0].integrity.isolation_verified
                and int(complete.get("initial_claim_count", -1)) == len(initial.claims)
                and int(complete.get("final_claim_count", -1)) == len(final_registry.claims)
                and int(complete.get("removed_claim_count", -1))
                == len(trace.removed_claim_ids)
                and telemetry_path.is_file()
                and sha256_file(telemetry_path) == complete.get("telemetry_hash")
                and int(complete.get("token_count", 0)) > 0
                and int(complete.get("model_call_count", 0)) > 0
                and float(complete.get("monetary_cost_usd", -1.0)) >= 0.0
            )
            if not valid:
                cells_integral = False
                violations.append(f"treatment cell evidence is inconsistent: {cell.cell_id}")
                continue
            completed_ids.append(cell.cell_id)
            scores.setdefault(cell.task_id, []).append(float(complete["task_native_score"]))
            initial_claims += len(initial.claims)
            final_claims += len(final_registry.claims)
            removed_claims += len(trace.removed_claim_ids)
            wall_clock += float(complete["wall_clock_seconds"])
        except Exception as exc:
            cells_integral = False
            violations.append(f"cannot audit treatment cell {cell.cell_id}: {exc}")

    expected_prefix = [cell.cell_id for cell in expected[: len(completed_ids)]]
    check("treatment_cells_integral", cells_integral, "one or more treatment cells failed evidence audit")
    check("treatment_order_is_prefix", completed_ids == expected_prefix, "treatment cells are out of order")
    check("no_invalid_treatment_cells", no_invalid, "a treatment cell is marked invalid")
    expected_count = len(expected)
    check(
        "treatment_matrix_not_oversubscribed",
        len(completed_ids) <= expected_count,
        "treatment matrix exceeds preregistered cells",
    )
    complete_matrix = len(completed_ids) == expected_count
    audit = Stage2TreatmentAudit(
        passed=bool(checks) and all(checks.values()),
        complete=complete_matrix and bool(checks) and all(checks.values()),
        checks=checks,
        violations=violations,
        protocol_id=protocol.protocol_id if protocol else None,
        expected_cells=expected_count,
        completed_cells=len(completed_ids),
        task_native_scores=scores,
        initial_claims=initial_claims,
        final_claims=final_claims,
        removed_claims=removed_claims,
        total_wall_clock_seconds=wall_clock,
    )
    if persist and stage2.is_dir():
        write_json_atomic(stage2 / "treatment_audit.json", audit)
    return audit


async def run_stage2_treatment(
    project: Path,
    *,
    max_cells: int | None = None,
) -> Stage2TreatmentAudit:
    project = project.resolve()
    stage2 = project / "stage2"
    protocol_audit = audit_stage2_protocol(project, persist=True)
    if not protocol_audit.passed:
        raise ValueError("Stage 2 protocol failed audit: " + "; ".join(protocol_audit.violations))
    baseline = audit_stage2_baseline(project, persist=True)
    if not baseline.complete:
        raise ValueError("Stage 2 treatment requires the complete verified baseline matrix")
    protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
    _ensure_orchestration_manifest(stage2, protocol)
    backbone = Stage2BackboneManifest.model_validate(read_json(stage2 / "backbone_manifest.json"))
    _verify_codex_backbone(backbone)
    runtime = _runtime_from_backbone(backbone)
    state = load_state(project)
    if state.stage not in {
        Stage.BASELINE_VERIFIED,
        Stage.EXPERIMENT_DESIGN,
        Stage.EXPERIMENT_RUNNING,
        Stage.RESULT_REVIEW,
    }:
        raise ValueError("Stage 2 treatment requires baseline_verified or an active treatment state")
    if state.stage == Stage.BASELINE_VERIFIED:
        transition(
            project,
            state,
            Stage.EXPERIMENT_DESIGN,
            "stage2_treatment_matrix_opened",
            protocol_id=protocol.protocol_id,
            planned_treatment_cells=len(
                [cell for cell in protocol.cells if cell.arm == StudyArm.TREATMENT]
            ),
        )
    if state.stage == Stage.EXPERIMENT_DESIGN:
        transition(
            project,
            state,
            Stage.EXPERIMENT_RUNNING,
            "stage2_treatment_matrix_started",
            protocol_id=protocol.protocol_id,
        )
    save_state(project, state)

    treatment_cells = [cell for cell in protocol.cells if cell.arm == StudyArm.TREATMENT]
    remaining = [
        cell
        for cell in treatment_cells
        if not (_cell_dir(stage2, cell) / "complete.json").is_file()
    ]
    if max_cells is not None:
        if max_cells < 1 or max_cells > len(treatment_cells):
            raise ValueError(f"max_cells must be between 1 and {len(treatment_cells)}")
        remaining = remaining[:max_cells]
    for cell in remaining:
        await _run_treatment_cell(project, stage2, protocol, backbone, cell, runtime)
        partial = audit_stage2_treatment(project, persist=True)
        if not partial.passed:
            raise ValueError("treatment matrix failed audit: " + "; ".join(partial.violations))

    audit = audit_stage2_treatment(project, persist=True)
    if audit.complete and state.stage == Stage.EXPERIMENT_RUNNING:
        transition(
            project,
            state,
            Stage.RESULT_REVIEW,
            "stage2_treatment_matrix_verified",
            protocol_id=protocol.protocol_id,
            completed_cells=len(
                [cell for cell in protocol.cells if cell.arm == StudyArm.TREATMENT]
            ),
        )
        state.run_count = len(protocol.cells)
        save_state(project, state)
    return audit


async def run_stage2_publication_pairs(
    project: Path,
    *,
    max_pairs: int | None = None,
    pair_sequences: list[int] | None = None,
) -> dict[str, object]:
    """Execute prospective shared-artifact pairs in their frozen branch order."""
    project = project.resolve()
    stage2 = project / "stage2"
    protocol_audit = audit_stage2_protocol(project, persist=True)
    if not protocol_audit.passed:
        raise ValueError(
            "Stage 2 protocol failed audit: " + "; ".join(protocol_audit.violations)
        )
    protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
    if protocol.study_intent != "publication":
        raise ValueError("publication pair runner requires a publication protocol")
    if protocol.counterfactual_source != "shared_run_artifact":
        raise ValueError("publication pair runner requires shared-artifact branching")
    _ensure_orchestration_manifest(stage2, protocol)
    backbone = Stage2BackboneManifest.model_validate(
        read_json(stage2 / "backbone_manifest.json")
    )
    _verify_codex_backbone(backbone)
    runtime = _runtime_from_backbone(backbone)
    baseline_cells = [cell for cell in protocol.cells if cell.arm == StudyArm.BASELINE]
    treatments = {
        (cell.task_id, cell.seed): cell
        for cell in protocol.cells
        if cell.arm == StudyArm.TREATMENT
    }
    if max_pairs is not None and pair_sequences is not None:
        raise ValueError("max_pairs and pair_sequences are mutually exclusive")
    if max_pairs is not None and not 1 <= max_pairs <= len(baseline_cells):
        raise ValueError(f"max_pairs must be between 1 and {len(baseline_cells)}")
    if pair_sequences is not None:
        if (
            not pair_sequences
            or len(pair_sequences) != len(set(pair_sequences))
            or any(item < 1 or item > len(baseline_cells) for item in pair_sequences)
        ):
            raise ValueError("pair_sequences must be unique valid 1-based pair indices")
        pairs = [baseline_cells[item - 1] for item in pair_sequences]
    else:
        pairs = baseline_cells[:max_pairs] if max_pairs is not None else baseline_cells
    for baseline_cell in pairs:
        treatment_cell = treatments[(baseline_cell.task_id, baseline_cell.seed)]
        await _ensure_publication_shared_pair(
            project,
            stage2,
            protocol,
            backbone,
            baseline_cell,
            treatment_cell,
            runtime,
        )
        _materialize_publication_pair_inputs(
            stage2, protocol, baseline_cell, treatment_cell
        )
        pair_dir = _shared_pair_dir(stage2, protocol, baseline_cell)
        order = protocol.pair_branch_order[
            f"{baseline_cell.task_id}--seed-{baseline_cell.seed}"
        ]
        ordered_cells = (
            [baseline_cell, treatment_cell]
            if order == "baseline_first"
            else [treatment_cell, baseline_cell]
        )
        execution_path = pair_dir / "branch_execution.json"
        execution = (
            read_json(execution_path)
            if execution_path.is_file()
            else {
                "schema_version": 1,
                "protocol_id": protocol.protocol_id,
                "pair_key": f"{baseline_cell.task_id}--seed-{baseline_cell.seed}",
                "frozen_order": order,
                "events": [],
            }
        )
        completed_arms = {
            str(item.get("arm"))
            for item in execution.get("events", [])
            if item.get("status") == "completed"
        }
        for cell in ordered_cells:
            if cell.arm.value in completed_arms and (
                _cell_dir(stage2, cell) / "complete.json"
            ).is_file():
                continue
            started_at = utc_now()
            if cell.arm == StudyArm.BASELINE:
                await _run_baseline_cell(
                    project, stage2, protocol, backbone, cell, runtime
                )
            else:
                await _run_treatment_cell(
                    project, stage2, protocol, backbone, cell, runtime
                )
            execution["events"].append(
                {
                    "arm": cell.arm.value,
                    "cell_id": cell.cell_id,
                    "started_at": started_at,
                    "completed_at": utc_now(),
                    "status": "completed",
                }
            )
            write_json_atomic(execution_path, execution)

    baseline_audit = audit_stage2_baseline(project, persist=True)
    full_run = all(
        (_cell_dir(stage2, cell) / "complete.json").is_file()
        for cell in protocol.cells
    )
    treatment_audit = (
        audit_stage2_treatment(project, persist=True) if full_run else None
    )
    if full_run:
        if not baseline_audit.complete:
            raise ValueError(
                "publication baseline audit failed: "
                + "; ".join(baseline_audit.violations)
            )
        assert treatment_audit is not None
        if not treatment_audit.complete:
            raise ValueError(
                "publication treatment audit failed: "
                + "; ".join(treatment_audit.violations)
            )
        state = load_state(project)
        if state.stage == Stage.BASELINE_PENDING:
            transition(
                project,
                state,
                Stage.BASELINE_VERIFIED,
                "publication_shared_artifact_baseline_verified",
                protocol_id=protocol.protocol_id,
                completed_cells=len(baseline_cells),
            )
            state.baseline_run_id = f"shared-pair-matrix-{protocol.protocol_id}"
            state.run_count = len(baseline_cells)
        if state.stage == Stage.BASELINE_VERIFIED:
            transition(
                project,
                state,
                Stage.EXPERIMENT_DESIGN,
                "publication_randomized_branches_materialized",
                protocol_id=protocol.protocol_id,
                planned_treatment_cells=len(treatments),
            )
        if state.stage == Stage.EXPERIMENT_DESIGN:
            transition(
                project,
                state,
                Stage.EXPERIMENT_RUNNING,
                "publication_randomized_branches_started",
                protocol_id=protocol.protocol_id,
            )
        if state.stage == Stage.EXPERIMENT_RUNNING:
            transition(
                project,
                state,
                Stage.RESULT_REVIEW,
                "publication_randomized_branches_verified",
                protocol_id=protocol.protocol_id,
                completed_cells=len(protocol.cells),
            )
            state.run_count = len(protocol.cells)
        save_state(project, state)
    return {
        "protocol_id": protocol.protocol_id,
        "pairs_requested": len(pairs),
        "pairs_total": len(baseline_cells),
        "full_run": full_run,
        "baseline_audit": baseline_audit.model_dump(mode="json"),
        "treatment_audit": (
            treatment_audit.model_dump(mode="json") if treatment_audit else None
        ),
    }


def _cell_experiment_packet(project: Path, cell_dir: Path) -> list[dict[str, object]]:
    packet: list[dict[str, object]] = []
    evidence_root = cell_dir / "evidence"
    for run_dir in sorted(path for path in evidence_root.iterdir() if path.is_dir()):
        record_path = run_dir / "record.json"
        if not record_path.is_file():
            continue
        record = RunRecord.model_validate(read_json(record_path))
        artifacts = [record_path.relative_to(project).as_posix()]
        artifacts.extend(
            path.relative_to(project).as_posix()
            for path in sorted(run_dir.glob("trial-*-metrics.json"))
        )
        packet.append(
            {
                "run_id": record.run_id,
                "is_baseline": record.is_baseline,
                "valid": record.valid,
                "verdict": record.verdict,
                "aggregate_metrics": record.aggregate_metrics,
                "metric_stddev": record.metric_stddev,
                "improvement": record.improvement,
                "isolation_verified": record.isolation_verified,
                "artifacts": artifacts,
            }
        )
    if not packet:
        raise ValueError(f"cell evidence packet is empty: {cell_dir}")
    return packet


def _blind_id(protocol_id: str, registry: StudyClaimRegistry) -> str:
    value = f"{protocol_id}|{registry.registry_id}|protected-evaluator-v1"
    return "blind-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _failure_modes_for_claim(
    claim: StudyClaim,
    verdict: ClaimVerdict,
    structural_checks: dict[str, bool],
) -> list[ClaimFailureMode]:
    modes: list[ClaimFailureMode] = []
    if structural_checks.get("source_ids_present") is False:
        modes.append(ClaimFailureMode.MISSING_SOURCE_ID)
    if structural_checks.get("source_ids_resolve") is False:
        modes.append(ClaimFailureMode.WRONG_SOURCE_ID)
    if structural_checks.get("run_id_present") is False:
        modes.append(ClaimFailureMode.MISSING_RUN_ID)
    if structural_checks.get("metrics_exact") is False:
        modes.append(ClaimFailureMode.EXACT_METRIC_MISMATCH)
    if (
        structural_checks.get("artifacts_present") is False
        or structural_checks.get("artifacts_allowed") is False
        or structural_checks.get("artifacts_exist") is False
    ):
        modes.append(ClaimFailureMode.MISSING_ARTIFACT)
    if verdict == ClaimVerdict.ABSTAIN:
        modes.append(ClaimFailureMode.VERIFIER_ABSTENTION)
    elif verdict == ClaimVerdict.UNSUPPORTED and not modes:
        if claim.claim_type == StudyClaimType.NOVELTY:
            modes.append(ClaimFailureMode.UNSUPPORTED_NOVELTY)
        elif claim.claim_type == StudyClaimType.EXPERIMENT:
            modes.append(ClaimFailureMode.EXPERIMENT_DOES_NOT_SUPPORT)
        else:
            modes.append(ClaimFailureMode.SOURCE_DOES_NOT_SUPPORT)
    return list(dict.fromkeys(modes))


def _registry_evaluation(
    protocol: Stage2Protocol,
    project: Path,
    stage2: Path,
    registry: StudyClaimRegistry,
    experiment_packet: list[dict[str, object]],
    judgments: list[SemanticClaimJudgment],
) -> StudyRegistryEvaluation:
    structural = audit_registry_structure(
        project,
        stage2,
        registry,
        experiment_packet,
        require_experiment_claim=False,
    )
    checks_by_claim = dict(structural.get("claim_checks", {}))
    judgment_by_id = {item.claim_id: item for item in judgments}
    claim_evaluations: list[StudyClaimEvaluation] = []
    for claim in registry.claims:
        judgment = judgment_by_id[claim.claim_id]
        structural_checks = {
            str(name): bool(passed)
            for name, passed in dict(checks_by_claim.get(claim.claim_id, {})).items()
        }
        verdict = judgment.verdict
        failed = [name for name, passed in structural_checks.items() if not passed]
        rationale = judgment.rationale
        if failed:
            verdict = ClaimVerdict.UNSUPPORTED
            rationale = "Deterministic evidence checks failed: " + ", ".join(sorted(failed))
        claim_evaluations.append(
            StudyClaimEvaluation(
                claim_id=claim.claim_id,
                claim_type=claim.claim_type,
                verdict=verdict,
                failure_modes=_failure_modes_for_claim(claim, verdict, structural_checks),
                resolved_source_ids=[
                    item for item in judgment.supporting_source_ids if item in claim.source_ids
                ],
                resolved_experiment_run_id=(
                    claim.experiment_run_id
                    if structural_checks.get("run_resolves") is True
                    else None
                ),
                structural_checks=structural_checks,
                semantic_rationale=rationale,
            )
        )

    total = len(claim_evaluations)
    unsupported = sum(item.verdict == ClaimVerdict.UNSUPPORTED for item in claim_evaluations)
    abstained = sum(item.verdict == ClaimVerdict.ABSTAIN for item in claim_evaluations)
    citation_claims = [
        item
        for item in claim_evaluations
        if item.claim_type in {StudyClaimType.LITERATURE, StudyClaimType.NOVELTY}
    ]
    experiment_claims = [
        item for item in claim_evaluations if item.claim_type == StudyClaimType.EXPERIMENT
    ]
    structurally_covered = sum(
        bool(item.structural_checks) and all(item.structural_checks.values())
        for item in claim_evaluations
    )
    failure_counts: dict[ClaimFailureMode, int] = {}
    for item in claim_evaluations:
        for mode in item.failure_modes:
            failure_counts[mode] = failure_counts.get(mode, 0) + 1
    return StudyRegistryEvaluation(
        protocol_id=protocol.protocol_id,
        registry_id=registry.registry_id,
        cell_id=registry.cell_id,
        claims=claim_evaluations,
        unsupported_claim_rate=unsupported / total if total else 0.0,
        verifier_abstention_rate=abstained / total if total else 0.0,
        citation_correctness=(
            sum(item.verdict == ClaimVerdict.SUPPORTED for item in citation_claims)
            / len(citation_claims)
            if citation_claims
            else None
        ),
        experiment_detail_error_rate=(
            sum(item.verdict == ClaimVerdict.UNSUPPORTED for item in experiment_claims)
            / len(experiment_claims)
            if experiment_claims
            else None
        ),
        evidence_coverage=structurally_covered / total if total else 0.0,
        failure_mode_count=failure_counts,
        protocol_valid=structural.get("passed") is True,
        integrity_violations=list(structural.get("violations", [])),
    )


def _pooled_arm_metrics(
    arm: StudyArm,
    rows: list[tuple[Stage2Cell, StudyClaimRegistry, StudyRegistryEvaluation, dict[str, object]]],
) -> dict[str, float | int | None]:
    selected = [row for row in rows if row[0].arm == arm]
    claims = [claim for _, _, evaluation, _ in selected for claim in evaluation.claims]
    total = len(claims)
    unsupported = sum(item.verdict == ClaimVerdict.UNSUPPORTED for item in claims)
    abstained = sum(item.verdict == ClaimVerdict.ABSTAIN for item in claims)
    citation = [
        item
        for item in claims
        if item.claim_type in {StudyClaimType.LITERATURE, StudyClaimType.NOVELTY}
    ]
    experiment = [item for item in claims if item.claim_type == StudyClaimType.EXPERIMENT]
    structurally_covered = sum(
        bool(item.structural_checks) and all(item.structural_checks.values()) for item in claims
    )
    initial_claims = sum(int(complete.get("initial_claim_count", len(registry.claims))) for _, registry, _, complete in selected)
    return {
        "registries": len(selected),
        "initial_claims": initial_claims,
        "final_claims": total,
        "retention_rate": total / initial_claims if initial_claims else 0.0,
        "unsupported_claims": unsupported,
        "unsupported_claim_rate": unsupported / total if total else 0.0,
        "abstained_claims": abstained,
        "verifier_abstention_rate": abstained / total if total else 0.0,
        "citation_correctness": (
            sum(item.verdict == ClaimVerdict.SUPPORTED for item in citation) / len(citation)
            if citation
            else None
        ),
        "experiment_detail_error_rate": (
            sum(item.verdict == ClaimVerdict.UNSUPPORTED for item in experiment)
            / len(experiment)
            if experiment
            else None
        ),
        "evidence_coverage": structurally_covered / total if total else 0.0,
        "mean_task_native_score": (
            statistics.fmean(float(complete["task_native_score"]) for *_, complete in selected)
            if selected
            else None
        ),
        "total_wall_clock_seconds": sum(
            float(complete["wall_clock_seconds"]) for *_, complete in selected
        ),
    }


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot compute a percentile of an empty sample")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _hierarchical_bootstrap(
    protocol: Stage2Protocol,
    paired_rows: list[dict[str, object]],
) -> dict[str, float | int | None]:
    by_task: dict[str, list[float]] = {}
    for row in paired_rows:
        by_task.setdefault(str(row["task_id"]), []).append(float(row["effect"]))
    tasks = sorted(by_task)
    if not tasks:
        return {"resamples": 0, "median": None, "ci95_low": None, "ci95_high": None}
    seed = int(hashlib.sha256((protocol.protocol_id + "|bootstrap-v1").encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(protocol.bootstrap_resamples):
        sampled_tasks = [rng.choice(tasks) for _ in tasks]
        sampled_effects: list[float] = []
        for task_id in sampled_tasks:
            task_values = by_task[task_id]
            sampled_effects.extend(rng.choice(task_values) for _ in task_values)
        estimates.append(statistics.fmean(sampled_effects))
    return {
        "resamples": protocol.bootstrap_resamples,
        "seed": seed,
        "median": _percentile(estimates, 0.5),
        "ci95_low": _percentile(estimates, 0.025),
        "ci95_high": _percentile(estimates, 0.975),
    }


def _paired_summary(
    protocol: Stage2Protocol,
    rows: list[tuple[Stage2Cell, StudyClaimRegistry, StudyRegistryEvaluation, dict[str, object]]],
) -> dict[str, object]:
    indexed = {
        (cell.arm, cell.task_id, cell.seed): (registry, evaluation, complete)
        for cell, registry, evaluation, complete in rows
    }
    paired_rows: list[dict[str, object]] = []
    for task_id in [task.task_id for task in protocol.tasks]:
        for seed in protocol.seeds:
            baseline = indexed[(StudyArm.BASELINE, task_id, seed)]
            treatment = indexed[(StudyArm.TREATMENT, task_id, seed)]
            paired_rows.append(
                {
                    "task_id": task_id,
                    "seed": seed,
                    "baseline_unsupported_claim_rate": baseline[1].unsupported_claim_rate,
                    "treatment_unsupported_claim_rate": treatment[1].unsupported_claim_rate,
                    "effect": treatment[1].unsupported_claim_rate
                    - baseline[1].unsupported_claim_rate,
                    "baseline_claim_count": len(baseline[0].claims),
                    "treatment_claim_count": len(treatment[0].claims),
                    "claim_count_effect": len(treatment[0].claims) - len(baseline[0].claims),
                    "task_native_score_effect": float(treatment[2]["task_native_score"])
                    - float(baseline[2]["task_native_score"]),
                }
            )
    effects = [float(row["effect"]) for row in paired_rows]
    mean_effect = statistics.fmean(effects)
    effect_std = statistics.stdev(effects) if len(effects) > 1 else 0.0
    task_effects = {
        task_id: statistics.fmean(
            float(row["effect"]) for row in paired_rows if row["task_id"] == task_id
        )
        for task_id in [task.task_id for task in protocol.tasks]
    }
    task_score_effects = {
        task_id: statistics.fmean(
            float(row["task_native_score_effect"])
            for row in paired_rows
            if row["task_id"] == task_id
        )
        for task_id in [task.task_id for task in protocol.tasks]
    }
    return {
        "effect_definition": "treatment_minus_baseline; negative unsupported-claim effect favors treatment",
        "pairs": paired_rows,
        "mean_paired_unsupported_claim_rate_effect": mean_effect,
        "paired_cohen_dz": mean_effect / effect_std if effect_std > 0 else None,
        "task_effects": task_effects,
        "task_native_score_effects": task_score_effects,
        "tasks_with_lower_unsupported_claim_rate": sum(value < 0 for value in task_effects.values()),
        "task_native_score_within_absolute_0_02": all(
            abs(value) <= 0.02 for value in task_score_effects.values()
        ),
        "hierarchical_bootstrap": _hierarchical_bootstrap(protocol, paired_rows),
    }


def _manual_audit_sample(
    protocol: Stage2Protocol,
    stage2: Path,
    rows: list[tuple[Stage2Cell, StudyClaimRegistry, StudyRegistryEvaluation, dict[str, object]]],
    packets: dict[str, list[dict[str, object]]],
    blind_root: Path,
) -> dict[str, object]:
    candidates: dict[tuple[str, str], list[dict[str, object]]] = {}
    for cell, registry, evaluation, _ in rows:
        packet_by_id = {
            str(item["claim_id"]): item
            for item in _claim_evidence_packets(
                stage2,
                registry,
                packets[cell.cell_id],
                audit_registry_structure(
                    stage2.parent,
                    stage2,
                    registry,
                    packets[cell.cell_id],
                    require_experiment_claim=False,
                ),
            )
        }
        claim_by_id = {claim.claim_id: claim for claim in registry.claims}
        for item in evaluation.claims:
            claim = claim_by_id[item.claim_id]
            key = (cell.task_id, cell.arm.value)
            sort_key = hashlib.sha256(
                f"{protocol.protocol_id}|{registry.registry_id}|{claim.claim_id}|manual-v1".encode()
            ).hexdigest()
            evidence = json.loads(json.dumps(packet_by_id[claim.claim_id], ensure_ascii=False))
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
            candidates.setdefault(key, []).append(
                {
                    "sort_key": sort_key,
                    "evaluator_class": (
                        "unsupported"
                        if item.verdict == ClaimVerdict.UNSUPPORTED
                        else "non_unsupported"
                    ),
                    "claim_type": claim.claim_type.value,
                    "claim_text": claim.claim_text,
                    "evidence": evidence,
                }
            )

    selected: list[dict[str, object]] = []
    group_counts: dict[str, int] = {}
    for task in protocol.tasks:
        for arm in (StudyArm.BASELINE, StudyArm.TREATMENT):
            group = sorted(candidates.get((task.task_id, arm.value), []), key=lambda item: item["sort_key"])
            unsupported = [item for item in group if item["evaluator_class"] == "unsupported"]
            other = [item for item in group if item["evaluator_class"] != "unsupported"]
            chosen = unsupported[: protocol.manual_audit.target_unsupported_per_task_arm]
            chosen += other[: protocol.manual_audit.target_non_unsupported_per_task_arm]
            if len(chosen) < protocol.manual_audit.claims_per_task_arm:
                remaining = [item for item in group if item not in chosen]
                chosen += remaining[: protocol.manual_audit.claims_per_task_arm - len(chosen)]
            group_counts[f"{task.task_id}|{arm.value}"] = len(chosen)
            selected.extend(chosen)

    sample_items: list[dict[str, object]] = []
    for item in sorted(selected, key=lambda value: value["sort_key"]):
        audit_id = "audit-" + str(item["sort_key"])[:16]
        sample_items.append(
            {
                "audit_id": audit_id,
                "claim_type": item["claim_type"],
                "claim_text": item["claim_text"],
                "linked_evidence": item["evidence"],
                "auditor_1": {"verdict": None, "rationale": ""},
                "auditor_2": {"verdict": None, "rationale": ""},
                "adjudication": {"verdict": None, "rationale": ""},
            }
        )
    sample = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "arm_blinded": True,
        "task_blinded": True,
        "evaluator_verdict_blinded": True,
        "independent_auditors_required": protocol.manual_audit.independent_auditors,
        "adjudication_required": protocol.manual_audit.adjudication_required,
        "planned_total": protocol.manual_audit.total_claims,
        "actual_total": len(sample_items),
        "items": sample_items,
    }
    audit_root = blind_root / "manual-audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    sample_path = audit_root / "sample.json"
    write_json_atomic(sample_path, sample)
    manifest = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "sample_path": str(sample_path),
        "sample_sha256": sha256_file(sample_path),
        "planned_total": protocol.manual_audit.total_claims,
        "actual_total": len(sample_items),
        "group_counts": group_counts,
        "status": "awaiting_two_independent_auditors",
    }
    write_json_atomic(stage2 / "manual_audit_manifest.json", manifest)
    return manifest


async def run_stage2_evaluation(project: Path) -> Stage2EvaluationAudit:
    project = project.resolve()
    stage2 = project / "stage2"
    protocol_audit = audit_stage2_protocol(project, persist=True)
    if not protocol_audit.passed:
        raise ValueError("Stage 2 protocol failed audit: " + "; ".join(protocol_audit.violations))
    baseline = audit_stage2_baseline(project, persist=True)
    treatment = audit_stage2_treatment(project, persist=True)
    if not baseline.complete or not treatment.complete:
        raise ValueError("protected evaluation requires both complete preregistered arms")
    protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
    _ensure_orchestration_manifest(stage2, protocol)
    backbone = Stage2BackboneManifest.model_validate(read_json(stage2 / "backbone_manifest.json"))
    _verify_codex_backbone(backbone)
    state = load_state(project)
    if state.stage != Stage.RESULT_REVIEW:
        raise ValueError("protected evaluation requires the result_review stage")

    registries: list[tuple[Stage2Cell, StudyClaimRegistry, dict[str, object]]] = []
    packets: dict[str, list[dict[str, object]]] = {}
    for cell in protocol.cells:
        cell_dir = _cell_dir(stage2, cell)
        registry = StudyClaimRegistry.model_validate(read_json(cell_dir / "final_registry.json"))
        complete = read_json(cell_dir / "complete.json")
        registries.append((cell, registry, complete))
        packets[cell.cell_id] = _cell_experiment_packet(project, cell_dir)
    entries = [
        {
            "blind_id": _blind_id(protocol.protocol_id, registry),
            "registry_sha256": sha256_file(_cell_dir(stage2, cell) / "final_registry.json"),
            "claim_count": len(registry.claims),
        }
        for cell, registry, _ in registries
    ]
    if len({str(item["blind_id"]) for item in entries}) != len(protocol.cells):
        raise ValueError("protected evaluator blind IDs are not unique")
    evaluation_manifest = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "evaluator": protocol.protected_evaluator,
        "model": backbone.model,
        "codex_sdk_version": backbone.codex_sdk_version,
        "prompt_sha256": backbone.prompt_hashes["study_claim_verifier.md"],
        "orchestration_manifest_sha256": sha256_file(stage2 / "orchestration_manifest.json"),
        "arm_blinded": True,
        "evaluation_order": sorted(entries, key=lambda item: str(item["blind_id"])),
    }
    evaluation_root = stage2 / "evaluations"
    evaluation_root.mkdir(parents=True, exist_ok=True)
    manifest_path = evaluation_root / "manifest.json"
    if manifest_path.is_file() and read_json(manifest_path) != evaluation_manifest:
        raise ValueError("protected evaluation manifest drifted after first use")
    if not manifest_path.is_file():
        write_json_atomic(manifest_path, evaluation_manifest)

    registry_by_blind = {
        _blind_id(protocol.protocol_id, registry): (cell, registry, complete)
        for cell, registry, complete in registries
    }
    blind_root = Path.home() / ".research-forge-blind" / protocol.protocol_id
    blind_root.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[Stage2Cell, StudyClaimRegistry, StudyRegistryEvaluation, dict[str, object]]] = []
    evaluation_hashes: dict[str, str] = {}
    for entry in evaluation_manifest["evaluation_order"]:
        blind_id = str(entry["blind_id"])
        cell, registry, complete = registry_by_blind[blind_id]
        cell_dir = _cell_dir(stage2, cell)
        structural = audit_registry_structure(
            project,
            stage2,
            registry,
            packets[cell.cell_id],
            require_experiment_claim=False,
        )
        blind_cwd = blind_root / blind_id
        blind_cwd.mkdir(parents=True, exist_ok=True)
        evaluation_dir = evaluation_root / blind_id
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        judgments_path = evaluation_dir / "semantic_judgments.json"
        if registry.claims:
            if judgments_path.is_file():
                judgments = _validate_judgments(
                    registry,
                    SemanticClaimJudgmentBatch.model_validate(read_json(judgments_path)),
                )
                judgments = _apply_structural_verdicts(judgments, structural)
            else:
                judgments = await _judge_registry(
                    stage2,
                    registry,
                    packets[cell.cell_id],
                    structural,
                    prompt_name="study_claim_verifier.md",
                    cwd=blind_cwd,
                    arm_blinded_evidence=True,
                )
                write_json_atomic(
                    judgments_path, SemanticClaimJudgmentBatch(judgments=judgments)
                )
        else:
            judgments = []
            if not judgments_path.is_file():
                write_json_atomic(judgments_path, {"judgments": []})
        evaluation_path = evaluation_dir / "evaluation.json"
        if evaluation_path.is_file():
            evaluation = StudyRegistryEvaluation.model_validate(read_json(evaluation_path))
        else:
            evaluation = _registry_evaluation(
                protocol,
                project,
                stage2,
                registry,
                packets[cell.cell_id],
                judgments,
            )
            write_json_atomic(evaluation_path, evaluation)
        if evaluation.registry_id != registry.registry_id or evaluation.cell_id != cell.cell_id:
            raise ValueError(f"protected evaluation does not match registry: {blind_id}")
        rows.append((cell, registry, evaluation, complete))
        evaluation_hashes[blind_id] = sha256_file(evaluation_path)

    unblinding = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "created_only_after_all_evaluations": True,
        "mapping": [
            {
                "blind_id": _blind_id(protocol.protocol_id, registry),
                "cell_id": cell.cell_id,
                "arm": cell.arm.value,
                "task_id": cell.task_id,
                "seed": cell.seed,
                "registry_id": registry.registry_id,
            }
            for cell, registry, _, _ in rows
        ],
    }
    write_json_atomic(evaluation_root / "unblinding.json", unblinding)
    arm_metrics = {
        arm.value: _pooled_arm_metrics(arm, rows)
        for arm in (StudyArm.BASELINE, StudyArm.TREATMENT)
    }
    paired = _paired_summary(protocol, rows)
    manual_manifest = _manual_audit_sample(
        protocol, stage2, rows, packets, blind_root
    )
    summary = {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "analysis_status": "protected_evaluator_complete_manual_audit_pending",
        "primary_analysis_interpretable": False,
        "reason": "The preregistered two-auditor blinded manual audit is not complete.",
        "evaluation_hashes": evaluation_hashes,
        "arm_metrics": arm_metrics,
        "paired_analysis": paired,
        "manual_audit_manifest": manual_manifest,
    }
    write_json_atomic(evaluation_root / "summary.json", summary)
    return audit_stage2_evaluation(project, persist=True)


def audit_stage2_evaluation(project: Path, *, persist: bool = False) -> Stage2EvaluationAudit:
    project = project.resolve()
    stage2 = project / "stage2"
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    protocol_audit = audit_stage2_protocol(project)
    check("stage2_protocol_valid", protocol_audit.passed, "frozen Stage 2 protocol failed audit")
    baseline = audit_stage2_baseline(project)
    treatment = audit_stage2_treatment(project)
    check("both_arms_complete", baseline.complete and treatment.complete, "both study arms are not complete")
    try:
        protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
        _ensure_orchestration_manifest(stage2, protocol)
        manifest = read_json(stage2 / "evaluations" / "manifest.json")
        summary = read_json(stage2 / "evaluations" / "summary.json")
        unblinding = read_json(stage2 / "evaluations" / "unblinding.json")
    except Exception as exc:
        protocol = None
        manifest = {}
        summary = {}
        unblinding = {}
        check("evaluation_artifacts_load", False, f"cannot load protected evaluation: {exc}")
    else:
        check("evaluation_artifacts_load", True, "")

    evaluated = 0
    evaluations_integral = True
    seen_cells: set[str] = set()
    if protocol:
        mapping = {str(item["blind_id"]): item for item in unblinding.get("mapping", [])}
        expected_entries = list(manifest.get("evaluation_order", []))
        for entry in expected_entries:
            blind_id = str(entry.get("blind_id"))
            item = mapping.get(blind_id)
            if not item:
                evaluations_integral = False
                violations.append(f"missing unblinding entry: {blind_id}")
                continue
            try:
                cell = next(cell for cell in protocol.cells if cell.cell_id == item["cell_id"])
                registry_path = _cell_dir(stage2, cell) / "final_registry.json"
                registry = StudyClaimRegistry.model_validate(read_json(registry_path))
                evaluation_path = stage2 / "evaluations" / blind_id / "evaluation.json"
                evaluation = StudyRegistryEvaluation.model_validate(read_json(evaluation_path))
                valid = (
                    entry.get("registry_sha256") == sha256_file(registry_path)
                    and evaluation.protocol_id == protocol.protocol_id
                    and evaluation.registry_id == registry.registry_id
                    and evaluation.cell_id == cell.cell_id
                    and evaluation.arm_blinded is True
                    and evaluation.protocol_valid
                    and summary.get("evaluation_hashes", {}).get(blind_id)
                    == sha256_file(evaluation_path)
                )
                if not valid:
                    evaluations_integral = False
                    violations.append(f"protected evaluation evidence is inconsistent: {blind_id}")
                    continue
                evaluated += 1
                seen_cells.add(cell.cell_id)
            except Exception as exc:
                evaluations_integral = False
                violations.append(f"cannot audit protected evaluation {blind_id}: {exc}")
        check(
            "evaluation_manifest_exact",
            len(expected_entries) == len(protocol.cells)
            and len(mapping) == len(protocol.cells),
            "protected evaluation manifest or unblinding map does not match the frozen factorial design",
        )
        check(
            "evaluations_integral",
            evaluations_integral and len(seen_cells) == evaluated,
            "one or more protected evaluations failed integrity",
        )
        manual = summary.get("manual_audit_manifest", {})
        sample_path = Path(str(manual.get("sample_path", "")))
        check(
            "manual_audit_sample_frozen",
            sample_path.is_file()
            and sha256_file(sample_path) == manual.get("sample_sha256")
            and manual.get("status") == "awaiting_two_independent_auditors",
            "the blinded manual-audit sample is missing or changed",
        )
        check(
            "primary_analysis_withheld",
            summary.get("primary_analysis_interpretable") is False,
            "primary analysis was exposed before the manual audit gate",
        )
    expected_registries = len(protocol.cells) if protocol else 18
    complete = evaluated == expected_registries and bool(checks) and all(checks.values())
    paired = dict(summary.get("paired_analysis", {}))
    audit = Stage2EvaluationAudit(
        passed=bool(checks) and all(checks.values()),
        complete=complete,
        checks=checks,
        violations=violations,
        protocol_id=protocol.protocol_id if protocol else None,
        expected_registries=expected_registries,
        evaluated_registries=evaluated,
        arm_metrics=dict(summary.get("arm_metrics", {})),
        paired_effects={
            "mean_paired_unsupported_claim_rate_effect": paired.get(
                "mean_paired_unsupported_claim_rate_effect"
            ),
            "paired_cohen_dz": paired.get("paired_cohen_dz"),
            "tasks_with_lower_unsupported_claim_rate": paired.get(
                "tasks_with_lower_unsupported_claim_rate"
            ),
        },
        manual_audit_required=True,
        primary_analysis_interpretable=False,
    )
    if persist and stage2.is_dir():
        write_json_atomic(stage2 / "evaluation_audit.json", audit)
    return audit
