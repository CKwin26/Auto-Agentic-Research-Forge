from __future__ import annotations

from pathlib import Path

from scripts.run_second_case_validation import run


def test_sick_second_case_runs_without_publication_specific_core(tmp_path: Path) -> None:
    report = run(tmp_path / "sick-lexical-replication")
    project = Path(str(report["project"]))
    assert report["baseline"]["valid"]
    assert report["candidate"]["valid"]
    assert report["completion_verification"]["passed"]
    assert (project / "pipeline_manifest.json").is_file()
    assert (project / "synthesis" / "manuscript.md").is_file()
    assert not (project / "stage2").exists()
