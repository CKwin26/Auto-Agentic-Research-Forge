from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from pydantic import Field, model_validator

from .claim_discovery import ClaimDiscoveryReport, discover_project_claims
from .manuscript_depth import audit_manuscript_depth
from .models import StrictModel
from .storage import read_json, sha256_file, slugify, write_json_atomic


_ALLOWED_SUFFIXES = {
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}
_EXCLUDED_DIRECTORIES = {
    ".agents",
    ".codex",
    ".git",
    ".next",
    ".openai",
    ".pytest_cache",
    ".venv",
    ".vercel",
    ".vinext",
    ".wrangler",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "tmp",
    "work",
}
_EXCLUDED_EXACT_NAMES = {
    ".env",
    ".env.local",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "secrets.json",
    "uv.lock",
    "yarn.lock",
}
_EXCLUDED_NAME_FRAGMENTS = ("credential", "private-key", "private_key", "secret")
_MAX_RESOURCE_BYTES = 25 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024
_MAX_INVENTORY_FILES = 20_000
_COMMON_TRACK_TOKENS = {
    "baseline",
    "model",
    "protocol",
    "recalculation",
    "report",
    "research",
    "robustness",
    "validation",
}
_STAGE_NAMES = (
    "stage_1_discovery",
    "stage_2_protocol",
    "stage_3_experimentation",
    "stage_4_synthesis",
)
_TEXT_MATERIAL_SUFFIXES = {".md", ".txt"}
_STRUCTURED_EVIDENCE_SUFFIXES = {".csv", ".json", ".jsonl", ".tsv"}
_SOURCE_MODES = Literal["declared_chain", "derived_materials"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BundleResource(StrictModel):
    path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    suffix: str
    modified_at: str


class NoveltyCandidate(StrictModel):
    track_id: str
    novelty_seed: str
    protocol_path: str
    output_path: str | None = None
    report_path: str | None = None
    implementation_paths: list[str] = Field(default_factory=list)
    test_paths: list[str] = Field(default_factory=list)
    conclusion_excerpt: str = ""
    latest_artifact_at: str
    evidence_maturity: Literal[
        "prospective_blind", "retrospective", "mixed_or_unspecified"
    ]
    artifact_chain_complete: bool
    protocol_bound_to_output: bool
    paperability_score: int = Field(ge=0)
    paperability_reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    source_mode: _SOURCE_MODES = "declared_chain"
    closure_input_ready: bool = False
    display_title: str = ""


class BundleInspection(StrictModel):
    schema_version: int = 1
    source_root: str
    inspected_at: str = Field(default_factory=_utc_now)
    resource_count: int
    excluded_count: int
    candidates: list[NoveltyCandidate]
    recommended_track_id: str | None = None
    claim_discovery: ClaimDiscoveryReport | None = None


class StageResourceUse(StrictModel):
    source_path: str
    snapshot_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str


class StageResourceManifest(StrictModel):
    schema_version: int = 1
    stage: Literal[
        "stage_1_discovery",
        "stage_2_protocol",
        "stage_3_experimentation",
        "stage_4_synthesis",
    ]
    track_id: str
    resources: list[StageResourceUse] = Field(min_length=1)


class ScopeContract(StrictModel):
    schema_version: int = 1
    track_id: str
    title: str
    research_question: str
    hypothesis_under_test: str
    novelty_candidate: str
    scope_in: list[str] = Field(min_length=1)
    scope_out: list[str] = Field(min_length=1)
    evidence_maturity: Literal[
        "prospective_blind", "retrospective", "mixed_or_unspecified"
    ]
    source_protocol: str
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    automatic_boundary_notes: list[str] = Field(default_factory=list)
    source_mode: _SOURCE_MODES = "declared_chain"


class ProtocolLock(StrictModel):
    schema_version: int = 1
    track_id: str
    protocol_path: str
    protocol_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_path: str
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    protocol_bound_to_output: bool
    implementation_paths: list[str]
    test_paths: list[str]
    source_mode: _SOURCE_MODES = "declared_chain"


class NumericEvidence(StrictModel):
    path: str
    value: int | float


class IdeaVerdict(StrictModel):
    schema_version: int = 1
    track_id: str
    status: Literal["supported", "refuted", "mixed", "inconclusive", "unverifiable"]
    idea_validated: bool
    evidence_maturity: Literal[
        "prospective_blind", "retrospective", "mixed_or_unspecified"
    ]
    protocol_bound_to_output: bool
    conclusion: str
    numeric_evidence: list[NumericEvidence]
    evidence_paths: list[str]
    limitations: list[str]
    next_action: str


class BundleAudit(StrictModel):
    schema_version: int = 2
    audited_at: str = Field(default_factory=_utc_now)
    passed: bool
    pilot_draft_generated: bool = False
    manuscript_depth_passed: bool = False
    paper_draft_ready: bool
    publication_ready: bool
    checks: dict[str, bool]
    violations: list[str]
    publication_blockers: list[str]


class BundleCompletionCertificate(StrictModel):
    schema_version: int = 2
    completed_at: str = Field(default_factory=_utc_now)
    track_id: str
    idea_status: str
    idea_validated: bool
    pilot_draft_generated: bool = False
    manuscript_depth_passed: bool = False
    paper_draft_ready: bool
    publication_ready: bool
    artifact_hashes: dict[str, str]

    @model_validator(mode="after")
    def require_artifacts(self) -> "BundleCompletionCertificate":
        if not self.artifact_hashes:
            raise ValueError("completion certificate requires artifact hashes")
        return self


def _safe_source_root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project bundle directory not found: {root}")
    return root


def _is_excluded(relative: Path) -> bool:
    lower_parts = [part.casefold() for part in relative.parts]
    if any(part in _EXCLUDED_DIRECTORIES for part in lower_parts[:-1]):
        return True
    name = relative.name.casefold()
    if name in _EXCLUDED_EXACT_NAMES or name.startswith(".env."):
        return True
    if any(fragment in name for fragment in _EXCLUDED_NAME_FRAGMENTS):
        return True
    if relative.suffix.casefold() in {".key", ".p12", ".pem"}:
        return True
    return False


def inventory_project_bundle(source_root: str | Path) -> tuple[list[BundleResource], int]:
    root = _safe_source_root(source_root)
    resources: list[BundleResource] = []
    excluded = 0
    seen = 0
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in directory_names:
            candidate = current_path / name
            relative = candidate.relative_to(root)
            if name.casefold() in _EXCLUDED_DIRECTORIES or candidate.is_symlink():
                excluded += 1
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                excluded += 1
                continue
            if root != resolved and root not in resolved.parents:
                excluded += 1
                continue
            if _is_excluded(relative / "placeholder.txt"):
                excluded += 1
                continue
            kept_directories.append(name)
        directory_names[:] = sorted(kept_directories)
        for name in sorted(file_names):
            path = current_path / name
            seen += 1
            if seen > _MAX_INVENTORY_FILES:
                raise ValueError(
                    f"project bundle exceeds the {_MAX_INVENTORY_FILES}-file inventory limit"
                )
            relative = path.relative_to(root)
            try:
                resolved = path.resolve()
            except OSError:
                excluded += 1
                continue
            if root != resolved and root not in resolved.parents:
                excluded += 1
                continue
            size = path.stat().st_size
            if (
                _is_excluded(relative)
                or path.suffix.casefold() not in _ALLOWED_SUFFIXES
                or size > _MAX_RESOURCE_BYTES
            ):
                excluded += 1
                continue
            resources.append(
                BundleResource(
                    path=relative.as_posix(),
                    size_bytes=size,
                    sha256=sha256_file(path),
                    suffix=path.suffix.casefold(),
                    modified_at=datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat(),
                )
            )
    return resources, excluded


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        return read_json(path)
    except Exception:
        return {}


def _track_tokens(track_id: str) -> list[str]:
    tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", track_id.casefold())
        if len(token) >= 3
        and not re.fullmatch(r"v\d+", token)
        and token not in _COMMON_TRACK_TOKENS
    ]
    return list(dict.fromkeys(tokens))


