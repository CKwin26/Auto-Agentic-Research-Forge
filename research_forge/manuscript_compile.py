from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from .manuscript_depth import audit_manuscript_depth


FINALIZATION_SCHEMA_VERSION = 1
FINALIZATION_GATE_ID = "manuscript-finalization-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _preferred_engine(source: Path, language: str) -> str:
    text = source.read_text(encoding="utf-8")
    if language == "zh" or "\\documentclass" in text and "ctex" in text:
        return "xelatex"
    return "pdflatex"


def _local_tectonic(source: Path) -> str | None:
    for parent in (source.parent, *source.parents):
        tool_root = parent / "tmp" / "tools"
        if not tool_root.is_dir():
            continue
        matches = sorted(tool_root.glob("tectonic-*/tectonic.exe"), reverse=True)
        if matches:
            return str(matches[0].resolve())
    return None


def _installed_miktex(engine: str) -> str | None:
    """Find a per-user/system MiKTeX install before the app is restarted."""

    executable = f"{engine}.exe"
    roots: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        roots.append(Path(local_app_data) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64")
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(variable, "").strip()
        if value:
            roots.append(Path(value) / "MiKTeX" / "miktex" / "bin" / "x64")
    for root in roots:
        candidate = root / executable
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def _resolve_compiler(source: Path, engine: str, language: str) -> tuple[str, str]:
    candidates = [engine] if engine != "auto" else [_preferred_engine(source, language), "tectonic"]
    for candidate in candidates:
        compiler = shutil.which(candidate)
        if compiler is None and candidate in {"pdflatex", "xelatex"}:
            compiler = _installed_miktex(candidate)
        if compiler is None and candidate == "tectonic":
            compiler = _local_tectonic(source)
        if compiler is not None:
            return candidate, compiler
    raise FileNotFoundError(
        "LaTeX compiler not found; install pdflatex/xelatex or provide Tectonic"
    )


def _publication_pdf_gate(source: Path, readiness_path: str | Path | None) -> dict[str, object] | None:
    """Apply the canonical release sequence to publication-matrix PDFs.

    Generic manuscripts retain the standalone depth gate.  The canonical
    publication source has a stricter project-aware gate so a direct compiler
    invocation cannot bypass experiment completion and readiness review.
    """
    if source.name != "publication_manuscript.tex" or source.parent.name != "synthesis":
        return None
    from .publication_synthesis import publication_layout_gate

    project = source.parent.parent
    readiness = (
        Path(readiness_path).resolve()
        if readiness_path is not None
        else source.parent / "publication_readiness.json"
    )
    if not readiness.is_file():
        raise ValueError(
            "final PDF blocked by publication release order: a fixed-venue "
            "publication_readiness.json is required"
        )
    return publication_layout_gate(readiness, project=project)


def finalize_manuscript_pdf(
    path: str | Path,
    *,
    output_dir: str | Path | None = None,
    profile: str = "journal-article",
    language: Literal["auto", "en", "zh"] = "auto",
    engine: Literal["auto", "pdflatex", "xelatex", "tectonic"] = "auto",
    passes: int = 2,
    report_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    readiness_path: str | Path | None = None,
) -> dict[str, object]:
    """Compile a final PDF only after the deterministic manuscript gate passes.

    Compilation happens in a temporary directory. The requested PDF and its
    finalization manifest are published only after every compiler pass succeeds.
    """

    source = Path(path).resolve()
    if source.suffix.lower() != ".tex":
        raise ValueError("final PDF generation requires a .tex manuscript")
    if not source.is_file():
        raise FileNotFoundError(f"manuscript not found: {source}")
    if passes < 1 or passes > 4:
        raise ValueError("compiler passes must be between 1 and 4")

    release_gate = _publication_pdf_gate(source, readiness_path)

    destination_dir = (
        Path(output_dir).resolve() if output_dir is not None else source.parent
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    depth_report = (
        Path(report_path).resolve()
        if report_path is not None
        else destination_dir / f"{source.stem}.depth.json"
    )
    finalization_manifest = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else destination_dir / f"{source.stem}.finalization.json"
    )

    audit = audit_manuscript_depth(
        source,
        profile=profile,
        language=language,
        report_path=depth_report,
    )
    if not audit.passed:
        raise ValueError(
            "final PDF blocked by manuscript depth gate: "
            + "; ".join(audit.violations)
        )

    selected_engine, compiler = _resolve_compiler(source, engine, audit.language)

    with tempfile.TemporaryDirectory(prefix="research-forge-finalize-") as temporary:
        staging = Path(temporary)
        logs: list[str] = []
        if selected_engine == "tectonic":
            commands = [
                [
                    compiler,
                    str(source),
                    "--outdir",
                    str(staging),
                    "--reruns",
                    str(passes - 1),
                    "--keep-logs",
                ]
            ]
        else:
            command = [
                compiler,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                f"-output-directory={staging}",
                str(source),
            ]
            commands = [command for _ in range(passes)]
        for pass_index, command in enumerate(commands, start=1):
            completed = subprocess.run(
                command,
                cwd=source.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            logs.append(completed.stdout + completed.stderr)
            if completed.returncode != 0:
                tail = logs[-1][-4000:]
                raise ValueError(
                    f"{selected_engine} pass {pass_index} failed with exit code "
                    f"{completed.returncode}:\n{tail}"
                )

        staged_pdf = staging / f"{source.stem}.pdf"
        if not staged_pdf.is_file() or staged_pdf.stat().st_size == 0:
            raise ValueError("LaTeX compiler completed without producing a non-empty PDF")

        destination_pdf = destination_dir / f"{source.stem}.pdf"
        staged_destination = destination_dir / f".{source.stem}.pdf.tmp"
        shutil.copy2(staged_pdf, staged_destination)
        os.replace(staged_destination, destination_pdf)

    manifest: dict[str, object] = {
        "schema_version": FINALIZATION_SCHEMA_VERSION,
        "gate_id": FINALIZATION_GATE_ID,
        "finalization_authorized": True,
        "source": str(source),
        "source_sha256": _sha256(source),
        "depth_profile": audit.profile,
        "depth_gate_id": audit.gate_id,
        "depth_report": str(depth_report),
        "depth_report_sha256": _sha256(depth_report),
        "language": audit.language,
        "narrative_count": audit.total_count,
        "narrative_unit": audit.unit,
        "compiler": selected_engine,
        "compiler_path": compiler,
        "compiler_passes": passes,
        "pdf": str(destination_pdf),
        "pdf_sha256": _sha256(destination_pdf),
    }
    if release_gate is not None:
        manifest["publication_release_gate"] = release_gate
    _write_json(finalization_manifest, manifest)
    manifest["manifest"] = str(finalization_manifest)
    manifest["manifest_sha256"] = _sha256(finalization_manifest)
    return manifest
