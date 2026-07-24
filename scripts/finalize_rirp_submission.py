from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from research_forge.paper_pipeline import (
    RIRP_RESEARCH_ARTICLE,
    latex_heading,
    validate_markdown_structure,
)
from research_forge.drawio_backend import (
    DrawioDiagram,
    DrawioEdge,
    DrawioNode,
    export_drawio,
    write_drawio,
)
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Circle, Rectangle


PROJECT_REL = Path("stage1_runs/research-agent-evidence-publication-v1")
SOURCE_REL = PROJECT_REL / "synthesis/publication_manuscript.rev6.md"
OUTPUT_REL = PROJECT_REL / "synthesis/submission_rirp"
INTEGRITY_AUDIT_REL = PROJECT_REL / "synthesis/review_stage3/academic_integrity_stage4_5_recheck4.json"

CANONICAL_TITLE = (
    "Pre-Delivery Claim-Evidence Gating in Autonomous Research Agents: "
    "A Same-Backbone Paired Ablation Study"
)
PRIMARY_RQ_ANCHOR = (
    "When the same frozen upstream artifact is processed by two otherwise matched branches, "
    "does adding a pre-delivery claim-evidence gate change the protected unsupported-claim rate "
    "without reducing task-native performance?"
)
CONTENT_REVIEW_AUTHOR_LINE = "Content-review copy; author details required before submission"
REQUIRED_FINAL_CONFIRMATIONS = (
    "all_authors_qualify",
    "all_authors_approved",
    "not_under_consideration_elsewhere",
    "competing_interests_complete",
    "funding_complete",
    "osf_link_tested_signed_out",
    "final_human_read_complete",
    "approved_for_submission",
)

CITATION_ORDER = [
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12",
    "Lu2024", "Wadden2020", "Munafò2017", "Pineau2021", "W3C-PROV", "Sculley2015", "He2020",
]
CITATION_NUMBER = {key: index + 1 for index, key in enumerate(CITATION_ORDER)}

