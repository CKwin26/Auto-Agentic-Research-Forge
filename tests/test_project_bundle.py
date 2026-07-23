from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from research_forge.models import utc_now
from research_forge.paper_expansion import (
    PaperDraftSections,
    expand_project_bundle_paper,
    prepare_project_bundle_paper,
    verify_project_bundle_paper,
)
from research_forge.paper_authoring import (
    HierarchicalPaperOutline,
    OutlineNode,
    RoleReview,
)
from research_forge.project_bundle import (
    audit_project_bundle_loop,
    close_project_bundle_loop,
    inspect_project_bundle,
    verify_project_bundle_completion,
)
from research_forge.storage import read_json, sha256_file


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _stock_like_bundle(root: Path) -> dict[str, object]:
    protocol: dict[str, object] = {
        "version": "exit-signal-research-v1",
        "purpose": "test whether a winner-protection target reduces false exits of 20-day winners",
        "strategy": "extreme_ranker_top5",
        "periods": {
            "development": "2025",
            "retrospectiveEvaluation": "2026-01 through 2026-05",
        },
        "execution": "after-close trigger, next sellable open; fixed day-20 close is control",
        "evidenceRole": "retrospective_signal_diagnostic_only",
        "evaluationIsProspectiveBlindTest": False,
        "forbiddenInterpretations": [
            "claiming that the best observed variant is prospectively validated",
            "changing the stock picks after reading evaluation results",
        ],
        "sha256": "fixture-self-hash",
    }
    output = {
        "protocol": protocol,
        "dataAudit": {"trades": 165, "months": 33},
        "selectedEvaluation": {
            "fixed20": {
                "compoundMonthlyReturn": 0.4128409331,
                "earlyExitRate": 0.0,
            },
            "winnerGuard": {
                "compoundMonthlyReturn": 0.4161532356,
                "earlyExitRate": 0.04,
            },
        },
        "completion_gate": {
            "retrospective_only": True,
            "prospective_effectiveness_claim_allowed": False,
        },
    }
    _write_text(root / "README.md", "# A-share research project\n")
    _write_text(root / ".env.local", "OPENAI_API_KEY=must-not-be-copied\n")
    _write_json(root / "protocols" / "exit-signal-research-v1.json", protocol)
    _write_json(root / "outputs" / "exit-signal-research-v1.json", output)
    _write_text(
        root / "docs" / "exit-signal-research-v1-report.md",
        """# Extreme-winner exit signal study v1

## 结论

当前数据不支持简单增加量价或新闻特征就能获得稳定卖点，但赢家保护目标带来了小幅改善。现阶段不替换固定20日规则。

## 实验口径

The project used 165 historical selections across 33 months.
""",
    )
    _write_text(
        root / "docs" / "data-leakage-checklist.md",
        "# Leakage checklist\n- labels mature before training\n",
    )
    _write_text(
        root / "backend" / "exit_signal_research.py",
        'PROTOCOL = "exit-signal-research-v1"\n',
    )
    _write_text(
        root / "tests_py" / "test_exit_signal_research.py",
        'def test_protocol():\n    assert "exit-signal-research-v1"\n',
    )
    return protocol


def test_bundle_inspection_finds_complete_novelty_chain_and_excludes_secrets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stock"
    _stock_like_bundle(source)

    inspection = inspect_project_bundle(source)

    assert inspection.recommended_track_id == "exit-signal-research-v1"
    assert inspection.excluded_count >= 1
    candidate = inspection.candidates[0]
    assert candidate.artifact_chain_complete
    assert candidate.protocol_bound_to_output
    assert candidate.evidence_maturity == "retrospective"
    assert candidate.latest_artifact_at
    assert candidate.implementation_paths == ["backend/exit_signal_research.py"]
    assert candidate.test_paths == ["tests_py/test_exit_signal_research.py"]


