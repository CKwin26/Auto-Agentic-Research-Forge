import re

from research_forge.paper_pipeline import (
    centralize_defensive_boundaries,
    compact_conclusion_paragraphs,
    GENERIC_JOURNAL_ARTICLE,
    PAPER_WORKFLOW,
    RIRP_RESEARCH_ARTICLE,
    PaperWorkflowStage,
    abstract_numeric_token_count,
    get_paper_structure_contract,
    latex_heading,
    markdown_heading,
    normalize_unstructured_abstract,
    relocate_conclusion_numeric_detail,
    validate_abstract_prose,
    validate_manuscript_narrative,
    validate_markdown_structure,
)


def test_lossless_narrative_relocation_preserves_protected_text() -> None:
    introduction = (
        "The study addresses an important comparison. The design is controlled. "
        "A limitation is the bounded fixture. It cannot establish a mechanism. "
        "The result does not establish effects beyond the sampled tasks. "
        "The available evidence is insufficient for the registered population."
    )
    limitations = "The evaluation uses a bounded task collection."
    revised_intro, revised_limitations = centralize_defensive_boundaries(
        introduction,
        limitations,
        maximum_introduction_matches=2,
    )
    assert len(re.findall(r"limitation|cannot|does not establish|insufficient", revised_intro, re.I)) <= 2
    assert "registered population" in revised_limitations
    assert sorted(re.findall(r"\b(?:fixture|mechanism|tasks|population)\b", introduction)) == sorted(
        re.findall(
            r"\b(?:fixture|mechanism|tasks|population)\b",
            revised_intro + " " + revised_limitations,
        )
    )

    conclusion = (
        "The intervention improved the registered outcome. "
        "The estimate was 0.14 with interval 0.02 to 0.26. "
        "The finding supports the bounded comparison."
    )
    results = "The registered analysis produced the primary estimate."
    revised_conclusion, revised_results = relocate_conclusion_numeric_detail(
        conclusion,
        results,
        maximum_numeric_tokens=2,
    )
    assert len(re.findall(r"\d+(?:\.\d+)?", revised_conclusion)) <= 2
    assert sorted(re.findall(r"\d+(?:\.\d+)?", conclusion)) == sorted(
        re.findall(r"\d+(?:\.\d+)?", revised_conclusion + " " + revised_results)
    )


def test_abstract_prefers_natural_language_and_caps_numeric_tokens() -> None:
    natural = (
        "We compared two controlled prompting conditions on a frozen item set. "
        "The verification condition achieved higher exact accuracy, and the "
        "registered paired analysis supported the comparison. The result remains "
        "limited to the controlled fixture and does not establish a mechanism."
    )
    overloaded = (
        natural
        + " Baseline accuracy was 0.760, treatment accuracy was 0.947, the "
        "difference was 0.187, and p was 0.001."
    )

    assert abstract_numeric_token_count(natural) == 0
    assert not validate_abstract_prose(
        natural, GENERIC_JOURNAL_ARTICLE.abstract
    )
    assert (
        "abstract contains 4 numeric tokens; maximum is 2"
        in validate_abstract_prose(
            overloaded, GENERIC_JOURNAL_ARTICLE.abstract
        )
    )
    prompt = GENERIC_JOURNAL_ARTICLE.abstract.prompt_contract()
    assert prompt["preferred_numeric_token_count"] == 0
    assert prompt["maximum_numeric_token_count"] == 2
    assert prompt["rhetorical_route"] == [
        "scientific_problem_or_tension",
        "bounded_study_design",
        "principal_finding_in_plain_language",
        "scientific_implication",
        "single_calibrated_boundary",
    ]
    assert "not a workflow requirement" in prompt["reader_facing_rhetoric_rule"]


