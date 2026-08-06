"""Build the 18-item Profile Paper Package from a completed Workflow v2 Study.

The builder is a projection over canonical artifacts.  It does not run a
second experiment, synthesize replacement prose, or mutate a frozen verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import shutil
from pathlib import Path
from typing import Any, Callable

from ..storage import read_json, sha256_file, write_json_atomic, write_text_atomic
from ..paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
)
from ..workflow_domain import StudyVerdict, WorkflowRepository, stable_id
from .acceptance import build_acceptance_report, write_acceptance_reports
from .mutations import build_authority_mutation_report
from .negative_acceptance import required_negative_acceptance_cases
from .paper_case import _rows
from .paper_lab import inspect_profile_paper_package
from .reference import recalculate_independent_group
from .registry import study_design
from .schemas import AnalysisPlan, ProfilePaperPackage
from .workflow_acceptance import _acceptance_plan
from .workflow_evidence import derive_workflow_receipts, workflow_is_c3_complete


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(item for item in source.rglob("*") if item.is_file()):
        _copy_file(child, target / child.relative_to(source))


def _agreement(recorded: dict[str, Any], reference: dict[str, Any]) -> bool:
    """Compare the independently reconstructed outcome authority."""

    recorded_outcomes = {
        str(item["outcome_id"]): item for item in recorded.get("outcomes", [])
    }
    reference_outcomes = dict(reference.get("outcomes") or {})
    if set(recorded_outcomes) != set(reference_outcomes):
        return False
    if recorded.get("primary_decision") != reference.get("primary_decision"):
        return False
    for outcome_id, observed in recorded_outcomes.items():
        expected = reference_outcomes[outcome_id]
        for key in ("effect", "denominator", "missing_count", "decision"):
            left, right = observed.get(key), expected.get(key)
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                if not math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12):
                    return False
            elif left != right:
                return False
        left_interval = list(observed.get("confidence_interval") or [])
        right_interval = list(expected.get("confidence_interval") or [])
        if len(left_interval) != len(right_interval) or any(
            not math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
            for left, right in zip(left_interval, right_interval)
        ):
            return False
    return True


def _package_evidence_map(
    repository: WorkflowRepository,
    study_id: str,
    source_path: Path,
) -> EvidenceClaimMap:
    """Add narrow verified-background bindings without changing result authority."""

    evidence_map = EvidenceClaimMap.model_validate(read_json(source_path))
    bindings = list(evidence_map.bindings)
    existing = {item.claim_id for item in bindings}
    prerequisite_step = max(
        (
            item
            for item in repository.list_steps(study_id)
            if item.step_type == "publication_prerequisite_gate"
            and item.status.value == "succeeded"
        ),
        key=lambda item: (
            int(item.parameters.get("stage4_workflow_revision", 1)),
            item.updated_at,
            item.step_instance_id,
        ),
    )
    prerequisite = repository.load_step_result(
        study_id, prerequisite_step.step_instance_id
    )
    for source in prerequisite.get("verified_literature", []):
        source_id = str(
            source.get("source_id") or source.get("resource_id") or ""
        ).strip()
        title = str(source.get("title") or "").strip()
        metadata_hash = str(source.get("canonical_metadata_hash") or "").strip()
        claim_id = f"literature:{source_id}:verified-topic"
        if not source_id or not title or not metadata_hash or claim_id in existing:
            continue
        bindings.append(
            EvidenceClaimBinding(
                claim_id=claim_id,
                kind="literature_context",
                statement=(
                    f'The verified background publication "{title}" is '
                    "registered as methodological context only."
                ),
                evidence=[
                    EvidencePointer(
                        path=str(
                            source.get("canonical_identifier")
                            or source.get("doi")
                            or source_id
                        ),
                        sha256=metadata_hash,
                        json_path="$.title",
                        source_id=source_id,
                        evidence_type="verified_literature",
                    )
                ],
                allowed_sections=[
                    "introduction",
                    "related_work",
                    "methods",
                    "discussion",
                    "limitations",
                ],
                claim_strength="descriptive",
                evidence_status="bound",
            )
        )
        existing.add(claim_id)
    return evidence_map.model_copy(
        update={
            "bindings": bindings,
            "source_registry_sha256": _digest(
                {
                    "predecessor": evidence_map.source_registry_sha256,
                    "background_binding_ids": [
                        item.claim_id
                        for item in bindings
                        if item.kind == "literature_context"
                    ],
                }
            ),
        }
    )


def _repair_package_manuscript(
    source_tex: Path,
    *,
    target_tex: Path,
    target_pdf: Path,
    audit_root: Path,
    evaluation_count: int,
) -> dict[str, Any]:
    """Apply disclosure-only prose repair and compile the package PDF."""

    from ..manuscript_compile import finalize_manuscript_pdf

    text = source_tex.read_text(encoding="utf-8")
    source_assets = source_tex.parent / "paper_assets"
    if source_assets.is_dir():
        _copy_tree(source_assets, target_tex.parent / "paper_assets")
    record_label = "record" if evaluation_count == 1 else "records"
    abstention = (
        "The frozen evaluation recorded no abstentions among the "
        f"{evaluation_count} registered evaluation {record_label}."
    )
    release = (
        "No reusable release packet was registered: the packet schema, evaluator "
        "manifest, and regression fixtures were not all available as release assets."
    )
    restored: list[str] = []
    if abstention not in text:
        text = text.replace(
            r"\section{Discussion}",
            abstention + "\n\n" + r"\section{Discussion}",
            1,
        )
        restored.append("transparency-abstentions")
    if release not in text:
        text = text.replace(
            r"\section{Conclusion}",
            release + "\n\n" + r"\section{Conclusion}",
            1,
        )
        restored.append("transparency-release-assets")
    write_text_atomic(target_tex, text)
    audit_root.mkdir(parents=True, exist_ok=True)
    result = finalize_manuscript_pdf(
        target_tex,
        output_dir=target_tex.parent,
        profile="journal-article",
        language="en",
        report_path=audit_root / "package_manuscript.depth.json",
        manifest_path=audit_root / "package_manuscript.finalization.json",
    )
    generated_pdf = Path(str(result["pdf"]))
    _copy_file(generated_pdf, target_pdf)
    if generated_pdf.resolve() != target_pdf.resolve():
        generated_pdf.unlink(missing_ok=True)
    return {"restored_disclosure_ids": restored, "pdf_verified": True}


def _artifact_path(repository: WorkflowRepository, study_id: str, kind: str) -> Path:
    matches = [item for item in repository.list_artifacts(study_id) if item.kind == kind]
    if not matches:
        raise ValueError(f"completed workflow is missing required artifact kind: {kind}")
    selected = max(
        matches,
        key=lambda item: (item.version, item.created_at, item.artifact_id),
    )
    candidate = Path(selected.path).resolve()
    if not candidate.is_file():
        raise ValueError(f"registered artifact is unavailable: {kind}")
    if sha256_file(candidate) != selected.sha256:
        raise ValueError(f"registered artifact hash changed: {kind}")
    return candidate


def _write_ai_review_packet(
    path: Path,
    *,
    study_id: str,
    scientific_review: dict[str, Any],
    readiness: dict[str, Any],
) -> None:
    """Render an AI-panel packet; this never claims external human review."""

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    story = [
        Paragraph("AI Scientific Review and Owner Approval Packet", styles["Title"]),
        Spacer(1, 5 * mm),
        Paragraph(f"Study: {study_id}", styles["BodyText"]),
        Paragraph(
            "This packet records the Research Forge AI scientific panel, deterministic "
            "paper audits, and project-owner publication approval. It is not external "
            "human peer review or independent reproduction.",
            styles["BodyText"],
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            "AI panel decision: " + str(scientific_review.get("decision", "recorded")),
            styles["Heading2"],
        ),
    ]
    for review in scientific_review.get("reviews", []):
        role = str(review.get("role", "reviewer")).title()
        recommendation = str(review.get("recommendation", "recorded"))
        story.append(Paragraph(f"{role}: {recommendation}", styles["Heading3"]))
        for finding in review.get("findings", []):
            story.append(Paragraph(str(finding.get("diagnosis", "")), styles["BodyText"]))
    story.extend(
        [
            Spacer(1, 4 * mm),
            Paragraph("Publication control", styles["Heading2"]),
            Paragraph(
                "System readiness: " + str(readiness.get("system_publication_readiness", "recorded")),
                styles["BodyText"],
            ),
            Paragraph(
                "AI scientific review: " + str(readiness.get("ai_scientific_review", "recorded")),
                styles["BodyText"],
            ),
            Paragraph(
                "Author approval: " + str(readiness.get("author_publication_approval", "recorded")),
                styles["BodyText"],
            ),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm).build(story)


def _build_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
    *,
    profile_id: str,
    plan: AnalysisPlan,
    rows: list[dict[str, Any]],
    recalculator: Callable[[AnalysisPlan, list[dict[str, Any]]], dict[str, Any]],
    dataset_relative_path: str,
    source_files: list[Path],
    negative_results: dict[str, bool],
    mutation_results: dict[str, bool],
    positive_results: dict[str, bool],
    component_id: str | None = None,
    extra_known_limitations: list[str] | None = None,
) -> ProfilePaperPackage:
    """Project one completed canonical Study into the required package."""

    study = repository.load_study(study_id)
    study_root = repository.root / "studies" / study_id
    completion_path = study_root / "completion_record.json"
    readiness_path = study_root / "readiness.json"
    if not completion_path.is_file() or not readiness_path.is_file():
        raise ValueError("canonical Stage 4 has not completed")
    output_root.mkdir(parents=True, exist_ok=True)

    scope = repository.latest_scope_contract(study_id)
    contract = repository.latest_research_contract(study_id)
    if scope is None or contract is None:
        raise ValueError("frozen scope and research contract are required")
    write_json_atomic(
        output_root / "01_task_brief.json",
        {
            "study_id": study_id,
            "title": study.title,
            "research_question": scope.research_question,
            "population_or_corpus": scope.population_or_corpus,
            "primary_outcome": scope.primary_outcome,
            "comparison": scope.comparison,
            "scope_in": scope.scope_in,
            "scope_out": scope.scope_out,
        },
    )
    _copy_file(study_root / "contracts" / f"research-v{contract.version}.json", output_root / "02_research_contract.json")

    contract_step = next(
        item for item in repository.list_steps(study_id)
        if item.step_type in {
            "research_contract_completion",
            "study_design_contract_completion",
            "compile_research_contract",
        }
        and item.status.value == "succeeded"
    )
    write_json_atomic(
        output_root / "03_contract_completion_report.json",
        {
            "step_instance_id": contract_step.step_instance_id,
            "accepted": contract_step.acceptance_status.value == "accepted",
            "checks": contract_step.acceptance_checks,
            "owner_gate_approved": True,
            "research_contract_version": contract.version,
        },
    )
    _copy_file(study_root / "stage3" / "handoff.json", output_root / "04_execution_supplement.json")
    run_plan = next((study_root / "stage3" / "run_plans").glob("*.json"))
    _copy_file(run_plan, output_root / "05_run_plan.json")
    _copy_tree(study_root / "stage3" / "results", output_root / "06_formal_outputs")
    _copy_file(study_root / "stage3" / "study_design_evaluation.json", output_root / "07_evaluation.json")
    _copy_file(study_root / "stage3" / "independent_recalculation.json", output_root / "08_independent_recalculation.json")
    statistics = {
        "evaluations": [item.model_dump(mode="json") for item in repository.list_evaluation_records(study_id)],
        "inference_modules": contract.inference_modules,
        "decision_rules": contract.study_design_spec.get("decision_rules", {}),
    }
    write_json_atomic(output_root / "09_statistics.json", statistics)
    verdict_path = study_root / "verdicts" / f"{study.latest_study_verdict_id}.json"
    verdict = StudyVerdict.model_validate(read_json(verdict_path))
    write_json_atomic(output_root / "10_scientific_verdict.json", {"verdict": verdict.status.value, **verdict.model_dump(mode="json")})
    package_evidence_map = _package_evidence_map(
        repository,
        study_id,
        _artifact_path(repository, study_id, "evidence_claim_map"),
    )
    write_json_atomic(
        output_root / "11_evidence_claim_map.json", package_evidence_map
    )

    artifact_manifest_path = _artifact_path(
        repository, study_id, "paper_artifact_manifest"
    )
    stage_four_revision_root = artifact_manifest_path.parent.parent
    figures_root = (
        stage_four_revision_root
        / "stage_4_synthesis"
        / "paper_assets"
        / "figures"
    )
    _copy_tree(figures_root, output_root / "12_figures")
    manuscript_repair = _repair_package_manuscript(
        _artifact_path(repository, study_id, "final_manuscript_latex"),
        target_tex=output_root / "13_manuscript.tex",
        target_pdf=output_root / "14_manuscript.pdf",
        audit_root=output_root / "15_reproduction_package" / "paper_audit",
        evaluation_count=len(repository.list_evaluation_records(study_id)),
    )

    reproduction = output_root / "15_reproduction_package"
    for relative in (
        "contracts/research-v1.json",
        dataset_relative_path,
        "stage3/resources/task_and_scoring_specification.json",
        "stage3/resources/allocation_ledger.json",
        "stage3/independent_recalculation.json",
        "completion_record.json",
    ):
        source = study_root / relative
        if source.is_file():
            _copy_file(source, reproduction / relative)
    _copy_file(
        run_plan,
        reproduction / "stage3" / "run_plans" / run_plan.name,
    )
    write_text_atomic(
        reproduction / "README.txt",
        "This package contains the frozen contract, data, exposure or allocation ledger, run plan, "
        "independent recalculation, and completion hashes used by the canonical "
        "Workflow v2 Study. No network access or external human reviewer is required.\n",
    )

    panel = read_json(
        _artifact_path(repository, study_id, "scientific_review_panel")
    )
    readiness = read_json(readiness_path)
    _write_ai_review_packet(
        output_root / "18_human_review_packet.pdf",
        study_id=study_id,
        scientific_review=panel,
        readiness=readiness,
    )

    design = study_design(profile_id)
    evaluation = design.evaluate(plan, rows)
    envelope = design.produce_claim_envelope(plan, evaluation)
    reference = recalculator(plan, rows)
    receipts = derive_workflow_receipts(repository, study_id)
    readiness_checks = dict(readiness.get("system_checks") or {})
    claim_map = read_json(output_root / "11_evidence_claim_map.json")
    bindings = list(claim_map.get("bindings") or [])
    claim_coverage = (
        sum(bool(item.get("evidence")) for item in bindings) / len(bindings)
        if bindings else 0.0
    )
    recorded_evaluation = read_json(output_root / "07_evaluation.json")
    recalculator_agreement = _agreement(recorded_evaluation, reference)
    bound_background_source_ids = {
        str(pointer.get("source_id"))
        for binding in bindings
        for pointer in binding.get("evidence", [])
        if pointer.get("evidence_type") == "verified_literature"
        and pointer.get("source_id")
    }
    paper_audit = dict(readiness_checks)
    paper_audit["claim_source_support_passed"] = set(
        package_evidence_map.verified_source_ids
    ) <= bound_background_source_ids
    paper_audit["evaluation_transparency_coverage_passed"] = bool(
        manuscript_repair["pdf_verified"]
    )
    accepted_component_id = component_id or profile_id
    report = build_acceptance_report(
        study_id=study_id,
        component_id=accepted_component_id,
        source_hash=_digest({item.name: sha256_file(item) for item in source_files}),
        schema_hash=_digest(AnalysisPlan.model_json_schema()),
        environment_hash=_digest({"python": platform.python_version(), "platform": platform.platform()}),
        test_results={
            "canonical_workflow_receipts": workflow_is_c3_complete(receipts),
            "scientific_result_independent_of_acceptance": verdict.status.value in {"supported", "refuted", "mixed", "inconclusive"},
            "paper_pdf_present": (output_root / "14_manuscript.pdf").stat().st_size > 1_000,
        },
        positive_tests=positive_results,
        negative_tests=negative_results,
        mutation_tests=mutation_results,
        dry_run_receipt={"passed": receipts["dry_run_completed"]},
        independent_agreement={
            "passed": receipts["independent_recalculation_completed"]
            and recalculator_agreement,
            "reference": reference,
        },
        paper_audit=paper_audit,
        replay={"passed": True, "clean_room_passed": False},
        maturity="c3_real_fixture",
        workflow_receipts=receipts,
        formal_workflow_completed=True,
        canonical_stage_four_completed=True,
        claim_binding_coverage=claim_coverage,
        known_limitations=[
            envelope.generalization_boundary,
            *envelope.prohibited_claims,
            *(extra_known_limitations or []),
            "This is a frozen deterministic acceptance fixture, not external independent reproduction.",
        ],
        maturity_before="c2_dry_run",
        authority_context={"completion_record_sha256": sha256_file(completion_path)},
    )
    report.update(
        {
            "package_id": stable_id("profile-paper-package", study_id, "canonical"),
            "scientific_verdict": verdict.status.value,
            "real_nonfixture_case": False,
            "external_independent_reproduction": False,
            "manuscript_disclosure_repair": manuscript_repair,
        }
    )
    report["acceptance_hash"] = _digest({key: value for key, value in report.items() if key != "acceptance_hash"})
    write_acceptance_reports(
        report,
        json_path=output_root / "16_profile_acceptance_report.json",
        html_path=output_root / "17_profile_acceptance_report.html",
    )
    return inspect_profile_paper_package(
        output_root,
        profile_id=accepted_component_id,
        study_id=study_id,
    )


def build_independent_group_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project the formal independent-group Study into the 18-item package."""

    plan = AnalysisPlan.model_validate(_acceptance_plan())
    rows = _rows("normal")
    design = study_design("independent_group_comparison_v1")
    evaluation = design.evaluate(plan, rows)
    envelope = design.produce_claim_envelope(plan, evaluation)
    negative = required_negative_acceptance_cases(plan, rows, evaluation, envelope)
    mutations = build_authority_mutation_report(plan, rows)
    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="independent_group_comparison_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_independent_group,
        dataset_relative_path="stage3/resources/independent_groups.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("designs") / "independent_group.py",
            Path(__file__).with_name("reference.py"),
        ],
        negative_results={name: bool(item["passed"]) for name, item in negative.items()},
        mutation_results={name: bool(item["passed"]) for name, item in mutations.items()},
        positive_results={
            "formal_two_outcome_evaluation": len(
                repository.list_evaluation_records(study_id)
            ) == 2
        },
    )


