from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from research_forge.models import utc_now
from research_forge.storage import append_jsonl, read_json, write_json_atomic


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve a contaminated ablation cell and reopen it for a clean run."
    )
    parser.add_argument("matrix")
    parser.add_argument("cell_id")
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    workspace = Path(__file__).resolve().parents[1]
    matrix = Path(args.matrix).resolve()
    if not _within(matrix, workspace):
        raise RuntimeError("matrix must stay within the Research Forge workspace")

    manifest = read_json(matrix / "matrix.json")
    state_path = matrix / "ablation_state.json"
    state = read_json(state_path)
    matches = [cell for cell in state["cells"] if cell["cell_id"] == args.cell_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one matrix cell named {args.cell_id}")
    cell = matches[0]

    cell_root = (
        Path(manifest["cell_storage"]["root"])
        / str(manifest["manifest_sha256"])[:12]
        / f"{int(cell['order']):02d}"
    ).resolve()
    manifests = sorted(cell_root.glob("*/loop_manifest.json"))
    if len(manifests) != 1:
        raise RuntimeError(
            f"expected exactly one run-loop output under {cell_root}, found {len(manifests)}"
        )
    source = manifests[0].parent.resolve()
    destination = (
        matrix / "excluded-cells" / str(cell["cell_id"]) / source.name
    ).resolve()
    if not _within(source, workspace) or not _within(destination, workspace):
        raise RuntimeError("source and quarantine destination must stay within the workspace")
    if destination.exists():
        raise FileExistsError(f"quarantine destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))

    cell["status"] = "pending"
    cell["output"] = None
    cell["result"] = None
    cell["error"] = None
    state["status"] = "running"
    state["updated_at"] = utc_now()
    write_json_atomic(state_path, state)
    append_jsonl(
        matrix / "events.jsonl",
        {
            "recorded_at": utc_now(),
            "event": "cell_quarantined_for_clean_rerun",
            "cell_id": cell["cell_id"],
            "reason": args.reason,
            "source": str(source),
            "destination": str(destination),
        },
    )
    print(
        json.dumps(
            {
                "cell_id": cell["cell_id"],
                "status": cell["status"],
                "source": str(source),
                "quarantine": str(destination),
                "reason": args.reason,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
