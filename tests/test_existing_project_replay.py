from __future__ import annotations

from pathlib import Path

import pytest

from research_forge.container_execution import inspect_local_container_image
from research_forge.existing_project_replay import (
    ExistingProjectReplaySpec,
    build_existing_project_replay_package,
    replay_existing_project_package,
)


def _spec() -> ExistingProjectReplaySpec:
    return ExistingProjectReplaySpec(
        case_id="real-project-replay",
        source_files=["project.py", "tests/test_project.py"],
        container_image="python:3.12-slim",
        command=[
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        parser="unittest_v1",
        expected_metrics={
            "tests_run": 1,
            "failures": 0,
            "errors": 0,
            "pass_rate": 1,
        },
    )


def test_replay_spec_rejects_secret_and_escape_paths() -> None:
    with pytest.raises(ValueError):
        ExistingProjectReplaySpec(
            **{
                **_spec().model_dump(),
                "source_files": ["../project.py"],
            }
        )
    with pytest.raises(ValueError):
        ExistingProjectReplaySpec(
            **{
                **_spec().model_dump(),
                "source_files": [".env"],
            }
        )


def test_sealed_package_detects_source_independent_tampering(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "tests").mkdir()
    (source / "project.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "tests" / "test_project.py").write_text(
        "import unittest\n"
        "from project import VALUE\n"
        "class T(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(VALUE, 1)\n",
        encoding="utf-8",
    )
    package = tmp_path / "package"
    manifest = build_existing_project_replay_package(source, _spec(), package)
    assert manifest["source_root_recorded"] is False
    assert len(manifest["files"]) == 2

    (package / "project.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        replay_existing_project_package(package, work_root=tmp_path / "runs")


def test_package_identity_is_stable_across_materializations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "tests").mkdir()
    (source / "project.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "tests" / "test_project.py").write_text(
        "import unittest\n"
        "from project import VALUE\n"
        "class T(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(VALUE, 1)\n",
        encoding="utf-8",
    )
    first = build_existing_project_replay_package(
        source, _spec(), tmp_path / "package-a"
    )
    second = build_existing_project_replay_package(
        source, _spec(), tmp_path / "package-b"
    )
    assert first["package_sha256"] == second["package_sha256"]


@pytest.mark.skipif(
    __import__("shutil").which("docker") is None,
    reason="Docker is required for isolated clean replay",
)
def test_project_replays_twice_in_fresh_offline_containers(
    tmp_path: Path,
) -> None:
    if inspect_local_container_image("docker", "python:3.12-slim") is None:
        pytest.skip("a running Docker daemon with python:3.12-slim is required")
    source = tmp_path / "source"
    source.mkdir()
    (source / "tests").mkdir()
    (source / "project.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "tests" / "test_project.py").write_text(
        "import unittest\n"
        "from project import VALUE\n"
        "class T(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(VALUE, 1)\n",
        encoding="utf-8",
    )
    package = tmp_path / "package"
    build_existing_project_replay_package(source, _spec(), package)
    report = replay_existing_project_package(
        package, work_root=tmp_path / "runs"
    )
    assert report["status"] == "verified"
    assert report["clean_workspace_count"] == 2
    assert report["cross_replay_equal"] is True
    assert report["historical_match"] is True
    assert all(
        run["isolation_attestation"]["network"] == "none"
        for run in report["runs"]
    )