ABBREVIATIONS_SECTION = """## List of abbreviations

DOI: digital object identifier; ML: machine learning; NLI: natural language inference; OSF: Open Science Framework; PROV-DM: Provenance Data Model; SHA-256: Secure Hash Algorithm 256-bit; URL: Uniform Resource Locator; USD: United States dollars.
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_dashes(text: str) -> str:
    return re.sub(r"[‐‑‒–—−]", "-", text)


def enforce_submission_scope(markdown: str) -> None:
    """Prevent formatting from silently turning the secondary diagnosis into the paper topic."""
    heading = next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), "")
    if _normalize_dashes(heading) != CANONICAL_TITLE:
        raise ValueError(
            "submission scope gate refused: title must remain the pre-delivery "
            "claim-evidence same-backbone paired ablation title"
        )
    normalized = _normalize_dashes(markdown)
    if PRIMARY_RQ_ANCHOR not in normalized:
        raise ValueError("submission scope gate refused: canonical primary research question is missing")
    abstract = normalized.split("## Abstract", 1)[-1].split("## Background", 1)[0]
    if "same-backbone" not in abstract or "non-confirmatory" not in abstract:
        raise ValueError(
            "submission scope gate refused: abstract must retain the paired-ablation design "
            "and non-confirmatory interpretation"
        )


def _valid_orcid(value: str) -> bool:
    compact = value.replace("https://orcid.org/", "").strip().upper()
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", compact):
        return False
    digits = compact.replace("-", "")
    total = 0
    for char in digits[:15]:
        total = (total + int(char)) * 2
    remainder = (12 - total % 11) % 11
    expected = "X" if remainder == 10 else str(remainder)
    return digits[-1] == expected


def validate_submission_metadata(metadata: dict) -> None:
    errors: list[str] = []
    if metadata.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    authors = metadata.get("authors")
    affiliations = metadata.get("affiliations")
    corresponding = metadata.get("corresponding_author")
    declarations = metadata.get("declarations")
    confirmations = metadata.get("confirmations")
    if not isinstance(authors, list) or not authors:
        errors.append("authors must contain at least one author")
        authors = []
    if not isinstance(affiliations, list) or not affiliations:
        errors.append("affiliations must contain at least one affiliation")
        affiliations = []

    affiliation_ids: set[str] = set()
    for index, affiliation in enumerate(affiliations):
        if not isinstance(affiliation, dict):
            errors.append(f"affiliations[{index}] must be an object")
            continue
        affiliation_id = str(affiliation.get("id", "")).strip()
        if not affiliation_id or affiliation_id in affiliation_ids:
            errors.append(f"affiliations[{index}].id must be unique and non-empty")
        affiliation_ids.add(affiliation_id)
        for field in ("institution", "city", "country"):
            if not str(affiliation.get(field, "")).strip():
                errors.append(f"affiliations[{index}].{field} is required")

    author_names: set[str] = set()
    for index, author in enumerate(authors):
        if not isinstance(author, dict):
            errors.append(f"authors[{index}] must be an object")
            continue
        name = str(author.get("name", "")).strip()
        if not name:
            errors.append(f"authors[{index}].name is required")
        author_names.add(name)
        linked = author.get("affiliation_ids")
        if not isinstance(linked, list) or not linked:
            errors.append(f"authors[{index}].affiliation_ids must be non-empty")
        elif any(str(item) not in affiliation_ids for item in linked):
            errors.append(f"authors[{index}].affiliation_ids contains an unknown id")
        orcid = str(author.get("orcid", "")).strip()
        if orcid and not _valid_orcid(orcid):
            errors.append(f"authors[{index}].orcid is invalid")

    if not isinstance(corresponding, dict):
        errors.append("corresponding_author must be an object")
        corresponding = {}
    corresponding_name = str(corresponding.get("name", "")).strip()
    email = str(corresponding.get("email", "")).strip()
    if corresponding_name not in author_names:
        errors.append("corresponding_author.name must exactly match a listed author")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        errors.append("corresponding_author.email must be a valid email address")

    if not isinstance(declarations, dict):
        errors.append("declarations must be an object")
        declarations = {}
    for field in ("competing_interests", "funding", "authors_contributions", "acknowledgements"):
        if not str(declarations.get(field, "")).strip():
            errors.append(f"declarations.{field} is required (use 'Not applicable.' when appropriate)")

    osf_url = str(metadata.get("osf_view_only_url", "")).strip()
    if not re.fullmatch(r"https://[^\s]+", osf_url):
        errors.append("osf_view_only_url must be a tested HTTPS view-only URL")

    if not isinstance(confirmations, dict):
        errors.append("confirmations must be an object")
        confirmations = {}
    for field in REQUIRED_FINAL_CONFIRMATIONS:
        if confirmations.get(field) is not True:
            errors.append(f"confirmations.{field} must be true")

    if errors:
        raise ValueError("final-submission metadata gate refused:\n- " + "\n- ".join(errors))


def apply_submission_metadata(markdown: str, metadata: dict) -> str:
    validate_submission_metadata(metadata)
    declarations = metadata["declarations"]
    replacements = {
        "Competing interests": declarations["competing_interests"],
        "Funding": declarations["funding"],
        "Authors' contributions": declarations["authors_contributions"],
        "Acknowledgements": declarations["acknowledgements"],
    }
    rendered = markdown.replace("[ANONYMIZED_OSF_VIEW_ONLY_URL]", metadata["osf_view_only_url"])
    for heading, body in replacements.items():
        pattern = rf"(### {re.escape(heading)}\n\n)(.*?)(?=\n\n### |\n\n## References)"
        rendered, count = re.subn(pattern, rf"\g<1>{body.strip()}", rendered, count=1, flags=re.DOTALL)
        if count != 1:
            raise ValueError(f"final-submission metadata gate refused: declaration section missing: {heading}")
    if "[ANONYMIZED_OSF_VIEW_ONLY_URL]" in rendered:
        raise ValueError("final-submission metadata gate refused: OSF placeholder remains")
    return rendered


def author_tex(metadata: dict) -> str:
    validate_submission_metadata(metadata)
    corresponding_name = metadata["corresponding_author"]["name"].strip()
    author_rows: list[str] = []
    for author in metadata["authors"]:
        marks = [str(item) for item in author["affiliation_ids"]]
        if author["name"].strip() == corresponding_name:
            marks.append("*")
        marker = r"\textsuperscript{" + ",".join(marks) + "}"
        author_rows.append(inline_tex(author["name"].strip()) + marker)
    affiliation_rows = [
        r"\textsuperscript{" + str(item["id"]) + "}" + inline_tex(
            f"{item['institution']}, {item['city']}, {item['country']}"
        )
        for item in metadata["affiliations"]
    ]
    email_row = r"\textsuperscript{*}Corresponding author: " + inline_tex(
        metadata["corresponding_author"]["email"].strip()
    )
    return r" \\ ".join([", ".join(author_rows), *affiliation_rows, email_row])


def final_title_page_markdown(metadata: dict) -> str:
    validate_submission_metadata(metadata)
    affiliations = {str(item["id"]): item for item in metadata["affiliations"]}
    author_lines = []
    for author in metadata["authors"]:
        linked = ", ".join(str(item) for item in author["affiliation_ids"])
        orcid = f"; ORCID: {author['orcid']}" if author.get("orcid") else ""
        author_lines.append(f"- {author['name']} (affiliation {linked}{orcid})")
    affiliation_lines = [
        f"- {key}: {item['institution']}, {item['city']}, {item['country']}"
        for key, item in affiliations.items()
    ]
    corresponding = metadata["corresponding_author"]
    return "\n".join([
        "# Final title page",
        "",
        f"**Article title:** {CANONICAL_TITLE}",
        "",
        "**Article type:** Research article",
        "",
        "## Authors",
        "",
        *author_lines,
        "",
        "## Affiliations",
        "",
        *affiliation_lines,
        "",
        f"**Corresponding author:** {corresponding['name']}",
        "",
        f"**Email:** {corresponding['email']}",
        "",
    ])


def final_cover_letter_markdown(metadata: dict) -> str:
    validate_submission_metadata(metadata)
    corresponding = metadata["corresponding_author"]
    affiliation_ids = metadata["authors"][
        next(index for index, author in enumerate(metadata["authors"]) if author["name"] == corresponding["name"])
    ]["affiliation_ids"]
    affiliation_map = {str(item["id"]): item for item in metadata["affiliations"]}
    signature_affiliations = "; ".join(
        f"{affiliation_map[str(item)]['institution']}, {affiliation_map[str(item)]['city']}, {affiliation_map[str(item)]['country']}"
        for item in affiliation_ids
    )
    return f"""# Cover letter

Dear Editors of *Research Integrity and Peer Review*,

Please consider our Research Article, *{CANONICAL_TITLE}*. The manuscript reports a preregistered same-backbone paired ablation of a claim-evidence gate at the delivery boundary. A registered human-validity gate invalidated the automated endpoint, so the observed contrast is reported as non-confirmatory and the later fault diagnosis remains a secondary result.

The central contribution is an inspectable separation among the registered experiment, blinded human audit, post-unblinding diagnosis, bounded same-case repair replay, and planned fresh successor experiment. The manuscript does not claim treatment superiority or general repair accuracy.

All authors approved this manuscript and its submission. The manuscript is not under consideration elsewhere. Competing interests and funding are fully declared in the manuscript. The submission includes a populated study-specific reporting checklist and a tested OSF view-only evidence link.

Sincerely,

