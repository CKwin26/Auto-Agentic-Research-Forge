from research_forge.paper_pipeline import (
    GENERIC_JOURNAL_ARTICLE,
    PAPER_WORKFLOW,
    RIRP_RESEARCH_ARTICLE,
    PaperWorkflowStage,
    get_paper_structure_contract,
    latex_heading,
    markdown_heading,
    normalize_unstructured_abstract,
    validate_abstract_prose,
    validate_markdown_structure,
)


def test_self_owned_paper_workflow_has_integrity_on_both_sides_of_review() -> None:
    stages = [item.stage for item in PAPER_WORKFLOW]

    assert stages == [
        PaperWorkflowStage.RESEARCH,
        PaperWorkflowStage.WRITE,
        PaperWorkflowStage.PRE_REVIEW_INTEGRITY,
        PaperWorkflowStage.REVIEW,
        PaperWorkflowStage.REVISE,
        PaperWorkflowStage.RE_REVIEW,
        PaperWorkflowStage.FINAL_INTEGRITY,
        PaperWorkflowStage.FINALIZE,
    ]
    assert PAPER_WORKFLOW[2].gate == "integrity_passed"
    assert PAPER_WORKFLOW[-2].gate == "final_integrity_passed"


def test_rirp_contract_encodes_numbering_and_nesting() -> None:
    contract = get_paper_structure_contract("rirp-research-article-v1")

    assert contract is RIRP_RESEARCH_ARTICLE
    assert not contract.section("abstract").numbered
    assert contract.section("background").numbered
    assert contract.section("related_work").parent_key == "background"
    assert contract.section("related_work").level == 3
    assert not contract.section("declarations").numbered
    assert latex_heading("section", "Declarations", numbered=False) == r"\section*{Declarations}"


def test_generic_contract_drives_chinese_markdown_headings() -> None:
    assert markdown_heading(GENERIC_JOURNAL_ARTICLE.section("methods"), "zh") == "## 方法"
    assert markdown_heading(GENERIC_JOURNAL_ARTICLE.section("references"), "zh") == "## 参考文献"


def test_structure_audit_detects_missing_level_and_order_errors() -> None:
    markdown = """# Paper

## Abstract

## Methods

### Related Work and Registered Sources

## Background

## Results

## Discussion

## Limitations

## Conclusions

## List of abbreviations

## Declarations

## References
"""

    violations = validate_markdown_structure(markdown, RIRP_RESEARCH_ARTICLE)

    assert "paper sections are out of contract order" in violations
    assert "missing required paper section: background" not in violations


def test_number_prefixes_do_not_break_structure_matching() -> None:
    assert RIRP_RESEARCH_ARTICLE.section_for_title("2. Methods").key == "methods"


def test_unstructured_abstract_normalization_removes_scaffolding() -> None:
    source = """### Background

Background: 代理会产生不受支持的主张，且这些主张必须回到冻结证据逐项核验。

Methods: 本研究比较同骨干的配对输出，并固定任务、种子、评价器和分析规则。

Results: 门控组的不支持率更低，但区间和适用边界仍需同时报告。

Conclusion: 结论只适用于冻结任务矩阵，不能外推为所有自主科研代理的普遍优势。"""

    normalized, changes = normalize_unstructured_abstract(source)

    assert "Background:" not in normalized
    assert "Methods:" not in normalized
    assert "\n" not in normalized
    assert "removed_structured_abstract_labels" in changes
    assert not validate_abstract_prose(normalized, GENERIC_JOURNAL_ARTICLE.abstract)


def test_structure_audit_rejects_labeled_or_multi_paragraph_abstract() -> None:
    abstract = (
        "Background: " + "这是用于满足长度约束的背景句。" * 8
        + "\n\nMethods: " + "这是第二段方法描述。" * 8
    )
    markdown = f"""# Paper

## Abstract

{abstract}

## Introduction

正文

## Related Work

正文

## Methods

正文

## Results

正文

## Discussion

正文

## Limitations

正文

## Conclusion

正文
"""

    violations = validate_markdown_structure(markdown, GENERIC_JOURNAL_ARTICLE)

    assert "abstract has 2 paragraphs; expected 1" in violations
    assert "unstructured abstract contains structured move labels" in violations


def test_keywords_are_metadata_not_a_second_abstract_paragraph() -> None:
    abstract = "这是单段非结构式摘要，完整覆盖背景、问题、方法、结果和受限结论。" * 5
    markdown = f"""# Paper

## Abstract

{abstract}

**Keywords:** research agents; evidence; audit

## Introduction

正文

## Related Work

正文

## Methods

正文

## Results

正文

## Discussion

正文

## Limitations

正文

## Conclusion

正文
"""

    violations = validate_markdown_structure(markdown, GENERIC_JOURNAL_ARTICLE)

    assert not [item for item in violations if item.startswith("abstract ")]
    assert not [item for item in violations if item.startswith("unstructured abstract")]
