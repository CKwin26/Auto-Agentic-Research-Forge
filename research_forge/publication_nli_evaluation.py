from __future__ import annotations

import hashlib
import json
import random
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .counterfactual_rebranch import (
    LocalNLIEngine,
    _claim_text_metrics,
    _evidence_chunks,
    _semantic_change_proxy,
)
from .models import utc_now
from .storage import read_json, sha256_file, write_json_atomic
from .study import audit_stage2_protocol
from .study_models import StudyClaimRegistry
from .study_runner import (
    _cell_experiment_packet,
    _claim_evidence_packets,
    audit_registry_structure,
    audit_stage2_baseline,
    audit_stage2_treatment,
)


EVALUATOR_ID = "external_evaluator_cross_family_deberta_v3_nli"
RESULT_DIRNAME = "protected_nli_evaluation"
IMPLEMENTATION_VERSION = "publication-nli-v1"


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _conditional_probabilities(probabilities: dict[str, float]) -> tuple[float, float]:
    entailment = float(probabilities["entailment"])
    contradiction = float(probabilities["contradiction"])
    denominator = entailment + contradiction
    if denominator <= 0.0:
        return 0.5, 0.5
    return entailment / denominator, contradiction / denominator


def _verdict_from_scores(
    scores: list[dict[str, float]], *, entailment_threshold: float, contradiction_threshold: float
) -> tuple[str, dict[str, float]]:
    conditional = [_conditional_probabilities(item) for item in scores]
    max_entailment = max(item[0] for item in conditional)
    max_contradiction = max(item[1] for item in conditional)
    raw_neutral = max(float(item["neutral"]) for item in scores)
    if max_entailment >= entailment_threshold and max_entailment > max_contradiction:
        verdict = "supported"
    elif max_contradiction >= contradiction_threshold and max_contradiction > max_entailment:
        verdict = "unsupported"
    else:
        verdict = "abstain"
    return verdict, {
        "max_conditional_entailment": max_entailment,
        "max_conditional_contradiction": max_contradiction,
        "max_raw_neutral": raw_neutral,
    }


