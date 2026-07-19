from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .models import Direction, LiteratureSourceType
from .runner import execute_run, promote_run, recover_run
from .service import (
    configure_project,
    create_project,
    freeze_project,
    key_is_present,
    load_proposal,
    plan_project,
    project_status,
    propose_experiment,
    register_literature_source,
    render_report,
    list_literature_sources,
)
from .storage import project_dir, read_json


def _add_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_runtime: str,
    default_docker_image: str = "python:3.12-slim",
) -> None:
    parser.add_argument("--runtime", choices=["local", "docker"], default=default_runtime)
    parser.add_argument("--docker-image", default=default_docker_image)
    parser.add_argument("--cpus", type=float, default=1.0)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--pids-limit", type=int, default=256)
    parser.add_argument("--tmpfs-mb", type=int, default=512)
    parser.add_argument("--max-output-mb", type=int, default=256)


def _runtime_options(args: argparse.Namespace):
    from .runtime import RuntimeOptions

    return RuntimeOptions(
        kind=args.runtime,
        image=args.docker_image,
        cpus=args.cpus,
        memory_mb=args.memory_mb,
        pids_limit=args.pids_limit,
        tmpfs_mb=args.tmpfs_mb,
        max_output_mb=args.max_output_mb,
    )


def _selected_seeds(args: argparse.Namespace) -> list[int] | None:
    values = list(getattr(args, "seeds", None) or [])
    comma_seeds = getattr(args, "seeds_csv", None)
    if comma_seeds:
        try:
            values.extend(int(item.strip()) for item in comma_seeds.split(",") if item.strip())
        except ValueError as exc:
            raise ValueError("--seeds must be a comma-separated list of integers") from exc
    if not values:
        return None
    if len(values) != len(set(values)):
        raise ValueError("seeds must be unique")
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-forge",
        description="Local-first AI research loop with deterministic scientific gates.",
    )
    parser.add_argument("--home", help="Project workspace root (default: ./workspaces)")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a research project")
    init.add_argument("--name", required=True)
    init.add_argument("--idea", required=True)
    init.add_argument("--slug")

    plan = sub.add_parser("plan", help="Draft or revise the research contract with the agent")
    plan.add_argument("project")
    plan.add_argument("--message", required=True)

    source = sub.add_parser("source", help="Register and inspect research sources")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_add = source_sub.add_parser("add", help="Register one source before freezing")
    source_add.add_argument("project")
    source_add.add_argument("--id", required=True)
    source_add.add_argument(
        "--type", choices=[item.value for item in LiteratureSourceType], required=True
    )
    source_add.add_argument("--title", required=True)
    source_add.add_argument("--author", action="append", required=True)
    source_add.add_argument("--year", type=int)
    source_add.add_argument("--locator", required=True, help="DOI, URL, or local canonical path")
    source_add.add_argument("--notes", default="")
    source_add.add_argument("--verification", required=True)
    source_add.add_argument(
        "--verified",
        action="store_true",
        help="Explicitly attest that the metadata and locator were checked",
    )
    source_list = source_sub.add_parser("list", help="List registered sources")
    source_list.add_argument("project")

    literature = sub.add_parser("literature", help="Run the evidence-bound Stage 1 workflow")
    literature_sub = literature.add_subparsers(dest="literature_command", required=True)
    literature_plan = literature_sub.add_parser(
        "plan", help="Use Codex to create bounded scholarly search queries"
    )
    literature_plan.add_argument("project")
    literature_plan.add_argument("--focus", default="")
    literature_discover = literature_sub.add_parser(
        "discover", help="Query, deduplicate, verify, rank, and screen real papers"
    )
    literature_discover.add_argument("project")
    literature_discover.add_argument("--search-plan", default="latest")
    literature_discover.add_argument("--rows-per-query", type=int, default=20)
    literature_discover.add_argument("--include", type=int, default=10)
    literature_discover.add_argument("--from-year", type=int)
    literature_discover.add_argument("--min-relevance", type=float, default=0.15)
    literature_screen = literature_sub.add_parser(
        "screen", help="Use Codex for bounded relevance judgments, then apply fixed quotas"
    )
    literature_screen.add_argument("project")
    literature_screen.add_argument("--max-candidates", type=int, default=40)
    literature_screen.add_argument("--include", type=int, default=12)
    literature_synthesize = literature_sub.add_parser(
        "synthesize", help="Create a source-bound related-work and novelty map"
    )
    literature_synthesize.add_argument("project")
    literature_approve = literature_sub.add_parser(
        "approve", help="Approve the reviewed shortlist and novelty map by exact review ID"
    )
    literature_approve.add_argument("project")
    literature_approve.add_argument("--confirm", required=True)
    literature_approve.add_argument("--novelty", required=True)
    literature_approve.add_argument("--note", default="")
    literature_audit = literature_sub.add_parser("audit", help="Audit every Stage 1 artifact and gate")
    literature_audit.add_argument("project")

    configure = sub.add_parser("configure", help="Configure deterministic execution and metrics")
    configure.add_argument("project")
    configure.add_argument("--primary", required=True)
    configure.add_argument("--direction", choices=[item.value for item in Direction], required=True)
    configure.add_argument("--entrypoint", default="run_experiment.py")
    configure.add_argument("--required-metric", action="append", default=[])
    configure.add_argument("--timeout", type=int, default=3600)
    configure.add_argument("--max-runs", type=int, default=100)
    configure.add_argument("--required-repeats", type=int, default=1)
    configure.add_argument("--max-repeats", type=int, default=5)
    configure.add_argument("--min-delta", type=float, default=0.0)
    configure.add_argument("--extra-arg", action="append", default=[])

    freeze = sub.add_parser("freeze", help="Freeze research and execution contracts")
    freeze.add_argument("project")

    baseline = sub.add_parser("baseline", help="Execute and verify the baseline")
    baseline.add_argument("project")
    baseline.add_argument("--repeats", type=int)
    _add_runtime_arguments(baseline, default_runtime="local")

    propose = sub.add_parser("propose", help="Ask the agent for one bounded experiment")
    propose.add_argument("project")
    propose.add_argument("--focus", default="Choose the highest-information next experiment.")

    run = sub.add_parser("run", help="Execute a validated experiment proposal")
    run.add_argument("project")
    run.add_argument("--proposal", default="latest")
    run.add_argument("--repeats", type=int)
    _add_runtime_arguments(run, default_runtime="local")

    promote = sub.add_parser("promote", help="Promote an improving run after explicit review")
    promote.add_argument("project")
    promote.add_argument("run_id")
    promote.add_argument("--confirm", required=True)

    recover = sub.add_parser("recover", help="Mark an interrupted active run complete or abandoned")
    recover.add_argument("project")
    recover.add_argument("--confirm", required=True, help="Exact active run ID")

    status = sub.add_parser("status", help="Show project state and evidence counts")
    status.add_argument("project")

    report = sub.add_parser("report", help="Render an evidence-grounded Markdown report")
    report.add_argument("project")

    synthesize = sub.add_parser(
        "synthesize", help="Generate a claim-linked manuscript and deterministic audit"
    )
    synthesize.add_argument("project")

    synthesis_audit = sub.add_parser("audit-synthesis", help="Re-audit synthesis artifacts")
    synthesis_audit.add_argument("project")

    journal = sub.add_parser(
        "journal",
        help="Recommend journals with auditable scope, maturity, and probability intervals",
    )
    journal_sub = journal.add_subparsers(dest="journal_command", required=True)
    journal_recommend = journal_sub.add_parser(
        "recommend",
        help="Rank candidate journals and separate current from post-remediation success estimates",
    )
    journal_recommend.add_argument(
        "path",
        help="Research Forge project slug/directory, or an English .tex/.md manuscript",
    )
    journal_recommend.add_argument(
        "--project",
        help="Optional Research Forge project directory supplying protocol and maturity evidence",
    )
    journal_recommend.add_argument("--registry", help="Optional journal-registry JSON")
    journal_recommend.add_argument(
        "--history",
        help="Optional local submission-history JSON; at least five records per journal are required for calibration",
    )
    journal_recommend.add_argument(
        "--assessment",
        help="Optional JSON object overriding automatically inferred assessment fields",
    )
    journal_recommend.add_argument("--top", type=int, default=8)
    journal_recommend.add_argument("--report", help="Output JSON path")
    journal_recommend.add_argument("--markdown", help="Output Markdown path")

    venue = sub.add_parser(
        "venue",
        help="Recommend only strict-whitelist journals and archival conference tracks",
    )
    venue_sub = venue.add_subparsers(dest="venue_command", required=True)
    venue_recommend = venue_sub.add_parser(
        "recommend",
        help="Rank established journals and formal conference tracks separately",
    )
    venue_recommend.add_argument(
        "path",
        help="Research Forge project slug/directory, or an English .tex/.md manuscript",
    )
    venue_recommend.add_argument(
        "--project",
        help="Optional Research Forge project directory supplying protocol and maturity evidence",
    )
    venue_recommend.add_argument("--registry", help="Optional strict venue-registry JSON")
    venue_recommend.add_argument(
        "--history",
        help="Optional local submission-history JSON; at least five same-venue records are required for calibration",
    )
    venue_recommend.add_argument(
        "--assessment",
        help="Optional JSON object overriding automatically inferred assessment fields",
    )
    venue_recommend.add_argument(
        "--type",
        choices=["all", "journal", "conference"],
        default="all",
        help="Limit output to one venue type",
    )
    venue_recommend.add_argument("--top", type=int, default=8, help="Maximum per venue type")
    venue_recommend.add_argument("--report", help="Output JSON path")
    venue_recommend.add_argument("--markdown", help="Output Markdown path")
    for evidence_parser in (journal_recommend, venue_recommend):
        evidence_parser.add_argument(
            "--live-evidence",
            action="store_true",
            help=(
                "Send only the manuscript title and abstract to OpenAlex semantic search; "
                "the full manuscript is never uploaded"
            ),
        )
        evidence_parser.add_argument(
            "--openreview-export",
            help="Optional local JSON/JSONL export of accepted OpenReview papers",
        )
        evidence_parser.add_argument(
            "--evidence-file",
            help="Optional normalized local similar-paper evidence JSON",
        )
        evidence_parser.add_argument(
            "--evidence-cache",
            help="Optional OpenAlex evidence-cache JSON path",
        )
        evidence_parser.add_argument(
            "--evidence-from-year",
            type=int,
            default=2021,
            help="Earliest publication year for live similar-paper evidence",
        )
        evidence_parser.add_argument(
            "--evidence-results",
            type=int,
            default=50,
            help="Number of OpenAlex semantic neighbors (1-50)",
        )
        evidence_parser.add_argument(
            "--ccf-deadlines",
            help="Optional local checkout of ccfddl/ccf-deadlines for ranks and cycle metadata",
        )

    venue_target = venue_sub.add_parser(
        "target",
        help="Freeze a venue-first publication target and emit the first 60% readiness audit",
    )
    venue_target.add_argument("path", help="English .tex/.md manuscript")
    venue_target.add_argument(
        "--project",
        required=True,
        help="Research Forge project directory supplying protocol and maturity evidence",
    )
    venue_target.add_argument(
        "--venue",
        required=True,
        help="Exact strict-registry venue id",
    )
    venue_target.add_argument(
        "--threshold",
        type=float,
        default=0.60,
        help="Internal submission-readiness threshold; never interpreted as acceptance probability",
    )
    venue_target.add_argument("--registry", help="Optional strict venue-registry JSON")
    venue_target.add_argument("--history", help="Optional local same-venue submission history")
    venue_target.add_argument("--contract", help="Output target-contract JSON path")
    venue_target.add_argument("--report", help="Output readiness-report JSON path")
    venue_target.add_argument("--markdown", help="Output readiness-report Markdown path")
    venue_target.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace a different frozen publication target",
    )

    venue_readiness = venue_sub.add_parser(
        "readiness",
        help="Re-audit a frozen publication target; exits 2 when the submission gate is blocked",
    )
    venue_readiness.add_argument("project", help="Research Forge project directory")
    venue_readiness.add_argument("--path", help="Updated manuscript; defaults to the contract baseline path")
    venue_readiness.add_argument("--contract", help="Frozen target-contract JSON path")
    venue_readiness.add_argument("--registry", help="Optional strict venue-registry JSON")
    venue_readiness.add_argument("--history", help="Optional local same-venue submission history")
    venue_readiness.add_argument("--report", help="Output readiness-report JSON path")
    venue_readiness.add_argument("--markdown", help="Output readiness-report Markdown path")
    venue_readiness.add_argument(
        "--no-fail",
        action="store_true",
        help="Write the audit without returning exit code 2 when blocked",
    )
    venue_experiment_target = venue_sub.add_parser(
        "freeze-experiment-target",
        help="Freeze one strict venue recommendation as the pre-experiment publication contract",
    )
    venue_experiment_target.add_argument("project", help="Research Forge project directory")
    venue_experiment_target.add_argument("--venue", required=True, help="Exact strict-registry venue id")
    venue_experiment_target.add_argument(
        "--recommendation-report",
        help="Venue recommendation JSON; defaults to project/synthesis/venue_recommendation.json",
    )
    venue_experiment_target.add_argument("--registry", help="Optional strict venue-registry JSON")
    venue_experiment_target.add_argument("--threshold", type=float, default=0.60)
    venue_experiment_target.add_argument("--output", help="Output publication experiment contract")
    venue_experiment_target.add_argument("--replace", action="store_true")

    manuscript = sub.add_parser(
        "manuscript",
        help="Run deterministic manuscript finalization gates",
    )
    manuscript_sub = manuscript.add_subparsers(
        dest="manuscript_command", required=True
    )
    manuscript_depth = manuscript_sub.add_parser(
        "audit-depth",
        help="Block finalization when a paper is structurally complete but substantively too thin",
    )
    manuscript_depth.add_argument("path", help="English or Chinese .tex/.md manuscript")
    manuscript_depth.add_argument(
        "--profile",
        choices=["journal-article", "short-report"],
        default="journal-article",
    )
    manuscript_depth.add_argument(
        "--language",
        choices=["auto", "en", "zh"],
        default="auto",
    )
    manuscript_depth.add_argument(
        "--report",
        help="Optional JSON report path; parent directories are created",
    )
    manuscript_finalize = manuscript_sub.add_parser(
        "finalize-pdf",
        help="Run the depth gate, compile in staging, and publish a hash-bound final PDF",
    )
    manuscript_finalize.add_argument("path", help="LaTeX manuscript to finalize")
    manuscript_finalize.add_argument("--output-dir")
    manuscript_finalize.add_argument(
        "--profile",
        choices=["journal-article", "short-report"],
        default="journal-article",
    )
    manuscript_finalize.add_argument(
        "--language",
        choices=["auto", "en", "zh"],
        default="auto",
    )
    manuscript_finalize.add_argument(
        "--engine",
        choices=["auto", "pdflatex", "xelatex", "tectonic"],
        default="auto",
    )
    manuscript_finalize.add_argument("--passes", type=int, default=2)
    manuscript_finalize.add_argument("--report")
    manuscript_finalize.add_argument("--manifest")

    terminology = sub.add_parser(
        "terminology",
        help="Prepare and curate the project bilingual academic termbase",
    )
    terminology_sub = terminology.add_subparsers(
        dest="terminology_command", required=True
    )
    terminology_prepare = terminology_sub.add_parser(
        "prepare",
        help="Extract and freeze a zh-CN terminology plan for the English manuscript",
    )
    terminology_prepare.add_argument("project")
    terminology_prepare.add_argument("--language", choices=["zh-CN"], default="zh-CN")
    terminology_import = terminology_sub.add_parser(
        "import",
        help="Import human-approved entries from a terminology review YAML file",
    )
    terminology_import.add_argument("project")
    terminology_import.add_argument("--file", required=True)
    terminology_import.add_argument("--language", choices=["zh-CN"], default="zh-CN")

    localize = sub.add_parser(
        "localize",
        help="Generate an audited simplified-Chinese Markdown manuscript",
    )
    localize.add_argument("project")
    localize.add_argument("--language", choices=["zh-CN"], default="zh-CN")

    localization_audit = sub.add_parser(
        "audit-localization",
        help="Audit the derived Chinese manuscript and terminology bindings",
    )
    localization_audit.add_argument("project")
    localization_audit.add_argument("--language", choices=["zh-CN"], default="zh-CN")

    complete = sub.add_parser("complete", help="Close the four-stage loop after synthesis audit")
    complete.add_argument("project")

    bundle = sub.add_parser(
        "bundle",
        help="Inspect a project folder, converge one research boundary, and close a four-stage paper loop",
    )
    bundle_sub = bundle.add_subparsers(dest="bundle_command", required=True)
    bundle_inspect = bundle_sub.add_parser(
        "inspect",
        help="Inventory a project folder and rank complete novelty/evidence tracks",
    )
    bundle_inspect.add_argument("source", help="Existing project folder to inspect read-only")
    bundle_close = bundle_sub.add_parser(
        "close-loop",
        help="Snapshot one selected track and generate four-stage evidence plus a working paper",
    )
    bundle_close.add_argument("source", help="Existing project folder to inspect read-only")
    bundle_close.add_argument(
        "--output-root",
        default="bundle_runs",
        help="Directory for immutable bundle run artifacts (default: ./bundle_runs)",
    )
    bundle_close.add_argument("--name")
    bundle_close.add_argument(
        "--track",
        default="auto",
        help="Exact protocol track ID, or auto for the highest-ranked complete chain",
    )
    bundle_audit = bundle_sub.add_parser(
        "audit",
        help="Re-audit a completed project-bundle loop and its completion certificate",
    )
    bundle_audit.add_argument("run", help="Completed bundle run directory")
    bundle_prepare_paper = bundle_sub.add_parser(
        "prepare-paper",
        help="Evaluate whether idea, evidence, and frozen-literature gates unlock full-paper writing",
    )
    bundle_prepare_paper.add_argument("run", help="Completed bundle run directory")
    bundle_expand_paper = bundle_sub.add_parser(
        "expand-paper",
        help="Run the evidence-bound full-paper writer after all prerequisite gates pass",
    )
    bundle_expand_paper.add_argument("run", help="Completed bundle run directory")
    bundle_audit_paper = bundle_sub.add_parser(
        "audit-paper",
        help="Verify the separately certified full-paper artifact chain",
    )
    bundle_audit_paper.add_argument("run", help="Completed bundle run directory")

    study = sub.add_parser("study", help="Freeze and run the preregistered claim-gate study")
    study_sub = study.add_subparsers(dest="study_command", required=True)
    study_freeze = study_sub.add_parser(
        "freeze", help="Freeze the novelty-02 two-arm Stage 2 protocol"
    )
    study_freeze.add_argument("project")
    study_freeze.add_argument("--docker-image", default="rf-airs-cpu:v1")
    study_freeze.add_argument(
        "--intent",
        choices=["pilot", "publication"],
        default="publication",
        help="Publication is the default and requires a locked venue contract plus every pre-experiment hard gate; pilot is an internal exception",
    )
    study_freeze.add_argument(
        "--pilot-reason",
        help="Required only for the exceptional pilot route; pilot artifacts are permanently non-publication-ready",
    )
    study_freeze.add_argument(
        "--publication-contract",
        help="Locked publication experiment contract; required by publication intent unless present at project root",
    )
    study_freeze.add_argument(
        "--task",
        dest="tasks",
        action="append",
        help="Frozen task ID; repeat for every task in a publication design",
    )
    study_freeze.add_argument(
        "--seed",
        dest="seeds",
        action="append",
        type=int,
        help="Frozen seed; repeat for every seed in a publication design",
    )
    study_freeze.add_argument(
        "--independent-calibration-contract",
        help="Project-local frozen calibration contract required by publication intent",
    )
    study_audit = study_sub.add_parser(
        "audit", help="Audit the frozen Stage 2 protocol and completed cells"
    )
    study_audit.add_argument("project")
    study_audit_publication = study_sub.add_parser(
        "audit-publication-design",
        help="Test an existing Stage 2 protocol against its locked strict-venue publication contract",
    )
    study_audit_publication.add_argument("project")
    study_audit_publication.add_argument("--publication-contract")
    study_supersede = study_sub.add_parser(
        "supersede", help="Archive an empty pre-data Stage 2 protocol before revision"
    )
    study_supersede.add_argument("project")
    study_supersede.add_argument("--reason", required=True)
    study_abandon = study_sub.add_parser(
        "abandon-preexecution",
        help="Archive a zero-metric protocol invalidated by the recognized Docker Unicode bind failure",
    )
    study_abandon.add_argument("project")
    study_abandon.add_argument("--reason", required=True)
    study_run_baseline = study_sub.add_parser(
        "run-baseline", help="Run or resume the nine no-gate baseline cells in fixed order"
    )
    study_run_baseline.add_argument("project")
    study_run_baseline.add_argument("--max-cells", type=int)
    study_audit_baseline = study_sub.add_parser(
        "audit-baseline", help="Audit the Stage 2 no-gate baseline matrix"
    )
    study_audit_baseline.add_argument("project")
    study_run_treatment = study_sub.add_parser(
        "run-treatment", help="Run or resume the nine gated treatment cells in fixed order"
    )
    study_run_treatment.add_argument("project")
    study_run_treatment.add_argument("--max-cells", type=int)
    study_audit_treatment = study_sub.add_parser(
        "audit-treatment", help="Audit the Stage 2 gated treatment matrix"
    )
    study_audit_treatment.add_argument("project")
    study_evaluate = study_sub.add_parser(
        "evaluate", help="Run or resume the protected arm-blinded evaluation of all 18 outputs"
    )
    study_evaluate.add_argument("project")
    study_audit_evaluation = study_sub.add_parser(
        "audit-evaluation", help="Audit the protected 18-registry evaluation"
    )
    study_audit_evaluation.add_argument("project")
    study_prepare_manual = study_sub.add_parser(
        "prepare-manual-audit",
        help="Verify and prepare the blinded two-auditor claim sample",
    )
    study_prepare_manual.add_argument("project")
    study_submit_manual = study_sub.add_parser(
        "submit-manual-audit",
        help="Validate and freeze two completed independent auditor packets",
    )
    study_submit_manual.add_argument("project")
    study_submit_manual.add_argument("--auditor-1", required=True)
    study_submit_manual.add_argument("--auditor-2", required=True)
    study_finalize_manual = study_sub.add_parser(
        "finalize-manual-audit",
        help="Apply blinded adjudication and evaluate the preregistered manual gate",
    )
    study_finalize_manual.add_argument("project")
    study_finalize_manual.add_argument("--adjudication")
    study_audit_manual = study_sub.add_parser(
        "audit-manual-audit", help="Audit manual-review preparation or completion"
    )
    study_audit_manual.add_argument("project")
    study_synthesize_provisional = study_sub.add_parser(
        "synthesize-provisional",
        help="Generate an evidence-bound provisional paper while human validation is deferred",
    )
    study_synthesize_provisional.add_argument("project")
    study_render_latex = study_sub.add_parser(
        "render-latex",
        help="Generate or refresh the audited LaTeX manuscript and completion binding",
    )
    study_render_latex.add_argument("project")
    study_audit_synthesis = study_sub.add_parser(
        "audit-synthesis",
        help="Audit the paired-study provisional analysis, claim registry, and manuscript",
    )
    study_audit_synthesis.add_argument("project")
    study_complete_provisional = study_sub.add_parser(
        "complete-provisional",
        help="Close the engineering loop without claiming publication readiness",
    )
    study_complete_provisional.add_argument("project")
    study_audit_completion = study_sub.add_parser(
        "audit-completion",
        help="Verify the provisional paired-study completion certificate and boundary",
    )
    study_audit_completion.add_argument("project")
    study_root_cause = study_sub.add_parser(
        "root-cause-preflight",
        help="Trace review symptoms to upstream design causes and enforce a claim ceiling",
    )
    study_root_cause.add_argument("project")
    study_root_cause.add_argument(
        "--target",
        choices=["execution", "developmental_review", "publication"],
        default="publication",
    )
    study_root_cause.add_argument(
        "--persist",
        action="store_true",
        help="Write JSON and Chinese Markdown reports under synthesis/",
    )
    study_rebranch_freeze = study_sub.add_parser(
        "freeze-rebranch",
        help="Freeze nine same-artifact four-arm branches and a local cross-family NLI evaluator",
    )
    study_rebranch_freeze.add_argument("project")
    study_rebranch_run = study_sub.add_parser(
        "run-rebranch",
        help="Run the frozen arm-blinded local NLI rebranch evaluation",
    )
    study_rebranch_run.add_argument("project")
    study_rebranch_audit = study_sub.add_parser(
        "audit-rebranch",
        help="Audit frozen rebranch inputs, model assets, and result hashes",
    )
    study_rebranch_audit.add_argument("project")

    benchmark = sub.add_parser("benchmark", help="Run and inspect RF-Bench evaluations")
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_command", required=True)
    benchmark_list = benchmark_sub.add_parser("list", help="List packaged benchmark tasks")
    benchmark_list.add_argument("--extra-root", help="Additional directory containing task packs")
    benchmark_doctor_parser = benchmark_sub.add_parser(
        "doctor", help="Check RF-Bench, Docker, and optional AIRS dataset runtime"
    )
    benchmark_doctor_parser.add_argument("--dataset-python")
    benchmark_doctor_parser.add_argument("--docker-image", default="rf-airs-cpu:v1")
    benchmark_build_env = benchmark_sub.add_parser(
        "build-env", help="Build and verify a controlled ML Docker environment"
    )
    benchmark_build_env.add_argument("--profile", choices=["airs-cpu"], default="airs-cpu")
    benchmark_build_env.add_argument("--docker-image", default="rf-airs-cpu:v1")
    benchmark_run = benchmark_sub.add_parser("run", help="Run one benchmark task")
    benchmark_run.add_argument("task", help="Built-in task ID or task-pack directory")
    benchmark_run.add_argument("--strategy", choices=["codex", "grid"], default="codex")
    benchmark_run.add_argument("--seed", type=int, action="append", dest="seeds")
    benchmark_run.add_argument("--seeds", dest="seeds_csv", help="Comma-separated seeds")
    benchmark_run.add_argument("--iterations", type=int)
    benchmark_run.add_argument("--output-root", help="Benchmark run output root")
    _add_runtime_arguments(benchmark_run, default_runtime="local")
    benchmark_loop = benchmark_sub.add_parser(
        "run-loop",
        help="Run the resumable deterministic automatic experiment controller",
    )
    benchmark_loop.add_argument("task", help="Built-in task ID or task-pack directory")
    benchmark_loop.add_argument("--strategy", choices=["codex", "grid"], default="codex")
    benchmark_loop.add_argument("--seed", type=int, action="append", dest="seeds")
    benchmark_loop.add_argument("--seeds", dest="seeds_csv", help="Comma-separated seeds")
    benchmark_loop.add_argument("--iterations", type=int)
    benchmark_loop.add_argument("--candidate-pool-size", type=int, default=3)
    benchmark_loop.add_argument("--proposal-attempts-per-iteration", type=int, default=6)
    benchmark_loop.add_argument("--patience", type=int, default=5)
    benchmark_loop.add_argument("--max-invalid-runs", type=int, default=3)
    benchmark_loop.add_argument(
        "--no-duplicate-detection",
        action="store_true",
        help="Ablation: execute repeated candidate target states instead of rejecting them",
    )
    benchmark_loop.add_argument(
        "--no-failure-diagnosis",
        action="store_true",
        help="Ablation: disable deterministic failure classification and repair priority",
    )
    benchmark_loop.add_argument("--output-root", help="Benchmark run output root")
    benchmark_loop.add_argument("--resume", help="Existing run-loop output directory")
    _add_runtime_arguments(
        benchmark_loop,
        default_runtime="docker",
        default_docker_image="rf-airs-cpu:v1",
    )
    benchmark_freeze_ablation = benchmark_sub.add_parser(
        "freeze-ablation",
        help="Freeze the three-task, four-variant controller ablation matrix",
    )
    benchmark_freeze_ablation.add_argument("--output-root")
    benchmark_freeze_ablation.add_argument("--seed", type=int, action="append", dest="seeds")
    benchmark_freeze_ablation.add_argument("--docker-image", default="rf-airs-cpu:v1")
    benchmark_run_ablation = benchmark_sub.add_parser(
        "run-ablation",
        help="Run or resume a frozen controller ablation matrix",
    )
    benchmark_run_ablation.add_argument("matrix", help="Frozen matrix output directory")
    benchmark_audit = benchmark_sub.add_parser("audit", help="Audit one benchmark project")
    benchmark_audit.add_argument("project", help="Path to a materialized benchmark project")
    benchmark_import = benchmark_sub.add_parser("import-airs", help="Import AIRS-Bench task metadata")
    benchmark_import.add_argument("source", help="AIRS task directory")
    benchmark_import.add_argument("--output-root", required=True, help="Destination task-pack root")
    benchmark_activate = benchmark_sub.add_parser(
        "activate-airs-lite",
        help="Prepare a CPU AIRS development task from its official dataset split",
    )
    benchmark_activate.add_argument("task_pack", help="Imported AIRS task-pack directory")
    benchmark_activate.add_argument(
        "--dataset-python",
        required=True,
        help="Python interpreter containing datasets==3.6.0",
    )
    benchmark_activate.add_argument("--cache-dir", required=True, help="Hugging Face data cache")

    web = sub.add_parser(
        "web",
        help="Start the local academic Research Forge user interface",
    )
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument(
        "--open",
        action="store_true",
        help="Open the interface in the default browser after startup",
    )

    sub.add_parser("doctor", help="Check local runtime prerequisites")
    return parser


