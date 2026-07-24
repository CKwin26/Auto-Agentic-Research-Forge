from research_forge.paper_expansion import _numeric_table
from research_forge.paper_pipeline import GENERIC_JOURNAL_ARTICLE, markdown_heading
from research_forge.paper_typesetting import render_submission_latex


def _manuscript() -> str:
    abstract = (
        "自主科研代理可能在交付前产生缺乏证据支持的主张。"
        "本研究检验同骨干证据门控是否改变不支持率。"
        "系统冻结任务、协议、评价器和分析规则，并比较配对输出。"
        "冻结结果显示门控组在当前任务矩阵中的不支持率更低。"
        "该结论仅适用于本次冻结设计，不能外推为普遍因果结论。"
    )
    sections = [
        ("摘要", abstract + "\n\n**关键词：** 科研代理；证据；审计"),
        ("引言", "研究问题与边界。"),
        ("相关工作", "核验来源提供背景 [paper-01]。"),
        ("方法", "### 冻结设计\n\n方法绑定冻结协议。"),
        ("结果", "结果值为 `0.10`。"),
        ("讨论", "讨论替代解释。"),
        ("局限性", "当前任务矩阵有限。"),
        ("结论", "结论保持在证据边界内。"),
        ("数据与材料可得性", "材料路径已记录。"),
        ("伦理声明", "不涉及新增人类参与者。"),
        ("作者贡献", "作者信息待确认。"),
        ("利益冲突", "待作者确认。"),
        ("资助声明", "待作者确认。"),
        ("AI 使用披露", "使用了 Research Forge。"),
        ("参考文献", "- [paper-01] Author. Verified paper."),
    ]
    return "# 证据约束论文\n\n" + "\n\n".join(
        f"## {heading}\n\n{content}" for heading, content in sections
    )


def test_typesetter_owns_abstract_and_numbered_body_structure() -> None:
    latex = render_submission_latex(
        _manuscript(), contract=GENERIC_JOURNAL_ARTICLE, language="zh"
    )

    assert r"\documentclass[11pt]{ctexart}" in latex
    assert latex.count(r"\begin{abstract}") == 1
    assert r"\section{摘要}" not in latex
    assert r"\textbf{关键词:}" in latex
    assert r"\section{引言}" in latex
    assert r"\subsection{冻结设计}" in latex
    assert r"\section*{数据与材料可得性}" in latex
    assert r"\cite{paper-01}" in latex
    assert r"\bibitem{paper-01}" in latex


def test_typesetter_accepts_uppercase_reference_keys() -> None:
    manuscript = _manuscript().replace("paper-01", "R1")

    latex = render_submission_latex(
        manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="zh"
    )

    assert r"\cite{R1}" in latex
    assert r"\bibitem{R1}" in latex


def test_typesetter_renders_reference_italics() -> None:
    manuscript = _manuscript().replace(
        "Author. Verified paper.", "Author. *Verified Journal*, 2(1)."
    )

    latex = render_submission_latex(
        manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="en"
    )

    assert r"\textit{Verified Journal}" in latex


def test_typesetter_maps_academic_unicode_for_pdf_latex() -> None:
    manuscript = _manuscript().replace(
        "研究问题与边界。",
        "Claim–evidence agreement used Cohen's κ; 8 × 5 cells were frozen.",
    )

    latex = render_submission_latex(
        manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="en"
    )

    assert "Claim--evidence" in latex
    assert r"\(\kappa\)" in latex
    assert r"\(\times\)" in latex


def test_typesetter_rejects_structured_abstract_labels() -> None:
    manuscript = _manuscript().replace(
        "自主科研代理可能在交付前产生缺乏证据支持的主张。",
        "Background: 自主科研代理可能在交付前产生缺乏证据支持的主张。",
    )

    try:
        render_submission_latex(
            manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="zh"
        )
    except ValueError as exc:
        assert "structured move labels" in str(exc)
    else:
        raise AssertionError("structured abstract labels must be rejected")


def test_typesetter_strips_internal_provenance_comments() -> None:
    manuscript = _manuscript().replace(
        "[paper-01]",
        "[paper-01]<!--ref:paper-01--><!--anchor:section:2-->",
        1,
    )

    latex = render_submission_latex(
        manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="en"
    )

    assert "<!--" not in latex
    assert "anchor:section" not in latex
    assert r"\cite{paper-01}" in latex


def test_typesetter_numbers_captioned_tables_and_uses_readable_columns() -> None:
    manuscript = _manuscript().replace(
        "研究问题与边界。",
        "Table: Evidence summary\n| Literature | Relation |\n|---|---|\n| Verification | Packet completeness |",
    )

    latex = render_submission_latex(
        manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="en"
    )

    assert r"\begin{table}[tbp]" in latex
    assert r"\caption{Evidence summary}" in latex
    assert r"\begin{tabularx}{\linewidth}{YY}" in latex
    assert r"\footnotesize" in latex


def test_frozen_numeric_evidence_table_satisfies_typesetting_contract() -> None:
    discussion = markdown_heading(GENERIC_JOURNAL_ARTICLE.section("discussion"), "zh")
    manuscript = _manuscript().replace(
        discussion,
        _numeric_table(
            {"numeric_evidence": [{"path": "metrics.primary", "value": 0.42}]}
        )
        + "\n\n"
        + discussion,
    )

    latex = render_submission_latex(
        manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="zh"
    )

    assert r"\begin{table}[tbp]" in latex
    assert r"\caption{" in latex
    assert "metrics.primary" in latex


def test_typesetter_rejects_unrendered_mermaid_source() -> None:
    manuscript = _manuscript().replace(
        "研究问题与边界。",
        "~~~mermaid\nflowchart LR\nA --> B\n~~~",
    )

    try:
        render_submission_latex(
            manuscript, contract=GENERIC_JOURNAL_ARTICLE, language="en"
        )
    except ValueError as exc:
        assert "draw.io backend" in str(exc)
    else:
        raise AssertionError("unrendered Mermaid must not leak into the manuscript")