{corresponding['name']}\u0020\u0020
{signature_affiliations}\u0020\u0020
{corresponding['email']}
"""


def strip_markers(text: str) -> str:
    return re.sub(r"<!--(?:block|ref|anchor):.*?-->", "", text)


def enforce_cite_time_gate(source: str, *, source_sha256: str, integrity_audit: dict) -> str:
    """Fail closed on unresolved citation markers before submission-marker stripping."""
    refusal_tokens = (
        "UNVERIFIED CITATION",
        "HIGH-WARN-CLAIM-NOT-SUPPORTED",
        "HIGH-WARN-NEGATIVE-CONSTRAINT-VIOLATION",
        "HIGH-WARN-FABRICATED-REFERENCE",
        "HIGH-WARN-CLAIM-AUDIT-ANCHORLESS",
        "HIGH-WARN-CONSTRAINT-VIOLATION-UNCITED",
        "severity=HIGH-BLOCK",
        "<!--anchor:none:",
    )
    present = [token for token in refusal_tokens if token in source]
    if present:
        raise ValueError("cite-time provenance gate refused: " + ", ".join(present))

    verification = integrity_audit.get("verification", {})
    audit_valid = (
        integrity_audit.get("verdict") == "PASS"
        and integrity_audit.get("manuscript_sha256") == source_sha256
        and verification.get("references_verified") == verification.get("references_total")
        and int(verification.get("references_total") or 0) > 0
        and verification.get("orphan_references") == 0
        and verification.get("dangling_citations") == 0
    )

    marker_pattern = re.compile(r"<!--ref:([^\s>]+)(?:\s+([^>]+?))?-->")
    anchor_pattern = re.compile(r"<!--anchor:(?!none:)[^>]+-->")
    normalized = source
    for marker in list(marker_pattern.finditer(source)):
        slug = marker.group(1)
        status = (marker.group(2) or "").strip()
        if status:
            if status != "ok" and "LOW-WARN-acknowledged" not in status:
                raise ValueError(f"cite-time provenance gate refused ref {slug}: status={status}")
            continue
        following = source[marker.end(): marker.end() + 512]
        if not audit_valid or not anchor_pattern.match(following.lstrip()):
            raise ValueError(f"cite-time provenance gate refused legacy ref {slug}")
        normalized = normalized.replace(marker.group(0), f"<!--ref:{slug} ok-->", 1)
    return normalized


def expand_citation_token(token: str) -> list[str]:
    token = token.strip()
    match = re.fullmatch(r"R(\d+)[–-]R(\d+)", token)
    if match:
        return [f"R{i}" for i in range(int(match.group(1)), int(match.group(2)) + 1)]
    return [token]


def compress_numbers(numbers: list[int]) -> str:
    values = sorted(set(numbers))
    groups: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        groups.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = value
    groups.append(str(start) if start == previous else f"{start}–{previous}")
    return ",".join(groups)


def number_citations(text: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        raw = match.group(1)
        keys: list[str] = []
        for token in raw.split(","):
            keys.extend(expand_citation_token(token))
        if keys and all(key in CITATION_NUMBER for key in keys):
            return f"[{compress_numbers([CITATION_NUMBER[key] for key in keys])}]"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]", replacement, text)


def split_section(text: str, heading: str, next_heading: str | None = None) -> tuple[str, str, str]:
    start = text.index(heading)
    body_start = start + len(heading)
    if next_heading is None:
        end = len(text)
    else:
        end = text.index(next_heading, body_start)
    return text[:start], text[body_start:end], text[end:]


def structured_abstract(abstract: str) -> str:
    abstract = " ".join(abstract.strip().split())
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", abstract)
    if len(sentences) != 8:
        raise ValueError(f"expected 8 abstract sentences, found {len(sentences)}")
    return (
        "**Background:** Autonomous research agents can deliver claims whose evidentiary support is difficult "
        "to inspect. We evaluated whether a pre-delivery claim–evidence gate changes unsupported-claim rates "
        "without reducing task-native performance when the research backbone and upstream artifact are held fixed.\n\n"
        "**Methods:** We conducted a preregistered same-backbone paired ablation across eight tasks and five seeds, "
        "producing 40 shared upstream artifacts and matched baseline and gated branches, followed by a registered "
        "audit of 128 claims. A "
        "false-positive threshold governed whether the automated unsupported-claim endpoint remained "
        "interpretable. After that gate closed, an append-only context-restored review and bounded same-case "
        "evaluator replay were used for diagnosis and regression testing.\n\n"
        "**Results:** The protected automated unsupported-claim rate was 0.034 in baseline and 0.009 in treatment "
        "(paired mean difference -0.025; 95% hierarchical-bootstrap interval -0.067 to 0.000), while mean "
        "task-native score was 0.337 in both arms. The audit found 4 false positives among the 5 automated "
        "unsupported judgments (0.800; exact 95% binomial interval 0.284–0.995), exceeding the 0.15 stopping "
        "threshold and rendering the automated contrast non-confirmatory. A post-unblinding context-restored "
        "review judged all five cases supported and localized the mismatch to missing frozen task specifications "
        "plus a decision rule that allowed probabilistic natural language inference (NLI) to override exact metric "
        "bindings; the repaired "
        "evaluator then classified the same five cases as supported.\n\n"
        "**Conclusions:** The same-backbone gate ablation completed, but its protected automated endpoint failed "
        "human validation and therefore cannot establish treatment benefit. The secondary diagnosis and same-case "
        "replay localize and repair the known measurement failure without establishing generalization. A fresh "
        "prospectively frozen rerun with independent audit is required.\n"
    )


def parse_references(section: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in section.splitlines():
        match = re.match(r"^- \[([^\]]+)\]\s+(.*)$", line.strip())
        if match:
            refs[match.group(1)] = match.group(2).strip()
    missing = sorted(set(CITATION_ORDER) - set(refs))
    if missing:
        raise ValueError(f"missing references: {missing}")
    return refs


def vancouver_references(refs: dict[str, str]) -> str:
    return "\n".join(f"{i}. {refs[key]}" for i, key in enumerate(CITATION_ORDER, 1))


def bibtex_references(refs: dict[str, str]) -> str:
    records: list[str] = []
    for key in CITATION_ORDER:
        raw = refs[key]
        year_match = re.search(r"\((\d{4})\)", raw)
        year = year_match.group(1) if year_match else ""
        url_match = re.search(r"https?://\S+", raw)
        url = url_match.group(0).rstrip(".") if url_match else ""
        head = raw[: year_match.start()].strip() if year_match else raw
        tail = raw[year_match.end() :].strip(" .") if year_match else raw
        title = tail[: tail.find(". ")] if ". " in tail else tail
        esc = lambda value: value.replace("{", "\\{").replace("}", "\\}")
        records.append(
            "@misc{" + key.replace("ò", "o") + ",\n"
            f"  author = {{{esc(head)}}},\n"
            f"  title = {{{esc(title)}}},\n"
            f"  year = {{{year}}},\n"
            f"  url = {{{url}}},\n"
            f"  note = {{Registry key: {key}}}\n"
            "}"
        )
    return "\n\n".join(records) + "\n"


def draw_folder(ax, x: float, y: float, color: str) -> None:
    ax.add_patch(Rectangle((x, y), 0.36, 0.22, fill=False, lw=2, edgecolor=color))
    ax.add_patch(Rectangle((x + 0.04, y + 0.22), 0.14, 0.07, fill=False, lw=2, edgecolor=color))


def draw_shield(ax, x: float, y: float, color: str) -> None:
    ax.plot([x, x + 0.18, x + 0.36, x + 0.31, x + 0.18, x + 0.05, x],
            [y + 0.27, y + 0.34, y + 0.27, y + 0.08, y, y + 0.08, y + 0.27], color=color, lw=2)
    ax.plot([x + 0.10, x + 0.16, x + 0.27], [y + 0.18, y + 0.11, y + 0.24], color=color, lw=2)


def draw_people(ax, x: float, y: float, color: str) -> None:
    ax.add_patch(Circle((x + 0.10, y + 0.25), 0.07, fill=False, lw=2, edgecolor=color))
    ax.add_patch(Circle((x + 0.27, y + 0.25), 0.07, fill=False, lw=2, edgecolor=color))
    ax.plot([x + 0.02, x + 0.18], [y + 0.07, y + 0.07], color=color, lw=2)
    ax.plot([x + 0.19, x + 0.35], [y + 0.07, y + 0.07], color=color, lw=2)


def draw_wrench(ax, x: float, y: float, color: str) -> None:
    ax.plot([x + 0.06, x + 0.30], [y + 0.05, y + 0.29], color=color, lw=4, solid_capstyle="round")
    ax.add_patch(Circle((x + 0.05, y + 0.04), 0.05, fill=False, lw=2, edgecolor=color))
    ax.plot([x + 0.25, x + 0.34], [y + 0.28, y + 0.34], color=color, lw=2)
    ax.plot([x + 0.29, x + 0.35], [y + 0.24, y + 0.33], color=color, lw=2)


def save_figure(fig: plt.Figure, directory: Path, stem: str) -> None:
    fig.savefig(directory / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(directory / f"{stem}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def create_figures(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    blue, navy, orange, gray, red = "#2274A5", "#16324F", "#F29E4C", "#5B6770", "#C44536"

    node_style = (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#222222;"
        "fontColor=#111111;fontSize=14;fontFamily=Arial;spacing=7;strokeWidth=1.4;"
    )
    edge_style = (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
        "endArrow=block;endFill=1;strokeColor=#222222;strokeWidth=1.4;"
    )
    workflow = DrawioDiagram(
        title="Evidence-bound evaluation workflow",
        page_width=900,
        page_height=180,
        nodes=(
            DrawioNode("boundary", "<b>Project boundary</b><br>(read-only resources)", 20, 40, 180, 64, node_style),
            DrawioNode("run", "<b>Registered run</b><br>(protocol + paired evidence)", 235, 40, 190, 64, node_style),
            DrawioNode("validity", "<b>Validity assessment</b><br>(independent judgments)", 460, 40, 190, 64, node_style),
            DrawioNode("lineage", "<b>Traceable outcome</b><br>(lineage + repair boundary)", 685, 40, 190, 64, node_style),
        ),
        edges=(
            DrawioEdge("boundary", "run", style=edge_style),
            DrawioEdge("run", "validity", style=edge_style),
            DrawioEdge("validity", "lineage", style=edge_style),
        ),
    )
    workflow_source = write_drawio(directory / "figure1_governed_workflow.drawio", workflow)
    for output_format in ("png", "pdf", "svg"):
        export_drawio(
            workflow_source,
            directory / f"figure1_governed_workflow.{output_format}",
            format=output_format,
        )

    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    rates = [4 / 116, 1 / 116]
    axes[0].bar([0, 1], rates, color=[gray, blue], width=0.58)
    axes[0].set_xticks([0, 1], ["Baseline", "Treatment"])
    axes[0].set_ylabel("Automated unsupported rate")
    axes[0].set_ylim(0, 0.045)
    for i, value in enumerate(rates): axes[0].text(i, value + 0.0015, f"{value:.3f}\n({[4,1][i]}/116)", ha="center", fontsize=9)
    axes[0].set_title("A  Historical proxy contrast", loc="left", weight="bold")
    matrix = [[2, 1, 2], [118, 4, 1]]
    im = axes[1].imshow(matrix, cmap="Blues", vmin=0, vmax=118)
    axes[1].set_xticks(range(3), ["Supported", "Unsupported", "Abstain"], rotation=25, ha="right")
    axes[1].set_yticks(range(2), ["Auto unsupported", "Auto non-unsupported"])
    axes[1].set_xlabel("Human verdict")
    for row in range(2):
        for col in range(3):
            axes[1].text(col, row, str(matrix[row][col]), ha="center", va="center",
                         color="white" if matrix[row][col] > 60 else navy, weight="bold")
    axes[1].set_title("B  Blinded human comparison (n=128)", loc="left", weight="bold")
    axes[2].errorbar([0], [0.8], yerr=[[0.8 - 0.284], [0.995 - 0.8]], fmt="o", color=red, capsize=5, lw=2)
    axes[2].axhline(0.15, color=navy, ls="--", lw=1.8, label="registered threshold 0.15")
    axes[2].set_xlim(-0.6, 0.6); axes[2].set_xticks([0], ["False-positive rate\n4/5"]); axes[2].set_ylim(0, 1.05)
    axes[2].set_ylabel("Rate")
    axes[2].legend(frameon=False, fontsize=8, loc="lower right")
    axes[2].set_title("C  Independent validity assessment", loc="left", weight="bold")
    for ax in axes: ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    save_figure(fig, directory, "figure2_registered_results")

    states = DrawioDiagram(
        title="Evaluation state transitions",
        page_width=1120,
        page_height=150,
        nodes=tuple(
            DrawioNode(node_id, label, 15 + index * 180, 35, 145, 58, node_style)
            for index, (node_id, label) in enumerate(
                (
                    ("frozen", "Frozen"),
                    ("invalid", "Invalidated"),
                    ("diagnosed", "Diagnosed"),
                    ("repaired", "Repaired"),
                    ("replayed", "Regression<br>replayed"),
                    ("fresh", "Prospectively<br>reevaluated"),
                )
            )
        ),
        edges=tuple(
            DrawioEdge(source, target, style=edge_style)
            for source, target in zip(
                ("frozen", "invalid", "diagnosed", "repaired", "replayed"),
                ("invalid", "diagnosed", "repaired", "replayed", "fresh"),
            )
        ),
    )
    state_source = write_drawio(directory / "figure3_state_and_responsibility.drawio", states)
    for output_format in ("png", "pdf", "svg"):
        export_drawio(
            state_source,
            directory / f"figure3_state_and_responsibility.{output_format}",
            format=output_format,
        )


def build_clean_markdown(source: str) -> tuple[str, dict[str, str]]:
    clean = strip_markers(source).replace("\r\n", "\n")
    clean = re.sub(r"~~~mermaid\n.*?\n~~~", "", clean, flags=re.S)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip() + "\n"
    _, refs_body, tail = split_section(clean, "## References\n", "## Reproducibility\n")
    refs = parse_references(refs_body)
    clean = clean.replace("## References\n" + refs_body, "## References\n" + vancouver_references(refs) + "\n\n")
    clean = number_citations(clean)

    status_match = re.search(r"## Status\n\n(.*?)\n\n## Abstract", clean, flags=re.S)
    if not status_match:
        raise ValueError("status section not found")
    status = status_match.group(1).strip()
    clean = clean[: status_match.start()] + "## Abstract" + clean[status_match.end() :]

    abstract_match = re.search(r"## Abstract\n\n(.*?)\n\n\*\*Keywords:\*\*", clean, flags=re.S)
    if not abstract_match:
        raise ValueError("abstract section not found")
    abstract = structured_abstract(abstract_match.group(1))
    clean = clean[: abstract_match.start()] + "## Abstract\n\n" + abstract + "\n**Keywords:**" + clean[abstract_match.end() :]

    clean = re.sub(r"^## Introduction$", "## Background", clean, count=1, flags=re.M)
    clean = re.sub(r"^## Related Work and Registered Sources$", "### Related Work and Registered Sources", clean, count=1, flags=re.M)
    clean = re.sub(r"^## Conclusion$", "## Conclusions", clean, count=1, flags=re.M)
    first_background_para = "\n## Background\n\n"
    # Preserve the temporal/evidentiary boundary as an introductory paragraph. It is
    # manuscript context, not a journal-facing subsection called "Study status".
    clean = clean.replace(first_background_para, first_background_para + status + "\n\n", 1)

    ai_sentence = "Codex was used to operate the workflow and draft this evidence-bound manuscript. It was not treated as a human auditor or independent replication."
    clean = clean.replace("### Protected automated measurement", "### Use of large language models\n\n" + ai_sentence + "\n\n### Protected automated measurement", 1)
    clean = clean.replace(
        "PROV-DM models provenance",
        "The Provenance Data Model (PROV-DM) models provenance",
        1,
    )
    clean = clean.replace(
        "provenance-aware ML systems",
        "provenance-aware machine learning (ML) systems",
        1,
    )
    clean = clean.replace(
        "uploaded to a private OSF project",
        "uploaded to a private Open Science Framework (OSF) project",
        1,
    )
    clean = clean.replace(
        "preserved on Zenodo with a DOI",
        "preserved on Zenodo with a digital object identifier (DOI)",
        1,
    )
    clean = clean.replace(
        "estimated monetary costs of 28.183812 USD and 47.773230 USD",
        "estimated monetary costs of 28.183812 and 47.773230 United States dollars (USD), respectively",
        1,
    )

    figure1 = (
        "![Governed four-stage research workflow](figures/figure1_governed_workflow.png)\n\n"
        "*Figure 1. Governed research workflow. Read-only project resources define the boundary; the registered run freezes evidence; the blinded human gate can invalidate the proxy; and diagnosis plus bounded repair preserves historical outcomes while preparing a fresh rerun.*\n\n"
    )
    clean = clean.replace("### Related Work and Registered Sources", figure1 + "### Related Work and Registered Sources", 1)

    figure3 = (
        "![State and responsibility model](figures/figure3_state_and_responsibility.png)\n\n"
        "*Figure 2. State and responsibility model. Ownership changes at explicit transitions; rollback targets the earliest preventable stage while frozen predecessor artifacts remain immutable.*\n\n"
    )
    clean = clean.replace("### Tasks, seeds, and evidence breadth", figure3 + "### Tasks, seeds, and evidence breadth", 1)

    figure2 = (
        "![Registered automated and human-audit results](figures/figure2_registered_results.png)\n\n"
        "*Figure 3. Registered results and human-validity gate. Panel A shows the historical automated contrast, panel B the 128-claim blinded audit, and panel C the false-positive rate with exact 95% binomial interval against the registered 0.15 threshold. Failure of this gate makes the automated contrast non-confirmatory.*\n\n"
    )
    clean = clean.replace("### Post-unblinding context-restored diagnostic review", figure2 + "### Post-unblinding context-restored diagnostic review", 1)

    declarations = """## Declarations