def _text(path: Path, *, limit: int = 400_000) -> str:
    if path.stat().st_size > limit:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_markdown_section(text: str, headings: Iterable[str]) -> str:
    wanted = {heading.casefold() for heading in headings}
    lines = text.splitlines()
    start: int | None = None
    level = 2
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        heading = match.group(2).strip().casefold()
        if heading in wanted:
            start = index + 1
            level = len(match.group(1))
            break
    if start is None:
        return ""
    selected: list[str] = []
    for line in lines[start:]:
        match = re.match(r"^(#{1,6})\s+", line)
        if match and len(match.group(1)) <= level:
            break
        selected.append(line)
    return "\n".join(selected).strip()


def _report_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _find_report(root: Path, track_id: str) -> Path | None:
    docs = root / "docs"
    if not docs.is_dir():
        return None
    exact_names = (
        f"{track_id}-report.md",
        f"{track_id}.md",
        f"{track_id.replace('-research', '')}-report.md",
    )
    for name in exact_names:
        path = docs / name
        if path.is_file():
            return path
    tokens = _track_tokens(track_id)
    scored: list[tuple[int, float, Path]] = []
    for path in docs.glob("*.md"):
        name = path.stem.casefold()
        score = sum(3 for token in tokens if token in name)
        if track_id.casefold() in _text(path, limit=120_000).casefold():
            score += 8
        if score:
            scored.append((score, path.stat().st_mtime, path))
    return max(scored, default=(0, 0.0, None))[2]


def _related_paths(
    root: Path, track_id: str, resources: dict[str, BundleResource]
) -> tuple[list[str], list[str]]:
    tokens = _track_tokens(track_id)
    implementation: list[tuple[int, str]] = []
    tests: list[tuple[int, str]] = []
    for relative in resources:
        rel = Path(relative)
        lower = relative.casefold()
        if not lower.startswith(("backend/", "scripts/", "tests/", "tests_py/")):
            continue
        name_score = sum(2 for token in tokens if token in rel.stem.casefold())
        content_score = 0
        path = root / rel
        if path.stat().st_size <= 500_000:
            content = _text(path, limit=500_000).casefold()
            if track_id.casefold() in content:
                content_score += 8
            content_score += sum(1 for token in tokens if token in content)
        score = name_score + content_score
        if not score:
            continue
        target = tests if lower.startswith(("tests/", "tests_py/")) else implementation
        target.append((score, relative))
    implementation.sort(key=lambda item: (-item[0], item[1]))
    tests.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in implementation[:16]], [item[1] for item in tests[:12]]


def _walk_named_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.extend(_walk_named_values(item, path))
    elif not isinstance(value, list):
        found.append((prefix, value))
    return found


def _evidence_maturity(protocol: dict[str, Any], output: dict[str, Any]) -> str:
    named = [(path.casefold(), value) for path, value in _walk_named_values(protocol)]
    named.extend((path.casefold(), value) for path, value in _walk_named_values(output))
    prospective_true = any(
        ("prospective" in path or "blindtest" in path or "blind_test" in path)
        and value is True
        for path, value in named
    )
    retrospective = any(
        (
            "retrospective" in path
            or "prospective" in path
            or "blindtest" in path
            or "blind_test" in path
        )
        and (value is False or (isinstance(value, str) and "retrospective" in value.casefold()))
        for path, value in named
    )
    if prospective_true and not retrospective:
        return "prospective_blind"
    if retrospective:
        return "retrospective"
    return "mixed_or_unspecified"


def _novelty_seed(protocol: dict[str, Any], track_id: str) -> str:
    for key in (
        "noveltyClaim",
        "novelty_claim",
        "purpose",
        "researchQuestion",
        "research_question",
        "question",
        "hypothesis",
    ):
        value = protocol.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"Evaluate the frozen research track {track_id}."


def _protocol_bound(protocol: dict[str, Any], output: dict[str, Any]) -> bool:
    embedded = output.get("protocol")
    return isinstance(embedded, dict) and embedded == protocol


def _scientific_design_bonus(
    track_id: str,
    novelty_seed: str,
    report_text: str,
    protocol: dict[str, Any],
    output: dict[str, Any],
) -> tuple[int, list[str]]:
    bonus = 0
    reasons: list[str] = []
    title = _report_title(report_text, "")
    if "research" in track_id.casefold() or "研究" in title:
        bonus += 8
        reasons.append("explicit research track rather than an implementation-only artifact")
    searchable_paths = " ".join(
        path.casefold()
        for path, _ in [
            *_walk_named_values(protocol),
            *_walk_named_values(output),
        ]
    )
    design_cues = {
        "pairedbootstrap": "paired bootstrap evidence",
        "randombaseline": "randomized baseline comparison",
        "ablation": "ablation evidence",
        "concentrationstress": "right-tail concentration stress test",
        "point-in-time": "point-in-time evidence controls",
        "causal": "causal-signal hypothesis",
    }
    cue_hits = 0
    combined_text = f"{novelty_seed.casefold()} {searchable_paths}"
    for cue, reason in design_cues.items():
        if cue in combined_text:
            cue_hits += 1
            reasons.append(reason)
    bonus += min(15, cue_hits * 3)
    breadth_values = [
        value
        for path, value in _walk_named_values(output)
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and path.casefold().endswith(("trades", "months"))
    ]
    if any(value >= 20 for value in breadth_values):
        bonus += 5
        reasons.append("multi-period or multi-trade machine-readable evidence")
    if "recalculation" in f"{track_id} {novelty_seed}".casefold():
        bonus -= 12
        reasons.append("penalty: narrow recalculation rather than a new research design")
    return bonus, reasons


def discover_novelty_candidates(
    source_root: str | Path,
    resources: list[BundleResource] | None = None,
) -> list[NoveltyCandidate]:
    root = _safe_source_root(source_root)
    inventory = resources if resources is not None else inventory_project_bundle(root)[0]
    resource_map = {resource.path: resource for resource in inventory}
    candidates: list[NoveltyCandidate] = []
    protocols = root / "protocols"
    if not protocols.is_dir():
        return []
    for protocol_path in sorted(protocols.glob("*.json")):
        relative_protocol = protocol_path.relative_to(root).as_posix()
        if relative_protocol not in resource_map:
            continue
        protocol = _load_json_object(protocol_path)
        track_id = str(protocol.get("version") or protocol_path.stem)
        output_path = root / "outputs" / f"{track_id}.json"
        if not output_path.is_file():
            output_path = root / "outputs" / f"{protocol_path.stem}.json"
        relative_output = (
            output_path.relative_to(root).as_posix()
            if output_path.is_file()
            and output_path.relative_to(root).as_posix() in resource_map
            else None
        )
        output = _load_json_object(output_path) if relative_output else {}
        report_path = _find_report(root, track_id)
        relative_report = (
            report_path.relative_to(root).as_posix()
            if report_path is not None
            and report_path.relative_to(root).as_posix() in resource_map
            else None
        )
        report_text = _text(report_path) if report_path is not None else ""
        conclusion = _extract_markdown_section(
            report_text,
            ("结论", "一句话结论", "最终判断", "conclusion", "findings"),
        )
        implementation, tests = _related_paths(root, track_id, resource_map)
        bound = bool(relative_output) and _protocol_bound(protocol, output)
        maturity = _evidence_maturity(protocol, output)
        blockers: list[str] = []
        if not relative_output:
            blockers.append("missing machine-readable experiment output")
        if not relative_report:
            blockers.append("missing human-readable conclusion report")
        if relative_output and not bound:
            blockers.append("experiment output is not bound to the exact protocol object")
        if not conclusion:
            blockers.append("report has no recognized conclusion section")
        if maturity != "prospective_blind":
            blockers.append("evidence is not a prospective blind validation")
        complete = bool(relative_output and relative_report and conclusion and bound)
        artifact_times = [protocol_path.stat().st_mtime]
        if relative_output:
            artifact_times.append(output_path.stat().st_mtime)
        if report_path is not None:
            artifact_times.append(report_path.stat().st_mtime)
        design_bonus, paperability_reasons = _scientific_design_bonus(
            track_id,
            _novelty_seed(protocol, track_id),
            report_text,
            protocol,
            output,
        )
        score = (
            30
            + (35 if relative_output else 0)
            + (25 if relative_report else 0)
            + (15 if bound else 0)
            + min(10, len(implementation) + len(tests))
            + (10 if maturity == "prospective_blind" else 0)
            + design_bonus
        )
        candidates.append(
            NoveltyCandidate(
                track_id=track_id,
                novelty_seed=_novelty_seed(protocol, track_id),
                protocol_path=relative_protocol,
                output_path=relative_output,
                report_path=relative_report,
                implementation_paths=implementation,
                test_paths=tests,
                conclusion_excerpt=conclusion[:4000],
                latest_artifact_at=datetime.fromtimestamp(
                    max(artifact_times), timezone.utc
                ).isoformat(),
                evidence_maturity=maturity,
                artifact_chain_complete=complete,
                protocol_bound_to_output=bound,
                paperability_score=score,
                paperability_reasons=paperability_reasons,
                blockers=blockers,
                source_mode="declared_chain",
                closure_input_ready=complete,
                display_title=_report_title(report_text, track_id),
            )
        )
    candidates.sort(
        key=lambda item: (
            -item.paperability_score,
            -int(item.artifact_chain_complete),
            -datetime.fromisoformat(item.latest_artifact_at).timestamp(),
            item.track_id,
        )
    )
    return candidates


