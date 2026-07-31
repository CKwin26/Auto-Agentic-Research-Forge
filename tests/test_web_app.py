from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

import research_forge.web_app as web_app
from research_forge.cli import _parser
from research_forge.web_app import (
    _write_runtime_env,
    _repair_mojibake,
    _safe_run_dir,
    bootstrap_payload,
    configure_external_research_setup,
    configure_runtime,
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
        {
            "source_root": "C:/projects/stock",
            "selected_track_id": "exit-signal-research-v1",
        },
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
    _write_json(
        run / "inspection.json",
        {"recommended_track_id": "exit-signal-research-v1", "candidates": []},
    )
    _write_json(
        run / "stage_1_discovery" / "scope_contract.json",
        {"track_id": "exit-signal-research-v1"},
    )
    _write_json(
        run / "stage_2_protocol" / "protocol_lock.json",
        {"protocol_bound_to_output": True},
    )
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
    (run / "stage_4_synthesis" / "manuscript.md").write_text(
        "# Working paper", encoding="utf-8"
    )
    for stage in (
        "stage_1_discovery",
        "stage_2_protocol",
        "stage_3_experimentation",
        "stage_4_synthesis",
    ):
        _write_json(
            run / stage / "resources.json",
            {"resources": [{"source_path": "README.md"}]},
        )
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
    assert (
        json.loads((project / "state.json").read_text(encoding="utf-8"))["stage"]
        == "scoping"
    )


def test_repairs_legacy_utf8_as_gbk_text() -> None:
    mojibake = "炒股".encode("utf-8").decode("gbk")
    assert _repair_mojibake(mojibake) == "炒股"


def test_health_endpoint(tmp_path: Path) -> None:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    server = create_server(
        "127.0.0.1", 0, runs_root=tmp_path / "runs", static_root=static
    )
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


def test_runtime_env_update_is_atomic_and_preserves_unrelated_values(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text(
        "# local only\nREDFOX_API_KEY=keep-me\nRESEARCH_FORGE_BACKEND=codex\n",
        encoding="utf-8",
    )

    _write_runtime_env(
        {
            "RESEARCH_FORGE_BACKEND": "api",
            "OPENAI_API_KEY": "secret-placeholder",
            "AUTORESEARCH_MODEL": "test-model",
        },
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "REDFOX_API_KEY=keep-me" in text
    assert "RESEARCH_FORGE_BACKEND=api" in text
    assert "OPENAI_API_KEY=secret-placeholder" in text
    assert not list(tmp_path.glob(".*.tmp"))


def test_runtime_config_rejects_insecure_custom_api_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        configure_runtime(
            {
                "backend": "api",
                "api_key": "secret-placeholder",
                "model": "test-model",
                "base_url": "http://provider.example/v1",
            },
            env_path=tmp_path / ".env.local",
        )


def test_selecting_managed_codex_clears_stale_custom_provider_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text(
        "RESEARCH_FORGE_BACKEND=codex\n"
        "RESEARCH_FORGE_CODEX_HOME=C:\\\\custom-provider\n"
        "RESEARCH_FORGE_PROVIDER_BILLING_CONTRACT=C:\\\\billing.json\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        web_app,
        "runtime_status_payload",
        lambda: {"backend": "codex", "ready": True},
    )

    configure_runtime(
        {"backend": "codex", "provider_mode": "managed"},
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "RESEARCH_FORGE_BACKEND=codex" in text
    assert "RESEARCH_FORGE_CODEX_HOME=" not in text
    assert "RESEARCH_FORGE_PROVIDER_BILLING_CONTRACT=" not in text


def test_external_research_setup_keeps_project_network_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESEARCH_FORGE_EXTERNAL_RESEARCH_SETUP", raising=False)
    env_path = tmp_path / ".env.local"

    configured = configure_external_research_setup(
        {"choice": "offline", "install_optional": False},
        env_path=env_path,
    )

    assert configured["choice"] == "offline"
    assert configured["project_network_default"] == "offline"
    assert configured["requires_project_owner_network_approval"] is True
    assert (
        "RESEARCH_FORGE_EXTERNAL_RESEARCH_SETUP=offline"
        in env_path.read_text(encoding="utf-8")
    )


def test_external_research_setup_installs_only_fixed_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[str] = []

    def fake_run(command: list[str], **_: object) -> object:
        observed.extend(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(web_app.subprocess, "run", fake_run)
    monkeypatch.setattr(
        web_app,
        "external_research_setup_payload",
        lambda **_: {
            "choice": "public",
            "components": [],
            "dependencies_installed": True,
            "project_network_default": "offline",
            "requires_project_owner_network_approval": True,
        },
    )

    configured = configure_external_research_setup(
        {
            "choice": "public",
            "install_optional": True,
            "packages": ["untrusted-user-supplied-package"],
        },
        env_path=tmp_path / ".env.local",
    )

    assert configured["choice"] == "public"
    assert "--user" in observed
    assert "--no-warn-script-location" in observed
    assert "paper-search-mcp==0.1.4" in observed
    assert "paper-qa==2026.3.18" in observed
    assert "validate_external_research_v1.py" in " ".join(observed)
    assert "--quick" in observed
    assert "--live" in observed
    assert "untrusted-user-supplied-package" not in observed


def test_runtime_endpoints_return_only_safe_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        web_app,
        "runtime_status_payload",
        lambda **_: {
            "backend": "codex",
            "model": "codex:gpt-5.6-sol",
            "provider_name": "OpenAI",
            "codex_authenticated": True,
            "ready": True,
        },
    )

    def fake_configure(payload: dict[str, object]) -> dict[str, object]:
        observed.update(payload)
        return {
            "backend": "api",
            "model": "gpt-5.6-terra",
            "provider_name": "OpenAI-compatible API",
            "ready": True,
        }

    monkeypatch.setattr(web_app, "configure_runtime", fake_configure)
    monkeypatch.setattr(
        web_app,
        "external_research_setup_payload",
        lambda **_: {
            "choice": "unconfigured",
            "components": [],
            "dependencies_installed": False,
            "project_network_default": "offline",
        },
    )
    monkeypatch.setattr(
        web_app,
        "configure_external_research_setup",
        lambda payload, **_: {
            "choice": payload["choice"],
            "components": [],
            "dependencies_installed": False,
            "project_network_default": "offline",
        },
    )
    server = create_server(
        "127.0.0.1", 0, runs_root=tmp_path / "runs", static_root=static
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/runtime/status"
        ) as response:
            status = json.loads(response.read().decode("utf-8"))
        assert status["ready"] is True
        assert "api_key" not in status

        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/runtime/configure",
            data=json.dumps(
                {
                    "backend": "api",
                    "model": "gpt-5.6-terra",
                    "api_key": "secret-not-returned",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            configured = json.loads(response.read().decode("utf-8"))
        assert observed["api_key"] == "secret-not-returned"
        assert configured["ready"] is True
        assert "api_key" not in configured
        assert "secret-not-returned" not in json.dumps(configured)

        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/runtime/external-research"
        ) as response:
            external = json.loads(response.read().decode("utf-8"))
        assert external["choice"] == "unconfigured"
        assert external["project_network_default"] == "offline"

        external_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/runtime/external-research",
            data=json.dumps(
                {"choice": "offline", "install_optional": False}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(external_request) as response:
            selected = json.loads(response.read().decode("utf-8"))
        assert selected["choice"] == "offline"

        task_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/tasks/submit",
            data=json.dumps(
                {
                    "operation": "bundle.inspect",
                    "payload": {
                        "source": str(tmp_path / "project"),
                        "discover_claims": False,
                    },
                    "request_id": "web-injects-workflow-root",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(task_request) as response:
            task = json.loads(response.read().decode("utf-8"))
        assert task["request"]["payload"]["workflow_root"] == str(
            (tmp_path / "runs" / ".workflow-v2").resolve()
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_discovery_selection_endpoint_freezes_the_selected_scope(
    tmp_path: Path,
) -> None:
    from research_forge.workflow_domain import WorkflowRepository
    from research_forge.workflow_scheduler import run_project_discovery

    source = tmp_path / "bundle"
    (source / "protocols").mkdir(parents=True)
    (source / "outputs").mkdir()
    (source / "reports").mkdir()
    _write_json(
        source / "protocols" / "demo.json",
        {
            "version": "demo",
            "hypothesis": "The candidate improves accuracy.",
            "metric": "accuracy",
        },
    )
    _write_json(
        source / "outputs" / "demo.json",
        {"accuracy": 0.81, "valid": True},
    )
    (source / "reports" / "demo.md").write_text(
        "# Contributions\n\nThe paired evaluation improves accuracy to 0.81.\n",
        encoding="utf-8",
    )
    (source / "src").mkdir()
    (source / "src" / "model.py").write_text(
        "def predict(value):\n    return int(value > 0)\n",
        encoding="utf-8",
    )
    (source / "data").mkdir()
    (source / "data" / "test.csv").write_text(
        "x,label\n1,1\n",
        encoding="utf-8",
    )
    workflow_root = tmp_path / "workflow"
    discovery = run_project_discovery(
        source,
        repository_root=workflow_root,
        include_external=False,
        identity="web-selection",
    )
    direction_id = discovery["discovery_portfolio"][
        "recommended_direction_id"
    ]

    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    server = create_server(
        "127.0.0.1",
        0,
        runs_root=tmp_path / "runs",
        static_root=static,
        workflow_root=workflow_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/studies/discovery/select",
            data=json.dumps(
                {
                    "study_id": discovery["study_id"],
                    "direction_id": direction_id,
                    "decided_by": "project_owner",
                    "reason": "Narrow the owner-approved Scope.",
                    "scope_overrides": {
                        "research_question": "Can the revised bundle question be evaluated?",
                        "scope_out": ["unregistered deployment claims"],
                    },
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            result = json.loads(response.read().decode("utf-8"))
        assert result["scope_contract"]["status"] == "frozen"
        assert result["scope_contract"]["field_diff"][
            "selected_direction_id"
        ] == direction_id
        assert result["scope_contract"]["research_question"] == (
            "Can the revised bundle question be evaluated?"
        )
        assert result["scope_contract"]["scope_out"] == [
            "unregistered deployment claims"
        ]
        assert result["gate"]["status"] == "approved"
        assert next(
            item
            for item in result["workflow"]["steps"]
            if item["step_type"] == "freeze_scope_contract"
        )["status"] == "succeeded"
        assert next(
            item
            for item in result["workflow"]["steps"]
            if item["step_type"] == "select_specific_topic"
        )["status"] == "waiting_for_user"
        assert (
            WorkflowRepository(workflow_root)
            .latest_scope_contract(discovery["study_id"])
            .status.value
            == "frozen"
        )
        candidates = json.loads(
            (
                workflow_root
                / "studies"
                / discovery["study_id"]
                / "stage2"
                / "candidate_topics.json"
            ).read_text(encoding="utf-8")
        )
        topic_request = Request(
            f"http://127.0.0.1:{server.server_port}"
            "/api/studies/stage2/topics/select",
            data=json.dumps(
                {
                    "study_id": discovery["study_id"],
                    "topic_id": candidates["recommended_topic_id"],
                    "scope_overrides": {"primary_outcome": "accuracy"},
                    "protocol_overrides": {
                        "primary_metric": "accuracy",
                        "metric_direction": "higher_is_better",
                    },
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(topic_request) as response:
            topic_result = json.loads(response.read().decode("utf-8"))
        assert topic_result["scope_contract"]["contract_level"] == "specific_topic"
        assert topic_result["scope_contract"]["version"] == 2
        assert next(
            item
            for item in topic_result["workflow"]["steps"]
            if item["step_type"] == "assess_stage2_gate"
        )["status"] == "succeeded"
        assert json.loads(
            (
                workflow_root
                / "studies"
                / discovery["study_id"]
                / "stage2"
                / "stage2_gate_report.json"
            ).read_text(encoding="utf-8")
        )["status"] == "DESIGN_READY"
        with urlopen(
            f"http://127.0.0.1:{server.server_port}"
            f"/api/studies/stage2?study_id={discovery['study_id']}"
        ) as response:
            stage_two = json.loads(response.read().decode("utf-8"))
        assert stage_two["artifacts"]["candidate_topics.json"]["candidates"]
        assert stage_two["artifacts"]["resource_requirements.json"][
            "requirements"
        ]
        assert stage_two["artifacts"]["resource_candidate_evaluation.json"][
            "candidates"
        ]
        assert stage_two["artifacts"]["resource_selection.json"]["status"] == (
            "frozen"
        )
        assert stage_two["artifacts"]["resource_acquisition_plan.md"].startswith(
            "# Resource acquisition plan"
        )
        assert stage_two["artifacts"]["topic_feasibility_matrix.md"].startswith(
            "# Topic feasibility matrix"
        )
        assert stage_two["artifacts"]["topic_recommendation.md"].startswith(
            "# Topic recommendation"
        )
        assert stage_two["artifacts"]["mvp_spec.json"][
            "scientific_evidence_eligible"
        ] is False
        assert stage_two["artifacts"]["stage3_resource_plan.json"]["tasks"]
        revise_request = Request(
            f"http://127.0.0.1:{server.server_port}"
            "/api/studies/stage2/protocol/revise",
            data=json.dumps(
                {
                    "study_id": discovery["study_id"],
                    "reason": "Complete pre-freeze protocol fields.",
                    "protocol_overrides": {
                        "sample_size": "12 frozen rows",
                        "statistical_power": "descriptive feasibility revision",
                    },
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(revise_request) as response:
            revised = json.loads(response.read().decode("utf-8"))
        assert revised["requested_version"] == 2
        with urlopen(
            f"http://127.0.0.1:{server.server_port}"
            f"/api/studies/stage2?study_id={discovery['study_id']}"
        ) as response:
            stage_two_v2 = json.loads(response.read().decode("utf-8"))
        assert (
            stage_two_v2["artifacts"]["protocol.draft.json"][
                "research_contract_version"
            ]
            == 2
        )
        mvp_report = tmp_path / "stage2-mvp-report.json"
        _write_json(
            mvp_report,
            {
                "scientific_evidence_eligible": False,
                "environment_started": True,
                "metric_computable": True,
                "baseline_instantiable": True,
                "failure_modes_distinguishable": True,
                "reset_or_isolation_verified": True,
                "runtime_seconds": 1.5,
                "smoke_cases": [
                    {"case_id": "smoke-1", "status": "passed"},
                    {"case_id": "smoke-2", "status": "passed"},
                ],
            },
        )
        mvp_request = Request(
            f"http://127.0.0.1:{server.server_port}"
            "/api/studies/stage2/mvp/approve",
            data=json.dumps(
                {
                    "study_id": discovery["study_id"],
                    "report_path": str(mvp_report),
                    "decided_by": "project_owner",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(mvp_request) as response:
            mvp_result = json.loads(response.read().decode("utf-8"))
        assert mvp_result["mvp"]["status"] == "verified"
        assert mvp_result["mvp"]["scientific_evidence_eligible"] is False
        assert mvp_result["revision"]["requested_version"] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_retrieval_api_defaults_offline_and_returns_blocked_run(
    tmp_path: Path,
) -> None:
    from research_forge.workflow_domain import (
        EntryMode,
        ExecutorType,
        Phase,
        WorkflowRepository,
    )

    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("ok", encoding="utf-8")
    workflow_root = tmp_path / "workflow"
    repository = WorkflowRepository(workflow_root)
    project = repository.create_project("Gateway API")
    study = repository.create_study(
        project.project_id,
        "Gateway API study",
        entry_mode=EntryMode.PROJECT_TO_PAPER,
    )
    step = repository.add_step(
        study.study_id,
        "execute_discovery_retrieval",
        Phase.DISCOVERY,
        ExecutorType.RETRIEVAL_SERVICE,
    )
    server = create_server(
        "127.0.0.1",
        0,
        runs_root=tmp_path / "runs",
        idea_root=tmp_path / "ideas",
        static_root=static,
        workflow_root=workflow_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/retrieval/policy"
            f"?project_id={project.project_id}"
        ) as response:
            policy = json.loads(response.read().decode("utf-8"))
        assert policy["mode"] == "offline"

        plan_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/retrieval/plan",
            data=json.dumps(
                {
                    "project_id": project.project_id,
                    "study_id": study.study_id,
                    "phase": "discovery",
                    "step_instance_id": step.step_instance_id,
                    "purpose": "related_work_search",
                    "queries": ["evidence bound agents"],
                    "providers": ["semantic_scholar"],
                    "resource_types": ["publication"],
                    "usage_role": "background_source",
                    "budget": {"max_queries": 1, "max_results": 10},
                    "idempotency_key": "web-retrieval-offline-v1",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(plan_request) as response:
            planned = json.loads(response.read().decode("utf-8"))
        run_request = Request(
            f"http://127.0.0.1:{server.server_port}/api/retrieval/run",
            data=json.dumps({"request_id": planned["request_id"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(run_request) as response:
            execution = json.loads(response.read().decode("utf-8"))
        assert execution["run"]["execution_status"] == "blocked"
        assert execution["run"]["error_classification"] == "policy_denied"
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/retrieval/overview"
            f"?project_id={project.project_id}&study_id={study.study_id}"
        ) as response:
            overview = json.loads(response.read().decode("utf-8"))
        assert overview["policy"]["mode"] == "offline"
        assert overview["runs"][0]["request"]["purpose"] == "related_work_search"
        assert overview["runs"][0]["run"]["execution_status"] == "blocked"
        assert overview["runs"][0]["coverage"] is None
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
    monkeypatch.setattr(
        web_app, "select_project_folder", lambda initial="": str(selected)
    )
    server = create_server(
        "127.0.0.1", 0, runs_root=tmp_path / "runs", static_root=static
    )
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
        assert any(
            item["action_id"] == "action-independent-evaluation"
            for item in plan["actions"]
        )

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
        assert [stage["resource_count"] for stage in completed["stages"]] == [
            1,
            1,
            2,
            2,
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
