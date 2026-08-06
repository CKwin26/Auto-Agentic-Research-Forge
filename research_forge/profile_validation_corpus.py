from __future__ import annotations

"""Black-box publication corpus for the ten formal experiment Profiles.

Each case owns a distinct Workflow v2 Study and output directory.  The suite
report is only an index over those independent runs; it is never a manuscript
and cannot merge scientific authority across Studies.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import Field

from .models import StrictModel, utc_now
from .storage import read_json, sha256_file, write_json_atomic
from .workflow_domain import WorkflowRepository


Runner = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ProfileValidationCase:
    ordinal: int
    profile_id: str
    directory_name: str
    natural_language_name: str
    runner: Runner
    runner_kwargs: dict[str, Any]


class ProfilePaperAcceptance(StrictModel):
    ordinal: int = Field(ge=1, le=10)
    profile_id: str
    natural_language_name: str
    study_id: str | None = None
    workflow_repository: str | None = None
    output_directory: str
    scientific_verdict: str | None = None
    pdf_path: str | None = None
    pdf_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    publication_acceptance_report_path: str | None = None
    publication_ready: bool = False
    blocking_steps: list[dict[str, Any]] = Field(default_factory=list)
    execution_error: str | None = None


class ProfileValidationCorpusReport(StrictModel):
    schema_version: int = 2
    corpus_id: str = "profile-validation-corpus-v2"
    independent_study_count: int
    papers: list[ProfilePaperAcceptance]
    all_profiles_present: bool
    all_studies_distinct: bool
    all_pdfs_distinct: bool
    all_publication_ready: bool
    generated_at: str = Field(default_factory=utc_now)


def profile_validation_cases() -> tuple[ProfileValidationCase, ...]:
    """Return the fixed ten-case corpus without starting any work."""

    from .study_design import (
        run_causal_workflow_acceptance,
        run_factorial_workflow_acceptance,
        run_human_rating_workflow_acceptance,
        run_independent_group_workflow_acceptance,
        run_longitudinal_workflow_acceptance,
        run_online_ab_workflow_acceptance,
        run_survival_workflow_acceptance,
    )

    return (
        ProfileValidationCase(1, "independent_group_comparison_v1", "01-independent-group", "Independent-group comparison", run_independent_group_workflow_acceptance, {"acceptance_variant": "superiority"}),
        ProfileValidationCase(2, "factorial_experiment_v1", "02-factorial", "Multi-factor factorial experiment", run_factorial_workflow_acceptance, {}),
        ProfileValidationCase(3, "longitudinal_repeated_measures_v1", "03-longitudinal", "Repeated-measures longitudinal study", run_longitudinal_workflow_acceptance, {}),
        ProfileValidationCase(4, "survival_analysis_v1", "04-survival", "Time-to-event analysis", run_survival_workflow_acceptance, {}),
        ProfileValidationCase(5, "causal_inference_v1", "05-causal", "Causal observational analysis", run_causal_workflow_acceptance, {}),
        ProfileValidationCase(6, "noninferiority_equivalence_v1", "06-equivalence", "Noninferiority and equivalence experiment", run_independent_group_workflow_acceptance, {"acceptance_variant": "equivalence"}),
        ProfileValidationCase(7, "bayesian_inference_v1", "07-bayesian", "Bayesian analysis", run_independent_group_workflow_acceptance, {"acceptance_variant": "bayesian"}),
        ProfileValidationCase(8, "online_ab_test_v1", "08-online-ab", "Online randomized A/B test", run_online_ab_workflow_acceptance, {}),
        ProfileValidationCase(9, "open_generation_human_rating_v1", "09-human-rating", "Open-ended generation with blinded human ratings", run_human_rating_workflow_acceptance, {}),
        ProfileValidationCase(10, "multiplicity_control_v1", "10-multiplicity", "Multi-outcome analysis with multiplicity control", run_independent_group_workflow_acceptance, {"acceptance_variant": "multiplicity"}),
    )


def _acceptance_from_result(
    case: ProfileValidationCase,
    output_directory: Path,
    result: dict[str, Any],
) -> ProfilePaperAcceptance:
    repository_path = str(result.get("workflow_repository") or "")
    study_id = str(result.get("study_id") or "")
    pdf_path: str | None = None
    pdf_sha: str | None = None
    acceptance_path: str | None = None
    publication_ready = False
    if repository_path and study_id:
        repository = WorkflowRepository(repository_path)
        pdfs = [
            item
            for item in repository.list_artifacts(study_id)
            if item.kind == "final_manuscript_pdf"
        ]
        if pdfs:
            latest_pdf = max(pdfs, key=lambda item: (item.version, item.created_at))
            candidate = Path(latest_pdf.path).resolve()
            if candidate.is_file() and sha256_file(candidate) == latest_pdf.sha256:
                pdf_path = str(candidate)
                pdf_sha = latest_pdf.sha256
        reports = [
            item
            for item in repository.list_artifacts(study_id)
            if item.kind == "publication_acceptance_report"
        ]
        if reports:
            latest_report = max(
                reports, key=lambda item: (item.version, item.created_at)
            )
            report_path = Path(latest_report.path).resolve()
            if report_path.is_file() and sha256_file(report_path) == latest_report.sha256:
                acceptance_path = str(report_path)
                publication_ready = bool(
                    read_json(report_path).get("publication_ready")
                )
    return ProfilePaperAcceptance(
        ordinal=case.ordinal,
        profile_id=case.profile_id,
        natural_language_name=case.natural_language_name,
        study_id=study_id or None,
        workflow_repository=repository_path or None,
        output_directory=str(output_directory),
        scientific_verdict=(
            str(result.get("scientific_verdict"))
            if result.get("scientific_verdict") is not None
            else None
        ),
        pdf_path=pdf_path,
        pdf_sha256=pdf_sha,
        publication_acceptance_report_path=acceptance_path,
        publication_ready=publication_ready,
        blocking_steps=list(result.get("blocking_steps") or []),
    )


def run_profile_validation_corpus(
    output_root: str | Path,
    *,
    selected_profile_ids: set[str] | None = None,
    continue_on_error: bool = True,
    max_cycles: int = 20,
) -> ProfileValidationCorpusReport:
    """Run ten independent pipelines and write one non-authoritative index."""

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    papers: list[ProfilePaperAcceptance] = []
    cases = [
        case
        for case in profile_validation_cases()
        if selected_profile_ids is None or case.profile_id in selected_profile_ids
    ]
    for case in cases:
        output_directory = root / case.directory_name
        try:
            result = case.runner(
                output_directory,
                max_cycles=max_cycles,
                **case.runner_kwargs,
            )
            papers.append(_acceptance_from_result(case, output_directory, result))
        except Exception as exc:
            papers.append(
                ProfilePaperAcceptance(
                    ordinal=case.ordinal,
                    profile_id=case.profile_id,
                    natural_language_name=case.natural_language_name,
                    output_directory=str(output_directory),
                    execution_error=f"{type(exc).__name__}: {exc}",
                )
            )
            if not continue_on_error:
                raise
    study_ids = [item.study_id for item in papers if item.study_id]
    pdf_paths = [item.pdf_path for item in papers if item.pdf_path]
    report = ProfileValidationCorpusReport(
        independent_study_count=len(set(study_ids)),
        papers=papers,
        all_profiles_present=(
            len(papers) == 10
            and {item.profile_id for item in papers}
            == {item.profile_id for item in profile_validation_cases()}
        ),
        all_studies_distinct=(
            len(study_ids) == len(papers) == len(set(study_ids))
        ),
        all_pdfs_distinct=(
            len(pdf_paths) == len(papers) == len(set(pdf_paths))
        ),
        all_publication_ready=(
            bool(papers) and all(item.publication_ready for item in papers)
        ),
    )
    write_json_atomic(root / "profile_validation_corpus_report_v2.json", report)
    return report


__all__ = [
    "ProfilePaperAcceptance",
    "ProfileValidationCase",
    "ProfileValidationCorpusReport",
    "profile_validation_cases",
    "run_profile_validation_corpus",
]