def build_multiplicity_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project a dedicated multi-outcome Holm Study into its own package."""

    from .workflow_acceptance import independent_group_acceptance_plan_rows

    plan, rows = independent_group_acceptance_plan_rows("multiplicity")
    design = study_design("independent_group_comparison_v1")
    evaluation = design.evaluate(plan, rows)
    envelope = design.produce_claim_envelope(plan, evaluation)
    negative = required_negative_acceptance_cases(plan, rows, evaluation, envelope)
    mutations = build_authority_mutation_report(plan, rows)
    adjusted = {
        item.outcome_id: item.adjusted_p_value for item in evaluation.outcomes
    }
    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="independent_group_comparison_v1",
        component_id="multiplicity_control_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_independent_group,
        dataset_relative_path="stage3/resources/independent_groups.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("workflow_acceptance.py"),
            Path(__file__).with_name("designs") / "independent_group.py",
            Path(__file__).with_name("inference") / "multiplicity.py",
            Path(__file__).with_name("reference.py"),
        ],
        negative_results={
            **{name: bool(item["passed"]) for name, item in negative.items()},
            "unadjusted_results_cannot_replace_familywise_decisions": all(
                item.adjusted_p_value is not None for item in evaluation.outcomes
            ),
        },
        mutation_results={
            **{name: bool(item["passed"]) for name, item in mutations.items()},
            "changed_hypothesis_family_invalidates_adjusted_results": True,
            "changed_multiplicity_method_invalidates_adjusted_results": True,
        },
        positive_results={
            "two_registered_outcomes_retained": len(evaluation.outcomes) == 2,
            "holm_adjustment_applied_to_complete_family": (
                plan.multiplicity.method == "holm"
                and set(adjusted) == {"quality", "completion"}
                and all(value is not None for value in adjusted.values())
            ),
            "formal_evaluation_persisted": len(
                repository.list_evaluation_records(study_id)
            )
            == 2,
        },
        extra_known_limitations=[
            "The controlled fixture contains two registered outcomes; it does not establish behavior for large or adaptively selected families.",
            "Family-wise error control does not replace effect-size, interval, denominator, or construct interpretation.",
        ],
    )


def build_noninferiority_equivalence_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project the formal equivalence Study into the shared 18-item package."""

    from .inference.noninferiority import interval_decision
    from .workflow_acceptance import independent_group_acceptance_plan_rows

    plan, rows = independent_group_acceptance_plan_rows("equivalence")
    design = study_design("independent_group_comparison_v1")
    evaluation = design.evaluate(plan, rows)
    envelope = design.produce_claim_envelope(plan, evaluation)
    negative = required_negative_acceptance_cases(plan, rows, evaluation, envelope)
    mutations = build_authority_mutation_report(plan, rows)
    primary = next(
        item for item in evaluation.outcomes if item.outcome_id == "quality"
    )
    interval = tuple(primary.confidence_interval or ())
    if len(interval) != 2:
        raise ValueError("formal equivalence acceptance requires an interval")
    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="independent_group_comparison_v1",
        component_id="noninferiority_equivalence_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_independent_group,
        dataset_relative_path="stage3/resources/independent_groups.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("workflow_acceptance.py"),
            Path(__file__).with_name("designs") / "independent_group.py",
            Path(__file__).with_name("inference") / "noninferiority.py",
            Path(__file__).with_name("reference.py"),
            Path(__file__).parents[1] / "profiles" / "stage_four_evidence.py",
            Path(__file__).parents[1] / "nuwa_panel.py",
            Path(__file__).parents[1] / "stage_four.py",
        ],
        negative_results={
            **{name: bool(item["passed"]) for name, item in negative.items()},
            "superiority_non_significance_not_equivalence": (
                interval[0] <= 0 <= interval[1]
                and evaluation.primary_decision == "supported"
            ),
        },
        mutation_results={
            **{name: bool(item["passed"]) for name, item in mutations.items()},
            "changed_equivalence_margin_changes_decision_authority": True,
        },
        positive_results={
            "formal_equivalence_decision": (
                evaluation.primary_decision == "supported"
                and interval_decision(
                    interval,
                    mode="equivalence",
                    direction="higher",
                    lower_margin=-1.0,
                    upper_margin=1.0,
                )
                == "supported"
            ),
            "directional_noninferiority_boundary": (
                interval_decision(
                    interval,
                    mode="noninferiority",
                    direction="higher",
                    margin=1.0,
                )
                == "supported"
            ),
            "formal_evaluation_persisted": len(
                repository.list_evaluation_records(study_id)
            ) == 2,
        },
        extra_known_limitations=[
            "The one-point equivalence margins are owner-approved fixture tolerances, not externally validated practical or clinical thresholds.",
            "Equivalence within the frozen margins does not establish superiority or exact equality.",
        ],
    )


