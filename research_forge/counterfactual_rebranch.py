from __future__ import annotations

import hashlib
import importlib.metadata
import itertools
import json
import os
import re
import shutil
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .models import utc_now
from .storage import read_json, sha256_file, sha256_tree, write_json_atomic
from .study import audit_stage2_protocol
from .study_models import StudyClaim, StudyClaimRegistry
from .study_runner import (
    _cell_experiment_packet,
    _claim_evidence_packets,
    audit_stage2_baseline,
    audit_registry_structure,
    audit_stage2_treatment,
)


REBRANCH_DIRNAME = "counterfactual_rebranch_v1"
REBRANCH_RELATIVE_ROOT = Path("post_review") / REBRANCH_DIRNAME
REBRANCH_IMPLEMENTATION_VERSION = "same-artifact-nli-v1"
ARMS = (
    "no_gate",
    "verify_only_selective_delivery",
    "verify_revise_no_recheck",
    "verify_revise_recheck",
)

NLI_MODEL_ID = "cross-encoder/nli-deberta-v3-base"
NLI_MODEL_REVISION = "6c749ce3425cd33b46d187e45b92bbf96ee12ec7"
NLI_MODEL_FILENAME = "model_quint8_avx2.onnx"
NLI_MODEL_REPOSITORY_PATH = "onnx/model_quint8_avx2.onnx"
NLI_MODEL_SHA256 = "36784d97274ef019db9f54fd16812f9a460e787703658b0f3b2200999692680b"
NLI_MODEL_BYTES = 244_422_412
NLI_TOKENIZER_FILENAME = "tokenizer.json"
NLI_TOKENIZER_SHA256 = "5124ef2ead1a10a717703bc436de7f353da76d6340e4587719b42b1693707964"
NLI_MAX_LENGTH = 512
NLI_ENTAILMENT_THRESHOLD = 0.5
NLI_CONTRADICTION_THRESHOLD = 0.5
NLI_LABELS = ("contradiction", "entailment", "neutral")
NLI_ONNXRUNTIME_VERSION = "1.27.0"
NLI_TOKENIZERS_VERSION = "0.23.1"

_HEDGE_PATTERNS = (
    r"\bmay\b",
    r"\bmight\b",
    r"\bcould\b",
    r"\bsuggests?\b",
    r"\bappears?\b",
    r"\blikely\b",
    r"\bapproximately\b",
    r"\babout\b",
    r"\bat least\b",
    r"\bat most\b",
    r"\bconsistent with\b",
    r"\bdoes not establish\b",
    r"\bwithin (?:the )?(?:provided|frozen|bounded|reviewed)\b",
)
_MODAL_PATTERN = re.compile(r"\b(?:may|might|could|can|would|should|must)\b", re.I)
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?(?![A-Za-z])")
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rebranch_root(project: Path) -> Path:
    return project.resolve() / REBRANCH_RELATIVE_ROOT


def _model_cache_root() -> Path:
    configured = os.getenv("RESEARCH_FORGE_MODEL_CACHE")
    if configured:
        return Path(configured).expanduser().resolve() / NLI_MODEL_REVISION
    return (Path.home() / ".cache" / "research-forge" / "models" / NLI_MODEL_REVISION).resolve()


def _implementation_sha256() -> str:
    return sha256_file(Path(__file__).resolve())


def _download_atomic(url: str, destination: Path, expected_sha256: str) -> None:
    if destination.is_file() and sha256_file(destination) == expected_sha256:
        return
    raise RuntimeError(
        "direct model download is disabled. Acquire the pinned Hugging Face "
        f"resource through RetrievalGateway ({url}), then place its verified "
        f"artifact at {destination}; expected sha256={expected_sha256}"
    )


def ensure_nli_assets() -> dict[str, object]:
    cache = _model_cache_root()
    model_path = cache / NLI_MODEL_FILENAME
    tokenizer_path = cache / NLI_TOKENIZER_FILENAME
    base = f"https://huggingface.co/{NLI_MODEL_ID}/resolve/{NLI_MODEL_REVISION}"
    _download_atomic(
        f"{base}/{NLI_MODEL_REPOSITORY_PATH}?download=true",
        model_path,
        NLI_MODEL_SHA256,
    )
    _download_atomic(
        f"{base}/{NLI_TOKENIZER_FILENAME}?download=true",
        tokenizer_path,
        NLI_TOKENIZER_SHA256,
    )
    return {
        "model_id": NLI_MODEL_ID,
        "revision": NLI_MODEL_REVISION,
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "model_bytes": model_path.stat().st_size,
        "tokenizer_path": str(tokenizer_path),
        "tokenizer_sha256": sha256_file(tokenizer_path),
        "onnxruntime_version": importlib.metadata.version("onnxruntime"),
        "tokenizers_version": importlib.metadata.version("tokenizers"),
        "labels": list(NLI_LABELS),
        "max_length": NLI_MAX_LENGTH,
    }


def _required_cell_files() -> tuple[str, ...]:
    return (
        "gate_input_registry.json",
        "gate_first_pass.json",
        "revised_registry.json",
        "gate_recheck.json",
        "final_registry.json",
        "gate_trace.json",
        "complete.json",
        "structural_audit.json",
    )


