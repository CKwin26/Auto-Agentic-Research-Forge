from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

from .models import utc_now
from .service import create_project
from .storage import load_jsonl, read_json, write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "bundle_runs"
DEFAULT_IDEA_ROOT = PROJECT_ROOT / "idea_runs"
DEFAULT_STATIC_ROOT = PROJECT_ROOT / "research-forge-ui" / "dist"


def _retrieval_freshness(value: Any) -> Literal["cache_only", "live"]:
    freshness = str(value or "cache_only")
    if freshness not in {"cache_only", "live"}:
        raise ValueError("freshness must be cache_only or live")
    return freshness  # type: ignore[return-value]
ALLOWED_ARTIFACT_SUFFIXES = {".json", ".md", ".txt"}


def _repair_mojibake(value: str) -> str:
    """Repair the common UTF-8-as-GBK corruption present in legacy artifacts."""

    try:
        repaired = value.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if repaired != value else value


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, str):
        return _repair_mojibake(value)
    return value


def _read_json_if_present(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return _clean_json(read_json(path))


def _read_text_if_present(path: Path) -> str:
    if not path.is_file():
        return ""
    return _repair_mojibake(path.read_text(encoding="utf-8", errors="replace"))


def _default_source() -> str:
    stock_project = Path.home() / "Documents" / "炒股"
    return str(stock_project if stock_project.is_dir() else PROJECT_ROOT)


def select_project_folder(initial: str = "") -> str | None:
    """Open the native Windows folder picker for the local desktop web app."""

    if os.name != "nt":  # pragma: no cover - the desktop target is Windows
        raise RuntimeError("原生文件夹选择器目前仅支持 Windows")

    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("未找到 PowerShell，无法打开系统文件夹选择器")

    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择项目文件夹或文本资料库'
$dialog.ShowNewFolderButton = $false
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.StartPosition = 'Manual'
$workingArea = [System.Windows.Forms.Screen]::FromPoint(
    [System.Windows.Forms.Cursor]::Position
).WorkingArea
$owner.Location = New-Object System.Drawing.Point(
    ($workingArea.Left + [int]($workingArea.Width / 2)),
    ($workingArea.Top + [int]($workingArea.Height / 2))
)
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
$initial = $env:RESEARCH_FORGE_INITIAL_FOLDER
if ($initial -and [System.IO.Directory]::Exists($initial)) {
    $dialog.SelectedPath = $initial
}
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Write($dialog.SelectedPath)
}
$dialog.Dispose()
$owner.Close()
$owner.Dispose()
"""
    environment = os.environ.copy()
    environment["RESEARCH_FORGE_INITIAL_FOLDER"] = initial.strip()
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-STA",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("文件夹选择器等待超时") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "未知错误"
        raise RuntimeError(f"无法打开文件夹选择器：{detail}")

    selected = completed.stdout.strip()
    if not selected:
        return None
    selected_path = Path(selected).resolve()
    if not selected_path.is_dir():
        raise FileNotFoundError(f"selected folder not found: {selected_path}")
    return str(selected_path)


def _safe_run_dir(runs_root: Path, run_name: str) -> Path:
    root = runs_root.resolve()
    candidate = (root / Path(run_name).name).resolve()
    if candidate.parent != root or not candidate.is_dir():
        raise FileNotFoundError(f"bundle run not found: {run_name}")
    return candidate


def _safe_artifact_path(run_dir: Path, relative: str) -> Path:
    path = (run_dir / relative).resolve()
    if run_dir != path and run_dir not in path.parents:
        raise ValueError("artifact path escapes the selected run")
    if path.suffix.casefold() not in ALLOWED_ARTIFACT_SUFFIXES or not path.is_file():
        raise FileNotFoundError(f"artifact not available: {relative}")
    return path


def _stage_payload(run_dir: Path, stage: str, title: str) -> dict[str, Any]:
    resources = _read_json_if_present(run_dir / stage / "resources.json", {})
    return {
        "id": stage,
        "title": title,
        "resource_count": len(resources.get("resources", [])),
        "resources": resources.get("resources", []),
    }


def summarize_run(run_dir: Path) -> dict[str, Any]:
    certificate = _read_json_if_present(run_dir / "completion_certificate.json", {})
    completion_record = _read_json_if_present(run_dir / "completion_record.json", {})
    manifest = _read_json_if_present(run_dir / "bundle_manifest.json", {})
    audit = _read_json_if_present(run_dir / "stage_4_synthesis" / "audit.json", {})
    paper_plan = _read_json_if_present(
        run_dir / "stage_4_synthesis" / "paper_expansion_plan.json", {}
    )
    paper_audit = _read_json_if_present(
        run_dir / "stage_4_synthesis" / "paper_expansion_audit.json", {}
    )
    lineage = _read_json_if_present(run_dir / "research_lineage.json", {})
    return {
        "name": run_dir.name,
        "path": str(run_dir),
        "source_root": manifest.get("source_root", ""),
        "track_id": certificate.get("track_id")
        or manifest.get("selected_track_id", ""),
        "completed_at": completion_record.get("completed_at")
        or certificate.get("completed_at", ""),
        "idea_status": certificate.get("idea_status", "unverifiable"),
        "idea_validated": bool(certificate.get("idea_validated", False)),
        "pilot_draft_generated": bool(
            certificate.get(
                "pilot_draft_generated", audit.get("pilot_draft_generated", False)
            )
        ),
        "paper_expansion_ready": bool(paper_plan.get("ready", False)),
        "full_manuscript_generated": bool(
            paper_audit.get("full_manuscript_generated", False)
        ),
        "manuscript_depth_passed": bool(
            paper_audit.get("manuscript_depth_passed", False)
        ),
        "paper_draft_ready": bool(paper_audit.get("paper_draft_ready", False)),
        "publication_ready": bool(
            completion_record.get("readiness", {}).get(
                "publication_ready", paper_audit.get("publication_ready", False)
            )
        ),
        "project_id": completion_record.get("project_id"),
        "study_id": completion_record.get("study_id"),
        "audit_passed": bool(audit.get("passed", False)),
        "lineage": lineage,
    }


def list_bundle_runs(runs_root: str | Path = DEFAULT_RUNS_ROOT) -> list[dict[str, Any]]:
    root = Path(runs_root).resolve()
    if not root.is_dir():
        return []
    runs = [
        summarize_run(path)
        for path in root.iterdir()
        if path.is_dir() and (path / "completion_certificate.json").is_file()
    ]
    runs.sort(
        key=lambda item: (item.get("completed_at", ""), item["name"]), reverse=True
    )
    return runs


def load_run_detail(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    summary = summarize_run(root)
    inspection = _read_json_if_present(root / "inspection.json", {})
    scope = _read_json_if_present(
        root / "stage_1_discovery" / "scope_contract.json", {}
    )
    protocol = _read_json_if_present(
        root / "stage_2_protocol" / "protocol_lock.json", {}
    )
    verdict = _read_json_if_present(
        root / "stage_3_experimentation" / "idea_verdict.json", {}
    )
    audit = _read_json_if_present(root / "stage_4_synthesis" / "audit.json", {})
    manuscript_depth = _read_json_if_present(
        root / "stage_4_synthesis" / "manuscript_depth.json", {}
    )
    paper_plan = _read_json_if_present(
        root / "stage_4_synthesis" / "paper_expansion_plan.json", {}
    )
    paper_audit = _read_json_if_present(
        root / "stage_4_synthesis" / "paper_expansion_audit.json", {}
    )
    full_manuscript_depth = _read_json_if_present(
        root / "stage_4_synthesis" / "full_manuscript_depth.json", {}
    )
    submission_genre = _read_json_if_present(
        root / "stage_4_synthesis" / "submission_genre.json", {}
    )
    evidence_claim_map = _read_json_if_present(
        root / "stage_4_synthesis" / "evidence_claim_map.json", {}
    )
    paper_outline = _read_json_if_present(
        root / "stage_4_synthesis" / "paper_outline.json", {}
    )
    outline_review = _read_json_if_present(
        root / "stage_4_synthesis" / "paper_outline_review.json", {}
    )
    draft_review = _read_json_if_present(
        root / "stage_4_synthesis" / "paper_draft_review.json", {}
    )
    paper_artifacts = _read_json_if_present(
        root / "stage_4_synthesis" / "paper_artifact_manifest.json", {}
    )
    prose_polish = _read_json_if_present(
        root / "stage_4_synthesis" / "paper_prose_polish_trace.json", {}
    )
    claims = _read_json_if_present(root / "stage_4_synthesis" / "claims.json", {})
    claim_discovery = _read_json_if_present(
        root / "stage_1_discovery" / "claim_discovery.json", {}
    )
    workflow_pointer = _read_json_if_present(root / "workflow_v2.json", {})
    workflow = None
    if workflow_pointer.get("repository_root") and workflow_pointer.get("study_id"):
        try:
            from .workflow_domain import WorkflowRepository

            workflow = WorkflowRepository(
                str(workflow_pointer["repository_root"])
            ).snapshot(str(workflow_pointer["study_id"]))
        except (OSError, ValueError):
            workflow = None
    return {
        **summary,
        "inspection": inspection,
        "scope": scope,
        "protocol": protocol,
        "verdict": verdict,
        "audit": audit,
        "manuscript_depth": manuscript_depth,
        "paper_expansion_plan": paper_plan,
        "paper_expansion_audit": paper_audit,
        "full_manuscript_depth": full_manuscript_depth,
        "submission_genre": submission_genre,
        "evidence_claim_map": evidence_claim_map,
        "paper_outline": paper_outline,
        "paper_outline_review": outline_review,
        "paper_draft_review": draft_review,
        "paper_artifact_manifest": paper_artifacts,
        "paper_prose_polish_trace": prose_polish,
        "claims": claims.get("claims", []),
        "claim_discovery": claim_discovery,
        "workflow": workflow,
        "manuscript": _read_text_if_present(
            root / "stage_4_synthesis" / "manuscript.md"
        ),
        "full_manuscript": _read_text_if_present(
            root / "stage_4_synthesis" / "full_manuscript.md"
        ),
        "stages": [
            _stage_payload(root, "stage_1_discovery", "发现与收敛"),
            _stage_payload(root, "stage_2_protocol", "协议与基线"),
            _stage_payload(root, "stage_3_experimentation", "实验与判定"),
            _stage_payload(root, "stage_4_synthesis", "论文与审计"),
        ],
    }


def bootstrap_payload(
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    idea_root: str | Path = DEFAULT_IDEA_ROOT,
) -> dict[str, Any]:
    runs = list_bundle_runs(runs_root)
    latest = None
    if runs:
        latest = load_run_detail(Path(runs[0]["path"]))
    return {
        "product": "Research Forge",
        "default_source": _default_source(),
        "runs_root": str(Path(runs_root).resolve()),
        "idea_root": str(Path(idea_root).resolve()),
        "recent_runs": runs[:12],
        "latest_run": latest,
    }


def initialize_idea_research(
    idea: str,
    *,
    title: str = "",
    idea_root: str | Path = DEFAULT_IDEA_ROOT,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Create a durable Stage 1 intake for the idea-to-paper workflow.

    This starts the real four-stage project without pretending that literature,
    experiment, or publication gates have already passed.
    """

    cleaned_idea = idea.strip()
    if len(cleaned_idea) < 12:
        raise ValueError("研究想法至少需要 12 个字符，以便形成可收敛的问题边界")
    cleaned_title = (
        title.strip() or cleaned_idea[:28].rstrip("，。；;,. ") or "未命名研究"
    )
    slug = (
        f"idea-{task_id.removeprefix('task-')}"
        if task_id
        else f"idea-{uuid.uuid4().hex[:10]}"
    )
    project_path = Path(idea_root).expanduser().resolve() / slug
    intake_path = project_path / "web_intake.json"
    if task_id and intake_path.is_file():
        return {
            **read_json(intake_path),
            "project_path": str(project_path),
            "message": "端到端研究任务已创建，并进入发现与收敛阶段。",
        }
    if task_id and (project_path / "project.json").is_file():
        project = project_path
    else:
        project = create_project(
            cleaned_title,
            cleaned_idea,
            slug=slug,
            root=idea_root,
        )
    from .workflow_domain import (
        EntryMode,
        ExecutorType,
        GateType,
        Phase,
        WorkflowRepository,
        stable_id,
    )

    workflow_repository = WorkflowRepository(
        Path(idea_root).expanduser().resolve() / ".workflow-v2"
    )
    workflow_project = workflow_repository.create_project(
        cleaned_title,
        source_root=str(project.resolve()),
        project_id=stable_id("project", str(project.resolve())),
    )
    workflow_study = workflow_repository.create_study(
        workflow_project.project_id,
        cleaned_title,
        entry_mode=EntryMode.IDEA_TO_PAPER,
        research_type="computational",
        study_id=stable_id("study", workflow_project.project_id, slug),
    )
    if not workflow_repository.list_steps(workflow_study.study_id):
        workflow_repository.add_step(
            workflow_study.study_id,
            "scope_drafting",
            Phase.DISCOVERY,
            ExecutorType.CODEX,
            expected_output="source-bound scope contract draft",
        )
        workflow_repository.create_gate(
            workflow_study.study_id,
            GateType.SCOPE_APPROVAL,
            "scope_contract",
            f"{workflow_study.study_id}:scope",
        )
    intake = {
        "workflow": "idea-to-paper",
        "requested_at": utc_now(),
        "title": cleaned_title,
        "idea": cleaned_idea,
        "status": "initialized",
        "current_stage": "stage_1_discovery",
        "next_gate": "真实文献检索与研究边界确认",
        "project_id": workflow_project.project_id,
        "study_id": workflow_study.study_id,
        "workflow_repository": str(workflow_repository.root),
        "stages": [
            {"id": "stage_1_discovery", "title": "发现与收敛", "status": "ready"},
            {"id": "stage_2_protocol", "title": "协议与基线", "status": "pending"},
            {
                "id": "stage_3_experimentation",
                "title": "实验与判定",
                "status": "pending",
            },
            {"id": "stage_4_synthesis", "title": "论文与审计", "status": "pending"},
        ],
    }
    write_json_atomic(project / "web_intake.json", intake)
    return {
        **intake,
        "project_path": str(project.resolve()),
        "message": "端到端研究任务已创建，并进入发现与收敛阶段。",
    }


class ResearchForgeWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        runs_root: Path,
        idea_root: Path,
        task_root: Path,
        workflow_root: Path,
        static_root: Path,
    ) -> None:
        self.runs_root = runs_root.resolve()
        self.idea_root = idea_root.resolve()
        self.task_root = task_root.resolve()
        self.workflow_root = workflow_root.resolve()
        self.static_root = static_root.resolve()
        super().__init__(address, ResearchForgeRequestHandler)


class ResearchForgeRequestHandler(BaseHTTPRequestHandler):
    server: ResearchForgeWebServer

    def log_message(self, format: str, *args: object) -> None:
        print(f"[research-forge-web] {self.address_string()} {format % args}")

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(_clean_json(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, exc: Exception) -> None:
        status = (
            HTTPStatus.NOT_FOUND
            if isinstance(exc, FileNotFoundError)
            else HTTPStatus.BAD_REQUEST
            if isinstance(exc, (ValueError, json.JSONDecodeError))
            else HTTPStatus.INTERNAL_SERVER_ERROR
        )
        self._send_json({"error": str(exc), "type": type(exc).__name__}, status)

    def _request_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length)
        if length <= 0 or length > 1_000_000:
            raise ValueError("request body must be a JSON object smaller than 1 MB")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _serve_static(self, request_path: str) -> None:
        if not self.server.static_root.is_dir():
            raise FileNotFoundError(
                "frontend build is missing; run the UI build before starting the web app"
            )
        relative = unquote(request_path).lstrip("/") or "index.html"
        candidate = (self.server.static_root / relative).resolve()
        if (
            self.server.static_root not in candidate.parents
            and candidate != self.server.static_root
        ):
            raise FileNotFoundError("static asset not found")
        if not candidate.is_file():
            candidate = self.server.static_root / "index.html"
        content = candidate.read_bytes()
        mime_type = (
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        )
        if mime_type.startswith("text/") or mime_type in {
            "application/javascript",
            "application/json",
        }:
            mime_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            path_parts = [item for item in parsed.path.split("/") if item]
            if parsed.path == "/retrieval/readiness":
                from .retrieval.interfaces.readiness import ReadinessService
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                self._send_json(
                    ReadinessService(
                        gateway.repository, gateway.providers
                    ).evaluate().model_dump(mode="json")
                )
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "projects"
                and path_parts[2] == "retrieval-policy"
            ):
                from .retrieval.interfaces.service import RetrievalGateway

                self._send_json(
                    RetrievalGateway(
                        str(self.server.workflow_root)
                    ).get_policy(path_parts[1]).model_dump(mode="json")
                )
                return
            if len(path_parts) >= 2 and path_parts[0] == "retrieval-runs":
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                run = gateway.repository.load_run(path_parts[1])
                if len(path_parts) == 3 and path_parts[2] == "coverage":
                    if not run.coverage_report_id:
                        raise FileNotFoundError("retrieval run has no coverage report")
                    self._send_json(
                        gateway.repository.load_coverage(
                            run.coverage_report_id
                        ).model_dump(mode="json")
                    )
                else:
                    self._send_json(run.model_dump(mode="json"))
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "studies"
                and path_parts[2] == "resources"
            ):
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                bindings = gateway.repository.list_bindings(path_parts[1])
                allowed = {item.resource_id for item in bindings}
                self._send_json(
                    {
                        "resources": [
                            item.model_dump(mode="json")
                            for item in gateway.repository.list_resources()
                            if item.resource_id in allowed
                        ],
                        "bindings": [
                            item.model_dump(mode="json") for item in bindings
                        ],
                    }
                )
                return
            if len(path_parts) >= 2 and path_parts[0] == "resources":
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                if len(path_parts) == 3 and path_parts[2] == "relations":
                    self._send_json(
                        {
                            "relations": [
                                item.model_dump(mode="json")
                                for item in gateway.repository.list_resource_relations(
                                    path_parts[1]
                                )
                            ]
                        }
                    )
                else:
                    self._send_json(
                        gateway.repository.load_resource(path_parts[1]).model_dump(
                            mode="json"
                        )
                    )
                return
            if len(path_parts) == 2 and path_parts[0] == "institution-sessions":
                from .retrieval.institution import InstitutionSessionBroker

                query = parse_qs(parsed.query)
                self._send_json(
                    InstitutionSessionBroker(self.server.workflow_root)
                    .load(
                        path_parts[1],
                        owner_user_id=query.get("owner_user_id", [""])[0],
                    )
                    .model_dump(mode="json")
                )
                return
            if parsed.path == "/api/health":
                self._send_json({"ok": True, "service": "research-forge-web"})
                return
            if parsed.path == "/api/bootstrap":
                self._send_json(
                    bootstrap_payload(self.server.runs_root, self.server.idea_root)
                )
                return
            if parsed.path == "/api/runs":
                self._send_json({"runs": list_bundle_runs(self.server.runs_root)})
                return
            if parsed.path == "/api/tasks":
                from .workflow_tasks import create_workflow_orchestrator

                orchestrator = create_workflow_orchestrator(self.server.task_root)
                self._send_json(
                    {
                        "tasks": [
                            item.model_dump(mode="json") for item in orchestrator.list()
                        ]
                    }
                )
                return
            if parsed.path == "/api/projects":
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                self._send_json(
                    {
                        "projects": [
                            item.model_dump(mode="json")
                            for item in repository.list_projects()
                        ]
                    }
                )
                return
            if parsed.path == "/api/step-definitions":
                from .workflow_domain import WorkflowRepository

                self._send_json(
                    {
                        "step_definitions": [
                            item.model_dump(mode="json")
                            for item in WorkflowRepository(
                                self.server.workflow_root
                            ).list_step_definitions()
                        ]
                    }
                )
                return
            if parsed.path == "/api/studies":
                from .workflow_domain import WorkflowRepository

                query = parse_qs(parsed.query)
                project_id = query.get("project_id", [""])[0] or None
                repository = WorkflowRepository(self.server.workflow_root)
                self._send_json(
                    {
                        "studies": [
                            item.model_dump(mode="json")
                            for item in repository.list_studies(project_id)
                        ]
                    }
                )
                return
            if parsed.path == "/api/study":
                from .workflow_domain import WorkflowRepository

                query = parse_qs(parsed.query)
                study_id = query.get("id", [""])[0]
                self._send_json(
                    WorkflowRepository(self.server.workflow_root).snapshot(study_id)
                )
                return
            if parsed.path.startswith("/api/retrieval/"):
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                query = parse_qs(parsed.query)
                if parsed.path == "/api/retrieval/policy":
                    self._send_json(
                        gateway.get_policy(query.get("project_id", [""])[0]).model_dump(
                            mode="json"
                        )
                    )
                    return
                if parsed.path == "/api/retrieval/readiness":
                    from .retrieval.interfaces.readiness import ReadinessService

                    refresh = query.get("refresh", ["false"])[0].casefold() == "true"
                    service = ReadinessService(
                        gateway.repository, gateway.providers
                    )
                    report = (
                        service.evaluate()
                        if refresh
                        else gateway.repository.latest_readiness_report()
                        or service.evaluate()
                    )
                    self._send_json(report.model_dump(mode="json"))
                    return
                if parsed.path == "/api/retrieval/overview":
                    study_id = query.get("study_id", [""])[0]
                    project_id = query.get("project_id", [""])[0]
                    if not study_id or not project_id:
                        raise ValueError(
                            "project_id and study_id are required"
                        )
                    runs = gateway.repository.list_runs(study_id)
                    run_rows = []
                    for run in runs:
                        request = gateway.repository.load_request(run.request_id)
                        plan = gateway.repository.load_query_plan(
                            request.query_plan_id
                        )
                        coverage = (
                            gateway.repository.load_coverage(
                                run.coverage_report_id
                            )
                            if run.coverage_report_id
                            else None
                        )
                        run_rows.append(
                            {
                                "run": run.model_dump(mode="json"),
                                "request": request.model_dump(mode="json"),
                                "query_plan": plan.model_dump(mode="json"),
                                "coverage": (
                                    coverage.model_dump(mode="json")
                                    if coverage
                                    else None
                                ),
                            }
                        )
                    bindings = gateway.repository.list_bindings(study_id)
                    resource_ids = {item.resource_id for item in bindings}
                    self._send_json(
                        {
                            "policy": gateway.get_policy(
                                project_id
                            ).model_dump(mode="json"),
                            "runs": run_rows,
                            "resources": [
                                item.model_dump(mode="json")
                                for item in gateway.repository.list_resources()
                                if item.resource_id in resource_ids
                            ],
                            "bindings": [
                                item.model_dump(mode="json")
                                for item in bindings
                            ],
                            "resource_sets": [
                                item.model_dump(mode="json")
                                for item in gateway.repository.list_resource_sets(
                                    study_id
                                )
                            ],
                        }
                    )
                    return
                if parsed.path == "/api/retrieval/status":
                    self._send_json(
                        gateway.repository.load_run(
                            query.get("run_id", [""])[0]
                        ).model_dump(mode="json")
                    )
                    return
                if parsed.path == "/api/retrieval/resources":
                    study_id = query.get("study_id", [""])[0] or None
                    bindings = gateway.repository.list_bindings(study_id)
                    allowed = {item.resource_id for item in bindings}
                    resources = gateway.repository.list_resources()
                    if study_id:
                        resources = [
                            item for item in resources if item.resource_id in allowed
                        ]
                    self._send_json(
                        {
                            "resources": [
                                item.model_dump(mode="json") for item in resources
                            ],
                            "bindings": [
                                item.model_dump(mode="json") for item in bindings
                            ],
                        }
                    )
                    return
                if parsed.path == "/api/retrieval/snapshots":
                    resource_id = query.get("resource_id", [""])[0] or None
                    self._send_json(
                        {
                            "snapshots": [
                                item.model_dump(mode="json")
                                for item in gateway.repository.list_snapshots(
                                    resource_id
                                )
                            ]
                        }
                    )
                    return
                if parsed.path == "/api/retrieval/bindings":
                    study_id = query.get("study_id", [""])[0] or None
                    self._send_json(
                        {
                            "bindings": [
                                item.model_dump(mode="json")
                                for item in gateway.repository.list_bindings(study_id)
                            ]
                        }
                    )
                    return
                if parsed.path == "/api/retrieval/coverage":
                    self._send_json(
                        gateway.repository.load_coverage(
                            query.get("id", [""])[0]
                        ).model_dump(mode="json")
                    )
                    return
                if parsed.path == "/api/retrieval/resource-sets":
                    study_id = query.get("study_id", [""])[0] or None
                    self._send_json(
                        {
                            "resource_sets": [
                                item.model_dump(mode="json")
                                for item in gateway.repository.list_resource_sets(
                                    study_id
                                )
                            ]
                        }
                    )
                    return
            if parsed.path == "/api/task":
                from .workflow_tasks import create_workflow_orchestrator

                query = parse_qs(parsed.query)
                task_id = query.get("id", [""])[0]
                record = create_workflow_orchestrator(self.server.task_root).load(
                    task_id
                )
                self._send_json(record.model_dump(mode="json"))
                return
            if parsed.path == "/api/task/events":
                query = parse_qs(parsed.query)
                task_id = query.get("id", [""])[0]
                events = [
                    item
                    for item in load_jsonl(self.server.task_root / "task_events.jsonl")
                    if item.get("task_id") == task_id
                ]
                self._send_json({"events": events[-200:]})
                return
            if parsed.path == "/api/remediation":
                from .remediation import load_remediation_plan

                query = parse_qs(parsed.query)
                run_name = query.get("name", [""])[0]
                plan_id = query.get("plan_id", [""])[0] or None
                plan = load_remediation_plan(
                    self.server.runs_root / ".remediation", run_name, plan_id
                )
                self._send_json(plan.model_dump(mode="json"))
                return
            if parsed.path == "/api/run":
                query = parse_qs(parsed.query)
                name = query.get("name", [""])[0]
                self._send_json(
                    load_run_detail(_safe_run_dir(self.server.runs_root, name))
                )
                return
            if parsed.path == "/api/artifact":
                query = parse_qs(parsed.query)
                run_dir = _safe_run_dir(
                    self.server.runs_root, query.get("name", [""])[0]
                )
                artifact = _safe_artifact_path(run_dir, query.get("path", [""])[0])
                self._send_json(
                    {
                        "name": artifact.name,
                        "path": str(artifact.relative_to(run_dir)).replace("\\", "/"),
                        "content": _read_text_if_present(artifact),
                    }
                )
                return
            self._serve_static(parsed.path)
        except Exception as exc:  # pragma: no cover - exercised through HTTP tests
            self._send_error_json(exc)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._request_json()
            parts = [item for item in parsed.path.split("/") if item]
            if (
                len(parts) == 3
                and parts[0] == "projects"
                and parts[2] == "retrieval-policy"
            ):
                from .retrieval.interfaces.service import RetrievalGateway
                from .retrieval.policy.engine import RetrievalNetworkPolicy

                policy = RetrievalNetworkPolicy.model_validate(
                    payload.get("policy") or payload
                )
                self._send_json(
                    RetrievalGateway(str(self.server.workflow_root))
                    .set_policy(parts[1], policy)
                    .model_dump(mode="json")
                )
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(exc)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            parts = [item for item in parsed.path.split("/") if item]
            if len(parts) == 2 and parts[0] == "institution-sessions":
                from .retrieval.institution import InstitutionSessionBroker

                query = parse_qs(parsed.query)
                session = InstitutionSessionBroker(
                    self.server.workflow_root
                ).revoke(
                    parts[1],
                    owner_user_id=query.get("owner_user_id", [""])[0],
                )
                self._send_json(session.model_dump(mode="json"))
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(exc)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._request_json()
            path_parts = [item for item in parsed.path.split("/") if item]
            if (
                len(path_parts) == 3
                and path_parts[0] == "studies"
                and path_parts[2] == "retrieval-dags"
            ):
                from .retrieval.workflow import (
                    append_external_research_dag,
                    append_external_research_loop,
                )
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    stage_one_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                stage = str(payload.get("stage", "all"))
                dependencies = [
                    str(item) for item in payload.get("depends_on", [])
                ]
                if stage == "all":
                    steps = append_external_research_loop(
                        repository,
                        path_parts[1],
                        depends_on=dependencies,
                        run_key=str(payload.get("run_key", "v1")),
                        include_repair=bool(payload.get("include_repair", False)),
                    )
                else:
                    steps = append_external_research_dag(
                        repository,
                        path_parts[1],
                        stage,
                        depends_on=dependencies,
                        run_key=str(payload.get("run_key", "v1")),
                    )
                snapshot = (
                    PersistentDAGScheduler(
                        repository,
                        stage_one_handlers(),
                        recover_interrupted=True,
                    ).run(path_parts[1])
                    if bool(payload.get("run", True))
                    else repository.snapshot(path_parts[1])
                )
                self._send_json(
                    {
                        "study_id": path_parts[1],
                        "created_step_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "snapshot": snapshot,
                    },
                    HTTPStatus.CREATED,
                )
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "studies"
                and path_parts[2] == "retrieval-runs"
            ):
                from .retrieval.domain.models import (
                    ContractRef,
                    ResourceType,
                    RetrievalBudget,
                    RetrievalPhase,
                )
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                request = gateway.plan(
                    project_id=str(payload.get("project_id", "")),
                    study_id=path_parts[1],
                    phase=RetrievalPhase(str(payload.get("phase", ""))),
                    step_instance_id=str(payload.get("step_instance_id", "")),
                    purpose=str(payload.get("purpose", "")),
                    queries=[str(item) for item in payload.get("queries", [])],
                    providers=[str(item) for item in payload.get("providers", [])],
                    resource_types=[
                        ResourceType(str(item))
                        for item in payload.get("resource_types", [])
                    ],
                    usage_role=str(payload.get("usage_role", "")),
                    budget=RetrievalBudget.model_validate(
                        payload.get("budget") or {}
                    ),
                    idempotency_key=str(payload.get("idempotency_key", "")),
                    contract_refs=[
                        ContractRef.model_validate(item)
                        for item in payload.get("contract_refs", [])
                    ],
                    freshness=_retrieval_freshness(payload.get("freshness")),
                    allowed_domains=[
                        str(item) for item in payload.get("allowed_domains", [])
                    ],
                    blocked_domains=[
                        str(item) for item in payload.get("blocked_domains", [])
                    ],
                )
                self._send_json(
                    gateway.run(request.request_id).model_dump(),
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/institution-sessions":
                from .retrieval.institution import InstitutionSessionBroker

                broker = InstitutionSessionBroker(self.server.workflow_root)
                session = broker.create(
                    owner_user_id=str(payload.get("owner_user_id", "")),
                    project_id=str(payload.get("project_id", "")),
                    study_id=str(payload.get("study_id", "")),
                    institution_id=str(payload.get("institution_id", "")),
                    target_url=str(payload.get("target_url", "")),
                    extra=payload,
                )
                response = session.model_dump(mode="json")
                if bool(payload.get("open_browser", False)):
                    session, launch = broker.open_browser(
                        session.session_id,
                        owner_user_id=session.owner_user_id,
                    )
                    response = {
                        "session": session.model_dump(mode="json"),
                        "browser": {
                            "process_id": launch.process_id,
                            "browser": launch.browser,
                            "storage_security": launch.storage_security,
                        },
                    }
                self._send_json(response, HTTPStatus.CREATED)
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "institution-sessions"
                and path_parts[2] == "open"
            ):
                from .retrieval.institution import InstitutionSessionBroker

                session, launch = InstitutionSessionBroker(
                    self.server.workflow_root
                ).open_browser(
                    path_parts[1],
                    owner_user_id=str(payload.get("owner_user_id", "")),
                )
                self._send_json(
                    {
                        "session": session.model_dump(mode="json"),
                        "browser": {
                            "process_id": launch.process_id,
                            "browser": launch.browser,
                            "storage_security": launch.storage_security,
                        },
                    }
                )
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "institution-sessions"
                and path_parts[2] == "confirm-authenticated"
            ):
                from .retrieval.institution import InstitutionSessionBroker

                session = InstitutionSessionBroker(
                    self.server.workflow_root
                ).confirm_authenticated(
                    path_parts[1],
                    owner_user_id=str(payload.get("owner_user_id", "")),
                    user_confirmation=bool(payload.get("user_confirmation", False)),
                    duration_minutes=int(payload.get("duration_minutes", 60)),
                )
                self._send_json(session.model_dump(mode="json"))
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "institution-sessions"
                and path_parts[2] == "documents"
            ):
                from .retrieval.institution import InstitutionSessionBroker

                result = InstitutionSessionBroker(
                    self.server.workflow_root
                ).register_authorized_document(
                    path_parts[1],
                    owner_user_id=str(payload.get("owner_user_id", "")),
                    file_path=str(payload.get("file_path", "")),
                    step_instance_id=str(payload.get("step_instance_id", "")),
                    model_processing_approved=bool(
                        payload.get("model_processing_approved", False)
                    ),
                    resource_id=(
                        str(payload["resource_id"])
                        if payload.get("resource_id")
                        else None
                    ),
                )
                self._send_json(result, HTTPStatus.CREATED)
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "institution-sessions"
                and path_parts[2] == "reauthenticate"
            ):
                from .retrieval.institution import InstitutionSessionBroker

                session = InstitutionSessionBroker(
                    self.server.workflow_root
                ).reauthenticate(
                    path_parts[1],
                    owner_user_id=str(payload.get("owner_user_id", "")),
                )
                self._send_json(session.model_dump(mode="json"))
                return
            if parsed.path == "/corpora":
                from .retrieval.domain.external_models import CorpusDocument
                from .retrieval.domain.models import RetrievalPhase
                from .retrieval.evidence import PaperQAEvidenceService
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                corpus = PaperQAEvidenceService(gateway.repository).build_corpus(
                    study_id=str(payload.get("study_id", "")),
                    phase=RetrievalPhase(str(payload.get("phase", ""))),
                    documents=[
                        CorpusDocument.model_validate(item)
                        for item in payload.get("documents", [])
                    ],
                    parser_version=str(payload.get("parser_version", "")),
                    embedding_model_hash=str(
                        payload.get("embedding_model_hash", "")
                    ),
                    llm_config_hash=str(payload.get("llm_config_hash", "")),
                )
                self._send_json(
                    corpus.model_dump(mode="json"), HTTPStatus.CREATED
                )
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "corpora"
                and path_parts[2] == "index"
            ):
                from .retrieval.evidence import PaperQAEvidenceService
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                corpus = PaperQAEvidenceService(gateway.repository).index(
                    path_parts[1]
                )
                self._send_json(corpus.model_dump(mode="json"))
                return
            if (
                len(path_parts) == 3
                and path_parts[0] == "corpora"
                and path_parts[2] == "query"
            ):
                from .retrieval.evidence import PaperQAEvidenceService
                from .retrieval.interfaces.service import RetrievalGateway

                gateway = RetrievalGateway(str(self.server.workflow_root))
                service = PaperQAEvidenceService(gateway.repository)
                if isinstance(payload.get("questions"), list):
                    results = service.query_evidence_batch(
                        path_parts[1],
                        [
                            {
                                "question_id": str(
                                    item.get("question_id") or ""
                                ),
                                "question": str(item.get("question") or ""),
                            }
                            for item in payload["questions"]
                            if isinstance(item, dict)
                        ],
                    )
                    self._send_json(
                        {
                            "answers": [
                                item.model_dump(mode="json") for item in results
                            ]
                        }
                    )
                else:
                    result = service.ask(
                        path_parts[1], str(payload.get("question", ""))
                    )
                    self._send_json(result.model_dump(mode="json"))
                return
            if parsed.path.startswith("/api/retrieval/"):
                from .retrieval.domain.models import (
                    ContractRef,
                    ResourceType,
                    RetrievalBudget,
                    RetrievalPhase,
                )
                from .retrieval.interfaces.service import RetrievalGateway
                from .retrieval.policy.engine import RetrievalNetworkPolicy

                gateway = RetrievalGateway(str(self.server.workflow_root))
                if parsed.path == "/api/retrieval/policy":
                    policy_payload = payload.get("policy") or {}
                    if not isinstance(policy_payload, dict):
                        raise ValueError("policy must be an object")
                    policy = RetrievalNetworkPolicy.model_validate(policy_payload)
                    self._send_json(
                        gateway.set_policy(
                            str(payload.get("project_id", "")).strip(),
                            policy,
                        ).model_dump(mode="json")
                    )
                    return
                if parsed.path == "/api/retrieval/plan":
                    request = gateway.plan(
                        project_id=str(payload.get("project_id", "")).strip(),
                        study_id=str(payload.get("study_id", "")).strip(),
                        phase=RetrievalPhase(str(payload.get("phase", ""))),
                        step_instance_id=str(
                            payload.get("step_instance_id", "")
                        ).strip(),
                        purpose=str(payload.get("purpose", "")).strip(),
                        queries=[str(item) for item in payload.get("queries", [])],
                        providers=[str(item) for item in payload.get("providers", [])],
                        resource_types=[
                            ResourceType(str(item))
                            for item in payload.get("resource_types", [])
                        ],
                        usage_role=str(payload.get("usage_role", "")).strip(),
                        budget=RetrievalBudget.model_validate(
                            payload.get("budget") or {}
                        ),
                        idempotency_key=str(payload.get("idempotency_key", "")).strip(),
                        contract_refs=[
                            ContractRef.model_validate(item)
                            for item in payload.get("contract_refs", [])
                        ],
                        internal_identifiers=[
                            str(item)
                            for item in payload.get("internal_identifiers", [])
                        ],
                        research_need=str(payload.get("research_need", "")).strip()
                        or None,
                        freshness=_retrieval_freshness(payload.get("freshness")),
                        allowed_domains=[
                            str(item) for item in payload.get("allowed_domains", [])
                        ],
                        blocked_domains=[
                            str(item) for item in payload.get("blocked_domains", [])
                        ],
                        require_search_execution=bool(
                            payload.get("require_search_execution", True)
                        ),
                    )
                    self._send_json(request.model_dump(mode="json"), HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/retrieval/run":
                    self._send_json(
                        gateway.run(
                            str(payload.get("request_id", "")).strip()
                        ).model_dump()
                    )
                    return
                if parsed.path == "/api/retrieval/freeze":
                    self._send_json(
                        gateway.freeze_resource_set(
                            str(payload.get("resource_set_id", "")).strip()
                        ).model_dump(mode="json")
                    )
                    return
                if parsed.path == "/api/retrieval/promote":
                    self._send_json(
                        gateway.promote_binding(
                            str(payload.get("binding_id", "")).strip(),
                            target_phase=RetrievalPhase(
                                str(payload.get("target_phase", ""))
                            ),
                            step_instance_id=str(
                                payload.get("step_instance_id", "")
                            ).strip(),
                            purpose=str(payload.get("purpose", "")).strip(),
                            usage_role=str(payload.get("usage_role", "")).strip(),
                            target_type=str(payload.get("target_type", "")).strip(),
                            target_id=str(payload.get("target_id", "")).strip(),
                            target_field=str(payload.get("target_field", "")).strip(),
                            contract_refs=[
                                ContractRef.model_validate(item)
                                for item in payload.get("contract_refs", [])
                            ],
                        ).model_dump(mode="json")
                    )
                    return
                if parsed.path == "/api/retrieval/retry":
                    self._send_json(
                        gateway.retry(
                            str(payload.get("run_id", "")).strip()
                        ).model_dump()
                    )
                    return
            if parsed.path == "/api/projects/create":
                from .workflow_domain import NetworkPolicy, WorkflowRepository

                policy_payload = payload.get("network_policy") or {}
                if not isinstance(policy_payload, dict):
                    raise ValueError("network_policy must be a JSON object")
                project = WorkflowRepository(self.server.workflow_root).create_project(
                    str(payload.get("title", "")).strip(),
                    source_root=str(payload.get("source_root", "")).strip() or None,
                    project_id=str(payload.get("project_id", "")).strip() or None,
                    network_policy=NetworkPolicy.model_validate(policy_payload)
                    if policy_payload
                    else None,
                )
                self._send_json(project.model_dump(mode="json"), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/create":
                from .workflow_domain import EntryMode, WorkflowRepository

                study = WorkflowRepository(self.server.workflow_root).create_study(
                    str(payload.get("project_id", "")).strip(),
                    str(payload.get("title", "")).strip(),
                    entry_mode=EntryMode(
                        str(payload.get("entry_mode", "project_to_paper"))
                    ),
                    research_type=str(
                        payload.get("research_type", "computational")
                    ).strip(),
                    predecessor_study_id=str(
                        payload.get("predecessor_study_id", "")
                    ).strip()
                    or None,
                )
                self._send_json(study.model_dump(mode="json"), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/discovery/select":
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import approve_discovery_direction

                result = approve_discovery_direction(
                    WorkflowRepository(self.server.workflow_root),
                    str(payload.get("study_id", "")).strip(),
                    str(payload.get("direction_id", "")).strip(),
                    decided_by=str(
                        payload.get("decided_by", "project_owner")
                    ).strip(),
                    reason=str(payload.get("reason", "")).strip() or None,
                )
                self._send_json(result)
                return
            if parsed.path == "/api/studies/gates/decide":
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    stage_one_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                gate = repository.decide_gate(
                    study_id,
                    str(payload.get("gate_id", "")).strip(),
                    approve=bool(payload.get("approve", False)),
                    decided_by=str(payload.get("decided_by", "project_owner")).strip(),
                    reason=str(payload.get("reason", "")).strip() or None,
                )
                workflow = PersistentDAGScheduler(repository, stage_one_handlers()).run(
                    study_id
                )
                self._send_json({**gate.model_dump(mode="json"), "workflow": workflow})
                return
            if parsed.path == "/api/studies/gates/create":
                from .workflow_domain import (
                    GateStatus,
                    GateType,
                    WorkflowRepository,
                )

                gate = WorkflowRepository(self.server.workflow_root).create_gate(
                    str(payload.get("study_id", "")).strip(),
                    GateType(str(payload.get("gate_type", ""))),
                    str(payload.get("subject_type", "")).strip(),
                    str(payload.get("subject_id", "")).strip(),
                    subject_version=(
                        int(payload["subject_version"])
                        if payload.get("subject_version") is not None
                        else None
                    ),
                    status=GateStatus(str(payload.get("status", "awaiting_user"))),
                )
                self._send_json(gate.model_dump(mode="json"), HTTPStatus.CREATED)
                return
            if parsed.path in {
                "/api/studies/scope-contract",
                "/api/studies/research-contract",
                "/api/studies/adjudications",
                "/api/studies/nli-alerts",
                "/api/studies/literature-set",
                "/api/studies/research-runs",
                "/api/studies/baseline-verification",
            }:
                from .workflow_domain import (
                    AdjudicationRecord,
                    BaselineVerificationContract,
                    LiteratureSetVersion,
                    NLIRiskAlert,
                    ResearchContractVersion,
                    ResearchRun,
                    ScopeContractVersion,
                    WorkflowRepository,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                handlers = {
                    "/api/studies/scope-contract": (
                        ScopeContractVersion,
                        repository.save_scope_contract,
                    ),
                    "/api/studies/research-contract": (
                        ResearchContractVersion,
                        repository.save_research_contract,
                    ),
                    "/api/studies/adjudications": (
                        AdjudicationRecord,
                        repository.save_adjudication,
                    ),
                    "/api/studies/nli-alerts": (
                        NLIRiskAlert,
                        repository.save_nli_alert,
                    ),
                    "/api/studies/literature-set": (
                        LiteratureSetVersion,
                        repository.save_literature_set,
                    ),
                    "/api/studies/research-runs": (
                        ResearchRun,
                        repository.save_research_run,
                    ),
                    "/api/studies/baseline-verification": (
                        BaselineVerificationContract,
                        repository.save_baseline_verification,
                    ),
                }
                model_type, save = handlers[parsed.path]
                record = save(model_type.model_validate(payload))
                response = record.model_dump(mode="json")
                if isinstance(record, BaselineVerificationContract):
                    response["baseline_verified"] = record.baseline_verified
                self._send_json(response, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/steps/create":
                from .workflow_domain import (
                    ExecutorType,
                    Phase,
                    WorkflowRepository,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                step = repository.add_step(
                    str(payload.get("study_id", "")).strip(),
                    str(payload.get("step_type", "")).strip(),
                    Phase(str(payload.get("phase", ""))),
                    ExecutorType(str(payload.get("executor_type", ""))),
                    depends_on=[str(item) for item in payload.get("depends_on", [])],
                    parent_step_id=str(payload.get("parent_step_id", "")).strip()
                    or None,
                    task_group=str(payload.get("task_group", "")).strip() or None,
                    expected_output=str(payload.get("expected_output", "")).strip()
                    or None,
                )
                self._send_json(step.model_dump(mode="json"), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/steps/update":
                from .workflow_domain import ExecutionStatus, WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                step = repository.update_step(
                    str(payload.get("study_id", "")).strip(),
                    str(payload.get("step_instance_id", "")).strip(),
                    ExecutionStatus(str(payload.get("status", ""))),
                    blocker=payload.get("blocker"),
                    input_artifact_ids=payload.get("input_artifact_ids"),
                    output_artifact_ids=payload.get("output_artifact_ids"),
                )
                self._send_json(step.model_dump(mode="json"))
                return
            if parsed.path == "/api/studies/repairs/propose":
                from .workflow_domain import (
                    DiagnosticOwner,
                    Phase,
                    WorkflowRepository,
                )

                repair = WorkflowRepository(self.server.workflow_root).propose_repair(
                    str(payload.get("study_id", "")).strip(),
                    diagnostic_owner=DiagnosticOwner(
                        str(payload.get("diagnostic_owner", "integrity_binding"))
                    ),
                    scientific_change=bool(payload.get("scientific_change", True)),
                    earliest_affected_phase=Phase(
                        str(payload.get("earliest_affected_phase", "experiment"))
                    ),
                    changed_artifact_ids=[
                        str(item) for item in payload.get("changed_artifact_ids", [])
                    ],
                    regression_checks=payload.get("regression_checks") or [],
                    predecessor_run_id=str(
                        payload.get("predecessor_run_id", "")
                    ).strip()
                    or None,
                    diagnostic_id=str(payload.get("diagnostic_id", "")).strip()
                    or None,
                    earliest_affected_step_type=str(
                        payload.get("earliest_affected_step_type", "")
                    ).strip()
                    or None,
                )
                self._send_json(repair.model_dump(mode="json"), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/discovery/auto-repair":
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import auto_repair_discovery_study

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                outcome = auto_repair_discovery_study(repository, study_id)
                self._send_json(
                    outcome
                    or {
                        "study_id": study_id,
                        "repair_required": False,
                        "workflow": repository.snapshot(study_id),
                    }
                )
                return
            if parsed.path == "/api/network/audit":
                from .workflow_domain import NetworkAuditEvent, WorkflowRepository

                event = WorkflowRepository(
                    self.server.workflow_root
                ).record_network_request(NetworkAuditEvent.model_validate(payload))
                self._send_json(event.model_dump(mode="json"), HTTPStatus.CREATED)
                return
            if parsed.path in {
                "/api/studies/pause",
                "/api/studies/resume",
                "/api/studies/cancel",
                "/api/studies/archive",
            }:
                from .workflow_domain import StudyLifecycle, WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                if parsed.path.endswith("/pause"):
                    study = repository.pause_study(study_id)
                elif parsed.path.endswith("/resume"):
                    study = repository.resume_study(study_id)
                    from .workflow_scheduler import (
                        PersistentDAGScheduler,
                        stage_one_handlers,
                    )

                    workflow = PersistentDAGScheduler(
                        repository,
                        stage_one_handlers(),
                        recover_interrupted=True,
                    ).run(study_id)
                    self._send_json(workflow)
                    return
                elif parsed.path.endswith("/cancel"):
                    study = repository.finish_study(study_id, StudyLifecycle.CANCELLED)
                else:
                    study = repository.finish_study(study_id, StudyLifecycle.ARCHIVED)
                self._send_json(study.model_dump(mode="json"))
                return
            if parsed.path in {"/api/studies/run", "/api/studies/retry-step"}:
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    stage_one_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                scheduler = PersistentDAGScheduler(repository, stage_one_handlers())
                if parsed.path.endswith("/retry-step"):
                    scheduler.retry_step(
                        study_id, str(payload.get("step_id", "")).strip()
                    )
                self._send_json(scheduler.run(study_id))
                return
            if parsed.path == "/api/studies/impact":
                from .workflow_domain import WorkflowRepository

                changed = payload.get("changed_artifact_ids") or []
                if not isinstance(changed, list):
                    raise ValueError("changed_artifact_ids must be a list")
                self._send_json(
                    WorkflowRepository(self.server.workflow_root).impact(
                        str(payload.get("study_id", "")).strip(),
                        [str(item) for item in changed],
                    )
                )
                return
            if parsed.path == "/api/studies/ai-review":
                from .workflow_domain import AIReviewStatus, WorkflowRepository

                assessment = WorkflowRepository(
                    self.server.workflow_root
                ).submit_ai_review(
                    str(payload.get("study_id", "")).strip(),
                    AIReviewStatus(str(payload.get("status", "pending"))),
                )
                self._send_json(assessment.model_dump(mode="json"))
                return
            if parsed.path == "/api/studies/publication-approval":
                from .workflow_domain import (
                    AuthorApprovalStatus,
                    WorkflowRepository,
                )

                approval, assessment = WorkflowRepository(
                    self.server.workflow_root
                ).submit_author_approval(
                    str(payload.get("study_id", "")).strip(),
                    AuthorApprovalStatus(str(payload.get("status", "pending"))),
                    decided_by=str(payload.get("decided_by", "project_owner")).strip(),
                    reason=str(payload.get("reason", "")).strip() or None,
                )
                self._send_json(
                    {
                        "approval": approval.model_dump(mode="json"),
                        "readiness": assessment.model_dump(mode="json"),
                    }
                )
                return
            if parsed.path == "/api/studies/migrate-run":
                from .workflow_migration import migrate_bundle_run

                run_dir = _safe_run_dir(
                    self.server.runs_root, str(payload.get("name", ""))
                )
                self._send_json(
                    migrate_bundle_run(
                        run_dir, repository_root=self.server.workflow_root
                    ),
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/completion/verify":
                from .workflow_domain import verify_completion_record

                run_dir = _safe_run_dir(
                    self.server.runs_root, str(payload.get("name", ""))
                )
                self._send_json(
                    verify_completion_record(
                        run_dir / "completion_record.json", artifact_root=run_dir
                    )
                )
                return
            if parsed.path == "/api/tasks/submit":
                from .orchestration import TaskRequest
                from .workflow_tasks import create_workflow_orchestrator

                request = TaskRequest(
                    operation=str(payload.get("operation", "")).strip(),
                    payload=payload.get("payload") or {},
                    idempotency_key=str(payload.get("request_id", "")).strip() or None,
                )
                orchestrator = create_workflow_orchestrator(self.server.task_root)
                record = orchestrator.submit(request)
                if record.status.value == "pending":
                    threading.Thread(
                        target=orchestrator.run_sync,
                        args=(record.task_id,),
                        daemon=True,
                        name=f"research-forge-{record.task_id}",
                    ).start()
                self._send_json(record.model_dump(mode="json"), HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/remediation/plan":
                from .remediation import create_remediation_plan

                run_dir = _safe_run_dir(
                    self.server.runs_root, str(payload.get("name", ""))
                )
                plan = create_remediation_plan(
                    run_dir,
                    remediation_root=self.server.runs_root / ".remediation",
                    force_new=bool(payload.get("force_new", False)),
                )
                self._send_json(plan.model_dump(mode="json"), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/remediation/approve":
                from .orchestration import TaskRequest
                from .remediation import approve_remediation_plan
                from .workflow_tasks import create_workflow_orchestrator

                run_name = str(payload.get("name", ""))
                selected = payload.get("selected_action_ids") or []
                if not isinstance(selected, list):
                    raise ValueError("selected_action_ids must be a list")
                plan = approve_remediation_plan(
                    self.server.runs_root / ".remediation",
                    run_name,
                    [str(item) for item in selected],
                    confirm_contract_revision=bool(
                        payload.get("confirm_contract_revision", False)
                    ),
                )
                response: dict[str, Any] = {
                    "plan": plan.model_dump(mode="json"),
                    "requires_contract_confirmation": (
                        plan.contract_confirmation_required
                        and not plan.contract_confirmed
                    ),
                }
                if plan.status == "approved":
                    orchestrator = create_workflow_orchestrator(self.server.task_root)
                    record = orchestrator.submit(
                        TaskRequest(
                            operation="bundle.remediate",
                            payload={
                                "source_run_name": run_name,
                                "plan_id": plan.plan_id,
                                "remediation_root": str(
                                    self.server.runs_root / ".remediation"
                                ),
                                "output_root": str(self.server.runs_root),
                            },
                            idempotency_key=f"remediation:{plan.plan_id}",
                        )
                    )
                    if record.status.value == "pending":
                        threading.Thread(
                            target=orchestrator.run_sync,
                            args=(record.task_id,),
                            daemon=True,
                            name=f"research-forge-{record.task_id}",
                        ).start()
                    response["task"] = record.model_dump(mode="json")
                self._send_json(response, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/remediation/archive":
                from .remediation import archive_remediation_plan

                plan = archive_remediation_plan(
                    self.server.runs_root / ".remediation",
                    str(payload.get("name", "")),
                )
                self._send_json(plan.model_dump(mode="json"))
                return
            if parsed.path == "/api/idea/start":
                from .orchestration import TaskRequest
                from .workflow_tasks import create_workflow_orchestrator

                orchestrator = create_workflow_orchestrator(self.server.task_root)
                record = orchestrator.execute_sync(
                    TaskRequest(
                        operation="idea.start",
                        payload={
                            "idea": str(payload.get("idea", "")),
                            "title": str(payload.get("title", "")),
                            "idea_root": str(self.server.idea_root),
                        },
                        idempotency_key=str(payload.get("request_id", "")).strip()
                        or None,
                    )
                )
                result = {
                    **(record.result or {}),
                    "_task": record.model_dump(mode="json"),
                }
                self._send_json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/select-folder":
                selected = select_project_folder(str(payload.get("initial", "")))
                self._send_json({"path": selected or "", "cancelled": selected is None})
                return
            if parsed.path == "/api/inspect":
                from .orchestration import TaskRequest
                from .workflow_tasks import create_workflow_orchestrator

                orchestrator = create_workflow_orchestrator(self.server.task_root)
                record = orchestrator.execute_sync(
                    TaskRequest(
                        operation="bundle.inspect",
                        payload={
                            "source": str(payload.get("source", "")).strip(),
                            "discover_claims": bool(
                                payload.get("discover_claims", False)
                            ),
                            "workflow_root": str(self.server.workflow_root),
                        },
                        idempotency_key=str(payload.get("request_id", "")).strip()
                        or None,
                    )
                )
                self._send_json(
                    {**(record.result or {}), "_task": record.model_dump(mode="json")}
                )
                return
            if parsed.path == "/api/close-loop":
                from .orchestration import TaskRequest
                from .workflow_tasks import create_workflow_orchestrator

                source = str(payload.get("source", "")).strip()
                record = create_workflow_orchestrator(
                    self.server.task_root
                ).execute_sync(
                    TaskRequest(
                        operation="bundle.close",
                        payload={
                            "source": source,
                            "output_root": str(self.server.runs_root),
                            "name": str(payload.get("name", "")).strip()
                            or Path(source).name,
                            "track_id": str(payload.get("track_id", "auto")).strip()
                            or "auto",
                            "discover_claims": bool(
                                payload.get("discover_claims", False)
                            ),
                        },
                        idempotency_key=str(payload.get("request_id", "")).strip()
                        or None,
                    )
                )
                run_dir = Path(str((record.result or {})["run_dir"]))
                self._send_json(
                    {
                        **load_run_detail(run_dir),
                        "_task": record.model_dump(mode="json"),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/expand-paper":
                from .orchestration import TaskRequest
                from .workflow_tasks import create_workflow_orchestrator

                run_dir = _safe_run_dir(
                    self.server.runs_root, str(payload.get("name", ""))
                )
                record = create_workflow_orchestrator(
                    self.server.task_root
                ).execute_sync(
                    TaskRequest(
                        operation="bundle.expand-paper",
                        payload={"run_dir": str(run_dir)},
                        idempotency_key=str(payload.get("request_id", "")).strip()
                        or None,
                    )
                )
                self._send_json(
                    {
                        **load_run_detail(run_dir),
                        "_task": record.model_dump(mode="json"),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/tasks/resume":
                from .workflow_tasks import create_workflow_orchestrator

                task_id = str(payload.get("task_id", "")).strip()
                orchestrator = create_workflow_orchestrator(self.server.task_root)
                record = orchestrator.load(task_id)
                threading.Thread(
                    target=orchestrator.run_sync,
                    args=(task_id,),
                    kwargs={"resume": True},
                    daemon=True,
                    name=f"research-forge-resume-{task_id}",
                ).start()
                self._send_json(record.model_dump(mode="json"), HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/tasks/pause":
                from .workflow_tasks import create_workflow_orchestrator

                task_id = str(payload.get("task_id", "")).strip()
                record = create_workflow_orchestrator(
                    self.server.task_root
                ).request_pause(task_id)
                self._send_json(record.model_dump(mode="json"))
                return
            if parsed.path == "/api/tasks/cancel":
                from .workflow_tasks import create_workflow_orchestrator

                task_id = str(payload.get("task_id", "")).strip()
                record = create_workflow_orchestrator(self.server.task_root).cancel(
                    task_id
                )
                self._send_json(record.model_dump(mode="json"))
                return
            if parsed.path == "/api/tasks/requirements/resolve":
                from .workflow_tasks import create_workflow_orchestrator

                task_id = str(payload.get("task_id", "")).strip()
                requirement_id = str(payload.get("requirement_id", "")).strip()
                resolution = payload.get("resolution") or {}
                if not isinstance(resolution, dict):
                    raise ValueError("requirement resolution must be a JSON object")
                record = create_workflow_orchestrator(
                    self.server.task_root
                ).resolve_requirement(task_id, requirement_id, resolution)
                self._send_json(record.model_dump(mode="json"))
                return
            if parsed.path == "/api/open-path":
                path = Path(str(payload.get("path", ""))).resolve()
                if not path.exists():
                    raise FileNotFoundError(f"path not found: {path}")
                if os.name == "nt":
                    os.startfile(path)  # type: ignore[attr-defined]
                else:  # pragma: no cover - the desktop target is Windows
                    webbrowser.open(path.as_uri())
                self._send_json({"opened": str(path)})
                return
            raise FileNotFoundError(f"API route not found: {parsed.path}")
        except Exception as exc:  # pragma: no cover - exercised through HTTP tests
            self._send_error_json(exc)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    idea_root: str | Path = DEFAULT_IDEA_ROOT,
    task_root: str | Path | None = None,
    workflow_root: str | Path | None = None,
    static_root: str | Path = DEFAULT_STATIC_ROOT,
) -> ResearchForgeWebServer:
    resolved_runs_root = Path(runs_root)
    return ResearchForgeWebServer(
        (host, port),
        runs_root=resolved_runs_root,
        idea_root=Path(idea_root),
        task_root=Path(task_root)
        if task_root is not None
        else resolved_runs_root / ".orchestration",
        workflow_root=(
            Path(workflow_root)
            if workflow_root is not None
            else resolved_runs_root / ".workflow-v2"
        ),
        static_root=Path(static_root),
    )


def run_web_app(
    host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = False
) -> None:
    server = create_server(host, port)
    url = f"http://{host}:{server.server_port}/"
    print(f"Research Forge web app: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = [
    "bootstrap_payload",
    "create_server",
    "initialize_idea_research",
    "list_bundle_runs",
    "load_run_detail",
    "run_web_app",
    "select_project_folder",
    "summarize_run",
]
