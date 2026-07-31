from pathlib import Path

from research_forge.paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
    FigureSlot,
    HierarchicalPaperOutline,
    OutlineNode,
    RoleReview,
    TableSlot,
    academicize_publication_text,
    build_paper_artifacts,
    decide_panel,
    materialize_artifact_callouts,
    publication_code_identifiers,
    publication_internal_tokens,
    validate_publication_title,
    validate_outline,
)
from research_forge.paper_pipeline import GENERIC_JOURNAL_ARTICLE


def test_publication_text_uses_scholarly_terms_without_changing_audit_source() -> None:
    source = (
        "evaluator_policy uses frozen statistical rules; "
        "decision=unverifiable for formal-primary-task-b6b855d916b9ad5f."
    )

    rendered = academicize_publication_text(source)

    assert "evaluator_policy" not in rendered
    assert "frozen statistical rules" not in rendered
    assert "decision=unverifiable" not in rendered
    assert "formal-primary-task-b6b855d916b9ad5f" not in rendered
    assert source.startswith("evaluator_policy")


def test_publication_token_audit_ignores_citations_but_flags_internal_fields() -> None:
    text = (
        "[resource-12345678] paper_assets/figures/result.svg "
        "resource_selection_id=internal"
    )

    assert publication_internal_tokens(text) == [
        "resource_selection_id",
        "resource_selection_id=internal",
    ]


def _map() -> EvidenceClaimMap:
    return EvidenceClaimMap(
        track_id="track-1",
        frozen_conclusion="冻结结论",
        bindings=[
            EvidenceClaimBinding(
                claim_id="numeric-001",
                kind="result_metric",
                statement="metrics.accuracy=0.8145",
                evidence=[
                    EvidencePointer(
                        path="outputs/result.json",
                        sha256="a" * 64,
                        json_path="metrics.accuracy",
                        evidence_type="project_artifact",
                    )
                ],
                allowed_sections=["results"],
                claim_strength="comparative",
                evidence_status="bound",
            )
        ],
        verified_source_ids=["paper-01"],
        forbidden_moves=["invent evidence"],
        source_registry_sha256="b" * 64,
    )


def _outline() -> HierarchicalPaperOutline:
    sections = [
        OutlineNode(
            node_id=f"section-{item.key.replace('_', '-')}",
            section_key=item.key,
            heading=item.english_title,
            level=2,
            purpose="完成投稿体裁要求的本节论证任务。",
            argument="陈述严格绑定冻结主张和核验来源。",
            claim_ids=["numeric-001"] if item.key == "results" else [],
            source_ids=["paper-01"] if item.key == "related_work" else [],
            figure_slot_ids=["fig-accuracy"] if item.key == "results" else [],
            table_slot_ids=["tab-accuracy"] if item.key == "results" else [],
        )
        for item in GENERIC_JOURNAL_ARTICLE.sections
    ]
    return HierarchicalPaperOutline(
        title="证据约束论文写作流水线",
        thesis="冻结证据先于论文写作，并约束后续图表、表达和投稿格式。",
        abstract_moves=["背景", "目标", "方法", "结果", "结论"],
        sections=sections,
        figure_slots=[
            FigureSlot(
                slot_id="fig-accuracy",
                purpose="展示冻结准确率结果及其证据绑定。",
                evidence_claim_ids=["numeric-001"],
                source_paths=["outputs/result.json"],
                chart_type="bar",
                caption_contract="图 1：冻结准确率，仅描述当前任务结果。",
            )
        ],
        table_slots=[
            TableSlot(
                slot_id="tab-accuracy",
                purpose="列出冻结指标和精确来源路径。",
                evidence_claim_ids=["numeric-001"],
                source_paths=["outputs/result.json"],
                columns=["claim_id", "statement", "source_path"],
                caption_contract="表 1：冻结结果与来源绑定。",
            )
        ],
    )


def test_outline_and_artifacts_remain_bound_to_frozen_claims(tmp_path: Path) -> None:
    evidence = _map()
    outline = _outline()

    assert not validate_outline(
        outline,
        contract=GENERIC_JOURNAL_ARTICLE,
        evidence_claim_map=evidence,
    )
    manifest = build_paper_artifacts(
        tmp_path,
        outline=outline,
        evidence_claim_map=evidence,
    )

    assert manifest.ready
    assert all(record.data_sha256 for record in manifest.records)
    figure_record = next(record for record in manifest.records if record.kind == "figure")
    assert figure_record.renderer == "native_svg"
    assert figure_record.artifact_sha256
    assert (tmp_path / "stage_4_synthesis/paper_assets/data/fig-accuracy.csv").is_file()
    assert (tmp_path / "stage_4_synthesis/paper_assets/figures/fig-accuracy.svg").is_file()
    rendered = materialize_artifact_callouts(
        "[FIGURE:fig-accuracy]\n\n[TABLE:tab-accuracy]", manifest
    )
    assert "![图 1" in rendered
    assert "Table: " in rendered
    assert "| `numeric-001` |" in rendered


def test_reader_facing_title_rejects_internal_code_identifiers(
    tmp_path: Path,
) -> None:
    evidence = _map()
    outline = _outline().model_copy(
        update={
            "title": (
                "dual_quality_top5 与 v3_tech_quality_top5 的注册比较"
            )
        }
    )

    violations = validate_outline(
        outline,
        contract=GENERIC_JOURNAL_ARTICLE,
        evidence_claim_map=evidence,
    )

    assert publication_code_identifiers(outline.title) == [
        "dual_quality_top5",
        "v3_tech_quality_top5",
    ]
    assert any("internal implementation or audit identifiers" in item for item in violations)


def test_publication_title_validation_accepts_natural_academic_title() -> None:
    assert not validate_publication_title(
        "双重标准排序能否提升高收益事件识别？一项预注册配对评估"
    )
    assert validate_publication_title(
        "dual_quality_top5 与 v3_tech_quality_top5 的比较"
    )


def test_panel_veto_and_abstention_rules_are_deterministic() -> None:
    accepting = [
        RoleReview(role=role, artifact="outline", recommendation="accept")
        for role in ("feynman", "tukey", "shannon", "popper")
    ]
    assert decide_panel("outline", accepting).decision == "accept"

    two_abstain = [
        RoleReview(role="feynman", artifact="outline", recommendation="abstain", abstention_reason="missing context"),
        RoleReview(role="tukey", artifact="outline", recommendation="abstain", abstention_reason="missing data"),
        RoleReview(role="shannon", artifact="outline", recommendation="accept"),
        RoleReview(role="popper", artifact="outline", recommendation="accept"),
    ]
    assert decide_panel("outline", two_abstain).decision == "halt"