def _derive_verify_only_registry(
    registry: StudyClaimRegistry, first_pass: dict[str, object]
) -> StudyClaimRegistry:
    judgments = list(first_pass.get("judgments", []))
    supported = {
        str(item.get("claim_id"))
        for item in judgments
        if isinstance(item, dict) and item.get("verdict") == "supported"
    }
    claims = [claim for claim in registry.claims if claim.claim_id in supported]
    registry_id = "registry-" + _stable_hash(
        {
            "source_registry_id": registry.registry_id,
            "arm": "verify_only_selective_delivery",
            "retained_claim_ids": [claim.claim_id for claim in claims],
        }
    )[:12]
    return registry.model_copy(
        update={
            "registry_id": registry_id,
            "final_output_text": "\n".join(claim.claim_text for claim in claims),
            "claims": claims,
        }
    )


def _materialize_branch_inputs(project: Path, staging: Path) -> list[dict[str, object]]:
    stage2 = project / "stage2"
    raw_protocol = read_json(stage2 / "protocol.json")
    treatment_cells = [
        item for item in raw_protocol.get("cells", []) if item.get("arm") == "treatment"
    ]
    if len(treatment_cells) != 9:
        raise ValueError("counterfactual rebranch requires exactly nine completed treatment cells")
    bindings: list[dict[str, object]] = []
    for pair_index, cell in enumerate(treatment_cells, start=1):
        cell_dir = stage2 / "r" / "t" / f"{pair_index:02d}"
        for name in _required_cell_files():
            if not (cell_dir / name).is_file():
                raise FileNotFoundError(f"missing treatment cell artifact: {cell_dir / name}")
        initial = StudyClaimRegistry.model_validate(read_json(cell_dir / "gate_input_registry.json"))
        revised = StudyClaimRegistry.model_validate(read_json(cell_dir / "revised_registry.json"))
        final = StudyClaimRegistry.model_validate(read_json(cell_dir / "final_registry.json"))
        first_pass = read_json(cell_dir / "gate_first_pass.json")
        trace = read_json(cell_dir / "gate_trace.json")
        if trace.get("gate_input_registry_id") != initial.registry_id:
            raise ValueError(f"gate trace does not bind the shared registry in pair {pair_index}")
        if trace.get("revised_registry_id") != revised.registry_id:
            raise ValueError(f"gate trace does not bind the revised registry in pair {pair_index}")
        if trace.get("final_registry_id") != final.registry_id:
            raise ValueError(f"gate trace does not bind the final registry in pair {pair_index}")
        verify_only = _derive_verify_only_registry(initial, first_pass)
        arm_registries = {
            "no_gate": initial,
            "verify_only_selective_delivery": verify_only,
            "verify_revise_no_recheck": revised,
            "verify_revise_recheck": final,
        }
        pair_id = f"pair-{pair_index:02d}"
        branch_hashes: dict[str, str] = {}
        for arm, registry in arm_registries.items():
            destination = staging / "branches" / pair_id / arm / "registry.json"
            write_json_atomic(destination, registry)
            branch_hashes[arm] = sha256_file(destination)
        source_hashes = {
            name: sha256_file(cell_dir / name) for name in _required_cell_files()
        }
        bindings.append(
            {
                "pair_id": pair_id,
                "source_cell_id": str(cell["cell_id"]),
                "task_id": str(cell["task_id"]),
                "seed": int(cell["seed"]),
                "source_cell_relative_path": cell_dir.relative_to(project).as_posix(),
                "shared_registry_id": initial.registry_id,
                "shared_registry_sha256": source_hashes["gate_input_registry.json"],
                "source_hashes": source_hashes,
                "evidence_tree_sha256": sha256_tree(cell_dir / "evidence"),
                "branch_registry_hashes": branch_hashes,
            }
        )
    return bindings


def _historical_stage2_evidence_audit(project: Path) -> dict[str, object]:
    """Verify completed historical evidence without requiring the live controller to stay old."""
    protocol = audit_stage2_protocol(project, persist=True)
    protocol_checks = dict(protocol.checks)
    protocol_exception_is_scoped = (
        protocol.violations
        == ["the live shared controller differs from the frozen Stage 2 snapshot"]
        and protocol_checks.get("live_controller_matches_frozen") is False
        and all(
            passed
            for name, passed in protocol_checks.items()
            if name != "live_controller_matches_frozen"
        )
    )
    baseline = audit_stage2_baseline(project, persist=True)
    baseline_checks = dict(baseline.checks)
    baseline_integral = (
        baseline.completed_cells == 9
        and baseline_checks.get("stage2_protocol_valid") is False
        and all(
            passed
            for name, passed in baseline_checks.items()
            if name != "stage2_protocol_valid"
        )
    )
    treatment = audit_stage2_treatment(project, persist=True)
    treatment_checks = dict(treatment.checks)
    treatment_integral = (
        treatment.completed_cells == 9
        and treatment_checks.get("stage2_protocol_valid") is False
        and treatment_checks.get("baseline_matrix_complete") is False
        and all(
            passed
            for name, passed in treatment_checks.items()
            if name not in {"stage2_protocol_valid", "baseline_matrix_complete"}
        )
    )
    passed = protocol_exception_is_scoped and baseline_integral and treatment_integral
    return {
        "passed": passed,
        "boundary": (
            "All frozen Stage 2 artifacts and all 18 completed cells are integral. The sole "
            "global-audit exception is that the current development controller no longer equals "
            "the historical frozen controller; this rebranch never executes that controller."
        ),
        "protocol_checks": protocol_checks,
        "protocol_violations": list(protocol.violations),
        "baseline_checks": baseline_checks,
        "baseline_completed_cells": baseline.completed_cells,
        "treatment_checks": treatment_checks,
        "treatment_completed_cells": treatment.completed_cells,
    }