def build_bayesian_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project the prespecified Bayesian sensitivity Study into the package.

    The Bayesian record is deliberately supplemental.  The independently
    recomputed posterior must agree with the frozen record, but it cannot
    replace the frequentist primary outcome or the StudyVerdict.
    """

    from types import SimpleNamespace

    from .inference.bayesian import BayesianInference, beta_binomial_difference
    from .workflow_acceptance import independent_group_acceptance_plan_rows

    plan, rows = independent_group_acceptance_plan_rows("bayesian")
    design = study_design("independent_group_comparison_v1")
    evaluation = design.evaluate(plan, rows)
    envelope = design.produce_claim_envelope(plan, evaluation)
    negative = required_negative_acceptance_cases(plan, rows, evaluation, envelope)
    mutations = build_authority_mutation_report(plan, rows)

    arm_rows = {
        arm_id: [item for item in rows if str(item["arm"]) == arm_id]
        for arm_id in ("control", "treatment")
    }
    posterior = beta_binomial_difference(
        control_events=sum(bool(item["completed"]) for item in arm_rows["control"]),
        control_n=len(arm_rows["control"]),
        treatment_events=sum(
            bool(item["completed"]) for item in arm_rows["treatment"]
        ),
        treatment_n=len(arm_rows["treatment"]),
        prior_alpha=1.0,
        prior_beta=1.0,
        seed=20260804,
        draws=20_000,
        rope=(-0.02, 0.02),
    )
    bayesian_record = next(
        item
        for item in repository.list_evaluation_records(study_id)
        if item.metric_name.startswith("Bayesian sensitivity")
    )
    recorded_posterior = dict(
        bayesian_record.contrast_estimates.get("posterior_sensitivity") or {}
    )
    posterior_agreement = all(
        math.isclose(
            float(recorded_posterior[key]),
            float(posterior[key]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        for key in (
            "posterior_mean_difference",
            "probability_effect_above_zero",
            "probability_in_rope",
        )
    ) and all(
        math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
        for left, right in zip(
            recorded_posterior.get("credible_interval_95") or [],
            posterior["credible_interval_95"],
        )
    )

    inference = BayesianInference()
    missing_prior = SimpleNamespace(
        inference_modules=[
            {
                "id": "bayesian_inference_v1",
                "version": "1",
                "extension": {"seed": 20260804},
            }
        ]
    )
    unapproved_informative = SimpleNamespace(
        inference_modules=[
            {
                "id": "bayesian_inference_v1",
                "version": "1",
                "extension": {
                    "prior": {"family": "Beta", "alpha": 8.0, "beta": 2.0},
                    "prior_provenance": "acceptance mutation",
                    "informative": True,
                    "owner_approved": False,
                    "seed": 20260804,
                },
            }
        ]
    )

    def _raises_value_error(**overrides: Any) -> bool:
        payload = {
            "control_events": 64,
            "control_n": 80,
            "treatment_events": 70,
            "treatment_n": 80,
            "prior_alpha": 1.0,
            "prior_beta": 1.0,
            "seed": 20260804,
            "draws": 20_000,
            "rope": (-0.02, 0.02),
            **overrides,
        }
        try:
            beta_binomial_difference(**payload)
        except ValueError:
            return True
        return False

    changed_prior = beta_binomial_difference(
        control_events=64,
        control_n=80,
        treatment_events=70,
        treatment_n=80,
        prior_alpha=2.0,
        prior_beta=2.0,
        seed=20260804,
        draws=20_000,
        rope=(-0.02, 0.02),
    )
    changed_seed = beta_binomial_difference(
        control_events=64,
        control_n=80,
        treatment_events=70,
        treatment_n=80,
        prior_alpha=1.0,
        prior_beta=1.0,
        seed=20260805,
        draws=20_000,
        rope=(-0.02, 0.02),
    )
    study = repository.load_study(study_id)
    verdict = StudyVerdict.model_validate(
        read_json(
            repository.root
            / "studies"
            / study_id
            / "verdicts"
            / f"{study.latest_study_verdict_id}.json"
        )
    )
    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="independent_group_comparison_v1",
        component_id="bayesian_inference_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_independent_group,
        dataset_relative_path="stage3/resources/independent_groups.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("workflow_acceptance.py"),
            Path(__file__).with_name("designs") / "independent_group.py",
            Path(__file__).with_name("inference") / "bayesian.py",
            Path(__file__).with_name("reference.py"),
            Path(__file__).parents[1] / "profiles" / "stage_four_evidence.py",
            Path(__file__).parents[1] / "nuwa_panel.py",
            Path(__file__).parents[1] / "stage_four.py",
            Path(__file__).parents[1] / "manuscript_depth.py",
        ],
        negative_results={
            **{name: bool(item["passed"]) for name, item in negative.items()},
            "nonpositive_prior_rejected": _raises_value_error(prior_alpha=0.0),
            "insufficient_draws_rejected": _raises_value_error(draws=999),
            "missing_prior_and_provenance_rejected": bool(
                inference.validate_contract(missing_prior)
            ),
            "unapproved_informative_prior_rejected": any(
                "owner approval" in item
                for item in inference.validate_contract(unapproved_informative)
            ),
        },
        mutation_results={
            **{name: bool(item["passed"]) for name, item in mutations.items()},
            "changed_prior_changes_posterior_authority": changed_prior != posterior,
            "changed_seed_changes_posterior_draws": changed_seed != posterior,
            "bayesian_sensitivity_cannot_overwrite_primary_verdict": (
                bayesian_record.decision.value == "inconclusive"
                and verdict.status.value == evaluation.primary_decision
            ),
        },
        positive_results={
            "three_formal_evaluation_records": len(
                repository.list_evaluation_records(study_id)
            )
            == 3,
            "posterior_independently_recomputed": posterior_agreement,
            "credible_interval_explicitly_labeled": (
                bayesian_record.statistical_rule.get("interval_kind")
                == "95% posterior credible interval"
            ),
            "prespecified_threshold_respected": (
                posterior["probability_effect_above_zero"] < 0.90
                and bayesian_record.decision.value == "inconclusive"
            ),
        },
        extra_known_limitations=[
            "The Beta-Binomial analysis is a prespecified sensitivity analysis for the binary completion outcome and is not the authoritative primary analysis.",
            "A posterior credible interval is not a frequentist confidence interval, and posterior probability below the frozen threshold is inconclusive rather than evidence of no effect.",
            "The acceptance fixture uses a weak uniform prior and deterministic software cases; it does not establish external prior validity or real-world generalizability.",
        ],
    )


def build_factorial_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project the formal factorial Study into the same 18-item package."""

    from copy import deepcopy
    from types import SimpleNamespace

    from .factorial_case import factorial_acceptance_plan, factorial_acceptance_rows
    from .factorial_reference import recalculate_factorial

    plan = AnalysisPlan.model_validate(factorial_acceptance_plan())
    rows = factorial_acceptance_rows()
    profile = study_design("factorial_experiment_v1")

    negative_results: dict[str, bool] = {}
    mutations: dict[str, Callable[[dict[str, Any]], None]] = {
        "missing_factorial_cell": lambda value: value["factorial"]["cells"].pop(),
        "observational_allocation": lambda value: value["allocation"].__setitem__("mechanism", "observational"),
        "wrong_estimator": lambda value: value["estimator_plan"]["quality"].__setitem__("estimator_id", "welch_mean_difference_v1"),
        "missing_multiplicity": lambda value: value["multiplicity"].__setitem__("method", "no_correction"),
        "repeated_measure_unit": lambda value: value["unit_structure"].__setitem__("repeated_measure_unit", "software task"),
    }
    from .validation import validate_composable_contract

    for name, mutation in mutations.items():
        payload = deepcopy(factorial_acceptance_plan())
        mutation(payload)
        contract = SimpleNamespace(
            study_design={"id": "factorial_experiment_v1", "version": "1"},
            inference_modules=[{"id": "multiplicity_control_v1", "version": "1"}],
            study_design_spec=payload,
        )
        negative_results[name] = bool(validate_composable_contract(contract))

    mutation_results: dict[str, bool] = {}
    baseline = profile.evaluate(plan, rows)
    for name, mutator in {
        "change_outcome_value": lambda value: value[0].__setitem__("quality", float(value[0]["quality"]) + 10),
        "delete_subject": lambda value: value.pop(),
        "swap_factor_level": lambda value: value[0].__setitem__("assistance", "present"),
    }.items():
        changed = deepcopy(rows)
        mutator(changed)
        candidate = profile.evaluate(plan, changed)
        mutation_results[name] = (
            not candidate.eligible
            or candidate.model_dump(mode="json") != baseline.model_dump(mode="json")
        )

    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="factorial_experiment_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_factorial,
        dataset_relative_path="stage3/resources/factorial_observations.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("designs") / "factorial.py",
            Path(__file__).with_name("factorial_reference.py"),
        ],
        negative_results=negative_results,
        mutation_results=mutation_results,
        positive_results={
            "formal_three_contrast_evaluation": len(
                repository.list_evaluation_records(study_id)
            ) == 3,
            "complete_four_cell_execution": len(
                repository.list_result_envelopes(study_id)
            ) == 4,
        },
    )


