from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import agent_runtime
from .models import (
    LocalizationAudit,
    LocalizationManifest,
    LocalizedBlock,
    TermDecision,
    TermPlan,
)
from .storage import safe_relative, sha256_file, write_json_atomic
from .terminology import (
    LOCALIZATION_ROOT,
    SOURCE_MANUSCRIPT,
    _term_pattern,
    _write_text_atomic,
    load_term_plan,
    prepare_terminology,
    term_plan_is_current,
)


_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+")
_LIST_RE = re.compile(r"^\s*(?:[-*+] |\d+[.)] |> )")
_URL_RE = re.compile(r"https?://[^\s>)]+")
_DOI_RE = re.compile(r"(?<![A-Za-z0-9])10\.\d{4,9}/[^\s,;)>]+", re.IGNORECASE)
_CODE_SPAN_RE = re.compile(r"`[^`\n]+`")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)
_BRACKET_ID_RE = re.compile(r"\[[A-Za-z0-9][A-Za-z0-9._:-]{0,120}\]")
_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:paper|source|run|task|cell|stage|stage2|result|review|protocol)"
    r"-[A-Za-z0-9][A-Za-z0-9._-]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _SourceBlock:
    block_id: str
    position: int
    kind: str
    markdown: str
    source_sha256: str
    protected_tokens: list[str]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _block_kind(markdown: str) -> str:
    stripped = markdown.lstrip()
    if _HEADING_RE.match(stripped):
        return "heading"
    if _FENCE_RE.match(stripped):
        return "code"
    lines = [line for line in markdown.splitlines() if line.strip()]
    if lines and all(line.lstrip().startswith("|") for line in lines):
        return "table"
    if lines and _LIST_RE.match(lines[0]):
        return "list"
    return "paragraph" if len(lines) == 1 else "other"


def _protected_tokens(markdown: str) -> list[str]:
    tokens: list[tuple[int, str]] = []
    for pattern in (
        _CODE_SPAN_RE,
        _URL_RE,
        _DOI_RE,
        _BRACKET_ID_RE,
        _IDENTIFIER_RE,
        _NUMBER_RE,
    ):
        tokens.extend((match.start(), match.group(0)) for match in pattern.finditer(markdown))
    return list(dict.fromkeys(value for _, value in sorted(tokens, key=lambda item: item[0])))


def parse_markdown_blocks(markdown: str) -> list[_SourceBlock]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    raw_blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in lines:
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
        if not line.strip() and fence is None:
            if current:
                raw_blocks.append("\n".join(current).strip("\n"))
                current = []
            continue
        current.append(line)
    if current:
        raw_blocks.append("\n".join(current).strip("\n"))

    blocks: list[_SourceBlock] = []
    for position, value in enumerate(raw_blocks):
        digest = _sha256_text(value)
        blocks.append(
            _SourceBlock(
                block_id=f"block-{position + 1:04d}-{digest[:8]}",
                position=position,
                kind=_block_kind(value),
                markdown=value,
                source_sha256=digest,
                protected_tokens=_protected_tokens(value),
            )
        )
    if not blocks:
        raise ValueError("source manuscript contains no Markdown blocks")
    return blocks


def _decision_labels(decision: TermDecision) -> list[str]:
    return sorted(
        list(dict.fromkeys([decision.english, *decision.aliases])),
        key=len,
        reverse=True,
    )


def _decisions_for_block(block: _SourceBlock, plan: TermPlan) -> list[TermDecision]:
    matches: list[tuple[int, int, TermDecision]] = []
    for decision in plan.decisions:
        for label in _decision_labels(decision):
            for match in _term_pattern(label).finditer(block.markdown):
                matches.append((match.start(), match.end(), decision))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2].decision_id))
    selected: list[tuple[int, int, TermDecision]] = []
    for match in matches:
        if any(match[0] < item[1] and item[0] < match[1] for item in selected):
            continue
        selected.append(match)
    decisions: list[TermDecision] = []
    seen: set[str] = set()
    for _, _, decision in selected:
        if decision.decision_id not in seen:
            decisions.append(decision)
            seen.add(decision.decision_id)
    return decisions


