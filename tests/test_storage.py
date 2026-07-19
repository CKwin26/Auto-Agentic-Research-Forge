from __future__ import annotations

from pathlib import Path

import pytest

from research_forge.storage import safe_relative, slugify


def test_safe_relative_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_relative(tmp_path, "../outside.txt")
    with pytest.raises(ValueError):
        safe_relative(tmp_path, str((tmp_path.parent / "outside.txt").resolve()))


def test_non_ascii_name_gets_stable_slug() -> None:
    first = slugify("透明物体视觉")
    second = slugify("透明物体视觉")
    assert first == second
    assert first.startswith("project-")