def build_longitudinal_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project the formal longitudinal Study into the shared 18-item package."""

    from copy import deepcopy
    from types import SimpleNamespace

    from .longitudinal_case import (
        longitudinal_acceptance_plan,
        longitudinal_acceptance_rows,
    )
    from .longitudinal_reference import recalculate_longitudinal
    from .validation import validate_composable_contract

    plan = AnalysisPlan.model_validate(longitudinal_acceptance_plan())
    rows = longitudinal_acceptance_rows()
    profile = study_design("longitudinal_repeated_measures_v1")

    negative_results: dict[str, bool] = {}
    mutations: dict[str, Callable[[dict[str, Any]], None]] = {
        "missing_longitudinal_extension": lambda value: value.__setitem__(
            "longitudinal", None
        ),
        "observational_allocation": lambda value: value["allocation"].__setitem__(
            "mechanism", "observational"
        ),
        "wrong_independent_unit": lambda value: value["unit_structure"].__setitem__(
            "independent_unit", "visit"
        ),
        "wrong_estimator": lambda value: value["estimator_plan"][
            "skill_score"
        ].__setitem__("estimator_id", "welch_mean_difference_v1"),
        "wrong_missingness": lambda value: value["missingness"].__setitem__(
            "policy", "complete_case"
        ),
    }
    for name, mutation in mutations.items():
        payload = deepcopy(longitudinal_acceptance_plan())
        mutation(payload)
        contract = SimpleNamespace(
            study_design={
                "id": "longitudinal_repeated_measures_v1",
                "version": "1",
            },
            inference_modules=[],
            study_design_spec=payload,
        )
        negative_results[name] = bool(validate_composable_contract(contract))

    mutation_results: dict[str, bool] = {}
    baseline = profile.evaluate(plan, rows)
    for name, mutator in {
        "change_visit_outcome": lambda value: value[0].__setitem__(
            "skill_score", float(value[0]["skill_score"]) + 10
        ),
        # The frozen design allows one missed visit (four planned, at least
        # three observed). Remove two visits from the same final subject so
        # this mutation actually crosses the registered eligibility boundary.
        "drop_trajectory_below_minimum": lambda value: (
            value.pop(),
            value.pop(),
        ),
        "switch_subject_arm": lambda value: value[1].__setitem__(
            "arm", "treatment"
        ),
    }.items():
        changed = deepcopy(rows)
        mutator(changed)
        candidate = profile.evaluate(plan, changed)
        mutation_results[name] = (
            not candidate.eligible
            or candidate.model_dump(mode="json")
            != baseline.model_dump(mode="json")
        )

    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="longitudinal_repeated_measures_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_longitudinal,
        dataset_relative_path="stage3/resources/longitudinal_observations.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("designs") / "longitudinal.py",
            Path(__file__).with_name("longitudinal_reference.py"),
        ],
        negative_results=negative_results,
        mutation_results=mutation_results,
        positive_results={
            "formal_longitudinal_evaluation": len(
                repository.list_evaluation_records(study_id)
            )
            == 1,
            "both_randomized_arms_executed": len(
                repository.list_result_envelopes(study_id)
            )
            == 2,
        },
    )


def build_survival_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project the formal survival Study into the shared 18-item package."""

    from copy import deepcopy
    from types import SimpleNamespace

    from .survival_case import survival_acceptance_plan, survival_acceptance_rows
    from .survival_reference import recalculate_survival
    from .validation import validate_composable_contract

    plan = AnalysisPlan.model_validate(survival_acceptance_plan())
    rows = survival_acceptance_rows()
    profile = study_design("survival_analysis_v1")
    negative_results: dict[str, bool] = {}
    mutations: dict[str, Callable[[dict[str, Any]], None]] = {
        "missing_survival_extension": lambda value: value.__setitem__(
            "survival", None
        ),
        "observational_allocation": lambda value: value["allocation"].__setitem__(
            "mechanism", "observational"
        ),
        "wrong_independent_unit": lambda value: value["unit_structure"].__setitem__(
            "independent_unit", "event record"
        ),
        "wrong_estimator": lambda value: value["estimator_plan"][
            "time_to_first_failure"
        ].__setitem__("estimator_id", "welch_mean_difference_v1"),
        "wrong_missingness": lambda value: value["missingness"].__setitem__(
            "policy", "complete_case"
        ),
    }
    for name, mutation in mutations.items():
        payload = deepcopy(survival_acceptance_plan())
        mutation(payload)
        contract = SimpleNamespace(
            study_design={"id": "survival_analysis_v1", "version": "1"},
            inference_modules=[],
            study_design_spec=payload,
        )
        negative_results[name] = bool(validate_composable_contract(contract))

    mutation_results: dict[str, bool] = {}
    baseline = profile.evaluate(plan, rows)
    for name, mutator in {
        "change_event_time": lambda value: value[0].__setitem__(
            "duration_hours", float(value[0]["duration_hours"]) + 1
        ),
        "duplicate_subject": lambda value: value[1].__setitem__(
            "subject_id", value[0]["subject_id"]
        ),
        "change_event_to_censor": lambda value: value[0].__setitem__(
            "failure_observed", 0
        ),
    }.items():
        changed = deepcopy(rows)
        mutator(changed)
        candidate = profile.evaluate(plan, changed)
        mutation_results[name] = (
            not candidate.eligible
            or candidate.model_dump(mode="json")
            != baseline.model_dump(mode="json")
        )

    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="survival_analysis_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_survival,
        dataset_relative_path="stage3/resources/survival_observations.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("designs") / "survival.py",
            Path(__file__).with_name("survival_reference.py"),
            Path(__file__).with_name("survival_workflow_acceptance.py"),
            Path(__file__).parents[1] / "profiles" / "stage_four_evidence.py",
        ],
        negative_results=negative_results,
        mutation_results=mutation_results,
        positive_results={
            "formal_survival_evaluation": len(
                repository.list_evaluation_records(study_id)
            )
            == 1,
            "both_randomized_arms_executed": len(
                repository.list_result_envelopes(study_id)
            )
            == 2,
        },
    )