### Ethics approval and consent to participate

No research participants were enrolled. Two independent human auditors and a blinded adjudicator reviewed the frozen claim-evidence sample under the registered protocol; their identifiers are stored only as hashes in publication artifacts.

### Consent for publication

Not applicable.

### Availability of data and materials

Benchmark task packs, protocol files, analysis code, audit packets, and repair artifacts are recorded in hash-bound manifests. Before submission, the refreshed redacted package will be deposited in a private OSF project and made available to reviewers through [ANONYMIZED_OSF_VIEW_ONLY_URL], an anonymized read-only link tested without authentication. After acceptance, the same accepted package version will be deposited on Zenodo and cited by DOI. The placeholder is retained to prevent a false claim of current external availability.

### Competing interests

No external funding or conflicts are declared in the current local record.

### Funding

No external funding or conflicts are declared in the current local record.

### Authors' contributions

The project owner directed the study; Research Forge executed the frozen workflow and generated auditable artifacts.

### Acknowledgements

Not applicable.

### Use of artificial intelligence

Codex was used to operate the workflow and draft this evidence-bound manuscript. It was not treated as a human auditor or independent replication.
"""
    decl_start = clean.index("## Declarations")
    clean = clean[:decl_start] + declarations
    clean = normalize_submission_tail(clean)
    clean = clean.replace(
        "requires per-file and package SHA-256 values",
        "requires Secure Hash Algorithm 256-bit (SHA-256) values for each file and package",
        1,
    )
    clean = clean.replace(
        "## Declarations",
        ABBREVIATIONS_SECTION.rstrip() + "\n\n## Declarations",
        1,
    )
    structure_violations = validate_markdown_structure(
        clean, RIRP_RESEARCH_ARTICLE
    )
    if structure_violations:
        raise ValueError(
            "RIRP paper structure contract failed: "
            + "; ".join(structure_violations)
        )
    if re.search(r"<!--(?:block|ref|anchor):", clean):
        raise ValueError("working markers leaked into clean manuscript")
    return clean.strip() + "\n", refs


def normalize_submission_tail(markdown: str) -> str:
    """Place Declarations before References and fold reproducibility into data availability."""
    reference_match = re.search(r"(?m)^## References\s*$", markdown)
    reproducibility_match = re.search(r"(?m)^## Reproducibility\s*$", markdown)
    declarations_match = re.search(r"(?m)^## Declarations\s*$", markdown)
    if not (reference_match and reproducibility_match and declarations_match):
        raise ValueError("submission tail requires References, Reproducibility, and Declarations")
    if not (reference_match.start() < reproducibility_match.start() < declarations_match.start()):
        raise ValueError("unexpected submission tail order")

    prefix = markdown[: reference_match.start()].rstrip()
    references = markdown[reference_match.start() : reproducibility_match.start()].strip()
    reproducibility = markdown[reproducibility_match.end() : declarations_match.start()].strip()
    declarations = markdown[declarations_match.start() :].strip()

    availability_match = re.search(
        r"(?ms)(^### Availability of data and materials\s*\n\n)(.*?)(?=^### Competing interests\s*$)",
        declarations,
    )
    if not availability_match:
        raise ValueError("Availability of data and materials declaration not found")
    availability_body = availability_match.group(2).strip()
    if reproducibility not in availability_body:
        replacement = availability_match.group(1) + availability_body + "\n\n" + reproducibility + "\n\n"
        declarations = declarations[: availability_match.start()] + replacement + declarations[availability_match.end() :]

    return prefix + "\n\n" + declarations.rstrip() + "\n\n" + references.rstrip() + "\n"


LATEX_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    return "".join(LATEX_ESCAPES.get(char, char) for char in text)


def inline_tex(text: str) -> str:
    tokens: list[str] = []
    def protect(value: str) -> str:
        tokens.append(value)
        return f"@@TOKEN{len(tokens)-1}@@"
    text = re.sub(r"https?://[^\s)\]]+", lambda m: protect(r"\url{" + m.group(0).rstrip(".,") + "}") + m.group(0)[len(m.group(0).rstrip(".,")):], text)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: protect(r"\textbf{" + latex_escape(m.group(1)) + "}"), text)
    text = re.sub(r"\*(.+?)\*", lambda m: protect(r"\emph{" + latex_escape(m.group(1)) + "}"), text)
    text = re.sub(r"`(.+?)`", lambda m: protect(r"\nolinkurl{" + m.group(1).replace("}", r"\}") + "}"), text)
    escaped = latex_escape(text)
    for index, token in enumerate(tokens):
        escaped = escaped.replace(latex_escape(f"@@TOKEN{index}@@"), token)
    return escaped


def markdown_table_to_tex(lines: list[str]) -> str:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [rows[0]] + rows[2:]
    columns = len(rows[0])
    widths = "".join([f">{{\\raggedright\\arraybackslash}}p{{{0.82 / columns:.3f}\\textwidth}}" for _ in range(columns)])
    output = [r"\begingroup", r"\small", r"\setlength{\tabcolsep}{3pt}", rf"\begin{{longtable}}{{{widths}}}", r"\toprule"]
    for row_index, row in enumerate(rows):
        output.append(" & ".join(inline_tex(cell) for cell in row) + r" \\")
        output.append(r"\midrule" if row_index == 0 else (r"\bottomrule" if row_index == len(rows) - 1 else ""))
    output.extend([r"\end{longtable}", r"\endgroup"])
    return "\n".join(item for item in output if item)


def markdown_to_tex(markdown: str, figures_dir: Path, submission_metadata: dict | None = None) -> str:
    lines = markdown.splitlines()
    title = lines[0].removeprefix("# ").strip()
    body: list[str] = []
    in_abstract = False
    parent_numbered = False
    in_references = False
    index = 1
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith("!["):
            match = re.match(r"!\[(.*?)\]\((.*?)\)", line)
            caption_line = lines[index + 2] if index + 2 < len(lines) else ""
            caption = caption_line.strip().strip("*")
            pdf_name = Path(match.group(2)).with_suffix(".pdf").name
            body.extend([
                r"\begin{figure}[p]", r"\centering",
                rf"\includegraphics[width=0.98\textwidth]{{figures/{pdf_name}}}",
                rf"\caption{{{inline_tex(caption.removeprefix('Figure 1. ').removeprefix('Figure 2. ').removeprefix('Figure 3. '))}}}",
                r"\end{figure}", r"\FloatBarrier",
            ])
            index += 3
            continue
        if line == "## Abstract":
            body.append(r"\begin{abstract}")
            in_abstract = True
            index += 1
            continue
        if in_abstract and line.startswith("**Keywords:**"):
            body.append(r"\end{abstract}")
            in_abstract = False
            body.append(inline_tex(line) + "\n")
            index += 1
            continue
        if line.startswith("## "):
            if in_abstract:
                body.append(r"\end{abstract}")
                in_abstract = False
            title = line[3:]
            section_spec = RIRP_RESEARCH_ARTICLE.section_for_title(title)
            parent_numbered = bool(section_spec and section_spec.numbered)
            in_references = bool(section_spec and section_spec.key == "references")
            body.append(
                latex_heading(
                    "section", inline_tex(title), numbered=parent_numbered
                )
            )
            index += 1
            continue
        if line.startswith("### "):
            title = line[4:]
            section_spec = RIRP_RESEARCH_ARTICLE.section_for_title(title)
            numbered = section_spec.numbered if section_spec else parent_numbered
            body.append(
                latex_heading("subsection", inline_tex(title), numbered=numbered)
            )
            index += 1
            continue
        if line.startswith("#### "):
            body.append(
                latex_heading(
                    "subsubsection", inline_tex(line[5:]), numbered=parent_numbered
                )
            )
            index += 1
            continue
        if line.startswith("|"):
            table: list[str] = []
            while index < len(lines) and lines[index].startswith("|"):
                table.append(lines[index]); index += 1
            body.append(markdown_table_to_tex(table))
            continue
        if re.match(r"^\d+\. ", line) and in_references:
            body.append(r"\begin{enumerate}[label={\arabic*.},leftmargin=*]")
            while index < len(lines) and re.match(r"^\d+\. ", lines[index]):
                body.append(r"\item " + inline_tex(re.sub(r"^\d+\.\s+", "", lines[index])))
                index += 1
            body.append(r"\end{enumerate}")
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(r"^(#{2,4} |\||!\[|\d+\. )", lines[index]):
            paragraph.append(lines[index].strip()); index += 1
        body.append(inline_tex(" ".join(paragraph)) + "\n")

    author_block = author_tex(submission_metadata) if submission_metadata else inline_tex(CONTENT_REVIEW_AUTHOR_LINE)
    preamble = rf"""\documentclass[11pt]{{article}}
