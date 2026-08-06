from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from research_forge.container_execution import (
    ContainerExecutionPolicy,
    ContainerSupplyChainEvidence,
    inspect_local_container_image,
    run_isolated_command,
)
from research_forge.experiment_execution import (
    ExperimentArtifactSpec,
    ExperimentSpec,
)
from research_forge.stage_three import _run_isolated_candidate_evaluator


def test_container_command_enforces_stage3_isolation(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "generated-code"
    output = tmp_path / "isolated-output"
    source.mkdir()
    (source / "run.py").write_text("print('ok')\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="sha256:test-image\n", stderr=""
            )
        return subprocess.CompletedProcess(
            command, 0, stdout="ok\n", stderr=""
        )

    monkeypatch.setattr("shutil.which", lambda _: "docker")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_isolated_command(
        ["python", "run.py"],
        input_dir=source,
        output_dir=output,
        policy=ContainerExecutionPolicy(image="python:test"),
    )

    command = calls[-1]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert ["--cap-drop", "ALL"] == command[
        command.index("--cap-drop") : command.index("--cap-drop") + 2
    ]
    assert "no-new-privileges" in command
    assert command[command.index("--ipc") + 1] == "none"
    assert command[command.index("--ulimit") + 1] == "nofile=1024:1024"
    assert any(
        item.endswith(",dst=/workspace/input,readonly") for item in command
    )
    assert any(item.endswith(",dst=/workspace/output") for item in command)
    assert result.isolation_attestation["host_secrets_mounted"] is False
    assert result.isolation_attestation["host_repository_writable"] is False
    assert result.isolation_attestation["docker_socket_mounted"] is False
    assert result.isolation_attestation["run_as_non_root"] is True


def test_container_rejects_secret_or_socket_named_mount_material(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env").write_text("TOKEN=not-for-container")

    with pytest.raises(ValueError, match="forbidden host material"):
        run_isolated_command(
            ["python", "run.py"],
            input_dir=source,
            output_dir=tmp_path / "output",
        )


def test_container_image_lookup_falls_back_to_filtered_local_id(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="sha256:" + "a" * 64 + "\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert inspect_local_container_image("docker", "python:test") == (
        "sha256:" + "a" * 64
    )
    assert calls[-1][-1] == "reference=python:test"


def test_container_supply_chain_evidence_is_content_addressed(
    tmp_path: Path,
) -> None:
    sbom = tmp_path / "sbom.spdx.json"
    scan = tmp_path / "cve-report.json"
    sbom.write_text("{}")
    scan.write_text("{}")
    from research_forge.storage import sha256_file

    evidence = ContainerSupplyChainEvidence(
        image_digest="sha256:" + "a" * 64,
        signature_verified=True,
        signature_policy="cosign-keyless-production",
        sbom_path=str(sbom),
        sbom_sha256=sha256_file(sbom),
        vulnerability_report_path=str(scan),
        vulnerability_report_sha256=sha256_file(scan),
        critical_vulnerability_count=0,
        high_vulnerability_count=0,
        scanner_version="scanner-test-v1",
    )
    policy = ContainerExecutionPolicy(
        require_supply_chain_evidence=True,
        supply_chain_evidence=evidence,
    )

    assert policy.supply_chain_evidence == evidence
    with pytest.raises(ValueError, match="critical vulnerabilities"):
        ContainerExecutionPolicy(
            require_supply_chain_evidence=True,
            supply_chain_evidence=ContainerSupplyChainEvidence(
                **{
                    **evidence.__dict__,
                    "critical_vulnerability_count": 1,
                }
            ),
        )


def test_container_rejects_overlapping_input_and_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    try:
        run_isolated_command(
            ["python", "run.py"],
            input_dir=source,
            output_dir=source / "output",
        )
    except ValueError as exc:
        assert "must be disjoint" in str(exc)
    else:
        raise AssertionError("overlapping container mounts were accepted")


def test_real_two_container_candidate_evaluator_boundary(
    tmp_path: Path,
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is unavailable")
    image_id = inspect_local_container_image(docker, "python:3.12-slim")
    if image_id is None:
        pytest.skip("python:3.12-slim image is unavailable")
    source = tmp_path / "frozen-package"
    (source / "data").mkdir(parents=True)
    (source / "data" / "formal.jsonl").write_text(
        '{"sample_id":"s1","features":{"x":1},'
        '"target_reference":"opaque-1"}\n',
        encoding="utf-8",
    )
    (source / "data" / "targets.jsonl").write_text(
        '{"target_reference":"opaque-1","target":1}\n',
        encoding="utf-8",
    )
    (source / "candidate.py").write_text(
        """
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--data", required=True)
p.add_argument("--output", required=True)
a = p.parse_args()
rows = [
    json.loads(line)
    for line in Path(a.data).read_text(encoding="utf-8").splitlines()
    if line
]
Path(a.output).write_text(
    "".join(json.dumps({
        "sample_id": row["sample_id"],
        "prediction": 1,
        "target_reference": row["target_reference"],
    }) + "\\n" for row in rows),
    encoding="utf-8",
)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (source / "platform_evaluator.py").write_text(
        """
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--input", required=True)
p.add_argument("--targets", required=True)
p.add_argument("--output", required=True)
a = p.parse_args()
predictions = [
    json.loads(line)
    for line in Path(a.input).read_text(encoding="utf-8").splitlines()
    if line
]
targets = {
    row["target_reference"]: row["target"]
    for row in (
        json.loads(line)
        for line in Path(a.targets).read_text(encoding="utf-8").splitlines()
        if line
    )
}
result = {
    "accuracy": sum(
        row["prediction"] == targets[row["target_reference"]]
        for row in predictions
    ) / len(predictions),
    "denominator": len(predictions),
    "sample_ids": [row["sample_id"] for row in predictions],
    "abstentions": 0,
}
Path(a.output).write_text(json.dumps(result), encoding="utf-8")
""".strip()
        + "\n",
        encoding="utf-8",
    )
    spec = ExperimentSpec(
        experiment_id="isolated-real-probe",
        action_ids=["action-real-probe"],
        title="Real two-container boundary",
        command=[
            "{python}",
            "candidate.py",
            "--data",
            "{data_file}",
            "--output",
            "{prediction_file}",
        ],
        required_inputs=["data/formal.jsonl"],
        execution_backend="isolated_candidate_evaluator",
        container_image="python:3.12-slim",
        candidate_code_paths=["candidate.py"],
        evaluator_command=[
            "{python}",
            "platform_evaluator.py",
            "--input",
            "{prediction_file}",
            "--targets",
            "{target_file}",
            "--output",
            "{metrics_file}",
        ],
        evaluator_code_paths=["platform_evaluator.py"],
        evaluator_required_inputs=["data/targets.jsonl"],
        prediction_artifact_path="predictions.jsonl",
        artifacts=[
            ExperimentArtifactSpec(
                path="metrics.json",
                format="json",
                required_keys=["accuracy", "denominator", "sample_ids"],
            )
        ],
    )

    report = _run_isolated_candidate_evaluator(
        spec,
        source_root=source,
        evidence_dir=tmp_path / "evidence",
        run_variables={
            "task_id": "probe",
            "split_id": "formal",
            "arm_id": "treatment",
            "seed": "1",
            "replicate": "1",
            "run_cell_id": "probe-cell",
        },
        expected_image_id=image_id,
    )

    assert json.loads(
        Path(report["artifacts"][0]["path"]).read_text(encoding="utf-8")
    )["accuracy"] == 1.0
    assert not (
        tmp_path / "evidence" / "candidate-input" / "data" / "targets.jsonl"
    ).exists()
    assert report["isolation_attestations"][
        "candidate_targets_mounted"
    ] is False
