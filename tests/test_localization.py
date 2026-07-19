from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import yaml

from research_forge.cli import _parser
from research_forge.localization import audit_localization, localize_project
from research_forge.models import (
    LocalizedBlockDraft,
    TermCandidate,
    TermCandidateBatch,
)
from research_forge.terminology import (
    import_terminology_review,
    load_termbase,
    prepare_terminology,
    resolve_term_decisions,
)


MANUSCRIPT = """# Agent Reliability Study

The Research Forge/Codex backbone uses a system prompt and prompt template.

The same backbone checks each claim in the baseline arm and treatment arm.

A reliability envelope records 10.00% at `run-1`.
"""


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "future-project"
    (project / "synthesis").mkdir(parents=True)
    (project / "synthesis" / "manuscript.md").write_text(
        MANUSCRIPT, encoding="utf-8", newline="\n"
    )
    return project


async def _fake_extract(prompt: str, *, cwd=None) -> TermCandidateBatch:
    payload = json.loads(prompt)
    assert "reliability envelope" in payload["manuscript"]
    return TermCandidateBatch(
        candidates=[
            TermCandidate(
                english="reliability envelope",
                suggested_chinese="可靠性边界",
                sense="系统结果可被可靠解释的适用边界",
                domain="ai-agents",
                context="A reliability envelope records 10.00% at `run-1`.",
            )
        ]
    )


async def _fake_localize(prompt: str, *, cwd=None) -> LocalizedBlockDraft:
    payload = json.loads(prompt)
    block_id = payload["block_id"]
    source = payload["source_markdown"]
    if source.startswith("#"):
        localized = "# 代理可靠性研究"
    else:
        terms = [item["required_form"] for item in payload["frozen_terms"]]
        tokens = payload["protected_tokens"]
        pieces = [*terms, *tokens] or ["中文学术段落"]
        localized = "；".join(pieces) + "。"
    return LocalizedBlockDraft(
        block_id=block_id,
        localized_markdown=localized,
        term_ids_used=[item["term_id"] for item in payload["frozen_terms"]],
    )


def test_context_disambiguation_and_longest_phrase_matching(tmp_path: Path) -> None:
    entries, _ = load_termbase(tmp_path)
    source = (
        "The frozen Research Forge/Codex backbone shares a runtime and controller. "
        "A system prompt and prompt template configure the agent."
    )
    decisions = resolve_term_decisions(source, entries, [])
    by_english = {item.english: item for item in decisions}
    assert by_english["backbone"].chinese == "基础系统"
    assert by_english["system prompt"].chinese == "系统提示词"
    assert by_english["prompt template"].chinese == "提示模板"
    assert "prompt" not in by_english


