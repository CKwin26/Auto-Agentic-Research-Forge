from __future__ import annotations

import hashlib
import json
import math
import random
import re
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
from .models import RunRecord, utc_now
from .storage import read_json, safe_relative, sha256_file, write_json_atomic
from .study import audit_stage2_protocol
from .study_models import StudyClaim, StudyClaimRegistry
from .study_runner import (
    _claim_evidence_packets,
    audit_registry_structure,
    audit_stage2_baseline,
    audit_stage2_treatment,
)


EVALUATOR_ID = "external_evaluator_cross_family_deberta_v3_nli"
RESULT_DIRNAME = "protected_nli_evaluation"
IMPLEMENTATION_VERSION = "publication-nli-v4-context-bound-deterministic-metrics"


def _implementation_contract_path(project: Path, protocol: dict[str, object]) -> Path:
    """Keep evaluator freezes immutable across successor protocol revisions."""
    revision = int(protocol.get("protocol_revision", 0))
    if revision <= 0:
        raise ValueError("protected NLI protocol is missing a positive revision")
    return project / "design_revisions" / f"protected_nli_implementation_contract_r{revision}.json"


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


_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def _metric_value_matches(
    claim: StudyClaim,
    experiment: dict[str, object],
) -> tuple[str, float] | None:
    if len(claim.metric_values) != 1:
        return None
    metric, declared = next(iter(claim.metric_values.items()))
    aggregate = experiment.get("aggregate_metrics")
    if not isinstance(aggregate, dict) or metric not in aggregate:
        return None
    observed = float(aggregate[metric])
    if not math.isclose(float(declared), observed, rel_tol=1e-12, abs_tol=1e-12):
        return None
    return metric, observed


def _deterministic_experiment_support(
    claim: StudyClaim,
    packet: dict[str, object],
) -> str | None:
    """Return a support rationale only for narrowly decidable numeric claims.

    The rule intentionally covers canonical exact-metric statements and
    canonical comparisons with a hash-bound frozen target.  All other semantic
    relations remain with the protected NLI evaluator.
    """

    if claim.claim_type.value != "experiment":
        return None
    experiment = packet.get("linked_experiment_evidence")
    if not isinstance(experiment, dict):
        return None
    if experiment.get("valid") is not True or experiment.get("isolation_verified") is not True:
        return None
    match = _metric_value_matches(claim, experiment)
    if match is None:
        return None
    metric, observed = match
    text = " ".join(claim.claim_text.strip().split())
    lower = text.lower()
    numbers = [float(item) for item in _NUMBER_PATTERN.findall(text)]
    metric_present = re.search(rf"\b{re.escape(metric.lower())}\b", lower) is not None

    comparative = bool(
        re.search(r"\b(?:did not reach|below (?:the )?(?:task )?target)\b", lower)
    )
    if comparative:
        task = packet.get("linked_task_specification")
        if not isinstance(task, dict):
            return None
        if not task.get("task_specification_sha256"):
            return None
        raw_target = task.get("target_score")
        if not isinstance(raw_target, (int, float)):
            return None
        target = float(raw_target)
        direction = str(task.get("direction", "")).lower()
        target_present = any(
            math.isclose(value, target, rel_tol=1e-12, abs_tol=1e-12)
            for value in numbers
        )
        relation_holds = (
            (direction == "maximize" and observed < target)
            or (direction == "minimize" and observed > target)
        )
        if target_present and relation_holds:
            return (
                "Deterministic task-context support: the valid isolated run's exact metric and "
                "the hash-bound frozen target satisfy the stated comparison."
            )
        return None

    canonical_exact = bool(
        re.fullmatch(
            r"the (?:linked )?(?:(?:valid|verified|evaluated|baseline|candidate) )*run "
            r"(?:achieved|recorded|obtained|had) (?:an? )?[a-z][a-z0-9 _-]* "
            r"(?:of|=) -?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
            r"(?: on the(?: .+?)? task)?\.?",
            text,
            flags=re.IGNORECASE,
        )
    )
    observed_present = any(
        math.isclose(value, observed, rel_tol=1e-12, abs_tol=1e-12)
        for value in numbers
    )
    if canonical_exact and metric_present and len(numbers) == 1 and observed_present:
        return (
            "Deterministic exact-metric support: claim metric and value equal the valid isolated "
            "run record."
        )
    return None