def _first_substantive_excerpt(text: str, *, limit: int = 700) -> str:
    for block in re.split(r"\n\s*\n", text):
        cleaned_lines = []
        for line in block.splitlines():
            line = re.sub(r"^\s*#{1,6}\s+", "", line)
            line = re.sub(r"^\s*(?:[-*+] |\d+[.)]\s+)", "", line)
            line = line.strip()
            if not line or line.startswith("|") or line.startswith("```"):
                continue
            cleaned_lines.append(line)
        cleaned = re.sub(r"\s+", " ", " ".join(cleaned_lines)).strip()
        if len(cleaned) >= 20:
            return cleaned[:limit].rstrip()
    return ""


def _derived_question(text: str, fallback: str) -> str:
    section = _extract_markdown_section(
        text,
        (
            "研究问题",
            "核心问题",
            "研究目标",
            "目标",
            "research question",
            "objective",
            "purpose",
            "problem statement",
            "摘要",
            "abstract",
            "overview",
        ),
    )
    source = section or text
    question_match = re.search(r"([^\n。！？!?]{12,500}[？?])", source)
    if question_match:
        return re.sub(r"\s+", " ", question_match.group(1)).strip()
    excerpt = _first_substantive_excerpt(source, limit=500)
    return excerpt or f"从项目 {fallback} 的现有材料中识别一个可检验的研究问题"


def _derived_conclusion(text: str) -> str:
    section = _extract_markdown_section(
        text,
        (
            "结论",
            "一句话结论",
            "最终判断",
            "研究发现",
            "主要发现",
            "conclusion",
            "findings",
            "results summary",
        ),
    )
    if section:
        return section[:4000].strip()
    inline = re.search(
        r"(?im)^\s*(?:结论|最终判断|主要发现|conclusion|findings)\s*[:：]\s*(.+)$",
        text,
    )
    return inline.group(1).strip()[:4000] if inline else ""


def _material_role_score(relative: str, text: str, *, role: str) -> int:
    lower = relative.casefold()
    normalized = text.casefold()
    score = 0
    if role == "boundary":
        name_cues = (
            "readme",
            "overview",
            "proposal",
            "research",
            "method",
            "design",
            "plan",
            "项目",
            "研究",
            "方案",
            "方法",
        )
        text_cues = (
            "研究问题",
            "研究目标",
            "research question",
            "objective",
            "hypothesis",
            "methodology",
        )
    else:
        name_cues = (
            "report",
            "result",
            "finding",
            "summary",
            "conclusion",
            "报告",
            "结果",
            "结论",
            "总结",
        )
        text_cues = (
            "## 结论",
            "## 研究发现",
            "## conclusion",
            "## findings",
            "结论：",
            "conclusion:",
        )
    score += sum(3 for cue in name_cues if cue in lower)
    score += sum(5 for cue in text_cues if cue in normalized)
    if relative.count("/") <= 1:
        score += 2
    return score


