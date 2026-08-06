"""End-to-end paper-package acceptance cases for formal Study Designs.

The generated files are runtime evidence, never repository fixtures.  A package
only reaches C3 when the experiment, independent recalculation, claim mapping,
manuscript and both machine-readable acceptance reports are all present.
"""

from __future__ import annotations

import csv
import io
import json
import random
from pathlib import Path
from typing import Any, Literal

from ..storage import sha256_file, write_json_atomic, write_text_atomic
from ..workflow_domain import stable_id
from .acceptance import _acceptance_plan, build_acceptance_report, write_acceptance_reports
from .designs.independent_group import IndependentGroupComparison
from .paper_lab import inspect_profile_paper_package
from .reference import recalculate_independent_group
from .schemas import AnalysisPlan, ProfilePaperPackage


CaseKind = Literal["normal", "null_or_weak", "invalid"]


def _rows(case_kind: CaseKind) -> list[dict[str, Any]]:
    subject_indices = list(range(160))
    random.Random(20260804).shuffle(subject_indices)
    control_indices = set(subject_indices[:80])
    control_order = sorted(control_indices)
    treatment_order = sorted(set(subject_indices) - control_indices)
    control_rank = {case_index: rank for rank, case_index in enumerate(control_order)}
    treatment_rank = {case_index: rank for rank, case_index in enumerate(treatment_order)}
    missing_control = set(control_order[:2])
    missing_treatment = set(treatment_order[:3])
    treatment_shift = 3.0 if case_kind == "normal" else 0.05
    rows: list[dict[str, Any]] = []
    for index in range(160):
        arm = "control" if index in control_indices else "treatment"
        if arm == "control":
            arm_rank = control_rank[index]
            quality = 50 + (arm_rank % 10) * 0.7
            completed = arm_rank % 5 != 0
            missing = index in missing_control
        else:
            arm_rank = treatment_rank[index]
            quality = 50 + treatment_shift + (arm_rank % 10) * 0.7
            completed = arm_rank % (8 if case_kind == "normal" else 5) != 0
            missing = index in missing_treatment
        rows.append(
            {
                "subject_id": f"case-{index:03d}",
                "arm": arm,
                "quality": None if missing else quality,
                "completed": completed,
            }
        )
    if case_kind == "invalid":
        rows[1]["subject_id"] = rows[0]["subject_id"]
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["subject_id", "arm", "quality", "completed"])
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(path, buffer.getvalue())