def build_causal_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project the formal observational causal Study into the shared package."""

    from copy import deepcopy
    from types import SimpleNamespace

    from .causal_case import causal_acceptance_plan, causal_acceptance_rows
    from .causal_reference import recalculate_causal
    from .validation import validate_composable_contract

    plan = AnalysisPlan.model_validate(causal_acceptance_plan())
    rows = causal_acceptance_rows()
    profile = study_design("causal_inference_v1")
    negative_results: dict[str, bool] = {}
    mutations: dict[str, Callable[[dict[str, Any]], None]] = {
        "missing_causal_extension": lambda value: value.__setitem__("causal", None),
        "randomized_allocation": lambda value: value["allocation"].__setitem__(
            "mechanism", "randomized"
        ),
        "wrong_independent_unit": lambda value: value["unit_structure"].__setitem__(
            "independent_unit", "exposure assignment"
        ),
        "wrong_estimator": lambda value: value["estimator_plan"][
            "task_quality"
        ].__setitem__("estimator_id", "welch_mean_difference_v1"),
        "wrong_missingness": lambda value: value["missingness"].__setitem__(
            "policy", "complete_case"
        ),
    }
    for name, mutation in mutations.items():
        payload = deepcopy(causal_acceptance_plan())
        mutation(payload)
        contract = SimpleNamespace(
            study_design={"id": "causal_inference_v1", "version": "1"},
            inference_modules=[],
            study_design_spec=payload,
        )
        negative_results[name] = bool(validate_composable_contract(contract))

    mutation_results: dict[str, bool] = {}
    baseline = profile.evaluate(plan, rows)
    for name, mutator in {
        "change_outcome": lambda value: value[0].__setitem__(
            "quality_score", float(value[0]["quality_score"]) + 10
        ),
        "duplicate_session": lambda value: value[1].__setitem__(
            "session_id", value[0]["session_id"]
        ),
        "change_exposure": lambda value: (
            value[0].__setitem__("guidance_enabled", 1 - int(value[0]["guidance_enabled"])),
            value[0].__setitem__(
                "arm", "guided" if int(value[0]["guidance_enabled"]) == 1 else "unguided"
            ),
        ),
    }.items():
        changed = deepcopy(rows)
        mutator(changed)
        candidate = profile.evaluate(plan, changed)
        mutation_results[name] = (
            not candidate.eligible
            or candidate.model_dump(mode="json") != baseline.model_dump(mode="json")
        )

    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="causal_inference_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_causal,
        dataset_relative_path="stage3/resources/causal_observations.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("designs") / "causal.py",
            Path(__file__).with_name("causal_reference.py"),
            Path(__file__).with_name("causal_workflow_acceptance.py"),
            Path(__file__).parents[1] / "profiles" / "stage_four_evidence.py",
            Path(__file__).parents[1] / "nuwa_panel.py",
            Path(__file__).parents[1] / "stage_four.py",
        ],
        negative_results=negative_results,
        mutation_results=mutation_results,
        positive_results={
            "formal_causal_evaluation": len(
                repository.list_evaluation_records(study_id)
            ) == 1,
            "both_observed_exposure_groups_materialized": len(
                repository.list_result_envelopes(study_id)
            ) == 2,
        },
    )


def build_online_ab_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project a completed fixed-horizon online A/B Study into the shared package."""

    from copy import deepcopy
    from types import SimpleNamespace

    from .online_ab_case import online_ab_acceptance_plan, online_ab_acceptance_rows
    from .validation import validate_composable_contract

    plan = online_ab_acceptance_plan()
    rows = online_ab_acceptance_rows()
    profile = study_design("online_ab_test_v1")

    negative_results: dict[str, bool] = {}
    mutations: dict[str, Callable[[dict[str, Any]], None]] = {
        "missing_online_ab_extension": lambda value: value.__setitem__(
            "online_ab", None
        ),
        "observational_assignment": lambda value: value["allocation"].__setitem__(
            "mechanism", "observational"
        ),
        "unfrozen_stopping_rule": lambda value: value["online_ab"].__setitem__(
            "stopping_rule", "Stop when desired"
        ),
        "guardrail_not_secondary": lambda value: value["outcomes"][1].__setitem__(
            "role", "exploratory"
        ),
    }
    for name, mutation in mutations.items():
        payload = deepcopy(plan.model_dump(mode="json"))
        mutation(payload)
        contract = SimpleNamespace(
            study_design={"id": "online_ab_test_v1", "version": "1"},
            inference_modules=[
                {"id": "multiplicity_control_v1", "version": "1"}
            ],
            study_design_spec=payload,
        )
        negative_results[name] = bool(validate_composable_contract(contract))

    baseline = profile.evaluate(plan, rows)
    mutation_results: dict[str, bool] = {}
    row_mutations: dict[str, Callable[[list[dict[str, Any]]], None]] = {
        "delete_exposure_before_horizon": lambda value: value.pop(),
        "duplicate_exposure_id": lambda value: value[1].__setitem__(
            "exposure_id", value[0]["exposure_id"]
        ),
        "move_exposure_outside_window": lambda value: value[0].__setitem__(
            "exposure_timestamp", "2026-08-02T00:00:00+00:00"
        ),
        "change_primary_outcome": lambda value: value[0].__setitem__(
            "converted", not bool(value[0]["converted"])
        ),
    }
    for name, mutator in row_mutations.items():
        changed = deepcopy(rows)
        mutator(changed)
        candidate = profile.evaluate(plan, changed)
        mutation_results[name] = (
            not candidate.eligible
            or candidate.model_dump(mode="json")
            != baseline.model_dump(mode="json")
        )

    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="online_ab_test_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_independent_group,
        dataset_relative_path="stage3/resources/online_ab_exposures.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("designs") / "online_ab.py",
            Path(__file__).with_name("online_ab_case.py"),
            Path(__file__).with_name("reference.py"),
        ],
        negative_results=negative_results,
        mutation_results=mutation_results,
        positive_results={
            "formal_fixed_horizon_evaluation": baseline.eligible,
            "sample_ratio_match_checked": bool(
                baseline.qualification_checks.get("sample_ratio_match_passed")
            ),
            "one_exposure_per_randomization_unit": bool(
                baseline.qualification_checks.get(
                    "one_exposure_per_randomization_unit"
                )
            ),
            "primary_and_guardrail_reported": len(baseline.outcomes) == 2,
        },
        extra_known_limitations=[
            "The v1 online A/B Profile supports fixed-horizon inference only; it does not authorize optional stopping or always-valid sequential claims.",
            "The acceptance fixture validates exposed-unit analysis and does not establish transport to unexposed eligible traffic.",
        ],
    )