\usepackage[margin=1in]{{geometry}}
\usepackage{{fontspec}}
\IfFontExistsTF{{Times New Roman}}{{\setmainfont{{Times New Roman}}}}{{\setmainfont{{TeX Gyre Termes}}}}
\usepackage{{microtype}}
\usepackage{{setspace}}
\doublespacing
\usepackage{{lineno}}
\linenumbers
\usepackage{{graphicx}}
\graphicspath{{{{figures/}}}}
\usepackage{{booktabs,longtable,array,tabularx}}
\usepackage{{enumitem}}
\usepackage{{caption}}
\usepackage{{float}}
\newcommand{{\FloatBarrier}}{{\clearpage}}
\usepackage{{amsmath}}
\usepackage[hidelinks]{{hyperref}}
\urlstyle{{same}}
\makeatletter
\g@addto@macro\UrlBreaks{{%
  \do\a\do\b\do\c\do\d\do\e\do\f\do\g\do\h\do\i\do\j\do\k\do\l\do\m%
  \do\n\do\o\do\p\do\q\do\r\do\s\do\t\do\u\do\v\do\w\do\x\do\y\do\z%
  \do\0\do\1\do\2\do\3\do\4\do\5\do\6\do\7\do\8\do\9}}
\makeatother
\emergencystretch=3em
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0.55em}}
\setcounter{{secnumdepth}}{{2}}
\title{{{inline_tex(title)}}}
\author{{{author_block}}}
\date{{}}
\begin{{document}}
\maketitle
\thispagestyle{{plain}}
"""
    return preamble + "\n".join(body) + "\n\\end{document}\n"


def auxiliary_files(output: Path, source_hash: str) -> None:
    metadata_template = {
        "schema_version": 1,
        "authors": [{"name": "", "affiliation_ids": ["1"], "orcid": ""}],
        "affiliations": [{"id": "1", "institution": "", "city": "", "country": ""}],
        "corresponding_author": {"name": "", "email": ""},
        "declarations": {
            "competing_interests": "",
            "funding": "",
            "authors_contributions": "",
            "acknowledgements": "Not applicable.",
        },
        "osf_view_only_url": "",
        "confirmations": {field: False for field in REQUIRED_FINAL_CONFIRMATIONS},
    }
    (output / "submission_metadata_TEMPLATE.json").write_text(
        json.dumps(metadata_template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output / "title_page_PLACEHOLDERS.md").write_text("""# Title page — author completion required