def freeze_counterfactual_rebranch(project: Path) -> dict[str, object]:
    project = project.resolve()
    root = _rebranch_root(project)
    if root.is_dir():
        audit = audit_counterfactual_rebranch(project)
        if not audit["passed"]:
            raise ValueError("existing counterfactual rebranch failed audit: " + "; ".join(audit["violations"]))
        return read_json(root / "protocol.json")
    source_audit = _historical_stage2_evidence_audit(project)
    if not source_audit["passed"]:
        raise ValueError(
            "historical Stage 2 evidence is not safe to rebranch: "
            + json.dumps(source_audit, ensure_ascii=False, sort_keys=True)
        )
    model = ensure_nli_assets()
    staging = root.with_name(f".{root.name}-{os.urandom(4).hex()}.tmp")
    staging.mkdir(parents=True)
    try:
        bindings = _materialize_branch_inputs(project, staging)
        seed = {
            "source_protocol_sha256": sha256_file(project / "stage2" / "protocol.json"),
            "bindings": bindings,
            "model_revision": NLI_MODEL_REVISION,
            "arms": ARMS,
        }
        protocol_id = "rebranch-" + _stable_hash(seed)[:12]
        protocol = {
            "schema_version": 1,
            "protocol_id": protocol_id,
            "frozen_at": utc_now(),
            "title": "Same-artifact four-arm delivery-gate rebranch with independent local NLI evaluation",
            "design_class": "retrospective_frozen_counterfactual_rebranch",
            "source_protocol_id": read_json(project / "stage2" / "protocol.json")["protocol_id"],
            "source_protocol_sha256": seed["source_protocol_sha256"],
            "source_audit_boundary": source_audit,
            "evaluator_implementation": {
                "version": REBRANCH_IMPLEMENTATION_VERSION,
                "relative_path": "research_forge/counterfactual_rebranch.py",
                "sha256": _implementation_sha256(),
            },
            "research_question": (
                "When each arm starts from the identical frozen pre-gate claim registry, how do "
                "verification, selective delivery, constrained revision, and recheck change independent "
                "NLI support labels and claim informativeness proxies?"
            ),
            "claim_ceiling": "post_hoc_cross_family_proxy_association",
            "pairs": bindings,
            "arms": [
                {
                    "arm": "no_gate",
                    "derivation": "identity branch of the frozen gate_input_registry",
                },
                {
                    "arm": "verify_only_selective_delivery",
                    "derivation": "retain only first-pass supported claims; no revision or recheck",
                },
                {
                    "arm": "verify_revise_no_recheck",
                    "derivation": "frozen revised_registry before the recheck decision",
                },
                {
                    "arm": "verify_revise_recheck",
                    "derivation": "frozen final_registry after recheck and removal policy",
                },
            ],
            "evaluator": {
                **model,
                "runtime": "local_onnxruntime_cpu",
                "network_during_inference": "not_required",
                "external_content_upload": False,
                "entailment_threshold": NLI_ENTAILMENT_THRESHOLD,
                "contradiction_threshold": NLI_CONTRADICTION_THRESHOLD,
                "novelty_claim_policy": "abstain_not_evaluable_by_pairwise_nli",
                "semantic_verdict_rule": (
                    "supported if maximum entailment probability is at least 0.5 and exceeds maximum "
                    "contradiction probability; unsupported if maximum contradiction probability is at "
                    "least 0.5 and exceeds maximum entailment probability; otherwise abstain"
                ),
            },
            "primary_metrics": [
                "nli_non_entailment_rate_among_evaluable_claims",
                "nli_unsupported_rate_among_evaluable_claims",
            ],
            "secondary_metrics": [
                "claim_retention_rate",
                "nli_coverage_rate",
                "mean_entailment_probability",
                "word_count_change",
                "hedge_marker_change",
                "modal_marker_change",
                "numeric_token_change",
                "wall_clock_seconds",
                "onnx_inference_calls",
                "encoded_pair_count",
                "encoded_token_count",
            ],
            "analysis": {
                "pair_count": 9,
                "exact_sign_flip_test": True,
                "sign_flip_assignments": 512,
                "reference_arm": "no_gate",
                "post_hoc": True,
                "human_validation": "deferred_not_replaced",
            },
            "hard_gates": [
                "all four arms are derived from one hash-bound registry per task-seed pair",
                "all 36 branch registries and all linked evidence remain hash-bound",
                "the evaluator model family is independent of the Codex generator and gate",
                "novelty claims abstain rather than being judged by unsupported NLI machinery",
                "no result is described as human validation or prospective confirmatory evidence",
            ],
        }
        write_json_atomic(staging / "protocol.json", protocol)
        protected_hashes = {
            path.relative_to(staging).as_posix(): sha256_file(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file() and path.name != "protected_manifest.json"
        }
        write_json_atomic(
            staging / "protected_manifest.json",
            {"schema_version": 1, "protocol_id": protocol_id, "hashes": protected_hashes},
        )
        root.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(root)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    audit = audit_counterfactual_rebranch(project, persist=True)
    if not audit["passed"]:
        raise ValueError("new counterfactual rebranch failed audit: " + "; ".join(audit["violations"]))
    return read_json(root / "protocol.json")


def audit_counterfactual_rebranch(project: Path, *, persist: bool = False) -> dict[str, object]:
    project = project.resolve()
    root = _rebranch_root(project)
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, condition: bool, message: str) -> None:
        checks[name] = bool(condition)
        if not condition:
            violations.append(message)

    check("root_exists", root.is_dir(), "counterfactual rebranch root is missing")
    if not root.is_dir():
        result = {"passed": False, "checks": checks, "violations": violations}
        return result
    try:
        protocol = read_json(root / "protocol.json")
        protected = read_json(root / "protected_manifest.json")
        check(
            "protocol_identity",
            protocol.get("protocol_id") == protected.get("protocol_id"),
            "protocol and protected manifest IDs differ",
        )
        expected_hashes = dict(protected.get("hashes", {}))
        actual_hashes: dict[str, str] = {}
        for relative in expected_hashes:
            path = root / relative
            if path.is_file():
                actual_hashes[relative] = sha256_file(path)
        check(
            "protected_inputs_unchanged",
            actual_hashes == expected_hashes,
            "one or more frozen branch inputs changed",
        )
        check(
            "source_protocol_unchanged",
            (project / "stage2" / "protocol.json").is_file()
            and sha256_file(project / "stage2" / "protocol.json")
            == protocol.get("source_protocol_sha256"),
            "source Stage 2 protocol changed",
        )
        pair_checks: list[bool] = []
        source_checks: list[bool] = []
        for pair in protocol.get("pairs", []):
            pair_id = str(pair["pair_id"])
            branch_hashes = dict(pair.get("branch_registry_hashes", {}))
            pair_checks.append(
                set(branch_hashes) == set(ARMS)
                and all(
                    (root / "branches" / pair_id / arm / "registry.json").is_file()
                    and sha256_file(root / "branches" / pair_id / arm / "registry.json")
                    == branch_hashes[arm]
                    for arm in ARMS
                )
            )
            source_root = project / str(pair["source_cell_relative_path"])
            source_checks.append(
                all(
                    (source_root / name).is_file()
                    and sha256_file(source_root / name) == expected
                    for name, expected in dict(pair.get("source_hashes", {})).items()
                )
                and sha256_tree(source_root / "evidence") == pair.get("evidence_tree_sha256")
            )
        check(
            "nine_shared_pairs_bound",
            len(pair_checks) == 9 and all(pair_checks),
            "the frozen four-arm branch matrix is incomplete or changed",
        )
        check(
            "source_cells_and_evidence_unchanged",
            len(source_checks) == 9 and all(source_checks),
            "one or more source treatment cells or evidence packets changed",
        )
        evaluator = dict(protocol.get("evaluator", {}))
        implementation = dict(protocol.get("evaluator_implementation", {}))
        check(
            "evaluator_implementation_unchanged",
            implementation.get("version") == REBRANCH_IMPLEMENTATION_VERSION
            and implementation.get("sha256") == _implementation_sha256(),
            "the frozen evaluator implementation changed",
        )
        check(
            "nli_runtime_versions_bound",
            evaluator.get("onnxruntime_version") == NLI_ONNXRUNTIME_VERSION
            and evaluator.get("tokenizers_version") == NLI_TOKENIZERS_VERSION
            and importlib.metadata.version("onnxruntime") == NLI_ONNXRUNTIME_VERSION
            and importlib.metadata.version("tokenizers") == NLI_TOKENIZERS_VERSION,
            "the local NLI runtime dependency versions changed",
        )
        model_path = Path(str(evaluator.get("model_path", "")))
        tokenizer_path = Path(str(evaluator.get("tokenizer_path", "")))
        check(
            "nli_model_bound",
            model_path.is_file()
            and sha256_file(model_path) == evaluator.get("model_sha256") == NLI_MODEL_SHA256,
            "the local NLI model is missing or changed",
        )
        check(
            "nli_tokenizer_bound",
            tokenizer_path.is_file()
            and sha256_file(tokenizer_path)
            == evaluator.get("tokenizer_sha256")
            == NLI_TOKENIZER_SHA256,
            "the local NLI tokenizer is missing or changed",
        )
        summary_path = root / "results" / "summary.json"
        results_manifest_path = root / "results" / "manifest.json"
        if results_manifest_path.is_file():
            result_manifest = read_json(results_manifest_path)
            result_hashes = dict(result_manifest.get("hashes", {}))
            check(
                "result_files_unchanged",
                all(
                    (root / relative).is_file()
                    and sha256_file(root / relative) == digest
                    for relative, digest in result_hashes.items()
                ),
                "one or more result files changed after evaluation",
            )
            check("summary_exists", summary_path.is_file(), "evaluation summary is missing")
    except Exception as exc:
        violations.append(str(exc))
        checks["audit_exception"] = False
    result = {
        "schema_version": 1,
        "audited_at": utc_now(),
        "passed": not violations,
        "checks": checks,
        "violations": violations,
    }
    if persist:
        write_json_atomic(root / "audit.json", result)
    return result


