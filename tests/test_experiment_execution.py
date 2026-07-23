from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_forge.experiment_execution import (
    ExperimentArtifactSpec,
    ExperimentPaused,
    ExperimentSpec,
    load_project_experiment_manifest,
    run_declared_experiment,
)
from research_forge.storage import read_json


def test_declared_experiment_can_be_paused_without_publishing_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "slow.py").write_text(
        "import pathlib, sys, time\ntime.sleep(5)\npathlib.Path(sys.argv[1]).write_text('{}')\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "execution"
    spec = ExperimentSpec(
        experiment_id="pause-test",
        action_ids=["action-independent-evaluation"],
        title="可暂停实验",
        command=["{python}", "slow.py", "{evidence_dir}/result.json"],
        artifacts=[ExperimentArtifactSpec(path="result.json", format="json")],
    )

    with pytest.raises(ExperimentPaused):
        run_declared_experiment(
            spec,
            source_root=source,
            evidence_dir=evidence,
            action_id="action-independent-evaluation",
            plan_id="remediation-000000000000",
            network_authorized=False,
            report_progress=lambda **_: None,
            control_status=lambda: "pause_requested",
        )

    assert read_json(evidence / "execution.json")["status"] == "paused"
    assert not (evidence / "artifact_manifest.json").exists()


def test_frozen_execution_contract_is_adapted_for_independent_evaluation(tmp_path: Path) -> None:
    (tmp_path / "execution_contract.json").write_text(
        json.dumps({
            "schema_version": 1,
            "configured_by_user": True,
            "command": ["{python}", "{experiment_dir}/run.py", "--metrics", "{metrics_file}"],
            "evaluator_command": None,
            "primary_metric": "accuracy",
            "direction": "maximize",
            "required_metrics": ["loss"],
            "timeout_seconds": 120,
        }),
        encoding="utf-8",
    )

    manifest, path = load_project_experiment_manifest(tmp_path)

    assert path == tmp_path / "execution_contract.json"
    assert manifest is not None
    spec = manifest.experiments[0]
    assert spec.action_ids == ["action-independent-evaluation"]
    assert spec.artifacts[0].required_keys == ["loss", "accuracy"]


def test_artifact_path_cannot_escape_isolated_execution_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "noop.py").write_text("print('ok')\n", encoding="utf-8")
    spec = ExperimentSpec(
        experiment_id="escape-test",
        action_ids=["action-independent-evaluation"],
        title="越界检查",
        command=["{python}", "noop.py"],
        artifacts=[ExperimentArtifactSpec(path="../outside.json")],
    )

    with pytest.raises(ValueError, match="escapes allowed root"):
        run_declared_experiment(
            spec,
            source_root=source,
            evidence_dir=tmp_path / "execution",
            action_id="action-independent-evaluation",
            plan_id="remediation-000000000000",
            network_authorized=False,
            report_progress=lambda **_: None,
        )