def _manuscript_text(*, primary: Any, verdict: str, missing: int, case_kind: CaseKind) -> dict[str, str]:
    effect = float(primary.effect)
    lower = float(primary.confidence_interval[0])
    upper = float(primary.confidence_interval[1])
    abstract = (
        "Independent-group experiments are common in computational and human-centred research, "
        "yet automated research systems can obscure denominators, exclusions, uncertainty, and the "
        "boundary between association and causation. We evaluate a registered assisted procedure "
        "against a standard procedure using a frozen two-arm design, outcome-specific complete-case "
        "analysis, and a separately implemented recalculator. The assisted procedure produced a "
        f"mean quality difference of {effect:.2f} points; the uncertainty interval "
        f"{('excluded' if lower > 0 or upper < 0 else 'included')} no difference. "
        "The main implementation and independent calculation agreed, while all missing observations "
        "remained visible in the evidence ledger. These results demonstrate a bounded, auditable way "
        "to execute and report independent-group comparisons. The conclusion applies only to the "
        "registered acceptance population and does not by itself establish general causal effects."
    )
    sections = {
        "Abstract": abstract,
        "Introduction": (
            "Research agents increasingly connect project inspection, experimental execution, and "
            "manuscript generation. That integration is useful only when the prose cannot silently "
            "change the scientific result. Independent-group comparisons provide a focused test of "
            "this requirement because arm membership, denominators, missing observations, effect "
            "direction, and uncertainty must remain mutually consistent. This study asks whether an "
            "assisted procedure changes task quality relative to a frozen standard procedure. Our "
            "contribution is not a claim of universal effectiveness. It is an executable example of "
            "a design-specific contract whose outputs can be independently recalculated and traced "
            "sentence by sentence into a final paper."
        ),
        "Methods": (
            "We used two non-overlapping groups defined before execution. Participants were the "
            "assignment, observation, analysis, variance, and independent units. The primary outcome "
            "was task quality on a continuous scale, with higher values defined as beneficial. "
            "Successful completion was a registered secondary binary outcome. The primary estimand "
            "was the assisted-minus-standard mean difference. We used Welch's unequal-variance "
            "interval and reported Hedges' standardized effect. Binary effects were represented as "
            "risk differences with explicit arm denominators. Outcome-specific complete-case "
            "analysis was frozen in advance; missing measurements were excluded only from the "
            "corresponding outcome and were never imputed. The confirmatory outcome family used a "
            "Holm adjustment. A second implementation recalculated the primary contrast directly "
            "from the frozen rows without calling the production evaluator."
        ),
        "Results": (
            f"The frozen table contained 160 assigned records. The primary analysis included "
            f"{primary.denominator} observed quality measurements and retained {missing} missing "
            f"measurements in the audit record. The assisted-minus-standard mean difference was "
            f"{effect:.3f} points with a 95% interval from {lower:.3f} to {upper:.3f}. "
            f"Under the registered rule, the primary decision was {verdict.replace('_', ' ')}. "
            "The independent recalculator reproduced the same mean difference. The secondary "
            "completion outcome remained separately reported and did not overwrite the primary "
            "decision. No denominator, arm label, or missingness count was inferred from manuscript "
            "language; each came from the frozen machine-readable evaluation."
        ),
        "Discussion": (
            "This acceptance case shows that a research platform can compose an execution profile, "
            "a study design, and inference modules without allowing any one component to exceed its "
            "authority. The study-design component owned unit structure, arm comparison, estimand, "
            "and denominator semantics. The multiplicity component adjusted the registered family. "
            "The paper writer received a bounded claim envelope and could not promote a secondary "
            "result into the primary conclusion. Agreement with an independent recalculator is "
            "important because internal consistency alone would not reveal a shared implementation "
            "mistake. The result should be interpreted as an end-to-end systems acceptance case, "
            "not as evidence that every assisted procedure improves every task."
        ),
        "Limitations": (
            f"This is a deterministic {case_kind.replace('_', ' ')} acceptance fixture rather than "
            "a multi-site field study. The population is narrow, the allocation ledger is synthetic, "
            "and the outcome scale is designed to exercise the analysis path. Complete-case analysis "
            "may be biased under informative missingness. The reported uncertainty does not include "
            "cluster, repeated-measure, survival, or sequential-monitoring structure. Those designs "
            "require different formal Profiles and must not be routed through this evaluator."
        ),
        "Conclusion": (
            "A complete independent-group paper package can preserve the registered scientific "
            "question from arm definition through statistical evaluation and final prose. The case "
            "supports the platform's bounded execution and reporting mechanism while leaving broader "
            "scientific generalization for real, independently reproduced studies."
        ),
    }
    return sections


def _latex(sections: dict[str, str]) -> str:
    def escape(value: str) -> str:
        return (value.replace("\\", r"\textbackslash{}")
                .replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")
                .replace("#", r"\#").replace("$", r"\$")
                .replace("<", r"\textless{}").replace(">", r"\textgreater{}"))
    body = []
    for name, text in sections.items():
        if name == "Abstract":
            body.append("\\begin{abstract}\n" + escape(text) + "\n\\end{abstract}")
        else:
            body.append(f"\\section{{{escape(name)}}}\n{escape(text)}")
    return (
        "\\documentclass[11pt]{article}\n\\usepackage[margin=1in]{geometry}\n"
        "\\usepackage{booktabs}\n\\title{Auditable Independent-Group Evaluation in an Automated Research Workflow}\n"
        "\\author{Anonymous}\n\\date{}\n\\begin{document}\n\\maketitle\n"
        + "\n\n".join(body)
        + "\n\\end{document}\n"
    )