def _source_records(stage2: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for path in sorted((stage2 / "evidence" / "sources").glob("*.json")):
        record = read_json(path)
        records[str(record["source_id"])] = record
    return records


def _experiment_evidence_text(record: dict[str, object]) -> str:
    fields = {
        "run_id": record.get("run_id"),
        "is_baseline": record.get("is_baseline"),
        "valid": record.get("valid"),
        "verdict": record.get("verdict"),
        "aggregate_metrics": record.get("aggregate_metrics"),
        "metric_stddev": record.get("metric_stddev"),
        "improvement": record.get("improvement"),
        "isolation_verified": record.get("isolation_verified"),
    }
    return "Experiment record: " + json.dumps(fields, ensure_ascii=False, sort_keys=True)


def _task_specification_evidence_text(record: dict[str, object]) -> str:
    fields = {
        "primary_metric": record.get("primary_metric"),
        "direction": record.get("direction"),
        "baseline_score": record.get("baseline_score"),
        "target_score": record.get("target_score"),
        "task_specification_sha256": record.get("task_specification_sha256"),
    }
    return "Frozen task specification: " + json.dumps(
        fields, ensure_ascii=False, sort_keys=True
    )


def _source_evidence_text(record: dict[str, object]) -> str:
    authors = record.get("authors") or []
    if isinstance(authors, list):
        authors_text = ", ".join(str(item) for item in authors[:8])
    else:
        authors_text = str(authors)
    return (
        f"Paper title: {record.get('title', '')}. Authors: {authors_text}. "
        f"Year: {record.get('year', '')}. Abstract or verified notes: {record.get('notes', '')}"
    )


def _evidence_chunks(
    claim: StudyClaim,
    packet: dict[str, object],
) -> list[str]:
    if claim.claim_type.value == "novelty":
        return []
    chunks: list[str] = []
    experiment = packet.get("linked_experiment_evidence")
    task_specification = packet.get("linked_task_specification")
    if isinstance(experiment, dict):
        chunks.append(_experiment_evidence_text(experiment))
    if isinstance(task_specification, dict):
        task_text = _task_specification_evidence_text(task_specification)
        chunks.append(task_text)
        if isinstance(experiment, dict):
            # Comparative claims require both premises in the same NLI window.
            chunks.append(
                task_text + "\n" + _experiment_evidence_text(experiment)
            )
    source_records = [
        item for item in packet.get("linked_source_records", []) if isinstance(item, dict)
    ]
    source_texts = [_source_evidence_text(item) for item in source_records]
    chunks.extend(source_texts)
    if len(source_texts) > 1:
        chunks.append("Linked source collection:\n" + "\n\n".join(source_texts))
    return list(dict.fromkeys(item for item in chunks if item.strip()))


class LocalNLIEngine:
    def __init__(self, model_path: Path, tokenizer_path: Path, *, batch_size: int = 8):
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "local NLI evaluation requires the optional dependencies: "
                "pip install 'research-forge[nli]'"
            ) from exc
        self.np = np
        self.session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=NLI_MAX_LENGTH, strategy="longest_first")
        self.tokenizer.enable_padding()
        self.batch_size = batch_size
        self.cache: dict[tuple[str, str], dict[str, float]] = {}
        self.inference_calls = 0
        self.encoded_pair_count = 0
        self.encoded_token_count = 0

    @property
    def providers(self) -> list[str]:
        return list(self.session.get_providers())

    def score(self, pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
        missing = [pair for pair in dict.fromkeys(pairs) if pair not in self.cache]
        for offset in range(0, len(missing), self.batch_size):
            batch_pairs = missing[offset : offset + self.batch_size]
            encodings = self.tokenizer.encode_batch(batch_pairs)
            input_ids = self.np.asarray([item.ids for item in encodings], dtype=self.np.int64)
            attention_mask = self.np.asarray(
                [item.attention_mask for item in encodings], dtype=self.np.int64
            )
            token_type_ids = self.np.asarray(
                [item.type_ids for item in encodings], dtype=self.np.int64
            )
            available = {item.name for item in self.session.get_inputs()}
            feed: dict[str, Any] = {}
            if "input_ids" in available:
                feed["input_ids"] = input_ids
            if "attention_mask" in available:
                feed["attention_mask"] = attention_mask
            if "token_type_ids" in available:
                feed["token_type_ids"] = token_type_ids
            logits = self.session.run(None, feed)[0]
            shifted = logits - logits.max(axis=1, keepdims=True)
            probabilities = self.np.exp(shifted)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            for pair, row in zip(batch_pairs, probabilities, strict=True):
                self.cache[pair] = {
                    label: float(row[index]) for index, label in enumerate(NLI_LABELS)
                }
            self.inference_calls += 1
            self.encoded_pair_count += len(encodings)
            self.encoded_token_count += int(attention_mask.sum())
        return [self.cache[pair] for pair in pairs]


def _claim_text_metrics(text: str) -> dict[str, int]:
    lowered = text.lower()
    return {
        "word_count": len(_WORD_PATTERN.findall(text)),
        "character_count": len(text),
        "hedge_marker_count": sum(len(re.findall(pattern, lowered)) for pattern in _HEDGE_PATTERNS),
        "modal_marker_count": len(_MODAL_PATTERN.findall(text)),
        "numeric_token_count": len(_NUMBER_PATTERN.findall(text)),
    }


def _semantic_change_proxy(before: StudyClaim, after: StudyClaim | None) -> dict[str, object]:
    before_metrics = _claim_text_metrics(before.claim_text)
    if after is None:
        return {
            "claim_id": before.claim_id,
            "change_type": "removed",
            "before": before_metrics,
            "after": None,
            "delta": {name: -value for name, value in before_metrics.items()},
        }
    after_metrics = _claim_text_metrics(after.claim_text)
    delta = {name: after_metrics[name] - before_metrics[name] for name in before_metrics}
    if before.model_dump(mode="json") == after.model_dump(mode="json"):
        change_type = "unchanged"
    elif delta["hedge_marker_count"] > 0 or delta["modal_marker_count"] > 0:
        change_type = "qualification_or_hedging_added"
    elif after_metrics["word_count"] < 0.8 * max(before_metrics["word_count"], 1):
        change_type = "lexical_scope_contraction_proxy"
    elif before.metric_values != after.metric_values or before.source_ids != after.source_ids:
        change_type = "evidence_binding_or_numeric_correction"
    else:
        change_type = "other_text_revision"
    return {
        "claim_id": before.claim_id,
        "change_type": change_type,
        "before": before_metrics,
        "after": after_metrics,
        "delta": delta,
    }


def _evaluate_registry(
    project: Path,
    stage2: Path,
    source_cell_dir: Path,
    registry: StudyClaimRegistry,
    engine: LocalNLIEngine,
) -> dict[str, object]:
    experiments = _cell_experiment_packet(project, source_cell_dir)
    structural = audit_registry_structure(
        project,
        stage2,
        registry,
        experiments,
        require_experiment_claim=False,
    )
    packets = {
        str(item["claim_id"]): item
        for item in _claim_evidence_packets(stage2, registry, experiments, structural)
    }
    claim_checks = dict(structural.get("claim_checks", {}))
    results: list[dict[str, object]] = []
    for claim in registry.claims:
        checks = {str(k): bool(v) for k, v in dict(claim_checks.get(claim.claim_id, {})).items()}
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            verdict = "unsupported"
            rationale = "Deterministic evidence checks failed: " + ", ".join(failed)
            probabilities = None
            chunks = []
        elif claim.claim_type.value == "novelty":
            verdict = "abstain"
            rationale = "Pairwise NLI is not a valid novelty or absence-of-prior-work evaluator."
            probabilities = None
            chunks = []
        else:
            chunks = _evidence_chunks(claim, packets[claim.claim_id])
            if not chunks:
                verdict = "abstain"
                rationale = "No NLI-compatible linked evidence chunk was available."
                probabilities = None
            else:
                scores = engine.score([(premise, claim.claim_text) for premise in chunks])
                entailment = max(item["entailment"] for item in scores)
                contradiction = max(item["contradiction"] for item in scores)
                neutral = max(item["neutral"] for item in scores)
                probabilities = {
                    "max_entailment": entailment,
                    "max_contradiction": contradiction,
                    "max_neutral": neutral,
                }
                if entailment >= NLI_ENTAILMENT_THRESHOLD and entailment > contradiction:
                    verdict = "supported"
                elif contradiction >= NLI_CONTRADICTION_THRESHOLD and contradiction > entailment:
                    verdict = "unsupported"
                else:
                    verdict = "abstain"
                rationale = (
                    f"Independent local NLI maxima across {len(chunks)} linked evidence chunk(s): "
                    f"entailment={entailment:.6f}, contradiction={contradiction:.6f}, neutral={neutral:.6f}."
                )
        results.append(
            {
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type.value,
                "verdict": verdict,
                "rationale": rationale,
                "probabilities": probabilities,
                "evidence_chunk_count": len(chunks),
                "structural_checks": checks,
                "text_metrics": _claim_text_metrics(claim.claim_text),
            }
        )
    evaluable = [item for item in results if item["claim_type"] != "novelty" and item["probabilities"]]
    supported = sum(item["verdict"] == "supported" for item in evaluable)
    unsupported = sum(item["verdict"] == "unsupported" for item in evaluable)
    abstained = sum(item["verdict"] == "abstain" for item in evaluable)
    entailment_values = [
        float(dict(item["probabilities"])["max_entailment"]) for item in evaluable
    ]
    return {
        "registry_id": registry.registry_id,
        "claim_count": len(results),
        "evaluable_claim_count": len(evaluable),
        "supported_count": supported,
        "unsupported_count": unsupported,
        "abstain_count": abstained,
        "novelty_abstain_count": sum(
            item["claim_type"] == "novelty" and item["verdict"] == "abstain"
            for item in results
        ),
        "nli_coverage_rate": len(evaluable) / len(results) if results else 0.0,
        "nli_unsupported_rate_among_evaluable_claims": (
            unsupported / len(evaluable) if evaluable else None
        ),
        "nli_non_entailment_rate_among_evaluable_claims": (
            (unsupported + abstained) / len(evaluable) if evaluable else None
        ),
        "mean_entailment_probability": (
            statistics.fmean(entailment_values) if entailment_values else None
        ),
        "structural_protocol_valid": structural.get("passed") is True,
        "structural_violations": list(structural.get("violations", [])),
        "claims": results,
    }


def _pooled_metrics(
    items: list[dict[str, object]], *, reference_claim_count: int
) -> dict[str, object]:
    claims = sum(int(item["claim_count"]) for item in items)
    evaluable = sum(int(item["evaluable_claim_count"]) for item in items)
    supported = sum(int(item["supported_count"]) for item in items)
    unsupported = sum(int(item["unsupported_count"]) for item in items)
    abstained = sum(int(item["abstain_count"]) for item in items)
    entailments = [
        float(dict(claim["probabilities"])["max_entailment"])
        for item in items
        for claim in item["claims"]
        if claim["probabilities"] is not None
    ]
    return {
        "registry_count": len(items),
        "claim_count": claims,
        "evaluable_claim_count": evaluable,
        "supported_count": supported,
        "unsupported_count": unsupported,
        "abstain_count": abstained,
        "claim_retention_rate": claims / reference_claim_count if reference_claim_count else 0.0,
        "nli_coverage_rate": evaluable / claims if claims else 0.0,
        "nli_unsupported_rate_among_evaluable_claims": (
            unsupported / evaluable if evaluable else None
        ),
        "nli_non_entailment_rate_among_evaluable_claims": (
            (unsupported + abstained) / evaluable if evaluable else None
        ),
        "mean_entailment_probability": statistics.fmean(entailments) if entailments else None,
    }


def exact_sign_flip_pvalue(differences: Iterable[float]) -> float | None:
    values = [float(value) for value in differences]
    if not values:
        return None
    observed = abs(statistics.fmean(values))
    magnitudes = [abs(value) for value in values]
    assignments = 2 ** len(values)
    extreme = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(values)):
        permuted = abs(statistics.fmean(sign * value for sign, value in zip(signs, magnitudes)))
        if permuted + 1e-15 >= observed:
            extreme += 1
    return extreme / assignments