def _project(value: str, home: str | None) -> Path:
    return project_dir(value, home)


def _resolve_venue_target(
    value: str,
    project_option: str | None,
    home: str | None,
) -> tuple[Path, Path | None]:
    candidate = Path(value).expanduser()
    if project_option:
        explicit = Path(project_option).expanduser()
        project = explicit.resolve() if explicit.is_dir() else _project(project_option, home)
        manuscript = candidate.resolve()
        if not manuscript.is_file():
            raise FileNotFoundError(f"manuscript not found: {manuscript}")
        return manuscript, project
    if candidate.is_file():
        return candidate.resolve(), None
    if candidate.is_dir():
        project = candidate.resolve()
    else:
        project = _project(value, home)
    manuscript_candidates = (
        project / "synthesis" / "manuscript.md",
        project / "synthesis" / "manuscript.tex",
    )
    manuscript = next((path for path in manuscript_candidates if path.is_file()), None)
    if manuscript is None:
        checked = ", ".join(str(path) for path in manuscript_candidates)
        raise FileNotFoundError(f"project has no synthesized English manuscript; checked: {checked}")
    return manuscript.resolve(), project


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _project_status_with_study_overlay(project: Path) -> dict[str, object]:
    status = project_status(project)
    closed_loop_path = project / "stage2" / "closed_loop_status.json"
    if not closed_loop_path.is_file():
        return status
    from .study_synthesis import audit_provisional_study_completion

    completion_audit = audit_provisional_study_completion(project)
    closed_loop = read_json(closed_loop_path)
    status["study_closed_loop"] = {
        **closed_loop,
        "verification": completion_audit,
    }
    if completion_audit.get("passed"):
        status["four_stage_gates"]["stage_3_experimentation"] = {
            "passed": True,
            "design": "paired_stage2_matrix",
            "completed_cells": closed_loop.get("study_evidence", {}).get(
                "completed_cells"
            ),
            "best_run_id": None,
            "note": "A paired matrix has no single promoted best run.",
        }
    return status


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            path = create_project(args.name, args.idea, slug=args.slug, root=args.home)
            print(f"Created: {path}")
            print(f"Next: python main.py --home \"{path.parent}\" plan {path.name} --message \"补充研究边界\"")
        elif args.command == "plan":
            draft_id, draft = asyncio.run(plan_project(_project(args.project, args.home), args.message))
            _print_json({"draft_id": draft_id, **draft.model_dump(mode="json")})
        elif args.command == "source":
            project = _project(args.project, args.home)
            if args.source_command == "add":
                registered = register_literature_source(
                    project,
                    source_id=args.id,
                    source_type=LiteratureSourceType(args.type),
                    title=args.title,
                    authors=args.author,
                    year=args.year,
                    locator=args.locator,
                    notes=args.notes,
                    verified=args.verified,
                    verification_method=args.verification,
                )
                _print_json(registered.model_dump(mode="json"))
            else:
                _print_json(
                    [source.model_dump(mode="json") for source in list_literature_sources(project)]
                )
        elif args.command == "literature":
            from .literature import (
                audit_stage1,
                approve_literature,
                discover_literature,
                plan_literature_search,
                screen_literature,
                synthesize_literature,
            )

            project = _project(args.project, args.home)
            if args.literature_command == "plan":
                plan_id, plan = asyncio.run(plan_literature_search(project, args.focus))
                _print_json({"search_plan_id": plan_id, **plan.model_dump(mode="json")})
            elif args.literature_command == "discover":
                discovery = discover_literature(
                    project,
                    search_plan_id=args.search_plan,
                    rows_per_query=args.rows_per_query,
                    include_count=args.include,
                    from_year=args.from_year,
                    min_relevance=args.min_relevance,
                )
                _print_json(
                    {
                        "discovery_id": discovery.discovery_id,
                        "search_plan_id": discovery.search_plan_id,
                        "provider_status": discovery.provider_status,
                        "candidate_count": len(discovery.candidates),
                        "included_count": len(discovery.included_source_ids),
                        "included": [
                            {
                                "candidate_id": candidate.candidate_id,
                                "title": candidate.title,
                                "year": candidate.year,
                                "doi": candidate.doi,
                                "relevance_score": candidate.relevance_score,
                                "verification_status": candidate.verification_status,
                                "matched_queries": candidate.matched_queries,
                            }
                            for candidate in discovery.candidates
                            if candidate.screening_status == "included"
                        ],
                    }
                )
            elif args.literature_command == "screen":
                screening = asyncio.run(
                    screen_literature(
                        project,
                        max_candidates=args.max_candidates,
                        include_count=args.include,
                    )
                )
                _print_json(screening.model_dump(mode="json"))
            elif args.literature_command == "synthesize":
                review, audit = asyncio.run(synthesize_literature(project))
                _print_json(
                    {
                        "review": review.model_dump(mode="json"),
                        "audit": audit.model_dump(mode="json"),
                    }
                )
            elif args.literature_command == "approve":
                approval, audit = approve_literature(
                    project,
                    confirmation=args.confirm,
                    selected_novelty_id=args.novelty,
                    note=args.note,
                )
                _print_json(
                    {
                        "approval": approval.model_dump(mode="json"),
                        "audit": audit.model_dump(mode="json"),
                    }
                )
            else:
                _print_json(audit_stage1(project).model_dump(mode="json"))
        elif args.command == "configure":
            contract = configure_project(
                _project(args.project, args.home),
                primary_metric=args.primary,
                direction=Direction(args.direction),
                entrypoint=args.entrypoint,
                timeout_seconds=args.timeout,
                max_runs=args.max_runs,
                required_repeats=args.required_repeats,
                max_repeats=args.max_repeats,
                min_delta=args.min_delta,
                required_metrics=args.required_metric,
                extra_args=args.extra_arg,
            )
            _print_json(contract.model_dump(mode="json"))
        elif args.command == "freeze":
            frozen_at = freeze_project(_project(args.project, args.home))
            print(f"Contracts frozen at {frozen_at}. Frozen files are now immutable.")
        elif args.command == "baseline":
            record = execute_run(
                _project(args.project, args.home),
                repeats=args.repeats,
                runtime=_runtime_options(args),
            )
            _print_json(record.model_dump(mode="json"))
        elif args.command == "propose":
            envelope = asyncio.run(propose_experiment(_project(args.project, args.home), args.focus))
            _print_json(envelope.model_dump(mode="json"))
        elif args.command == "run":
            project = _project(args.project, args.home)
            envelope = load_proposal(project, args.proposal)
            record = execute_run(
                project,
                envelope=envelope,
                repeats=args.repeats,
                runtime=_runtime_options(args),
            )
            _print_json(record.model_dump(mode="json"))
        elif args.command == "promote":
            promotion = promote_run(_project(args.project, args.home), args.run_id, args.confirm)
            _print_json(promotion.model_dump(mode="json"))
        elif args.command == "recover":
            record = recover_run(_project(args.project, args.home), args.confirm)
            _print_json(record.model_dump(mode="json"))
        elif args.command == "status":
            _print_json(
                _project_status_with_study_overlay(
                    _project(args.project, args.home)
                )
            )
        elif args.command == "report":
            output = render_report(_project(args.project, args.home))
            print(output)
        elif args.command == "synthesize":
            from .synthesis import synthesize_project

            audit = synthesize_project(_project(args.project, args.home))
            _print_json(audit.model_dump(mode="json"))
        elif args.command == "audit-synthesis":
            from .synthesis import audit_synthesis

            audit = audit_synthesis(_project(args.project, args.home))
            _print_json(audit.model_dump(mode="json"))
        elif args.command == "venue" and args.venue_command == "freeze-experiment-target":
            from .publication_readiness import freeze_publication_experiment_target

            target, output = freeze_publication_experiment_target(
                project=args.project,
                venue_id=args.venue,
                recommendation_report_path=args.recommendation_report,
                registry_path=args.registry,
                final_readiness_threshold=args.threshold,
                output_path=args.output,
                replace=args.replace,
            )
            _print_json(
                {
                    "contract": str(output),
                    "contract_id": target.contract_id,
                    "venue_id": target.venue_id,
                    "venue_quality_bar": target.venue_quality_bar,
                    "source_quality_score": target.recommendation_quality_score,
                    "minimum_tasks": target.minimum_tasks,
                    "minimum_seeds_per_task": target.minimum_seeds_per_task,
                    "target_locked": target.venue_target_locked,
                }
            )
        elif args.command == "venue" and args.venue_command in {"target", "readiness"}:
            from .publication_readiness import (
                CONTRACT_FILENAME,
                DESIGN_REPAIR_FILENAME,
                DESIGN_REPAIR_MARKDOWN_FILENAME,
                FAILURE_LEDGER_FILENAME,
                MARKDOWN_FILENAME,
                REPORT_FILENAME,
                audit_publication_readiness,
                build_system_design_repair_report,
                create_publication_target,
                load_publication_target,
                persist_publication_readiness,
                persist_system_design_repair,
            )

            project_path = Path(args.project).resolve()
            synthesis = project_path / "synthesis"
            contract_path = (
                Path(args.contract).resolve()
                if args.contract
                else synthesis / CONTRACT_FILENAME
            )
            report_path = (
                Path(args.report).resolve()
                if args.report
                else synthesis / REPORT_FILENAME
            )
            markdown_path = (
                Path(args.markdown).resolve()
                if args.markdown
                else synthesis / MARKDOWN_FILENAME
            )
            if args.venue_command == "target":
                target, contract_path = create_publication_target(
                    args.path,
                    project=project_path,
                    venue_id=args.venue,
                    readiness_threshold=args.threshold,
                    registry_path=args.registry,
                    contract_path=contract_path,
                    replace=args.replace,
                )
                readiness = audit_publication_readiness(
                    target,
                    manuscript=args.path,
                    project=project_path,
                    registry_path=args.registry,
                    history_path=args.history,
                    contract_path=contract_path,
                )
            else:
                target = load_publication_target(contract_path)
                readiness = audit_publication_readiness(
                    target,
                    manuscript=args.path,
                    project=project_path,
                    registry_path=args.registry,
                    history_path=args.history,
                    contract_path=contract_path,
                )
            json_output, markdown_output = persist_publication_readiness(
                readiness,
                json_path=report_path,
                markdown_path=markdown_path,
            )
            repair_outputs: dict[str, str] = {}
            if readiness.system_design_repair_required:
                design_root = project_path / "design_revisions"
                repair_json, repair_markdown = persist_system_design_repair(
                    build_system_design_repair_report(readiness),
                    json_path=design_root / DESIGN_REPAIR_FILENAME,
                    markdown_path=design_root / DESIGN_REPAIR_MARKDOWN_FILENAME,
                    ledger_path=design_root / FAILURE_LEDGER_FILENAME,
                )
                repair_outputs = {
                    "design_repair": str(repair_json),
                    "design_repair_markdown": str(repair_markdown),
                    "failure_ledger": str(design_root / FAILURE_LEDGER_FILENAME),
                    "failure_fingerprint": readiness.failure_fingerprint or "",
                }
            _print_json(
                {
                    "contract": str(contract_path),
                    "report": str(json_output),
                    "markdown": str(markdown_output),
                    "readiness_score": readiness.readiness_score,
                    "readiness_threshold": readiness.readiness_threshold,
                    "publication_submission_ready": readiness.publication_submission_ready,
                    "hard_blocker_count": len(readiness.hard_blockers),
                    "estimated_acceptance_probability": readiness.estimated_acceptance_probability.model_dump(mode="json"),
                    "projected_readiness_after_plan": readiness.projected_readiness_after_plan,
                    "semantics": "readiness is not acceptance probability",
                    **repair_outputs,
                }
            )
            if args.venue_command == "readiness" and not args.no_fail and not readiness.publication_submission_ready:
                return 2
        elif args.command == "journal" or (
            args.command == "venue" and args.venue_command == "recommend"
        ):
            from .journal_recommendation import (
                persist_journal_recommendation,
                recommend_journals,
                recommend_venues,
            )

            manuscript_path, project_path = _resolve_venue_target(
                args.path,
                args.project,
                args.home,
            )
            output_root = (
                project_path / "synthesis"
                if project_path is not None
                else manuscript_path.parent
            )
            stem = "venue_recommendation" if args.command == "venue" else "journal_recommendation"
            report_path = Path(args.report).resolve() if args.report else output_root / f"{stem}.json"
            markdown_path = Path(args.markdown).resolve() if args.markdown else output_root / f"{stem}.md"
            overrides = read_json(Path(args.assessment).resolve()) if args.assessment else None
            if args.command == "venue":
                venue_types = (
                    {"journal", "conference"}
                    if args.type == "all"
                    else {args.type}
                )
                recommendation = recommend_venues(
                    manuscript_path,
                    project=project_path,
                    registry_path=args.registry,
                    history_path=args.history,
                    overrides=overrides,
                    top=args.top,
                    venue_types=venue_types,
                    live_evidence=args.live_evidence,
                    evidence_cache_path=args.evidence_cache,
                    openreview_export=args.openreview_export,
                    local_evidence=args.evidence_file,
                    evidence_from_year=args.evidence_from_year,
                    evidence_results=args.evidence_results,
                    ccf_deadlines_path=args.ccf_deadlines,
                )
            else:
                recommendation = recommend_journals(
                    manuscript_path,
                    project=project_path,
                    registry_path=args.registry,
                    history_path=args.history,
                    overrides=overrides,
                    top=args.top,
                    live_evidence=args.live_evidence,
                    evidence_cache_path=args.evidence_cache,
                    openreview_export=args.openreview_export,
                    local_evidence=args.evidence_file,
                    evidence_from_year=args.evidence_from_year,
                    evidence_results=args.evidence_results,
                    ccf_deadlines_path=args.ccf_deadlines,
                )
            json_output, markdown_output = persist_journal_recommendation(
                recommendation,
                json_path=report_path,
                markdown_path=markdown_path,
            )
            _print_json(
                {
                    "report": str(json_output),
                    "markdown": str(markdown_output),
                    "calibration_status": recommendation.calibration_status,
                    "publication_ready": recommendation.assessment.publication_ready,
                    "recommendations": [
                        {
                            "rank": item.rank,
                            "venue": item.journal_name,
                            "venue_type": item.venue_type,
                            "track": item.track,
                            "cycle_status": item.cycle_status,
                            "current_cycle_eligible": item.current_cycle_eligible,
                            "recommendation_band": item.recommendation_band,
                            "venue_fit_score": item.venue_fit_score,
                            "scope_fit": item.scope_fit,
                            "semantic_fit": item.semantic_fit,
                            "evidence_count": item.evidence_count,
                            "rankings": item.rankings,
                            "scientific_success_if_validly_submitted": item.combined_submission_success.model_dump(mode="json"),
                            "current_cycle_success": item.current_cycle_submission_success.model_dump(mode="json"),
                            "after_known_blockers_resolved": item.after_known_blockers_resolved.model_dump(mode="json"),
                            "routing_label": item.routing_label,
                        }
                        for item in recommendation.recommendations
                    ],
                    "evidence": {
                        "provider_status": recommendation.evidence.provider_status,
                        "similar_papers": len(recommendation.evidence.papers),
                        "cache_hit": recommendation.evidence.cache_hit,
                        "sent_fields": recommendation.evidence.sent_fields,
                    },
                    "discovered_candidates": [
                        {
                            "venue": item.venue_name,
                            "venue_type": item.venue_type,
                            "evidence_count": item.evidence_count,
                            "mean_relevance": item.mean_relevance,
                            "status": item.status,
                        }
                        for item in recommendation.discovered_candidates
                    ],
                }
            )
        elif args.command == "manuscript":
            if args.manuscript_command == "audit-depth":
                from .manuscript_depth import audit_manuscript_depth

                audit = audit_manuscript_depth(
                    args.path,
                    profile=args.profile,
                    language=args.language,
                    report_path=args.report,
                )
                _print_json(audit.as_dict())
                if not audit.passed:
                    return 2
            elif args.manuscript_command == "finalize-pdf":
                from .manuscript_compile import finalize_manuscript_pdf

                _print_json(
                    finalize_manuscript_pdf(
                        args.path,
                        output_dir=args.output_dir,
                        profile=args.profile,
                        language=args.language,
                        engine=args.engine,
                        passes=args.passes,
                        report_path=args.report,
                        manifest_path=args.manifest,
                    )
                )
        elif args.command == "terminology":
            from .terminology import import_terminology_review, prepare_terminology

            project = _project(args.project, args.home)
            if args.terminology_command == "prepare":
                plan = asyncio.run(
                    prepare_terminology(project, language=args.language)
                )
                _print_json(plan.model_dump(mode="json"))
            else:
                result = import_terminology_review(
                    project,
                    Path(args.file),
                    language=args.language,
                )
                _print_json(result)
        elif args.command == "localize":
            from .localization import localize_project

            audit = asyncio.run(
                localize_project(
                    _project(args.project, args.home),
                    language=args.language,
                )
            )
            _print_json(audit.model_dump(mode="json"))
        elif args.command == "audit-localization":
            from .localization import audit_localization

            audit = audit_localization(
                _project(args.project, args.home),
                language=args.language,
            )
            _print_json(audit.model_dump(mode="json"))
        elif args.command == "complete":
            from .synthesis import complete_project

            certificate = complete_project(_project(args.project, args.home))
            _print_json(certificate.model_dump(mode="json"))
        elif args.command == "bundle":
            from .project_bundle import (
                close_project_bundle_loop,
                inspect_project_bundle,
                verify_project_bundle_completion,
            )

            if args.bundle_command == "inspect":
                inspection = inspect_project_bundle(args.source)
                _print_json(inspection.model_dump(mode="json"))
            elif args.bundle_command == "close-loop":
                run_dir = close_project_bundle_loop(
                    args.source,
                    output_root=args.output_root,
                    name=args.name,
                    track_id=args.track,
                )
                verification = verify_project_bundle_completion(run_dir)
                _print_json({"run_dir": str(run_dir), "verification": verification})
            elif args.bundle_command == "audit":
                _print_json(verify_project_bundle_completion(args.run))
            elif args.bundle_command == "prepare-paper":
                from .paper_expansion import prepare_project_bundle_paper

                plan = prepare_project_bundle_paper(args.run, persist=True)
                _print_json(plan.model_dump(mode="json"))
            elif args.bundle_command == "expand-paper":
                from .paper_expansion import expand_project_bundle_paper

                audit = asyncio.run(expand_project_bundle_paper(args.run))
                _print_json(audit.model_dump(mode="json"))
            else:
                from .paper_expansion import verify_project_bundle_paper

                _print_json(verify_project_bundle_paper(args.run))
        elif args.command == "study":
            from .study import (
                audit_stage2_publication_design,
                audit_stage2_protocol,
                abandon_preexecution_stage2_protocol,
                freeze_stage2_protocol,
                supersede_empty_stage2_protocol,
            )

            project = _project(args.project, args.home)
            if args.study_command == "freeze":
                protocol = freeze_stage2_protocol(
                    project,
                    docker_image=args.docker_image,
                    intent=args.intent,
                    publication_contract=args.publication_contract,
                    pilot_reason=args.pilot_reason,
                    task_ids=args.tasks,
                    seeds=args.seeds,
                    independent_calibration_contract=args.independent_calibration_contract,
                )
                _print_json(protocol.model_dump(mode="json"))
            elif args.study_command == "audit":
                audit = audit_stage2_protocol(project, persist=True)
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "audit-publication-design":
                audit = audit_stage2_publication_design(
                    project,
                    contract_path=args.publication_contract,
                    persist=True,
                )
                _print_json(audit.model_dump(mode="json"))
                if not audit.pre_experiment_gate_passed:
                    return 2
            elif args.study_command == "supersede":
                archived = supersede_empty_stage2_protocol(project, reason=args.reason)
                _print_json({"archived": str(archived)})
            elif args.study_command == "abandon-preexecution":
                archived = abandon_preexecution_stage2_protocol(
                    project,
                    reason=args.reason,
                )
                _print_json({"archived": str(archived)})
            elif args.study_command == "run-baseline":
                from .study_runner import run_stage2_baseline

                audit = asyncio.run(
                    run_stage2_baseline(project, max_cells=args.max_cells)
                )
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "audit-baseline":
                from .study_runner import audit_stage2_baseline

                audit = audit_stage2_baseline(project, persist=True)
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "run-treatment":
                from .study_runner import run_stage2_treatment

                audit = asyncio.run(
                    run_stage2_treatment(project, max_cells=args.max_cells)
                )
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "audit-treatment":
                from .study_runner import audit_stage2_treatment

                audit = audit_stage2_treatment(project, persist=True)
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "evaluate":
                from .study_runner import run_stage2_evaluation

                audit = asyncio.run(run_stage2_evaluation(project))
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "audit-evaluation":
                from .study_runner import audit_stage2_evaluation

                audit = audit_stage2_evaluation(project, persist=True)
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "prepare-manual-audit":
                from .manual_audit import prepare_manual_audit

                _print_json(prepare_manual_audit(project))
            elif args.study_command == "submit-manual-audit":
                from .manual_audit import submit_manual_audits

                _print_json(
                    submit_manual_audits(
                        project,
                        Path(args.auditor_1),
                        Path(args.auditor_2),
                    )
                )
            elif args.study_command == "finalize-manual-audit":
                from .manual_audit import finalize_manual_audit

                _print_json(
                    finalize_manual_audit(
                        project,
                        Path(args.adjudication) if args.adjudication else None,
                    )
                )
            elif args.study_command == "audit-manual-audit":
                from .manual_audit import audit_manual_audit

                _print_json(audit_manual_audit(project))
            elif args.study_command == "synthesize-provisional":
                from .study_synthesis import synthesize_provisional_study

                audit = synthesize_provisional_study(project)
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "render-latex":
                from .study_synthesis import render_provisional_study_latex

                _print_json(render_provisional_study_latex(project))
            elif args.study_command == "audit-synthesis":
                from .study_synthesis import audit_provisional_study_synthesis

                audit = audit_provisional_study_synthesis(project)
                _print_json(audit.model_dump(mode="json"))
            elif args.study_command == "complete-provisional":
                from .study_synthesis import complete_provisional_study

                _print_json(complete_provisional_study(project))
            elif args.study_command == "audit-completion":
                from .study_synthesis import audit_provisional_study_completion

                _print_json(audit_provisional_study_completion(project))
            elif args.study_command == "root-cause-preflight":
                from .root_cause_preflight import (
                    ReviewTarget,
                    analyze_project_root_causes,
                )

                report = analyze_project_root_causes(
                    project,
                    target=ReviewTarget(args.target),
                    persist=args.persist,
                )
                _print_json(report.model_dump(mode="json"))
                if not report.gate_passed:
                    return 2
            elif args.study_command == "freeze-rebranch":
                from .counterfactual_rebranch import freeze_counterfactual_rebranch

                _print_json(freeze_counterfactual_rebranch(project))
            elif args.study_command == "run-rebranch":
                from .counterfactual_rebranch import run_counterfactual_rebranch

                _print_json(run_counterfactual_rebranch(project))
            elif args.study_command == "audit-rebranch":
                from .counterfactual_rebranch import audit_counterfactual_rebranch

                audit = audit_counterfactual_rebranch(project, persist=True)
                _print_json(audit)
                if not audit["passed"]:
                    return 2
        elif args.command == "benchmark":
            from .benchmark import (
                activate_airs_lite,
                audit_project,
                benchmark_doctor,
                import_airs_task,
                list_tasks,
                run_benchmark,
            )

            if args.benchmark_command == "list":
                _print_json(list_tasks(args.extra_root))
            elif args.benchmark_command == "doctor":
                _print_json(
                    benchmark_doctor(
                        args.dataset_python,
                        docker_image=args.docker_image,
                    )
                )
            elif args.benchmark_command == "build-env":
                from .controlled_env import build_controlled_environment

                _print_json(
                    build_controlled_environment(
                        args.profile,
                        image=args.docker_image,
                    )
                )
            elif args.benchmark_command == "run":
                output, benchmark_report = asyncio.run(
                    run_benchmark(
                        args.task,
                        strategy=args.strategy,
                        seeds=_selected_seeds(args),
                        iterations=args.iterations,
                        output_root=args.output_root,
                        runtime=_runtime_options(args),
                    )
                )
                _print_json(
                    {
                        "output": str(output),
                        **benchmark_report.model_dump(mode="json"),
                    }
                )
            elif args.benchmark_command == "run-loop":
                from .research_loop import run_loop_benchmark

                output, benchmark_report, loop_states = asyncio.run(
                    run_loop_benchmark(
                        args.task,
                        strategy=args.strategy,
                        seeds=_selected_seeds(args),
                        iterations=args.iterations,
                        output_root=args.output_root,
                        runtime=_runtime_options(args),
                        candidate_pool_size=args.candidate_pool_size,
                        proposal_attempts_per_iteration=args.proposal_attempts_per_iteration,
                        patience=args.patience,
                        max_invalid_runs=args.max_invalid_runs,
                        deduplicate_candidates=not args.no_duplicate_detection,
                        failure_diagnosis=not args.no_failure_diagnosis,
                        resume=args.resume,
                    )
                )
                _print_json(
                    {
                        "output": str(output),
                        **benchmark_report.model_dump(mode="json"),
                        "loops": [item.model_dump(mode="json") for item in loop_states],
                    }
                )
            elif args.benchmark_command == "freeze-ablation":
                from .controller_ablation import freeze_controller_ablation

                output = freeze_controller_ablation(
                    output_root=args.output_root,
                    docker_image=args.docker_image,
                    seeds=args.seeds,
                )
                _print_json(
                    {
                        "output": str(output),
                        "matrix": read_json(output / "matrix.json"),
                    }
                )
            elif args.benchmark_command == "run-ablation":
                from .controller_ablation import run_controller_ablation

                results = asyncio.run(run_controller_ablation(args.matrix))
                _print_json(
                    {
                        "output": str(Path(args.matrix).resolve()),
                        **results,
                    }
                )
            elif args.benchmark_command == "audit":
                _print_json(audit_project(Path(args.project)).model_dump(mode="json"))
            elif args.benchmark_command == "import-airs":
                output = import_airs_task(args.source, args.output_root)
                _print_json({"imported": str(output)})
            elif args.benchmark_command == "activate-airs-lite":
                activated = activate_airs_lite(
                    args.task_pack,
                    dataset_python=args.dataset_python,
                    cache_dir=args.cache_dir,
                )
                _print_json(activated.model_dump(mode="json"))
        elif args.command == "web":
            from .web_app import run_web_app

            run_web_app(args.host, args.port, open_browser=args.open)
        elif args.command == "doctor":
            from .agent_runtime import backend_status

            status = {
                "python": sys.version.split()[0],
                "python_executable": sys.executable,
                **backend_status(),
            }
            if status["backend"] == "api":
                status["openai_api_key_present"] = key_is_present()
            _print_json(status)
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
