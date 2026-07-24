from __future__ import annotations

from ...workflow_domain import (
    ExecutorType,
    Phase,
    StepDefinition,
    StepInstance,
    WorkflowRepository,
)


EXTERNAL_RESEARCH_DAGS: dict[str, tuple[str, ...]] = {
    "discovery": (
        "plan_external_research",
        "evaluate_retrieval_policy",
        "sanitize_queries",
        "execute_academic_search",
        "execute_github_search",
        "execute_huggingface_search",
        "execute_official_web_search",
        "normalize_resources",
        "build_identifier_graph",
        "deduplicate_resources",
        "verify_metadata",
        "build_resource_relation_graph",
        "resolve_open_access",
        "optionally_request_institution_access",
        "acquire_approved_documents",
        "build_paperqa_corpus",
        "analyze_discovery_evidence",
        "generate_coverage_report",
        "freeze_discovery_resource_set",
        "scope_review_gate",
    ),
    "protocol": (
        "plan_protocol_retrieval",
        "search_protocol_sources",
        "resolve_baseline_code",
        "resolve_dataset_versions",
        "resolve_model_revisions",
        "search_official_documentation",
        "verify_licenses",
        "bind_resources_to_contract_fields",
        "freeze_protocol_resource_set",
    ),
    "experimentation": (
        "resolve_contract_approved_resources",
        "fetch_pinned_code",
        "fetch_pinned_model",
        "fetch_pinned_dataset",
        "verify_hashes",
        "release_to_sandbox",
    ),
    "synthesis": (
        "build_synthesis_corpus",
        "verify_citations",
        "check_retractions_and_corrections",
        "verify_resource_versions",
        "search_submission_requirements",
        "analyze_claim_support",
        "produce_citation_audit",
    ),
    "repair": (
        "plan_diagnostic_retrieval",
        "execute_diagnostic_search",
        "bind_findings_to_failure",
        "propose_repair_contract",
        "regression_scope_review",
    ),
}


_PHASES = {
    "discovery": Phase.DISCOVERY,
    "protocol": Phase.PROTOCOL,
    "experimentation": Phase.EXPERIMENT,
    "synthesis": Phase.PAPER,
    # Repair is a cross-cutting state in Workflow v2, not a fifth macro phase.
    "repair": Phase.EXPERIMENT,
}


_EXECUTORS: dict[str, ExecutorType] = {
    "execute_academic_search": ExecutorType.RETRIEVAL_SERVICE,
    "execute_github_search": ExecutorType.RETRIEVAL_SERVICE,
    "execute_huggingface_search": ExecutorType.RETRIEVAL_SERVICE,
    "execute_official_web_search": ExecutorType.RETRIEVAL_SERVICE,
    "search_protocol_sources": ExecutorType.RETRIEVAL_SERVICE,
    "search_official_documentation": ExecutorType.RETRIEVAL_SERVICE,
    "fetch_pinned_code": ExecutorType.RETRIEVAL_SERVICE,
    "fetch_pinned_model": ExecutorType.RETRIEVAL_SERVICE,
    "fetch_pinned_dataset": ExecutorType.RETRIEVAL_SERVICE,
    "check_retractions_and_corrections": ExecutorType.RETRIEVAL_SERVICE,
    "search_submission_requirements": ExecutorType.RETRIEVAL_SERVICE,
    "execute_diagnostic_search": ExecutorType.RETRIEVAL_SERVICE,
    "verify_metadata": ExecutorType.DETERMINISTIC_EVALUATOR,
    "verify_licenses": ExecutorType.DETERMINISTIC_EVALUATOR,
    "verify_hashes": ExecutorType.DETERMINISTIC_EVALUATOR,
    "verify_citations": ExecutorType.DETERMINISTIC_EVALUATOR,
    "verify_resource_versions": ExecutorType.DETERMINISTIC_EVALUATOR,
    "analyze_discovery_evidence": ExecutorType.MODEL,
    "analyze_claim_support": ExecutorType.MODEL,
    "build_paperqa_corpus": ExecutorType.DETERMINISTIC_SERVICE,
    "release_to_sandbox": ExecutorType.SANDBOX_RUNNER,
    "scope_review_gate": ExecutorType.PROJECT_OWNER,
    "regression_scope_review": ExecutorType.PROJECT_OWNER,
}


