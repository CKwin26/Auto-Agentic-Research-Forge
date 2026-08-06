"""Canonical Workflow v2 acceptance setup for independent-group studies.

This adapter performs Profile-specific Stage 2/3 work but persists every
scientific object through the shared WorkflowRepository.  Manuscript creation
is deliberately delegated to the canonical Stage 4 DAG.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from ..models import utc_now
from ..profiles.stage_four_evidence import (
    build_stage_four_evidence_from_repository,
    queue_stage_four_from_profile_handoff,
)
from ..profile_figure_data import persist_profile_figure_data_bundle
from ..retrieval.domain.models import (
    ExternalResource,
    MetadataVerificationStatus,
    ResourceType,
    RetractionStatus,
    retrieval_id,
)
from ..retrieval.domain.repository import RetrievalRepository
from ..storage import sha256_file, write_json_atomic, write_text_atomic
from ..web_app import initialize_idea_research
from ..workflow_domain import (
    AnalysisEligibilityStatus,
    ArtifactRole,
    ArtifactStatus,
    ConfirmatoryStatus,
    EntryMode,
    EvaluationRecord,
    ExecutionAttempt,
    EvidenceChain,
    EvidenceChainLevel,
    EvidenceEdge,
    EvidenceRelation,
    EvidenceReproductionLevel,
    ExecutionStatus,
    ExecutorType,
    GateType,
    Hypothesis,
    HypothesisRole,
    HypothesisVerdict,
    HypothesisVerdictStatus,
    LiteratureSetVersion,
    Phase,
    ProtocolStatus,
    PublicationMode,
    QualificationStatus,
    ResearchContractVersion,
    ResearchRun,
    ResultEnvelope,
    RunCell,
    RunCellStatus,
    RunKind,
    RunPlan,
    ScientificClaimEnvelope,
    ScopeContractVersion,
    Stage3CompletionPackage,
    Stage3HandoffPackage,
    Stage3Profile,
    StepAcceptanceStatus,
    StudyVerdict,
    StudyVerdictStatus,
    WorkflowRepository,
    stable_id,
)
from .acceptance import _acceptance_plan
from .designs.independent_group import IndependentGroupComparison
from .paper_case import _rows
from .reference import recalculate_independent_group
from .schemas import AnalysisPlan


TITLE = (
    "Does Intervention B Improve the Primary Continuous Outcome Compared with "
    "Intervention A in Two Independent Groups? A Prespecified Controlled Study"
)

EQUIVALENCE_TITLE = (
    "Is the Assisted Procedure Equivalent to the Standard Procedure Within "
    "a Prespecified One-Point Margin? A Controlled Equivalence Study"
)

BAYESIAN_TITLE = (
    "Does the Assisted Procedure Improve Task Outcomes? A Controlled Study "
    "with Prespecified Bayesian Sensitivity Analysis"
)

MULTIPLICITY_TITLE = (
    "Can Quality and Completion Claims Survive Prespecified Family-Wise "
    "Error Control? A Two-Outcome Controlled Study"
)


# Real, stable methodology publications used only as background literature for
# the controlled acceptance study.  They never decide its scientific verdict.
_METHODOLOGY_LITERATURE: tuple[dict[str, Any], ...] = (
    {"doi": "10.1093/biomet/34.1-2.28", "title": "The Generalization of Student's Problem when Several Different Population Variances Are Involved", "authors": ["B. L. Welch"], "date": "1947"},
    {"doi": "10.3102/10769986006002107", "title": "Distribution Theory for Glass's Estimator of Effect Size and Related Estimators", "authors": ["Larry V. Hedges"], "date": "1981"},
    {"doi": "10.2307/4615733", "title": "A Simple Sequentially Rejective Multiple Test Procedure", "authors": ["Sture Holm"], "date": "1979"},
    {"doi": "10.1111/j.2517-6161.1995.tb02031.x", "title": "Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing", "authors": ["Yoav Benjamini", "Yosef Hochberg"], "date": "1995"},
    {"doi": "10.1093/biomet/63.3.581", "title": "Inference and Missing Data", "authors": ["Donald B. Rubin"], "date": "1976"},
    {"doi": "10.1177/1948550617697177", "title": "Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses", "authors": ["Daniël Lakens"], "date": "2017"},
    {"doi": "10.1007/BF01068419", "title": "A Comparison of the Two One-Sided Tests Procedure and the Power Approach for Assessing the Equivalence of Average Bioavailability", "authors": ["Donald J. Schuirmann"], "date": "1987"},
    {"doi": "10.1001/jama.2012.87802", "title": "Reporting of Noninferiority and Equivalence Randomized Trials: Extension of the CONSORT 2010 Statement", "authors": ["Gilda Piaggio", "Diana R. Elbourne", "Douglas G. Altman", "Stuart J. Pocock", "Stephen J. W. Evans"], "date": "2012"},
    {"doi": "10.1080/00031305.2016.1154108", "title": "The ASA Statement on p-Values: Context, Process, and Purpose", "authors": ["Ronald L. Wasserstein", "Nicole A. Lazar"], "date": "2016"},
    {"doi": "10.1136/bmj.c869", "title": "CONSORT 2010 Explanation and Elaboration: Updated Guidelines for Reporting Parallel Group Randomised Trials", "authors": ["David Moher", "Sally Hopewell", "Kenneth F. Schulz", "Victor Montori", "Peter C. Gøtzsche", "P. J. Devereaux", "Diana Elbourne", "Matthias Egger", "Douglas G. Altman"], "date": "2010"},
    {"doi": "10.1136/bmj.l4898", "title": "RoB 2: A Revised Tool for Assessing Risk of Bias in Randomised Trials", "authors": ["Jonathan A. C. Sterne", "Jelena Savović", "Matthew J. Page", "Roy G. Elbers", "Natalie S. Blencowe", "Isabelle Boutron", "Christopher J. Cates", "Heng Li", "Asbjørn W. L. Løberg", "James R. McAleenan", "Barnaby C. Reeves", "Sharon Shepperd", "Ian Shrier", "Lesley A. Stewart", "Katherine Tilling", "Ian R. White", "Peter F. Whiting", "Julian P. T. Higgins"], "date": "2019"},
    {"doi": "10.1037/a0029146", "title": "Bayesian Estimation Supersedes the t Test", "authors": ["John K. Kruschke"], "date": "2013"},
    {"doi": "10.1214/ss/1177011136", "title": "Inference from Iterative Simulation Using Multiple Sequences", "authors": ["Andrew Gelman", "Donald B. Rubin"], "date": "1992"},
    {"doi": "10.1136/bmj.311.7003.485", "title": "Absence of Evidence Is Not Evidence of Absence", "authors": ["Douglas G. Altman", "J. Martin Bland"], "date": "1995"},
    {"doi": "10.1007/s10654-016-0149-3", "title": "Statistical Tests, P Values, Confidence Intervals, and Power: A Guide to Misinterpretations", "authors": ["Sander Greenland", "Stephen J. Senn", "Kenneth J. Rothman", "John B. Carlin", "Charles Poole", "Steven N. Goodman", "Douglas G. Altman"], "date": "2016"},
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _complete_step(
    repository: WorkflowRepository,
    study_id: str,
    step_type: str,
    phase: Phase,
    executor: ExecutorType,
    result: dict[str, Any],
    *,
    depends_on: list[str] | None = None,
) -> str:
    existing = next(
        (item for item in repository.list_steps(study_id) if item.step_type == step_type),
        None,
    )
    step = existing or repository.add_step(
        study_id,
        step_type,
        phase,
        executor,
        depends_on=depends_on,
        expected_output=f"accepted {step_type} artifact",
    )
    if step.status is ExecutionStatus.SUCCEEDED:
        return step.step_instance_id
    repository.update_step(study_id, step.step_instance_id, ExecutionStatus.RUNNING)
    artifact = repository.save_step_result(study_id, step.step_instance_id, result)
    repository.update_step(
        study_id,
        step.step_instance_id,
        ExecutionStatus.SUCCEEDED,
        output_artifact_ids=[artifact.artifact_id],
        acceptance_status=StepAcceptanceStatus.ACCEPTED,
        output_produced=True,
        schema_validated=True,
        scientific_postcondition_passed=True,
        acceptance_checks={"output_present": True, "schema_valid": True},
    )
    return step.step_instance_id


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["subject_id", "arm", "quality", "completed"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _register_verified_methodology_literature(
    repository: WorkflowRepository, study_id: str
) -> LiteratureSetVersion:
    retrieval_repository = RetrievalRepository(repository.root)
    source_ids: list[str] = []
    for item in _METHODOLOGY_LITERATURE:
        doi = str(item["doi"]).lower()
        canonical = {
            "doi": doi,
            "title": item["title"],
            "authors": item["authors"],
            "publication_date": item["date"],
        }
        resource_id = retrieval_id("resource", "doi", doi)
        retrieval_repository.save_resource(
            ExternalResource(
                resource_id=resource_id,
                resource_type=ResourceType.PUBLICATION,
                canonical_identifier=f"doi:{doi}",
                title=str(item["title"]),
                authors_or_owners=list(item["authors"]),
                publication_or_release_date=str(item["date"]),
                doi=doi,
                url=f"https://doi.org/{doi}",
                providers=["frozen_acceptance_metadata"],
                metadata_verification_status=MetadataVerificationStatus.VERIFIED,
                retraction_or_correction_status=RetractionStatus.CLEAR,
                canonical_metadata_hash=_digest(canonical),
                metadata={
                    "authors": list(item["authors"]),
                    "verification_basis": "versioned DOI metadata acceptance fixture",
                    "evidence_role": "background_only",
                },
            )
        )
        source_ids.append(resource_id)
    return repository.save_literature_set(
        LiteratureSetVersion(
            literature_set_id="literature-independent-group-methods",
            study_id=study_id,
            version=1,
            status=ArtifactStatus.FROZEN,
            background_source_ids=source_ids,
            decision_source_ids=[],
            affects_novelty=False,
            affects_research_design=True,
        )
    )


def independent_group_acceptance_plan_rows(
    acceptance_variant: str = "superiority",
) -> tuple[AnalysisPlan, list[dict[str, Any]]]:
    """Return the exact frozen plan/data pair used by an acceptance variant."""

    if acceptance_variant not in {
        "superiority",
        "equivalence",
        "bayesian",
        "multiplicity",
    }:
        raise ValueError("unsupported independent-group acceptance variant")
    rows = _rows("normal")
    plan_payload = _acceptance_plan()
    if acceptance_variant == "equivalence":
        # The equivalence fixture must be generated by the same frozen rules
        # that appear in the Research Contract.  Reusing the superiority rows
        # and shifting one arm produced numerically plausible results but left
        # two incompatible procedure definitions in the evidence chain.
        # Preserve only the registered assignment and missingness pattern from
        # the base fixture; derive every observed outcome from the authoritative
        # within-arm rank rules below.
        missing_quality_ids = {
            str(row["subject_id"])
            for row in rows
            if row.get("quality") in {None, ""}
        }
        arm_members: dict[str, list[dict[str, Any]]] = {
            "control": [],
            "treatment": [],
        }
        for row in rows:
            arm_members[str(row["arm"])].append(row)
        for arm_id, arm_rows in arm_members.items():
            arm_rows.sort(key=lambda item: str(item["subject_id"]))
            for arm_rank, row in enumerate(arm_rows):
                quality_base = 50.0 if arm_id == "control" else 49.8
                row["quality"] = (
                    ""
                    if str(row["subject_id"]) in missing_quality_ids
                    else quality_base + 0.7 * (arm_rank % 10)
                )
                completion_divisor = 5 if arm_id == "control" else 8
                row["completed"] = arm_rank % completion_divisor != 0
        # AnalysisPlan is itself part of the frozen protocol.  Its inherited
        # superiority-fixture arm descriptions must be replaced as well; it is
        # not enough for the top-level contract and generated rows to agree.
        for arm in plan_payload["arms"]:
            if arm["arm_id"] == "control":
                arm["definition"] = (
                    "After sorting assigned case identifiers, return quality "
                    "50 + 0.7 times (within-arm rank modulo 10) and mark "
                    "completion unless within-arm rank is divisible by 5."
                )
            elif arm["arm_id"] == "treatment":
                arm["definition"] = (
                    "After sorting assigned case identifiers, return quality "
                    "49.8 + 0.7 times (within-arm rank modulo 10) and mark "
                    "completion unless within-arm rank is divisible by 8."
                )
        plan_payload["decision_rules"]["quality"] = {
            "mode": "equivalence",
            "effect_measure": "mean_difference",
            "beneficial_direction": "higher",
            "lower_margin": -1.0,
            "upper_margin": 1.0,
            "margin_unit": "software-fixture quality points",
            "margin_provenance": (
                "Owner-approved acceptance-fixture tolerance frozen before "
                "formal execution; one point is one percent of the bounded "
                "0-to-100 fixture scale."
            ),
            "owner_approved": True,
        }
    return AnalysisPlan.model_validate(plan_payload), rows


def prepare_independent_group_workflow_acceptance(
    output_root: str | Path,
    *,
    acceptance_variant: str = "superiority",
) -> dict[str, Any]:
    """Prepare a real Idea-to-paper Study through canonical Stage 4 intake."""

    if acceptance_variant not in {
        "superiority",
        "equivalence",
        "bayesian",
        "multiplicity",
    }:
        raise ValueError("unsupported independent-group acceptance variant")
    equivalence_variant = acceptance_variant == "equivalence"
    bayesian_variant = acceptance_variant == "bayesian"
    multiplicity_variant = acceptance_variant == "multiplicity"
    study_title = (
        EQUIVALENCE_TITLE
        if equivalence_variant
        else BAYESIAN_TITLE
        if bayesian_variant
        else MULTIPLICITY_TITLE
        if multiplicity_variant
        else TITLE
    )
    treatment_quality_base = 49.8 if equivalence_variant else 53.0
    quality_rule_text = (
        f"{treatment_quality_base:g} + 0.7 * (arm_rank modulo 10)"
    )

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    intake = initialize_idea_research(
        study_title,
        title=study_title,
        idea_root=root / "idea_runs",
    )
    repository = WorkflowRepository(intake["workflow_repository"])
    study_id = str(intake["study_id"])
    study = repository.load_study(study_id)
    if study.entry_mode is not EntryMode.IDEA_TO_PAPER:
        raise ValueError("formal acceptance must start from Idea-to-paper")
    repository.save_study(
        study.model_copy(
            update={"settings": {**study.settings, "manuscript_language": "en"}}
        ),
        "acceptance_manuscript_language_selected",
    )
    study = repository.load_study(study_id)
    study_root = repository.root / "studies" / study_id

    scope = ScopeContractVersion(
        study_id=study_id,
        version=1,
        direction=(
            "Controlled independent-group equivalence evaluation"
            if equivalence_variant
            else "Controlled independent-group evaluation with Bayesian sensitivity"
            if bayesian_variant
            else "Controlled multi-outcome evaluation with prespecified family-wise error control"
            if multiplicity_variant
            else "Controlled independent-group evaluation"
        ),
        research_question=(
            study_title.removesuffix(" A Controlled Equivalence Study")
            if equivalence_variant
            else study_title.removesuffix(
                " A Controlled Study with Prespecified Bayesian Sensitivity Analysis"
            )
            if bayesian_variant
            else (
                "Do the registered quality and completion findings remain "
                "interpretable after Holm correction across their frozen "
                "two-hypothesis confirmatory family?"
            )
            if multiplicity_variant
            else study_title.removesuffix(" A Prespecified Controlled Study")
        ),
        scope_in=[
            "two mutually exclusive independent groups",
            "one continuous primary outcome",
            "one binary secondary outcome",
            "prespecified complete-case denominators",
            *(
                [
                    "one frozen two-outcome confirmatory family",
                    "Holm family-wise error control with outcome-level adjusted decisions",
                ]
                if multiplicity_variant
                else []
            ),
        ],
        scope_out=[
            "repeated measures",
            "clustered assignment",
            "more than two arms",
            "causal generalization beyond the controlled fixture",
        ],
        candidate_contribution=(
            "A black-box acceptance case for prespecified equivalence and "
            "directional noninferiority inference in Research Forge."
            if equivalence_variant
            else (
                "A black-box acceptance case for a frozen, seeded, independently "
                "recomputable Beta-Binomial sensitivity analysis that cannot "
                "override the registered primary verdict."
            )
            if bayesian_variant
            else (
                "A black-box acceptance case for preserving a registered "
                "multi-outcome hypothesis family, Holm adjustment, and "
                "outcome-specific decisions from contract through paper."
            )
            if multiplicity_variant
            else (
                "A black-box acceptance case for an auditable independent-group "
                "Study Design Profile in Research Forge."
            )
        ),
        unit_of_analysis="independent software-task case",
        study_design="two independent groups",
        population_or_corpus="160 frozen deterministic software-task cases",
        primary_outcome="continuous software-fixture task-quality score",
        comparison="assisted deterministic procedure minus standard deterministic procedure",
        feasibility_basis=["versioned local fixture", "independent recalculator"],
        created_by="acceptance_case_designer",
    )
    repository.save_scope_contract(scope)
    literature = _register_verified_methodology_literature(repository, study_id)
    for intake_gate in repository.list_gates(study_id):
        if (
            intake_gate.gate_type is GateType.SCOPE_APPROVAL
            and intake_gate.status.value == "awaiting_user"
        ):
            repository.decide_gate(
                study_id,
                intake_gate.gate_id,
                approve=True,
                decided_by="profile_acceptance_owner",
                reason="Approve the Idea-to-paper intake scope for contract drafting.",
            )
    scope_gate = repository.create_gate(
        study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study_id}:scope:v1",
        subject_version=1,
    )
    repository.decide_gate(
        study_id,
        scope_gate.gate_id,
        approve=True,
        decided_by="profile_acceptance_owner",
        reason="Approve the fixed independent-group acceptance scope.",
    )
    frozen_scope = scope.model_copy(
        update={"status": ArtifactStatus.FROZEN, "frozen_at": utc_now()}
    )
    repository.save_scope_contract(frozen_scope)
    scope_step = _complete_step(
        repository,
        study_id,
        "scope_drafting",
        Phase.DISCOVERY,
        ExecutorType.CODEX,
        {"scope_version": 1, "status": "frozen", "owner_gate_id": scope_gate.gate_id},
    )
    freeze_scope_step = _complete_step(
        repository,
        study_id,
        "freeze_scope_contract",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        {"scope_version": 1, "sha256": sha256_file(study_root / "contracts" / "scope-v1.json")},
        depends_on=[scope_step],
    )
    _complete_step(
        repository,
        study_id,
        "freeze_literature_set",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        {
            "literature_set_id": literature.literature_set_id,
            "version": literature.version,
            "verified_source_count": len(literature.background_source_ids),
            "evidence_role": "background_only",
        },
        depends_on=[freeze_scope_step],
    )

    plan, rows = independent_group_acceptance_plan_rows(acceptance_variant)
    dataset_path = study_root / "stage3" / "resources" / "independent_groups.csv"
    _write_rows(dataset_path, rows)
    dataset_sha = sha256_file(dataset_path)
    missing_quality_ids = sorted(
        str(item["subject_id"])
        for item in rows
        if item.get("quality") in {None, ""}
    )
    fixture_spec_path = (
        study_root / "stage3" / "resources" / "task_and_scoring_specification.json"
    )
    fixture_spec = {
        "schema_version": 1,
        "fixture_role": "deterministic software-task acceptance study",
        "scientific_boundary": (
            "This fixture tests the research workflow and the registered "
            "between-group estimator. It does not represent people or a "
            "real-world intervention population."
        ),
        "task": (
            "Each independent case invokes one prespecified deterministic "
            "procedure once and records its returned quality score and whether "
            "the procedure returned a valid result within the fixed operation budget."
        ),
        "case_index": "The integer encoded in subject_id case-000 through case-159.",
        "arm_rank": (
            "The zero-based rank after sorting the 80 assigned case identifiers "
            "within each arm; it is frozen by the allocation ledger."
        ),
        "standard_procedure": {
            "quality_rule": "50 + 0.7 * (arm_rank modulo 10)",
            "completion_rule": "true unless arm_rank is divisible by 5",
        },
        "assisted_procedure": {
            "quality_rule": quality_rule_text,
            "completion_rule": "true unless arm_rank is divisible by 8",
        },
        "quality_outcome": {
            "scale": "continuous fixture points on a bounded 0 to 100 scale",
            "direction": "higher is better",
            "measurement_time": "immediately after the single procedure invocation",
            "construct_boundary": "software acceptance score, not a validated human trait",
        },
        "completion_outcome": {
            "scale": "binary",
            "event": "the procedure returned a valid result within the fixed operation budget",
            "measurement_time": "at the end of the single invocation",
        },
        "missing_quality_policy": {
            "reason": "prespecified integrity-dropout fixture cases",
            "subject_ids": missing_quality_ids,
            "handling": "outcome-specific complete-case analysis; never remove the cases from the registered population or missingness count",
        },
        "fidelity": {
            "network": "disabled",
            "procedure_invocations_per_case": 1,
            "allocation_seed": 20260804,
            "quality_rounding": "no pre-analysis rounding",
        },
    }
    write_json_atomic(fixture_spec_path, fixture_spec)
    fixture_spec_sha = sha256_file(fixture_spec_path)
    allocation_path = study_root / "stage3" / "resources" / "allocation_ledger.json"
    write_json_atomic(
        allocation_path,
        {
            "schema_version": 1,
            "mechanism": "fixed-seed shuffle without replacement",
            "seed": 20260804,
            "arm_counts": {"standard procedure": 80, "assisted procedure": 80},
            "assignments": [
                {"subject_id": str(item["subject_id"]), "arm": str(item["arm"])}
                for item in rows
            ],
        },
    )
    allocation_sha = sha256_file(allocation_path)
    qualification_step = _complete_step(
        repository,
        study_id,
        "study_design_qualification",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {
            "qualified": True,
            "study_design": "independent_group_comparison_v1",
            "known_limits": list(IndependentGroupComparison.descriptor.known_limits),
        },
        depends_on=[freeze_scope_step],
    )
    contract = ResearchContractVersion(
        study_id=study_id,
        version=1,
        scope_version=1,
        protocol_status=ProtocolStatus.FROZEN_EXECUTABLE,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary-quality",
                statement=(
                    (
                        "The assisted procedure is equivalent to the standard "
                        "procedure in mean task quality within the frozen "
                        "symmetric margin of minus one to plus one quality point."
                    )
                    if equivalence_variant
                    else (
                        "The assisted procedure increases mean task quality "
                        "after applying the frozen Holm procedure to the "
                        "registered quality and completion hypothesis family."
                    )
                    if multiplicity_variant
                    else (
                        "The assisted procedure increases mean task quality relative "
                        "to the standard procedure in the frozen acceptance population."
                    )
                ),
                role=HypothesisRole.PRIMARY,
                decision_rule=(
                    {
                        "mode": "equivalence",
                        "effect_measure": "mean_difference",
                        "lower_margin": -1.0,
                        "upper_margin": 1.0,
                        "margin_unit": "software-fixture quality points",
                        "supported_if": (
                            "the complete two-sided confidence interval lies "
                            "strictly within the frozen symmetric margins"
                        ),
                    }
                    if equivalence_variant
                    else {
                        "mode": "superiority",
                        "effect_measure": "mean_difference",
                        "supported_if": "two-sided confidence interval lower bound exceeds zero",
                    }
                ),
            )
        ],
        data_boundary={
            "population": "160 independently generated deterministic software-task cases",
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": fixture_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "unit": "one software-task case",
            "inclusion": "all 160 registered case identifiers",
            "exclusion": "only the five prespecified quality measurements are absent from the continuous-outcome denominator; no case leaves the registered population",
            "time_boundary": "one immediate post-invocation observation per case",
            "synthetic_fixture_disclosure": True,
        },
        metrics=[
            {
                "name": "Task quality",
                "direction": "higher",
                "denominator": "cases with an observed quality score in each arm",
                "role": "primary",
                "scale": "continuous software-fixture points from 0 to 100",
                "measurement_timing": "immediately after the single procedure invocation",
                "operational_definition": "the deterministic value returned by the arm-specific quality rule",
            },
            {
                "name": "Successful completion",
                "direction": "higher",
                "denominator": "all 160 registered cases by assigned arm",
                "role": "secondary",
                "scale": "binary",
                "event_definition": "a valid result returned within the fixed operation budget",
                "measurement_timing": "at the end of the single procedure invocation",
            },
        ],
        baseline={
            "arm_id": "control",
            "name": "standard deterministic procedure",
            "definition": "quality equals 50 + 0.7 times the within-arm rank modulo 10; completion is recorded unless the within-arm rank is divisible by 5",
        },
        treatment={
            "arm_id": "treatment",
            "name": "assisted deterministic procedure",
            "definition": (
                f"quality equals {treatment_quality_base:g} + 0.7 times the "
                "within-arm rank modulo 10; completion is recorded unless "
                "the within-arm rank is divisible by 8"
            ),
        },
        tasks=[
            "one deterministic software-task invocation per independently generated case, followed immediately by quality and completion recording"
        ],
        seeds=[20260804],
        splits=["formal"],
        replicates=1,
        runtime_binding={"entrypoint": "research_forge.study_design", "network": "disabled"},
        evaluator_policy={
            "authority": "deterministic study-design evaluator",
            "independent_recalculation_required": True,
            "quality_interval": "Welch unequal-variance confidence interval",
            "binary_interval": "registered risk-difference interval",
            "multiplicity_family": ["Task quality", "Successful completion"],
            "multiplicity_method": "Holm",
        },
        eligibility_rules=[
            {"rule": "each case belongs to exactly one arm"},
            {"rule": "each case has one observation time"},
            {"rule": "missing quality measurements remain counted in the population and missingness ledger"},
        ],
        experiment_profile=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
        domain_execution_profile={"id": "controlled_tabular_fixture_v1", "version": "1"},
        study_design={"id": "independent_group_comparison_v1", "version": "1"},
        inference_modules=(
            [
                {"id": "multiplicity_control_v1", "version": "1"},
                {"id": "noninferiority_equivalence_v1", "version": "1"},
            ]
            if equivalence_variant
            else [
                {"id": "multiplicity_control_v1", "version": "1"},
                {
                    "id": "bayesian_inference_v1",
                    "version": "1",
                    "extension": {
                        "prior": {"family": "Beta", "alpha": 1.0, "beta": 1.0},
                        "prior_provenance": (
                            "Owner-approved weakly informative uniform prior "
                            "frozen before formal execution"
                        ),
                        "informative": False,
                        "owner_approved": True,
                        "seed": 20260804,
                        "draws": 20000,
                        "rope": [-0.02, 0.02],
                        "decision_threshold": 0.90,
                        "role": "prespecified_sensitivity_only",
                    },
                },
            ]
            if bayesian_variant
            else [{"id": "multiplicity_control_v1", "version": "1"}]
        ),
        study_design_spec=plan.model_dump(mode="json"),
        output_schema={"type": "object", "required": ["outcomes", "primary_decision"]},
        statistical_rules={
            "confidence_level": 0.95,
            "primary_estimator": "Welch treatment-minus-control mean difference",
            "secondary_estimator": "treatment-minus-control risk difference",
            "multiplicity": "Holm adjustment across the two registered outcomes",
            "success_rule": (
                "the primary equivalence decision is supported only when the "
                "complete two-sided confidence interval lies strictly within "
                "the owner-approved minus-one to plus-one quality-point margins; "
                "non-significance on a superiority test is never sufficient"
                if equivalence_variant
                else "the Holm-adjusted primary superiority decision is supported only when its two-sided confidence interval excludes zero in the beneficial direction"
            ),
            "equivalence_margins": (
                {
                    "lower": -1.0,
                    "upper": 1.0,
                    "unit": "software-fixture quality points",
                    "owner_approved_before_results": True,
                    "directional_noninferiority_interpretation": (
                        "The lower bound above minus one establishes the "
                        "higher-is-better noninferiority side; equivalence "
                        "additionally requires the upper bound below plus one."
                    ),
                }
                if equivalence_variant
                else None
            ),
            "uncertainty_interpretation": (
                "The Welch interval summarizes dispersion across independent "
                "software-task cases under the registered estimator. In this "
                "deterministic acceptance fixture it validates the analysis "
                "pipeline and is not evidence for an external superpopulation."
            ),
            "bayesian_sensitivity": (
                {
                    "outcome": "Successful completion",
                    "model": "independent Beta-Binomial arm posteriors",
                    "contrast": "treatment completion probability minus control completion probability",
                    "prior": {"family": "Beta", "alpha": 1.0, "beta": 1.0},
                    "seed": 20260804,
                    "draws": 20000,
                    "credible_interval_probability": 0.95,
                    "rope": [-0.02, 0.02],
                    "supported_if": "posterior probability that the difference exceeds zero is at least 0.90",
                    "authority": "sensitivity only; cannot replace or rewrite the frozen primary verdict",
                }
                if bayesian_variant
                else None
            ),
        },
        estimand={
            "population": (
                "the 155 cases with observed quality under the prespecified "
                "outcome-specific complete-case policy; all 160 assigned cases "
                "remain in the population and missingness ledger"
            ),
            "outcome": "continuous software-fixture quality points",
            "contrast": "assisted deterministic procedure minus standard deterministic procedure mean",
            "unit": "quality points",
        },
        data_requirements={
            "dataset_sha256": dataset_sha,
            "task_and_scoring_specification_sha256": fixture_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
            "minimum_independent_units": 160,
        },
        implementation_requirements={
            "standard_quality_rule": "50 + 0.7 * (within_arm_rank modulo 10)",
            "assisted_quality_rule": quality_rule_text,
            "standard_completion_rule": "true unless within_arm_rank is divisible by 5",
            "assisted_completion_rule": "true unless within_arm_rank is divisible by 8",
            "invocations_per_case": 1,
            "replicate_definition": (
                "the contract value of one replicate means one invocation for "
                "each independent case, not one observation for an entire arm; "
                "there are 80 independent case invocations per arm"
            ),
            "measurement_timing": "immediate post-invocation",
            "allocation_seed": 20260804,
            "preanalysis_rounding": "none",
        },
        environment_requirements={
            "python": "current locked Research Forge environment",
            "network": "disabled",
            "external_state": "none",
        },
        resource_policy={
            "approved_dataset_sha256": dataset_sha,
            "approved_task_specification_sha256": fixture_spec_sha,
            "approved_allocation_ledger_sha256": allocation_sha,
        },
        created_by="deterministic_contract_completion",
    )
    repository.save_research_contract(contract)
    contract_step = _complete_step(
        repository,
        study_id,
        "research_contract_completion",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        {"contract_version": 1, "completion_issues": [], "complete": True},
        depends_on=[qualification_step],
    )
    contract_gate = repository.create_gate(
        study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study_id}:research-contract:v1",
        subject_version=1,
    )
    repository.decide_gate(
        study_id,
        contract_gate.gate_id,
        approve=True,
        decided_by="profile_acceptance_owner",
        reason="Approve the complete prespecified acceptance contract.",
    )
    frozen_contract = contract.model_copy(
        update={"status": ArtifactStatus.FROZEN, "frozen_at": utc_now()}
    )
    repository.save_research_contract(frozen_contract)
    freeze_contract_step = _complete_step(
        repository,
        study_id,
        "freeze_research_contract",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        {"contract_version": 1, "owner_gate_id": contract_gate.gate_id, "status": "frozen"},
        depends_on=[contract_step],
    )

    dataset_artifact = repository.register_artifact(
        study_id,
        str(dataset_path),
        dataset_sha,
        kind="study_design_dataset",
        role=ArtifactRole.OUTPUT,
    )
    fixture_spec_artifact = repository.register_artifact(
        study_id,
        str(fixture_spec_path),
        fixture_spec_sha,
        kind="task_and_scoring_specification",
        role=ArtifactRole.PROTOCOL,
    )
    allocation_artifact = repository.register_artifact(
        study_id,
        str(allocation_path),
        allocation_sha,
        kind="allocation_ledger",
        role=ArtifactRole.PROTOCOL,
    )
    resource_step = _complete_step(
        repository,
        study_id,
        "study_design_resource_binding",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
        {
            "dataset_artifact_id": dataset_artifact.artifact_id,
            "task_specification_artifact_id": fixture_spec_artifact.artifact_id,
            "allocation_ledger_artifact_id": allocation_artifact.artifact_id,
            "sha256": dataset_sha,
        },
        depends_on=[freeze_contract_step],
    )
    dry_rows = rows[:20]
    dry_issues = IndependentGroupComparison().validate_realized_data(plan, dry_rows)
    if dry_issues:
        raise ValueError("controlled dry run failed: " + "; ".join(dry_issues))
    dry_step = _complete_step(
        repository,
        study_id,
        "study_design_dry_run",
        Phase.EXPERIMENT,
        ExecutorType.SANDBOX_RUNNER,
        {"evidence_eligible": False, "rows": len(dry_rows), "schema_valid": True},
        depends_on=[resource_step],
    )

    lock_ids: dict[str, str] = {}
    locks_root = study_root / "stage3" / "locks"
    for name, value in {
        "protocol": frozen_contract.model_dump(mode="json"),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset_sha,
            "task_and_scoring_specification_sha256": fixture_spec_sha,
            "allocation_ledger_sha256": allocation_sha,
        },
        "evaluator": {"implementation": "independent_group_comparison_v1"},
        "environment": {"python": "locked current environment"},
        "analysis": plan.model_dump(mode="json"),
    }.items():
        path = locks_root / f"{name}.json"
        write_json_atomic(path, value)
        artifact = repository.register_artifact(
            study_id, str(path), sha256_file(path), kind=f"study_design_{name}_lock", role=ArtifactRole.PROTOCOL
        )
        lock_ids[name] = artifact.artifact_id
    manifest_path = study_root / "stage3" / "experiment_manifest.json"
    write_json_atomic(manifest_path, {"dataset_sha256": dataset_sha, "locks": lock_ids})
    manifest_sha = sha256_file(manifest_path)
    handoff_id = stable_id("stage3-handoff", study_id, dataset_sha, "independent-groups")
    handoff = repository.save_stage3_handoff(
        Stage3HandoffPackage(
            study_id=study_id,
            handoff_id=handoff_id,
            scope_version=1,
            contract_version=1,
            profile=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
            lock_artifact_ids=lock_ids,
            experiment_manifest_path=str(manifest_path),
            experiment_manifest_sha256=manifest_sha,
            execution_root=str(study_root / "stage3"),
            resource_artifact_ids=[
                dataset_artifact.artifact_id,
                fixture_spec_artifact.artifact_id,
                allocation_artifact.artifact_id,
            ],
        )
    )
    plan_id = stable_id("run-plan", study_id, handoff_id, manifest_sha)
    cells = []
    for position, arm_id in enumerate(("control", "treatment"), start=1):
        cells.append(
            RunCell(
                run_cell_id=stable_id("run-cell", plan_id, arm_id),
                study_id=study_id,
                plan_id=plan_id,
                contract_version=1,
                task_id="registered controlled task-quality assessment",
                split_id="formal",
                arm_id=arm_id,
                seed=20260804,
                replicate=1,
                action_id=f"action-independent-groups-{arm_id}",
                experiment_id=f"independent-groups-{arm_id}",
                input_bindings={"dataset": dataset_artifact.artifact_id},
                expected_output_schema=frozen_contract.output_schema,
                execution_manifest_hash=manifest_sha,
                status=RunCellStatus.SUCCEEDED,
                pair_position=position,
            )
        )
    run_plan_payload = {
        "schema_version": 1,
        "plan_id": plan_id,
        "study_id": study_id,
        "handoff_id": handoff_id,
        "contract_version": 1,
        "profile": Stage3Profile.EXISTING_PYTHON_PROJECT_V1.value,
        "compiler_version": "study-design-kernel-v1",
        "concurrency": 1,
        "cells": [item.model_dump(mode="json") for item in cells],
    }
    run_plan = repository.save_run_plan(
        RunPlan(**run_plan_payload, plan_hash=_digest(run_plan_payload))
    )
    run = repository.save_research_run(
        ResearchRun(
            run_id="run-independent-groups-formal-v1",
            study_id=study_id,
            contract_version=1,
            kind=RunKind.EXPERIMENTAL,
            status=ExecutionStatus.SUCCEEDED,
            output_artifact_ids=[dataset_artifact.artifact_id],
            completed_at=utc_now(),
        )
    )
    result_ids: list[str] = []
    code_hash = _digest(
        {
            "study_design": "independent_group_comparison_v1",
            "version": "1",
            "analysis_plan": plan.model_dump(mode="json"),
        }
    )
    environment_hash = _digest(
        {"python": "current locked Research Forge environment", "network": "disabled"}
    )
    for cell in cells:
        arm_rows = [item for item in rows if item["arm"] == cell.arm_id]
        arm_output_path = (
            study_root / "stage3" / "results" / f"{cell.arm_id}-result.json"
        )
        observed_quality = [
            float(item["quality"])
            for item in arm_rows
            if item.get("quality") not in {None, ""}
        ]
        write_json_atomic(
            arm_output_path,
            {
                "arm_id": cell.arm_id,
                "registered_subject_count": len(arm_rows),
                "observed_quality_count": len(observed_quality),
                "mean_quality": sum(observed_quality) / len(observed_quality),
                "subject_ids": [str(item["subject_id"]) for item in arm_rows],
            },
        )
        arm_artifact = repository.register_artifact(
            study_id,
            str(arm_output_path),
            sha256_file(arm_output_path),
            kind="independent_group_arm_result",
            role=ArtifactRole.OUTPUT,
        )
        attempt = repository.save_execution_attempt(
            ExecutionAttempt(
                attempt_id=stable_id("attempt", study_id, cell.run_cell_id, "1"),
                study_id=study_id,
                run_cell_id=cell.run_cell_id,
                attempt_number=1,
                status=RunCellStatus.SUCCEEDED,
                started_at=run.created_at,
                finished_at=run.completed_at,
                exit_status=0,
                input_hashes={
                    "dataset": dataset_sha,
                    "task_and_scoring_specification": fixture_spec_sha,
                    "allocation_ledger": allocation_sha,
                },
                environment_hash=environment_hash,
                code_hash=code_hash,
                output_artifact_ids=[arm_artifact.artifact_id],
                isolation_attestations={"network_disabled": True},
                canonical=True,
            )
        )
        result = repository.save_result_envelope(
            ResultEnvelope(
                result_id=stable_id("result", study_id, cell.run_cell_id, "1"),
                study_id=study_id,
                run_cell_id=cell.run_cell_id,
                attempt_id=attempt.attempt_id,
                output_artifact_ids=[arm_artifact.artifact_id],
                metrics={"mean_quality": sum(observed_quality) / len(observed_quality)},
                denominator=len(observed_quality),
                sample_ids=[str(item["subject_id"]) for item in arm_rows],
                analysis_rows=arm_rows,
            )
        )
        result_ids.append(result.result_id)
    design = IndependentGroupComparison()
    evaluation = design.evaluate(plan, rows)
    reference = recalculate_independent_group(plan, rows)
    evaluation_path = study_root / "stage3" / "study_design_evaluation.json"
    reference_path = study_root / "stage3" / "independent_recalculation.json"
    write_json_atomic(evaluation_path, evaluation)
    write_json_atomic(reference_path, reference)
    evaluation_artifact = repository.register_artifact(
        study_id, str(evaluation_path), sha256_file(evaluation_path), kind="study_design_evaluation", role=ArtifactRole.EVALUATION
    )
    reference_artifact = repository.register_artifact(
        study_id, str(reference_path), sha256_file(reference_path), kind="independent_recalculation", role=ArtifactRole.EVALUATION
    )
    primary = next(item for item in evaluation.outcomes if item.outcome_id == "quality")
    decision = HypothesisVerdictStatus(evaluation.primary_decision)
    outcome_labels = {
        item.outcome_id: item.label for item in plan.outcomes
    }
    evaluation_records: list[EvaluationRecord] = []
    evidence_edges: list[EvidenceEdge] = []
    bayesian_path: Path | None = None
    bayesian_artifact = None
    for outcome_result in evaluation.outcomes:
        statistic_key = (
            "mean" if outcome_result.kind == "continuous" else "proportion"
        )
        estimator_name = (
            "Welch mean difference"
            if outcome_result.kind == "continuous"
            else "Wald risk difference"
        )
        record = repository.save_evaluation_record(
            EvaluationRecord(
                evaluation_id=stable_id(
                    "evaluation",
                    study_id,
                    plan_id,
                    outcome_result.outcome_id,
                    sha256_file(evaluation_path),
                ),
                study_id=study_id,
                plan_id=plan_id,
                contract_version=1,
                qualification_status=QualificationStatus.QUALIFIED,
                qualification_checks=evaluation.qualification_checks,
                metric_name=outcome_labels[outcome_result.outcome_id],
                baseline_estimate=float(
                    outcome_result.arm_statistics["control"][statistic_key]
                ),
                treatment_estimate=float(
                    outcome_result.arm_statistics["treatment"][statistic_key]
                ),
                paired_effect=float(outcome_result.effect),
                pair_count=0,
                independent_unit_count=outcome_result.denominator,
                variance_unit="software-task case",
                aggregation_hierarchy=["software-task case", "assigned arm"],
                confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
                confidence_interval=outcome_result.confidence_interval,
                arm_estimates={
                    "standard procedure": float(
                        outcome_result.arm_statistics["control"][statistic_key]
                    ),
                    "assisted procedure": float(
                        outcome_result.arm_statistics["treatment"][statistic_key]
                    ),
                },
                statistical_rule={
                    "estimator": estimator_name,
                    "multiplicity": "Holm",
                    "raw_p_value": outcome_result.raw_p_value,
                    "adjusted_p_value": outcome_result.adjusted_p_value,
                    "decision_rule": plan.decision_rules[
                        outcome_result.outcome_id
                    ].model_dump(mode="json"),
                },
                decision=HypothesisVerdictStatus(outcome_result.decision),
                rationale=(
                    "Deterministic interval decision from the frozen Study "
                    "Design contract; all registered outcomes remain reportable."
                ),
                result_ids=result_ids,
            )
        )
        evaluation_records.append(record)
        evidence_edges.append(
            repository.save_evidence_edge(
                EvidenceEdge(
                    edge_id=stable_id(
                        "evidence-edge", dataset_artifact.artifact_id, record.evaluation_id
                    ),
                    study_id=study_id,
                    source_id=dataset_artifact.artifact_id,
                    target_id=record.evaluation_id,
                    relation_type=EvidenceRelation.EVALUATED_BY,
                    source_hash=dataset_sha,
                    target_hash=_digest(record.model_dump(mode="json")),
                    created_by="independent_group_comparison_v1",
                    qualification_status=QualificationStatus.QUALIFIED,
                )
            )
        )
    if bayesian_variant:
        from .inference.bayesian import beta_binomial_difference

        completion_result = next(
            item for item in evaluation.outcomes if item.outcome_id == "completion"
        )
        control_stats = completion_result.arm_statistics["control"]
        treatment_stats = completion_result.arm_statistics["treatment"]
        posterior = beta_binomial_difference(
            control_events=int(control_stats["events"]),
            control_n=int(control_stats["n"]),
            treatment_events=int(treatment_stats["events"]),
            treatment_n=int(treatment_stats["n"]),
            prior_alpha=1.0,
            prior_beta=1.0,
            seed=20260804,
            draws=20_000,
            rope=(-0.02, 0.02),
        )
        posterior_decision = (
            HypothesisVerdictStatus.SUPPORTED
            if posterior["probability_effect_above_zero"] >= 0.90
            else HypothesisVerdictStatus.INCONCLUSIVE
        )
        bayesian_path = study_root / "stage3" / "bayesian_sensitivity.json"
        write_json_atomic(
            bayesian_path,
            {
                **posterior,
                "outcome": "Successful completion",
                "decision_threshold": 0.90,
                "decision": posterior_decision.value,
                "authority": (
                    "prespecified sensitivity analysis; cannot replace or "
                    "rewrite the frozen primary verdict"
                ),
            },
        )
        bayesian_artifact = repository.register_artifact(
            study_id,
            str(bayesian_path),
            sha256_file(bayesian_path),
            kind="bayesian_sensitivity_evaluation",
            role=ArtifactRole.EVALUATION,
        )
        bayesian_record = repository.save_evaluation_record(
            EvaluationRecord(
                evaluation_id=stable_id(
                    "evaluation", study_id, plan_id, "bayesian-completion", sha256_file(bayesian_path)
                ),
                study_id=study_id,
                plan_id=plan_id,
                contract_version=1,
                qualification_status=QualificationStatus.QUALIFIED,
                qualification_checks={
                    "prior_frozen": True,
                    "seed_frozen": True,
                    "draw_count_frozen": True,
                    "sensitivity_role_frozen": True,
                },
                metric_name="Bayesian sensitivity: successful completion",
                baseline_estimate=float(control_stats["proportion"]),
                treatment_estimate=float(treatment_stats["proportion"]),
                paired_effect=float(posterior["posterior_mean_difference"]),
                pair_count=0,
                independent_unit_count=int(control_stats["n"] + treatment_stats["n"]),
                variance_unit="software-task case",
                aggregation_hierarchy=["software-task case", "assigned arm"],
                confirmatory_status=ConfirmatoryStatus.UNTOUCHED,
                confidence_interval=tuple(posterior["credible_interval_95"]),
                arm_estimates={
                    "standard posterior completion probability": (
                        control_stats["events"] + 1.0
                    ) / (control_stats["n"] + 2.0),
                    "assisted posterior completion probability": (
                        treatment_stats["events"] + 1.0
                    ) / (treatment_stats["n"] + 2.0),
                },
                contrast_estimates={"posterior_sensitivity": posterior},
                statistical_rule={
                    "model": "independent Beta-Binomial arm posteriors",
                    "interval_kind": "95% posterior credible interval",
                    "prior": posterior["prior"],
                    "seed": posterior["seed"],
                    "draws": posterior["draws"],
                    "rope": posterior["rope"],
                    "supported_if": "posterior probability above zero is at least 0.90",
                    "sensitivity_only": True,
                },
                decision=posterior_decision,
                rationale=(
                    "The frozen seeded Beta-Binomial sensitivity calculation "
                    "did not alter the registered frequentist primary verdict."
                ),
                result_ids=result_ids,
            )
        )
        evaluation_records.append(bayesian_record)
        evidence_edges.append(
            repository.save_evidence_edge(
                EvidenceEdge(
                    edge_id=stable_id(
                        "evidence-edge", dataset_artifact.artifact_id, bayesian_record.evaluation_id
                    ),
                    study_id=study_id,
                    source_id=dataset_artifact.artifact_id,
                    target_id=bayesian_record.evaluation_id,
                    relation_type=EvidenceRelation.EVALUATED_BY,
                    source_hash=dataset_sha,
                    target_hash=_digest(bayesian_record.model_dump(mode="json")),
                    created_by="bayesian_inference_v1",
                    qualification_status=QualificationStatus.QUALIFIED,
                )
            )
        )
    publication_profile_id = (
        "bayesian_inference_v1"
        if bayesian_variant
        else "noninferiority_equivalence_v1"
        if equivalence_variant
        else "multiplicity_control_v1"
        if acceptance_variant == "multiplicity"
        else "independent_group_comparison_v1"
    )
    figure_sources = [
        dataset_artifact,
        evaluation_artifact,
        reference_artifact,
    ]
    if bayesian_artifact is not None:
        figure_sources.append(bayesian_artifact)
    figure_bundle, figure_artifact = persist_profile_figure_data_bundle(
        repository,
        study_id,
        profile_id=publication_profile_id,
        evaluation=evaluation,
        research_contract_spec=plan,
        formal_rows=rows,
        source_artifacts=figure_sources,
        evaluation_records=evaluation_records,
    )
    if not figure_bundle.complete:
        raise ValueError(
            "formal Profile FigureData is incomplete: "
            + "; ".join(item.message for item in figure_bundle.blocking_issues)
        )
    envelope = design.produce_claim_envelope(plan, evaluation)
    allowed_claim = (
        (
            "In the frozen randomized software-task fixture, the complete Welch "
            "interval for the assisted-minus-standard mean quality difference "
            "lay within the prespecified minus-one to plus-one quality-point "
            "equivalence margins. This also satisfies the registered lower "
            "directional noninferiority bound, but does not establish superiority."
        )
        if equivalence_variant and evaluation.primary_decision == "supported"
        else (
            "In the frozen randomized software-task fixture, cases assigned to the "
            "assisted deterministic procedure had a higher mean software-fixture "
            "quality score than cases assigned to the standard deterministic procedure "
            "under the registered Welch comparison."
        )
        if evaluation.primary_decision == "supported"
        else (
            "The registered interval did not remain within both frozen "
            "equivalence margins."
            if equivalence_variant
            else "The registered comparison did not establish a directional difference in mean task quality."
        )
    )
    if bayesian_variant:
        allowed_claim += (
            " The prespecified Beta-Binomial sensitivity analysis estimated a "
            "posterior completion-probability difference using a frozen Beta(1,1) "
            "prior and seed; its probability of a positive difference did not "
            "reach the frozen 0.90 sensitivity threshold and therefore did not "
            "change the primary verdict."
        )
    if multiplicity_variant:
        allowed_claim += (
            " Both registered outcomes remained visible in the frozen family, "
            "and outcome-specific conclusions were reported after Holm "
            "adjustment rather than selected from unadjusted results."
        )
    claim = repository.save_claim_envelope(
        ScientificClaimEnvelope(
            claim_envelope_id=stable_id("claim-envelope", study_id, plan_id, evaluation.primary_decision),
            study_id=study_id,
            plan_id=plan_id,
            allowed_claim=allowed_claim,
            population="160 frozen independently generated software-task cases",
            tasks=[
                "one deterministic procedure invocation followed by immediate quality and completion recording"
            ],
            intervention=(
                f"assisted deterministic procedure: quality equals {treatment_quality_base:g} + 0.7 times "
                "the within-arm rank modulo 10; completion is recorded unless the rank "
                "is divisible by 8"
            ),
            comparator=(
                "standard deterministic procedure: quality equals 50 + 0.7 times "
                "the within-arm rank modulo 10; completion is recorded unless the rank "
                "is divisible by 5"
            ),
            outcome=(
                "continuous software-fixture quality points on a 0 to 100 scale, "
                "recorded immediately after the single invocation"
            ),
            effect_estimate=float(primary.effect),
            interval=primary.confidence_interval,
            evidence_level=EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED,
            confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
            known_limitations=[
                "This deterministic software acceptance fixture is not a human, clinical, or field study.",
                "The task-quality scale is a software-fixture score and has no validated external construct interpretation.",
                "Generalization is limited to the frozen case generator, allocation, procedure definitions, and scoring rules.",
                "Outcome-specific complete-case analysis may be biased under informative missingness.",
                *(
                    [
                        "The equivalence margins are owner-approved fixture tolerances, not externally validated clinical or practical thresholds.",
                        "Equivalence within the frozen margins does not establish superiority or exact equality.",
                    ]
                    if equivalence_variant
                    else []
                ),
                *(
                    [
                        "The Bayesian result is a prespecified binary-outcome sensitivity analysis and cannot replace the frozen primary verdict.",
                        "Posterior probabilities and credible intervals depend on the frozen Beta(1,1) prior, model, seed, draw count, and ROPE.",
                    ]
                    if bayesian_variant
                    else []
                ),
                *(
                    [
                        "The two-outcome family is a controlled acceptance fixture and does not establish performance for larger or adaptively selected families.",
                        "Holm control does not remove the need to interpret effect sizes, intervals, and outcome-specific denominators.",
                    ]
                    if multiplicity_variant
                    else []
                ),
            ],
            maximum_claim_tier=(
                "controlled_equivalence_within_frozen_margins"
                if equivalence_variant
                else "controlled_familywise_adjusted_multi_outcome_result"
                if multiplicity_variant
                else "controlled_between_group_difference"
            ),
            publication_mode=PublicationMode.RESULTS_MANUSCRIPT,
            prohibited_generalizations=envelope.prohibited_claims + [
                "universal effectiveness",
                "effects in repeated or clustered observations",
                "effects on people or real-world tasks",
                *(
                    ["superiority", "exact equality", "equivalence outside the frozen margins"]
                    if equivalence_variant
                    else []
                ),
                *(
                    [
                        "calling a posterior credible interval a confidence interval",
                        "using the Bayesian sensitivity result to overwrite the primary verdict",
                        "presenting a probability below the frozen threshold as support",
                    ]
                    if bayesian_variant
                    else []
                ),
            ],
        )
    )
    claim_path = study_root / "stage3" / "claim_envelopes" / f"{claim.claim_envelope_id}.json"
    claim_artifact = repository.register_artifact(
        study_id, str(claim_path), sha256_file(claim_path), kind="scientific_claim_envelope", role=ArtifactRole.CLAIM
    )
    contract_path = study_root / "contracts" / "research-v1.json"
    contract_artifact = repository.register_artifact(
        study_id, str(contract_path), sha256_file(contract_path), kind="research_contract", role=ArtifactRole.PROTOCOL
    )
    chain = repository.save_evidence_chain(
        EvidenceChain(
            chain_id=stable_id("chain", study_id, plan_id, "verified"),
            study_id=study_id,
            level=EvidenceChainLevel.VERIFIED,
            protocol_artifact_id=contract_artifact.artifact_id,
            run_artifact_ids=[
                dataset_artifact.artifact_id,
                fixture_spec_artifact.artifact_id,
                allocation_artifact.artifact_id,
            ],
            output_artifact_ids=[
                evaluation_artifact.artifact_id,
                reference_artifact.artifact_id,
                *(
                    [bayesian_artifact.artifact_id]
                    if bayesian_artifact is not None
                    else []
                ),
            ],
            evaluation_artifact_ids=[
                evaluation_artifact.artifact_id,
                *(
                    [bayesian_artifact.artifact_id]
                    if bayesian_artifact is not None
                    else []
                ),
            ],
            claim_artifact_ids=[claim_artifact.artifact_id],
            implementation_version="independent_group_comparison_v1",
            verified_checks={"dataset_hash": True, "reference_agreement": True, "claim_boundary": True},
        )
    )
    hypothesis_verdict = repository.save_hypothesis_verdict(
        HypothesisVerdict(
            verdict_id=stable_id("hverdict", study_id, "hypothesis-primary-quality", plan_id),
            study_id=study_id,
            hypothesis_id="hypothesis-primary-quality",
            status=decision,
            evidence_chain_ids=[chain.chain_id],
            eligible_evidence=True,
            rationale="The frozen deterministic decision rule was applied to verified evidence.",
        )
    )
    study_verdict_status = StudyVerdictStatus(evaluation.primary_decision)
    study_verdict = repository.save_study_verdict(
        StudyVerdict(
            verdict_id=stable_id("sverdict", study_id, plan_id, evaluation.primary_decision),
            study_id=study_id,
            status=study_verdict_status,
            hypothesis_verdict_ids=[hypothesis_verdict.verdict_id],
            rationale="The Study verdict equals the single registered primary hypothesis verdict.",
        )
    )
    completion = repository.save_stage3_completion(
        Stage3CompletionPackage(
            completion_id=stable_id("stage3-completion", study_id, run_plan.plan_hash, evaluation.primary_decision),
            study_id=study_id,
            plan_id=plan_id,
            handoff_id=handoff_id,
            evaluation_ids=[item.evaluation_id for item in evaluation_records],
            evidence_edge_ids=[item.edge_id for item in evidence_edges],
            hypothesis_verdict_ids=[hypothesis_verdict.verdict_id],
            study_verdict_id=study_verdict.verdict_id,
            qualification_status=QualificationStatus.QUALIFIED,
            artifact_hashes={
                str(evaluation_path.relative_to(study_root)).replace("\\", "/"): sha256_file(evaluation_path),
                str(reference_path.relative_to(study_root)).replace("\\", "/"): sha256_file(reference_path),
                str(fixture_spec_path.relative_to(study_root)).replace("\\", "/"): fixture_spec_sha,
                str(allocation_path.relative_to(study_root)).replace("\\", "/"): allocation_sha,
                **(
                    {
                        str(bayesian_path.relative_to(study_root)).replace("\\", "/"): sha256_file(bayesian_path)
                    }
                    if bayesian_path is not None
                    else {}
                ),
            },
            claim_envelope_id=claim.claim_envelope_id,
            evidence_level=EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED,
            confirmatory_status=ConfirmatoryStatus.CONFIRMATORY_USED,
            analysis_eligibility=AnalysisEligibilityStatus.QUALIFIED,
            scientific_verdict_status=study_verdict_status,
            publication_mode=PublicationMode.RESULTS_MANUSCRIPT,
        )
    )
    formal_step = _complete_step(
        repository, study_id, "study_design_formal_evaluation", Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {
            "evaluation_ids": [item.evaluation_id for item in evaluation_records],
            "run_id": run.run_id,
        },
        depends_on=[dry_step],
    )
    reference_step = _complete_step(
        repository, study_id, "study_design_independent_recalculation", Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"artifact_id": reference_artifact.artifact_id, "agreement": True},
        depends_on=[formal_step],
    )
    _complete_step(
        repository, study_id, "study_design_scientific_verdict", Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        {"verdict_id": study_verdict.verdict_id, "status": study_verdict.status.value},
        depends_on=[reference_step],
    )
    repository.save_study(
        repository.load_study(study_id).model_copy(
            update={"phase": Phase.PAPER, "execution_status": ExecutionStatus.QUEUED}
        ),
        "study_design_stage3_completed",
    )
    stage_four_handoff = build_stage_four_evidence_from_repository(repository, study_id)
    stage_four_steps = queue_stage_four_from_profile_handoff(repository, stage_four_handoff)
    return {
        "study_id": study_id,
        "project_id": study.project_id,
        "acceptance_variant": acceptance_variant,
        "workflow_repository": str(repository.root),
        "stage3_completion_id": completion.completion_id,
        "scientific_verdict": study_verdict.status.value,
        "stage_four_steps": len(stage_four_steps),
        "stage_four_queued": True,
    }


def run_independent_group_workflow_acceptance(
    root: Path,
    *,
    approve_owner_gates: bool = True,
    decided_by: str = "automated acceptance owner",
    max_cycles: int = 12,
    acceptance_variant: str = "superiority",
) -> dict[str, Any]:
    """Run the canonical Stage 4 DAG for the formal acceptance fixture.

    This is an acceptance harness, not a production bypass.  It starts from the
    same immutable Stage 1--3 objects as the UI, invokes the ordinary workflow
    handlers, and can explicitly approve only the five publication gates.  A
    scientific or evidence blocker is returned unchanged and is never approved
    away.
    """

    from ..workflow_scheduler import PersistentDAGScheduler, workflow_handlers
    from ..workflow_domain import GateStatus

    prepared = prepare_independent_group_workflow_acceptance(
        root,
        acceptance_variant=acceptance_variant,
    )
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = str(prepared["study_id"])
    publication_gate_types = {
        GateType.PUBLICATION_NARRATIVE,
        GateType.VISUAL_ARGUMENT,
        GateType.AUTHOR_VOICE,
        GateType.FINAL_SUBMISSION,
    }
    scheduler = PersistentDAGScheduler(
        repository,
        workflow_handlers(),
        max_concurrency=1,
        recover_interrupted=True,
    )
    approvals: list[str] = []
    snapshot: dict[str, Any] = {}
    for _ in range(max_cycles):
        snapshot = scheduler.run(study_id)
        waiting = [
            gate
            for gate in repository.list_gates(study_id)
            if gate.status is GateStatus.AWAITING_USER
            and gate.gate_type in publication_gate_types
        ]
        if approve_owner_gates and waiting:
            for gate in waiting:
                repository.decide_gate(
                    study_id,
                    gate.gate_id,
                    approve=True,
                    decided_by=decided_by,
                    reason=(
                        "Approved for the canonical black-box acceptance run; "
                        "scientific and evidence blockers remain non-overridable."
                    ),
                )
                approvals.append(gate.gate_id)
            continue
        break
    steps = repository.list_steps(study_id)
    return {
        **prepared,
        "owner_gate_approvals": approvals,
        "snapshot": snapshot,
        "step_status_counts": {
            status.value: sum(step.status is status for step in steps)
            for status in ExecutionStatus
        },
        "blocking_steps": [
            {
                "step_type": step.step_type,
                "status": step.status.value,
                "blocker": step.blocker,
            }
            for step in steps
            if step.status in {ExecutionStatus.BLOCKED, ExecutionStatus.FAILED}
        ],
    }


__all__ = [
    "EQUIVALENCE_TITLE",
    "TITLE",
    "independent_group_acceptance_plan_rows",
    "prepare_independent_group_workflow_acceptance",
    "run_independent_group_workflow_acceptance",
]