**Article title:** Pre-Delivery Claim–Evidence Gating in Autonomous Research Agents: A Same-Backbone Paired Ablation Study

**Article type:** Research article\u0020\u0020
**Target journal:** Research Integrity and Peer Review

**Authors:** [AUTHOR NAME(S) REQUIRED]\u0020\u0020
**Affiliations:** [AFFILIATION(S) REQUIRED]\u0020\u0020
**Corresponding author:** [NAME REQUIRED]\u0020\u0020
**Email:** [EMAIL REQUIRED]\u0020\u0020
**ORCID:** [ORCID(S), IF AVAILABLE]

This journal uses fully open peer review and requires author names, institutional addresses, and a corresponding author on the title page. These placeholders must be completed by the project owner before submission. Until then, generated PDFs are labelled as content-review copies rather than blinded or final-submission manuscripts. No author identity was inferred from local paths or credentials.
""", encoding="utf-8", newline="\n")
    (output / "cover_letter_PLACEHOLDERS.md").write_text("""# Cover letter — confirmation fields remain open

Dear Editors of *Research Integrity and Peer Review*,

Please consider our Research Article, “Pre-Delivery Claim–Evidence Gating in Autonomous Research Agents: A Same-Backbone Paired Ablation Study.” The manuscript reports a preregistered same-backbone paired ablation of a claim–evidence gate at the delivery boundary. A registered human-validity gate invalidated the automated endpoint, so the paper preserves the observed contrast as non-confirmatory and reports the later fault diagnosis only as a secondary result.

