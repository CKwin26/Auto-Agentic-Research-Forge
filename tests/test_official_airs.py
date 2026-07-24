from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

from research_forge.official_airs import (
    evaluate_official_airs_submission,
    import_official_airs_task,
    prepare_official_airs_task,
)


def _write_task(root: Path) -> None:
    (root / "metadata.yaml").write_text("logging_info:\n  name: TinyOfficialAirs\n", encoding="utf-8")
    (root / "project_description.md").write_text("official fixture", encoding="utf-8")
    (root / "prepare.py").write_text("import argparse, pathlib\np=argparse.ArgumentParser(); p.add_argument('--global-shared-data-dir'); p.add_argument('--agent-data-mount-dir'); p.add_argument('--agent-log-dir'); a=p.parse_args(); pathlib.Path(a.agent_data_mount_dir,'public.txt').write_text('public')\n", encoding="utf-8")
    (root / "evaluate_prepare.py").write_text("import argparse, pathlib, shutil\np=argparse.ArgumentParser(); p.add_argument('--global-shared-data-dir'); p.add_argument('--agent-data-mount-dir'); p.add_argument('--agent-log-dir'); a=p.parse_args(); shutil.copy2(pathlib.Path(a.agent_log_dir,'submission.csv'), pathlib.Path(a.agent_data_mount_dir,'submission.csv'))\n", encoding="utf-8")
    evaluator = """import argparse, pathlib
p=argparse.ArgumentParser(); p.add_argument('--submission-file'); a=p.parse_args()
assert pathlib.Path(a.submission_file).read_text() == 'value\\nok\\n'
print('{\"Accuracy\": 1.0}')
"""
    (root / "evaluate.py").write_text(evaluator, encoding="utf-8")


def test_official_adapter_runs_verbatim_prepare_and_evaluation(tmp_path: Path) -> None:
    source = tmp_path / "official-source"; source.mkdir(); _write_task(source)
    task = import_official_airs_task(source, tmp_path / "packs")
    raw, agent, log, evaluator = (tmp_path / name for name in ("raw", "agent", "log", "evaluator"))
    prepared = prepare_official_airs_task(task, global_shared_data_dir=raw, agent_data_mount_dir=agent, agent_log_dir=log, evaluator_data_mount_dir=evaluator, python=sys.executable)
    assert (agent / "public.txt").read_text() == "public"
    assert prepared["leaderboard_status"] == "not_submitted"
    (log / "submission.csv").write_text("value\nok\n", encoding="utf-8")
    scored = evaluate_official_airs_submission(task, python=sys.executable)
    assert scored["evaluation_prepared"]
    assert "1.0" in scored["evaluate"]["stdout"]
    (task / "evaluate.py").write_text("tampered", encoding="utf-8")
    try:
        prepare_official_airs_task(task, global_shared_data_dir=raw, agent_data_mount_dir=agent, agent_log_dir=log, evaluator_data_mount_dir=evaluator, python=sys.executable)
    except ValueError as exc:
        assert "source drift" in str(exc)
    else:
        raise AssertionError("official source drift must be rejected")


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_AIRS_TEST") != "1", reason="requires locally built official AIRS Docker image")
def test_official_adapter_runs_in_linux_docker(tmp_path: Path) -> None:
    source = tmp_path / "official-source"; source.mkdir(); _write_task(source)
    task = import_official_airs_task(source, tmp_path / "packs")
    raw, agent, log, evaluator = (tmp_path / name for name in ("raw", "agent", "log", "evaluator"))
    prepare_official_airs_task(
        task, global_shared_data_dir=raw, agent_data_mount_dir=agent, agent_log_dir=log,
        evaluator_data_mount_dir=evaluator, python=sys.executable,
        execution="docker", docker_image="research-forge/airs-official-cpu:v1",
    )
    assert (agent / "public.txt").read_text() == "public"
    (log / "submission.csv").write_text("value\nok\n", encoding="utf-8")
    scored = evaluate_official_airs_submission(
        task, python=sys.executable, execution="docker", docker_image="research-forge/airs-official-cpu:v1",
    )
    assert scored["evaluation_prepared"]
    assert "1.0" in scored["evaluate"]["stdout"]
    assert "--network" in scored["evaluate"]["command"]
