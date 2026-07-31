from __future__ import annotations

import asyncio
import importlib.util
import json
import mimetypes
import os
import shutil
import subprocess
import sys
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
EXTERNAL_RESEARCH_OPTIONAL_PACKAGES = (
    "huggingface-hub==1.24.0",
    "mcp>=1.28,<2",
    "openai>=2.45,<3",
    "paper-search-mcp==0.1.4",
    "paper-qa==2026.3.18",
)
EXTERNAL_RESEARCH_IMPORTS = {
    "academic_search": ("paper_search_mcp",),
    "huggingface_research": ("huggingface_hub",),
    "evidence_analysis": ("paperqa", "paperqa_pypdf"),
}


def _retrieval_freshness(value: Any) -> Literal["cache_only", "live"]:
    freshness = str(value or "cache_only")
    if freshness not in {"cache_only", "live"}:
        raise ValueError("freshness must be cache_only or live")
    return freshness  # type: ignore[return-value]


def _run_stage3_build_action(
    repository: Any,
    study_id: str,
    step_type: str,
    action: Any,
) -> Any:
    """Keep API build failures in the persisted Stage 3 state machine."""

    from .stage_three_build import (
        Stage3BuildAdmissionError,
        record_stage3_build_failure,
    )

    try:
        return action()
    except Stage3BuildAdmissionError as exc:
        record_stage3_build_failure(
            repository, study_id, step_type, exc
        )
        raise


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


