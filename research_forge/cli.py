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
    parser.add_argument(
        "--runtime", choices=["local", "docker"], default=default_runtime
    )
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
            values.extend(
                int(item.strip()) for item in comma_seeds.split(",") if item.strip()
            )
        except ValueError as exc:
            raise ValueError(
                "--seeds must be a comma-separated list of integers"
            ) from exc
    if not values:
        return None
    if len(values) != len(set(values)):
        raise ValueError("seeds must be unique")
    return values


def _task_payload(value: str) -> dict[str, object]:
    if value.startswith("@"):
        payload = json.loads(
            Path(value[1:]).expanduser().resolve().read_text(encoding="utf-8")
        )
    else:
        payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("task payload must be a JSON object")
    return payload


def _add_retrieval_policy_set_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("project_id")
    parser.add_argument(
        "--mode",
        choices=[
            "offline",
            "public_research",
            "public_research_plus_institution",
            "academic_read",
            "public_web_read",
            "authenticated_read",
            "external_write",
        ],
        required=True,
    )
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--method", action="append", default=["GET"])
    parser.add_argument("--resource-type", action="append", default=[])
    parser.add_argument("--max-queries", type=int, default=20)
    parser.add_argument("--max-results", type=int, default=200)
    parser.add_argument("--max-bytes", type=int, default=5_000_000)
    parser.add_argument("--max-cost", type=float, default=1.0)
    parser.add_argument("--allow-full-text", action="store_true")
    parser.add_argument(
        "--allow-proxy-fake-ip",
        action="store_true",
        help=(
            "Owner-approved support for local proxy 198.18/15 fake-IP DNS; "
            "literal and local targets remain blocked"
        ),
    )
    parser.add_argument("--approved-by")