# These names were used by the first vertical slice. They remain registered so
# old Study records can be resumed without rewriting history.
_LEGACY_DEFINITIONS = (
    ("draft_discovery_query_plan", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_SERVICE),
    ("evaluate_network_policy", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_SERVICE),
    ("sanitize_discovery_queries", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_SERVICE),
    ("execute_discovery_retrieval", Phase.DISCOVERY, ExecutorType.RETRIEVAL_SERVICE),
    (
        "normalize_external_resources",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
    (
        "deduplicate_external_resources",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
    ("verify_external_metadata", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_EVALUATOR),
    ("build_discovery_source_set", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_SERVICE),
    (
        "freeze_discovery_source_set",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
    ("draft_protocol_query_plan", Phase.PROTOCOL, ExecutorType.DETERMINISTIC_SERVICE),
    ("execute_protocol_grounding", Phase.PROTOCOL, ExecutorType.RETRIEVAL_SERVICE),
    (
        "verify_baseline_and_metric_sources",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_EVALUATOR,
    ),
    (
        "bind_sources_to_contract_fields",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
    ("build_protocol_resource_set", Phase.PROTOCOL, ExecutorType.DETERMINISTIC_SERVICE),
    (
        "freeze_protocol_resource_set",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
    (
        "resolve_approved_resources",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
    ("fetch_and_hash_resources", Phase.EXPERIMENT, ExecutorType.RETRIEVAL_SERVICE),
    ("verify_contract_binding", Phase.EXPERIMENT, ExecutorType.DETERMINISTIC_EVALUATOR),
    ("release_resources_to_sandbox", Phase.EXPERIMENT, ExecutorType.SANDBOX_RUNNER),
    ("resolve_manuscript_citations", Phase.PAPER, ExecutorType.DETERMINISTIC_SERVICE),
    ("freeze_synthesis_reference_set", Phase.PAPER, ExecutorType.DETERMINISTIC_SERVICE),
    (
        "draft_diagnostic_query_plan",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
    (
        "propose_repair_contract_change",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
    ),
)


def retrieval_step_definitions() -> list[StepDefinition]:
    """Workflow v2 contracts for every External Research V1 DAG node."""
    definitions: list[StepDefinition] = []
    seen: set[str] = set()
    for stage, step_types in EXTERNAL_RESEARCH_DAGS.items():
        phase = _PHASES[stage]
        for step_type in step_types:
            if step_type in seen:
                continue
            seen.add(step_type)
            definitions.append(
                StepDefinition(
                    step_type=step_type,
                    phase=phase,
                    executor_type=_EXECUTORS.get(
                        step_type, ExecutorType.DETERMINISTIC_SERVICE
                    ),
                    expected_output=(
                        "Immutable Retrieval Gateway artifact or an explicit "
                        "blocked/not-applicable result"
                    ),
                    scientific_failure_enters_diagnosis=stage
                    in {"experimentation", "synthesis", "repair"},
                )
            )
    for step_type, phase, executor in _LEGACY_DEFINITIONS:
        if step_type in seen:
            continue
        definitions.append(
            StepDefinition(
                step_type=step_type,
                phase=phase,
                executor_type=executor,
                expected_output=(
                    "Compatibility Retrieval Gateway artifact or explicit "
                    "blocked/not-implemented result"
                ),
                scientific_failure_enters_diagnosis=phase
                in {Phase.EXPERIMENT, Phase.PAPER},
            )
        )
    return definitions


CONNECTED_DISCOVERY_STEPS = {
    "draft_discovery_query_plan",
    "evaluate_network_policy",
    "sanitize_discovery_queries",
    "execute_discovery_retrieval",
    "normalize_external_resources",
    "deduplicate_external_resources",
    "verify_external_metadata",
    "build_discovery_source_set",
    "freeze_discovery_source_set",
}


UNCONNECTED_RETRIEVAL_STEPS = {
    item.step_type
    for item in retrieval_step_definitions()
    if item.step_type not in CONNECTED_DISCOVERY_STEPS
}


def append_external_research_dag(
    repository: WorkflowRepository,
    study_id: str,
    stage: str,
    *,
    depends_on: list[str] | None = None,
    run_key: str = "v1",
) -> list[StepInstance]:
    """Persist one idempotent phase DAG in the existing Workflow v2 scheduler."""
    if stage not in EXTERNAL_RESEARCH_DAGS:
        raise ValueError(f"unknown external research stage: {stage}")
    group = f"external_research:{stage}:{run_key}"
    existing = [
        item for item in repository.list_steps(study_id) if item.task_group == group
    ]
    if existing:
        return existing
    definitions = {item.step_type: item for item in retrieval_step_definitions()}
    for definition in definitions.values():
        repository.save_step_definition(definition)

    created: dict[str, StepInstance] = {}
    external_dependencies = list(dict.fromkeys(depends_on or []))

    def add(step_type: str, predecessors: list[str]) -> StepInstance:
        definition = definitions[step_type]
        step = repository.add_step(
            study_id,
            step_type,
            definition.phase,
            definition.executor_type,
            depends_on=[
                *(external_dependencies if not predecessors else []),
                *[
                    created[predecessor].step_instance_id
                    for predecessor in predecessors
                ],
            ],
            task_group=group,
            expected_output=definition.expected_output,
        )
        created[step_type] = step
        return step

    if stage == "discovery":
        add("plan_external_research", [])
        add("evaluate_retrieval_policy", ["plan_external_research"])
        add("sanitize_queries", ["plan_external_research"])
        search_predecessors = ["evaluate_retrieval_policy", "sanitize_queries"]
        for step_type in (
            "execute_academic_search",
            "execute_github_search",
            "execute_huggingface_search",
            "execute_official_web_search",
        ):
            add(step_type, search_predecessors)
        add(
            "normalize_resources",
            [
                "execute_academic_search",
                "execute_github_search",
                "execute_huggingface_search",
                "execute_official_web_search",
            ],
        )
        chain = (
            "build_identifier_graph",
            "deduplicate_resources",
            "verify_metadata",
            "build_resource_relation_graph",
            "resolve_open_access",
            "optionally_request_institution_access",
            "acquire_approved_documents",
            "build_paperqa_corpus",
            "analyze_discovery_evidence",
            "generate_coverage_report",
            "freeze_discovery_resource_set",
            "scope_review_gate",
        )
        predecessor = "normalize_resources"
        for step_type in chain:
            add(step_type, [predecessor])
            predecessor = step_type
    elif stage == "protocol":
        add("plan_protocol_retrieval", [])
        add("search_protocol_sources", ["plan_protocol_retrieval"])
        for step_type in (
            "resolve_baseline_code",
            "resolve_dataset_versions",
            "resolve_model_revisions",
            "search_official_documentation",
        ):
            add(step_type, ["search_protocol_sources"])
        add(
            "verify_licenses",
            [
                "resolve_baseline_code",
                "resolve_dataset_versions",
                "resolve_model_revisions",
                "search_official_documentation",
            ],
        )
        add("bind_resources_to_contract_fields", ["verify_licenses"])
        add(
            "freeze_protocol_resource_set",
            ["bind_resources_to_contract_fields"],
        )
    elif stage == "experimentation":
        add("resolve_contract_approved_resources", [])
        for step_type in (
            "fetch_pinned_code",
            "fetch_pinned_model",
            "fetch_pinned_dataset",
        ):
            add(step_type, ["resolve_contract_approved_resources"])
        add(
            "verify_hashes",
            ["fetch_pinned_code", "fetch_pinned_model", "fetch_pinned_dataset"],
        )
        add("release_to_sandbox", ["verify_hashes"])
    else:
        sequential_predecessor: str | None = None
        for step_type in EXTERNAL_RESEARCH_DAGS[stage]:
            add(
                step_type,
                [sequential_predecessor] if sequential_predecessor else [],
            )
            sequential_predecessor = step_type
    return list(created.values())


def append_external_research_loop(
    repository: WorkflowRepository,
    study_id: str,
    *,
    depends_on: list[str] | None = None,
    run_key: str = "v1",
    include_repair: bool = False,
) -> list[StepInstance]:
    """Persist the complete four-phase external-research loop.

    Each phase remains an ordinary idempotent Workflow v2 DAG. The first node
    of a downstream phase depends on the terminal node of the preceding phase,
    so the existing scheduler and owner gates retain authority. Repair is an
    optional cross-cutting branch and is never represented as a fifth research
    phase.
    """

    stages = ["discovery", "protocol", "experimentation", "synthesis"]
    if include_repair:
        stages.append("repair")
    all_steps: list[StepInstance] = []
    upstream_dependencies = list(dict.fromkeys(depends_on or []))
    for stage in stages:
        phase_steps = append_external_research_dag(
            repository,
            study_id,
            stage,
            depends_on=upstream_dependencies,
            run_key=run_key,
        )
        all_steps.extend(phase_steps)
        terminal_type = EXTERNAL_RESEARCH_DAGS[stage][-1]
        terminal = next(
            item for item in phase_steps if item.step_type == terminal_type
        )
        upstream_dependencies = [terminal.step_instance_id]
    return all_steps