def runtime_status_payload() -> dict[str, Any]:
    """Return a secret-free summary for the first-run deployment wizard."""

    from .agent_runtime import backend_status
    from .service import key_is_present

    try:
        status = backend_status()
    except Exception as exc:  # noqa: BLE001
        return {
            "backend": "unknown",
            "ready": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    backend = str(status.get("backend", "codex"))
    api_key_present = key_is_present()
    ready = (
        bool(status.get("codex_sdk_installed"))
        and bool(status.get("codex_authenticated"))
        if backend == "codex"
        else bool(status.get("api_sdk_installed")) and api_key_present
    )
    allowed = {
        "backend",
        "model",
        "api_sdk_installed",
        "api_sdk_version",
        "codex_sdk_installed",
        "codex_sdk_version",
        "codex_authenticated",
        "codex_account_type",
        "codex_plan_type",
        "provider_name",
        "provider_base_url",
        "provider_model",
        "provider_home_source",
    }
    return {
        **{key: value for key, value in status.items() if key in allowed},
        "api_key_present": api_key_present,
        "ready": ready,
    }


def _write_runtime_env(
    values: dict[str, str | None],
    *,
    env_path: str | Path = PROJECT_ROOT / ".env.local",
) -> Path:
    """Atomically update the ignored local runtime file without exposing secrets."""

    path = Path(env_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    normalized: dict[str, str | None] = {}
    for key, value in values.items():
        if not key or not key.replace("_", "").isalnum() or key.upper() != key:
            raise ValueError("runtime environment keys must use uppercase letters and underscores")
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError(f"runtime value for {key} must be a single line")
        normalized[key] = value

    output: list[str] = []
    consumed: set[str] = set()
    for line in existing:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if key in normalized:
            if key not in consumed and normalized[key] is not None:
                output.append(f"{key}={normalized[key]}")
            consumed.add(key)
        else:
            output.append(line)
    for key, value in normalized.items():
        if key not in consumed and value is not None:
            output.append(f"{key}={value}")

    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, path)
    return path


def configure_runtime(
    payload: dict[str, Any],
    *,
    env_path: str | Path = PROJECT_ROOT / ".env.local",
) -> dict[str, Any]:
    """Configure the local model backend from the deployment wizard."""

    backend = str(payload.get("backend", "")).strip().lower()
    if backend not in {"codex", "api"}:
        raise ValueError("backend must be codex or api")
    updates: dict[str, str | None] = {"RESEARCH_FORGE_BACKEND": backend}
    runtime_values: dict[str, str | None] = {"RESEARCH_FORGE_BACKEND": backend}
    if backend == "codex":
        provider_mode = str(
            payload.get("provider_mode", "managed")
        ).strip().lower()
        if provider_mode not in {"managed", "configured"}:
            raise ValueError(
                "Codex provider_mode must be managed or configured"
            )
        if provider_mode == "managed":
            # Selecting “use Codex login” means the local managed Codex
            # subscription.  A stale CC-Switch/custom-provider home must not
            # silently redirect later Stage 3/4 model calls.
            updates.update(
                {
                    "RESEARCH_FORGE_CODEX_HOME": None,
                    "RESEARCH_FORGE_PROVIDER_BILLING_CONTRACT": None,
                }
            )
            runtime_values.update(updates)
    else:
        api_key = str(payload.get("api_key", "")).strip()
        model = str(payload.get("model", "")).strip()
        base_url = str(payload.get("base_url", "")).strip().rstrip("/")
        if len(api_key) < 8:
            raise ValueError("API key is required")
        if not model or len(model) > 160:
            raise ValueError("a valid model name is required")
        if base_url and not base_url.startswith("https://"):
            raise ValueError("custom API base URL must use HTTPS")
        updates.update(
            {
                "OPENAI_API_KEY": api_key,
                "AUTORESEARCH_MODEL": model,
                "OPENAI_BASE_URL": base_url or None,
            }
        )
        runtime_values.update(updates)
    _write_runtime_env(updates, env_path=env_path)
    for key, value in runtime_values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return runtime_status_payload()


def _local_env_value(
    key: str,
    *,
    env_path: str | Path = PROJECT_ROOT / ".env.local",
) -> str | None:
    value = os.environ.get(key)
    if value is not None:
        return value
    path = Path(env_path).expanduser().resolve()
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        candidate, separator, raw = line.partition("=")
        if separator and candidate.strip() == key:
            return raw.strip()
    return None


def external_research_setup_payload(
    *,
    env_path: str | Path = PROJECT_ROOT / ".env.local",
    workflow_root: str | Path = DEFAULT_RUNS_ROOT / ".workflow-v2",
) -> dict[str, Any]:
    """Return installation state without treating installation as live validation."""

    importlib.invalidate_caches()
    components = []
    for capability, module_names in EXTERNAL_RESEARCH_IMPORTS.items():
        missing = [
            module_name
            for module_name in module_names
            if importlib.util.find_spec(module_name) is None
        ]
        components.append(
            {
                "capability": capability,
                "installed": not missing,
                "missing_modules": missing,
            }
        )
    choice = (
        _local_env_value(
            "RESEARCH_FORGE_EXTERNAL_RESEARCH_SETUP",
            env_path=env_path,
        )
        or "unconfigured"
    )
    validation_path = (
        Path(workflow_root).resolve()
        / "retrieval"
        / "readiness-validation.json"
    )
    validation = read_json(validation_path) if validation_path.is_file() else None
    return {
        "choice": choice,
        "components": components,
        "dependencies_installed": all(item["installed"] for item in components),
        "project_network_default": "offline",
        "requires_project_owner_network_approval": True,
        "institutional_login_deferred": True,
        "validation": validation,
    }


def configure_external_research_setup(
    payload: dict[str, Any],
    *,
    env_path: str | Path = PROJECT_ROOT / ".env.local",
    workflow_root: str | Path = DEFAULT_RUNS_ROOT / ".workflow-v2",
) -> dict[str, Any]:
    """Persist the deployment choice and optionally install a fixed capability set."""

    choice = str(payload.get("choice", "")).strip().lower()
    if choice not in {"public", "offline"}:
        raise ValueError("external research choice must be public or offline")
    install_optional = bool(payload.get("install_optional", False))
    if choice == "offline" and install_optional:
        raise ValueError("offline setup cannot request dependency installation")
    if choice == "public" and install_optional:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--user",
                "--no-warn-script-location",
                *EXTERNAL_RESEARCH_OPTIONAL_PACKAGES,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "可选研究组件安装失败。请检查网络后重试，或暂时选择离线模式。"
            )
    if choice == "public" and bool(payload.get("run_validation", True)):
        validation = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "validate_external_research_v1.py"),
                "--workflow-root",
                str(Path(workflow_root).resolve()),
                "--quick",
                "--live",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=480,
            check=False,
        )
        if validation.returncode != 0:
            raise RuntimeError(
                "外部研究组件已安装，但部署验收未通过。请查看本机验收结果后重试。"
            )
    _write_runtime_env(
        {"RESEARCH_FORGE_EXTERNAL_RESEARCH_SETUP": choice},
        env_path=env_path,
    )
    os.environ["RESEARCH_FORGE_EXTERNAL_RESEARCH_SETUP"] = choice
    return external_research_setup_payload(
        env_path=env_path,
        workflow_root=workflow_root,
    )


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
            if parsed.path == "/api/runtime/status":
                self._send_json(runtime_status_payload())
                return
            if parsed.path == "/api/runtime/external-research":
                self._send_json(
                    external_research_setup_payload(
                        workflow_root=self.server.workflow_root
                    )
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
            if parsed.path == "/api/studies/stage2":
                from .workflow_domain import WorkflowRepository

                query = parse_qs(parsed.query)
                study_id = query.get("study_id", [""])[0]
                repository = WorkflowRepository(self.server.workflow_root)
                repository.load_study(study_id)
                stage2 = (
                    repository.root / "studies" / study_id / "stage2"
                )
                contract = repository.latest_research_contract(study_id)
                contract_version = contract.version if contract else 1

                def current_stage2_path(name: str) -> Path:
                    path = stage2 / name
                    if contract_version <= 1:
                        return path
                    candidate = stage2 / (
                        f"{Path(name).stem}.v{contract_version}"
                        f"{Path(name).suffix}"
                    )
                    return candidate if candidate.is_file() else path

                json_names = (
                    "stage2_input_check.json",
                    "baseline_candidates.json",
                    "resource_inventory.json",
                    "data_quality_report.json",
                    "data_boundary.json",
                    "leakage_risk_report.json",
                    "resource_gap_report.json",
                    "resource_requirements.json",
                    "concrete_resource_candidates.json",
                    "resource_candidate_evaluation.json",
                    "resource_selection.json",
                    "candidate_topics.json",
                    "scope_change_request.json",
                    "scope_contract.json",
                    "protocol.draft.json",
                    "decision_rules.json",
                    "preflight_report.json",
                    "baseline_validation_report.json",
                    "contract_lint_report.json",
                    "contract_compile_report.json",
                    "contract_dry_run_report.json",
                    "execution_readiness_gate.json",
                    "blocking_issue_report.json",
                    "stage2_gate_report.json",
                )
                artifacts = {
                    name: _read_json_if_present(
                        current_stage2_path(name), None
                    )
                    for name in json_names
                }
                for name in (
                    "research_method_summary.md",
                    "resource_acquisition_plan.md",
                    "resource_candidate_comparison.md",
                    "topic_feasibility_matrix.md",
                    "topic_recommendation.md",
                    "protocol.draft.md",
                    "baseline_validation_summary.md",
                ):
                    artifacts[name] = (
                        _read_text_if_present(current_stage2_path(name))
                        if current_stage2_path(name).is_file()
                        else None
                    )
                for name in (
                    "mvp_spec.json",
                    "stage3_resource_plan.json",
                    "dataset_spec.json",
                    "annotation_protocol.json",
                    "research-forge.experiments.json",
                    "treatment_declaration.json",
                ):
                    artifacts[name] = _read_json_if_present(
                        stage2 / "experiment_build" / name,
                        None,
                    )
                self._send_json(
                    {
                        "study_id": study_id,
                        "artifacts": artifacts,
                        "amendments": [
                            _read_json_if_present(path, {})
                            for path in sorted(
                                stage2.glob("protocol_amendment_*.json")
                            )
                        ],
                        "input_check_attempts": [
                            _read_json_if_present(path, {})
                            for path in sorted(
                                stage2.glob(
                                    "stage2_input_check.attempt-*.json"
                                )
                            )
                        ],
                    }
                )
                return
            if parsed.path == "/api/studies/stage3":
                from .stage_three import stage3_read_model
                from .workflow_domain import WorkflowRepository

                query = parse_qs(parsed.query)
                study_id = query.get("study_id", [""])[0]
                self._send_json(
                    stage3_read_model(
                        WorkflowRepository(self.server.workflow_root),
                        study_id,
                    )
                )
                return
            if parsed.path == "/api/studies/stage4":
                from .stage_four import stage4_read_model
                from .workflow_domain import WorkflowRepository

                query = parse_qs(parsed.query)
                study_id = query.get("study_id", [""])[0]
                if not study_id:
                    raise ValueError("study_id query parameter is required")
                self._send_json(
                    stage4_read_model(
                        WorkflowRepository(self.server.workflow_root),
                        study_id,
                    )
                )
                return
            path_parts = [
                item for item in parsed.path.split("/") if item
            ]
            if (
                len(path_parts) == 4
                and path_parts[:2] == ["api", "studies"]
                and path_parts[3] == "evidence-grade"
            ):
                from .reproduction_verifier import (
                    ReproductionStore,
                    assess_reproduction_evidence,
                    load_reproduction_trust_registry,
                )

                study_id = path_parts[2]
                store = ReproductionStore(self.server.workflow_root)
                receipts = store.list_receipts(study_id)
                conflicts = store.list_conflicts(study_id)
                assessment = assess_reproduction_evidence(
                    study_id=study_id,
                    package_verified=bool(store.list_jobs(study_id)),
                    receipts=receipts,
                    trusted_verifier_keys=load_reproduction_trust_registry(
                        self.server.workflow_root,
                        registry="reproduction-verifiers",
                    ),
                    conflicts=conflicts,
                )
                self._send_json(assessment.model_dump(mode="json"))
                return
            if (
                len(path_parts) == 3
                and path_parts[:2] == ["api", "reproductions"]
            ):
                from .reproduction_verifier import ReproductionStore

                query = parse_qs(parsed.query)
                study_id = query.get("study_id", [""])[0]
                if not study_id:
                    raise ValueError("study_id query parameter is required")
                job = ReproductionStore(
                    self.server.workflow_root
                ).load_job(study_id, path_parts[2])
                self._send_json(job.model_dump(mode="json"))
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
            if parsed.path == "/api/runtime/configure":
                self._send_json(configure_runtime(payload))
                return
            if parsed.path == "/api/runtime/external-research":
                self._send_json(
                    configure_external_research_setup(
                        payload,
                        workflow_root=self.server.workflow_root,
                    )
                )
                return
            if (
                len(path_parts) == 4
                and path_parts[:2] == ["api", "studies"]
                and path_parts[3] == "reproductions"
            ):
                from .reproduction_checker import (
                    verify_reproduction_package,
                )
                from .reproduction_domain import (
                    ReproductionPackageManifest,
                    ReproductionPolicy,
                )
                from .reproduction_launcher import (
                    create_reproduction_job,
                )
                from .reproduction_verifier import (
                    ReproductionStore,
                    load_reproduction_trust_registry,
                )

                package_path = Path(
                    str(payload.get("package_path", ""))
                ).expanduser().resolve()
                trusted_keys = load_reproduction_trust_registry(
                    self.server.workflow_root,
                    registry="control-plane-signers",
                )
                verification = verify_reproduction_package(
                    package_path,
                    trusted_control_plane_keys=trusted_keys,
                )
                if not verification["reproduction_ready"]:
                    self._send_json(
                        {
                            "status": "blocked",
                            "verification": verification,
                        },
                        HTTPStatus.CONFLICT,
                    )
                    return
                manifest = ReproductionPackageManifest.model_validate(
                    verification["manifest"]
                )
                policy = ReproductionPolicy.model_validate(
                    verification["policy"]
                )
                if manifest.study_id != path_parts[2]:
                    raise ValueError(
                        "reproduction package Study does not match URL"
                    )
                job = create_reproduction_job(
                    package_path=package_path,
                    manifest=manifest,
                    policy=policy,
                    requested_by=str(
                        payload.get("requested_by", "project_owner")
                    ),
                )
                store = ReproductionStore(self.server.workflow_root)
                store.save_policy(policy)
                store.save_job(job)
                self._send_json(
                    job.model_dump(mode="json"), HTTPStatus.CREATED
                )
                return
            if (
                len(path_parts) == 4
                and path_parts[:2] == ["api", "reproductions"]
                and path_parts[3] == "cancel"
            ):
                from .models import utc_now
                from .reproduction_domain import ReproductionJobStatus
                from .reproduction_verifier import ReproductionStore

                study_id = str(payload.get("study_id", "")).strip()
                store = ReproductionStore(self.server.workflow_root)
                job = store.load_job(study_id, path_parts[2])
                job = job.model_copy(
                    update={
                        "status": ReproductionJobStatus.CANCELLED,
                        "updated_at": utc_now(),
                    }
                )
                store.save_job(job)
                self._send_json(job.model_dump(mode="json"))
                return
            if parsed.path == "/api/reproductions/receipts/ingest":
                from .reproduction_attestation import (
                    verify_signed_reproduction_receipt,
                )
                from .reproduction_domain import (
                    ReproductionConflict,
                    ReproductionResult,
                    SignedReproductionReceipt,
                )
                from .reproduction_verifier import (
                    ReproductionStore,
                    assess_reproduction_evidence,
                    load_reproduction_trust_registry,
                )
                from .workflow_domain import stable_id

                signed = SignedReproductionReceipt.model_validate(
                    payload.get("signed_receipt") or {}
                )
                trusted_verifiers = load_reproduction_trust_registry(
                    self.server.workflow_root,
                    registry="reproduction-verifiers",
                )
                if not verify_signed_reproduction_receipt(
                    signed, trusted_public_keys=trusted_verifiers
                ):
                    raise ValueError(
                        "receipt signature is not in the verifier registry"
                    )
                receipt = signed.receipt
                store = ReproductionStore(self.server.workflow_root)
                store.save_receipt(signed)
                if receipt.result in {
                    ReproductionResult.MISMATCH,
                    ReproductionResult.FAILED_EXECUTION,
                }:
                    store.save_conflict(
                        ReproductionConflict(
                            conflict_id=stable_id(
                                "reproduction-conflict",
                                receipt.study_id,
                                receipt.job_id,
                                receipt.receipt_id,
                            ),
                            study_id=receipt.study_id,
                            job_id=receipt.job_id,
                            original_verdict_id="preserved-in-stage3-package",
                            receipt_id=receipt.receipt_id,
                            reproduction_result=receipt.result,
                            details={
                                "original_verdict": (
                                    receipt.comparison.original_verdict
                                ),
                                "reproduced_verdict": (
                                    receipt.comparison.reproduced_verdict
                                ),
                            },
                        )
                    )
                assessment = assess_reproduction_evidence(
                    study_id=receipt.study_id,
                    package_verified=True,
                    receipts=store.list_receipts(receipt.study_id),
                    trusted_verifier_keys=trusted_verifiers,
                    conflicts=store.list_conflicts(receipt.study_id),
                )
                store.save_assessment(assessment)
                self._send_json(
                    {
                        "receipt": signed.model_dump(mode="json"),
                        "assessment": assessment.model_dump(mode="json"),
                        "original_verdict_overwritten": False,
                    },
                    HTTPStatus.CREATED,
                )
                return
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
                    scope_overrides=(
                        dict(payload["scope_overrides"])
                        if isinstance(payload.get("scope_overrides"), dict)
                        else None
                    ),
                )
                self._send_json(result)
                return
            if parsed.path == "/api/studies/stage2/initialize":
                from .stage_two import ensure_stage_two_dag
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                steps = ensure_stage_two_dag(repository, study_id)
                snapshot = PersistentDAGScheduler(
                    repository, workflow_handlers(), recover_interrupted=True
                ).run(study_id)
                self._send_json(
                    {
                        "study_id": study_id,
                        "created_or_existing_step_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "workflow": snapshot,
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage3/initialize":
                from .stage_three import (
                    ensure_stage_three_dag,
                    stage3_read_model,
                )
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                handoff, plan, steps = ensure_stage_three_dag(
                    repository,
                    study_id,
                    explicit_manifest_path=(
                        str(payload.get("experiment_manifest_path", "")).strip()
                        or None
                    ),
                )
                workflow = PersistentDAGScheduler(
                    repository,
                    workflow_handlers(),
                    max_concurrency=plan.concurrency,
                    recover_interrupted=True,
                ).run(study_id)
                self._send_json(
                    {
                        "study_id": study_id,
                        "handoff": handoff.model_dump(mode="json"),
                        "plan_id": plan.plan_id,
                        "created_or_existing_step_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "workflow": workflow,
                        "stage3": stage3_read_model(repository, study_id),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage4/initialize":
                from .stage_four import ensure_stage_four_dag, stage4_read_model
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                authority, steps = ensure_stage_four_dag(
                    repository,
                    study_id,
                    venue_policy_id=str(
                        payload.get("venue_policy_id", "generic-journal-v1")
                    ).strip(),
                )
                workflow = PersistentDAGScheduler(
                    repository,
                    workflow_handlers(),
                    recover_interrupted=True,
                ).run(study_id)
                self._send_json(
                    {
                        "study_id": study_id,
                        "claim_authority": authority,
                        "created_or_existing_step_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "workflow": workflow,
                        "stage4": stage4_read_model(repository, study_id),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage4/revise":
                from .stage_four import (
                    request_stage_four_revision,
                    stage4_read_model,
                )
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                authority, steps, revision_request = (
                    request_stage_four_revision(
                        repository,
                        study_id,
                        str(payload.get("gate_id", "")).strip(),
                        reason=str(payload.get("reason", "")).strip(),
                        decided_by=str(
                            payload.get("decided_by", "project_owner")
                        ).strip(),
                    )
                )
                workflow = PersistentDAGScheduler(
                    repository,
                    workflow_handlers(),
                    recover_interrupted=True,
                ).run(study_id)
                self._send_json(
                    {
                        "study_id": study_id,
                        "claim_authority": authority,
                        "revision_request": revision_request,
                        "created_step_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "workflow": workflow,
                        "stage4": stage4_read_model(repository, study_id),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage3/build/initialize":
                from .stage_three import stage3_read_model
                from .stage_three_build import (
                    create_experiment_build_plan,
                    stage3_build_admission_from_stage2,
                )
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                handoff, seal, capability = _run_stage3_build_action(
                    repository,
                    study_id,
                    "stage3_build_admission",
                    lambda: stage3_build_admission_from_stage2(
                        repository, study_id
                    ),
                )
                build_plan = _run_stage3_build_action(
                    repository,
                    study_id,
                    "create_experiment_build_plan",
                    lambda: create_experiment_build_plan(
                        repository, study_id, handoff.handoff_id
                    ),
                )
                self._send_json(
                    {
                        "study_id": study_id,
                        "handoff": handoff.model_dump(mode="json"),
                        "scientific_specification_seal": seal.model_dump(
                            mode="json"
                        ),
                        "profile_capability": capability.value,
                        "build_plan": build_plan.model_dump(mode="json"),
                        "stage3": stage3_read_model(repository, study_id),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage3/build/freeze-ready-made":
                from .stage_three_build import (
                    freeze_ready_made_execution_package,
                )
                from .workflow_domain import (
                    ExecutionTrustLevel,
                    WorkflowRepository,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                seal = _run_stage3_build_action(
                    repository,
                    study_id,
                    "freeze_execution_package",
                    lambda: freeze_ready_made_execution_package(
                        repository,
                        study_id,
                        str(payload.get("build_plan_id", "")).strip(),
                        trust_level=ExecutionTrustLevel(
                            str(
                                payload.get(
                                    "trust_level",
                                    "trusted_local_project",
                                )
                            )
                        ),
                    ),
                )
                self._send_json(
                    seal.model_dump(mode="json"), HTTPStatus.CREATED
                )
                return
            if parsed.path == "/api/studies/stage3/complete-boundary":
                from .stage_four import (
                    ensure_stage_four_dag,
                    stage4_read_model,
                )
                from .stage_three import (
                    finalize_stage3_unverifiable_boundary,
                    stage3_read_model,
                )
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                raw_reasons = payload.get("reasons")
                reasons = (
                    [str(item) for item in raw_reasons]
                    if isinstance(raw_reasons, list)
                    else []
                )
                completion = finalize_stage3_unverifiable_boundary(
                    repository,
                    study_id,
                    reasons=reasons,
                )
                authority, steps = ensure_stage_four_dag(
                    repository, study_id
                )
                workflow = PersistentDAGScheduler(
                    repository,
                    workflow_handlers(),
                    recover_interrupted=True,
                ).run(study_id)
                self._send_json(
                    {
                        "completion": completion.model_dump(mode="json"),
                        "claim_authority": authority,
                        "stage3": stage3_read_model(repository, study_id),
                        "stage4_step_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "stage4": stage4_read_model(repository, study_id),
                        "workflow": workflow,
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage3/build/generate":
                from .stage_three import stage3_read_model
                from .stage_three_build import (
                    generate_and_materialize_profile_v1,
                )
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                current_stage3 = stage3_read_model(repository, study_id)
                current_status = str(
                    (current_stage3.get("overview") or {}).get("status") or ""
                )
                current_build_steps = list(
                    current_stage3.get("build_steps") or []
                )
                if current_status in {
                    "contract_revision_required",
                    "unsupported_profile",
                } or any(
                    (item.get("blocker") or {}).get("kind")
                    == "contract_revision_required"
                    for item in current_build_steps
                ):
                    raise ValueError(
                        "Stage 3 construction is not authorized because the "
                        "Stage 2 scientific specification is incomplete."
                    )
                if any(
                    item.get("status") == "running"
                    for item in current_build_steps
                ):
                    raise ValueError(
                        "Stage 3 construction is already running for this Study."
                    )
                materialization = _run_stage3_build_action(
                    repository,
                    study_id,
                    "resolve_or_build_assets",
                    lambda: asyncio.run(
                        generate_and_materialize_profile_v1(
                            repository,
                            study_id,
                            str(payload.get("build_plan_id", "")).strip(),
                        )
                    ),
                )
                self._send_json(
                    materialization, HTTPStatus.CREATED
                )
                return
            if parsed.path == "/api/studies/stage3/build/resolve-resources":
                from .stage_three_build import (
                    resolve_stage3_resource_routes,
                )
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                resolution = _run_stage3_build_action(
                    repository,
                    study_id,
                    "resolve_external_resources",
                    lambda: resolve_stage3_resource_routes(
                        repository,
                        study_id,
                        str(payload.get("build_plan_id", "")).strip(),
                    ),
                )
                self._send_json(resolution, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/stage3/build/smoke-generated":
                from .stage_three_build import (
                    smoke_generated_profile_v1_package,
                )
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                receipt = _run_stage3_build_action(
                    repository,
                    study_id,
                    "run_engineering_smoke_tests",
                    lambda: smoke_generated_profile_v1_package(
                        repository,
                        study_id,
                        str(payload.get("build_plan_id", "")).strip(),
                    ),
                )
                self._send_json(receipt, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/stage3/build/freeze-generated":
                from .stage_three_build import (
                    freeze_generated_profile_v1_execution_package,
                )
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                seal = _run_stage3_build_action(
                    repository,
                    study_id,
                    "freeze_execution_package",
                    lambda: freeze_generated_profile_v1_execution_package(
                        repository,
                        study_id,
                        str(payload.get("build_plan_id", "")).strip(),
                    ),
                )
                self._send_json(
                    seal.model_dump(mode="json"), HTTPStatus.CREATED
                )
                return
            if parsed.path == "/api/studies/stage3/formal-admission":
                from .stage_three import stage3_read_model
                from .stage_three_build import formal_execution_admission
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                handoff = _run_stage3_build_action(
                    repository,
                    study_id,
                    "formal_execution_admission",
                    lambda: formal_execution_admission(
                        repository,
                        study_id,
                        str(
                            payload.get("execution_package_seal_id", "")
                        ).strip(),
                    ),
                )
                self._send_json(
                    {
                        "handoff": handoff.model_dump(mode="json"),
                        "stage3": stage3_read_model(repository, study_id),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage3/repairs/propose":
                from .stage_three import propose_stage3_repair
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                repair = propose_stage3_repair(
                    repository,
                    str(payload.get("study_id", "")).strip(),
                    diagnostic_id=str(
                        payload.get("diagnostic_id", "")
                    ).strip(),
                    changed_artifact_ids=[
                        str(item)
                        for item in payload.get("changed_artifact_ids", [])
                    ]
                    or None,
                    scientific_change=bool(
                        payload.get("scientific_change", False)
                    ),
                    changed_contract_fields=[
                        str(item)
                        for item in payload.get(
                            "changed_contract_fields", []
                        )
                    ]
                    or None,
                    regression_checks=[
                        dict(item)
                        for item in payload.get("regression_checks", [])
                        if isinstance(item, dict)
                    ],
                )
                self._send_json(
                    repair.model_dump(mode="json"), HTTPStatus.CREATED
                )
                return
            if parsed.path == "/api/studies/stage3/successors/initialize":
                from .stage_three import (
                    initialize_stage3_successor,
                    stage3_read_model,
                )
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                successor, handoff, plan, steps = initialize_stage3_successor(
                    repository,
                    study_id,
                    str(payload.get("repair_contract_id", "")).strip(),
                    explicit_manifest_path=(
                        str(payload.get("experiment_manifest_path", "")).strip()
                        or None
                    ),
                )
                workflow = PersistentDAGScheduler(
                    repository,
                    workflow_handlers(),
                    max_concurrency=plan.concurrency,
                ).run(study_id)
                self._send_json(
                    {
                        "successor": successor.model_dump(mode="json"),
                        "handoff": handoff.model_dump(mode="json"),
                        "plan_id": plan.plan_id,
                        "step_instance_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "workflow": workflow,
                        "stage3": stage3_read_model(repository, study_id),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if (
                parsed.path
                == "/api/studies/stage3/scientific-successors/apply"
            ):
                from .stage_three import (
                    apply_stage3_scientific_successor_contract,
                    stage3_read_model,
                )
                from .workflow_domain import WorkflowRepository

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                changes = payload.get("changes")
                if not isinstance(changes, dict):
                    raise ValueError("changes must be an object")
                contract = apply_stage3_scientific_successor_contract(
                    repository,
                    study_id,
                    request_id=str(
                        payload.get("request_id", "")
                    ).strip(),
                    changes=changes,
                    decided_by=str(
                        payload.get("decided_by") or "project_owner"
                    ),
                    reason=str(
                        payload.get("reason")
                        or "Approved bounded scientific successor."
                    ),
                )
                self._send_json(
                    {
                        "research_contract": contract.model_dump(
                            mode="json"
                        ),
                        "stage3": stage3_read_model(repository, study_id),
                    },
                    HTTPStatus.CREATED,
                )
                return
            if parsed.path == "/api/studies/stage2/topics/select":
                from .stage_two import select_stage_two_topic
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                result = select_stage_two_topic(
                    repository,
                    study_id,
                    str(payload.get("topic_id", "")).strip(),
                    resource_candidate_ids=(
                        [
                            str(item)
                            for item in payload["resource_candidate_ids"]
                        ]
                        if isinstance(
                            payload.get("resource_candidate_ids"), list
                        )
                        else None
                    ),
                    decided_by=str(
                        payload.get("decided_by", "project_owner")
                    ).strip(),
                    reason=str(payload.get("reason", "")).strip() or None,
                    overrides=(
                        dict(payload["scope_overrides"])
                        if isinstance(payload.get("scope_overrides"), dict)
                        else None
                    ),
                    protocol_overrides=(
                        dict(payload["protocol_overrides"])
                        if isinstance(payload.get("protocol_overrides"), dict)
                        else None
                    ),
                )
                result["workflow"] = PersistentDAGScheduler(
                    repository, workflow_handlers()
                ).run(study_id)
                self._send_json(result)
                return
            if parsed.path == "/api/studies/stage2/protocol/revise":
                from .stage_two import revise_stage_two_protocol
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                overrides = payload.get("protocol_overrides")
                if not isinstance(overrides, dict):
                    raise ValueError("protocol_overrides must be an object")
                result = revise_stage_two_protocol(
                    repository,
                    study_id,
                    dict(overrides),
                    decided_by=str(
                        payload.get("decided_by", "project_owner")
                    ).strip(),
                    reason=(
                        str(payload.get("reason", "")).strip()
                        or "Revise the unfrozen Stage 2 protocol."
                    ),
                )
                result["workflow"] = PersistentDAGScheduler(
                    repository, workflow_handlers()
                ).run(study_id)
                self._send_json(result)
                return
            if parsed.path == "/api/studies/stage2/mvp/approve":
                from .stage_two import approve_stage_two_mvp
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                report_path = str(payload.get("report_path", "")).strip()
                if not report_path:
                    raise ValueError("report_path is required")
                result = approve_stage_two_mvp(
                    repository,
                    study_id,
                    report_path,
                    decided_by=str(
                        payload.get("decided_by", "project_owner")
                    ).strip(),
                )
                result["workflow"] = PersistentDAGScheduler(
                    repository, workflow_handlers()
                ).run(study_id)
                self._send_json(result)
                return
            if parsed.path == "/api/studies/stage2/approve":
                from .stage_two import approve_stage_two_contract
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                result = approve_stage_two_contract(
                    repository,
                    study_id,
                    decided_by=str(
                        payload.get("decided_by", "project_owner")
                    ).strip(),
                    reason=str(payload.get("reason", "")).strip() or None,
                )
                result["workflow"] = PersistentDAGScheduler(
                    repository, workflow_handlers()
                ).run(study_id)
                self._send_json(result)
                return
            if parsed.path == "/api/studies/stage2/amendments":
                from .stage_two import propose_stage_two_amendment
                from .workflow_domain import WorkflowRepository

                result = propose_stage_two_amendment(
                    WorkflowRepository(self.server.workflow_root),
                    str(payload.get("study_id", "")).strip(),
                    reason=str(payload.get("reason", "")).strip(),
                    changes=(
                        dict(payload["changes"])
                        if isinstance(payload.get("changes"), dict)
                        else {}
                    ),
                    impact_scope=[
                        str(item)
                        for item in payload.get("impact_scope", [])
                    ],
                    treatment_results_viewed_before_change=bool(
                        payload.get(
                            "treatment_results_viewed_before_change", False
                        )
                    ),
                    requires_rerun=bool(
                        payload.get("requires_rerun", True)
                    ),
                    exploratory_downgrade=bool(
                        payload.get("exploratory_downgrade", False)
                    ),
                    requires_owner_reapproval=bool(
                        payload.get("requires_owner_reapproval", True)
                    ),
                )
                self._send_json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/studies/gates/decide":
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    workflow_handlers,
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
                max_concurrency = 4
                if gate.subject_type == "stage3_run_plan":
                    max_concurrency = repository.load_run_plan(
                        study_id, gate.subject_id
                    ).concurrency
                workflow = PersistentDAGScheduler(
                    repository,
                    workflow_handlers(),
                    max_concurrency=max_concurrency,
                ).run(study_id)
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
                    parameters=(
                        dict(payload["parameters"])
                        if isinstance(payload.get("parameters"), dict)
                        else None
                    ),
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
                        workflow_handlers,
                    )

                    workflow = PersistentDAGScheduler(
                        repository,
                        workflow_handlers(),
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
                    workflow_handlers,
                )

                repository = WorkflowRepository(self.server.workflow_root)
                study_id = str(payload.get("study_id", "")).strip()
                plans = repository.list_run_plans(study_id)
                scheduler = PersistentDAGScheduler(
                    repository,
                    workflow_handlers(),
                    max_concurrency=(
                        plans[-1].concurrency if plans else 4
                    ),
                )
                if parsed.path.endswith("/retry-step"):
                    scheduler.retry_step(
                        study_id,
                        str(payload.get("step_id", "")).strip(),
                        authorized_by=(
                            str(payload.get("decided_by", "")).strip()
                            or None
                        ),
                        reason=(
                            str(payload.get("reason", "")).strip() or None
                        ),
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

                operation = str(payload.get("operation", "")).strip()
                task_payload = payload.get("payload") or {}
                if not isinstance(task_payload, dict):
                    raise ValueError("task payload must be a JSON object")
                task_payload = dict(task_payload)
                if operation == "bundle.inspect":
                    # ``workflow_root`` is deployment context, not research
                    # input. Never ask the user or browser to supply it.
                    task_payload["workflow_root"] = str(self.server.workflow_root)
                request = TaskRequest(
                    operation=operation,
                    payload=task_payload,
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
                from .models import utc_now
                from .retrieval.domain.models import (
                    NetworkMode,
                    ResourceType,
                    retrieval_id,
                )
                from .retrieval.interfaces.service import RetrievalGateway
                from .retrieval.policy.engine import RetrievalNetworkPolicy
                from .workflow_tasks import create_workflow_orchestrator

                task_id = str(payload.get("task_id", "")).strip()
                requirement_id = str(payload.get("requirement_id", "")).strip()
                resolution = payload.get("resolution") or {}
                if not isinstance(resolution, dict):
                    raise ValueError("requirement resolution must be a JSON object")
                orchestrator = create_workflow_orchestrator(self.server.task_root)
                current = orchestrator.load(task_id)
                requirement = next(
                    (
                        item
                        for item in current.requirements
                        if item.requirement_id == requirement_id
                    ),
                    None,
                )
                if requirement is None:
                    raise FileNotFoundError(
                        f"task requirement not found: {requirement_id}"
                    )
                if (
                    resolution.get("action_id") == "approve_public_research"
                    and resolution.get("approve_public_research") is True
                    and not resolution.get("alternative")
                ):
                    action = next(
                        (
                            item
                            for item in requirement.accepted_inputs
                            if item.get("action_id")
                            == "approve_public_research"
                        ),
                        {},
                    )
                    project_id = str(action.get("project_id", "")).strip()
                    if not project_id:
                        raise ValueError(
                            "public retrieval approval is missing project_id"
                        )
                    providers = {
                        "paper_search_mcp",
                        "semantic_scholar",
                        "crossref",
                        "github",
                        "huggingface",
                        "codex_native_web_search",
                        "openai_web_search",
                        "redfox_wechat",
                    }
                    RetrievalGateway(str(self.server.workflow_root)).set_policy(
                        project_id,
                        RetrievalNetworkPolicy(
                            policy_id=retrieval_id(
                                "network-policy",
                                project_id,
                                "public-research-owner-approved-v1",
                            ),
                            mode=NetworkMode.PUBLIC_RESEARCH,
                            allowed_providers=providers,
                            allowed_http_methods={"GET"},
                            allowed_resource_types=set(ResourceType),
                            allow_metadata=True,
                            allow_abstract=True,
                            allow_full_text=False,
                            max_queries=20,
                            max_results=200,
                            max_bytes=20_000_000,
                            max_cost=2.0,
                            approved_by="project_owner",
                            approved_at=utc_now(),
                        ),
                    )
                record = orchestrator.resolve_requirement(
                    task_id, requirement_id, resolution
                )
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
