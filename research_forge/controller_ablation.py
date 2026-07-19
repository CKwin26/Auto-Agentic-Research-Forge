from __future__ import annotations

import hashlib
import json
import statistics
import uuid
from pathlib import Path

from .agent_runtime import backend_status
from .benchmark import DEFAULT_BENCHMARK_ROOT, audit_project, benchmark_doctor, load_task
from .models import utc_now
from .research_loop import run_loop_benchmark
from .runtime import DEFAULT_CONTROLLED_CPU_IMAGE, RuntimeOptions
from .storage import append_jsonl, load_jsonl, read_json, sha256_tree, write_json_atomic


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CELL_STORAGE_ROOT = ROOT / ".rfab"

DEFAULT_TASKS = (
    {
        "task_id": "coreferenceresolutionsupergluewscaccuracy",
        "alias": "wsc",
        "iterations": 1,
    },
    {
        "task_id": "textualclassificationsickaccuracy",
        "alias": "sick-accuracy",
        "iterations": 3,
    },
    {
        "task_id": "textualsimilaritysickspearmancorrelation",
        "alias": "sick-spearman",
        "iterations": 3,
    },
)

DEFAULT_VARIANTS = (
    {
        "variant_id": "full",
        "label": "Full controller (state-safe pool of 3)",
        "candidate_pool_size": 3,
        "proposal_attempts_per_iteration": 6,
        "deduplicate_candidates": True,
        "failure_diagnosis": True,
    },
    {
        "variant_id": "no-candidate-pool",
        "label": "No candidate pool",
        "candidate_pool_size": 1,
        "proposal_attempts_per_iteration": 6,
        "deduplicate_candidates": True,
        "failure_diagnosis": True,
    },
    {
        "variant_id": "no-duplicate-detection",
        "label": "No duplicate detection",
        "candidate_pool_size": 3,
        "proposal_attempts_per_iteration": 6,
        "deduplicate_candidates": False,
        "failure_diagnosis": True,
    },
    {
        "variant_id": "no-failure-diagnosis",
        "label": "No failure diagnosis",
        "candidate_pool_size": 3,
        "proposal_attempts_per_iteration": 6,
        "deduplicate_candidates": True,
        "failure_diagnosis": False,
    },
)

