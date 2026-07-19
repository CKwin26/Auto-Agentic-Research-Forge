from __future__ import annotations

import math
from pathlib import Path

from .models import (
    Direction,
    EventRecord,
    ExecutionContract,
    ExperimentProposal,
    FrozenManifest,
    LiteratureManifest,
    Stage1Manifest,
    ProtectedManifest,
    ProjectState,
    Stage,
)
from .storage import append_jsonl, read_json, safe_relative, sha256_file, write_json_atomic


TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.SCOPING: {Stage.PLAN_REVIEW, Stage.PAUSED},
    Stage.PLAN_REVIEW: {Stage.PLAN_REVIEW, Stage.CONTRACT_FROZEN, Stage.PAUSED},
    Stage.CONTRACT_FROZEN: {Stage.BASELINE_PENDING, Stage.PAUSED},
    Stage.BASELINE_PENDING: {Stage.BASELINE_PENDING, Stage.BASELINE_VERIFIED, Stage.PAUSED},
    Stage.BASELINE_VERIFIED: {Stage.EXPERIMENT_DESIGN, Stage.SYNTHESIS, Stage.PAUSED},
    Stage.EXPERIMENT_DESIGN: {Stage.EXPERIMENT_DESIGN, Stage.EXPERIMENT_RUNNING, Stage.SYNTHESIS, Stage.PAUSED},
    Stage.EXPERIMENT_RUNNING: {Stage.RESULT_REVIEW, Stage.PAUSED},
    Stage.RESULT_REVIEW: {Stage.EXPERIMENT_DESIGN, Stage.EXPERIMENT_RUNNING, Stage.SYNTHESIS, Stage.PAUSED},
    Stage.SYNTHESIS: {Stage.EXPERIMENT_DESIGN, Stage.COMPLETED, Stage.PAUSED},
    Stage.PAUSED: {
        Stage.SCOPING,
        Stage.PLAN_REVIEW,
        Stage.CONTRACT_FROZEN,
        Stage.BASELINE_PENDING,
        Stage.BASELINE_VERIFIED,
        Stage.EXPERIMENT_DESIGN,
        Stage.RESULT_REVIEW,
        Stage.SYNTHESIS,
    },
    Stage.COMPLETED: set(),
}


def transition(project: Path, state: ProjectState, target: Stage, event: str, **details: str | int | float | bool | None) -> None:
    previous = state.stage
    if target not in TRANSITIONS[previous]:
        raise ValueError(f"illegal stage transition: {previous.value} -> {target.value}")
    state.stage = target
    append_jsonl(
        project / "events.jsonl",
        EventRecord(event=event, from_stage=previous, to_stage=target, details=details),
    )


def validate_proposal(project: Path, proposal: ExperimentProposal, contract: ExecutionContract) -> list[str]:
    errors: list[str] = []
    total_chars = 0
    experiment_dir = project / "experiment"
    seen: set[str] = set()
    forbidden_names = {
        ".env",
        ".env.local",
        "research_contract.json",
        "execution_contract.json",
        "frozen_manifest.json",
        "state.json",
        "evidence.jsonl",
    }
    forbidden_secret_markers = ("OPENAI_API_KEY", "sk-proj-", "BEGIN PRIVATE KEY")

    for parameter in proposal.parameters:
        rendered = f"{parameter.name}={parameter.value}"
        if any(marker.lower() in rendered.lower() for marker in forbidden_secret_markers):
            errors.append(f"possible secret material in parameter: {parameter.name}")
        if (
            isinstance(parameter.value, float)
            and not math.isfinite(parameter.value)
        ):
            errors.append(f"parameter must be finite: {parameter.name}")

    for replacement in proposal.file_replacements:
        normalized = replacement.path.replace("\\", "/")
        total_chars += len(replacement.content)
        if any(marker in replacement.content for marker in forbidden_secret_markers):
            errors.append(f"possible secret material in replacement: {normalized}")
        if normalized in seen:
            errors.append(f"duplicate replacement path: {normalized}")
            continue
        seen.add(normalized)
        try:
            destination = safe_relative(experiment_dir, normalized)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if destination.name in forbidden_names:
            errors.append(f"protected file cannot be replaced: {normalized}")
        if destination.suffix.lower() not in set(contract.allowed_suffixes):
            errors.append(f"suffix is not allowed: {normalized}")

    if total_chars > 100_000:
        errors.append("proposal replacement content exceeds 100000 characters")
    return errors