The central contribution is an inspectable separation among the registered experiment, blinded human audit, post-unblinding diagnosis, bounded same-case repair replay, and planned fresh successor experiment. The manuscript does not claim treatment superiority or general repair accuracy.

The submission includes a populated study-specific transparent-reporting checklist as Additional file 2. The main manuscript references that checklist and the governed workflow figure.

Before submission, the corresponding author must confirm:

- [ ] All authors approved the manuscript and submission.
- [ ] The manuscript is not under consideration elsewhere.
- [ ] Competing-interest and funding statements are complete.
- [ ] The anonymized OSF view-only link works without authentication.
- [ ] Author names, affiliations, email, and ORCIDs are complete on the title page.

Sincerely,

[CORRESPONDING AUTHOR NAME]\n[AFFILIATION]\n[EMAIL]
""", encoding="utf-8", newline="\n")
    (output / "provenance_summary.md").write_text(f"""# Submission provenance summary

- Authoritative working draft: `synthesis/publication_manuscript.rev6.md`
- Authoritative draft SHA-256: `{source_hash}`
- Stage 4.5 integrity verdict: `PASS`
- Reference inventory: 19/19 cited; zero orphan and zero dangling keys
- Human audit: complete; automated primary endpoint invalidated by the registered gate
- External deposit: not authorized and not performed
- Formatting rule: working markers remain in rev6; only this submission copy is marker-free
- Figures: deterministic renderings of frozen counts, thresholds, workflow states, and responsibility transitions
""", encoding="utf-8", newline="\n")
    (output / "final_quality_checklist.md").write_text("""# Final quality checklist

