from __future__ import annotations

"""Render the Research Forge upstream-learning evidence audit."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research_forge.upstream_capabilities import (
    UPSTREAM_CAPABILITIES,
    audit_upstream_capabilities,
)


def _cell(items: list[str]) -> str:
    return "<br>".join(item.replace("|", "\\|") for item in items) or "—"


def render_markdown(repository_root: Path) -> str:
    report = audit_upstream_capabilities(repository_root)
    audited = {item.record_id: item for item in report.records}
    counts = Counter(item.status.value for item in UPSTREAM_CAPABILITIES)
    lines = [
        "# Research Forge upstream capability revalidation",
        "",
        "This report distinguishes upstream capability, locally learned behavior, "
        "platform evidence, and current adoption boundary. A source being reviewed "
        "does not by itself make it a Research Forge runtime dependency.",
        "",
        "## Machine audit",
        "",
        f"- Records: {report.record_count}",
        f"- Claims supported by their declared evidence level: {report.supported_count}",
        f"- Unsupported registry claims: {report.unsupported_count}",
        "- Status counts: "
        + ", ".join(f"`{key}`={value}" for key, value in sorted(counts.items())),
        "",
        "## Source-by-source result",
        "",
        "| Source | Status | What upstream provides | What Research Forge learned | "
        "Evidence / remaining boundary |",
        "|---|---|---|---|---|",
    ]
    for item in UPSTREAM_CAPABILITIES:
        check = audited[item.record_id]
        evidence = [
            *item.research_forge_evidence,
            *(
                [f"real run: {value}" for value in item.real_run_evidence]
                if item.real_run_evidence
                else []
            ),
        ]
        boundary = [
            *item.missing_or_bounded,
            f"permitted claim: {item.permitted_claim}",
        ]
        if check.findings:
            boundary.extend(f"audit finding: {value}" for value in check.findings)
        lines.append(
            "| [{name}]({url}) | `{status}` | {upstream} | {learned} | "
            "**Evidence:** {evidence}<br>**Boundary:** {boundary} |".format(
                name=item.name.replace("|", "\\|"),
                url=item.source_url,
                status=item.status.value,
                upstream=_cell(item.upstream_capabilities),
                learned=_cell(item.learned_capabilities),
                evidence=_cell(evidence),
                boundary=_cell(boundary),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `verified_runtime` requires integration tests, a durable real-run "
            "record, and a revalidation date.",
            "- `partial_runtime` proves platform code and tests, but not a current "
            "complete external acceptance.",
            "- `adapter_only` means Research Forge can process a supplied upstream "
            "artifact; it does not execute the upstream tool.",
            "- `architecture_only` and `codex_skill_only` must never be presented as "
            "platform runtime features.",
            "- `stub_or_missing` is an explicit negative result, not a successful "
            "integration.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    root = args.repository_root.resolve()
    report = audit_upstream_capabilities(root)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(
            render_markdown(root),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "record_count": report.record_count,
                "supported_count": report.supported_count,
                "unsupported_count": report.unsupported_count,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.unsupported_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
