from __future__ import annotations

"""Canonical evidence handoff from experiment Profiles to Stage 4.

Profiles own experimental semantics and deterministic evaluation.  They do not
own manuscript generation.  This adapter converts Profile-specific outputs into
the two publication contracts consumed by the canonical Stage 4 DAG:
``MandatoryReportingRegister`` and ``EvidenceClaimMap``.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ..models import StrictModel, utc_now
from ..paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
)
from ..paper_reporting_compliance import (
    MandatoryReportingItem,
    MandatoryReportingRegister,
)
from ..storage import sha256_file, write_json_atomic


STAGE_FOUR_EVIDENCE_ADAPTER_ID = "stage-four-evidence-handoff-v1"
STAGE_FOUR_EVIDENCE_ARTIFACT_NAME = "stage_four_evidence_handoff_v1"


class ProfileEvidencePointer(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    json_path: str | None = None


class ProfileReportingFinding(StrictModel):
    claim_id: str = Field(min_length=3, max_length=200)
    reporting_item_id: str = Field(pattern=r"^report-[a-z0-9-]{2,100}$")
    category: Literal[
        "primary", "secondary", "negative", "safety", "limitation", "operational"
    ]
    statement: str = Field(min_length=3, max_length=10_000)
    evidence: list[ProfileEvidencePointer] = Field(default_factory=list)
    required_destination: Literal[
        "main_text", "limitations", "supplement", "results_registry", "completion_package"
    ]
    destination_section: str | None = None
    allowed_sections: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=10, max_length=2_000)
    claim_strength: Literal[
        "descriptive", "associational", "comparative", "causal", "limitation"
    ] = "descriptive"
    material: bool = True
    preregistered: bool = False
    evidence_facets: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def material_scientific_finding_requires_evidence(
        self,
    ) -> "ProfileReportingFinding":
        if self.material and self.category != "limitation" and not self.evidence:
            raise ValueError(
                "material Profile findings require a hash-bound evidence pointer"
            )
        if self.required_destination in {"main_text", "limitations"} and not self.destination_section:
            raise ValueError("main-text Profile findings require a destination section")
        if len(self.evidence_facets) != len(set(self.evidence_facets)):
            raise ValueError("Profile evidence facets must be unique per finding")
        if any(not facet.strip() for facet in self.evidence_facets):
            raise ValueError("Profile evidence facets cannot be blank")
        if len(self.allowed_sections) != len(set(self.allowed_sections)):
            raise ValueError("Profile allowed sections must be unique")
        if any(not section.strip() for section in self.allowed_sections):
            raise ValueError("Profile allowed sections cannot be blank")
        return self


class ProfileStageFourEvidenceInput(StrictModel):
    study_id: str
    profile_id: str
    profile_version: str
    source_claim_envelope_id: str
    frozen_conclusion: str = Field(min_length=3)
    findings: list[ProfileReportingFinding] = Field(min_length=1)
    required_evidence_facets: list[str] = Field(default_factory=list)
    verified_source_ids: list[str] = Field(default_factory=list)
    forbidden_moves: list[str] = Field(
        default_factory=lambda: [
            "invent an unregistered result, statistic, citation, or experiment",
            "change a frozen numeric value or scientific verdict",
            "upgrade comparative evidence to an unauthorized causal claim",
            "omit a material registered result because it weakens the narrative",
        ]
    )

    @model_validator(mode="after")
    def requires_primary_result_and_limitation(
        self,
    ) -> "ProfileStageFourEvidenceInput":
        categories = {item.category for item in self.findings}
        if "primary" not in categories:
            raise ValueError("Stage 4 evidence handoff requires a primary finding")
        if "limitation" not in categories:
            raise ValueError("Stage 4 evidence handoff requires a limitation")
        claim_ids = [item.claim_id for item in self.findings]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Profile Stage 4 claim IDs must be unique")
        if len(self.required_evidence_facets) != len(
            set(self.required_evidence_facets)
        ):
            raise ValueError("required publication evidence facets must be unique")
        if any(not facet.strip() for facet in self.required_evidence_facets):
            raise ValueError("required publication evidence facets cannot be blank")
        return self


class StageFourEvidenceHandoff(StrictModel):
    schema_version: int = 1
    adapter_id: str = STAGE_FOUR_EVIDENCE_ADAPTER_ID
    study_id: str
    profile_id: str
    profile_version: str
    created_at: str = Field(default_factory=utc_now)
    mandatory_reporting_register: MandatoryReportingRegister
    evidence_claim_map: EvidenceClaimMap
    required_claim_count: int = Field(ge=1)
    bound_claim_count: int = Field(ge=0)
    claim_binding_coverage: float = Field(ge=0.0, le=1.0)
    required_evidence_facets: list[str] = Field(default_factory=list)
    covered_evidence_facets: list[str] = Field(default_factory=list)
    missing_evidence_facets: list[str] = Field(default_factory=list)
    publication_evidence_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    adapter_complete: bool
    manuscript_generated_by_profile: Literal[False] = False
    formal_manuscript_authority: Literal["canonical_stage_four_dag"] = (
        "canonical_stage_four_dag"
    )


def _registry_digest(value: ProfileStageFourEvidenceInput) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_stage_four_evidence_handoff(
    value: ProfileStageFourEvidenceInput,
) -> StageFourEvidenceHandoff:
    reporting_items: list[MandatoryReportingItem] = []
    bindings: list[EvidenceClaimBinding] = []
    for finding in value.findings:
        reporting_items.append(
            MandatoryReportingItem(
                reporting_item_id=finding.reporting_item_id,
                claim_id=finding.claim_id,
                category=finding.category,
                material=finding.material,
                preregistered=finding.preregistered,
                required_destination=finding.required_destination,
                destination_section=finding.destination_section,
                rationale=finding.rationale,
            )
        )
        pointers = [
            EvidencePointer(
                path=item.path,
                sha256=item.sha256,
                json_path=item.json_path,
                evidence_type="project_artifact",
            )
            for item in finding.evidence
        ]
        bindings.append(
            EvidenceClaimBinding(
                claim_id=finding.claim_id,
                kind=(
                    "result_metric"
                    if finding.category in {"primary", "secondary"}
                    else finding.category
                ),
                statement=finding.statement,
                evidence=pointers,
                evidence_facets=finding.evidence_facets,
                allowed_sections=(
                    finding.allowed_sections
                    or (
                        [finding.destination_section]
                        if finding.destination_section
                        else [finding.required_destination]
                    )
                ),
                claim_strength=finding.claim_strength,
                evidence_status=(
                    "bound"
                    if pointers
                    else "context_only"
                    if finding.category == "limitation"
                    else "unsupported"
                ),
            )
        )
    register = MandatoryReportingRegister(
        study_id=value.study_id,
        source_claim_envelope_id=value.source_claim_envelope_id,
        items=reporting_items,
        frozen=True,
        frozen_at=utc_now(),
    )
    evidence_map = EvidenceClaimMap(
        track_id=f"profile:{value.profile_id}:{value.study_id}",
        frozen_conclusion=value.frozen_conclusion,
        bindings=bindings,
        verified_source_ids=value.verified_source_ids,
        forbidden_moves=value.forbidden_moves,
        source_registry_sha256=_registry_digest(value),
    )
    required = register.required_claim_ids()
    bound = {
        item.claim_id
        for item in bindings
        if item.evidence_status == "bound"
        or (
            item.evidence_status == "context_only"
            and item.claim_strength == "limitation"
        )
    }
    covered = required & bound
    coverage = len(covered) / len(required)
    required_facets = set(value.required_evidence_facets)
    covered_facets = {
        facet
        for finding in value.findings
        if finding.claim_id in bound
        for facet in finding.evidence_facets
    }
    missing_facets = required_facets - covered_facets
    facet_coverage = (
        len(required_facets & covered_facets) / len(required_facets)
        if required_facets
        else 1.0
    )
    return StageFourEvidenceHandoff(
        study_id=value.study_id,
        profile_id=value.profile_id,
        profile_version=value.profile_version,
        mandatory_reporting_register=register,
        evidence_claim_map=evidence_map,
        required_claim_count=len(required),
        bound_claim_count=len(covered),
        claim_binding_coverage=coverage,
        required_evidence_facets=sorted(required_facets),
        covered_evidence_facets=sorted(covered_facets),
        missing_evidence_facets=sorted(missing_facets),
        publication_evidence_coverage=facet_coverage,
        adapter_complete=coverage == 1.0 and not missing_facets,
    )


def _reader_label(value: Any) -> str:
    """Render a frozen contract token as a readable label without changing it."""

    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "not specified"
    if isinstance(value, (list, tuple)):
        return ", ".join(_reader_label(item) for item in value) or "none"
    if isinstance(value, dict):
        return "; ".join(
            f"{str(key).replace('_', ' ')}: {_reader_label(item)}"
            for key, item in value.items()
        ) or "none"
    return str(value).replace("_", " ")


def _scientific_status_label(value: Any) -> str:
    """Translate internal lifecycle enums into publication-safe prose."""

    token = getattr(value, "value", value)
    labels = {
        "qualified": "eligible for the prespecified analysis",
        "disqualified": "ineligible for the prespecified analysis",
        "confirmatory_used": "included in the prespecified confirmatory analysis",
        "adaptive_reuse": "reported as an adaptive reuse analysis",
        "exhausted": "not eligible for confirmatory interpretation",
        "descriptive_only": "reported descriptively",
    }
    return labels.get(str(token), _reader_label(token))


def _contract_publication_statement(contract: Any) -> str:
    """Project the frozen executable design into manuscript-safe Methods prose.

    This is deliberately generic: experiment Profiles supply their semantics in
    the Research Contract, while Stage 4 receives one common evidence binding.
    The exact contract remains the authority and this sentence is only its
    reader-facing projection.
    """

    metrics = list(contract.metrics or [])
    metric_text = "; ".join(_reader_label(item) for item in metrics) or "not specified"
    hypothesis_text = "; ".join(
        (
            f"{item.statement} Decision rule: "
            f"{_reader_label(item.decision_rule)}"
        )
        for item in contract.hypotheses
    ) or "not specified"
    design_text = (
        _reader_label(contract.study_design)
        if getattr(contract, "study_design", None)
        else "the design bundled with the execution profile"
    )
    inference_text = (
        _reader_label(contract.inference_modules)
        if getattr(contract, "inference_modules", None)
        else "the inference bundled with the execution profile"
    )
    implementation_requirements = _reader_label(
        contract.implementation_requirements
    )
    design_parameters = _reader_label(
        getattr(contract, "study_design_spec", None) or {}
    )
    replicate_definition = None
    if isinstance(contract.implementation_requirements, dict):
        replicate_definition = contract.implementation_requirements.get(
            "replicate_definition"
        )
    replicate_text = (
        _reader_label(replicate_definition)
        if replicate_definition
        else f"{contract.replicates} registered replicate(s) per arm"
    )
    return (
        f"The frozen domain execution profile was {_reader_label(contract.experiment_profile)}. "
        f"The frozen study design was {design_text}, with inference specified as {inference_text}. "
        f"The primary hypothesis and rule were: {hypothesis_text}. "
        f"The comparison arms were the comparator ({_reader_label(contract.baseline)}) "
        f"and the intervention ({_reader_label(contract.treatment)}). "
        f"The registered metric specification was: {metric_text}. "
        f"The registered tasks were {_reader_label(contract.tasks)}, the splits were "
        f"{_reader_label(contract.splits)}, the seeds were {_reader_label(contract.seeds)}, "
        f"and the replicate specification was: {replicate_text}. "
        f"The estimand was specified as: {_reader_label(contract.estimand)}. "
        f"The statistical rules were: {_reader_label(contract.statistical_rules)}. "
        f"The data boundary was: {_reader_label(contract.data_boundary)}. "
        f"The only authorized implementation differences were: "
        f"{implementation_requirements}. "
        f"The registered data requirements were: "
        f"{_reader_label(contract.data_requirements)}. "
        f"The frozen design-specific parameters were: {design_parameters}. "
        f"The Profile parameters were: {_reader_label(contract.profile_parameters)}."
    )


def persist_stage_four_evidence_handoff(
    root: Path,
    handoff: StageFourEvidenceHandoff,
    *,
    artifact_version: int | None = None,
) -> dict[str, str]:
    """Write the immutable Profile-to-Stage-4 contracts, never a manuscript."""

    root.mkdir(parents=True, exist_ok=True)
    suffix = "" if artifact_version is None else f"_v{artifact_version}"
    paths = {
        "handoff": root / f"stage_four_evidence_handoff{suffix}.json",
        "mandatory_reporting_register": root
        / f"mandatory_reporting_register{suffix}.json",
        "evidence_claim_map": root / f"evidence_claim_map{suffix}.json",
    }
    write_json_atomic(paths["handoff"], handoff)
    write_json_atomic(
        paths["mandatory_reporting_register"], handoff.mandatory_reporting_register
    )
    write_json_atomic(paths["evidence_claim_map"], handoff.evidence_claim_map)
    return {name: sha256_file(path) for name, path in paths.items()}


def build_stage_four_evidence_from_repository(
    repository: Any,
    study_id: str,
) -> StageFourEvidenceHandoff:
    """Project any canonical Stage 3 completion into the common Stage 4 input.

    Profile-specific code has already ended when this function runs.  Only the
    immutable Stage 3 completion, claim envelope, and evaluation records are
    inspected, so Stage 4 never needs a switch statement for experiment type.
    """

    completions = repository.list_stage3_completions(study_id)
    if not completions:
        raise ValueError("a canonical Stage 3 completion is required")
    completion = completions[-1]
    if completion.claim_envelope_id is None:
        raise ValueError("Stage 3 completion has no scientific claim envelope")
    envelopes = {
        item.claim_envelope_id: item
        for item in repository.list_claim_envelopes(study_id)
    }
    envelope = envelopes.get(completion.claim_envelope_id)
    if envelope is None:
        raise ValueError("the completion's scientific claim envelope is missing")
    stage3_handoff = repository.load_stage3_handoff(study_id)
    profile = stage3_handoff.profile
    profile_id = profile.value
    try:
        from .registry import profile_bundle

        profile_version = (
            profile_bundle(profile).profile_version
        )
    except (KeyError, ValueError):
        profile_version = "unregistered-compatibility"

    study_root = repository.root / "studies" / study_id
    envelope_relative = (
        f"stage3/claim_envelopes/{envelope.claim_envelope_id}.json"
    )
    envelope_path = study_root / envelope_relative
    envelope_pointer = ProfileEvidencePointer(
        path=envelope_relative,
        sha256=sha256_file(envelope_path),
    )
    contract = repository.latest_research_contract(study_id)
    if contract is None:
        raise ValueError("a frozen Research Contract is required for Stage 4")
    contract_relative = f"contracts/research-v{contract.version}.json"
    contract_path = study_root / contract_relative
    if not contract_path.is_file():
        raise ValueError("the frozen Research Contract artifact is missing")
    contract_pointer = ProfileEvidencePointer(
        path=contract_relative,
        sha256=sha256_file(contract_path),
    )
    findings: list[ProfileReportingFinding] = [
        ProfileReportingFinding(
            claim_id=f"{envelope.claim_envelope_id}:primary",
            reporting_item_id="report-primary-outcome",
            category="primary",
            statement=envelope.allowed_claim,
            evidence=[envelope_pointer],
            required_destination="main_text",
            destination_section="results",
            allowed_sections=["abstract", "results", "discussion", "conclusion"],
            rationale="The immutable Stage 3 claim envelope is the only primary scientific claim authority available to Stage 4.",
            claim_strength="comparative",
            preregistered=(envelope.confirmatory_status.value == "confirmatory_used"),
            evidence_facets=["primary_decision"],
        ),
        ProfileReportingFinding(
            claim_id=f"{envelope.claim_envelope_id}:design-boundary",
            reporting_item_id="report-design-boundary",
            category="operational",
            statement=(
                f"The registered population was {envelope.population}; the intervention was "
                f"{envelope.intervention}, the comparator was {envelope.comparator}, the "
                f"outcome was {envelope.outcome}, and the registered tasks were "
                + ", ".join(envelope.tasks)
                + "."
            ),
            evidence=[envelope_pointer],
            required_destination="main_text",
            destination_section="methods",
            allowed_sections=["abstract", "introduction", "methods", "limitations"],
            rationale="Population, task, arm and outcome boundaries are required to interpret the frozen result without broadening it.",
        ),
        ProfileReportingFinding(
            claim_id=f"research-v{contract.version}:registered-protocol",
            reporting_item_id="report-registered-protocol",
            category="operational",
            statement=_contract_publication_statement(contract),
            evidence=[contract_pointer],
            required_destination="main_text",
            destination_section="methods",
            allowed_sections=["methods", "limitations", "supplement"],
            rationale=(
                "The frozen Research Contract supplies the executable design, "
                "Profile parameters, estimand, sampling matrix, and decision rule "
                "needed to interpret and reproduce the registered comparison."
            ),
            evidence_facets=[
                "operational_protocol",
                "sampling_matrix",
                "metric_definition",
                "decision_rule",
                "arm_definition",
            ],
            preregistered=True,
        ),
    ]
    evaluations = {
        item.evaluation_id: item
        for item in repository.list_evaluation_records(study_id)
        if item.evaluation_id in completion.evaluation_ids
    }
    if set(evaluations) != set(completion.evaluation_ids):
        raise ValueError("one or more Stage 3 evaluation records are missing")
    for index, evaluation_id in enumerate(completion.evaluation_ids, start=1):
        evaluation = evaluations[evaluation_id]
        relative = f"stage3/evaluations/{evaluation.evaluation_id}.json"
        pointer = ProfileEvidencePointer(
            path=relative,
            sha256=sha256_file(study_root / relative),
        )
        interval = (
            "not estimated"
            if evaluation.confidence_interval is None
            else (
                f"{evaluation.confidence_interval[0]:.12g} to "
                f"{evaluation.confidence_interval[1]:.12g}"
            )
        )
        interval_kind = str(
            (evaluation.statistical_rule or {}).get("interval_kind")
            or "confidence interval"
        ).strip()
        arm_estimates = dict(evaluation.arm_estimates)
        if not arm_estimates:
            if evaluation.baseline_estimate is not None:
                arm_estimates["baseline"] = evaluation.baseline_estimate
            if evaluation.treatment_estimate is not None:
                arm_estimates["treatment"] = evaluation.treatment_estimate
        arm_text = ", ".join(
            f"{name}={estimate:.12g}"
            for name, estimate in sorted(arm_estimates.items())
        ) or "no qualified arm estimate"
        if evaluation.pair_count > 0:
            denominator_text = (
                f"the registered paired comparison count was {evaluation.pair_count} "
                f"and the independent-unit count was {evaluation.independent_unit_count}"
            )
        else:
            denominator_text = (
                f"the registered independent-unit count was "
                f"{evaluation.independent_unit_count}"
            )
        findings.append(
            ProfileReportingFinding(
                claim_id=f"evaluation:{evaluation.evaluation_id}:result",
                reporting_item_id=f"report-evaluation-{index}-result",
                category="secondary",
                statement=(
                    f"For {evaluation.metric_name}, the frozen arm estimates were "
                    f"{arm_text}; the estimated contrast was {evaluation.paired_effect}, "
                    f"the {interval_kind} was {interval}, {denominator_text}, "
                    f"and the decision was "
                    f"{evaluation.decision.value}."
                ),
                evidence=[pointer],
                required_destination="main_text",
                destination_section="results",
                allowed_sections=["abstract", "results", "discussion", "conclusion"],
                rationale="Every evaluation named by the immutable Stage 3 completion must be reported with arm estimates, effect, uncertainty, denominator and decision.",
                claim_strength="comparative",
                preregistered=True,
                evidence_facets=[
                    "primary_result",
                    "uncertainty",
                    "denominator_reconciliation",
                ],
            )
        )
        findings.append(
            ProfileReportingFinding(
                claim_id=f"evaluation:{evaluation.evaluation_id}:method",
                reporting_item_id=f"report-evaluation-{index}-method",
                category="operational",
                statement=(
                    f"Evaluation {index} used variance unit {evaluation.variance_unit}, "
                    f"aggregation hierarchy {_reader_label(evaluation.aggregation_hierarchy or ['none'])}. "
                    f"The frozen estimator and uncertainty details were "
                    f"{_reader_label(evaluation.statistical_rule)}; the frozen contrast "
                    f"details were {_reader_label(evaluation.contrast_estimates)}. "
                    f"It was {_scientific_status_label(evaluation.qualification_status)} "
                    f"and {_scientific_status_label(evaluation.confirmatory_status)}, and "
                    f"excluded {len(evaluation.excluded_run_cell_ids)} run cells."
                ),
                evidence=[pointer],
                required_destination="main_text",
                destination_section="methods",
                allowed_sections=["methods", "limitations"],
                rationale="Qualification, variance unit, exclusions and aggregation are required for reproducible interpretation of the registered estimator.",
                evidence_facets=["analysis_method", "sample_flow"],
            )
        )
    limitations = envelope.known_limitations or [
        "The result is bounded to the registered population, tasks, implementation, evaluator, and execution environment."
    ]
    for index, limitation in enumerate(limitations, start=1):
        findings.append(
            ProfileReportingFinding(
                claim_id=f"{envelope.claim_envelope_id}:limitation-{index}",
                reporting_item_id=f"report-limitation-{index}",
                category="limitation",
                statement=str(limitation),
                evidence=[envelope_pointer],
                required_destination="limitations",
                destination_section="limitations",
                allowed_sections=["discussion", "limitations", "conclusion"],
                rationale=(
                    str(limitation)
                    if len(str(limitation)) >= 10
                    else "This frozen limitation must remain visible in the manuscript."
                ),
                claim_strength="limitation",
            )
        )
    return build_stage_four_evidence_handoff(
        ProfileStageFourEvidenceInput(
            study_id=study_id,
            profile_id=profile_id,
            profile_version=profile_version,
            source_claim_envelope_id=envelope.claim_envelope_id,
            frozen_conclusion=envelope.allowed_claim,
            findings=findings,
        )
    )


def queue_stage_four_from_profile_handoff(
    repository: Any,
    handoff: StageFourEvidenceHandoff,
    *,
    venue_policy_id: str | None = None,
    artifact_version: int = 1,
) -> list[Any]:
    """Attach a verified handoff to a canonical Stage 3 completion and queue Stage 4.

    The Stage 3 claim envelope remains scientific authority.  The adapter only
    expands the mandatory reporting surface; it cannot create a completion or a
    verdict when the canonical Stage 3 package is absent.
    """

    if not handoff.adapter_complete:
        raise ValueError("incomplete Profile evidence cannot enter Stage 4")
    if artifact_version < 1:
        raise ValueError("Stage 4 handoff artifact version must be positive")
    from ..paper_venue_policy import GENERIC_JOURNAL_POLICY
    from ..stage_four import (
        ensure_stage_four_dag,
        persist_stage_four_artifact,
        stage4_claim_authority,
    )
    from ..workflow_domain import ArtifactRole

    authority = stage4_claim_authority(repository, handoff.study_id)
    expected_envelope = str(authority["claims"][0]["claim_envelope_id"])
    if handoff.mandatory_reporting_register.source_claim_envelope_id != expected_envelope:
        raise ValueError("Profile handoff does not match the canonical Stage 3 claim envelope")
    persist_stage_four_artifact(
        repository,
        handoff.study_id,
        name=f"stage_four_evidence_handoff_v{artifact_version}",
        value=handoff,
        kind="stage_four_evidence_handoff",
        role=ArtifactRole.CLAIM,
        version=artifact_version,
        immutable=True,
    )
    _, steps = ensure_stage_four_dag(
        repository,
        handoff.study_id,
        venue_policy_id=venue_policy_id or GENERIC_JOURNAL_POLICY.profile_id,
    )
    return steps


__all__ = [
    "ProfileEvidencePointer",
    "ProfileReportingFinding",
    "ProfileStageFourEvidenceInput",
    "STAGE_FOUR_EVIDENCE_ADAPTER_ID",
    "STAGE_FOUR_EVIDENCE_ARTIFACT_NAME",
    "StageFourEvidenceHandoff",
    "build_stage_four_evidence_handoff",
    "build_stage_four_evidence_from_repository",
    "persist_stage_four_evidence_handoff",
    "queue_stage_four_from_profile_handoff",
]