CONTROLLER_SOURCES = (
    "research_forge/agent_runtime.py",
    "research_forge/benchmark.py",
    "research_forge/benchmark_models.py",
    "research_forge/contracts.py",
    "research_forge/research_loop.py",
    "research_forge/controller_ablation.py",
    "research_forge/runner.py",
    "research_forge/runtime.py",
    "research_forge/service.py",
    "research_forge/storage.py",
    "research_forge/prompts/experimenter.md",
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload_sha256(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _manifest_sha256(manifest: dict[str, object]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    return _payload_sha256(payload)


def _execution_cells(
    tasks: list[dict[str, object]],
    variants: list[dict[str, object]],
    seeds: list[int],
) -> list[dict[str, object]]:
    """Build a task-blocked cyclic order so the full controller is not always first."""

    cells: list[dict[str, object]] = []
    order = 0
    for seed in seeds:
        for task_index, task in enumerate(tasks):
            for offset in range(len(variants)):
                variant = variants[(task_index + offset) % len(variants)]
                order += 1
                cell_id = (
                    f"{order:02d}-{variant['variant_id']}-{task['alias']}-s{seed}"
                )
                cells.append(
                    {
                        "order": order,
                        "cell_id": cell_id,
                        "task_id": task["task_id"],
                        "task_alias": task["alias"],
                        "variant_id": variant["variant_id"],
                        "seed": seed,
                        "iterations": task["iterations"],
                    }
                )
    return cells


def _task_entries() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for configured in DEFAULT_TASKS:
        task_dir, spec = load_task(str(configured["task_id"]))
        iterations = int(configured["iterations"])
        if not spec.runnable:
            raise ValueError(f"ablation task is not runnable: {spec.task_id}")
        if spec.compute_tier != "cpu":
            raise ValueError(f"ablation task is not CPU controlled: {spec.task_id}")
        if iterations > spec.max_iterations:
            raise ValueError(
                f"ablation iterations exceed registered task budget: {spec.task_id}"
            )
        entries.append(
            {
                **configured,
                "task_hash": sha256_tree(task_dir),
                "task_source": str(task_dir),
                "metric": spec.primary_metric,
                "direction": spec.direction.value,
                "baseline_score": spec.baseline_score,
                "target_score": spec.target_score,
                "max_iterations": spec.max_iterations,
                "required_repeats": spec.required_repeats,
                "timeout_seconds": spec.timeout_seconds,
            }
        )
    return entries


def _controller_source_hashes() -> dict[str, str]:
    return {relative: _file_sha256(ROOT / relative) for relative in CONTROLLER_SOURCES}


def _validate_environment(
    *, docker_image: str,
) -> tuple[dict[str, object], dict[str, object]]:
    doctor = benchmark_doctor(None, docker_image=docker_image)
    required = (
        "docker_daemon_ready",
        "docker_image_ready",
        "docker_isolation_ready",
        "controlled_ml_environment",
        "ml_capabilities_verified",
        "ml_environment_ready",
    )
    missing = [name for name in required if doctor.get(name) is not True]
    if missing:
        raise RuntimeError(
            "controlled benchmark environment is not ready: " + ", ".join(missing)
        )
    backend = backend_status()
    if backend.get("backend") != "codex" or backend.get("codex_authenticated") is not True:
        raise RuntimeError("authenticated Codex backend is required for controller ablation")
    return doctor, backend


def freeze_controller_ablation(
    *,
    output_root: str | Path | None = None,
    docker_image: str = DEFAULT_CONTROLLED_CPU_IMAGE,
    seeds: list[int] | None = None,
) -> Path:
    selected_seeds = list(seeds or [0])
    if not selected_seeds or len(selected_seeds) != len(set(selected_seeds)):
        raise ValueError("ablation seeds must be non-empty and unique")
    doctor, backend = _validate_environment(docker_image=docker_image)
    runtime_options = RuntimeOptions(kind="docker", image=docker_image)
    tasks = _task_entries()
    variants = [dict(item) for item in DEFAULT_VARIANTS]
    stamp = utc_now().replace(":", "").replace("-", "").replace("+00:00", "Z")
    matrix_id = f"controller-ablation-{stamp}-{uuid.uuid4().hex[:6]}"
    cells = _execution_cells(tasks, variants, selected_seeds)
    payload: dict[str, object] = {
        "schema_version": 2,
        "matrix_id": matrix_id,
        "status": "frozen",
        "created_at": utc_now(),
        "purpose": (
            "Paired three-task comparison of the state-safe three-candidate deterministic "
            "run-loop controller against removal of candidate pooling, duplicate detection, "
            "or failure diagnosis."
        ),
        "strategy": "codex",
        "model": backend["model"],
        "seeds": selected_seeds,
        "tasks": tasks,
        "variants": variants,
        "execution_order": cells,
        "common_controller_config": {
            "patience": 5,
            "max_invalid_runs": 3,
            "auto_promote": True,
            "stop_at_target": True,
            "pool_policy": {
                "admission": "contract-valid current-state targets with deterministic fingerprints",
                "pending_invalidation": (
                    "revalidate invariant targets and discard targets whose fingerprint changes "
                    "after canonical state promotion"
                ),
                "full_pool_size": 3,
            },
        },
        "runtime": {
            "kind": "docker",
            "image": docker_image,
            "options": runtime_options.public_config(),
            "image_id": doctor["docker_image_id"],
            "capability_manifest_sha256": doctor["capability_manifest_sha256"],
            "runtime_capabilities": doctor["runtime_capabilities"],
            "network_access": "disabled",
        },
        "cell_storage": {
            "root": str(DEFAULT_CELL_STORAGE_ROOT.resolve()),
            "layout": "<manifest_sha256_prefix_12>/<execution_order_2_digits>",
            "reason": (
                "Short Windows workspace paths keep nested run, trial, and atomic-temp paths "
                "below MAX_PATH while the frozen matrix retains all result references."
            ),
        },
        "codex_backend": {
            "backend": backend["backend"],
            "model": backend["model"],
            "sdk_version": backend.get("codex_sdk_version"),
            "account_type": backend.get("codex_account_type"),
            "plan_type": backend.get("codex_plan_type"),
        },
        "controller_source_hashes": _controller_source_hashes(),
        "cell_count": len(cells),
        "maximum_candidate_runs": sum(
            int(cell["iterations"]) for cell in cells
        ),
        "interpretation_boundary": (
            "One registered seed across three task blocks is a descriptive controller ablation, "
            "not a powered statistical claim about model-independent superiority."
        ),
    }
    payload["manifest_sha256"] = _manifest_sha256(payload)
    root = Path(output_root or DEFAULT_BENCHMARK_ROOT).resolve()
    output = root / matrix_id
    output.mkdir(parents=True, exist_ok=False)
    write_json_atomic(output / "matrix.json", payload)
    state = {
        "schema_version": 1,
        "matrix_id": matrix_id,
        "manifest_sha256": payload["manifest_sha256"],
        "status": "frozen",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "cells": [
            {
                **cell,
                "status": "pending",
                "output": None,
                "error": None,
                "result": None,
            }
            for cell in cells
        ],
    }
    write_json_atomic(output / "ablation_state.json", state)
    append_jsonl(
        output / "events.jsonl",
        {
            "recorded_at": utc_now(),
            "event": "matrix_frozen",
            "manifest_sha256": payload["manifest_sha256"],
            "cell_count": len(cells),
        },
    )
    return output


def verify_frozen_ablation(matrix_dir: str | Path) -> dict[str, object]:
    root = Path(matrix_dir).resolve()
    manifest = read_json(root / "matrix.json")
    expected = manifest.get("manifest_sha256")
    actual = _manifest_sha256(manifest)
    if expected != actual:
        raise ValueError("controller ablation matrix hash does not match frozen manifest")
    if manifest.get("status") != "frozen":
        raise ValueError("controller ablation matrix is not frozen")

    task_entries = {str(item["task_id"]): item for item in manifest["tasks"]}
    for task_id, entry in task_entries.items():
        task_dir, _ = load_task(task_id)
        if sha256_tree(task_dir) != entry["task_hash"]:
            raise ValueError(f"registered task changed after matrix freeze: {task_id}")
    current_sources = _controller_source_hashes()
    if current_sources != manifest.get("controller_source_hashes"):
        raise ValueError("controller source changed after matrix freeze")

    runtime = manifest["runtime"]
    doctor, backend = _validate_environment(docker_image=str(runtime["image"]))
    if doctor.get("docker_image_id") != runtime.get("image_id"):
        raise ValueError("Docker image ID changed after matrix freeze")
    if (
        doctor.get("capability_manifest_sha256")
        != runtime.get("capability_manifest_sha256")
    ):
        raise ValueError("runtime capability manifest changed after matrix freeze")
    if backend.get("model") != manifest.get("model"):
        raise ValueError("Codex model changed after matrix freeze")
    return manifest


def _find_cell_output(cell_root: Path) -> Path | None:
    manifests = sorted(cell_root.glob("*/loop_manifest.json"))
    if len(manifests) > 1:
        raise ValueError(f"multiple run-loop outputs found for one ablation cell: {cell_root}")
    return manifests[0].parent if manifests else None


def _validate_cell_completion(
    *,
    report: dict[str, object],
    summary: dict[str, object],
    proposal_ids: set[str],
    events: list[dict[str, object]],
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    seeds = report.get("seeds")
    loops = summary.get("loops")
    if not isinstance(seeds, list) or len(seeds) != 1:
        raise ValueError("ablation cell report must contain exactly one seed")
    if not isinstance(loops, list) or len(loops) != 1:
        raise ValueError("ablation cell summary must contain exactly one loop")
    seed_result = seeds[0]
    loop = loops[0]
    if not isinstance(seed_result, dict) or not isinstance(loop, dict):
        raise ValueError("ablation cell seed and loop records must be objects")
    if loop.get("status") not in {"completed", "stopped"} or not loop.get(
        "stop_reason"
    ):
        raise ValueError("ablation cell loop did not reach a terminal state")
    if loop.get("errors") or seed_result.get("errors"):
        raise ValueError("ablation cell completed with recorded errors")
    if report.get("publishable") is not True:
        raise ValueError("ablation cell report is not publishable")

    proposal_attempts = int(loop.get("proposal_attempts", -1))
    if proposal_attempts != len(proposal_ids) or int(
        seed_result.get("proposal_attempts", -1)
    ) != len(proposal_ids):
        raise ValueError("ablation cell proposal journal differs from proposal files")

    config = summary.get("config")
    if not isinstance(config, dict):
        raise ValueError("ablation cell summary lacks controller config")
    target_pool_size = int(config.get("candidate_pool_size", 0))
    if target_pool_size < 1:
        raise ValueError("ablation cell candidate pool target is invalid")

    pool_ready_events: list[dict[str, object]] = []
    generated_from: dict[str, str] = {}
    invalidated: set[str] = set()
    current_pool_state: str | None = None
    for event in events:
        name = event.get("event")
        details = event.get("details")
        if not isinstance(details, dict):
            details = {}
        proposal_id = details.get("proposal_id")
        if name == "proposal_admitted_to_pool" and proposal_id is not None:
            generated_from[str(proposal_id)] = str(
                details.get("generated_from_fingerprint")
            )
        elif (
            name == "pending_candidate_revalidated_after_state_change"
            and proposal_id is not None
        ):
            generated_from[str(proposal_id)] = str(
                details.get("current_state_fingerprint")
            )
        elif (
            name == "pending_candidate_invalidated_after_state_change"
            and proposal_id is not None
        ):
            invalidated.add(str(proposal_id))
        elif name == "candidate_pool_ready":
            pool_ready_events.append(event)
            current_pool_state = str(details.get("canonical_state_fingerprint"))
            if (
                details.get("full") is not True
                or int(details.get("pool_size", -1)) != target_pool_size
                or int(details.get("target_pool_size", -1)) != target_pool_size
            ):
                raise ValueError("ablation cell executed with a partial candidate pool")
        elif name == "candidate_selected" and proposal_id is not None:
            selected_id = str(proposal_id)
            if selected_id not in proposal_ids:
                raise ValueError("selected candidate lacks a proposal file")
            if selected_id in invalidated:
                raise ValueError("an invalidated stale candidate was selected")
            if generated_from.get(selected_id) != current_pool_state:
                raise ValueError("selected candidate was generated from a stale state")

    completed_iterations = int(loop.get("completed_iterations", -1))
    if len(pool_ready_events) != completed_iterations:
        raise ValueError("candidate-pool journal differs from completed iterations")
    return seed_result, loop, pool_ready_events


def _cell_result(
    *,
    output: Path,
    variant_id: str,
    task_id: str,
    seed: int,
) -> dict[str, object]:
    report = read_json(output / "report.json")
    summary = read_json(output / "loop_summary.json")
    raw_seeds = report.get("seeds")
    if not isinstance(raw_seeds, list) or len(raw_seeds) != 1:
        raise ValueError("ablation cell report must contain exactly one seed")
    raw_seed = raw_seeds[0]
    if not isinstance(raw_seed, dict):
        raise ValueError("ablation cell seed record must be an object")
    project = Path(str(raw_seed["project"]))
    audit = audit_project(project)
    if not audit.passed or not audit.isolation_verified:
        raise ValueError(f"ablation cell failed integrity audit: {variant_id}/{task_id}")
    events = load_jsonl(project / "benchmark" / "loop_events.jsonl")
    proposal_ids = {path.stem for path in (project / "proposals").glob("*.json")}
    seed_result, loop, pool_ready_events = _validate_cell_completion(
        report=report,
        summary=summary,
        proposal_ids=proposal_ids,
        events=events,
    )
    return {
        "variant_id": variant_id,
        "task_id": task_id,
        "seed": seed,
        "output": str(output),
        "project": str(project),
        "baseline_score": seed_result["baseline_score"],
        "best_score": seed_result["best_score"],
        "normalized_gain": seed_result["normalized_gain"],
        "target_reached": seed_result["target_reached"],
        "proposal_attempts": loop["proposal_attempts"],
        "candidate_runs": seed_result["candidate_runs"],
        "valid_candidate_runs": seed_result["valid_candidate_runs"],
        "valid_submission_rate": seed_result["valid_submission_rate"],
        "anytime_auc": seed_result["anytime_auc"],
        "completed_iterations": loop["completed_iterations"],
        "invalid_runs": loop["invalid_runs"],
        "stop_reason": loop["stop_reason"],
        "wall_time_seconds": seed_result["wall_time_seconds"],
        "publishable": report["publishable"],
        "integrity_passed": audit.passed,
        "isolation_verified": audit.isolation_verified,
        "duplicate_rejections": sum(
            event["event"] in {
                "proposal_rejected_as_duplicate",
                "pending_candidate_rejected_as_noop",
            }
            for event in events
        ),
        "diagnosis_updates": sum(
            event["event"] == "diagnosis_updated" for event in events
        ),
        "diagnosis_disabled_events": sum(
            event["event"] == "diagnosis_disabled" for event in events
        ),
        "pool_ready_events": len(pool_ready_events),
        "full_pool_ready_events": sum(
            bool(event["details"].get("full")) for event in pool_ready_events
        ),
        "mean_pool_fill_rate": statistics.fmean(
            float(event["details"]["pool_size"])
            / float(event["details"]["target_pool_size"])
            for event in pool_ready_events
        )
        if pool_ready_events
        else None,
        "stale_candidate_invalidations": sum(
            event["event"] == "pending_candidate_invalidated_after_state_change"
            for event in events
        ),
    }


def _aggregate_results(
    completed: list[dict[str, object]],
    variants: list[dict[str, object]],
) -> list[dict[str, object]]:
    full_by_task = {
        str(item["task_id"]): item
        for item in completed
        if item["variant_id"] == "full"
    }
    aggregates: list[dict[str, object]] = []
    for variant in variants:
        variant_id = str(variant["variant_id"])
        cells = [item for item in completed if item["variant_id"] == variant_id]
        paired_deltas = [
            float(item["normalized_gain"])
            - float(full_by_task[str(item["task_id"])]["normalized_gain"])
            for item in cells
            if str(item["task_id"]) in full_by_task
        ]
        pool_ready_events = sum(int(item.get("pool_ready_events", 0)) for item in cells)
        full_pool_ready_events = sum(
            int(item.get("full_pool_ready_events", 0)) for item in cells
        )
        aggregates.append(
            {
                "variant_id": variant_id,
                "label": variant["label"],
                "completed_cells": len(cells),
                "mean_normalized_gain": statistics.fmean(
                    float(item["normalized_gain"]) for item in cells
                )
                if cells
                else None,
                "mean_paired_delta_vs_full": statistics.fmean(paired_deltas)
                if paired_deltas
                else None,
                "mean_anytime_auc": statistics.fmean(
                    float(item["anytime_auc"]) for item in cells
                )
                if cells
                else None,
                "mean_valid_submission_rate": statistics.fmean(
                    float(item["valid_submission_rate"]) for item in cells
                )
                if cells
                else None,
                "target_successes": sum(bool(item["target_reached"]) for item in cells),
                "proposal_attempts": sum(int(item["proposal_attempts"]) for item in cells),
                "candidate_runs": sum(int(item["candidate_runs"]) for item in cells),
                "invalid_runs": sum(int(item["invalid_runs"]) for item in cells),
                "duplicate_rejections": sum(
                    int(item["duplicate_rejections"]) for item in cells
                ),
                "pool_ready_events": pool_ready_events,
                "full_pool_ready_events": full_pool_ready_events,
                "full_pool_rate": (
                    full_pool_ready_events / pool_ready_events
                    if pool_ready_events
                    else None
                ),
                "mean_pool_fill_rate": statistics.fmean(
                    float(item["mean_pool_fill_rate"])
                    for item in cells
                    if item.get("mean_pool_fill_rate") is not None
                )
                if any(item.get("mean_pool_fill_rate") is not None for item in cells)
                else None,
                "stale_candidate_invalidations": sum(
                    int(item.get("stale_candidate_invalidations", 0)) for item in cells
                ),
                "total_wall_time_seconds": sum(
                    float(item["wall_time_seconds"]) for item in cells
                ),
                "all_integrity_passed": bool(cells)
                and all(bool(item["integrity_passed"]) for item in cells),
            }
        )
    return aggregates


def _render_ablation_report(results: dict[str, object], output: Path) -> None:
    def number(value: object, digits: int = 4) -> str:
        return "n/a" if value is None else f"{float(value):.{digits}f}"

    lines = [
        "# Three-task controller ablation",
        "",
        f"- Matrix: `{results['matrix_id']}`",
        f"- Frozen manifest: `{results['manifest_sha256']}`",
        f"- Status: `{results['status']}`",
        f"- Model: `{results['model']}`",
        f"- Docker image: `{results['docker_image']}`",
        f"- Completed cells: `{results['completed_cells']}/{results['cell_count']}`",
        "",
        "## Variant summary",
        "",
        "| Variant | Mean normalized gain | Paired delta vs full | Anytime AUC | Valid rate | Full-pool rate | Stale dropped | Proposals | Runs | Invalid | Integrity |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for item in results["aggregates"]:
        lines.append(
            "| {label} | {gain} | {delta} | {auc} | {valid} | {pool} | {stale} | {proposals} | {runs} | {invalid} | {integrity} |".format(
                label=item["label"],
                gain=number(item["mean_normalized_gain"]),
                delta=number(item["mean_paired_delta_vs_full"]),
                auc=number(item["mean_anytime_auc"]),
                valid=number(item["mean_valid_submission_rate"]),
                pool=number(item["full_pool_rate"]),
                stale=item["stale_candidate_invalidations"],
                proposals=item["proposal_attempts"],
                runs=item["candidate_runs"],
                invalid=item["invalid_runs"],
                integrity="pass" if item["all_integrity_passed"] else "incomplete",
            )
        )
    lines.extend(
        [
            "",
            "## Cell results",
            "",
            "| Order | Task | Variant | Baseline | Best | Normalized gain | Valid rate | Proposals | Runs | Stop |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    completed_by_id = {item["cell_id"]: item for item in results["cells"]}
    for cell in results["execution_order"]:
        item = completed_by_id.get(cell["cell_id"])
        if item is None:
            lines.append(
                f"| {cell['order']} | {cell['task_alias']} | {cell['variant_id']} | n/a | n/a | n/a | n/a | n/a | n/a | pending |"
            )
            continue
        lines.append(
            "| {order} | {task} | {variant} | {baseline} | {best} | {gain} | {valid} | {proposals} | {runs} | {stop} |".format(
                order=cell["order"],
                task=cell["task_alias"],
                variant=cell["variant_id"],
                baseline=number(item["baseline_score"]),
                best=number(item["best_score"]),
                gain=number(item["normalized_gain"]),
                valid=number(item["valid_submission_rate"]),
                proposals=item["proposal_attempts"],
                runs=item["candidate_runs"],
                stop=item["stop_reason"],
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            str(results["interpretation_boundary"]),
            "All comparisons are paired by task, seed, task budget, model, and controlled Docker environment. Every condition keeps the same maximum invalid-proposal retry allowance; the no-candidate-pool condition stops proposing as soon as it has one valid candidate.",
            "AIRS-lite preserves split and metric semantics but is not an official AIRS leaderboard submission.",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_controller_ablation(matrix_dir: str | Path) -> dict[str, object]:
    root = Path(matrix_dir).resolve()
    manifest = read_json(root / "matrix.json")
    state = read_json(root / "ablation_state.json")
    cells = [
        {"cell_id": item["cell_id"], **item["result"]}
        for item in state["cells"]
        if item.get("status") == "completed" and item.get("result")
    ]
    failed = [item["cell_id"] for item in state["cells"] if item["status"] == "failed"]
    status = "completed" if len(cells) == manifest["cell_count"] and not failed else "incomplete"
    results = {
        "schema_version": 1,
        "matrix_id": manifest["matrix_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "status": status,
        "created_at": utc_now(),
        "model": manifest["model"],
        "docker_image": manifest["runtime"]["image"],
        "docker_image_id": manifest["runtime"]["image_id"],
        "cell_count": manifest["cell_count"],
        "completed_cells": len(cells),
        "failed_cells": failed,
        "execution_order": manifest["execution_order"],
        "cells": cells,
        "aggregates": _aggregate_results(cells, manifest["variants"]),
        "interpretation_boundary": manifest["interpretation_boundary"],
    }
    write_json_atomic(root / "results.json", results)
    _render_ablation_report(results, root)
    return results


async def run_controller_ablation(matrix_dir: str | Path) -> dict[str, object]:
    root = Path(matrix_dir).resolve()
    manifest = verify_frozen_ablation(root)
    state_path = root / "ablation_state.json"
    state = read_json(state_path)
    if state.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("ablation state does not belong to the frozen matrix")
    variants = {str(item["variant_id"]): item for item in manifest["variants"]}
    tasks = {str(item["task_id"]): item for item in manifest["tasks"]}
    frozen_runtime = manifest["runtime"]["options"]
    runtime = RuntimeOptions(
        kind="docker",
        image=str(manifest["runtime"]["image"]),
        cpus=float(frozen_runtime["cpus"]),
        memory_mb=int(frozen_runtime["memory_mb"]),
        pids_limit=int(frozen_runtime["pids_limit"]),
        tmpfs_mb=int(frozen_runtime["tmpfs_mb"]),
        max_output_mb=int(frozen_runtime["max_output_mb"]),
    )

    state["status"] = "running"
    state["updated_at"] = utc_now()
    write_json_atomic(state_path, state)
    for cell in state["cells"]:
        if cell["status"] == "completed" and cell.get("result"):
            continue
        variant = variants[str(cell["variant_id"])]
        task = tasks[str(cell["task_id"])]
        cell_root = (
            Path(manifest["cell_storage"]["root"])
            / str(manifest["manifest_sha256"])[:12]
            / f"{int(cell['order']):02d}"
        )
        cell_root.mkdir(parents=True, exist_ok=True)
        existing = _find_cell_output(cell_root)
        cell["status"] = "running"
        cell["error"] = None
        cell["output"] = str(existing) if existing else None
        state["updated_at"] = utc_now()
        write_json_atomic(state_path, state)
        append_jsonl(
            root / "events.jsonl",
            {
                "recorded_at": utc_now(),
                "event": "cell_started" if existing is None else "cell_resumed",
                "cell_id": cell["cell_id"],
                "task_id": cell["task_id"],
                "variant_id": cell["variant_id"],
            },
        )
        try:
            output, _, _ = await run_loop_benchmark(
                str(task["task_id"]),
                strategy="codex",
                seeds=[int(cell["seed"])],
                iterations=int(task["iterations"]),
                output_root=cell_root,
                runtime=runtime,
                candidate_pool_size=int(variant["candidate_pool_size"]),
                proposal_attempts_per_iteration=int(
                    variant["proposal_attempts_per_iteration"]
                ),
                patience=int(manifest["common_controller_config"]["patience"]),
                max_invalid_runs=int(
                    manifest["common_controller_config"]["max_invalid_runs"]
                ),
                deduplicate_candidates=bool(variant["deduplicate_candidates"]),
                failure_diagnosis=bool(variant["failure_diagnosis"]),
                resume=existing,
            )
            result = _cell_result(
                output=output,
                variant_id=str(cell["variant_id"]),
                task_id=str(cell["task_id"]),
                seed=int(cell["seed"]),
            )
            cell["status"] = "completed"
            cell["output"] = str(output)
            cell["result"] = result
            append_jsonl(
                root / "events.jsonl",
                {
                    "recorded_at": utc_now(),
                    "event": "cell_completed",
                    "cell_id": cell["cell_id"],
                    "output": str(output),
                    "normalized_gain": result["normalized_gain"],
                },
            )
        except Exception as exc:
            cell["status"] = "failed"
            cell["error"] = f"{type(exc).__name__}: {exc}"
            append_jsonl(
                root / "events.jsonl",
                {
                    "recorded_at": utc_now(),
                    "event": "cell_failed",
                    "cell_id": cell["cell_id"],
                    "error": cell["error"],
                },
            )
        state["updated_at"] = utc_now()
        write_json_atomic(state_path, state)
        summarize_controller_ablation(root)

    results = summarize_controller_ablation(root)
    state["status"] = results["status"]
    state["updated_at"] = utc_now()
    write_json_atomic(state_path, state)
    append_jsonl(
        root / "events.jsonl",
        {
            "recorded_at": utc_now(),
            "event": "matrix_finished",
            "status": results["status"],
            "completed_cells": results["completed_cells"],
            "failed_cells": results["failed_cells"],
        },
    )
    return results
