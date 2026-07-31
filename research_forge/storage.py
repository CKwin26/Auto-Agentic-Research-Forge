from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from .models import ProjectMeta, ProjectState


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACES = ROOT / "workspaces"
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower().replace("_", "-")).strip("-")
    if not slug:
        digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:10]
        slug = f"project-{digest}"
    return slug[:80]


def resolve_workspace_root(value: str | Path | None = None) -> Path:
    return Path(value or os.getenv("RESEARCH_FORGE_HOME") or DEFAULT_WORKSPACES).resolve()


def project_dir(slug: str, root: str | Path | None = None, *, must_exist: bool = True) -> Path:
    base = resolve_workspace_root(root)
    candidate = (base / slugify(slug)).resolve()
    ensure_within(base, candidate)
    if must_exist and not candidate.is_dir():
        raise FileNotFoundError(f"project not found: {candidate}")
    return candidate


def ensure_within(base: Path, candidate: Path) -> Path:
    base_resolved = base.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != base_resolved and base_resolved not in candidate_resolved.parents:
        raise ValueError(f"path escapes allowed root: {candidate}")
    return candidate_resolved


def safe_relative(base: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or any(part in {"..", ""} for part in rel.parts):
        raise ValueError(f"unsafe relative path: {relative}")
    return ensure_within(base, base / rel)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def write_json_atomic(path: Path, value: Any) -> None:
    with _path_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            _jsonable(value), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(8):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    # Windows indexers and antivirus scanners can briefly
                    # retain a handle. The in-process path lock removes normal
                    # scheduler read/write contention; this retry covers
                    # external scanners without weakening atomic replacement.
                    time.sleep(0.01 * (2**attempt))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def write_text_atomic(path: Path, value: str) -> None:
    with _path_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(8):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    time.sleep(0.01 * (2**attempt))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def append_jsonl(path: Path, value: Any) -> None:
    with _path_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def read_json(path: Path) -> dict[str, Any]:
    with _path_lock(path):
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def read_model(path: Path, model_type: type[BaseModel]) -> BaseModel:
    return model_type.model_validate(read_json(path))


def load_meta(project: Path) -> ProjectMeta:
    return ProjectMeta.model_validate(read_json(project / "project.json"))


def load_state(project: Path) -> ProjectState:
    return ProjectState.model_validate(read_json(project / "state.json"))


def save_state(project: Path, state: ProjectState) -> None:
    state.revision += 1
    from .models import utc_now

    state.updated_at = utc_now()
    write_json_atomic(project / "state.json", state)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path, *, suffixes: Iterable[str] | None = None) -> str:
    digest = hashlib.sha256()
    allowed = set(suffixes) if suffixes else None
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if allowed is not None and path.suffix.lower() not in allowed:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            records.append(value)
    return records