def _paired_analysis(
    pair_results: list[dict[str, object]], metric: str
) -> dict[str, object]:
    output: dict[str, object] = {}
    for arm in ARMS[1:]:
        differences: list[float] = []
        rows: list[dict[str, object]] = []
        for pair in pair_results:
            arms = dict(pair["arms"])
            baseline_value = dict(arms["no_gate"]).get(metric)
            arm_value = dict(arms[arm]).get(metric)
            if baseline_value is None or arm_value is None:
                continue
            difference = float(arm_value) - float(baseline_value)
            differences.append(difference)
            rows.append(
                {
                    "pair_id": pair["pair_id"],
                    "task_id": pair["task_id"],
                    "seed": pair["seed"],
                    "reference": baseline_value,
                    "arm_value": arm_value,
                    "difference": difference,
                }
            )
        output[arm] = {
            "pair_count": len(differences),
            "mean_paired_difference": statistics.fmean(differences) if differences else None,
            "median_paired_difference": statistics.median(differences) if differences else None,
            "exact_two_sided_sign_flip_pvalue": exact_sign_flip_pvalue(differences),
            "differences": rows,
        }
    return output


def _revision_trace(root: Path, pair: dict[str, object]) -> dict[str, object]:
    pair_id = str(pair["pair_id"])
    initial = StudyClaimRegistry.model_validate(
        read_json(root / "branches" / pair_id / "no_gate" / "registry.json")
    )
    revised = StudyClaimRegistry.model_validate(
        read_json(
            root
            / "branches"
            / pair_id
            / "verify_revise_no_recheck"
            / "registry.json"
        )
    )
    final = StudyClaimRegistry.model_validate(
        read_json(root / "branches" / pair_id / "verify_revise_recheck" / "registry.json")
    )
    revised_by_id = {claim.claim_id: claim for claim in revised.claims}
    final_by_id = {claim.claim_id: claim for claim in final.claims}
    initial_to_revised = [
        _semantic_change_proxy(claim, revised_by_id.get(claim.claim_id))
        for claim in initial.claims
    ]
    revised_to_final = [
        _semantic_change_proxy(claim, final_by_id.get(claim.claim_id))
        for claim in revised.claims
    ]
    return {
        "pair_id": pair_id,
        "task_id": pair["task_id"],
        "seed": pair["seed"],
        "initial_to_revised": initial_to_revised,
        "revised_to_final": revised_to_final,
    }