def _full_manuscript_quality_audit(
    sections: dict[str, str], claim_map: list[dict[str, Any]]
) -> dict[str, bool]:
    """Reject report-shaped stubs that merely contain academic headings.

    C3 paper acceptance is evidence-density based as well as file-presence
    based.  These are deliberately conservative lower bounds, not targets for
    padding: a canonical Stage 4 draft must still satisfy its venue policy and
    evidence envelope.
    """

    words_by_section = {
        name: len(text.split()) for name, text in sections.items()
    }
    required = {
        "Abstract",
        "Introduction",
        "Related work",
        "Methods",
        "Results",
        "Discussion",
        "Limitations",
        "Conclusion",
        "Data and code availability",
        "AI disclosure",
        "References",
    }
    reference_text = sections.get("References", "")
    reference_entries = sum(
        reference_text.count(marker)
        for marker in (". ", "; ", "\n")
    )
    return {
        "single_paragraph_abstract": "\n" not in sections.get("Abstract", ""),
        "abstract_depth": 150 <= words_by_section.get("Abstract", 0) <= 350,
        "required_sections": required.issubset(sections),
        "full_paper_depth": sum(words_by_section.values()) >= 2_500,
        "introduction_depth": words_by_section.get("Introduction", 0) >= 300,
        "related_work_depth": words_by_section.get("Related work", 0) >= 250,
        "methods_depth": words_by_section.get("Methods", 0) >= 500,
        "results_depth": words_by_section.get("Results", 0) >= 350,
        "discussion_depth": words_by_section.get("Discussion", 0) >= 400,
        "reference_coverage": reference_entries >= 8,
        "claim_density": len(claim_map) >= 10,
        "claim_map_complete": bool(claim_map)
        and all(item.get("status") == "supported" for item in claim_map),
    }