def _publication_cell_experiment_packet(
    project: Path, stage2: Path, cell_dir: Path
) -> list[dict[str, object]]:
    """Load only the hash-bound shared evidence assigned to a formal branch.

    Publication branches deliberately do not duplicate evidence.  Resolving the
    exact shared pair here prevents a branch from selecting an arbitrary sibling
    artifact while keeping the evidence-location rule inside the frozen evaluator.
    """
    binding_path = cell_dir / "shared_artifact_binding.json"
    if not binding_path.is_file():
        raise ValueError(f"publication branch is missing shared evidence binding: {cell_dir}")
    binding = read_json(binding_path)
    protocol = read_json(stage2 / "protocol.json")
    if binding.get("protocol_id") != protocol.get("protocol_id"):
        raise ValueError("publication shared binding belongs to a different protocol")
    relative_registry = str(binding.get("shared_registry_path", ""))
    registry_path = safe_relative(project, relative_registry)
    if registry_path.name != "shared_registry.json" or not registry_path.is_file():
        raise ValueError(f"publication shared registry is unavailable: {relative_registry}")
    if sha256_file(registry_path) != binding.get("shared_registry_sha256"):
        raise ValueError("publication shared registry hash does not match branch binding")
    pair_dir = registry_path.parent
    if pair_dir.parent.parent != stage2 / "shared":
        raise ValueError("publication shared registry path is outside the frozen pair root")
    pair_manifest_path = pair_dir / "pair_manifest.json"
    if not pair_manifest_path.is_file():
        raise ValueError("publication shared pair manifest is missing")
    pair_manifest = read_json(pair_manifest_path)
    if pair_manifest.get("pair_key") != binding.get("pair_key"):
        raise ValueError("publication shared pair key does not match branch binding")
    evidence_root = pair_dir / "evidence"
    if not evidence_root.is_dir():
        raise ValueError("publication shared evidence root is missing")

    packet: list[dict[str, object]] = []
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
        raise ValueError(f"publication shared evidence packet is empty: {pair_dir}")
    return packet


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
    experiments = _publication_cell_experiment_packet(project, stage2, cell_dir)
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
            deterministic_support = _deterministic_experiment_support(
                claim, packets[claim.claim_id]
            )
            if deterministic_support is not None:
                verdict = "supported"
                decision_source = "deterministic_numeric_entailment"
                rationale = deterministic_support
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


def _construct_metrics(
    registry: StudyClaimRegistry,
    evaluation: dict[str, object],
    complete: dict[str, object],
) -> dict[str, object]:
    claims = list(evaluation["claims"])
    experiment = [item for item in claims if item["claim_type"] == "experiment"]
    literature = [item for item in claims if item["claim_type"] == "literature"]
    bound = [
        claim
        for claim in registry.claims
        if claim.artifact_paths or claim.source_ids or claim.experiment_run_id
    ]
    text_metrics = [_claim_text_metrics(claim.claim_text) for claim in registry.claims]
    return {
        "experiment_detail_error_rate": (
            sum(item["verdict"] == "unsupported" for item in experiment) / len(experiment)
            if experiment
            else None
        ),
        "citation_correctness_proxy": (
            sum(item["verdict"] == "supported" for item in literature) / len(literature)
            if literature
            else None
        ),
        "evidence_binding_coverage": len(bound) / len(registry.claims)
        if registry.claims
        else 0.0,
        "verifier_abstention_rate": (
            int(evaluation["abstain_count"]) / int(evaluation["eligible_claim_count"])
            if int(evaluation["eligible_claim_count"])
            else None
        ),
        "failure_mode_count": int(evaluation["unsupported_count"])
        + int(evaluation["abstain_count"]),
        "informativeness_vector": {
            "claim_count": len(registry.claims),
            "word_count": sum(item["word_count"] for item in text_metrics),
            "numeric_token_count": sum(item["numeric_token_count"] for item in text_metrics),
            "evidence_bound_claim_count": len(bound),
        },
        "task_native_score": float(complete["task_native_score"]),
        "wall_clock_runtime_seconds": float(complete["wall_clock_seconds"]),
        "token_count": int(complete["token_count"]),
        "model_call_count": int(complete["model_call_count"]),
        "monetary_cost_usd": float(complete["monetary_cost_usd"]),
        "audit_false_positive_rate": None,
    }


def _arm_metrics(pairs: list[dict[str, object]], arm: str) -> dict[str, object]:
    rows = [dict(pair[arm]) for pair in pairs]
    eligible = sum(int(row["eligible_claim_count"]) for row in rows)
    unsupported = sum(int(row["unsupported_count"]) for row in rows)
    abstained = sum(int(row["abstain_count"]) for row in rows)
    constructs = [dict(row["construct_metrics"]) for row in rows]
    return {
        "registry_count": len(rows),
        "claim_count": sum(int(row["claim_count"]) for row in rows),
        "eligible_claim_count": eligible,
        "unsupported_count": unsupported,
        "abstain_count": abstained,
        "unsupported_claim_rate": unsupported / eligible if eligible else None,
        "non_entailment_rate": (unsupported + abstained) / eligible if eligible else None,
        "mean_task_native_score": statistics.fmean(
            float(item["task_native_score"]) for item in constructs
        ),
        "total_wall_clock_runtime_seconds": sum(
            float(item["wall_clock_runtime_seconds"]) for item in constructs
        ),
        "total_token_count": sum(int(item["token_count"]) for item in constructs),
        "total_model_call_count": sum(int(item["model_call_count"]) for item in constructs),
        "total_monetary_cost_usd": sum(
            float(item["monetary_cost_usd"]) for item in constructs
        ),
    }


