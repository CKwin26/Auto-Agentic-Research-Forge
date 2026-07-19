from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
import subprocess
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .models import utc_now
from .paper_expansion import expand_project_bundle_paper
from .project_bundle import close_project_bundle_loop, inspect_project_bundle
from .service import create_project
from .storage import read_json, write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "bundle_runs"
DEFAULT_IDEA_ROOT = PROJECT_ROOT / "idea_runs"
DEFAULT_STATIC_ROOT = PROJECT_ROOT / "research-forge-ui" / "dist"
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
            [powershell, "-NoProfile", "-WindowStyle", "Hidden", "-STA", "-Command", script],
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
    manifest = _read_json_if_present(run_dir / "bundle_manifest.json", {})
    audit = _read_json_if_present(run_dir / "stage_4_synthesis" / "audit.json", {})
    paper_plan = _read_json_if_present(
        run_dir / "stage_4_synthesis" / "paper_expansion_plan.json", {}
    )
    paper_audit = _read_json_if_present(
        run_dir / "stage_4_synthesis" / "paper_expansion_audit.json", {}
    )
    return {
        "name": run_dir.name,
        "path": str(run_dir),
        "source_root": manifest.get("source_root", ""),
        "track_id": certificate.get("track_id") or manifest.get("selected_track_id", ""),
        "completed_at": certificate.get("completed_at", ""),
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
        "publication_ready": bool(paper_audit.get("publication_ready", False)),
        "audit_passed": bool(audit.get("passed", False)),
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
    runs.sort(key=lambda item: (item.get("completed_at", ""), item["name"]), reverse=True)
    return runs


def load_run_detail(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    summary = summarize_run(root)
    inspection = _read_json_if_present(root / "inspection.json", {})
    scope = _read_json_if_present(root / "stage_1_discovery" / "scope_contract.json", {})
    protocol = _read_json_if_present(root / "stage_2_protocol" / "protocol_lock.json", {})
    verdict = _read_json_if_present(root / "stage_3_experimentation" / "idea_verdict.json", {})
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
    claims = _read_json_if_present(root / "stage_4_synthesis" / "claims.json", {})
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
        "claims": claims.get("claims", []),
        "manuscript": _read_text_if_present(root / "stage_4_synthesis" / "manuscript.md"),
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
) -> dict[str, Any]:
    """Create a durable Stage 1 intake for the idea-to-paper workflow.

    This starts the real four-stage project without pretending that literature,
    experiment, or publication gates have already passed.
    """

    cleaned_idea = idea.strip()
    if len(cleaned_idea) < 12:
        raise ValueError("研究想法至少需要 12 个字符，以便形成可收敛的问题边界")
    cleaned_title = title.strip() or cleaned_idea[:28].rstrip("，。；;,. ") or "未命名研究"
    project = create_project(
        cleaned_title,
        cleaned_idea,
        slug=f"idea-{uuid.uuid4().hex[:10]}",
        root=idea_root,
    )
    intake = {
        "workflow": "idea-to-paper",
        "requested_at": utc_now(),
        "title": cleaned_title,
        "idea": cleaned_idea,
        "status": "initialized",
        "current_stage": "stage_1_discovery",
        "next_gate": "真实文献检索与研究边界确认",
        "stages": [
            {"id": "stage_1_discovery", "title": "发现与收敛", "status": "ready"},
            {"id": "stage_2_protocol", "title": "协议与基线", "status": "pending"},
            {"id": "stage_3_experimentation", "title": "实验与判定", "status": "pending"},
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
        static_root: Path,
    ) -> None:
        self.runs_root = runs_root.resolve()
        self.idea_root = idea_root.resolve()
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
        if self.server.static_root not in candidate.parents and candidate != self.server.static_root:
            raise FileNotFoundError("static asset not found")
        if not candidate.is_file():
            candidate = self.server.static_root / "index.html"
        content = candidate.read_bytes()
        mime_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if mime_type.startswith("text/") or mime_type in {"application/javascript", "application/json"}:
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
            if parsed.path == "/api/health":
                self._send_json({"ok": True, "service": "research-forge-web"})
                return
            if parsed.path == "/api/bootstrap":
                self._send_json(bootstrap_payload(self.server.runs_root, self.server.idea_root))
                return
            if parsed.path == "/api/runs":
                self._send_json({"runs": list_bundle_runs(self.server.runs_root)})
                return
            if parsed.path == "/api/run":
                query = parse_qs(parsed.query)
                name = query.get("name", [""])[0]
                self._send_json(load_run_detail(_safe_run_dir(self.server.runs_root, name)))
                return
            if parsed.path == "/api/artifact":
                query = parse_qs(parsed.query)
                run_dir = _safe_run_dir(self.server.runs_root, query.get("name", [""])[0])
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._request_json()
            if parsed.path == "/api/idea/start":
                result = initialize_idea_research(
                    str(payload.get("idea", "")),
                    title=str(payload.get("title", "")),
                    idea_root=self.server.idea_root,
                )
                self._send_json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/select-folder":
                selected = select_project_folder(str(payload.get("initial", "")))
                self._send_json({"path": selected or "", "cancelled": selected is None})
                return
            if parsed.path == "/api/inspect":
                source = str(payload.get("source", "")).strip()
                inspection = inspect_project_bundle(source)
                self._send_json(inspection.model_dump(mode="json"))
                return
            if parsed.path == "/api/close-loop":
                source = str(payload.get("source", "")).strip()
                track_id = str(payload.get("track_id", "auto")).strip() or "auto"
                name = str(payload.get("name", "")).strip() or Path(source).name
                run_dir = close_project_bundle_loop(
                    source,
                    output_root=self.server.runs_root,
                    name=name,
                    track_id=track_id,
                )
                self._send_json(load_run_detail(run_dir), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/expand-paper":
                run_dir = _safe_run_dir(
                    self.server.runs_root, str(payload.get("name", ""))
                )
                asyncio.run(expand_project_bundle_paper(run_dir))
                self._send_json(load_run_detail(run_dir), HTTPStatus.CREATED)
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
    static_root: str | Path = DEFAULT_STATIC_ROOT,
) -> ResearchForgeWebServer:
    return ResearchForgeWebServer(
        (host, port),
        runs_root=Path(runs_root),
        idea_root=Path(idea_root),
        static_root=Path(static_root),
    )


def run_web_app(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = False) -> None:
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
