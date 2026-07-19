from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from . import agent_runtime
from .models import (
    TermCandidate,
    TermDecision,
    TermEntry,
    TermPack,
    TermPlan,
    TermReview,
    TermReviewItem,
)
from .storage import safe_relative, sha256_file, write_json_atomic


SUPPORTED_LANGUAGES = {"zh-CN"}
SOURCE_MANUSCRIPT = "synthesis/manuscript.md"
LOCALIZATION_ROOT = "synthesis/localized/zh-CN"
PROJECT_TERMBASE = "terminology/project.zh-CN.yaml"
_BUNDLED_ROOT = Path(__file__).resolve().parent / "resources" / "terminology"
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _LoadedEntry:
    entry: TermEntry
    scope: str


@dataclass(frozen=True)
class _Occurrence:
    start: int
    end: int
    canonical: str
    matched: str
    context: str


def _validate_language(language: str) -> None:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported localization language: {language}")


def _normalize(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip().casefold())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_pack(path: Path) -> TermPack:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML object in {path}")
    return TermPack.model_validate(value)


def _dump_yaml(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return yaml.safe_dump(
        value,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def load_termbase(
    project: Path, *, language: str = "zh-CN"
) -> tuple[list[_LoadedEntry], dict[str, str]]:
    _validate_language(language)
    entries: list[_LoadedEntry] = []
    hashes: dict[str, str] = {}
    for path in sorted(_BUNDLED_ROOT.glob("*.yaml")):
        pack = _read_pack(path)
        if pack.language != language:
            continue
        hashes[f"bundled/{path.name}"] = sha256_file(path)
        entries.extend(_LoadedEntry(entry=item, scope="bundled") for item in pack.terms)
    project_path = safe_relative(project, PROJECT_TERMBASE)
    if project_path.is_file():
        pack = _read_pack(project_path)
        if pack.language != language:
            raise ValueError(
                f"project termbase language {pack.language!r} does not match {language!r}"
            )
        hashes[PROJECT_TERMBASE] = sha256_file(project_path)
        entries.extend(_LoadedEntry(entry=item, scope="project") for item in pack.terms)
    term_ids = [item.entry.term_id for item in entries]
    if len(term_ids) != len(set(term_ids)):
        duplicates = sorted(item for item, count in Counter(term_ids).items() if count > 1)
        raise ValueError(f"duplicate terminology IDs: {', '.join(duplicates)}")
    return entries, hashes


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    prefix = r"(?<![A-Za-z0-9_])" if term[:1].isalnum() else ""
    suffix = r"(?![A-Za-z0-9_])" if term[-1:].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def _context_for(source: str, start: int, end: int) -> str:
    left = source.rfind("\n\n", 0, start)
    right = source.find("\n\n", end)
    left = 0 if left < 0 else left + 2
    right = len(source) if right < 0 else right
    return source[left:right].strip()[:1000]


def _known_occurrences(source: str, entries: Iterable[_LoadedEntry]) -> list[_Occurrence]:
    label_to_canonical: dict[str, tuple[str, str]] = {}
    for loaded in entries:
        entry = loaded.entry
        canonical = _normalize(entry.english)
        for label in [entry.english, *entry.aliases]:
            normalized = _normalize(label)
            current = label_to_canonical.get(normalized)
            if current is None or len(label) > len(current[1]):
                label_to_canonical[normalized] = (canonical, label)

    candidates: list[_Occurrence] = []
    for canonical, label in label_to_canonical.values():
        for match in _term_pattern(label).finditer(source):
            candidates.append(
                _Occurrence(
                    start=match.start(),
                    end=match.end(),
                    canonical=canonical,
                    matched=match.group(0),
                    context=_context_for(source, match.start(), match.end()),
                )
            )
    candidates.sort(key=lambda item: (item.start, -(item.end - item.start), item.canonical))
    selected: list[_Occurrence] = []
    for candidate in candidates:
        if any(candidate.start < item.end and item.start < candidate.end for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item.start)


def _score_entry(entry: TermEntry, contexts: list[str], suggestion: str | None) -> int:
    haystack = "\n".join(contexts).casefold()
    score = sum(haystack.count(keyword.casefold()) for keyword in entry.context_keywords)
    if suggestion and _normalize(suggestion) == _normalize(entry.chinese):
        score += 2
    return score


def _decision_from_entry(
    loaded: _LoadedEntry,
    occurrences: list[_Occurrence],
) -> TermDecision:
    entry = loaded.entry
    return TermDecision(
        decision_id=entry.term_id,
        english=entry.english,
        chinese=entry.chinese,
        sense=entry.sense,
        domain=entry.domain,
        aliases=entry.aliases,
        discouraged=entry.discouraged,
        first_use=entry.first_use,
        preserve_english=entry.preserve_english,
        status="approved",
        source_entry_id=entry.term_id,
        source_scope=loaded.scope,
        contexts=list(dict.fromkeys(item.context for item in occurrences))[:20],
        occurrence_count=len(occurrences),
        first_source_offset=min(item.start for item in occurrences),
    )


def resolve_term_decisions(
    source: str,
    entries: list[_LoadedEntry],
    model_candidates: list[TermCandidate],
) -> list[TermDecision]:
    by_canonical: dict[str, list[_LoadedEntry]] = {}
    alias_to_canonical: dict[str, str] = {}
    for loaded in entries:
        canonical = _normalize(loaded.entry.english)
        by_canonical.setdefault(canonical, []).append(loaded)
        for label in [loaded.entry.english, *loaded.entry.aliases]:
            alias_to_canonical[_normalize(label)] = canonical

    suggestions: dict[str, TermCandidate] = {}
    for candidate in model_candidates:
        normalized = _normalize(candidate.english)
        canonical = alias_to_canonical.get(normalized, normalized)
        suggestions.setdefault(canonical, candidate)

    occurrences_by_canonical: dict[str, list[_Occurrence]] = {}
    for occurrence in _known_occurrences(source, entries):
        occurrences_by_canonical.setdefault(occurrence.canonical, []).append(occurrence)

    decisions: list[TermDecision] = []
    for canonical, occurrences in occurrences_by_canonical.items():
        choices = by_canonical[canonical]
        project_choices = [item for item in choices if item.scope == "project"]
        if project_choices:
            choices = project_choices
        contexts = [item.context for item in occurrences]
        suggestion = suggestions.get(canonical)
        chosen = sorted(
            choices,
            key=lambda item: (
                -_score_entry(
                    item.entry,
                    contexts,
                    suggestion.suggested_chinese if suggestion else None,
                ),
                item.entry.term_id,
            ),
        )[0]
        decisions.append(_decision_from_entry(chosen, occurrences))

    resolved_labels = set(occurrences_by_canonical)
    for candidate in model_candidates:
        normalized = _normalize(candidate.english)
        canonical = alias_to_canonical.get(normalized, normalized)
        if canonical in resolved_labels:
            continue
        matches = list(_term_pattern(candidate.english).finditer(source))
        if not matches:
            continue
        digest = _sha256_text(
            f"{normalized}\0{_normalize(candidate.sense)}\0{_normalize(candidate.suggested_chinese)}"
        )[:16]
        decisions.append(
            TermDecision(
                decision_id=f"provisional-{digest}",
                english=candidate.english,
                chinese=candidate.suggested_chinese,
                sense=candidate.sense,
                domain=candidate.domain,
                aliases=[],
                discouraged=[],
                first_use="chinese_english",
                preserve_english=False,
                status="provisional_model_choice",
                source_entry_id=None,
                source_scope="model",
                contexts=[candidate.context],
                occurrence_count=len(matches),
                first_source_offset=matches[0].start(),
            )
        )
        resolved_labels.add(canonical)
    return sorted(decisions, key=lambda item: (item.first_source_offset, -len(item.english)))


def _review_from_plan(plan: TermPlan) -> TermReview:
    return TermReview(
        source_sha256=plan.source_sha256,
        items=[
            TermReviewItem(
                decision_id=item.decision_id,
                english=item.english,
                chinese=item.chinese,
                sense=item.sense,
                domain=item.domain,
                context=item.contexts[0] if item.contexts else "",
                status="pending",
            )
            for item in plan.decisions
            if item.status == "provisional_model_choice"
        ],
    )


async def prepare_terminology(
    project: Path,
    *,
    language: str = "zh-CN",
    source_path: str = SOURCE_MANUSCRIPT,
) -> TermPlan:
    _validate_language(language)
    project = project.resolve()
    source_file = safe_relative(project, source_path)
    if not source_file.is_file():
        raise FileNotFoundError(f"source manuscript not found: {source_file}")
    source = source_file.read_text(encoding="utf-8")
    if not source.strip():
        raise ValueError("source manuscript is empty")
    entries, termbase_hashes = load_termbase(project, language=language)
    prompt = json.dumps(
        {
            "language": language,
            "task": "extract_reusable_scholarly_and_technical_terms",
            "manuscript": source,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    candidates = await agent_runtime.extract_terminology_candidates(prompt, cwd=project)
    decisions = resolve_term_decisions(source, entries, candidates.candidates)
    plan = TermPlan(
        language=language,
        source_path=source_path,
        source_sha256=sha256_file(source_file),
        model=agent_runtime.model_name(),
        selected_domains=sorted({item.domain for item in decisions}),
        termbase_hashes=termbase_hashes,
        decisions=decisions,
    )
    output_root = safe_relative(project, LOCALIZATION_ROOT)
    write_json_atomic(output_root / "term-plan.json", plan)
    _write_text_atomic(output_root / "term-review.yaml", _dump_yaml(_review_from_plan(plan)))
    return plan


def load_term_plan(project: Path, *, language: str = "zh-CN") -> TermPlan:
    _validate_language(language)
    path = safe_relative(project.resolve(), f"synthesis/localized/{language}/term-plan.json")
    if not path.is_file():
        raise FileNotFoundError(f"term plan not found: {path}")
    return TermPlan.model_validate_json(path.read_text(encoding="utf-8"))


def term_plan_is_current(project: Path, plan: TermPlan) -> bool:
    source = safe_relative(project.resolve(), plan.source_path)
    if not source.is_file() or sha256_file(source) != plan.source_sha256:
        return False
    _, current_hashes = load_termbase(project.resolve(), language=plan.language)
    return current_hashes == plan.termbase_hashes


def import_terminology_review(
    project: Path,
    review_file: Path,
    *,
    language: str = "zh-CN",
) -> dict[str, object]:
    _validate_language(language)
    project = project.resolve()
    review_value = yaml.safe_load(review_file.resolve().read_text(encoding="utf-8"))
    if not isinstance(review_value, dict):
        raise ValueError(f"expected a YAML object in {review_file}")
    review = TermReview.model_validate(review_value)
    if review.language != language:
        raise ValueError(
            f"review language {review.language!r} does not match requested {language!r}"
        )
    approved = [item for item in review.items if item.status == "approved"]
    project_path = safe_relative(project, PROJECT_TERMBASE)
    if project_path.is_file():
        pack = _read_pack(project_path)
    else:
        pack = TermPack(
            pack_id="project-terms-zh-cn",
            language="zh-CN",
            version="1.0.0",
            terms=[],
        )
    terms = list(pack.terms)
    by_key = {(_normalize(item.english), _normalize(item.sense)): item for item in terms}
    imported: list[str] = []
    for item in approved:
        key = (_normalize(item.english), _normalize(item.sense))
        existing = by_key.get(key)
        if existing and _normalize(existing.chinese) != _normalize(item.chinese):
            raise ValueError(
                f"project term conflict for {item.english!r} ({item.sense}): "
                f"{existing.chinese!r} != {item.chinese!r}"
            )
        if existing:
            continue
        term_id = f"project-{_sha256_text(chr(0).join(key))[:16]}"
        entry = TermEntry(
            term_id=term_id,
            english=item.english,
            chinese=item.chinese,
            sense=item.sense,
            domain=item.domain,
            aliases=[],
            discouraged=[],
            context_keywords=[],
            first_use="chinese_english",
            preserve_english=False,
            source=f"Human-approved terminology review: {review_file.name}",
            version="1.0.0",
            status="approved",
        )
        terms.append(entry)
        by_key[key] = entry
        imported.append(term_id)
    if imported:
        updated = pack.model_copy(update={"terms": terms})
        _write_text_atomic(project_path, _dump_yaml(updated))
    return {
        "language": language,
        "approved_items": len(approved),
        "imported_term_ids": imported,
        "ignored_items": len(review.items) - len(approved),
        "project_termbase": PROJECT_TERMBASE,
    }
