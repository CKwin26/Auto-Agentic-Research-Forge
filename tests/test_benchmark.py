from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from research_forge.benchmark import (
    BUILTIN_TASKS,
    activate_airs_lite,
    audit_project,
    import_airs_task,
    list_tasks,
    load_task,
    materialize_task,
    run_benchmark,
)
from research_forge.runner import execute_run
from research_forge.storage import load_jsonl


def test_builtin_task_catalog_contains_three_runnable_tasks() -> None:
    tasks = list_tasks()
    builtins = [item for item in tasks if item["registry"] == "builtin"]
    assert {item["task_id"] for item in builtins} == {
        "rf-coupled-max",
        "rf-quadratic-max",
        "rf-quadratic-min",
    }
    assert all(item["runnable"] for item in builtins)


def test_task_catalog_rejects_duplicate_ids_in_extra_root(tmp_path: Path) -> None:
    duplicate = tmp_path / "rf-quadratic-max"
    duplicate.mkdir()
    source = BUILTIN_TASKS / "rf-quadratic-max" / "task.json"
    (duplicate / "task.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate registered task_id"):
        list_tasks(tmp_path)


def test_grid_benchmark_scores_evaluator_output_and_audits_evidence(tmp_path: Path) -> None:
    output, report = asyncio.run(
        run_benchmark(
            "rf-quadratic-max",
            strategy="grid",
            seeds=[0],
            iterations=3,
            output_root=tmp_path,
        )
    )
    seed = report.seeds[0]
    assert seed.baseline_score == pytest.approx(0.0)
    assert seed.best_score == pytest.approx(1.0)
    assert seed.normalized_gain == pytest.approx(1.0)
    assert seed.valid_submission_rate == pytest.approx(1.0)
    assert seed.reproducible
    assert seed.integrity.passed
    assert report.success_at_n == pytest.approx(1.0)
    assert not report.publishable  # The test environment has no enforced container boundary.
    assert (output / "report.json").is_file()
    assert (output / "report.md").is_file()

    project = Path(seed.project)
    baseline = next(item for item in load_jsonl(project / "evidence.jsonl") if item["is_baseline"])
    assert baseline["primary_value"] == pytest.approx(0.0)
    baseline_run = project / "runs" / baseline["run_id"]
    assert "9999" in (baseline_run / "trial-001" / "stdout.log").read_text(encoding="utf-8")
    assert json.loads((baseline_run / "trial-001" / "metrics.json").read_text(encoding="utf-8"))[
        "score"
    ] == pytest.approx(0.0)

    evidence_path = project / "evidence.jsonl"
    evidence = load_jsonl(evidence_path)
    evidence[0]["primary_value"] = 1234.0
    evidence_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in evidence),
        encoding="utf-8",
    )
    audit = audit_project(project)
    assert not audit.passed
    assert any("does not match" in item for item in audit.violations)


def test_protected_evaluator_change_blocks_execution(tmp_path: Path) -> None:
    task_dir, spec = load_task("rf-quadratic-min")
    project = materialize_task(task_dir, spec, seed=0, project_root=tmp_path)
    evaluator = project / "evaluator" / "evaluate.py"
    evaluator.write_text(evaluator.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="protected artifact changed"):
        execute_run(project)


def test_import_airs_task_creates_non_runnable_portable_pack(tmp_path: Path) -> None:
    source = tmp_path / "airs-source"
    source.mkdir()
    (source / "project_description.md").write_text(
        "Optimize a held-out classification task using the supplied data and evaluator.",
        encoding="utf-8",
    )
    (source / "metadata.yaml").write_text(
        """metric_lower_is_better: false
logging_info:
  name: TinyClassificationAccuracy
  research_problem: Classification
  metric: Accuracy
  estimated_worst_score: 0.1
  optimal_score: 1.0
  sota:
    - sota_score: 0.9
""",
        encoding="utf-8",
    )
    (source / "evaluate.py").write_text("# evaluator fixture\n", encoding="utf-8")
    destination = import_airs_task(source, tmp_path / "imported")
    spec = json.loads((destination / "task.json").read_text(encoding="utf-8"))
    assert spec["source"] == "airs"
    assert spec["primary_metric"] == "Accuracy"
    assert spec["direction"] == "maximize"
    assert spec["baseline_score"] == pytest.approx(0.1)
    assert spec["target_score"] == pytest.approx(0.9)
    assert spec["runnable"] is False
    assert (destination / "source" / "evaluate.py").is_file()