def freeze_publication_nli_implementation(project: Path) -> dict[str, object]:
    project = project.resolve()
    stage2 = project / "stage2"
    protocol = read_json(stage2 / "protocol.json")
    calibration_path = project / str(protocol["independent_calibration_contract"])
    calibration = read_json(calibration_path)
    completed = len(list((stage2 / "r").glob("*/*/complete.json")))
    contract = {
        "schema_version": 1,
        "frozen_at": utc_now(),
        "implementation_version": IMPLEMENTATION_VERSION,
        "implementation_path": "research_forge/publication_nli_evaluation.py",
        "implementation_sha256": sha256_file(Path(__file__).resolve()),
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha256_file(stage2 / "protocol.json"),
        "calibration_contract_sha256": sha256_file(calibration_path),
        "evaluator": EVALUATOR_ID,
        "entailment_threshold": float(calibration["entailment_threshold"]),
        "contradiction_threshold": float(calibration["contradiction_threshold"]),
        "formal_cells_completed_when_implementation_frozen": completed,
        "treatment_effects_inspected": False,
        "human_validation_complete": False,
    }
    path = _implementation_contract_path(project, protocol)
    if path.is_file():
        existing = read_json(path)
        immutable = {key: value for key, value in existing.items() if key != "frozen_at"}
        proposed = {key: value for key, value in contract.items() if key != "frozen_at"}
        if immutable != proposed:
            raise ValueError("protected NLI implementation contract already exists and drifted")
        return existing
    write_json_atomic(path, contract)
    return contract


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
    implementation_contract_path = _implementation_contract_path(project, protocol)
    implementation_contract = read_json(implementation_contract_path)
    if implementation_contract.get("protocol_id") != protocol["protocol_id"]:
        raise ValueError("protected NLI implementation belongs to another protocol")
    if implementation_contract.get("implementation_sha256") != sha256_file(
        Path(__file__).resolve()
    ):
        raise ValueError("protected NLI implementation drifted after freeze")
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
        "implementation_contract_sha256": sha256_file(implementation_contract_path),
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
    blinded_items: list[dict[str, Any]] = []
    for pair_sequence in range(1, 41):
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
            blinded_items.append(
                {
                    "blind_id": blind_id,
                    "pair_sequence": pair_sequence,
                    "arm": arm,
                    "cell_dir": cell_dir,
                    "registry_path": registry_path,
                    "registry": registry,
                }
            )
    if len({str(item["blind_id"]) for item in blinded_items}) != 80:
        raise ValueError("protected evaluator blind IDs are not unique")

    evaluated: dict[str, dict[str, object]] = {}
    for item in sorted(blinded_items, key=lambda value: str(value["blind_id"])):
            registry = item["registry"]
            evaluation = _evaluate_registry(
                project,
                stage2,
                item["cell_dir"],
                registry,
                engine,
                entailment_threshold=manifest["entailment_threshold"],
                contradiction_threshold=manifest["contradiction_threshold"],
            )
            evaluation.update(
                {
                    "schema_version": 1,
                    "protocol_id": protocol["protocol_id"],
                    "blind_id": item["blind_id"],
                    "arm_blinded_during_inference": True,
                }
            )
            complete = read_json(item["cell_dir"] / "complete.json")
            evaluation["construct_metrics"] = _construct_metrics(
                registry, evaluation, complete
            )
            destination = result_root / "evaluations" / item["blind_id"] / "evaluation.json"
            write_json_atomic(destination, evaluation)
            evaluation_hashes[str(item["blind_id"])] = sha256_file(destination)
            evaluated[str(item["blind_id"])] = evaluation

    for pair_sequence in range(1, 41):
        pair_items = [
            item for item in blinded_items if item["pair_sequence"] == pair_sequence
        ]
        arm_items = {str(item["arm"]): item for item in pair_items}
        arm_results = {
            arm: evaluated[str(item["blind_id"])] for arm, item in arm_items.items()
        }
        registries = {arm: item["registry"] for arm, item in arm_items.items()}
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
            "secondary_metric_effects": {
                name: float(arm_results["treatment"]["construct_metrics"][name])
                - float(arm_results["baseline"]["construct_metrics"][name])
                for name in (
                    "evidence_binding_coverage",
                    "verifier_abstention_rate",
                    "task_native_score",
                    "wall_clock_runtime_seconds",
                    "token_count",
                    "model_call_count",
                    "monetary_cost_usd",
                )
                if arm_results["baseline"]["construct_metrics"][name] is not None
                and arm_results["treatment"]["construct_metrics"][name] is not None
            },
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
        "arm_metrics": {
            arm: _arm_metrics(pairs, arm) for arm in ("baseline", "treatment")
        },
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
                    "blind_id": item["blind_id"],
                    "pair_sequence": item["pair_sequence"],
                    "arm": item["arm"],
                    "cell_id": item["registry"].cell_id,
                    "task_id": item["registry"].task_pack,
                    "seed": item["registry"].seed,
                    "registry_id": item["registry"].registry_id,
                    "registry_sha256": sha256_file(item["registry_path"]),
                }
                for item in sorted(blinded_items, key=lambda value: str(value["blind_id"]))
            ],
        },
    )
    return summary