def _first_use_form(decision: TermDecision) -> str:
    if decision.first_use == "english_only":
        return decision.english
    if decision.first_use == "chinese_only":
        return decision.chinese
    return f"{decision.chinese}（{decision.english}）"


def _prompt_decisions(
    decisions: list[TermDecision], introduced: set[str]
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for decision in decisions:
        first = decision.decision_id not in introduced
        payload.append(
            {
                "term_id": decision.decision_id,
                "english": decision.english,
                "preferred_chinese": decision.chinese,
                "sense": decision.sense,
                "discouraged": decision.discouraged,
                "preserve_english": decision.preserve_english,
                "first_use_required": first,
                "required_form": _first_use_form(decision) if first else decision.chinese,
            }
        )
    return payload


def _make_localized_block(
    source: _SourceBlock,
    localized_markdown: str,
    term_ids: list[str],
) -> LocalizedBlock:
    localized = localized_markdown.strip("\n")
    return LocalizedBlock(
        block_id=source.block_id,
        position=source.position,
        kind=source.kind,
        source_sha256=source.source_sha256,
        localized_sha256=_sha256_text(localized),
        localized_markdown=localized,
        protected_tokens=source.protected_tokens,
        term_ids=term_ids,
    )


async def _localize_blocks(
    project: Path,
    source_blocks: list[_SourceBlock],
    plan: TermPlan,
) -> list[LocalizedBlock]:
    introduced: set[str] = set()
    localized: list[LocalizedBlock] = []
    for source in source_blocks:
        decisions = _decisions_for_block(source, plan)
        term_ids = [item.decision_id for item in decisions]
        if source.kind == "code":
            localized.append(_make_localized_block(source, source.markdown, term_ids))
            introduced.update(term_ids)
            continue
        prompt = json.dumps(
            {
                "language": plan.language,
                "block_id": source.block_id,
                "block_kind": source.kind,
                "source_markdown": source.markdown,
                "protected_tokens": source.protected_tokens,
                "frozen_terms": _prompt_decisions(decisions, introduced),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        draft = await agent_runtime.localize_markdown_block(prompt, cwd=project)
        if draft.block_id != source.block_id:
            raise ValueError(
                f"localizer returned block ID {draft.block_id!r}; expected {source.block_id!r}"
            )
        localized.append(_make_localized_block(source, draft.localized_markdown, term_ids))
        introduced.update(term_ids)
    return localized


def _remove_protected(text: str, protected: list[str]) -> str:
    value = text
    for token in sorted(protected, key=len, reverse=True):
        value = value.replace(token, "")
    return value


def _term_issues_by_block(
    source_blocks: list[_SourceBlock],
    localized_blocks: list[LocalizedBlock],
    plan: TermPlan,
) -> dict[str, list[str]]:
    localized_by_id = {item.block_id: item for item in localized_blocks}
    introduced: set[str] = set()
    issues: dict[str, list[str]] = {}
    for source in source_blocks:
        localized = localized_by_id.get(source.block_id)
        if localized is None:
            continue
        decisions = _decisions_for_block(source, plan)
        clean = _remove_protected(localized.localized_markdown, localized.protected_tokens)
        for decision in decisions:
            first = decision.decision_id not in introduced
            expected = _first_use_form(decision) if first else decision.chinese
            if expected not in clean:
                issues.setdefault(source.block_id, []).append(
                    f"term {decision.decision_id} must use {expected!r}"
                )
            discouraged_search = clean.replace(expected, "").replace(decision.chinese, "")
            for discouraged in decision.discouraged:
                if discouraged and discouraged in discouraged_search:
                    issues.setdefault(source.block_id, []).append(
                        f"term {decision.decision_id} uses discouraged form {discouraged!r}"
                    )
            if not first and not decision.preserve_english and source.kind not in {"list", "table"}:
                if any(_term_pattern(label).search(clean) for label in _decision_labels(decision)):
                    issues.setdefault(source.block_id, []).append(
                        f"term {decision.decision_id} repeats raw English after first use"
                    )
            introduced.add(decision.decision_id)
    return issues


async def _repair_terminology_once(
    project: Path,
    source_blocks: list[_SourceBlock],
    localized_blocks: list[LocalizedBlock],
    plan: TermPlan,
    issues: dict[str, list[str]],
) -> list[LocalizedBlock]:
    source_by_id = {item.block_id: item for item in source_blocks}
    localized_by_id = {item.block_id: item for item in localized_blocks}
    introduced: set[str] = set()
    repaired: list[LocalizedBlock] = []
    for source in source_blocks:
        current = localized_by_id[source.block_id]
        decisions = _decisions_for_block(source, plan)
        term_ids = [item.decision_id for item in decisions]
        block_issues = issues.get(source.block_id, [])
        if block_issues and source.kind != "code":
            prompt = json.dumps(
                {
                    "language": plan.language,
                    "block_id": source.block_id,
                    "source_markdown": source.markdown,
                    "current_localized_markdown": current.localized_markdown,
                    "protected_tokens": source.protected_tokens,
                    "terminology_violations": block_issues,
                    "frozen_terms": _prompt_decisions(decisions, introduced),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            draft = await agent_runtime.repair_localized_markdown_block(prompt, cwd=project)
            if draft.block_id != source.block_id:
                raise ValueError(
                    f"repairer returned block ID {draft.block_id!r}; expected {source.block_id!r}"
                )
            current = _make_localized_block(source, draft.localized_markdown, term_ids)
        repaired.append(current)
        introduced.update(term_ids)
    return repaired


def _render_manuscript(blocks: list[LocalizedBlock]) -> str:
    return "\n\n".join(item.localized_markdown for item in blocks).rstrip() + "\n"


def _heading_level(value: str) -> int | None:
    match = _HEADING_RE.match(value.lstrip())
    return len(match.group(1)) if match else None


def audit_localization(
    project: Path,
    *,
    language: str = "zh-CN",
    persist: bool = True,
) -> LocalizationAudit:
    if language != "zh-CN":
        raise ValueError(f"unsupported localization language: {language}")
    project = project.resolve()
    root = safe_relative(project, f"synthesis/localized/{language}")
    checks: dict[str, bool] = {}
    violations: list[str] = []
    warnings: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    plan_path = root / "term-plan.json"
    manifest_path = root / "manifest.json"
    manuscript_path = root / "manuscript.md"
    try:
        plan = TermPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        manifest = LocalizationManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        source_path = safe_relative(project, manifest.source_path)
        source = source_path.read_text(encoding="utf-8")
        source_blocks = parse_markdown_blocks(source)
        manuscript = manuscript_path.read_text(encoding="utf-8")
    except Exception as exc:
        audit = LocalizationAudit(
            passed=False,
            checks={"artifacts_loadable": False},
            violations=[f"localization artifacts are missing or invalid: {exc}"],
            warnings=[],
            provisional_term_ids=[],
        )
        if persist:
            write_json_atomic(root / "audit.json", audit)
        return audit

    checks["artifacts_loadable"] = True
    check(
        "artifact_paths_bound",
        manifest.source_path == plan.source_path
        and manifest.term_plan_path == f"synthesis/localized/{language}/term-plan.json"
        and manifest.manuscript_path == f"synthesis/localized/{language}/manuscript.md",
        "localization manifest paths are not bound to the frozen plan and output locations",
    )
    check(
        "source_hash_matches",
        sha256_file(source_path) == plan.source_sha256 == manifest.source_sha256,
        "source manuscript hash does not match the frozen term plan and manifest",
    )
    check(
        "term_plan_current",
        term_plan_is_current(project, plan),
        "term plan is stale relative to the source manuscript or termbase",
    )
    check(
        "term_plan_hash_matches",
        sha256_file(plan_path) == manifest.term_plan_sha256,
        "manifest term-plan hash does not match term-plan.json",
    )
    check(
        "manuscript_hash_matches",
        sha256_file(manuscript_path) == manifest.manuscript_sha256,
        "manifest manuscript hash does not match manuscript.md",
    )

    expected_ids = [item.block_id for item in source_blocks]
    actual_ids = [item.block_id for item in manifest.blocks]
    block_hashes_match = len(source_blocks) == len(manifest.blocks) and all(
        source_block.position == localized.position
        and source_block.source_sha256 == localized.source_sha256
        and localized.localized_sha256 == _sha256_text(localized.localized_markdown)
        for source_block, localized in zip(source_blocks, manifest.blocks, strict=False)
    )
    check(
        "block_coverage_complete",
        expected_ids == actual_ids and block_hashes_match,
        "localized block lineage is incomplete, reordered, or hash-invalid",
    )
    check(
        "manuscript_matches_manifest",
        manuscript == _render_manuscript(manifest.blocks),
        "localized manuscript cannot be reconstructed from the manifest blocks",
    )
    protected_ok = True
    for source_block, localized in zip(source_blocks, manifest.blocks, strict=False):
        for token in source_block.protected_tokens:
            if token not in localized.localized_markdown:
                protected_ok = False
                violations.append(
                    f"block {source_block.block_id} dropped protected token {token!r}"
                )
    checks["protected_tokens_preserved"] = protected_ok

    headings_ok = all(
        source.kind != "heading"
        or _heading_level(source.markdown) == _heading_level(localized.localized_markdown)
        for source, localized in zip(source_blocks, manifest.blocks, strict=False)
    )
    check(
        "heading_structure_preserved",
        headings_ok,
        "one or more localized headings changed Markdown level",
    )
    code_ok = all(
        source.kind != "code" or source.markdown == localized.localized_markdown
        for source, localized in zip(source_blocks, manifest.blocks, strict=False)
    )
    check("code_blocks_preserved", code_ok, "one or more code blocks changed")

    term_issues = _term_issues_by_block(source_blocks, manifest.blocks, plan)
    for block_id, block_issues in term_issues.items():
        violations.extend(f"block {block_id}: {item}" for item in block_issues)
    checks["terminology_consistent"] = not term_issues

    provisional = [
        item.decision_id for item in plan.decisions if item.status == "provisional_model_choice"
    ]
    if provisional:
        warnings.append(
            f"{len(provisional)} provisional model terminology choices await optional human review"
        )
    audit = LocalizationAudit(
        passed=bool(checks) and all(checks.values()),
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        warnings=warnings,
        source_sha256=manifest.source_sha256,
        manuscript_sha256=manifest.manuscript_sha256,
        provisional_term_ids=provisional,
    )
    if persist:
        write_json_atomic(root / "audit.json", audit)
    return audit


async def localize_project(
    project: Path,
    *,
    language: str = "zh-CN",
) -> LocalizationAudit:
    if language != "zh-CN":
        raise ValueError(f"unsupported localization language: {language}")
    project = project.resolve()
    try:
        plan = load_term_plan(project, language=language)
    except (FileNotFoundError, ValueError):
        plan = await prepare_terminology(project, language=language)
    else:
        if not term_plan_is_current(project, plan):
            plan = await prepare_terminology(project, language=language)

    source_path = safe_relative(project, plan.source_path or SOURCE_MANUSCRIPT)
    source = source_path.read_text(encoding="utf-8")
    source_blocks = parse_markdown_blocks(source)
    localized = await _localize_blocks(project, source_blocks, plan)
    initial_issues = _term_issues_by_block(source_blocks, localized, plan)
    repair_attempted = bool(initial_issues)
    if repair_attempted:
        localized = await _repair_terminology_once(
            project,
            source_blocks,
            localized,
            plan,
            initial_issues,
        )

    root = safe_relative(project, LOCALIZATION_ROOT)
    manuscript_path = root / "manuscript.md"
    term_plan_path = root / "term-plan.json"
    manuscript = _render_manuscript(localized)
    _write_text_atomic(manuscript_path, manuscript)
    manifest = LocalizationManifest(
        language=language,
        source_path=plan.source_path,
        source_sha256=plan.source_sha256,
        term_plan_path=f"{LOCALIZATION_ROOT}/term-plan.json",
        term_plan_sha256=sha256_file(term_plan_path),
        manuscript_path=f"{LOCALIZATION_ROOT}/manuscript.md",
        manuscript_sha256=sha256_file(manuscript_path),
        model=agent_runtime.model_name(),
        repair_attempted=repair_attempted,
        blocks=localized,
    )
    write_json_atomic(root / "manifest.json", manifest)
    return audit_localization(project, language=language, persist=True)