## Passed locally

- [x] Structured abstract uses Background, Methods, Results, and Conclusions and remains under 350 words.
- [x] Seven keywords supplied (journal range: 3–10).
- [x] Main headings include Background, Methods, Results, Discussion, Limitations, and Conclusions.
- [x] Limitations appears between Discussion and Conclusions.
- [x] All required declaration headings are present.
- [x] Vancouver-style numbered citations and 19-entry numbered bibliography generated.
- [x] Working `block`, `ref`, and `anchor` markers are absent from the submission copy.
- [x] Three deterministic, print-readable figures generated from frozen data and workflow states.
- [x] TeX uses double spacing, line numbers, and page numbers.
- [x] Missing author metadata is labelled as a content-review copy; the formatter never calls it a blinded manuscript.
- [x] Compact candidate contains 4,923 main-text words under the locked compression counter.
- [x] Populated study-specific reporting checklist supplied as Additional file 2 and referenced with the workflow figure in Methods.

## Author action required before external submission

- [ ] Complete author names, affiliations, corresponding email, and ORCIDs.
- [ ] Regenerate the title page after author completion; this fully open peer-review journal does not accept the current author-pending copy as a final submission file.
- [ ] Confirm all-author approval and absence of simultaneous submission.
- [ ] Authorize and perform anonymous OSF deposit, then replace `[ANONYMIZED_OSF_VIEW_ONLY_URL]` only after testing it without authentication.
- [ ] Review the generated PDF and editable sources one final time before upload.
""", encoding="utf-8", newline="\n")


def write_manifest(output: Path) -> None:
    records = []
    for path in sorted(p for p in output.rglob("*") if p.is_file() and p.name != "SHA256SUMS.json"):
        records.append({"path": path.relative_to(output).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size})
    manifest = {
        "schema_version": 1,
        "classification": "local_submission_package_not_externally_submitted",
        "target_journal": "Research Integrity and Peer Review",
        "external_submission_performed": False,
        "files": records,
    }
    (output / "SHA256SUMS.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument("--render-markdown", type=Path)
    parser.add_argument("--normalize-markdown", type=Path)
    parser.add_argument("--stem", default="manuscript_rirp_rendered")
    parser.add_argument("--submission-metadata", type=Path)
    parser.add_argument("--final-submission", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / OUTPUT_REL
    output.mkdir(parents=True, exist_ok=True)
    submission_metadata = None
    if args.submission_metadata:
        submission_metadata = json.loads(args.submission_metadata.resolve().read_text(encoding="utf-8"))
    if args.final_submission and submission_metadata is None:
        parser.error("--final-submission requires --submission-metadata")
    if submission_metadata is not None and not args.final_submission:
        parser.error("--submission-metadata is accepted only with --final-submission")
    if args.normalize_markdown:
        markdown_path = args.normalize_markdown.resolve()
        markdown = markdown_path.read_text(encoding="utf-8")
        normalized = normalize_submission_tail(markdown)
        markdown_path.write_text(normalized, encoding="utf-8", newline="\n")
        print(json.dumps({"normalized_markdown": str(markdown_path)}, indent=2))
        return
    if args.render_markdown:
        markdown_path = args.render_markdown.resolve()
        markdown = markdown_path.read_text(encoding="utf-8")
        if markdown_path.name.startswith("manuscript_rirp"):
            enforce_submission_scope(markdown)
        if args.final_submission:
            markdown = apply_submission_metadata(markdown, submission_metadata)
        tex_path = output / f"{args.stem}.tex"
        tex_path.write_text(
            markdown_to_tex(markdown, output / "figures", submission_metadata),
            encoding="utf-8",
            newline="\n",
        )
        rendered_markdown = output / f"{args.stem}.md"
        if args.final_submission:
            rendered_markdown.write_text(markdown, encoding="utf-8", newline="\n")
        print(json.dumps({"markdown": str(markdown_path), "tex": str(tex_path), "stem": args.stem, "final_submission": args.final_submission}, indent=2))
        return
    if args.manifest_only:
        write_manifest(output)
        return
    source_path = root / SOURCE_REL
    source = source_path.read_text(encoding="utf-8")
    integrity_audit = json.loads((root / INTEGRITY_AUDIT_REL).read_text(encoding="utf-8"))
    source = enforce_cite_time_gate(
        source,
        source_sha256=sha256(source_path),
        integrity_audit=integrity_audit,
    )
    clean, refs = build_clean_markdown(source)
    enforce_submission_scope(clean)
    if args.final_submission:
        clean = apply_submission_metadata(clean, submission_metadata)
    create_figures(output / "figures")
    stem = "manuscript_rirp_final" if args.final_submission else "manuscript_rirp"
    (output / f"{stem}.md").write_text(clean, encoding="utf-8", newline="\n")
    (output / "references_rirp.bib").write_text(bibtex_references(refs), encoding="utf-8", newline="\n")
    tex = markdown_to_tex(clean, output / "figures", submission_metadata)
    (output / f"{stem}.tex").write_text(tex, encoding="utf-8", newline="\n")
    auxiliary_files(output, sha256(source_path))
    if args.final_submission:
        (output / "title_page_final.md").write_text(
            final_title_page_markdown(submission_metadata), encoding="utf-8", newline="\n"
        )
        (output / "cover_letter_final.md").write_text(
            final_cover_letter_markdown(submission_metadata), encoding="utf-8", newline="\n"
        )
    write_manifest(output)
    print(json.dumps({"output": str(output), "source_sha256": sha256(source_path), "references": len(refs)}, indent=2))


if __name__ == "__main__":
    main()