def _evaluate_registry(
    project: Path,
    stage2: Path,
    cell_dir: Path,
    registry: StudyClaimRegistry,
    engine: LocalNLIEngine,
    *,
    entailment_threshold: float,
    contradiction_threshold: float,
) -> dict[str, object]:
    experiments = _cell_experiment_packet(project, cell_dir)
    structural = audit_registry_structure(
        project, stage2, registry, experiments, require_experiment_claim=False
    )
    packets = {
        str(item["claim_id"]): item
        for item in _claim_evidence_packets(stage2, registry, experiments, structural)
    }
    claim_checks = dict(structural.get("claim_checks", {}))
    results: list[dict[str, object]] = []
    for claim in registry.claims:
        checks = {
            str(key): bool(value)
            for key, value in dict(claim_checks.get(claim.claim_id, {})).items()
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        probabilities: dict[str, float] | None = None
        chunks: list[str] = []
        if failed:
            verdict = "unsupported"
            decision_source = "deterministic_structural_failure"
            rationale = "Deterministic evidence checks failed: " + ", ".join(failed)
        elif claim.claim_type.value == "novelty":
            verdict = "abstain"
            decision_source = "novelty_out_of_scope"
            rationale = "Pairwise NLI cannot establish novelty or absence of prior work."
        else:
            chunks = _evidence_chunks(claim, packets[claim.claim_id])
            if not chunks:
                verdict = "abstain"
                decision_source = "no_compatible_evidence"
                rationale = "No NLI-compatible linked evidence chunk was available."
            else:
                raw_scores = engine.score([(premise, claim.claim_text) for premise in chunks])
                verdict, probabilities = _verdict_from_scores(
                    raw_scores,
                    entailment_threshold=entailment_threshold,
                    contradiction_threshold=contradiction_threshold,
                )
                decision_source = "calibrated_cross_family_nli"
                rationale = (
                    f"Conditional E/(E+C) decision across {len(chunks)} linked evidence chunks; "
                    f"thresholds={entailment_threshold:.2f}/{contradiction_threshold:.2f}."
                )
        results.append(
            {
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type.value,
                "verdict": verdict,
                "decision_source": decision_source,
                "rationale": rationale,
                "probabilities": probabilities,
                "evidence_chunk_count": len(chunks),
                "structural_checks": checks,
                "text_metrics": _claim_text_metrics(claim.claim_text),
            }
        )

    eligible = [item for item in results if item["claim_type"] != "novelty"]
    nli_evaluable = [
        item for item in eligible if item["decision_source"] == "calibrated_cross_family_nli"
    ]
    unsupported = sum(item["verdict"] == "unsupported" for item in eligible)
    supported = sum(item["verdict"] == "supported" for item in eligible)
    abstained = sum(item["verdict"] == "abstain" for item in eligible)
    return {
        "registry_id": registry.registry_id,
        "claim_count": len(results),
        "eligible_claim_count": len(eligible),
        "nli_evaluable_claim_count": len(nli_evaluable),
        "supported_count": supported,
        "unsupported_count": unsupported,
        "abstain_count": abstained,
        "unsupported_claim_rate": unsupported / len(eligible) if eligible else None,
        "non_entailment_rate": (unsupported + abstained) / len(eligible) if eligible else None,
        "nli_coverage_rate": len(nli_evaluable) / len(eligible) if eligible else 0.0,
        "structural_protocol_valid": structural.get("passed") is True,
        "structural_violations": list(structural.get("violations", [])),
        "claims": results,
    }


def _hierarchical_bootstrap(
    rows: list[dict[str, object]], *, metric: str, resamples: int, seed: int = 20260719
) -> dict[str, object]:
    by_task: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_task.setdefault(str(row["task_id"]), []).append(row)
    tasks = sorted(by_task)
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(resamples):
        sampled_tasks = [rng.choice(tasks) for _ in tasks]
        effects: list[float] = []
        for task in sampled_tasks:
            task_rows = by_task[task]
            sampled_rows = [rng.choice(task_rows) for _ in task_rows]
            effects.extend(float(row[metric]) for row in sampled_rows)
        values.append(statistics.fmean(effects))
    ordered = sorted(values)
    lower = ordered[int(0.025 * (resamples - 1))]
    upper = ordered[int(0.975 * (resamples - 1))]
    return {
        "resamples": resamples,
        "seed": seed,
        "mean": statistics.fmean(float(row[metric]) for row in rows),
        "median": statistics.median(float(row[metric]) for row in rows),
        "ci_95": [lower, upper],
    }


def _leave_one_task_out(rows: list[dict[str, object]], metric: str) -> list[dict[str, object]]:
    tasks = sorted({str(row["task_id"]) for row in rows})
    return [
        {
            "omitted_task": task,
            "pair_count": sum(str(row["task_id"]) != task for row in rows),
            "mean_effect": statistics.fmean(
                float(row[metric]) for row in rows if str(row["task_id"]) != task
            ),
        }
        for task in tasks
    ]


def _revision_trace(baseline: StudyClaimRegistry, treatment: StudyClaimRegistry) -> list[dict[str, object]]:
    treatment_by_id = {claim.claim_id: claim for claim in treatment.claims}
    return [
        _semantic_change_proxy(claim, treatment_by_id.get(claim.claim_id))
        for claim in baseline.claims
    ]


def run_publication_nli_evaluation(project: Path) -> dict[str, object]:
    project = project.resolve()
    stage2 = project / "stage2"
    protocol_audit = audit_stage2_protocol(project, persist=True)
    baseline_audit = audit_stage2_baseline(project, persist=True)
    treatment_audit = audit_stage2_treatment(project, persist=True)
    if not protocol_audit.passed:
        raise ValueError("protocol audit failed: " + "; ".join(protocol_audit.violations))
    if not baseline_audit.complete or not treatment_audit.complete:
        raise ValueError("independent evaluation requires all 80 frozen branches")

    protocol = read_json(stage2 / "protocol.json")
    calibration_path = project / str(protocol["independent_calibration_contract"])
    calibration = read_json(calibration_path)
    if calibration.get("passed") is not True or calibration.get("evaluator") != EVALUATOR_ID:
        raise ValueError("independent calibration contract is not valid for the protected evaluator")
    if protocol.get("protected_evaluator") != EVALUATOR_ID:
        raise ValueError("protocol evaluator binding drifted")
    model = dict(calibration["model"])
    model_path = Path(str(model["model_path"]))
    tokenizer_path = Path(str(model["tokenizer_path"]))
    if sha256_file(model_path) != model["model_sha256"]:
        raise ValueError("protected NLI model hash drifted")
    if sha256_file(tokenizer_path) != model["tokenizer_sha256"]:
        raise ValueError("protected NLI tokenizer hash drifted")

    result_root = stage2 / RESULT_DIRNAME
    result_root.mkdir(parents=True, exist_ok=True)
    implementation_path = Path(__file__).resolve()
    manifest = {
        "schema_version": 1,
        "implementation_version": IMPLEMENTATION_VERSION,
        "implementation_sha256": sha256_file(implementation_path),
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha256_file(stage2 / "protocol.json"),
        "calibration_contract_sha256": sha256_file(calibration_path),
        "evaluator": EVALUATOR_ID,
        "entailment_threshold": float(calibration["entailment_threshold"]),
        "contradiction_threshold": float(calibration["contradiction_threshold"]),
        "arm_blinded_inference": True,
        "human_validation_complete": False,
    }
    manifest_path = result_root / "manifest.json"
    if manifest_path.is_file() and read_json(manifest_path) != manifest:
        raise ValueError("protected NLI evaluation manifest drifted")
    if not manifest_path.is_file():
        write_json_atomic(manifest_path, manifest)

    engine = LocalNLIEngine(model_path, tokenizer_path)
    started = time.perf_counter()
    pairs: list[dict[str, object]] = []
    evaluation_hashes: dict[str, str] = {}
    for pair_sequence in range(1, 41):
        arm_results: dict[str, dict[str, object]] = {}
        registries: dict[str, StudyClaimRegistry] = {}
        for arm, code in (("baseline", "b"), ("treatment", "t")):
            cell_dir = stage2 / "r" / code / f"{pair_sequence:02d}"
            registry_path = cell_dir / "final_registry.json"
            registry = StudyClaimRegistry.model_validate(read_json(registry_path))
            blind_id = "blind-" + _stable_hash(
                {
                    "protocol_id": protocol["protocol_id"],
                    "registry_sha256": sha256_file(registry_path),
                }
            )[:20]
            evaluation = _evaluate_registry(
                project,
                stage2,
                cell_dir,
                registry,
                engine,
                entailment_threshold=manifest["entailment_threshold"],
                contradiction_threshold=manifest["contradiction_threshold"],
            )
            evaluation.update(
                {
                    "schema_version": 1,
                    "protocol_id": protocol["protocol_id"],
                    "blind_id": blind_id,
                    "arm_blinded_during_inference": True,
                }
            )
            destination = result_root / "evaluations" / blind_id / "evaluation.json"
            write_json_atomic(destination, evaluation)
            evaluation_hashes[blind_id] = sha256_file(destination)
            arm_results[arm] = evaluation
            registries[arm] = registry
        baseline = registries["baseline"]
        treatment = registries["treatment"]
        changes = _revision_trace(baseline, treatment)
        pair = {
            "pair_sequence": pair_sequence,
            "task_id": baseline.task_pack,
            "seed": baseline.seed,
            "baseline": arm_results["baseline"],
            "treatment": arm_results["treatment"],
            "unsupported_claim_rate_effect": (
                float(arm_results["treatment"]["unsupported_claim_rate"])
                - float(arm_results["baseline"]["unsupported_claim_rate"])
            ),
            "claim_retention_rate": len(treatment.claims) / len(baseline.claims)
            if baseline.claims
            else 1.0,
            "semantic_changes": changes,
            "semantic_change_counts": dict(
                sorted(Counter(str(item["change_type"]) for item in changes).items())
            ),
        }
        pairs.append(pair)

    metric = "unsupported_claim_rate_effect"
    summary = {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "completed_at": utc_now(),
        "analysis_status": "protected_independent_nli_complete_human_audit_pending",
        "primary_analysis_interpretable": False,
        "reason": "The preregistered two-human blinded audit remains deferred.",
        "pair_count": len(pairs),
        "primary_metric": metric,
        "paired_analysis": _hierarchical_bootstrap(
            pairs, metric=metric, resamples=int(protocol["bootstrap_resamples"])
        ),
        "leave_one_task_out": _leave_one_task_out(pairs, metric),
        "pair_results": pairs,
        "evaluation_hashes": evaluation_hashes,
        "telemetry": {
            "wall_clock_seconds": time.perf_counter() - started,
            "onnx_inference_calls": engine.inference_calls,
            "encoded_pair_count": engine.encoded_pair_count,
            "encoded_token_count": engine.encoded_token_count,
            "unique_pair_cache_size": len(engine.cache),
            "external_content_upload": False,
        },
        "construct_boundary": (
            "Retention and semantic-change fields are deterministic lexical proxies; "
            "scientific usefulness and human truth remain unvalidated until the deferred audit."
        ),
    }
    write_json_atomic(result_root / "summary.json", summary)
    write_json_atomic(
        result_root / "unblinding.json",
        {
            "schema_version": 1,
            "created_only_after_all_evaluations": True,
            "mapping": [
                {
                    "pair_sequence": pair["pair_sequence"],
                    "task_id": pair["task_id"],
                    "seed": pair["seed"],
                }
                for pair in pairs
            ],
        },
    )
    return summary