def _aggregate_revision_traces(traces: list[dict[str, object]]) -> dict[str, object]:
    changes = [
        change
        for trace in traces
        for change in trace["initial_to_revised"]
    ]
    recheck_changes = [
        change
        for trace in traces
        for change in trace["revised_to_final"]
    ]
    fields = (
        "word_count",
        "character_count",
        "hedge_marker_count",
        "modal_marker_count",
        "numeric_token_count",
    )
    return {
        "initial_to_revised_change_types": dict(
            sorted(Counter(str(item["change_type"]) for item in changes).items())
        ),
        "revised_to_final_change_types": dict(
            sorted(Counter(str(item["change_type"]) for item in recheck_changes).items())
        ),
        "initial_to_revised_total_deltas": {
            field: sum(int(dict(item["delta"])[field]) for item in changes) for field in fields
        },
        "revised_to_final_total_deltas": {
            field: sum(int(dict(item["delta"])[field]) for item in recheck_changes)
            for field in fields
        },
        "trace_count": len(traces),
        "claim_trace_count": len(changes),
        "classification_boundary": (
            "These are deterministic lexical proxies, not human judgments of semantic "
            "informativeness or scientific usefulness."
        ),
    }


def _render_report(summary: dict[str, object]) -> str:
    lines = [
        "# Same-Artifact Counterfactual Rebranch: Independent NLI Results",
        "",
        "## Material Passport",
        "",
        "- Origin Skill: experiment-agent",
        "- Origin Mode: run + validate",
        f"- Origin Date: {summary['completed_at']}",
        "- Verification Status: ANALYZED",
        "- Version Label: counterfactual_rebranch_v1",
        "",
        "## Boundary",
        "",
        "This is a retrospective, frozen rebranch of nine existing pre-gate registries. It uses a local cross-family NLI proxy and does not replace the deferred human audit. Novelty claims are deliberately abstained.",
        "",
        "## Pooled arm metrics",
        "",
        "| Arm | Claims | Retention | Evaluable | Unsupported | Non-entailment | Mean entailment |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        item = dict(summary["arm_metrics"])[arm]
        lines.append(
            "| {arm} | {claims} | {ret:.4f} | {evals} | {unsup} | {non:.4f} | {ent:.4f} |".format(
                arm=arm,
                claims=item["claim_count"],
                ret=float(item["claim_retention_rate"]),
                evals=item["evaluable_claim_count"],
                unsup=item["unsupported_count"],
                non=float(item["nli_non_entailment_rate_among_evaluable_claims"] or 0.0),
                ent=float(item["mean_entailment_probability"] or 0.0),
            )
        )
    lines.extend(
        [
            "",
            "## Revision-trace lexical proxies",
            "",
            "```json",
            json.dumps(summary["revision_trace_summary"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Telemetry",
            "",
            "```json",
            json.dumps(summary["telemetry"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Interpretation rule",
            "",
            "Report the paired estimates and exact sign-flip checks descriptively. Do not use this post-hoc NLI analysis as human validation, a prospective confirmatory result, or a publication-ready causal effect.",
            "",
        ]
    )
    return "\n".join(lines)


def run_counterfactual_rebranch(project: Path) -> dict[str, object]:
    project = project.resolve()
    root = _rebranch_root(project)
    if not root.is_dir():
        freeze_counterfactual_rebranch(project)
    audit = audit_counterfactual_rebranch(project, persist=True)
    if not audit["passed"]:
        raise ValueError("counterfactual rebranch failed pre-run audit: " + "; ".join(audit["violations"]))
    protocol = read_json(root / "protocol.json")
    existing_summary = root / "results" / "summary.json"
    if existing_summary.is_file():
        final_audit = audit_counterfactual_rebranch(project, persist=True)
        if not final_audit["passed"]:
            raise ValueError("existing rebranch results failed audit")
        return read_json(existing_summary)
    evaluator = dict(protocol["evaluator"])
    engine = LocalNLIEngine(
        Path(str(evaluator["model_path"])),
        Path(str(evaluator["tokenizer_path"])),
    )
    stage2 = project / "stage2"
    results_root = root / "results"
    results_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    cpu_started = time.process_time()
    pair_results: list[dict[str, object]] = []
    evaluation_hashes: dict[str, str] = {}
    for pair in sorted(protocol["pairs"], key=lambda item: str(item["pair_id"])):
        pair_id = str(pair["pair_id"])
        source_cell_dir = project / str(pair["source_cell_relative_path"])
        arm_results: dict[str, dict[str, object]] = {}
        for arm in ARMS:
            registry_path = root / "branches" / pair_id / arm / "registry.json"
            registry = StudyClaimRegistry.model_validate(read_json(registry_path))
            blind_id = "blind-" + _stable_hash(
                {
                    "protocol_id": protocol["protocol_id"],
                    "registry_sha256": sha256_file(registry_path),
                    "pair_id": pair_id,
                    "arm": arm,
                }
            )[:16]
            evaluation = _evaluate_registry(
                project, stage2, source_cell_dir, registry, engine
            )
            evaluation.update(
                {
                    "schema_version": 1,
                    "protocol_id": protocol["protocol_id"],
                    "blind_id": blind_id,
                    "arm_blinded_during_inference": True,
                }
            )
            destination = results_root / "evaluations" / blind_id / "evaluation.json"
            write_json_atomic(destination, evaluation)
            evaluation_hashes[blind_id] = sha256_file(destination)
            arm_results[arm] = evaluation
        pair_results.append(
            {
                "pair_id": pair_id,
                "task_id": pair["task_id"],
                "seed": pair["seed"],
                "arms": arm_results,
            }
        )
    completed_at = utc_now()
    traces = [_revision_trace(root, pair) for pair in protocol["pairs"]]
    trace_path = results_root / "revision_traces.json"
    write_json_atomic(trace_path, {"schema_version": 1, "traces": traces})
    reference_claim_count = sum(
        int(dict(pair["arms"])["no_gate"]["claim_count"])
        for pair in pair_results
    )
    arm_metrics = {
        arm: _pooled_metrics(
            [dict(pair["arms"])[arm] for pair in pair_results],
            reference_claim_count=reference_claim_count,
        )
        for arm in ARMS
    }
    summary = {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "completed_at": completed_at,
        "analysis_status": "post_hoc_independent_nli_complete_human_audit_deferred",
        "primary_analysis_interpretable_as_human_truth": False,
        "claim_ceiling": protocol["claim_ceiling"],
        "arm_metrics": arm_metrics,
        "paired_non_entailment_analysis": _paired_analysis(
            pair_results, "nli_non_entailment_rate_among_evaluable_claims"
        ),
        "paired_unsupported_analysis": _paired_analysis(
            pair_results, "nli_unsupported_rate_among_evaluable_claims"
        ),
        "revision_trace_summary": _aggregate_revision_traces(traces),
        "pair_results": pair_results,
        "telemetry": {
            "wall_clock_seconds": time.perf_counter() - started,
            "process_cpu_seconds": time.process_time() - cpu_started,
            "onnx_inference_calls": engine.inference_calls,
            "encoded_pair_count": engine.encoded_pair_count,
            "encoded_token_count": engine.encoded_token_count,
            "unique_pair_cache_size": len(engine.cache),
            "model_bytes": Path(str(evaluator["model_path"])).stat().st_size,
            "providers": engine.providers,
            "external_content_upload": False,
        },
        "evaluation_hashes": evaluation_hashes,
    }
    write_json_atomic(existing_summary, summary)
    report_path = root / "report.md"
    report_path.write_text(_render_report(summary), encoding="utf-8", newline="\n")
    hashes = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(results_root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    hashes[report_path.relative_to(root).as_posix()] = sha256_file(report_path)
    write_json_atomic(
        results_root / "manifest.json",
        {"schema_version": 1, "protocol_id": protocol["protocol_id"], "hashes": hashes},
    )
    final_audit = audit_counterfactual_rebranch(project, persist=True)
    if not final_audit["passed"]:
        raise ValueError("counterfactual rebranch failed post-run audit: " + "; ".join(final_audit["violations"]))
    return summary