def freeze_contracts(project: Path) -> FrozenManifest:
    targets = ["research_contract.json", "execution_contract.json", "literature_manifest.json"]
    literature_manifest = LiteratureManifest.model_validate(
        read_json(project / "literature_manifest.json")
    )
    for source_id, expected in sorted(literature_manifest.source_hashes.items()):
        relative = f"literature/sources/{source_id}.json"
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError(f"registered literature source is missing: {source_id}")
        if sha256_file(path) != expected:
            raise ValueError(f"registered literature source changed before freezing: {source_id}")
        targets.append(relative)
    stage1_path = project / "literature" / "stage1_manifest.json"
    if stage1_path.is_file():
        stage1_manifest = Stage1Manifest.model_validate(read_json(stage1_path))
        targets.append("literature/stage1_manifest.json")
        for relative, expected in sorted(stage1_manifest.hashes.items()):
            artifact = safe_relative(project, relative.replace("\\", "/"))
            if not artifact.is_file():
                raise FileNotFoundError(f"Stage 1 artifact is missing: {relative}")
            if sha256_file(artifact) != expected:
                raise ValueError(f"Stage 1 artifact changed before freezing: {relative}")
            targets.append(relative)
    if (project / "plan_evidence_binding.json").is_file():
        targets.append("plan_evidence_binding.json")
    if (project / "protected_manifest.json").is_file():
        targets.append("protected_manifest.json")
    hashes: dict[str, str] = {}
    for name in targets:
        path = project / name
        if not path.is_file():
            raise FileNotFoundError(f"required contract is missing: {name}")
        hashes[name] = sha256_file(path)
    manifest = FrozenManifest(hashes=hashes)
    write_json_atomic(project / "frozen_manifest.json", manifest)
    return manifest


def protect_artifacts(project: Path, relative_paths: list[str]) -> ProtectedManifest:
    hashes: dict[str, str] = {}
    for relative in sorted(set(relative_paths)):
        path = safe_relative(project, relative.replace("\\", "/"))
        if not path.is_file():
            raise FileNotFoundError(f"protected artifact is missing: {relative}")
        normalized = path.relative_to(project).as_posix()
        hashes[normalized] = sha256_file(path)
    manifest = ProtectedManifest(hashes=hashes)
    write_json_atomic(project / "protected_manifest.json", manifest)
    return manifest


def verify_protected_artifacts(project: Path) -> dict[str, str]:
    path = project / "protected_manifest.json"
    if not path.is_file():
        return {}
    manifest = ProtectedManifest.model_validate(read_json(path))
    for relative, expected in manifest.hashes.items():
        artifact = safe_relative(project, relative)
        if not artifact.is_file():
            raise ValueError(f"protected artifact disappeared: {relative}")
        actual = sha256_file(artifact)
        if actual != expected:
            raise ValueError(f"protected artifact changed: {relative}")
    return manifest.hashes


def verify_frozen_contracts(project: Path) -> str:
    manifest = FrozenManifest.model_validate(read_json(project / "frozen_manifest.json"))
    for name, expected in manifest.hashes.items():
        path = project / name
        if not path.is_file():
            raise ValueError(f"frozen contract disappeared: {name}")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"frozen contract changed: {name}")
    protected_hashes = verify_protected_artifacts(project)
    joined_parts = [f"{name}:{manifest.hashes[name]}" for name in sorted(manifest.hashes)]
    joined_parts.extend(
        f"protected:{name}:{protected_hashes[name]}" for name in sorted(protected_hashes)
    )
    joined = "|".join(joined_parts)
    import hashlib

    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def metric_improvement(candidate: float, reference: float, direction: Direction) -> float:
    if not (math.isfinite(candidate) and math.isfinite(reference)):
        raise ValueError("metric values must be finite")
    return candidate - reference if direction == Direction.MAXIMIZE else reference - candidate


def validate_metrics(metrics: dict[str, object], contract: ExecutionContract) -> tuple[dict[str, float], list[str]]:
    normalized: dict[str, float] = {}
    errors: list[str] = []
    for name in contract.required_metrics:
        if name not in metrics:
            errors.append(f"required metric is missing: {name}")
            continue
        value = metrics[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"metric must be numeric: {name}")
            continue
        number = float(value)
        if not math.isfinite(number):
            errors.append(f"metric must be finite: {name}")
            continue
        normalized[name] = number
    for name, value in metrics.items():
        if name in normalized or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            normalized[name] = number
    return normalized, errors