def _add_retrieval_corpus_build_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("--study-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--document-json", action="append", required=True)
    parser.add_argument("--parser-version", required=True)
    parser.add_argument("--embedding-model-hash", required=True)
    parser.add_argument("--llm-config-hash", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-forge",
        description="Local-first AI research loop with deterministic scientific gates.",
    )
    parser.add_argument("--home", help="Project workspace root (default: ./workspaces)")
    parser.add_argument(
        "--task-root",
        default=".rfab/orchestration",
        help="Durable task records shared by orchestration-aware CLI commands",
    )
    parser.add_argument(
        "--workflow-root",
        default=".rfab/workflow-v2",
        help="Versioned Project/Study workflow repository",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a research project")
    init.add_argument("--name", required=True)
    init.add_argument("--idea", required=True)
    init.add_argument("--slug")

    pipeline = sub.add_parser(
        "pipeline",
        help="Initialize or inspect the domain-neutral pipeline contract for any project",
    )
    pipeline_sub = pipeline.add_subparsers(dest="pipeline_command", required=True)
    pipeline_initialize = pipeline_sub.add_parser(
        "initialize",
        help="Write a stage/skill governance manifest from a project specification",
    )
    pipeline_initialize.add_argument("project", help="Target project directory")
    pipeline_initialize.add_argument(
        "--spec", required=True, help="Path to ProjectSpec JSON"
    )
    pipeline_inspect = pipeline_sub.add_parser(
        "inspect", help="Print a pipeline governance manifest"
    )
    pipeline_inspect.add_argument("project", help="Target project directory")

    task = sub.add_parser(
        "task", help="Submit, inspect, run, or resume a durable workflow operation"
    )
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_submit = task_sub.add_parser(
        "submit", help="Persist a task before any side effect"
    )
    task_submit.add_argument("--operation", required=True)
    task_submit.add_argument(
        "--payload", default="{}", help="JSON object or @path/to/payload.json"
    )
    task_submit.add_argument("--idempotency-key")
    task_submit.add_argument("--run", action="store_true")
    task_inspect = task_sub.add_parser("inspect", help="Inspect one durable task")
    task_inspect.add_argument("task_id")
    task_resume = task_sub.add_parser(
        "resume", help="Explicitly resume a safe operation"
    )
    task_resume.add_argument("task_id")
    task_cancel = task_sub.add_parser(
        "cancel", help="Cancel a durable task and retain its record"
    )
    task_cancel.add_argument("task_id")
    task_list = task_sub.add_parser("list", help="List durable workflow tasks")
    task_list.add_argument(
        "--status",
        choices=[
            "pending",
            "running",
            "retrying",
            "paused",
            "waiting_for_user",
            "succeeded",
            "failed",
            "blocked",
            "cancelled",
        ],
    )

    workflow = sub.add_parser(
        "workflow",
        help="Manage versioned Projects, Studies, DAG state, and completion records",
    )
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_sub.add_parser("list-projects")
    workflow_studies = workflow_sub.add_parser("list-studies")
    workflow_studies.add_argument("--project-id")
    workflow_inspect = workflow_sub.add_parser("inspect-study")
    workflow_inspect.add_argument("study_id")
    workflow_run = workflow_sub.add_parser("run-study")
    workflow_run.add_argument("study_id")
    workflow_pause = workflow_sub.add_parser("pause-study")
    workflow_pause.add_argument("study_id")
    workflow_resume = workflow_sub.add_parser("resume-study")
    workflow_resume.add_argument("study_id")
    workflow_retry = workflow_sub.add_parser("retry-step")
    workflow_retry.add_argument("study_id")
    workflow_retry.add_argument("step_id")
    workflow_migrate = workflow_sub.add_parser("migrate-run")
    workflow_migrate.add_argument("run_dir")
    workflow_verify = workflow_sub.add_parser("verify-completion")
    workflow_verify.add_argument("record")
    workflow_verify.add_argument("--artifact-root")

    retrieval = sub.add_parser(
        "retrieval",
        help="Manage the policy-controlled Forge Retrieval Gateway",
    )
    retrieval_sub = retrieval.add_subparsers(dest="retrieval_command", required=True)
    retrieval_policy_show = retrieval_sub.add_parser("policy-show")
    retrieval_policy_show.add_argument("project_id")
    retrieval_policy_set = retrieval_sub.add_parser("policy-set")
    _add_retrieval_policy_set_arguments(retrieval_policy_set)
    retrieval_policy = retrieval_sub.add_parser(
        "policy",
        help="Show or set project retrieval policy",
    )
    retrieval_policy_sub = retrieval_policy.add_subparsers(
        dest="retrieval_policy_command",
        required=True,
    )
    retrieval_policy_nested_show = retrieval_policy_sub.add_parser("show")
    retrieval_policy_nested_show.add_argument("project_id")
    retrieval_policy_nested_set = retrieval_policy_sub.add_parser("set")
    _add_retrieval_policy_set_arguments(retrieval_policy_nested_set)
    retrieval_plan = retrieval_sub.add_parser("plan")
    retrieval_plan.add_argument("--project-id", required=True)
    retrieval_plan.add_argument("--study-id", required=True)
    retrieval_plan.add_argument(
        "--phase",
        choices=["discovery", "protocol", "experimentation", "synthesis", "repair"],
        required=True,
    )
    retrieval_plan.add_argument("--step-id", required=True)
    retrieval_plan.add_argument("--purpose", required=True)
    retrieval_plan.add_argument("--query", action="append", required=True)
    retrieval_plan.add_argument("--provider", action="append", required=True)
    retrieval_plan.add_argument("--resource-type", action="append", required=True)
    retrieval_plan.add_argument("--usage-role", required=True)
    retrieval_plan.add_argument("--idempotency-key", required=True)
    retrieval_plan.add_argument(
        "--freshness", choices=["cache_only", "live"], default="cache_only"
    )
    retrieval_plan.add_argument("--allow-domain", action="append", default=[])
    retrieval_plan.add_argument("--block-domain", action="append", default=[])
    retrieval_readiness = retrieval_sub.add_parser("readiness")
    retrieval_readiness.add_argument("--refresh", action="store_true")
    retrieval_workflow_run = retrieval_sub.add_parser("workflow-run")
    retrieval_workflow_run.add_argument("--study-id", required=True)
    retrieval_workflow_run.add_argument(
        "--stage",
        choices=[
            "all",
            "discovery",
            "protocol",
            "experimentation",
            "synthesis",
            "repair",
        ],
        required=True,
    )
    retrieval_workflow_run.add_argument("--run-key", default="v1")
    retrieval_workflow_run.add_argument("--depends-on", action="append", default=[])
    retrieval_workflow_run.add_argument(
        "--include-repair",
        action="store_true",
        help="Append the conditional repair branch after synthesis",
    )
    retrieval_workflow_run.add_argument("--plan-only", action="store_true")
    retrieval_run = retrieval_sub.add_parser("run")
    retrieval_run.add_argument("request_id")
    retrieval_status = retrieval_sub.add_parser("status")
    retrieval_status.add_argument("run_id")
    retrieval_resources = retrieval_sub.add_parser("resources")
    retrieval_resources.add_argument("--study-id")
    retrieval_coverage = retrieval_sub.add_parser("coverage")
    retrieval_coverage.add_argument("coverage_report_id")
    retrieval_freeze = retrieval_sub.add_parser("freeze")
    retrieval_freeze.add_argument("resource_set_id")
    retrieval_promote = retrieval_sub.add_parser("promote")
    retrieval_promote.add_argument("binding_id")
    retrieval_promote.add_argument("--phase", required=True)
    retrieval_promote.add_argument("--step-id", required=True)
    retrieval_promote.add_argument("--purpose", required=True)
    retrieval_promote.add_argument("--usage-role", required=True)
    retrieval_promote.add_argument("--target-type", required=True)
    retrieval_promote.add_argument("--target-id", required=True)
    retrieval_promote.add_argument("--target-field", required=True)
    retrieval_retry = retrieval_sub.add_parser("retry")
    retrieval_retry.add_argument("run_id")
    retrieval_search = retrieval_sub.add_parser("search")
    retrieval_search.add_argument("--project-id", required=True)
    retrieval_search.add_argument("--study-id", required=True)
    retrieval_search.add_argument("--phase", required=True)
    retrieval_search.add_argument("--step-id", required=True)
    retrieval_search.add_argument("--purpose", required=True)
    retrieval_search.add_argument("--query", action="append", required=True)
    retrieval_search.add_argument("--provider", action="append", required=True)
    retrieval_search.add_argument("--resource-type", action="append", required=True)
    retrieval_search.add_argument("--usage-role", required=True)
    retrieval_search.add_argument("--idempotency-key", required=True)
    retrieval_search.add_argument(
        "--freshness", choices=["cache_only", "live"], default="cache_only"
    )
    retrieval_search.add_argument("--allow-domain", action="append", default=[])
    retrieval_search.add_argument("--block-domain", action="append", default=[])
    institution_connect = retrieval_sub.add_parser("connect-institution")
    institution_connect.add_argument("--owner-user-id", required=True)
    institution_connect.add_argument("--project-id", required=True)
    institution_connect.add_argument("--study-id", required=True)
    institution_connect.add_argument("--institution-id", required=True)
    institution_connect.add_argument("--target-url", required=True)
    institution_status = retrieval_sub.add_parser("institution-status")
    institution_status.add_argument("session_id")
    institution_status.add_argument("--owner-user-id", required=True)
    institution_revoke = retrieval_sub.add_parser("revoke-institution")
    institution_revoke.add_argument("session_id")
    institution_revoke.add_argument("--owner-user-id", required=True)
    corpus_build = retrieval_sub.add_parser("corpus-build")
    _add_retrieval_corpus_build_arguments(corpus_build)
    corpus_query = retrieval_sub.add_parser("corpus-query")
    corpus_query.add_argument("corpus_id")
    corpus_query_input = corpus_query.add_mutually_exclusive_group(required=True)
    corpus_query_input.add_argument("--question")
    corpus_query_input.add_argument("--questions-file", type=Path)
    retrieval_corpus = retrieval_sub.add_parser(
        "corpus",
        help="Build or query a rights-approved PaperQA corpus",
    )
    retrieval_corpus_sub = retrieval_corpus.add_subparsers(
        dest="retrieval_corpus_command",
        required=True,
    )
    retrieval_corpus_nested_build = retrieval_corpus_sub.add_parser("build")
    _add_retrieval_corpus_build_arguments(retrieval_corpus_nested_build)
    retrieval_corpus_nested_query = retrieval_corpus_sub.add_parser("query")
    retrieval_corpus_nested_query.add_argument("corpus_id")
    nested_query_input = retrieval_corpus_nested_query.add_mutually_exclusive_group(
        required=True
    )
    nested_query_input.add_argument("--question")
    nested_query_input.add_argument("--questions-file", type=Path)

    plan = sub.add_parser(
        "plan", help="Draft or revise the research contract with the agent"
    )
    plan.add_argument("project")
    plan.add_argument("--message", required=True)

    source = sub.add_parser("source", help="Register and inspect research sources")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_add = source_sub.add_parser(
        "add", help="Register one source before freezing"
    )
    source_add.add_argument("project")
    source_add.add_argument("--id", required=True)
    source_add.add_argument(
        "--type", choices=[item.value for item in LiteratureSourceType], required=True
    )
    source_add.add_argument("--title", required=True)
    source_add.add_argument("--author", action="append", required=True)
    source_add.add_argument("--year", type=int)
    source_add.add_argument(
        "--locator", required=True, help="DOI, URL, or local canonical path"
    )
    source_add.add_argument("--notes", default="")
    source_add.add_argument("--verification", required=True)
    source_add.add_argument(
        "--verified",
        action="store_true",
        help="Explicitly attest that the metadata and locator were checked",
    )
    source_list = source_sub.add_parser("list", help="List registered sources")
    source_list.add_argument("project")

    literature = sub.add_parser(
        "literature", help="Run the evidence-bound Stage 1 workflow"
    )
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
        "screen",
        help="Use Codex for bounded relevance judgments, then apply fixed quotas",
    )
    literature_screen.add_argument("project")
    literature_screen.add_argument("--max-candidates", type=int, default=40)
    literature_screen.add_argument("--include", type=int, default=12)
    literature_synthesize = literature_sub.add_parser(
        "synthesize", help="Create a source-bound related-work and novelty map"
    )
    literature_synthesize.add_argument("project")
    literature_approve = literature_sub.add_parser(
        "approve",
        help="Approve the reviewed shortlist and novelty map by exact review ID",
    )
    literature_approve.add_argument("project")
    literature_approve.add_argument("--confirm", required=True)
    literature_approve.add_argument("--novelty", required=True)
    literature_approve.add_argument("--note", default="")
    literature_audit = literature_sub.add_parser(
        "audit", help="Audit every Stage 1 artifact and gate"
    )
    literature_audit.add_argument("project")

    configure = sub.add_parser(
        "configure", help="Configure deterministic execution and metrics"
    )
    configure.add_argument("project")
    configure.add_argument("--primary", required=True)
    configure.add_argument(
        "--direction", choices=[item.value for item in Direction], required=True
    )
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
    propose.add_argument(
        "--focus", default="Choose the highest-information next experiment."
    )

    run = sub.add_parser("run", help="Execute a validated experiment proposal")
    run.add_argument("project")
    run.add_argument("--proposal", default="latest")
    run.add_argument("--repeats", type=int)
    _add_runtime_arguments(run, default_runtime="local")

    promote = sub.add_parser(
        "promote", help="Promote an improving run after explicit review"
    )
    promote.add_argument("project")
    promote.add_argument("run_id")
    promote.add_argument("--confirm", required=True)

    recover = sub.add_parser(
        "recover", help="Mark an interrupted active run complete or abandoned"
    )
    recover.add_argument("project")
    recover.add_argument("--confirm", required=True, help="Exact active run ID")

    status = sub.add_parser("status", help="Show project state and evidence counts")
    status.add_argument("project")

    report = sub.add_parser(
        "report", help="Render an evidence-grounded Markdown report"
    )
    report.add_argument("project")

    synthesize = sub.add_parser(
        "synthesize", help="Generate a claim-linked manuscript and deterministic audit"
    )
    synthesize.add_argument("project")

    synthesis_audit = sub.add_parser(
        "audit-synthesis", help="Re-audit synthesis artifacts"
    )
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
    venue_recommend.add_argument(
        "--registry", help="Optional strict venue-registry JSON"
    )
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
    venue_recommend.add_argument(
        "--top", type=int, default=8, help="Maximum per venue type"
    )
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
    venue_target.add_argument(
        "--history", help="Optional local same-venue submission history"
    )
    venue_target.add_argument("--contract", help="Output target-contract JSON path")
    venue_target.add_argument("--report", help="Output readiness-report JSON path")
    venue_target.add_argument(
        "--markdown", help="Output readiness-report Markdown path"
    )
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
    venue_readiness.add_argument(
        "--path", help="Updated manuscript; defaults to the contract baseline path"
    )
    venue_readiness.add_argument("--contract", help="Frozen target-contract JSON path")
    venue_readiness.add_argument(
        "--registry", help="Optional strict venue-registry JSON"
    )
    venue_readiness.add_argument(
        "--history", help="Optional local same-venue submission history"
    )
    venue_readiness.add_argument("--report", help="Output readiness-report JSON path")
    venue_readiness.add_argument(
        "--markdown", help="Output readiness-report Markdown path"
    )
    venue_readiness.add_argument(
        "--no-fail",
        action="store_true",
        help="Write the audit without returning exit code 2 when blocked",
    )
    venue_experiment_target = venue_sub.add_parser(
        "freeze-experiment-target",
        help="Freeze one strict venue recommendation as the pre-experiment publication contract",
    )
    venue_experiment_target.add_argument(
        "project", help="Research Forge project directory"
    )
    venue_experiment_target.add_argument(
        "--venue", required=True, help="Exact strict-registry venue id"
    )
    venue_experiment_target.add_argument(
        "--recommendation-report",
        help="Venue recommendation JSON; defaults to project/synthesis/venue_recommendation.json",
    )
    venue_experiment_target.add_argument(
        "--registry", help="Optional strict venue-registry JSON"
    )
    venue_experiment_target.add_argument("--threshold", type=float, default=0.60)
    venue_experiment_target.add_argument(
        "--output", help="Output publication experiment contract"
    )
    venue_experiment_target.add_argument("--replace", action="store_true")

    manuscript = sub.add_parser(
        "manuscript",
        help="Run deterministic manuscript finalization gates",
    )
    manuscript_sub = manuscript.add_subparsers(dest="manuscript_command", required=True)
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
    manuscript_canonicalize = manuscript_sub.add_parser(
        "canonicalize",
        help="Convert a reviewed draft into the venue-neutral canonical paper form",
    )
    manuscript_canonicalize.add_argument(
        "path", help="Reviewed English or Chinese Markdown manuscript"
    )
    manuscript_canonicalize.add_argument(
        "--output", help="Canonical Markdown output path"
    )
    manuscript_canonicalize.add_argument(
        "--report", help="Canonicalization JSON report path"
    )
    manuscript_canonicalize.add_argument(
        "--language", choices=["en", "zh"], default="en"
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
    manuscript_finalize.add_argument(
        "--readiness",
        help=(
            "Required for a publication_manuscript.tex unless the default "
            "synthesis/publication_readiness.json exists; enforces the release order"
        ),
    )

    diagram = sub.add_parser(
        "diagram",
        help="Render editable draw.io research diagrams into publication assets",
    )
    diagram_sub = diagram.add_subparsers(dest="diagram_command", required=True)
    diagram_export = diagram_sub.add_parser(
        "export", help="Export a .drawio source to PNG, PDF, or SVG"
    )
    diagram_export.add_argument("source", help="Editable .drawio source")
    diagram_export.add_argument("--output", required=True, help="Rendered output path")
    diagram_export.add_argument(
        "--format", choices=["png", "pdf", "svg"], help="Defaults to output suffix"
    )
    diagram_export.add_argument("--executable", help="Optional draw.io executable path")

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

    complete = sub.add_parser(
        "complete", help="Close the four-stage loop after synthesis audit"
    )
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
    bundle_inspect.add_argument(
        "source", help="Existing project folder to inspect read-only"
    )
    bundle_inspect.add_argument(
        "--discover-claims",
        action="store_true",
        help="Also retrieve RedFox/scholarly attention signals and match them to project-authored claims",
    )
    bundle_close = bundle_sub.add_parser(
        "close-loop",
        help="Snapshot one selected track and generate four-stage evidence plus a working paper",
    )
    bundle_close.add_argument(
        "source", help="Existing project folder to inspect read-only"
    )
    bundle_close.add_argument(
        "--output-root",
        default="bundle_runs",
        help="Directory for immutable bundle run artifacts (default: ./bundle_runs)",
    )
    bundle_close.add_argument("--name")
    bundle_close.add_argument(
        "--discover-claims",
        action="store_true",
        help="Persist the two-channel claim-discovery report in Stage 1",
    )
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

    study = sub.add_parser(
        "study", help="Freeze and run the preregistered claim-gate study"
    )
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
    study_freeze.add_argument(
        "--secondary-evaluator-contract",
        help="Project-local hash-bound independent robustness evaluator required by publication intent",
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
        "run-baseline",
        help="Run or resume the nine no-gate baseline cells in fixed order",
    )
    study_run_baseline.add_argument("project")
    study_run_baseline.add_argument("--max-cells", type=int)
    study_audit_baseline = study_sub.add_parser(
        "audit-baseline", help="Audit the Stage 2 no-gate baseline matrix"
    )
    study_audit_baseline.add_argument("project")
    study_run_treatment = study_sub.add_parser(
        "run-treatment",
        help="Run or resume the nine gated treatment cells in fixed order",
    )
    study_run_treatment.add_argument("project")
    study_run_treatment.add_argument("--max-cells", type=int)
    study_audit_treatment = study_sub.add_parser(
        "audit-treatment", help="Audit the Stage 2 gated treatment matrix"
    )
    study_audit_treatment.add_argument("project")
    study_evaluate = study_sub.add_parser(
        "evaluate",
        help="Run or resume the protected arm-blinded evaluation of all 18 outputs",
    )
    study_evaluate.add_argument("project")
    study_audit_evaluation = study_sub.add_parser(
        "audit-evaluation", help="Audit the protected 18-registry evaluation"
    )
    study_audit_evaluation.add_argument("project")
    study_evaluate_publication = study_sub.add_parser(
        "evaluate-publication",
        help="Run the protected arm-blinded NLI evaluation of the frozen 8x5 publication matrix",
    )
    study_evaluate_publication.add_argument("project")
    study_audit_publication_pairs = study_sub.add_parser(
        "audit-publication-pairs",
        help="Audit shared-artifact, branch-order, telemetry, and hash bindings for all publication pairs",
    )
    study_audit_publication_pairs.add_argument("project")
    study_prepare_publication_manual = study_sub.add_parser(
        "prepare-publication-manual-audit",
        help="Create two independent auditor packets for the frozen publication sample",
    )
    study_prepare_publication_manual.add_argument("project")
    study_submit_publication_manual = study_sub.add_parser(
        "submit-publication-manual-audit",
        help="Validate and freeze two completed publication auditor packets",
    )
    study_submit_publication_manual.add_argument("project")
    study_submit_publication_manual.add_argument("--auditor-1", required=True)
    study_submit_publication_manual.add_argument("--auditor-2", required=True)
    study_finalize_publication_manual = study_sub.add_parser(
        "finalize-publication-manual-audit",
        help="Apply blinded adjudication and evaluate the publication human gate",
    )
    study_finalize_publication_manual.add_argument("project")
    study_finalize_publication_manual.add_argument("--adjudication")
    study_audit_publication_manual = study_sub.add_parser(
        "audit-publication-manual-audit",
        help="Audit publication manual-review preparation or completion",
    )
    study_audit_publication_manual.add_argument("project")
    study_import_publication_manual = study_sub.add_parser(
        "import-publication-manual-audit-workbooks",
        help="Strictly import two reviewed Excel workbooks plus the signed supplement and close the human gate",
    )
    study_import_publication_manual.add_argument("project")
    study_import_publication_manual.add_argument("--auditor-1-xlsx", required=True)
    study_import_publication_manual.add_argument("--auditor-2-xlsx", required=True)
    study_import_publication_manual.add_argument("--supplement-xlsx", required=True)
    study_context_review = study_sub.add_parser(
        "record-publication-context-review",
        help="Append a post-unblinding task-context review without replacing the frozen blinded audit",
    )
    study_context_review.add_argument("project")
    study_context_review.add_argument(
        "--verdicts",
        required=True,
        help="A/B/C verdicts in frozen evaluator-unsupported order",
    )
    study_context_review.add_argument("--reviewer-id", required=True)
    study_audit_context_review = study_sub.add_parser(
        "audit-publication-context-review",
        help="Audit the append-only context-restored diagnostic review",
    )
    study_audit_context_review.add_argument("project")
    study_verify_context_repair = study_sub.add_parser(
        "verify-publication-context-repair",
        help="Replay the diagnosed unsupported cases against the successor context-bound evaluator rules",
    )
    study_verify_context_repair.add_argument("project")
    study_prepare_successor = study_sub.add_parser(
        "prepare-publication-successor-plan",
        help="Freeze a prospective successor protocol plan without reusing predecessor outcomes",
    )
    study_prepare_successor.add_argument("project")
    study_audit_successor = study_sub.add_parser(
        "audit-publication-successor-plan",
        help="Audit repair bindings and non-reuse boundaries in the frozen successor plan",
    )
    study_audit_successor.add_argument("project")
    study_synthesize_publication = study_sub.add_parser(
        "synthesize-publication",
        help="Generate evidence-bound manuscript artifacts from the completed 8x5 publication matrix",
    )
    study_synthesize_publication.add_argument("project")
    study_audit_publication_synthesis = study_sub.add_parser(
        "audit-publication-synthesis",
        help="Audit the publication manuscript, claim registry, and human-validation boundary",
    )
    study_audit_publication_synthesis.add_argument("project")
    study_publication_layout_gate = study_sub.add_parser(
        "publication-layout-gate",
        help="Require a passing fixed-venue readiness report before LaTeX/PDF layout",
    )
    study_publication_layout_gate.add_argument("project")
    study_publication_layout_gate.add_argument("--readiness", required=True)
    study_prepare_publication_supplement = study_sub.add_parser(
        "prepare-publication-supplement",
        help="Prepare a local anonymous, hash-bound supplement package without publishing it",
    )
    study_prepare_publication_supplement.add_argument("project")
    study_audit_publication_supplement = study_sub.add_parser(
        "audit-publication-supplement",
        help="Audit the local anonymous supplement package and its current-source bindings",
    )
    study_audit_publication_supplement.add_argument("project")
    study_prepare_review_package = study_sub.add_parser(
        "prepare-review-submission-package",
        help="Assemble a local review package after the automated publication gate; never upload or submit it",
    )
    study_prepare_review_package.add_argument("project")
    study_prepare_review_package.add_argument("--readiness", required=True)
    study_audit_review_package = study_sub.add_parser(
        "audit-review-submission-package",
        help="Audit the local review package, PDF, and anonymous supplement bindings",
    )
    study_audit_review_package.add_argument("project")
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
    benchmark_list = benchmark_sub.add_parser(
        "list", help="List packaged benchmark tasks"
    )
    benchmark_list.add_argument(
        "--extra-root", help="Additional directory containing task packs"
    )
    benchmark_doctor_parser = benchmark_sub.add_parser(
        "doctor", help="Check RF-Bench, Docker, and optional AIRS dataset runtime"
    )
    benchmark_doctor_parser.add_argument("--dataset-python")
    benchmark_doctor_parser.add_argument("--docker-image", default="rf-airs-cpu:v1")
    benchmark_build_env = benchmark_sub.add_parser(
        "build-env", help="Build and verify a controlled ML Docker environment"
    )
    benchmark_build_env.add_argument(
        "--profile", choices=["airs-cpu"], default="airs-cpu"
    )
    benchmark_build_env.add_argument("--docker-image", default="rf-airs-cpu:v1")
    benchmark_run = benchmark_sub.add_parser("run", help="Run one benchmark task")
    benchmark_run.add_argument("task", help="Built-in task ID or task-pack directory")
    benchmark_run.add_argument("--strategy", choices=["codex", "grid"], default="codex")
    benchmark_run.add_argument("--seed", type=int, action="append", dest="seeds")
    benchmark_run.add_argument(
        "--seeds", dest="seeds_csv", help="Comma-separated seeds"
    )
    benchmark_run.add_argument("--iterations", type=int)
    benchmark_run.add_argument("--output-root", help="Benchmark run output root")
    _add_runtime_arguments(benchmark_run, default_runtime="local")
    benchmark_loop = benchmark_sub.add_parser(
        "run-loop",
        help="Run the resumable deterministic automatic experiment controller",
    )
    benchmark_loop.add_argument("task", help="Built-in task ID or task-pack directory")
    benchmark_loop.add_argument(
        "--strategy", choices=["codex", "grid"], default="codex"
    )
    benchmark_loop.add_argument("--seed", type=int, action="append", dest="seeds")
    benchmark_loop.add_argument(
        "--seeds", dest="seeds_csv", help="Comma-separated seeds"
    )
    benchmark_loop.add_argument("--iterations", type=int)
    benchmark_loop.add_argument("--candidate-pool-size", type=int, default=3)
    benchmark_loop.add_argument(
        "--proposal-attempts-per-iteration", type=int, default=6
    )
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
    benchmark_freeze_ablation.add_argument(
        "--seed", type=int, action="append", dest="seeds"
    )
    benchmark_freeze_ablation.add_argument("--docker-image", default="rf-airs-cpu:v1")
    benchmark_run_ablation = benchmark_sub.add_parser(
        "run-ablation",
        help="Run or resume a frozen controller ablation matrix",
    )
    benchmark_run_ablation.add_argument("matrix", help="Frozen matrix output directory")
    benchmark_audit = benchmark_sub.add_parser(
        "audit", help="Audit one benchmark project"
    )
    benchmark_audit.add_argument(
        "project", help="Path to a materialized benchmark project"
    )
    benchmark_import = benchmark_sub.add_parser(
        "import-airs", help="Import AIRS-Bench task metadata"
    )
    benchmark_import.add_argument("source", help="AIRS task directory")
    benchmark_import.add_argument(
        "--output-root", required=True, help="Destination task-pack root"
    )
    benchmark_activate = benchmark_sub.add_parser(
        "activate-airs-lite",
        help="Prepare a CPU AIRS development task from its official dataset split",
    )
    benchmark_activate.add_argument(
        "task_pack", help="Imported AIRS task-pack directory"
    )
    benchmark_activate.add_argument(
        "--dataset-python",
        required=True,
        help="Python interpreter containing datasets==3.6.0",
    )
    benchmark_activate.add_argument(
        "--cache-dir", required=True, help="Hugging Face data cache"
    )
    benchmark_import_official = benchmark_sub.add_parser(
        "import-airs-official",
        help="Freeze an unmodified AIRS RAD task for the aira-dojo adapter",
    )
    benchmark_import_official.add_argument(
        "source", help="Official AIRS airsbench/tasks/rad task directory"
    )
    benchmark_import_official.add_argument("--output-root", required=True)
    benchmark_prepare_official = benchmark_sub.add_parser(
        "prepare-airs-official", help="Run an official AIRS RAD prepare.py unchanged"
    )
    benchmark_prepare_official.add_argument("task_pack")
    benchmark_prepare_official.add_argument("--global-shared-data-dir", required=True)
    benchmark_prepare_official.add_argument("--agent-data-mount-dir", required=True)
    benchmark_prepare_official.add_argument("--agent-log-dir", required=True)
    benchmark_prepare_official.add_argument("--evaluator-data-mount-dir", required=True)
    benchmark_prepare_official.add_argument("--python", required=True)
    benchmark_prepare_official.add_argument(
        "--runtime", choices=("local", "docker"), default="local"
    )
    benchmark_prepare_official.add_argument("--docker-image")
    benchmark_evaluate_official = benchmark_sub.add_parser(
        "evaluate-airs-official",
        help="Run official AIRS evaluate_prepare.py and evaluate.py unchanged",
    )
    benchmark_evaluate_official.add_argument("task_pack")
    benchmark_evaluate_official.add_argument("--python", required=True)
    benchmark_evaluate_official.add_argument(
        "--runtime", choices=("local", "docker"), default="local"
    )
    benchmark_evaluate_official.add_argument("--docker-image")

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
        project = (
            explicit.resolve() if explicit.is_dir() else _project(project_option, home)
        )
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
        raise FileNotFoundError(
            f"project has no synthesized English manuscript; checked: {checked}"
        )
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
            print(
                f'Next: python main.py --home "{path.parent}" plan {path.name} --message "补充研究边界"'
            )
        elif args.command == "pipeline":
            from .pipeline_contracts import (
                PIPELINE_MANIFEST_FILENAME,
                initialize_pipeline_project,
                load_project_spec,
            )

            project = Path(args.project).expanduser().resolve()
            if args.pipeline_command == "initialize":
                spec = load_project_spec(Path(args.spec).expanduser().resolve())
                output = initialize_pipeline_project(project, spec)
                _print_json({"output": str(output), **read_json(output)})
            elif args.pipeline_command == "inspect":
                _print_json(read_json(project / PIPELINE_MANIFEST_FILENAME))
        elif args.command == "task":
            from .orchestration import TaskRequest, TaskStatus
            from .workflow_tasks import create_workflow_orchestrator

            orchestrator = create_workflow_orchestrator(args.task_root)
            if args.task_command == "submit":
                record = orchestrator.submit(
                    TaskRequest(
                        operation=args.operation,
                        payload=_task_payload(args.payload),
                        idempotency_key=args.idempotency_key,
                    )
                )
                if args.run:
                    record = orchestrator.run_sync(record.task_id)
                _print_json(record.model_dump(mode="json"))
                if record.status in {TaskStatus.FAILED, TaskStatus.BLOCKED}:
                    return 2
            elif args.task_command == "inspect":
                _print_json(orchestrator.load(args.task_id).model_dump(mode="json"))
            elif args.task_command == "resume":
                record = orchestrator.run_sync(args.task_id, resume=True)
                _print_json(record.model_dump(mode="json"))
                if record.status is not TaskStatus.SUCCEEDED:
                    return 2
            elif args.task_command == "cancel":
                _print_json(orchestrator.cancel(args.task_id).model_dump(mode="json"))
            elif args.task_command == "list":
                status = TaskStatus(args.status) if args.status else None
                _print_json(
                    {
                        "tasks": [
                            item.model_dump(mode="json")
                            for item in orchestrator.list(status=status)
                        ]
                    }
                )
        elif args.command == "workflow":
            from .workflow_domain import WorkflowRepository, verify_completion_record

            repository = WorkflowRepository(args.workflow_root)
            if args.workflow_command == "list-projects":
                _print_json(
                    {
                        "projects": [
                            item.model_dump(mode="json")
                            for item in repository.list_projects()
                        ]
                    }
                )
            elif args.workflow_command == "list-studies":
                _print_json(
                    {
                        "studies": [
                            item.model_dump(mode="json")
                            for item in repository.list_studies(args.project_id)
                        ]
                    }
                )
            elif args.workflow_command == "inspect-study":
                _print_json(repository.snapshot(args.study_id))
            elif args.workflow_command == "run-study":
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    stage_one_handlers,
                )

                _print_json(
                    PersistentDAGScheduler(repository, stage_one_handlers()).run(
                        args.study_id
                    )
                )
            elif args.workflow_command == "pause-study":
                _print_json(
                    repository.pause_study(args.study_id).model_dump(mode="json")
                )
            elif args.workflow_command == "resume-study":
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    stage_one_handlers,
                )

                repository.resume_study(args.study_id)
                _print_json(
                    PersistentDAGScheduler(
                        repository,
                        stage_one_handlers(),
                        recover_interrupted=True,
                    ).run(args.study_id)
                )
            elif args.workflow_command == "retry-step":
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    stage_one_handlers,
                )

                scheduler = PersistentDAGScheduler(repository, stage_one_handlers())
                scheduler.retry_step(args.study_id, args.step_id)
                _print_json(scheduler.run(args.study_id))
            elif args.workflow_command == "migrate-run":
                from .workflow_migration import migrate_bundle_run

                _print_json(
                    migrate_bundle_run(
                        Path(args.run_dir).expanduser().resolve(),
                        repository_root=repository.root,
                    )
                )
            elif args.workflow_command == "verify-completion":
                result = verify_completion_record(
                    Path(args.record).expanduser().resolve(),
                    artifact_root=(
                        Path(args.artifact_root).expanduser().resolve()
                        if args.artifact_root
                        else None
                    ),
                )
                _print_json(result)
                if not result["passed"]:
                    return 2
        elif args.command == "retrieval":
            from .models import utc_now
            from .retrieval.domain.models import (
                NetworkMode,
                ResourceType,
                RetrievalBudget,
                RetrievalPhase,
                retrieval_id,
            )
            from .retrieval.interfaces.service import RetrievalGateway
            from .retrieval.policy.engine import RetrievalNetworkPolicy

            # External Research V1 documents the readable nested forms
            # ``retrieval policy show|set`` and ``retrieval corpus
            # build|query``. Preserve the original flat commands as
            # compatibility aliases and normalize both forms here.
            if args.retrieval_command == "policy":
                args.retrieval_command = (
                    f"policy-{args.retrieval_policy_command}"
                )
            elif args.retrieval_command == "corpus":
                args.retrieval_command = (
                    f"corpus-{args.retrieval_corpus_command}"
                )

            gateway = RetrievalGateway(args.workflow_root)
            if args.retrieval_command == "policy-show":
                _print_json(gateway.get_policy(args.project_id).model_dump(mode="json"))
            elif args.retrieval_command == "policy-set":
                mode = NetworkMode(args.mode)
                resource_types = (
                    {ResourceType(item) for item in args.resource_type}
                    if args.resource_type
                    else set(ResourceType)
                )
                policy = RetrievalNetworkPolicy(
                    policy_id=retrieval_id(
                        "network-policy",
                        args.project_id,
                        mode.value,
                        *sorted(args.provider),
                        *sorted(args.domain),
                        "proxy-fake-ip" if args.allow_proxy_fake_ip else "",
                    ),
                    mode=mode,
                    allowed_providers=set(args.provider),
                    allowed_domains=set(args.domain),
                    allowed_http_methods={item.upper() for item in args.method},
                    allowed_resource_types=resource_types,
                    allow_abstract=mode is not NetworkMode.OFFLINE,
                    allow_full_text=args.allow_full_text,
                    allow_proxy_fake_ip=args.allow_proxy_fake_ip,
                    allow_authenticated_access=mode
                    in {
                        NetworkMode.AUTHENTICATED_READ,
                        NetworkMode.PUBLIC_RESEARCH_PLUS_INSTITUTION,
                        NetworkMode.EXTERNAL_WRITE,
                    },
                    allow_external_write=mode is NetworkMode.EXTERNAL_WRITE,
                    max_queries=args.max_queries,
                    max_results=args.max_results,
                    max_bytes=args.max_bytes,
                    max_cost=args.max_cost,
                    approved_by=args.approved_by,
                    approved_at=utc_now() if args.approved_by else None,
                )
                _print_json(
                    gateway.set_policy(args.project_id, policy).model_dump(mode="json")
                )
            elif args.retrieval_command == "plan":
                request = gateway.plan(
                    project_id=args.project_id,
                    study_id=args.study_id,
                    phase=RetrievalPhase(args.phase),
                    step_instance_id=args.step_id,
                    purpose=args.purpose,
                    queries=args.query,
                    providers=args.provider,
                    resource_types=[ResourceType(item) for item in args.resource_type],
                    usage_role=args.usage_role,
                    budget=RetrievalBudget(
                        max_queries=len(args.query),
                        max_results=100,
                        max_download_bytes=5_000_000,
                        max_cost=1.0,
                    ),
                    idempotency_key=args.idempotency_key,
                    freshness=args.freshness,
                    allowed_domains=args.allow_domain,
                    blocked_domains=args.block_domain,
                )
                _print_json(request.model_dump(mode="json"))
            elif args.retrieval_command == "readiness":
                from .retrieval.interfaces.readiness import ReadinessService

                service = ReadinessService(gateway.repository, gateway.providers)
                report = (
                    service.evaluate()
                    if args.refresh
                    else gateway.repository.latest_readiness_report()
                    or service.evaluate()
                )
                _print_json(report.model_dump(mode="json"))
            elif args.retrieval_command == "workflow-run":
                from .retrieval.workflow import (
                    append_external_research_dag,
                    append_external_research_loop,
                )
                from .workflow_domain import WorkflowRepository
                from .workflow_scheduler import (
                    PersistentDAGScheduler,
                    stage_one_handlers,
                )

                repository = WorkflowRepository(args.workflow_root)
                if args.stage == "all":
                    steps = append_external_research_loop(
                        repository,
                        args.study_id,
                        depends_on=args.depends_on,
                        run_key=args.run_key,
                        include_repair=args.include_repair,
                    )
                else:
                    steps = append_external_research_dag(
                        repository,
                        args.study_id,
                        args.stage,
                        depends_on=args.depends_on,
                        run_key=args.run_key,
                    )
                snapshot = (
                    repository.snapshot(args.study_id)
                    if args.plan_only
                    else PersistentDAGScheduler(
                        repository,
                        stage_one_handlers(),
                        recover_interrupted=True,
                    ).run(args.study_id)
                )
                _print_json(
                    {
                        "study_id": args.study_id,
                        "created_step_ids": [
                            item.step_instance_id for item in steps
                        ],
                        "snapshot": snapshot,
                    }
                )
            elif args.retrieval_command == "run":
                _print_json(gateway.run(args.request_id).model_dump())
            elif args.retrieval_command == "status":
                _print_json(
                    gateway.repository.load_run(args.run_id).model_dump(mode="json")
                )
            elif args.retrieval_command == "resources":
                resources = gateway.repository.list_resources()
                bindings = gateway.repository.list_bindings(args.study_id)
                if args.study_id:
                    allowed = {item.resource_id for item in bindings}
                    resources = [
                        item for item in resources if item.resource_id in allowed
                    ]
                _print_json(
                    {
                        "resources": [
                            item.model_dump(mode="json") for item in resources
                        ],
                        "bindings": [item.model_dump(mode="json") for item in bindings],
                    }
                )
            elif args.retrieval_command == "coverage":
                _print_json(
                    gateway.repository.load_coverage(
                        args.coverage_report_id
                    ).model_dump(mode="json")
                )
            elif args.retrieval_command == "freeze":
                _print_json(
                    gateway.freeze_resource_set(args.resource_set_id).model_dump(
                        mode="json"
                    )
                )
            elif args.retrieval_command == "promote":
                _print_json(
                    gateway.promote_binding(
                        args.binding_id,
                        target_phase=RetrievalPhase(args.phase),
                        step_instance_id=args.step_id,
                        purpose=args.purpose,
                        usage_role=args.usage_role,
                        target_type=args.target_type,
                        target_id=args.target_id,
                        target_field=args.target_field,
                    ).model_dump(mode="json")
                )
            elif args.retrieval_command == "retry":
                _print_json(gateway.retry(args.run_id).model_dump())
            elif args.retrieval_command == "search":
                request = gateway.plan(
                    project_id=args.project_id,
                    study_id=args.study_id,
                    phase=RetrievalPhase(args.phase),
                    step_instance_id=args.step_id,
                    purpose=args.purpose,
                    queries=args.query,
                    providers=args.provider,
                    resource_types=[
                        ResourceType(item) for item in args.resource_type
                    ],
                    usage_role=args.usage_role,
                    budget=RetrievalBudget(
                        max_queries=len(args.query),
                        max_results=100,
                        max_download_bytes=5_000_000,
                        max_cost=1.0,
                    ),
                    idempotency_key=args.idempotency_key,
                    freshness=args.freshness,
                    allowed_domains=args.allow_domain,
                    blocked_domains=args.block_domain,
                )
                _print_json(gateway.run(request.request_id).model_dump())
            elif args.retrieval_command == "connect-institution":
                from .retrieval.institution import InstitutionSessionBroker

                _print_json(
                    InstitutionSessionBroker(args.workflow_root)
                    .create(
                        owner_user_id=args.owner_user_id,
                        project_id=args.project_id,
                        study_id=args.study_id,
                        institution_id=args.institution_id,
                        target_url=args.target_url,
                    )
                    .model_dump(mode="json")
                )
            elif args.retrieval_command == "institution-status":
                from .retrieval.institution import InstitutionSessionBroker

                _print_json(
                    InstitutionSessionBroker(args.workflow_root)
                    .load(
                        args.session_id,
                        owner_user_id=args.owner_user_id,
                    )
                    .model_dump(mode="json")
                )
            elif args.retrieval_command == "revoke-institution":
                from .retrieval.institution import InstitutionSessionBroker

                _print_json(
                    InstitutionSessionBroker(args.workflow_root)
                    .revoke(
                        args.session_id,
                        owner_user_id=args.owner_user_id,
                    )
                    .model_dump(mode="json")
                )
            elif args.retrieval_command == "corpus-build":
                from .retrieval.domain.external_models import CorpusDocument
                from .retrieval.evidence import PaperQAEvidenceService

                _print_json(
                    PaperQAEvidenceService(gateway.repository)
                    .build_corpus(
                        study_id=args.study_id,
                        phase=RetrievalPhase(args.phase),
                        documents=[
                            CorpusDocument.model_validate(json.loads(item))
                            for item in args.document_json
                        ],
                        parser_version=args.parser_version,
                        embedding_model_hash=args.embedding_model_hash,
                        llm_config_hash=args.llm_config_hash,
                    )
                    .model_dump(mode="json")
                )
            elif args.retrieval_command == "corpus-query":
                from .retrieval.evidence import PaperQAEvidenceService

                service = PaperQAEvidenceService(gateway.repository)
                if args.questions_file:
                    questions = json.loads(
                        args.questions_file.read_text(encoding="utf-8")
                    )
                    if not isinstance(questions, list):
                        raise ValueError("questions file must contain a JSON list")
                    _print_json(
                        {
                            "answers": [
                                item.model_dump(mode="json")
                                for item in service.query_evidence_batch(
                                    args.corpus_id,
                                    questions,
                                )
                            ]
                        }
                    )
                else:
                    _print_json(
                        service.ask(args.corpus_id, args.question).model_dump(
                            mode="json"
                        )
                    )
        elif args.command == "plan":
            draft_id, draft = asyncio.run(
                plan_project(_project(args.project, args.home), args.message)
            )
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
                    [
                        source.model_dump(mode="json")
                        for source in list_literature_sources(project)
                    ]
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
            envelope = asyncio.run(
                propose_experiment(_project(args.project, args.home), args.focus)
            )
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
            promotion = promote_run(
                _project(args.project, args.home), args.run_id, args.confirm
            )
            _print_json(promotion.model_dump(mode="json"))
        elif args.command == "recover":
            record = recover_run(_project(args.project, args.home), args.confirm)
            _print_json(record.model_dump(mode="json"))
        elif args.command == "status":
            _print_json(
                _project_status_with_study_overlay(_project(args.project, args.home))
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
        elif (
            args.command == "venue" and args.venue_command == "freeze-experiment-target"
        ):
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
                    "automated_publication_gate_passed": readiness.automated_publication_gate_passed,
                    "human_gate_pending": readiness.human_gate_pending,
                    "publication_submission_ready": readiness.publication_submission_ready,
                    "hard_blocker_count": len(readiness.hard_blockers),
                    "estimated_acceptance_probability": readiness.estimated_acceptance_probability.model_dump(
                        mode="json"
                    ),
                    "projected_readiness_after_plan": readiness.projected_readiness_after_plan,
                    "semantics": "readiness is not acceptance probability",
                    **repair_outputs,
                }
            )
            if (
                args.venue_command == "readiness"
                and not args.no_fail
                and not readiness.publication_submission_ready
            ):
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
            stem = (
                "venue_recommendation"
                if args.command == "venue"
                else "journal_recommendation"
            )
            report_path = (
                Path(args.report).resolve()
                if args.report
                else output_root / f"{stem}.json"
            )
            markdown_path = (
                Path(args.markdown).resolve()
                if args.markdown
                else output_root / f"{stem}.md"
            )
            overrides = (
                read_json(Path(args.assessment).resolve()) if args.assessment else None
            )
            if args.command == "venue":
                venue_types = (
                    {"journal", "conference"} if args.type == "all" else {args.type}
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
                            "scientific_success_if_validly_submitted": item.combined_submission_success.model_dump(
                                mode="json"
                            ),
                            "current_cycle_success": item.current_cycle_submission_success.model_dump(
                                mode="json"
                            ),
                            "after_known_blockers_resolved": item.after_known_blockers_resolved.model_dump(
                                mode="json"
                            ),
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
            elif args.manuscript_command == "canonicalize":
                from .manuscript_canonicalization import (
                    canonicalize_reviewed_manuscript,
                )

                report = canonicalize_reviewed_manuscript(
                    args.path,
                    output=args.output,
                    report_path=args.report,
                    language=args.language,
                )
                _print_json(report.model_dump(mode="json"))
                if not report.passed:
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
                        readiness_path=args.readiness,
                    )
                )
        elif args.command == "diagram":
            from .drawio_backend import export_drawio, find_drawio

            output = export_drawio(
                args.source,
                args.output,
                format=args.format,
                executable=args.executable,
            )
            _print_json(
                {
                    "source": str(Path(args.source).resolve()),
                    "output": str(output),
                    "drawio": str(Path(args.executable).resolve())
                    if args.executable
                    else str(find_drawio()),
                }
            )
        elif args.command == "terminology":
            from .terminology import import_terminology_review, prepare_terminology

            project = _project(args.project, args.home)
            if args.terminology_command == "prepare":
                plan = asyncio.run(prepare_terminology(project, language=args.language))
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
            from .orchestration import TaskRequest
            from .workflow_tasks import create_workflow_orchestrator

            orchestrator = create_workflow_orchestrator(args.task_root)

            if args.bundle_command == "inspect":
                record = orchestrator.execute_sync(
                    TaskRequest(
                        operation="bundle.inspect",
                        payload={
                            "source": args.source,
                            "discover_claims": args.discover_claims,
                            "workflow_root": args.workflow_root,
                        },
                    )
                )
                _print_json(
                    {**(record.result or {}), "_task": record.model_dump(mode="json")}
                )
            elif args.bundle_command == "close-loop":
                record = orchestrator.execute_sync(
                    TaskRequest(
                        operation="bundle.close",
                        payload={
                            "source": args.source,
                            "output_root": args.output_root,
                            "name": args.name or "",
                            "track_id": args.track,
                            "discover_claims": args.discover_claims,
                        },
                    )
                )
                _print_json(
                    {**(record.result or {}), "_task": record.model_dump(mode="json")}
                )
            elif args.bundle_command == "audit":
                from .project_bundle import verify_project_bundle_completion

                _print_json(verify_project_bundle_completion(args.run))
            elif args.bundle_command == "prepare-paper":
                from .paper_expansion import prepare_project_bundle_paper

                plan = prepare_project_bundle_paper(args.run, persist=True)
                _print_json(plan.model_dump(mode="json"))
            elif args.bundle_command == "expand-paper":
                record = orchestrator.execute_sync(
                    TaskRequest(
                        operation="bundle.expand-paper",
                        payload={"run_dir": args.run},
                    )
                )
                _print_json(
                    {
                        **((record.result or {}).get("audit") or {}),
                        "_task": record.model_dump(mode="json"),
                    }
                )
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
                    secondary_evaluator_contract=args.secondary_evaluator_contract,
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
            elif args.study_command in {
                "evaluate-publication",
                "audit-publication-pairs",
                "prepare-publication-manual-audit",
                "submit-publication-manual-audit",
                "finalize-publication-manual-audit",
                "audit-publication-manual-audit",
                "import-publication-manual-audit-workbooks",
                "record-publication-context-review",
                "audit-publication-context-review",
                "verify-publication-context-repair",
                "prepare-publication-successor-plan",
                "audit-publication-successor-plan",
                "synthesize-publication",
                "audit-publication-synthesis",
                "publication-layout-gate",
                "prepare-publication-supplement",
                "audit-publication-supplement",
                "prepare-review-submission-package",
                "audit-review-submission-package",
            }:
                from .publication_adapters import execute_publication_action

                action_map = {
                    "evaluate-publication": "evaluate",
                    "audit-publication-pairs": "audit-pairs",
                    "prepare-publication-manual-audit": "manual-prepare",
                    "submit-publication-manual-audit": "manual-submit",
                    "finalize-publication-manual-audit": "manual-finalize",
                    "audit-publication-manual-audit": "manual-audit",
                    "import-publication-manual-audit-workbooks": "manual-import-workbooks",
                    "record-publication-context-review": "context-record",
                    "audit-publication-context-review": "context-audit",
                    "verify-publication-context-repair": "context-verify-repair",
                    "prepare-publication-successor-plan": "successor-prepare",
                    "audit-publication-successor-plan": "successor-audit",
                    "synthesize-publication": "synthesize",
                    "audit-publication-synthesis": "audit-synthesis",
                    "publication-layout-gate": "render-layout",
                    "prepare-publication-supplement": "supplement-prepare",
                    "audit-publication-supplement": "supplement-audit",
                    "prepare-review-submission-package": "review-package-prepare",
                    "audit-review-submission-package": "review-package-audit",
                }
                parameters: dict[str, object] = {}
                if args.study_command == "submit-publication-manual-audit":
                    parameters = {
                        "auditor_1": args.auditor_1,
                        "auditor_2": args.auditor_2,
                    }
                elif args.study_command == "finalize-publication-manual-audit":
                    parameters = {"adjudication": args.adjudication}
                elif args.study_command == "import-publication-manual-audit-workbooks":
                    parameters = {
                        "auditor_1_xlsx": args.auditor_1_xlsx,
                        "auditor_2_xlsx": args.auditor_2_xlsx,
                        "supplement_xlsx": args.supplement_xlsx,
                    }
                elif args.study_command == "record-publication-context-review":
                    parameters = {
                        "verdicts": args.verdicts,
                        "reviewer_id": args.reviewer_id,
                    }
                elif args.study_command in {
                    "publication-layout-gate",
                    "prepare-review-submission-package",
                }:
                    parameters = {"readiness": args.readiness}
                action = action_map[args.study_command]
                result = execute_publication_action(
                    project, action, parameters=parameters
                )
                _print_json(result)
                if action == "successor-audit" and not result.get("passed"):
                    return 2
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
            elif args.benchmark_command == "import-airs-official":
                from .official_airs import import_official_airs_task

                _print_json(
                    {
                        "imported": str(
                            import_official_airs_task(args.source, args.output_root)
                        )
                    }
                )
            elif args.benchmark_command == "prepare-airs-official":
                from .official_airs import prepare_official_airs_task

                _print_json(
                    prepare_official_airs_task(
                        args.task_pack,
                        global_shared_data_dir=args.global_shared_data_dir,
                        agent_data_mount_dir=args.agent_data_mount_dir,
                        agent_log_dir=args.agent_log_dir,
                        evaluator_data_mount_dir=args.evaluator_data_mount_dir,
                        python=args.python,
                        execution=args.runtime,
                        docker_image=args.docker_image,
                    )
                )
            elif args.benchmark_command == "evaluate-airs-official":
                from .official_airs import evaluate_official_airs_submission

                _print_json(
                    evaluate_official_airs_submission(
                        args.task_pack,
                        python=args.python,
                        execution=args.runtime,
                        docker_image=args.docker_image,
                    )
                )
        elif args.command == "web":
            from .web_app import run_web_app

            run_web_app(args.host, args.port, open_browser=args.open)
        elif args.command == "doctor":
            from .agent_runtime import backend_status
            from .drawio_backend import find_drawio

            status = {
                "python": sys.version.split()[0],
                "python_executable": sys.executable,
                "drawio_executable": str(find_drawio()) if find_drawio() else None,
                **backend_status(),
            }
            if status["backend"] == "api":
                status["openai_api_key_present"] = key_is_present()
            _print_json(status)
        return 0
    except (
        ValueError,
        FileNotFoundError,
        FileExistsError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