def _structured_evidence_score(
    root: Path, resource: BundleResource
) -> tuple[int, int, str]:
    lower = resource.path.casefold()
    score = sum(
        3
        for cue in (
            "output",
            "result",
            "metric",
            "evaluation",
            "experiment",
            "data",
            "结果",
            "指标",
            "实验",
            "数据",
        )
        if cue in lower
    )
    numeric_count = 0
    if resource.suffix == ".json":
        payload = _load_json_object(root / resource.path)
        numeric_count = sum(
            1
            for _, value in _walk_named_values(payload)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        score += min(10, numeric_count)
    return score, numeric_count, resource.path


def discover_derived_material_candidate(
    source_root: str | Path,
    resources: list[BundleResource] | None = None,
) -> NoveltyCandidate | None:
    root = _safe_source_root(source_root)
    inventory = resources if resources is not None else inventory_project_bundle(root)[0]
    text_resources = [
        resource for resource in inventory if resource.suffix in _TEXT_MATERIAL_SUFFIXES
    ]
    if not text_resources:
        return None

    material_texts: list[tuple[BundleResource, str]] = []
    for resource in text_resources[:300]:
        material_texts.append((resource, _text(root / resource.path, limit=160_000)))
    boundary_resource, boundary_text = max(
        material_texts,
        key=lambda item: (
            _material_role_score(item[0].path, item[1], role="boundary"),
            item[0].modified_at,
            item[0].path,
        ),
    )
    conclusion_resource, conclusion_text = max(
        material_texts,
        key=lambda item: (
            _material_role_score(item[0].path, item[1], role="conclusion"),
            item[0].modified_at,
            item[0].path,
        ),
    )
    conclusion = _derived_conclusion(conclusion_text)
    conclusion_found = bool(conclusion)
    if not conclusion:
        conclusion = (
            "现有资料未提供可与冻结实验协议绑定的结论；"
            "本轮仅完成研究问题收敛和证据缺口识别。"
        )

    structured = [
        resource
        for resource in inventory
        if resource.suffix in _STRUCTURED_EVIDENCE_SUFFIXES
    ]
    if structured:
        _, numeric_count, evidence_path = max(
            (_structured_evidence_score(root, resource) for resource in structured),
            key=lambda item: (item[0], item[1], item[2]),
        )
    else:
        numeric_count = 0
        evidence_path = conclusion_resource.path

    implementation = [
        resource.path
        for resource in inventory
        if resource.suffix in {".py", ".ps1", ".sql"}
        and "test" not in resource.path.casefold()
    ][:16]
    tests = [
        resource.path
        for resource in inventory
        if resource.suffix in {".py", ".ps1"}
        and "test" in resource.path.casefold()
    ][:12]
    title = _report_title(conclusion_text, "") or _report_title(boundary_text, "")
    title = title[:120].strip() or f"{root.name} 项目资料研究"
    question = _derived_question(boundary_text, root.name)
    selected_paths = {
        boundary_resource.path,
        conclusion_resource.path,
        evidence_path,
        *implementation,
        *tests,
    }
    latest = max(
        datetime.fromisoformat(resource.modified_at)
        for resource in inventory
        if resource.path in selected_paths
    )
    reasons = [
        f"derived a research boundary from {len(text_resources)} readable text materials",
    ]
    if conclusion_found:
        reasons.append("located a project-authored conclusion or findings section")
    if structured:
        reasons.append(
            f"located {len(structured)} structured evidence files; selected one with {numeric_count} numeric fields"
        )
    if implementation or tests:
        reasons.append("located implementation or test artifacts for reproducibility context")
    score = (
        25
        + min(20, len(text_resources) * 2)
        + (15 if conclusion_found else 0)
        + (15 if structured else 0)
        + min(10, len(implementation) + len(tests))
    )
    return NoveltyCandidate(
        track_id=f"derived-materials-{slugify(root.name)}-v1",
        novelty_seed=question,
        protocol_path=boundary_resource.path,
        output_path=evidence_path,
        report_path=conclusion_resource.path,
        implementation_paths=implementation,
        test_paths=tests,
        conclusion_excerpt=conclusion,
        latest_artifact_at=latest.isoformat(),
        evidence_maturity="mixed_or_unspecified",
        artifact_chain_complete=False,
        protocol_bound_to_output=False,
        paperability_score=score,
        paperability_reasons=reasons,
        blockers=[
            "no declared frozen research protocol",
            "no exact protocol-output binding",
            "idea verdict must remain unverifiable until an explicit experiment chain is supplied",
        ],
        source_mode="derived_materials",
        closure_input_ready=True,
        display_title=title,
    )


def inspect_project_bundle(
    source_root: str | Path, *, discover_claims: bool = False
) -> BundleInspection:
    root = _safe_source_root(source_root)
    resources, excluded = inventory_project_bundle(root)
    candidates = discover_novelty_candidates(root, resources)
    if not any(candidate.artifact_chain_complete for candidate in candidates):
        derived = discover_derived_material_candidate(root, resources)
        if derived is not None:
            candidates.append(derived)
    candidates.sort(
        key=lambda item: (
            -int(item.artifact_chain_complete),
            -int(item.closure_input_ready),
            -item.paperability_score,
            -datetime.fromisoformat(item.latest_artifact_at).timestamp(),
            item.track_id,
        )
    )
    recommended = next(
        (candidate.track_id for candidate in candidates if candidate.closure_input_ready),
        candidates[0].track_id if candidates else None,
    )
    claim_discovery = (
        discover_project_claims(
            root, resources, candidates, include_external=True
        )
        if discover_claims
        else None
    )
    return BundleInspection(
        source_root=str(root),
        resource_count=len(resources),
        excluded_count=excluded,
        candidates=candidates,
        recommended_track_id=recommended,
        claim_discovery=claim_discovery,
    )


def _selected_candidate(inspection: BundleInspection, track_id: str) -> NoveltyCandidate:
    selected_id = inspection.recommended_track_id if track_id == "auto" else track_id
    for candidate in inspection.candidates:
        if candidate.track_id == selected_id:
            if not candidate.closure_input_ready:
                raise ValueError(
                    f"selected track is incomplete: {candidate.track_id}: "
                    + "; ".join(candidate.blockers)
                )
            return candidate
    raise ValueError(f"research track not found in project bundle: {selected_id}")


def _supporting_paths(
    resources: dict[str, BundleResource], names: Iterable[str]
) -> list[str]:
    selected: list[str] = []
    for relative in resources:
        lower = relative.casefold()
        if any(name in lower for name in names):
            selected.append(relative)
    return selected


def _literature_paths(resources: dict[str, BundleResource]) -> list[str]:
    """Return only canonical frozen-literature artifacts.

    Arbitrary files containing words such as ``paper`` or ``reference`` are
    deliberately excluded; the paper expansion gate accepts the same explicit
    source-record and manifest shape used by Research Forge Stage 1.
    """

    selected: list[str] = []
    for relative in resources:
        normalized = relative.replace("\\", "/").casefold()
        if normalized == "literature_manifest.json" or (
            normalized.startswith("literature/sources/")
            and normalized.endswith(".json")
        ):
            selected.append(relative)
    return sorted(selected)


def _stage_path_sets(
    candidate: NoveltyCandidate, resources: dict[str, BundleResource]
) -> dict[str, list[tuple[str, str]]]:
    if candidate.source_mode == "derived_materials":
        stage_1: list[tuple[str, str]] = [
            (
                candidate.protocol_path,
                "Supplies the primary project context used to derive the research boundary.",
            ),
            (
                candidate.report_path or "",
                "Supplies any project-authored findings while preserving their unverified status.",
            ),
        ]
        stage_1.extend(
            (path, "Provides additional project context, methods, or material boundaries.")
            for path in _supporting_paths(
                resources,
                ("readme", "overview", "method", "proposal", "研究", "方法", "方案"),
            )[:8]
        )
        stage_2 = [
            (
                candidate.protocol_path,
                "Locks the source material from which a provisional protocol boundary was derived.",
            )
        ]
        stage_2.extend(
            (path, "Provides implementation or test context; it is not treated as a frozen experiment.")
            for path in [*candidate.implementation_paths, *candidate.test_paths]
        )
        stage_3 = [
            (
                candidate.output_path or candidate.protocol_path,
                "Inspects available project evidence without claiming exact protocol binding.",
            ),
            (
                candidate.protocol_path,
                "Keeps the derived question visible while the idea verdict remains unverifiable.",
            ),
        ]
        stage_3.extend(
            (path, "Provides executable or test context for identifying the next validation step.")
            for path in [*candidate.implementation_paths, *candidate.test_paths]
        )
        stage_4 = [
            (
                candidate.report_path or candidate.protocol_path,
                "Provides project-authored interpretation, if present, without upgrading it to validation.",
            ),
            (
                candidate.output_path or candidate.protocol_path,
                "Provides available structured observations for an evidence-gap working paper.",
            ),
            (
                candidate.protocol_path,
                "Constrains synthesis to the snapshotted project materials.",
            ),
        ]
    else:
        stage_1 = [
            (candidate.protocol_path, "Defines the candidate research question and frozen boundary."),
            (candidate.report_path or "", "Supplies the existing conclusion used to test paperability."),
        ]
        stage_1.extend(
            (path, "Provides project context, data scope, or existing evidence limits.")
            for path in _supporting_paths(
                resources,
                ("readme.md", "current-state", "data-schema", "methodology"),
            )[:8]
        )
        stage_2 = [
            (candidate.protocol_path, "The exact protocol object frozen before evidence interpretation."),
        ]
        stage_2.extend(
            (path, "Implements or tests the selected frozen research track.")
            for path in [*candidate.implementation_paths, *candidate.test_paths]
        )
        stage_2.extend(
            (path, "Supplies leakage, model, or protocol control constraints.")
            for path in _supporting_paths(
                resources,
                ("data-leakage", "model-card", "research_protocol", "audit_"),
            )[:10]
        )
        stage_3 = [
            (candidate.output_path or "", "Contains the machine-readable experimental evidence."),
            (candidate.protocol_path, "Binds interpretation to the frozen protocol."),
        ]
        stage_3.extend(
            (path, "Provides executable or test evidence for the selected experiment.")
            for path in [*candidate.implementation_paths, *candidate.test_paths]
        )
        stage_4 = [
            (candidate.report_path or "", "Provides the project-authored interpretation and limitations."),
            (candidate.output_path or "", "Provides numerical evidence for manuscript claims."),
            (candidate.protocol_path, "Constrains manuscript scope and forbidden interpretations."),
        ]
    literature_paths = _literature_paths(resources)
    stage_1.extend(
        (path, "Provides a frozen, verified literature record for novelty grounding.")
        for path in literature_paths
    )
    stage_4.extend(
        (path, "Constrains related-work citations to frozen, verified sources.")
        for path in literature_paths
    )

    result: dict[str, list[tuple[str, str]]] = {}
    for stage, values in zip(
        _STAGE_NAMES,
        (stage_1, stage_2, stage_3, stage_4),
        strict=True,
    ):
        seen: set[str] = set()
        filtered: list[tuple[str, str]] = []
        for path, reason in values:
            if path and path in resources and path not in seen:
                seen.add(path)
                filtered.append((path, reason))
        result[stage] = filtered
    return result


def _snapshot_resources(
    root: Path,
    run_dir: Path,
    selected_paths: Iterable[str],
    resource_map: dict[str, BundleResource],
) -> None:
    total = 0
    for relative in sorted(set(selected_paths)):
        resource = resource_map[relative]
        total += resource.size_bytes
        if total > _MAX_SNAPSHOT_BYTES:
            raise ValueError(
                f"selected project resources exceed the {_MAX_SNAPSHOT_BYTES}-byte snapshot limit"
            )
        source = root / Path(relative)
        destination = run_dir / "source_snapshot" / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256_file(destination) != resource.sha256:
            raise ValueError(f"resource changed while snapshotting: {relative}")


def _protocol_scope(
    candidate: NoveltyCandidate, protocol: dict[str, Any], protocol_hash: str, report_text: str
) -> ScopeContract:
    title = _report_title(
        report_text, candidate.display_title or candidate.track_id
    )
    purpose = candidate.novelty_seed
    research_question = purpose.rstrip(".。")
    if not research_question.endswith(("?", "？")):
        research_question += "?"
    hypothesis = str(
        protocol.get("hypothesis")
        or protocol.get("hypothesisUnderTest")
        or f"At least one prespecified method in {candidate.track_id} improves the frozen control under the stated evaluation policy."
    )
    scope_in: list[str] = []
    for key in ("strategy", "execution", "evidenceRole", "interpretation", "purpose"):
        value = protocol.get(key)
        if isinstance(value, str) and value.strip():
            scope_in.append(f"{key}: {value.strip()}")
    periods = protocol.get("periods")
    if isinstance(periods, dict):
        for key, value in periods.items():
            if isinstance(value, (str, int, float)):
                scope_in.append(f"periods.{key}: {value}")
    if not scope_in:
        if candidate.source_mode == "derived_materials":
            scope_in.append(
                "Use only the snapshotted project materials to define the provisional question and evidence gaps."
            )
        else:
            scope_in.append(f"Use only the exact protocol and evidence for {candidate.track_id}.")
    scope_out = [
        str(item)
        for item in (
            protocol.get("forbiddenInterpretations")
            or protocol.get("forbiddenAfterSeeingResults")
            or []
        )
        if str(item).strip()
    ]
    if candidate.evidence_maturity != "prospective_blind":
        scope_out.append("Do not describe retrospective evidence as prospective validation.")
    if candidate.source_mode == "derived_materials":
        scope_out.extend(
            [
                "Do not treat a project-authored conclusion as an independently validated result.",
                "Do not infer an experimental protocol, control group, or causal effect that the source materials do not declare.",
            ]
        )
    if not scope_out:
        scope_out.append("Do not generalize beyond the frozen data, method, and evaluation period.")
    return ScopeContract(
        track_id=candidate.track_id,
        title=title,
        research_question=research_question,
        hypothesis_under_test=hypothesis,
        novelty_candidate=(
            "A project-derived candidate contribution, not yet an externally verified novelty claim: "
            + purpose
        ),
        scope_in=list(dict.fromkeys(scope_in))[:20],
        scope_out=list(dict.fromkeys(scope_out))[:20],
        evidence_maturity=candidate.evidence_maturity,
        source_protocol=candidate.protocol_path,
        protocol_sha256=protocol_hash,
        automatic_boundary_notes=(
            [
                "The boundary was derived from project materials because no complete protocol-output-report chain was available.",
                "The idea verdict is structurally limited to unverifiable until an explicit frozen experiment chain is supplied.",
                "Idea validation and paper generation are recorded as separate gates.",
                "External scholarly novelty is not inferred from local project artifacts alone.",
            ]
            if candidate.source_mode == "derived_materials"
            else [
                "The boundary was reconstructed from a complete protocol-output-report chain.",
                "Idea validation and paper generation are recorded as separate gates.",
                "External scholarly novelty is not inferred from local project artifacts alone.",
            ]
        ),
        source_mode=candidate.source_mode,
    )


def _classify_conclusion(conclusion: str) -> str:
    normalized = conclusion.casefold()
    if "inconclusive" in normalized or "不确定" in conclusion or "没有达到" in conclusion:
        return "inconclusive"
    negative_markers = (
        "不支持",
        "不能替代",
        "没有形成",
        "没有成立",
        "未通过",
        "refute",
        "not support",
        "failed",
    )
    positive_markers = (
        "支持保留",
        "有效",
        "改善",
        "增益",
        "成立",
        "supported",
        "improved",
        "outperformed",
    )
    negative = any(marker in normalized for marker in negative_markers)
    positive = any(marker in normalized for marker in positive_markers)
    if negative and positive:
        return "mixed"
    if negative:
        return "refuted"
    if positive:
        return "supported"
    return "unverifiable"


def _numeric_evidence(output: dict[str, Any], *, limit: int = 16) -> list[NumericEvidence]:
    priority_terms = (
        "selected",
        "evaluation",
        "aggregate",
        "bootstrap",
        "compound",
        "return",
        "lift",
        "auc",
        "drawdown",
        "hitrate",
        "winrate",
        "trades",
        "months",
        "probability",
    )
    deprioritized = ("rank", "sha", "seed", "year", "day", "n_estimators")
    scored: list[tuple[int, str, int | float]] = []
    for path, value in _walk_named_values(output):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        lower = path.casefold()
        score = sum(2 for term in priority_terms if term in lower)
        score -= sum(1 for term in deprioritized if term in lower)
        if score > 0 and len(path.split(".")) <= 6:
            scored.append((score, path, value))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [NumericEvidence(path=path, value=value) for _, path, value in scored[:limit]]


def _limitations(
    protocol: dict[str, Any], maturity: str, source_mode: str = "declared_chain"
) -> list[str]:
    limitations: list[str] = []
    for key in ("forbiddenInterpretations", "forbiddenAfterSeeingResults"):
        value = protocol.get(key)
        if isinstance(value, list):
            limitations.extend(str(item) for item in value if str(item).strip())
    for key in ("warning", "eventFeatureWarning", "interpretation", "evidenceRole"):
        value = protocol.get(key)
        if isinstance(value, str) and value.strip():
            limitations.append(value.strip())
    if maturity != "prospective_blind":
        limitations.append(
            "The selected evidence is not a prospective blind validation and cannot establish live effectiveness."
        )
    if source_mode == "derived_materials":
        limitations.extend(
            [
                "No frozen research protocol was declared in the source package.",
                "Available observations are not exactly bound to a prespecified protocol and cannot validate the idea.",
            ]
        )
    return list(dict.fromkeys(limitations))[:20]


def _idea_verdict(
    candidate: NoveltyCandidate,
    protocol: dict[str, Any],
    output: dict[str, Any],
) -> IdeaVerdict:
    if candidate.source_mode == "derived_materials":
        status = "unverifiable"
        validated = False
        next_action = (
            "Convert the derived question into a frozen protocol with an explicit baseline, "
            "evaluation policy, and machine-readable output before judging the idea."
        )
    else:
        status = _classify_conclusion(candidate.conclusion_excerpt)
        validated = (
            status in {"supported", "refuted"}
            and candidate.evidence_maturity == "prospective_blind"
        )
    if candidate.source_mode == "derived_materials":
        pass
    elif status in {"mixed", "inconclusive"}:
        next_action = (
            "Freeze the narrowed claim and obtain a new independent evaluation before treating the idea as validated."
        )
    elif candidate.evidence_maturity != "prospective_blind":
        next_action = (
            "Preserve the protocol and collect a prospective blind evaluation; do not tune on the observed window."
        )
    else:
        next_action = "Proceed to full literature grounding, independent review, and publication formatting."
    return IdeaVerdict(
        track_id=candidate.track_id,
        status=status,
        idea_validated=validated,
        evidence_maturity=candidate.evidence_maturity,
        protocol_bound_to_output=candidate.protocol_bound_to_output,
        conclusion=candidate.conclusion_excerpt,
        numeric_evidence=_numeric_evidence(output),
        evidence_paths=[
            candidate.protocol_path,
            candidate.output_path or "",
            candidate.report_path or "",
        ],
        limitations=_limitations(
            protocol, candidate.evidence_maturity, candidate.source_mode
        ),
        next_action=next_action,
    )


def _render_claims(
    scope: ScopeContract,
    verdict: IdeaVerdict,
    resource_map: dict[str, BundleResource],
) -> dict[str, Any]:
    claims: list[dict[str, Any]] = [
        {
            "claim_id": "method-001",
            "kind": "method",
            "statement": scope.hypothesis_under_test,
            "evidence": [
                {
                    "path": scope.source_protocol,
                    "sha256": resource_map[scope.source_protocol].sha256,
                }
            ],
        },
        {
            "claim_id": "result-001",
            "kind": "result",
            "statement": verdict.conclusion,
            "evidence": [
                {
                    "path": path,
                    "sha256": resource_map[path].sha256,
                }
                for path in verdict.evidence_paths
                if path in resource_map
            ],
        },
        {
            "claim_id": "limitation-001",
            "kind": "limitation",
            "statement": (
                f"Evidence maturity is {verdict.evidence_maturity}; idea_validated={str(verdict.idea_validated).lower()}."
            ),
            "evidence": [],
        },
    ]
    for index, item in enumerate(verdict.numeric_evidence, start=1):
        output_path = verdict.evidence_paths[1]
        claims.append(
            {
                "claim_id": f"numeric-{index:03d}",
                "kind": "result_metric",
                "statement": f"{item.path}={item.value}",
                "evidence": [
                    {
                        "path": output_path,
                        "sha256": resource_map[output_path].sha256,
                        "json_path": item.path,
                    }
                ],
            }
        )
    return {"schema_version": 1, "track_id": scope.track_id, "claims": claims}


def _render_novelty_portfolio(
    inspection: BundleInspection, selected_track_id: str
) -> str:
    lines = [
        "# 项目包创新候选组合",
        "",
        "> 候选贡献优先来自项目内部协议—结果—报告链；没有完整链时，只从项目材料派生待验证问题。两者都不是已完成外部文献验证的学术首创声明。",
        "",
        f"自动推荐并进入闭环的研究链：`{selected_track_id}`。",
        "",
    ]
    for index, candidate in enumerate(inspection.candidates, start=1):
        marker = "（本轮选中）" if candidate.track_id == selected_track_id else ""
        lines.extend(
            [
                f"## {index}. {candidate.track_id}{marker}",
                "",
                f"- 候选贡献：{candidate.novelty_seed}",
                f"- 证据成熟度：`{candidate.evidence_maturity}`",
                f"- 产物链完整：`{str(candidate.artifact_chain_complete).lower()}`",
                f"- 可进入闭环：`{str(candidate.closure_input_ready).lower()}`",
                f"- 输入模式：`{candidate.source_mode}`",
                f"- 协议与结果精确绑定：`{str(candidate.protocol_bound_to_output).lower()}`",
                f"- 论文可提炼分：`{candidate.paperability_score}`",
                f"- 协议：`{candidate.protocol_path}`",
                f"- 结果：`{candidate.output_path or 'missing'}`",
                f"- 报告：`{candidate.report_path or 'missing'}`",
                "",
            ]
        )
        if candidate.conclusion_excerpt:
            lines.extend(["已有结论：", "", candidate.conclusion_excerpt, ""])
        if candidate.paperability_reasons:
            lines.extend(["排序依据：", ""])
            lines.extend(f"- {item}" for item in candidate.paperability_reasons)
            lines.append("")
        if candidate.blockers:
            lines.extend(["尚缺证据：", ""])
            lines.extend(f"- {item}" for item in candidate.blockers)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_manuscript(
    scope: ScopeContract,
    verdict: IdeaVerdict,
    stage_manifests: dict[str, StageResourceManifest],
) -> str:
    numbers = verdict.numeric_evidence[:12]
    lines = [
        f"# {scope.title}：证据约束工作论文",
        "",
        "> 状态：自动提炼的工作稿。它验证了项目包到论文草稿的闭环，不等同于同行评审或可发表认证。",
        "",
        "## 摘要",
        "",
        f"本文研究的问题是：{scope.research_question}。",
        f"项目内冻结假设为：{scope.hypothesis_under_test}",
        f"机器可读证据与协议的绑定状态为 `{str(verdict.protocol_bound_to_output).lower()}`；当前结论分类为 `{verdict.status}`，证据成熟度为 `{verdict.evidence_maturity}`。",
        verdict.conclusion,
        "",
        "## 研究问题与候选新颖性",
        "",
        scope.novelty_candidate,
        "",
        "这里的“新颖”仅指项目内部可提炼的候选贡献；在完成外部文献检索和相似工作排查前，不主张学术首创。",
        "",
        "## 数据与方法",
        "",
        (
            "研究边界由项目文本与可用结构化材料自动派生；由于缺少冻结协议绑定，本节只描述材料范围和证据缺口。"
            if scope.source_mode == "derived_materials"
            else "研究边界由冻结协议、机器可读输出和项目报告的完整链条共同确定。"
        ),
        "纳入范围如下：",
        "",
    ]
    lines.extend(f"- {item}" for item in scope.scope_in)
    lines.extend(
        [
            "",
            "排除与禁止解释如下：",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in scope.scope_out)
    lines.extend(
        [
            "",
            "## 结果",
            "",
            verdict.conclusion,
            "",
        ]
    )
    if numbers:
        lines.extend(
            [
                "以下数值直接来自机器可读材料；字段名保留原路径以便复核。"
                + (
                    "这些数值尚未绑定冻结实验协议，不能单独验证想法。"
                    if scope.source_mode == "derived_materials"
                    else ""
                ),
                "",
                "| JSON 路径 | 数值 |",
                "|---|---:|",
            ]
        )
        lines.extend(f"| `{item.path}` | {item.value} |" for item in numbers)
    else:
        lines.extend(
            [
                "未发现可绑定到冻结实验协议的机器可读数值结果。",
                "",
            ]
        )
    lines.extend(
        [
            "",
            "## 讨论",
            "",
            f"本轮闭环把“想法判断”固定为独立产物 `idea_verdict.json`。因此论文写作失败不会反向改变第三阶段的实验结论；同样，草稿生成成功也不能把 `{verdict.status}` 自动升级为已验证。",
            "",
            "## 局限",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in verdict.limitations)
    lines.extend(
        [
            "",
            "- 尚未执行系统性外部文献检索，因此候选新颖性未通过学术查重式验证。",
            "- 本稿复用项目内已有结论，没有把报告文本当作独立复现实验。",
            "",
            "## 结论",
            "",
            verdict.conclusion,
            "",
            f"下一步：{verdict.next_action}",
            "",
            "## 可复现性与资源调用",
            "",
        ]
    )
    for stage in _STAGE_NAMES:
        manifest = stage_manifests[stage]
        lines.append(f"### {stage}")
        lines.append("")
        for item in manifest.resources:
            lines.append(f"- `{item.source_path}` — {item.reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _stage_manifest(
    stage: str,
    track_id: str,
    uses: list[tuple[str, str]],
    resource_map: dict[str, BundleResource],
) -> StageResourceManifest:
    return StageResourceManifest(
        stage=stage,  # type: ignore[arg-type]
        track_id=track_id,
        resources=[
            StageResourceUse(
                source_path=path,
                snapshot_path=f"source_snapshot/{path}",
                sha256=resource_map[path].sha256,
                reason=reason,
            )
            for path, reason in uses
        ],
    )


def _make_run_dir(output_root: Path, name: str, track_id: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(f"{name}|{track_id}|{_utc_now()}".encode("utf-8")).hexdigest()[:8]
    run_dir = output_root / f"{slugify(name)}-{stamp}-{digest}"
    run_dir.mkdir()
    return run_dir


def close_project_bundle_loop(
    source_root: str | Path,
    *,
    output_root: str | Path,
    name: str | None = None,
    track_id: str = "auto",
    discover_claims: bool = False,
    progress: Callable[..., None] | None = None,
) -> Path:
    def report(**detail: Any) -> None:
        if progress is not None:
            progress(**detail)

    root = _safe_source_root(source_root)
    report(
        stage="discovery",
        title="正在扫描项目材料和已有实验产物",
        detail="读取协议、实验输出、报告和实现文件，并排除密钥与环境文件。",
        reason="先确认哪些材料能够进入证据链，避免把无关文件当成研究依据。",
        output="可用材料清单与候选研究问题",
        next="选择证据链最完整的候选选题",
    )
    inspection = inspect_project_bundle(root, discover_claims=discover_claims)
    candidate = _selected_candidate(inspection, track_id)
    resources, _ = inventory_project_bundle(root)
    resource_map = {resource.path: resource for resource in resources}
    stage_paths = _stage_path_sets(candidate, resource_map)
    selected_paths = [path for values in stage_paths.values() for path, _ in values]
    report(
        stage="discovery",
        title=f"正在为“{candidate.track_id}”建立证据清单",
        detail=f"已找到 {len(resources)} 项项目材料，其中 {len(set(selected_paths))} 项进入本次研究闭环。",
        reason="每个后续结论都必须能追溯到本次选中的材料。",
        output="四个研究阶段各自使用的资源清单",
        next="保存只读快照并冻结研究范围",
    )
    run_dir = _make_run_dir(Path(output_root).resolve(), name or root.name, candidate.track_id)
    _snapshot_resources(root, run_dir, selected_paths, resource_map)

    write_json_atomic(run_dir / "inspection.json", inspection)
    write_json_atomic(
        run_dir / "bundle_manifest.json",
        {
            "schema_version": 1,
            "created_at": _utc_now(),
            "source_root": str(root),
            "selected_track_id": candidate.track_id,
            "source_mode": candidate.source_mode,
            "resources": [
                resource.model_dump(mode="json")
                for resource in resources
                if resource.path in set(selected_paths)
            ],
            "security_boundary": {
                "excluded_directories": sorted(_EXCLUDED_DIRECTORIES),
                "secrets_and_env_files_excluded": True,
                "max_resource_bytes": _MAX_RESOURCE_BYTES,
                "max_snapshot_bytes": _MAX_SNAPSHOT_BYTES,
            },
        },
    )

    stage_manifests: dict[str, StageResourceManifest] = {}
    for stage in _STAGE_NAMES:
        manifest = _stage_manifest(stage, candidate.track_id, stage_paths[stage], resource_map)
        stage_manifests[stage] = manifest
        write_json_atomic(run_dir / stage / "resources.json", manifest)

    protocol = _load_json_object(run_dir / "source_snapshot" / candidate.protocol_path)
    output = _load_json_object(run_dir / "source_snapshot" / (candidate.output_path or ""))
    report_text = _text(run_dir / "source_snapshot" / (candidate.report_path or ""))
    scope = _protocol_scope(
        candidate,
        protocol,
        resource_map[candidate.protocol_path].sha256,
        report_text,
    )
    write_json_atomic(run_dir / "stage_1_discovery" / "scope_contract.json", scope)
    write_json_atomic(
        run_dir / "stage_1_discovery" / "novelty_candidates.json",
        {
            "schema_version": 1,
            "recommended_track_id": inspection.recommended_track_id,
            "selected_track_id": candidate.track_id,
            "candidates": [item.model_dump(mode="json") for item in inspection.candidates],
        },
    )
    if inspection.claim_discovery is not None:
        write_json_atomic(
            run_dir / "stage_1_discovery" / "claim_discovery.json",
            inspection.claim_discovery,
        )
    (run_dir / "stage_1_discovery" / "novelty_candidates.md").write_text(
        _render_novelty_portfolio(inspection, candidate.track_id),
        encoding="utf-8",
        newline="\n",
    )

    report(
        stage="protocol",
        title="正在冻结实验协议与输出文件的绑定关系",
        detail=f"核对协议 {candidate.protocol_path} 与实验输出 {candidate.output_path or '未提供'} 的哈希和引用关系。",
        reason="防止后续写作更换评价口径、数据范围或实验结果。",
        output="不可被论文阶段改写的协议锁",
        next="依据冻结口径计算研究判定",
    )
    protocol_lock = ProtocolLock(
        track_id=candidate.track_id,
        protocol_path=candidate.protocol_path,
        protocol_sha256=resource_map[candidate.protocol_path].sha256,
        output_path=candidate.output_path or "",
        output_sha256=resource_map[candidate.output_path or ""].sha256,
        protocol_bound_to_output=candidate.protocol_bound_to_output,
        implementation_paths=candidate.implementation_paths,
        test_paths=candidate.test_paths,
        source_mode=candidate.source_mode,
    )
    write_json_atomic(run_dir / "stage_2_protocol" / "protocol_lock.json", protocol_lock)

    report(
        stage="experimentation",
        title="正在读取实验输出并计算研究判定",
        detail="逐项比较冻结协议中的通过条件与实际实验输出，单独生成想法判定。",
        reason="让证据决定支持、反驳、混合或暂不可验证，而不是让论文措辞决定结论。",
        output="独立的想法判定与证据门结果",
        next="只把判定允许的主张交给论文阶段",
    )
    verdict = _idea_verdict(candidate, protocol, output)
    write_json_atomic(run_dir / "stage_3_experimentation" / "idea_verdict.json", verdict)

    report(
        stage="synthesis",
        title="正在把证据允许的主张组织成研究工作稿",
        detail=f"当前研究判定为 {verdict.status}；正在生成主张清单、论文结构和扩写资格。",
        reason="论文只能陈述已经被协议和实验结果允许的内容。",
        output="可追溯的主张清单与研究工作稿",
        next="审计每条主张、资源快照和论文资格",
    )
    claims = _render_claims(scope, verdict, resource_map)
    write_json_atomic(run_dir / "stage_4_synthesis" / "claims.json", claims)
    manuscript = _render_manuscript(scope, verdict, stage_manifests)
    manuscript_path = run_dir / "stage_4_synthesis" / "manuscript.md"
    manuscript_path.write_text(manuscript, encoding="utf-8", newline="\n")

    # The close-loop phase always emits a deterministic expansion decision.
    # It does not run the long-form writer until idea, evidence, and literature
    # gates are independently satisfied.
    from .paper_expansion import prepare_project_bundle_paper

    prepare_project_bundle_paper(run_dir, persist=True)

    report(
        stage="synthesis",
        title="正在逐项审计证据门和论文资格",
        detail="检查资源快照、协议哈希、实验判定、主张范围和稿件深度是否一致。",
        reason="即使某个指标很好，只要证据链不完整，也不能升级成可发表结论。",
        output="研究审计结果与论文扩写资格",
        next="签发本次研究闭环的完成证书",
    )
    audit = audit_project_bundle_loop(run_dir, persist=True)
    if not audit.passed:
        raise ValueError("bundle close-loop audit failed: " + "; ".join(audit.violations))
    artifact_paths = [
        "bundle_manifest.json",
        "stage_1_discovery/scope_contract.json",
        "stage_1_discovery/novelty_candidates.md",
        "stage_2_protocol/protocol_lock.json",
        "stage_3_experimentation/idea_verdict.json",
        "stage_4_synthesis/claims.json",
        "stage_4_synthesis/manuscript.md",
        "stage_4_synthesis/manuscript_depth.json",
        "stage_4_synthesis/paper_expansion_plan.json",
        "stage_4_synthesis/audit.json",
    ]
    if inspection.claim_discovery is not None:
        artifact_paths.append("stage_1_discovery/claim_discovery.json")
    certificate = BundleCompletionCertificate(
        track_id=candidate.track_id,
        idea_status=verdict.status,
        idea_validated=verdict.idea_validated,
        pilot_draft_generated=audit.pilot_draft_generated,
        manuscript_depth_passed=audit.manuscript_depth_passed,
        paper_draft_ready=audit.paper_draft_ready,
        publication_ready=audit.publication_ready,
        artifact_hashes={relative: sha256_file(run_dir / relative) for relative in artifact_paths},
    )
    write_json_atomic(run_dir / "completion_certificate.json", certificate)
    report(
        stage="synthesis",
        title="研究闭环审计完成，正在保存最终结果",
        detail=f"审计已通过；研究判定为 {verdict.status}，论文扩写资格为 {audit.paper_draft_ready}。",
        reason="把本次执行结果固定为可复核、不可静默改写的记录。",
        output="完成证书和全部四阶段研究产物",
        next="进入研究判定页面",
    )
    return run_dir


def audit_project_bundle_loop(run_dir: str | Path, *, persist: bool = False) -> BundleAudit:
    root = Path(run_dir).resolve()
    checks: dict[str, bool] = {}
    violations: list[str] = []
    blockers: list[str] = []
    try:
        manifest = read_json(root / "bundle_manifest.json")
    except Exception as exc:
        audit = BundleAudit(
            passed=False,
            pilot_draft_generated=False,
            manuscript_depth_passed=False,
            paper_draft_ready=False,
            publication_ready=False,
            checks={"bundle_manifest_valid": False},
            violations=[f"bundle manifest is invalid: {exc}"],
            publication_blockers=["bundle manifest is invalid"],
        )
        if persist:
            write_json_atomic(root / "stage_4_synthesis" / "audit.json", audit)
        return audit
    checks["bundle_manifest_valid"] = True
    resources = manifest.get("resources", [])
    checks["secrets_excluded"] = all(
        not _is_excluded(Path(str(item.get("path", "")))) for item in resources
    )
    snapshots_intact = True
    for item in resources:
        relative = str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        path = root / "source_snapshot" / relative
        if not path.is_file() or sha256_file(path) != expected:
            snapshots_intact = False
            violations.append(f"snapshot missing or changed: {relative}")
    checks["resource_snapshots_intact"] = snapshots_intact

    stage_manifests: dict[str, StageResourceManifest] = {}
    stages_valid = True
    manuscript_depth_passed = False
    try:
        for stage in _STAGE_NAMES:
            stage_manifests[stage] = StageResourceManifest.model_validate(
                read_json(root / stage / "resources.json")
            )
    except Exception as exc:
        stages_valid = False
        violations.append(f"stage resource manifest is invalid: {exc}")
    checks["all_four_stage_resource_manifests_valid"] = stages_valid

    try:
        scope = ScopeContract.model_validate(
            read_json(root / "stage_1_discovery" / "scope_contract.json")
        )
        checks["scope_contract_valid"] = True
    except Exception as exc:
        scope = None
        checks["scope_contract_valid"] = False
        violations.append(f"scope contract is invalid: {exc}")
    try:
        lock = ProtocolLock.model_validate(
            read_json(root / "stage_2_protocol" / "protocol_lock.json")
        )
        protocol_path = root / "source_snapshot" / lock.protocol_path
        output_path = root / "source_snapshot" / lock.output_path
        checks["protocol_hash_locked"] = (
            sha256_file(protocol_path) == lock.protocol_sha256
            and sha256_file(output_path) == lock.output_sha256
        )
        actual_protocol_binding = (
            lock.protocol_bound_to_output
            and _protocol_bound(_load_json_object(protocol_path), _load_json_object(output_path))
        )
        checks["protocol_bound_to_output"] = actual_protocol_binding
        checks["evidence_binding_policy_valid"] = (
            actual_protocol_binding
            if lock.source_mode == "declared_chain"
            else not lock.protocol_bound_to_output and not actual_protocol_binding
        )
    except Exception as exc:
        lock = None
        checks["protocol_hash_locked"] = False
        checks["protocol_bound_to_output"] = False
        checks["evidence_binding_policy_valid"] = False
        violations.append(f"protocol lock is invalid: {exc}")
    try:
        verdict = IdeaVerdict.model_validate(
            read_json(root / "stage_3_experimentation" / "idea_verdict.json")
        )
        checks["idea_verdict_separate_and_valid"] = True
        checks["derived_materials_verdict_guard"] = bool(
            lock
            and (
                lock.source_mode != "derived_materials"
                or (
                    verdict.status == "unverifiable"
                    and not verdict.idea_validated
                    and not verdict.protocol_bound_to_output
                )
            )
        )
    except Exception as exc:
        verdict = None
        checks["idea_verdict_separate_and_valid"] = False
        checks["derived_materials_verdict_guard"] = False
        violations.append(f"idea verdict is invalid: {exc}")
    try:
        claims = read_json(root / "stage_4_synthesis" / "claims.json")
        manuscript = (root / "stage_4_synthesis" / "manuscript.md").read_text(
            encoding="utf-8"
        )
        required_sections = (
            "## 摘要",
            "## 研究问题与候选新颖性",
            "## 数据与方法",
            "## 结果",
            "## 局限",
            "## 结论",
            "## 可复现性与资源调用",
        )
        checks["claim_registry_present"] = bool(claims.get("claims"))
        checks["manuscript_sections_complete"] = all(
            section in manuscript for section in required_sections
        )
        checks["manuscript_uses_project_conclusion"] = bool(
            verdict and verdict.conclusion and verdict.conclusion in manuscript
        )
        checks["manuscript_reports_idea_status"] = bool(
            verdict and f"`{verdict.status}`" in manuscript
        )
        depth = audit_manuscript_depth(
            root / "stage_4_synthesis" / "manuscript.md",
            profile="journal-article",
            report_path=(
                root / "stage_4_synthesis" / "manuscript_depth.json"
                if persist
                else None
            ),
        )
        checks["manuscript_depth_audit_completed"] = True
        manuscript_depth_passed = depth.passed
        checks["manuscript_depth_gate_passed"] = manuscript_depth_passed
        if not depth.passed:
            blockers.append(
                "journal-article manuscript depth gate failed: "
                + "; ".join(depth.violations)
            )
    except Exception as exc:
        checks["claim_registry_present"] = False
        checks["manuscript_sections_complete"] = False
        checks["manuscript_uses_project_conclusion"] = False
        checks["manuscript_reports_idea_status"] = False
        checks["manuscript_depth_audit_completed"] = False
        checks["manuscript_depth_gate_passed"] = False
        violations.append(f"synthesis artifacts are invalid: {exc}")

    readiness_only_checks = {"manuscript_depth_gate_passed"}
    if lock is not None and lock.source_mode == "derived_materials":
        readiness_only_checks.add("protocol_bound_to_output")
    for name, passed in checks.items():
        if name in readiness_only_checks:
            continue
        if not passed and not any(name in item for item in violations):
            violations.append(f"failed bundle check: {name}")
    passed = all(
        check_passed
        for name, check_passed in checks.items()
        if name not in readiness_only_checks
    )
    pilot_checks = (
        "claim_registry_present",
        "manuscript_sections_complete",
        "manuscript_uses_project_conclusion",
        "manuscript_reports_idea_status",
    )
    pilot_draft_generated = passed and all(checks.get(name, False) for name in pilot_checks)
    # The four-stage close-loop artifact is deliberately a diagnostic working
    # paper. Full-paper readiness is certified only by paper_expansion.py after
    # the separate idea, evidence, literature, citation, and long-form gates.
    paper_ready = False
    if verdict is None or verdict.evidence_maturity != "prospective_blind":
        blockers.append("selected evidence is not a prospective blind validation")
    if lock is not None and lock.source_mode == "derived_materials":
        blockers.append(
            "project materials do not contain an exact frozen protocol-output binding"
        )
    blockers.append("external scholarly novelty and citations have not been verified")
    blockers.append("the separately gated full-paper expansion has not passed")
    if verdict is not None and verdict.status in {"mixed", "inconclusive", "unverifiable"}:
        blockers.append(f"idea verdict is {verdict.status}, not a clean validated result")
    publication_ready = paper_ready and not blockers
    audit = BundleAudit(
        passed=passed,
        pilot_draft_generated=pilot_draft_generated,
        manuscript_depth_passed=manuscript_depth_passed,
        paper_draft_ready=paper_ready,
        publication_ready=publication_ready,
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        publication_blockers=list(dict.fromkeys(blockers)),
    )
    if persist:
        write_json_atomic(root / "stage_4_synthesis" / "audit.json", audit)
    return audit


def verify_project_bundle_completion(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    try:
        certificate = BundleCompletionCertificate.model_validate(
            read_json(root / "completion_certificate.json")
        )
    except Exception as exc:
        return {"passed": False, "violations": [f"completion certificate is invalid: {exc}"]}
    violations: list[str] = []
    for relative, expected in certificate.artifact_hashes.items():
        path = root / relative
        if not path.is_file():
            violations.append(f"completed artifact is missing: {relative}")
        elif sha256_file(path) != expected:
            violations.append(f"completed artifact changed: {relative}")
    audit = audit_project_bundle_loop(root)
    if not audit.passed:
        violations.extend(audit.violations)
    legacy_certificate = certificate.schema_version < 2
    certificate_pilot = (
        audit.pilot_draft_generated
        if legacy_certificate
        else certificate.pilot_draft_generated
    )
    certificate_depth = (
        audit.manuscript_depth_passed
        if legacy_certificate
        else certificate.manuscript_depth_passed
    )
    return {
        "passed": not violations,
        "track_id": certificate.track_id,
        "idea_status": certificate.idea_status,
        "idea_validated": certificate.idea_validated,
        "pilot_draft_generated": (
            certificate_pilot and audit.pilot_draft_generated and not violations
        ),
        "manuscript_depth_passed": (
            certificate_depth and audit.manuscript_depth_passed and not violations
        ),
        "paper_draft_ready": (
            certificate.paper_draft_ready and audit.paper_draft_ready and not violations
        ),
        "publication_ready": (
            certificate.publication_ready and audit.publication_ready and not violations
        ),
        "violations": list(dict.fromkeys(violations)),
    }
