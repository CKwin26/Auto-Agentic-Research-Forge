from __future__ import annotations

from pathlib import Path

from research_forge.clean_room_replay import build_clean_room_docker_command


def test_clean_room_command_mounts_only_package_and_evidence(tmp_path: Path) -> None:
    package = tmp_path / "sealed.zip"
    package.write_bytes(b"zip")
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    command = build_clean_room_docker_command(
        package_path=package,
        evidence_directory=evidence,
        entrypoint_logical_path="runner/replay.py",
        image="python:3.12-slim",
        container_name="rf-clean-room-test",
    )

    joined = " ".join(command)
    assert "--network none" in joined
    assert "--read-only" in command
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in joined
    assert str(package.resolve()) in joined
    assert str(evidence.resolve()) in joined
    assert str(Path.cwd().resolve()) not in joined
    assert "docker.sock" not in joined
    assert "runner/replay.py" == command[-1]