def test_abstract_rejects_internal_audit_voice_and_stacked_disclaimers() -> None:
    internal = (
        "We study dual_quality_top5 in run-stage3-final. The frozen audit ledger "
        "and immutable hash gate define the result."
    )
    defensive = (
        "We compared direct and verification prompts on the same questions. "
        "The verification prompt yielded higher exact accuracy. This does not "
        "establish a reasoning mechanism. The result cannot generalize to other tasks."
    )

    internal_violations = validate_abstract_prose(
        internal, GENERIC_JOURNAL_ARTICLE.abstract
    )
    defensive_violations = validate_abstract_prose(
        defensive, GENERIC_JOURNAL_ARTICLE.abstract
    )

    assert any(
        item.startswith("abstract exposes internal identifiers")
        for item in internal_violations
    )
    assert any(
        item.startswith("abstract uses audit or governance vocabulary")
        for item in internal_violations
    )
    assert any(
        item.startswith("abstract ends with consecutive limitation sentences")
        for item in defensive_violations
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
    violations = validate_abstract_prose(
        normalized, GENERIC_JOURNAL_ARTICLE.abstract
    )
    assert "unstructured abstract contains structured move labels" not in violations
    assert "unstructured abstract contains a Markdown heading" not in violations
    assert any(
        item.startswith("abstract uses audit or governance vocabulary")
        for item in violations
    )


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


def test_structure_contract_exposes_distinct_section_narrative_roles() -> None:
    narrative = GENERIC_JOURNAL_ARTICLE.prompt_contract("en")[
        "manuscript_narrative"
    ]

    assert narrative["sections"]["results"]["purpose"].startswith(
        "Report observations"
    )
    assert "interpretation" in narrative["sections"]["discussion"][
        "required_moves"
    ]
    assert "direct_answer" in narrative["sections"]["conclusion"][
        "required_moves"
    ]


def test_narrative_audit_rejects_audit_report_voice_and_dense_conclusion() -> None:
    sections = {
        "introduction": (
            "The frozen audit workflow cannot establish a result. "
            "The immutable ledger cannot support a claim. "
            "The evidence-bound gate is unavailable and the study cannot generalize."
        ),
        "related_work": "Prior work studies the scientific question by two competing approaches.",
        "methods": "We compared the candidate and reference methods on paired observations.",
        "results": (
            "The system copies values from supplied bindings and the writing agent cannot modify them."
        ),
        "discussion": (
            "The frozen scientific verdict was supported under the controlled acceptance fixture."
        ),
        "limitations": "The sample was narrow, which limits population-level inference.",
        "conclusion": (
            "The audit found 10 cases, 5 seeds, an effect of 0.25, and p=0.01. "
            "This cannot establish a mechanism and cannot generalize."
        ),
        "data_availability": "Materials will be provided during review.",
    }

    violations = validate_manuscript_narrative(sections)

    assert any("production commentary" in item for item in violations)
    assert any("dense numeric recap" in item for item in violations)
    assert any("centralize them in Limitations" in item for item in violations)
    assert any("submission placeholders" in item for item in violations)
    assert any("frozen scientific verdict" in item for item in violations)


def test_narrative_audit_rejects_stale_visual_placeholders() -> None:
    sections = {
        "introduction": "We compare two classifiers on a registered held-out task.",
        "related_work": "Prior studies motivate fixed-split classifier comparison.",
        "methods": "We used the same samples and primary outcome in both conditions.",
        "results": (
            "The callout marks the approved location, but the pending artifact "
            "was not supplied for inspection."
        ),
        "discussion": "The observed difference answers the bounded comparison.",
        "limitations": "The study contains one independent paired comparison.",
        "conclusion": "The candidate performed better in the evaluated setting.",
        "data_availability": "Materials are available in the accompanying package.",
    }

    violations = validate_manuscript_narrative(sections)

    assert any("production commentary" in item for item in violations)


def test_narrative_audit_accepts_conventional_scientific_story() -> None:
    sections = {
        "introduction": (
            "Reliable self-evaluation remains difficult for language models. "
            "Prior studies disagree on whether verification improves answers. "
            "We compare direct answering with a verification step and test whether "
            "the added step improves exact correctness."
        ),
        "related_work": (
            "Existing methods divide into iterative refinement and independent verification. "
            "The former revises outputs using self-feedback, whereas the latter checks an "
            "answer against task conditions. Our study isolates the second operation."
        ),
        "methods": (
            "We used a paired design with identical questions in both conditions. "
            "The primary outcome was exact correctness, evaluated under a prespecified rule."
        ),
        "results": (
            "Verification improved exact correctness. The paired estimate remained positive "
            "under the prespecified sensitivity analysis."
        ),
        "discussion": (
            "The pattern is consistent with verification catching condition violations. "
            "An alternative explanation is that the extra instruction increased deliberation. "
            "The finding therefore supports verification as a useful procedure, not a mechanism claim."
        ),
        "limitations": (
            "The task set was narrow, so the estimate should not be generalized to all reasoning tasks."
        ),
        "conclusion": (
            "A verification step improved answer reliability in the studied setting. "
            "The result motivates broader tests across tasks and models."
        ),
        "data_availability": "The evaluation materials are available in the accompanying repository.",
    }

    assert not validate_manuscript_narrative(sections)


def test_conclusion_compaction_preserves_all_tokens_and_repairs_segmentation() -> None:
    source = (
        "The study answered the registered interaction question with a positive estimate.\n\n"
        "The assistance-associated gain was larger under detailed feedback.\n\n"
        "This result remains bounded to the deterministic fixture.\n\n"
        "It does not establish a mechanism or effectiveness in production systems."
    )

    repaired = compact_conclusion_paragraphs(source, maximum_paragraphs=2)

    assert len(re.split(r"\n\s*\n", repaired)) == 2
    assert re.findall(r"\S+", repaired) == re.findall(r"\S+", source)
    assert not any(
        "over-segmented" in item
        for item in validate_manuscript_narrative({"conclusion": repaired})
    )
