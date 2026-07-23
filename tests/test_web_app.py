from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

import research_forge.web_app as web_app
from research_forge.cli import _parser
from research_forge.web_app import (
    _repair_mojibake,
    _safe_run_dir,
    bootstrap_payload,
    create_server,
    initialize_idea_research,
    load_run_detail,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_run(root: Path, name: str = "stock-run") -> Path:
    run = root / name
    _write_json(
        run / "bundle_manifest.json",
        {"source_root": "C:/projects/stock", "selected_track_id": "exit-signal-research-v1"},
    )
    _write_json(
        run / "completion_certificate.json",
        {
            "track_id": "exit-signal-research-v1",
            "completed_at": "2026-07-18T10:56:33+00:00",
            "idea_status": "mixed",
            "idea_validated": False,
            "pilot_draft_generated": True,
            "manuscript_depth_passed": False,
            "paper_draft_ready": False,
            "publication_ready": False,
        },
    )
    _write_json(run / "inspection.json", {"recommended_track_id": "exit-signal-research-v1", "candidates": []})
    _write_json(run / "stage_1_discovery" / "scope_contract.json", {"track_id": "exit-signal-research-v1"})
    _write_json(run / "stage_2_protocol" / "protocol_lock.json", {"protocol_bound_to_output": True})
    _write_json(
        run / "stage_3_experimentation" / "idea_verdict.json",
        {"status": "mixed", "numeric_evidence": [{"path": "metric", "value": 0.64}]},
    )
    _write_json(
        run / "stage_4_synthesis" / "audit.json",
        {
            "passed": True,
            "pilot_draft_generated": True,
            "manuscript_depth_passed": False,
            "paper_draft_ready": False,
            "publication_ready": False,
        },
    )
    _write_json(
        run / "stage_4_synthesis" / "paper_expansion_plan.json",
        {
            "ready": False,
            "idea_gate_passed": False,
            "evidence_gate_passed": False,
            "literature_gate_passed": False,
        },
    )
    _write_json(
        run / "stage_4_synthesis" / "manuscript_depth.json",
        {
            "passed": False,
            "language": "zh",
            "profile": "journal-article-zh-v1",
            "unit": "han_chars",
            "total_count": 668,
        },
    )
    _write_json(run / "stage_4_synthesis" / "claims.json", {"claims": []})
    (run / "stage_4_synthesis" / "manuscript.md").write_text("# Working paper", encoding="utf-8")
    for stage in (
        "stage_1_discovery",
        "stage_2_protocol",
        "stage_3_experimentation",
        "stage_4_synthesis",
    ):
        _write_json(run / stage / "resources.json", {"resources": [{"source_path": "README.md"}]})
    return run


def test_web_parser_defaults_to_localhost() -> None:
    args = _parser().parse_args(["web"])
    assert args.command == "web"
    assert args.host == "127.0.0.1"
    assert args.port == 8765


def test_run_detail_and_bootstrap_use_completed_artifacts(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    detail = load_run_detail(run)
    bootstrap = bootstrap_payload(tmp_path)

    assert detail["track_id"] == "exit-signal-research-v1"
    assert detail["audit"]["passed"] is True
    assert detail["pilot_draft_generated"] is True
    assert detail["manuscript_depth_passed"] is False
    assert detail["paper_draft_ready"] is False
    assert detail["manuscript_depth"]["profile"] == "journal-article-zh-v1"
    assert detail["manuscript_depth"]["total_count"] == 668
    assert detail["manuscript"] == "# Working paper"
    assert [stage["resource_count"] for stage in detail["stages"]] == [1, 1, 1, 1]
    assert bootstrap["latest_run"]["name"] == "stock-run"


def test_run_lookup_rejects_traversal(tmp_path: Path) -> None:
    _make_run(tmp_path)
    with pytest.raises(FileNotFoundError):
        _safe_run_dir(tmp_path, "../outside")


def test_initialize_idea_research_creates_real_stage_one_intake(tmp_path: Path) -> None:
    result = initialize_idea_research(
        "竞争对手异常退出是否能够提前预测极端赢家？",
        title="退出信号研究",
        idea_root=tmp_path,
    )

    project = Path(result["project_path"])
    assert project.parent == tmp_path.resolve()
    assert result["status"] == "initialized"
    assert result["current_stage"] == "stage_1_discovery"
    assert result["stages"][0]["status"] == "ready"
    assert (project / "project.json").is_file()
    assert (project / "web_intake.json").is_file()
    assert json.loads((project / "state.json").read_text(encoding="utf-8"))["stage"] == "scoping"


def test_repairs_legacy_utf8_as_gbk_text() -> None:
    mojibake = "炒股".encode("utf-8").decode("gbk")
    assert _repair_mojibake(mojibake) == "炒股"


def test_health_endpoint(tmp_path: Path) -> None:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    server = create_server("127.0.0.1", 0, runs_root=tmp_path / "runs", static_root=static)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/health") as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"ok": True, "service": "research-forge-web"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_select_folder_endpoint_returns_native_picker_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "dist"
    selected = tmp_path / "资料库"
    static.mkdir()
    selected.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(web_app, "select_project_folder", lambda initial="": str(selected))
    server = create_server("127.0.0.1", 0, runs_root=tmp_path / "runs", static_root=static)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/select-folder",
            data=json.dumps({"initial": "C:/projects"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"path": str(selected), "cancelled": False}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_remediation_api_persists_selection_and_requires_contract_confirmation(
    tmp_path: Path,
) -> None:
    static = tmp_path / "dist"
    runs = tmp_path / "runs"
    static.mkdir()
    runs.mkdir()
    run = _make_run(runs)
    (static / "index.html").write_text("ok", encoding="utf-8")
    server = create_server("127.0.0.1", 0, runs_root=runs, static_root=static)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        plan_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/remediation/plan",
            data=json.dumps({"name": run.name}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(plan_request) as response:
            plan = json.loads(response.read().decode("utf-8"))
        assert plan["verdict_status"] == "mixed"
        assert any(item["action_id"] == "action-independent-evaluation" for item in plan["actions"])

        approve_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/remediation/approve",
            data=json.dumps(
                {
                    "name": run.name,
                    "selected_action_ids": ["action-independent-evaluation"],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(approve_request) as response:
            approved = json.loads(response.read().decode("utf-8"))
        assert approved["requires_contract_confirmation"] is True
        assert "task" not in approved
        assert approved["plan"]["selected_action_ids"] == [
            "action-independent-evaluation",
            "action-rescan-and-rejudge",
        ]

        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/remediation?name={run.name}"
        ) as response:
            restored = json.loads(response.read().decode("utf-8"))
        assert restored["plan_id"] == plan["plan_id"]
        assert restored["contract_confirmation_required"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_project_api_closes_plain_text_library_with_unverifiable_guard(
    tmp_path: Path,
) -> None:
    static = tmp_path / "dist"
    source = tmp_path / "资料库"
    static.mkdir()
    source.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    (source / "研究说明.md").write_text(
        """# 复习间隔研究

## 研究问题

不同复习间隔是否影响一周后的知识保持率？

## 结论

现有笔记提出了候选差异，但没有冻结协议，不能判断哪种间隔有效。
""",
        encoding="utf-8",
    )
    _write_json(
        source / "observations.json",
        {"selectedEvaluation": {"learners": 40, "retentionRate": 0.61}},
    )
    server = create_server(
        "127.0.0.1",
        0,
        runs_root=tmp_path / "runs",
        idea_root=tmp_path / "ideas",
        static_root=static,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        inspect_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/inspect",
            data=json.dumps({"source": str(source)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(inspect_request) as response:
            inspection = json.loads(response.read().decode("utf-8"))
        candidate = inspection["candidates"][0]
        assert candidate["source_mode"] == "derived_materials"
        assert candidate["closure_input_ready"] is True

        close_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/close-loop",
            data=json.dumps(
                {
                    "source": str(source),
                    "track_id": candidate["track_id"],
                    "name": "text-library-web-test",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(close_request) as response:
            completed = json.loads(response.read().decode("utf-8"))

        assert completed["idea_status"] == "unverifiable"
        assert completed["idea_validated"] is False
        assert completed["pilot_draft_generated"] is True
        assert completed["paper_draft_ready"] is False
        assert completed["audit"]["checks"]["derived_materials_verdict_guard"] is True
        assert [stage["resource_count"] for stage in completed["stages"]] == [1, 1, 2, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
