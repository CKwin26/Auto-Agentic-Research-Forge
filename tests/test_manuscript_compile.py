from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from research_forge.manuscript_compile import finalize_manuscript_pdf


ROOT = Path(__file__).resolve().parents[1]


def test_finalization_blocks_before_compiler_when_depth_gate_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler_called = False

    def unexpected_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal compiler_called
        compiler_called = True
        raise AssertionError("compiler must not run after a failed depth gate")

    monkeypatch.setattr("research_forge.manuscript_compile.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="final PDF blocked"):
        finalize_manuscript_pdf(
            ROOT / "output" / "pdf" / "research-agent-evidence-v3-paper-en.tex",
            output_dir=tmp_path,
        )

    assert not compiler_called
    assert not list(tmp_path.glob("*.pdf"))
    assert list(tmp_path.glob("*.depth.json"))
    assert not list(tmp_path.glob("*.finalization.json"))


def test_finalization_publishes_pdf_and_hash_manifest_only_after_compile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ROOT / "output" / "pdf" / "research-agent-evidence-v4-paper-en.tex"
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "research_forge.manuscript_compile.shutil.which",
        lambda engine: f"/mock/{engine}",
    )

    def successful_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output_argument = next(
            item for item in command if item.startswith("-output-directory=")
        )
        staging = Path(output_argument.split("=", 1)[1])
        (staging / f"{source.stem}.pdf").write_bytes(b"%PDF-1.7\nmock\n")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("research_forge.manuscript_compile.subprocess.run", successful_run)

    result = finalize_manuscript_pdf(source, output_dir=tmp_path, passes=2)

    assert len(calls) == 2
    assert result["finalization_authorized"] is True
    assert (tmp_path / f"{source.stem}.pdf").is_file()
    assert (tmp_path / f"{source.stem}.depth.json").is_file()
    assert (tmp_path / f"{source.stem}.finalization.json").is_file()
    assert result["source_sha256"]
    assert result["pdf_sha256"]