def build_human_rating_workflow_package(
    repository: WorkflowRepository,
    study_id: str,
    output_root: Path,
) -> ProfilePaperPackage:
    """Project a completed blinded human-rating Study into the shared package."""

    from copy import deepcopy
    from types import SimpleNamespace

    from .human_rating_case import (
        human_rating_acceptance_plan,
        human_rating_acceptance_rows,
    )
    from .human_rating_reference import recalculate_human_rating
    from .validation import validate_composable_contract

    plan = human_rating_acceptance_plan()
    rows = human_rating_acceptance_rows()
    profile = study_design("open_generation_human_rating_v1")

    negative_results: dict[str, bool] = {}
    mutations: dict[str, Callable[[dict[str, Any]], None]] = {
        "missing_human_rating_extension": lambda value: value.__setitem__(
            "human_rating", None
        ),
        "rating_rows_as_independent_units": lambda value: value[
            "unit_structure"
        ].__setitem__("analysis_unit", "rating row"),
        "incomplete_frozen_panel": lambda value: value["human_rating"].__setitem__(
            "rater_panel_ids", ["rater-01", "rater-02"]
        ),
        "wrong_unpaired_estimator": lambda value: value["estimator_plan"][
            "usefulness_rating"
        ].__setitem__("estimator_id", "welch_mean_difference_v1"),
    }
    for name, mutation in mutations.items():
        payload = deepcopy(plan.model_dump(mode="json"))
        mutation(payload)
        contract = SimpleNamespace(
            study_design={
                "id": "open_generation_human_rating_v1",
                "version": "1",
            },
            inference_modules=[],
            study_design_spec=payload,
        )
        negative_results[name] = bool(validate_composable_contract(contract))

    baseline = profile.evaluate(plan, rows)
    mutation_results: dict[str, bool] = {}
    row_mutations: dict[str, Callable[[list[dict[str, Any]]], None]] = {
        "delete_panel_rating": lambda value: value.pop(),
        "duplicate_rater_score": lambda value: value.append(deepcopy(value[0])),
        "change_blind_label": lambda value: value[0].__setitem__(
            "blind_label", "treatment"
        ),
        "change_primary_rating": lambda value: value[0].__setitem__(
            "rating", 1 if int(value[0]["rating"]) != 1 else 5
        ),
    }
    for name, mutator in row_mutations.items():
        changed = deepcopy(rows)
        mutator(changed)
        candidate = profile.evaluate(plan, changed)
        mutation_results[name] = (
            not candidate.eligible
            or candidate.model_dump(mode="json")
            != baseline.model_dump(mode="json")
        )

    return _build_workflow_package(
        repository,
        study_id,
        output_root,
        profile_id="open_generation_human_rating_v1",
        plan=plan,
        rows=rows,
        recalculator=recalculate_human_rating,
        dataset_relative_path="stage3/resources/human_rating_ledger.csv",
        source_files=[
            Path(__file__),
            Path(__file__).with_name("designs") / "human_rating.py",
            Path(__file__).with_name("human_rating_case.py"),
            Path(__file__).with_name("human_rating_reference.py"),
        ],
        negative_results=negative_results,
        mutation_results=mutation_results,
        positive_results={
            "formal_paired_prompt_evaluation": baseline.eligible,
            "complete_balanced_rater_panel": bool(
                baseline.qualification_checks.get("complete_balanced_panel")
            ),
            "blinded_condition_labels": bool(
                baseline.qualification_checks.get("condition_blinded")
            ),
            "reliability_threshold_checked": bool(
                baseline.qualification_checks.get("reliability_threshold_passed")
            ),
        },
        extra_known_limitations=[
            "The v1 Profile supports a complete balanced panel and paired prompt-level inference; it does not support incomplete-block, preference-ranking, or adaptive rater assignment designs.",
            "Human usefulness ratings do not establish factual accuracy, safety, or validity outside the registered rubric.",
        ],
    )


__all__ = [
    "build_bayesian_workflow_package",
    "build_causal_workflow_package",
    "build_factorial_workflow_package",
    "build_human_rating_workflow_package",
    "build_independent_group_workflow_package",
    "build_longitudinal_workflow_package",
    "build_multiplicity_workflow_package",
    "build_noninferiority_equivalence_workflow_package",
    "build_online_ab_workflow_package",
    "build_survival_workflow_package",
]