def test_activate_airs_lite_rejects_custom_gold_accuracy_without_adapter(
    tmp_path: Path,
) -> None:
    source = tmp_path / "airs-custom-gold"
    source.mkdir()
    (source / "project_description.md").write_text(
        "Answer a task whose official scorer needs more than one hidden label field.",
        encoding="utf-8",
    )
    (source / "metadata.yaml").write_text(
        """metric_lower_is_better: false
logging_info:
  name: CustomGoldAccuracy
  research_problem: Question Answering
  metric: Accuracy
  custom_gold_labels: true
  estimated_worst_score: 0.0
  optimal_score: 1.0
  sota:
    - sota_score: 0.8
""",
        encoding="utf-8",
    )
    pack = import_airs_task(source, tmp_path / "packs")
    with pytest.raises(ValueError, match="task-specific"):
        activate_airs_lite(
            pack,
            dataset_python=tmp_path / "missing-python.exe",
            cache_dir=tmp_path / "cache",
        )


def test_activate_airs_lite_generates_runnable_protected_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "airs-source"
    source.mkdir()
    (source / "project_description.md").write_text(
        "Classify paired examples using an official benchmark split and hidden labels.",
        encoding="utf-8",
    )
    (source / "metadata.yaml").write_text(
        """metric_lower_is_better: false
logging_info:
  name: TinyPairedAccuracy
  dataset: example/tiny
  config: default
  research_problem: Classification
  metric: Accuracy
  input_columns: [left, right]
  scoring_column: label
  train_split: train
  test_split: test
  estimated_worst_score: 0.1
  optimal_score: 1.0
  sota:
    - sota_score: 0.9
""",
        encoding="utf-8",
    )
    pack = import_airs_task(source, tmp_path / "packs")
    dataset_python = tmp_path / "dataset-python.exe"
    dataset_python.write_text("fixture", encoding="utf-8")

    def fake_export(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if Path(command[0]).name.lower() == "docker.exe":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="docker unavailable")
        if "-c" in command:
            return subprocess.CompletedProcess(command, 0, stdout="3.6.0\n", stderr="")
        output = Path(command[command.index("--output") + 1])
        (output / "agent").mkdir(parents=True)
        (output / "hidden").mkdir(parents=True)
        (output / "agent" / "train.jsonl").write_text(
            '\n'.join(
                [
                    json.dumps({"left": "a", "right": "a", "label": 1}),
                    json.dumps({"left": "b", "right": "c", "label": 1}),
                    json.dumps({"left": "d", "right": "e", "label": 0}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (output / "agent" / "test.jsonl").write_text(
            json.dumps({"left": "a", "right": "a"})
            + "\n"
            + json.dumps({"left": "b", "right": "c"})
            + "\n",
            encoding="utf-8",
        )
        (output / "hidden" / "labels.json").write_text("[1, 0]\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="exported", stderr="")

    with monkeypatch.context() as scoped:
        scoped.setattr("research_forge.benchmark.subprocess.run", fake_export)
        activated = activate_airs_lite(
            pack,
            dataset_python=dataset_python,
            cache_dir=tmp_path / "cache",
        )
    assert activated.runnable
    assert activated.baseline_score == pytest.approx(0.5)
    assert activated.baseline_parameters == {"constant_label": 1}
    assert (pack / "data" / "test.jsonl").is_file()
    assert (pack / "evaluator" / "labels.json").is_file()

    project = materialize_task(pack, activated, seed=0, project_root=tmp_path / "projects")
    baseline = execute_run(project)
    assert baseline.valid
    assert baseline.aggregate_metrics["Accuracy"] == pytest.approx(0.5)
    assert audit_project(project).passed