def test_project_termbase_overrides_bundled_entry(tmp_path: Path) -> None:
    project = _project(tmp_path)
    termbase = project / "terminology" / "project.zh-CN.yaml"
    termbase.parent.mkdir(parents=True)
    termbase.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "pack_id": "project-terms-zh-cn",
                "language": "zh-CN",
                "version": "1.0.0",
                "terms": [
                    {
                        "schema_version": 1,
                        "term_id": "project-backbone",
                        "english": "backbone",
                        "chinese": "系统底座",
                        "sense": "本项目对共享底层系统的批准称呼",
                        "domain": "project",
                        "aliases": [],
                        "discouraged": [],
                        "context_keywords": [],
                        "first_use": "chinese_english",
                        "preserve_english": False,
                        "source": "human review",
                        "version": "1.0.0",
                        "status": "approved",
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    entries, _ = load_termbase(project)
    decisions = resolve_term_decisions(
        "The frozen Research Forge/Codex backbone shares the prompt runtime.",
        entries,
        [],
    )
    backbone = next(item for item in decisions if item.english == "backbone")
    assert backbone.chinese == "系统底座"
    assert backbone.source_scope == "project"


def test_localization_pipeline_and_optional_review_import(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    source_path = project / "synthesis" / "manuscript.md"
    original_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "research_forge.agent_runtime.extract_terminology_candidates", _fake_extract
    )
    monkeypatch.setattr(
        "research_forge.agent_runtime.localize_markdown_block", _fake_localize
    )
    monkeypatch.setattr(
        "research_forge.agent_runtime.repair_localized_markdown_block", _fake_localize
    )

    audit = asyncio.run(localize_project(project))
    assert audit.passed
    assert audit.provisional_term_ids
    assert original_hash == hashlib.sha256(source_path.read_bytes()).hexdigest()

    output = project / "synthesis" / "localized" / "zh-CN"
    assert (output / "manuscript.md").is_file()
    assert (output / "term-plan.json").is_file()
    assert (output / "manifest.json").is_file()
    assert (output / "audit.json").is_file()
    review_path = output / "term-review.yaml"
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    assert review["items"][0]["status"] == "pending"
    review["items"][0]["status"] = "approved"
    review_path.write_text(
        yaml.safe_dump(review, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    imported = import_terminology_review(project, review_path)
    assert len(imported["imported_term_ids"]) == 1
    project_pack = yaml.safe_load(
        (project / "terminology" / "project.zh-CN.yaml").read_text(encoding="utf-8")
    )
    assert [item["english"] for item in project_pack["terms"]] == [
        "reliability envelope"
    ]

    refreshed = asyncio.run(prepare_terminology(project))
    reliability = next(
        item for item in refreshed.decisions if item.english == "reliability envelope"
    )
    assert reliability.status == "approved"
    assert reliability.source_scope == "project"


def test_audit_detects_source_change(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        "research_forge.agent_runtime.extract_terminology_candidates", _fake_extract
    )
    monkeypatch.setattr(
        "research_forge.agent_runtime.localize_markdown_block", _fake_localize
    )
    monkeypatch.setattr(
        "research_forge.agent_runtime.repair_localized_markdown_block", _fake_localize
    )
    assert asyncio.run(localize_project(project)).passed
    source = project / "synthesis" / "manuscript.md"
    source.write_text(MANUSCRIPT + "\nA new result appears.\n", encoding="utf-8")
    audit = audit_localization(project)
    assert not audit.passed
    assert not audit.checks["source_hash_matches"]
    assert not audit.checks["term_plan_current"]


def test_localization_repairs_terminology_once(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    repair_calls: list[str] = []

    async def bad_first_pass(prompt: str, *, cwd=None) -> LocalizedBlockDraft:
        draft = await _fake_localize(prompt, cwd=cwd)
        payload = json.loads(prompt)
        backbone = next(
            (
                item
                for item in payload["frozen_terms"]
                if item["term_id"] == "agent-system-backbone"
            ),
            None,
        )
        if backbone:
            draft.localized_markdown = draft.localized_markdown.replace(
                backbone["required_form"], "系统骨干"
            )
        return draft

    async def repair(prompt: str, *, cwd=None) -> LocalizedBlockDraft:
        payload = json.loads(prompt)
        repair_calls.append(payload["block_id"])
        return await _fake_localize(prompt, cwd=cwd)

    monkeypatch.setattr(
        "research_forge.agent_runtime.extract_terminology_candidates", _fake_extract
    )
    monkeypatch.setattr(
        "research_forge.agent_runtime.localize_markdown_block", bad_first_pass
    )
    monkeypatch.setattr(
        "research_forge.agent_runtime.repair_localized_markdown_block", repair
    )
    audit = asyncio.run(localize_project(project))
    assert audit.passed
    assert len(repair_calls) == 2
    manifest = json.loads(
        (
            project / "synthesis" / "localized" / "zh-CN" / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["repair_attempted"] is True


def test_cli_exposes_terminology_and_localization_commands() -> None:
    parser = _parser()
    prepared = parser.parse_args(["terminology", "prepare", "future-project"])
    assert prepared.command == "terminology"
    assert prepared.terminology_command == "prepare"
    localized = parser.parse_args(["localize", "future-project"])
    assert localized.command == "localize"
    audited = parser.parse_args(["audit-localization", "future-project"])
    assert audited.command == "audit-localization"