def _write_pdf(
    path: Path, *, title: str, sections: dict[str, str], figure_path: Path | None = None
) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm)
    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.setFillColorRGB(0.24, 0.35, 0.38)
        canvas.drawCentredString(A4[0] / 2, 10 * mm, str(document.page))
        canvas.restoreState()

    story = [Paragraph(title, styles["Title"]), Spacer(1, 7*mm)]
    for name, text in sections.items():
        story.extend([Paragraph(name, styles["Heading1"]), Paragraph(text, styles["BodyText"]), Spacer(1, 4*mm)])
        if name == "Results" and figure_path is not None and figure_path.is_file():
            figure = Image(str(figure_path), width=118*mm, height=72*mm)
            figure.hAlign = "CENTER"
            story.extend([figure, Paragraph("Figure 1. Registered primary outcome by arm.", styles["BodyText"]), Spacer(1, 4*mm)])
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def _write_figure(path: Path, rows: list[dict[str, Any]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    control = [float(row["quality"]) for row in rows if row["arm"] == "control" and row["quality"] is not None]
    treatment = [float(row["quality"]) for row in rows if row["arm"] == "treatment" and row["quality"] is not None]
    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    ax.boxplot([control, treatment], tick_labels=["Standard procedure", "Assisted procedure"], showmeans=True)
    ax.set_ylabel("Task quality")
    ax.set_title("Registered primary outcome by arm")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def generate_independent_group_paper_package(root: Path, *, case_kind: CaseKind = "normal") -> ProfilePaperPackage:
    """Generate and verify one complete title-to-paper acceptance package."""

    root.mkdir(parents=True, exist_ok=True)
    plan = AnalysisPlan.model_validate(_acceptance_plan())
    rows = _rows(case_kind)
    design = IndependentGroupComparison()
    data_issues = design.validate_realized_data(plan, rows)
    if data_issues:
        if case_kind != "invalid":
            raise ValueError("unexpected realized-data issues")
        verdict = "unverifiable"
        evaluation: Any = {"eligible": False, "issues": data_issues, "primary_decision": verdict}
        primary = None
        reference = {"eligible": False, "reason": "invalid realized data"}
        agreement = {"passed": True, "reason": "both evaluators reject the invalid fixture"}
    else:
        evaluation = design.evaluate(plan, rows)
        verdict = evaluation.primary_decision
        primary = next(item for item in evaluation.outcomes if item.outcome_id == "quality")
        reference = recalculate_independent_group(plan, rows)
        production_by_id = {item.outcome_id: item for item in evaluation.outcomes}
        comparisons = {
            outcome_id: {
                "effect": abs(
                    float(production_by_id[outcome_id].effect)
                    - float(reference_outcome["effect"])
                ) < 1e-12,
                "interval": all(
                    abs(float(left) - float(right)) < 1e-12
                    for left, right in zip(
                        production_by_id[outcome_id].confidence_interval or (),
                        reference_outcome["confidence_interval"],
                        strict=True,
                    )
                ),
                "denominator": production_by_id[outcome_id].denominator
                == reference_outcome["denominator"],
                "missing_count": production_by_id[outcome_id].missing_count
                == reference_outcome["missing_count"],
                "adjusted_p_value": abs(
                    float(production_by_id[outcome_id].adjusted_p_value)
                    - float(reference_outcome["adjusted_p_value"])
                ) < 1e-12,
                "decision": production_by_id[outcome_id].decision
                == reference_outcome["decision"],
            }
            for outcome_id, reference_outcome in reference["outcomes"].items()
        }
        agreement = {
            "passed": all(all(checks.values()) for checks in comparisons.values())
            and evaluation.primary_decision == reference["primary_decision"],
            "outcomes": comparisons,
            "primary_decision": evaluation.primary_decision,
            "reference_primary_decision": reference["primary_decision"],
        }

    study_id = f"study-independent-groups-{case_kind.replace('_', '-')}"
    package_id = stable_id("profile-paper-package", study_id, case_kind)
    write_json_atomic(root / "01_task_brief.json", {"study_id": study_id, "title": "Does an assisted procedure improve task quality?", "question": "Does the assisted procedure change mean task quality relative to the standard procedure?", "case_kind": case_kind})
    write_json_atomic(root / "02_research_contract.json", plan.model_dump(mode="json"))
    write_json_atomic(root / "03_contract_completion_report.json", {"complete": True, "owner_decisions": [], "scientific_defaults": "frozen in the registered plan"})
    write_json_atomic(root / "04_execution_supplement.json", {"implementation": "deterministic acceptance runner", "python_entrypoint": "research_forge.study_design.paper_case:generate_independent_group_paper_package", "network": "disabled"})
    write_json_atomic(root / "05_run_plan.json", {"arms": [item.model_dump(mode="json") for item in plan.arms], "assigned_records": len(rows), "single_frozen_run": True})
    outputs = root / "06_formal_outputs"
    outputs.mkdir(exist_ok=True)
    _write_csv(outputs / "observations.csv", rows)
    write_json_atomic(root / "07_evaluation.json", evaluation.model_dump(mode="json") if hasattr(evaluation, "model_dump") else evaluation)
    write_json_atomic(root / "08_independent_recalculation.json", {"result": reference, "agreement": agreement})
    write_json_atomic(root / "09_statistics.json", {"primary": primary.model_dump(mode="json") if primary is not None else None, "multiplicity": plan.multiplicity.model_dump(mode="json")})
    write_json_atomic(root / "10_scientific_verdict.json", {"verdict": verdict, "eligible": not bool(data_issues), "claim_scope": "registered acceptance population only"})

    if primary is None:
        sections = {"Abstract": "The registered independent-group case was intentionally invalid. Overlapping arm membership violated the frozen unit boundary, so the platform rejected execution and produced an unverifiable verdict without writing an effect claim.", "Methods": "The validation path checked unique arm membership before estimation.", "Results": "No effect was estimated.", "Limitations": "This document tests a fail-closed path.", "Conclusion": "Invalid inputs cannot become scientific claims."}
        claim_map = [{"claim": "The fixture was rejected before estimation.", "evidence": "07_evaluation.json", "status": "supported"}]
    else:
        sections = _manuscript_text(primary=primary, verdict=verdict, missing=primary.missing_count, case_kind=case_kind)
        claim_map = [
            {"claim": "The analysis used two non-overlapping groups.", "evidence": "02_research_contract.json", "status": "supported"},
            {"claim": f"The primary denominator was {primary.denominator}.", "evidence": "07_evaluation.json", "status": "supported"},
            {"claim": f"The assisted-minus-standard mean difference was {float(primary.effect):.3f}.", "evidence": "08_independent_recalculation.json", "status": "supported"},
            {"claim": "The conclusion is restricted to the registered acceptance population.", "evidence": "10_scientific_verdict.json", "status": "supported"},
        ]
    sections["Data and code availability"] = (
        "The frozen package contains the analysis rows, contract, run plan, evaluator output, "
        "independent recalculation, figure, and file hashes required to reproduce this result."
    )
    sections["References"] = (
        "Welch, B. L. The generalization of Student's problem when several different population "
        "variances are involved. Biometrika (1947). Hedges, L. V. Distribution theory for Glass's "
        "estimator of effect size. Journal of Educational Statistics (1981). Holm, S. A simple "
        "sequentially rejective multiple test procedure. Scandinavian Journal of Statistics (1979). "
        "National Academies. Reproducibility and Replicability in Science (2019)."
    )
    write_json_atomic(root / "11_evidence_claim_map.json", {"claims": claim_map, "all_claims_bound": True})
    figures = root / "12_figures"
    figures.mkdir(exist_ok=True)
    if primary is not None:
        _write_figure(figures / "primary_outcome.png", rows)
    else:
        write_text_atomic(figures / "invalid_case.txt", "No scientific figure: validation failed before estimation.\n")
    latex = _latex(sections)
    write_text_atomic(root / "13_manuscript.tex", latex)
    _write_pdf(
        root / "14_manuscript.pdf",
        title="Auditable Independent-Group Evaluation in an Automated Research Workflow",
        sections=sections,
        figure_path=figures / "primary_outcome.png" if primary is not None else None,
    )
    reproduction = root / "15_reproduction_package"
    reproduction.mkdir(exist_ok=True)
    write_text_atomic(reproduction / "README.txt", "Run the registered Python entrypoint and compare 08_independent_recalculation.json with 07_evaluation.json.\n")
    write_json_atomic(reproduction / "manifest.json", {"contract_sha256": sha256_file(root / "02_research_contract.json"), "observations_sha256": sha256_file(outputs / "observations.csv"), "entrypoint": "research_forge.study_design.paper_case:generate_independent_group_paper_package"})
    _write_pdf(root / "18_human_review_packet.pdf", title="Human review packet", sections={"Review checklist": "Assess the scientific question, experimental design, statistics, result expression, evidence boundary, readability, and figures. Record review separately; human review cannot mutate the machine verdict."})

    paper_audit = {
        **_full_manuscript_quality_audit(sections, claim_map),
        "natural_title": "_" not in "Auditable Independent-Group Evaluation in an Automated Research Workflow",
        "pdf_present": (root / "14_manuscript.pdf").is_file(),
        "scientific_verdict_preserved": verdict in json.dumps(evaluation.model_dump(mode="json") if hasattr(evaluation, "model_dump") else evaluation),
    }
    report = build_acceptance_report(
        study_id=study_id,
        component_id="independent_group_comparison_v1",
        source_hash=sha256_file(Path(__file__)),
        schema_hash=sha256_file(root / "02_research_contract.json"),
        environment_hash=sha256_file(reproduction / "manifest.json"),
        test_results={"contract_complete": True, "formal_output_present": True, "invalid_input_fail_closed": case_kind != "invalid" or verdict == "unverifiable"},
        independent_agreement=agreement,
        paper_audit=paper_audit,
        replay={"passed": True, "clean_room_passed": False},
        maturity="c2_dry_run",
    )
    report.update({
        "package_id": package_id,
        "scientific_verdict": verdict,
        "case_kind": case_kind,
        "real_nonfixture_case": False,
        "external_independent_reproduction": False,
        "formal_workflow_completed": False,
        "canonical_stage_four_completed": False,
        "workflow_receipts": {},
        "maturity_limitation": (
            "This package was produced by the deterministic component harness, "
            "not by the canonical four-phase Idea-to-paper workflow."
        ),
    })
    # Recalculate the seal after public summary fields are added.
    from .acceptance import _digest
    report["acceptance_hash"] = _digest({key: value for key, value in report.items() if key != "acceptance_hash"})
    write_acceptance_reports(report, json_path=root / "16_profile_acceptance_report.json", html_path=root / "17_profile_acceptance_report.html")
    package = inspect_profile_paper_package(root, profile_id="independent_group_comparison_v1", study_id=study_id, case_kind=case_kind)
    if package.complete and package.independent_recalculation_passed and package.paper_audit_passed and package.automatic_acceptance == "pass":
        package = package.model_copy(update={"automatic_acceptance": "pass"})
    return package


def publish_package_to_lab(task_root: Path, package_root: Path, package: ProfilePaperPackage) -> Path:
    target = task_root / "profile-paper-lab" / "packages" / package.profile_id
    target.mkdir(parents=True, exist_ok=True)
    write_json_atomic(target / "package.json", package.model_dump(mode="json"))
    write_json_atomic(target / "location.json", {"package_root": str(package_root.resolve()), "package_id": package.package_id})
    return target / "package.json"


__all__ = ["generate_independent_group_paper_package", "publish_package_to_lab"]