def test_bundle_close_loop_separates_idea_verdict_from_working_paper(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stock"
    _stock_like_bundle(source)

    run = close_project_bundle_loop(
        source,
        output_root=tmp_path / "runs",
        name="stock-paper-test",
    )

    for stage in (
        "stage_1_discovery",
        "stage_2_protocol",
        "stage_3_experimentation",
        "stage_4_synthesis",
    ):
        manifest = read_json(run / stage / "resources.json")
        assert manifest["resources"]

    verdict = read_json(run / "stage_3_experimentation" / "idea_verdict.json")
    assert verdict["status"] == "mixed"
    assert verdict["idea_validated"] is False
    assert verdict["evidence_maturity"] == "retrospective"
    assert any(
        item["path"] == "selectedEvaluation.fixed20.compoundMonthlyReturn"
        for item in verdict["numeric_evidence"]
    )

    manuscript = (run / "stage_4_synthesis" / "manuscript.md").read_text(encoding="utf-8")
    assert "## 研究问题与候选新颖性" in manuscript
    assert "selectedEvaluation.fixed20.compoundMonthlyReturn" in manuscript
    assert verdict["conclusion"] in manuscript
    assert "idea_verdict.json" in manuscript

    portfolio = (run / "stage_1_discovery" / "novelty_candidates.md").read_text(
        encoding="utf-8"
    )
    assert "# 项目包创新候选组合" in portfolio
    assert "exit-signal-research-v1（本轮选中）" in portfolio
    assert "已有结论" in portfolio

    manifest_text = (run / "bundle_manifest.json").read_text(encoding="utf-8")
    assert ".env.local" not in manifest_text
    assert "must-not-be-copied" not in manifest_text
    assert not (run / "source_snapshot" / ".env.local").exists()

    audit = audit_project_bundle_loop(run)
    assert audit.passed
    assert audit.pilot_draft_generated
    assert not audit.manuscript_depth_passed
    assert not audit.paper_draft_ready
    assert not audit.publication_ready
    assert "prospective blind" in " ".join(audit.publication_blockers)

    completion = verify_project_bundle_completion(run)
    assert completion["passed"]
    assert completion["idea_status"] == "mixed"
    assert completion["pilot_draft_generated"]
    assert not completion["manuscript_depth_passed"]
    assert not completion["paper_draft_ready"]
    assert not completion["publication_ready"]

    expansion = prepare_project_bundle_paper(run, persist=False)
    assert not expansion.ready
    assert expansion.diagnostic_owner == "idea_validation"
    assert not (run / "stage_4_synthesis" / "full_manuscript.md").exists()
    prepare_project_bundle_paper(run, persist=True)
    assert verify_project_bundle_completion(run)["passed"]


def test_bundle_completion_detects_snapshot_tampering(tmp_path: Path) -> None:
    source = tmp_path / "stock"
    _stock_like_bundle(source)
    run = close_project_bundle_loop(source, output_root=tmp_path / "runs")

    report = run / "source_snapshot" / "docs" / "exit-signal-research-v1-report.md"
    report.write_text(report.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")

    audit = audit_project_bundle_loop(run)
    assert not audit.passed
    assert any("snapshot" in item for item in audit.violations)
    completion = verify_project_bundle_completion(run)
    assert not completion["passed"]


def test_auto_selection_ignores_incomplete_newer_protocol(tmp_path: Path) -> None:
    source = tmp_path / "stock"
    _stock_like_bundle(source)
    _write_json(
        source / "protocols" / "unfinished-v2.json",
        {"version": "unfinished-v2", "purpose": "an incomplete newer idea"},
    )

    inspection = inspect_project_bundle(source)

    assert inspection.recommended_track_id == "exit-signal-research-v1"
    incomplete = next(item for item in inspection.candidates if item.track_id == "unfinished-v2")
    assert not incomplete.artifact_chain_complete


def test_clean_prospective_support_is_the_only_path_to_idea_validated(
    tmp_path: Path,
) -> None:
    source = tmp_path / "prospective"
    protocol = _stock_like_bundle(source)
    protocol["evaluationIsProspectiveBlindTest"] = True
    protocol["evidenceRole"] = "prospective_blind_test"
    _write_json(source / "protocols" / "exit-signal-research-v1.json", protocol)
    output = {
        "protocol": protocol,
        "prospectiveEvaluation": {
            "months": 12,
            "trades": 180,
            "compoundReturn": 0.18,
            "pairedBootstrap": {"probabilityPositive": 0.97},
        },
        "completion_gate": {"prospective_effectiveness_claim_allowed": True},
    }
    _write_json(source / "outputs" / "exit-signal-research-v1.json", output)
    _write_text(
        source / "docs" / "exit-signal-research-v1-report.md",
        """# Prospective winner-protection study

## 结论

冻结后的前瞻盲测显示赢家保护目标有效，配对改善达到预设门槛。
""",
    )

    run = close_project_bundle_loop(source, output_root=tmp_path / "runs")
    verdict = read_json(run / "stage_3_experimentation" / "idea_verdict.json")

    assert verdict["status"] == "supported"
    assert verdict["evidence_maturity"] == "prospective_blind"
    assert verdict["idea_validated"] is True
    audit = audit_project_bundle_loop(run)
    assert audit.pilot_draft_generated
    assert not audit.manuscript_depth_passed
    assert not audit.paper_draft_ready
    assert not audit.publication_ready
    assert "external scholarly novelty and citations have not been verified" in (
        audit.publication_blockers
    )
    assert any(
        item.startswith("journal-article manuscript depth gate failed:")
        for item in audit.publication_blockers
    )
    expansion = prepare_project_bundle_paper(run, persist=False)
    assert not expansion.ready
    assert expansion.idea_gate_passed
    assert not expansion.evidence_gate_passed
    assert expansion.diagnostic_owner == "evidence_packaging"


def _substantive_paragraphs(
    label: str,
    count: int,
    repeats: int,
    *,
    subsection_every: int = 0,
    citations: list[str] | None = None,
) -> str:
    paragraphs: list[str] = []
    citations = citations or []
    for index in range(count):
        if subsection_every and index % subsection_every == 0:
            paragraphs.append(f"### {label}分析维度{index // subsection_every + 1}")
        cited = " ".join(
            f"[{source_id}]"
            for position, source_id in enumerate(citations)
            if position % count == index
        )
        sentence = (
            f"{label}第{index + 1}段严格围绕冻结边界展开，区分观察、解释、替代机制与可复核限制，"
            "所有判断均回到协议、机器结果和来源记录，不把候选贡献升级为超出证据的普遍结论。"
        )
        paragraphs.append((sentence * repeats) + cited)
    return "\n\n".join(paragraphs)


def _full_draft(verdict: dict[str, object], source_ids: list[str]) -> PaperDraftSections:
    conclusion = str(verdict["conclusion"])
    return PaperDraftSections(
        title="冻结前瞻证据下的赢家保护目标验证研究",
        abstract=(
            "本研究在预先冻结的研究问题、对照规则和前瞻盲测窗口下，检验赢家保护目标能否改善既定评价结果。"
            "系统把想法判定、机器证据、论文写作与最终审计分离，所有数值由本地渲染器直接绑定，文献仅来自冻结清单。"
            "结果解释保持在协议允许范围内，并将替代解释、适用边界和复现条件作为结论的一部分。"
        ) * 3,
        introduction=_substantive_paragraphs("引言", 5, 6),
        related_work=_substantive_paragraphs(
            "相关工作", 6, 6, citations=source_ids
        ),
        methods=_substantive_paragraphs("方法", 8, 7, subsection_every=2),
        results=_substantive_paragraphs("结果", 7, 7, subsection_every=2),
        discussion=_substantive_paragraphs("讨论", 7, 7, subsection_every=2),
        limitations=_substantive_paragraphs("局限性", 3, 5),
        conclusion=conclusion + "\n\n" + _substantive_paragraphs("结论", 2, 4),
        data_availability="冻结协议、机器输出、声明文件和资源哈希保存在本次运行目录；公开发布位置尚待作者确认。",
        ethics_statement="本自动化测试不涉及新增人类参与者或动物实验；真实研究的伦理适用性仍需作者和机构确认。",
        author_contributions="研究构思、数据责任、软件、分析、写作与最终批准的具体作者分工尚待真实作者按 CRediT 口径确认。",
        conflict_of_interest="未向系统提供利益冲突信息，提交前必须由全体作者确认。",
        funding="未向系统提供资助信息，提交前必须由全体作者确认。",
        ai_disclosure="论文候选稿由 Research Forge 写作代理辅助形成；冻结结论、数值表、引用列表和完整性审计由确定性程序约束，作者仍须逐项复核并承担责任。",
    )


def _full_outline(source_ids: list[str]) -> HierarchicalPaperOutline:
    section_keys = [
        "abstract",
        "introduction",
        "related_work",
        "methods",
        "results",
        "discussion",
        "limitations",
        "conclusion",
        "data_availability",
        "ethics_statement",
        "author_contributions",
        "conflict_of_interest",
        "funding",
        "ai_disclosure",
        "references",
    ]
    return HierarchicalPaperOutline(
        title="冻结前瞻证据下的赢家保护目标验证研究",
        thesis="本研究只在冻结的项目证据和核验文献边界内组织方法、结果与受限结论。",
        abstract_moves=["背景问题", "研究目标", "冻结方法", "主要结果", "边界结论"],
        sections=[
            OutlineNode(
                node_id=f"section-{key.replace('_', '-')}",
                section_key=key,
                heading=key,
                level=2,
                purpose="按投稿体裁完成本节的明确论证任务。",
                argument="所有陈述只使用冻结主张和已经核验的来源记录。",
                claim_ids=["result-001"] if key in {"abstract", "results", "discussion", "conclusion"} else [],
                source_ids=source_ids if key == "related_work" else [],
            )
            for key in section_keys
        ],
    )


def test_full_paper_expansion_has_separate_success_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "prospective-with-literature"
    protocol = _stock_like_bundle(source)
    protocol["evaluationIsProspectiveBlindTest"] = True
    protocol["evidenceRole"] = "prospective_blind_test"
    _write_json(source / "protocols" / "exit-signal-research-v1.json", protocol)
    output = {
        "protocol": protocol,
        "prospectiveEvaluation": {
            "months": 12,
            "trades": 180,
            **{f"evaluationMetric{index}": round(0.1 + index / 100, 3) for index in range(1, 18)},
        },
        "completion_gate": {"prospective_effectiveness_claim_allowed": True},
    }
    _write_json(source / "outputs" / "exit-signal-research-v1.json", output)
    _write_text(
        source / "docs" / "exit-signal-research-v1-report.md",
        "# 前瞻赢家保护研究\n\n## 结论\n\n冻结后的前瞻盲测显示赢家保护目标有效，配对改善达到预设门槛。\n",
    )

    source_ids: list[str] = []
    hashes: dict[str, str] = {}
    for index in range(1, 16):
        source_id = f"paper-{index:02d}"
        source_ids.append(source_id)
        path = source / "literature" / "sources" / f"{source_id}.json"
        _write_json(
            path,
            {
                "schema_version": 1,
                "source_id": source_id,
                "source_type": "paper",
                "title": f"Verified winner research source {index}",
                "authors": [f"Author {index}"],
                "year": 2020 + (index % 6),
                "locator": f"https://doi.org/10.1000/test.{index}",
                "notes": f"Verified abstract-level note for bounded comparison {index}.",
                "verified": True,
                "verification_method": "canonical DOI fixture verification",
                "origin": "manual",
                "origin_id": None,
                "added_at": utc_now(),
            },
        )
        hashes[source_id] = sha256_file(path)
    _write_json(
        source / "literature_manifest.json",
        {"schema_version": 1, "frozen_at": utc_now(), "source_hashes": hashes},
    )

    run = close_project_bundle_loop(source, output_root=tmp_path / "runs")
    plan = prepare_project_bundle_paper(run, persist=False)
    assert plan.ready
    assert plan.idea_gate_passed
    assert plan.evidence_gate_passed
    assert plan.literature_gate_passed
    assert plan.verified_paper_count == 15

    verdict = read_json(run / "stage_3_experimentation" / "idea_verdict.json")

    async def fake_writer(prompt: str, *, cwd: Path | None = None) -> PaperDraftSections:
        assert "paper-15" in prompt
        assert "approved_hierarchical_outline" in prompt
        assert cwd == run.resolve()
        return _full_draft(verdict, source_ids)

    async def fake_outline(prompt: str, *, cwd: Path | None = None) -> HierarchicalPaperOutline:
        assert "evidence_claim_map" in prompt
        assert cwd == run.resolve()
        return _full_outline(source_ids)

    async def fake_review(prompt: str, *, cwd: Path | None = None) -> RoleReview:
        payload = json.loads(prompt)
        return RoleReview(
            role=payload["required_role"],
            artifact=payload["required_artifact"],
            recommendation="accept",
        )

    async def fake_humanizer(prompt: str, *, cwd: Path | None = None) -> PaperDraftSections:
        payload = json.loads(prompt)
        return PaperDraftSections.model_validate(payload["approved_draft"])

    import research_forge.agent_runtime as agent_runtime

    monkeypatch.setattr(agent_runtime, "generate_bundle_paper_draft", fake_writer)
    monkeypatch.setattr(agent_runtime, "generate_bundle_paper_outline", fake_outline)
    monkeypatch.setattr(agent_runtime, "review_bundle_paper_artifact", fake_review)
    monkeypatch.setattr(agent_runtime, "humanize_bundle_paper_draft", fake_humanizer)
    audit = asyncio.run(expand_project_bundle_paper(run))

    assert audit.full_manuscript_generated
    assert audit.manuscript_depth_passed
    assert audit.citation_integrity_passed
    assert audit.conclusion_binding_passed
    assert audit.numeric_evidence_binding_passed
    assert audit.paper_draft_ready
    assert not audit.publication_ready
    assert audit.diagnostic_owner == "none"

    manuscript = (run / "stage_4_synthesis" / "full_manuscript.md").read_text(
        encoding="utf-8"
    )
    assert verdict["conclusion"] in manuscript
    assert "[paper-15]" in manuscript
    assert "prospectiveEvaluation.evaluationMetric17" in manuscript
    verification = verify_project_bundle_paper(run)
    assert verification["passed"]
    assert verification["paper_draft_ready"]
    assert not verification["publication_ready"]
    assert verify_project_bundle_completion(run)["passed"]


def test_generic_project_materials_close_four_stage_loop_as_unverifiable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "retention-project"
    _write_text(
        source / "README.md",
        """# 睡前提醒与次日留存研究

## 研究问题

睡前提醒是否能够提高新用户次日留存率？

## 方法

项目记录了两个用户批次，但尚未冻结分组、基线和评价协议。
""",
    )
    _write_text(
        source / "notes" / "findings.md",
        """# 睡前提醒观察记录

## 结论

现有观察中提醒组的次日留存率更高，但分组并非预先冻结，不能判断提醒是否有效。
""",
    )
    _write_json(
        source / "data" / "observations.json",
        {
            "selectedEvaluation": {
                "users": 80,
                "reminderRetentionRate": 0.62,
                "controlRetentionRate": 0.54,
            }
        },
    )
    _write_text(source / "src" / "analyze.py", "print('observations only')\n")
    _write_text(
        source / "tests" / "test_analysis.py",
        "def test_observations_exist():\n    assert True\n",
    )

    inspection = inspect_project_bundle(source)

    assert inspection.recommended_track_id is not None
    candidate = next(
        item
        for item in inspection.candidates
        if item.track_id == inspection.recommended_track_id
    )
    assert candidate.source_mode == "derived_materials"
    assert candidate.closure_input_ready
    assert not candidate.artifact_chain_complete
    assert not candidate.protocol_bound_to_output
    assert candidate.protocol_path == "README.md"
    assert candidate.output_path == "data/observations.json"
    assert candidate.report_path == "notes/findings.md"
    assert "睡前提醒" in candidate.novelty_seed

    run = close_project_bundle_loop(
        source,
        output_root=tmp_path / "runs",
        name="generic-materials-test",
    )

    for stage in (
        "stage_1_discovery",
        "stage_2_protocol",
        "stage_3_experimentation",
        "stage_4_synthesis",
    ):
        manifest = read_json(run / stage / "resources.json")
        assert manifest["resources"]

    scope = read_json(run / "stage_1_discovery" / "scope_contract.json")
    lock = read_json(run / "stage_2_protocol" / "protocol_lock.json")
    verdict = read_json(run / "stage_3_experimentation" / "idea_verdict.json")
    audit = audit_project_bundle_loop(run)
    completion = verify_project_bundle_completion(run)

    assert scope["source_mode"] == "derived_materials"
    assert scope["research_question"] == "睡前提醒是否能够提高新用户次日留存率？"
    assert lock["source_mode"] == "derived_materials"
    assert lock["protocol_bound_to_output"] is False
    assert verdict["status"] == "unverifiable"
    assert verdict["idea_validated"] is False
    assert verdict["protocol_bound_to_output"] is False
    assert verdict["numeric_evidence"]
    assert audit.passed
    assert audit.checks["protocol_bound_to_output"] is False
    assert audit.checks["evidence_binding_policy_valid"] is True
    assert audit.checks["derived_materials_verdict_guard"] is True
    assert audit.pilot_draft_generated
    assert not audit.paper_draft_ready
    assert completion["passed"]
    assert completion["idea_status"] == "unverifiable"


def test_single_text_library_can_close_without_inventing_experimental_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "text-library"
    _write_text(
        source / "资料库.txt",
        """研究问题：不同复习间隔是否影响知识保持率？

材料说明：这里汇总了项目访谈和研究笔记，没有冻结实验方案或机器结果。

结论：现有资料只能提出候选问题，不能判断哪一种复习间隔有效。
""",
    )

    inspection = inspect_project_bundle(source)
    assert inspection.resource_count == 1
    candidate = inspection.candidates[0]
    assert candidate.source_mode == "derived_materials"
    assert candidate.protocol_path == "资料库.txt"
    assert candidate.output_path == "资料库.txt"
    assert candidate.report_path == "资料库.txt"

    run = close_project_bundle_loop(source, output_root=tmp_path / "runs")
    verdict = read_json(run / "stage_3_experimentation" / "idea_verdict.json")
    manuscript = (run / "stage_4_synthesis" / "manuscript.md").read_text(
        encoding="utf-8"
    )
    completion = verify_project_bundle_completion(run)

    assert verdict["status"] == "unverifiable"
    assert verdict["numeric_evidence"] == []
    assert "未发现可绑定到冻结实验协议的机器可读数值结果" in manuscript
    assert completion["passed"]
    assert completion["pilot_draft_generated"]
    assert not completion["paper_draft_ready"]
