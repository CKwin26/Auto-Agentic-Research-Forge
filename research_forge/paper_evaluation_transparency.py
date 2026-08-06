from __future__ import annotations

"""Evidence-bound operational reporting for Stage 4 manuscripts.

The register is deliberately descriptive.  It records what the frozen study
can report about evaluation flow, evaluator configuration, sensitivity checks,
and repair evidence.  Missing analyses remain explicit missing disclosures;
they are never synthesized from prose or inferred from model knowledge.
"""

from enum import StrEnum
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


class TransparencyStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    PLANNED = "planned"
    UNVERIFIED = "unverified"


class EvaluationTransparencyItem(StrictModel):
    item_id: str = Field(pattern=r"^transparency-[a-z0-9-]{2,100}$")
    category: Literal[
        "sample_flow",
        "eligibility",
        "abstention",
        "threshold_provenance",
        "numeric_precision",
        "evaluator_sensitivity",
        "verifier_ablation",
        "packet_example",
        "prospective_validation",
        "fault_localization_baseline",
        "semantic_preservation",
        "release_assets",
    ]
    status: TransparencyStatus
    statement: str = Field(min_length=10, max_length=8_000)
    required_destination: Literal[
        "methods", "results", "limitations", "supplement", "completion_package"
    ]
    material: bool = True
    evidence_paths: list[str] = Field(default_factory=list)
    missing_reason: str | None = None
    follow_up_action: str | None = None

    @model_validator(mode="after")
    def missing_items_explain_the_gap(self) -> "EvaluationTransparencyItem":
        if (
            self.status
            in {
                TransparencyStatus.MISSING,
                TransparencyStatus.PLANNED,
                TransparencyStatus.UNVERIFIED,
            }
            and not self.missing_reason
        ):
            raise ValueError(
                "missing, planned, or unverified transparency items require "
                "a missing_reason"
            )
        if (
            self.status is TransparencyStatus.AVAILABLE
            and not self.evidence_paths
        ):
            raise ValueError(
                "available transparency items require at least one evidence path"
            )
        return self


class EvaluationTransparencyRegister(StrictModel):
    schema_version: int = 1
    study_id: str
    source_claim_envelope_id: str
    items: list[EvaluationTransparencyItem]
    frozen: bool = False
    frozen_at: str | None = None
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_register(self) -> "EvaluationTransparencyRegister":
        identifiers = [item.item_id for item in self.items]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("transparency item IDs must be unique")
        if self.frozen and not self.frozen_at:
            raise ValueError("a frozen transparency register requires frozen_at")
        return self

    def material_items(self) -> list[EvaluationTransparencyItem]:
        return [item for item in self.items if item.material]


class EvaluationTransparencyCoverageReport(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    required_item_ids: list[str]
    declared_item_ids: list[str]
    missing_item_ids: list[str]


class Stage4EvidenceBackfillRequest(StrictModel):
    schema_version: int = 1
    study_id: str
    source_claim_envelope_id: str
    status: Literal[
        "sufficient", "disclosure_only", "stage3_backfill_required"
    ]
    required_item_ids: list[str] = Field(default_factory=list)
    disclosure_item_ids: list[str] = Field(default_factory=list)
    changed_contract_fields: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    historical_verdict_must_be_preserved: Literal[True] = True
    created_at: str = Field(default_factory=utc_now)


def audit_evaluation_transparency_coverage(
    register: EvaluationTransparencyRegister,
    *,
    declared_item_ids: list[str],
) -> EvaluationTransparencyCoverageReport:
    required = {item.item_id for item in register.material_items()}
    declared = set(declared_item_ids)
    missing = sorted(required - declared)
    return EvaluationTransparencyCoverageReport(
        passed=not missing,
        required_item_ids=sorted(required),
        declared_item_ids=sorted(declared),
        missing_item_ids=missing,
    )


_DISCLOSURE_MARKERS: dict[str, tuple[tuple[str, ...], ...]] = {
    "sample_flow": (
        ("assigned", "analysis"),
        ("population", "denominator"),
        ("样本", "分母"),
        ("分配", "分析"),
    ),
    "eligibility": (
        ("eligible", "excluded"),
        ("included", "excluded"),
        ("资格", "排除"),
        ("纳入", "排除"),
    ),
    "abstention": (("abstention",), ("abstain",), ("弃权",)),
    "numeric_precision": (
        ("decimal",),
        ("precision",),
        ("rounding",),
        ("小数",),
        ("精度",),
        ("舍入",),
    ),
    "release_assets": (
        ("release asset",),
        ("public packet",),
        ("packet schema",),
        ("发布资产",),
        ("公开材料包",),
        ("数据包模式",),
    ),
}


def infer_declared_transparency_item_ids(
    register: EvaluationTransparencyRegister,
    *,
    sections: dict[str, str],
) -> list[str]:
    """Infer reader-facing disclosures without leaking internal item IDs.

    Transparency identifiers belong in the audit ledger, not in manuscript
    prose.  Earlier code nevertheless counted only literal
    ``transparency:<id>`` claim tokens, so a manuscript could disclose sample
    flow, eligibility, abstentions, precision, and missing release assets in
    ordinary language while the coverage gate still reported zero coverage.
    This conservative semantic check requires category-specific marker groups
    in the registered destination (with ``limitations`` mapped to the draft's
    limitations/discussion text).
    """

    normalized = {
        str(name): str(value or "").casefold()
        for name, value in sections.items()
    }
    declared: list[str] = []
    for item in register.material_items():
        destinations = [item.required_destination]
        if item.required_destination == "limitations":
            destinations.append("discussion")
        destination_text = "\n".join(
            normalized.get(name, "") for name in destinations
        )
        # Section placement is a presentation recommendation, not scientific
        # authority. Count a clear disclosure elsewhere in the manuscript and
        # let the structure audit handle relocation separately.
        manuscript_text = "\n".join(normalized.values())
        marker_groups = _DISCLOSURE_MARKERS.get(item.category, ())
        if marker_groups and any(
            all(
                marker.casefold() in destination_text
                or marker.casefold() in manuscript_text
                for marker in group
            )
            for group in marker_groups
        ):
            declared.append(item.item_id)
    return sorted(declared)


def restore_required_transparency_disclosures(
    register: EvaluationTransparencyRegister,
    *,
    sections: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Append frozen disclosure statements that prose generation omitted.

    The repair is deterministic and may only copy statements already frozen in
    the transparency register.  It therefore cannot change an estimate,
    decision, or scientific claim.  Natural drafting should normally place the
    statements first; this function is the fail-closed completeness fallback.
    """

    repaired = {str(key): str(value or "") for key, value in sections.items()}
    restored: list[str] = []
    declared = set(
        infer_declared_transparency_item_ids(register, sections=repaired)
    )
    for item in register.material_items():
        if item.item_id in declared:
            continue
        destination = item.required_destination
        if destination not in repaired:
            destination = (
                "limitations"
                if "limitations" in repaired
                else "discussion"
            )
        statement = item.statement.strip()
        if item.missing_reason:
            statement += " This remains a limitation because " + item.missing_reason.strip().rstrip(".") + "."
        repaired[destination] = (
            repaired.get(destination, "").rstrip()
            + "\n\n"
            + statement
        ).strip()
        restored.append(item.item_id)
    return repaired, restored


_BACKFILL_CONTRACT_FIELDS = {
    "eligibility": ["eligibility_rules"],
    "threshold_provenance": ["evaluator_policy"],
    "numeric_precision": ["output_schema", "statistical_rules"],
    "evaluator_sensitivity": ["evaluator_policy"],
    "verifier_ablation": ["evaluator_policy"],
    "packet_example": ["evaluator_policy", "output_schema"],
    "prospective_validation": ["evaluator_policy", "tasks", "seeds"],
    "fault_localization_baseline": ["evaluator_policy"],
    "semantic_preservation": ["metrics", "evaluator_policy"],
}


def assess_stage4_evidence_sufficiency(
    register: EvaluationTransparencyRegister,
    *,
    required_backfill_categories: set[str] | None = None,
) -> Stage4EvidenceBackfillRequest:
    """Separate Stage 3 evidence gaps from honest Stage 4 disclosures.

    Missing operational evidence never causes Stage 4 to fabricate text.  A
    contract-selected subset routes to a scientific successor; the remaining
    gaps are mandatory disclosures in the current manuscript.
    """

    required_categories = (
        set(required_backfill_categories)
        if required_backfill_categories is not None
        else {
            "eligibility",
            "threshold_provenance",
            "prospective_validation",
        }
    )
    unresolved = [
        item
        for item in register.material_items()
        if item.status
        in {
            TransparencyStatus.MISSING,
            TransparencyStatus.PLANNED,
            TransparencyStatus.UNVERIFIED,
        }
    ]
    backfill = [
        item for item in unresolved if item.category in required_categories
    ]
    disclosure = [item for item in unresolved if item not in backfill]
    fields = sorted(
        {
            field
            for item in backfill
            for field in _BACKFILL_CONTRACT_FIELDS.get(item.category, [])
        }
    )
    actions = [
        str(item.follow_up_action or item.missing_reason)
        for item in backfill
    ]
    status: Literal[
        "sufficient", "disclosure_only", "stage3_backfill_required"
    ]
    if backfill:
        status = "stage3_backfill_required"
    elif disclosure:
        status = "disclosure_only"
    else:
        status = "sufficient"
    return Stage4EvidenceBackfillRequest(
        study_id=register.study_id,
        source_claim_envelope_id=register.source_claim_envelope_id,
        status=status,
        required_item_ids=[item.item_id for item in backfill],
        disclosure_item_ids=[item.item_id for item in disclosure],
        changed_contract_fields=fields,
        required_actions=actions,
    )


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(item, path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            flattened.update(_flatten(item, f"{prefix}[{index}]"))
    else:
        flattened[prefix] = value
    return flattened


def _uses_learned_evaluator(evaluator_policy: dict[str, Any]) -> bool:
    """Return whether a learned evaluator is operational in this study.

    Listing ``nli_risk_alert`` in the scientific authority order only says
    that NLI may raise a warning.  It does not mean that NLI produced the
    experiment's endpoint.
    """

    explicit = evaluator_policy.get("uses_learned_evaluator")
    if isinstance(explicit, bool):
        return explicit
    operational_policy = {
        str(key): value
        for key, value in evaluator_policy.items()
        if str(key)
        not in {
            "authority_order",
            "resource_selection_id",
            "approved_benchmark_ids",
        }
    }
    serialized = _serialized(operational_policy)
    return any(
        token in serialized
        for token in ("nli", "deberta", "classifier", "semantic", "llm", "model")
    )


def _matching_paths(value: dict[str, Any], *needles: str) -> list[str]:
    flattened = _flatten(value)
    return sorted(
        path
        for path in flattened
        if any(needle in path.casefold() for needle in needles)
    )


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).casefold()


def build_evaluation_transparency_register(
    *,
    study_id: str,
    source_claim_envelope_id: str,
    evaluation_path: str,
    contract_path: str,
    evaluator_policy: dict[str, Any],
    total_records: int,
    eligible_records: int,
    excluded_record_ids: list[str],
    exclusion_reasons: dict[str, int] | None,
    abstention_count: int,
    arm_estimates: dict[str, float],
    repair_present: bool = False,
    prospective_successor_present: bool = False,
    available_artifact_kinds: set[str] | None = None,
) -> EvaluationTransparencyRegister:
    """Build a frozen, manuscript-facing transparency register.

    Counts and configuration come only from frozen Workflow objects supplied by
    the caller.  Absence is represented as a disclosure item, not guessed.
    """

    if total_records < 0 or eligible_records < 0 or abstention_count < 0:
        raise ValueError("evaluation transparency counts cannot be negative")
    if eligible_records > total_records:
        raise ValueError("eligible_records cannot exceed total_records")
    artifacts = {item.casefold() for item in (available_artifact_kinds or set())}
    items: list[EvaluationTransparencyItem] = []
    evidence = [evaluation_path, contract_path]

    items.append(
        EvaluationTransparencyItem(
            item_id="transparency-sample-flow",
            category="sample_flow",
            status=TransparencyStatus.AVAILABLE,
            statement=(
                f"The frozen evaluation contains {total_records} records, of "
                f"which {eligible_records} are eligible and "
                f"{total_records - eligible_records} are excluded."
            ),
            required_destination="results",
            evidence_paths=evidence,
        )
    )
    reason_counts = dict(exclusion_reasons or {})
    excluded_count = total_records - eligible_records
    reasons_accounted = sum(reason_counts.values())
    eligibility_available = (
        excluded_count == 0
        or (
            reasons_accounted == excluded_count
            and all(value >= 0 for value in reason_counts.values())
        )
    )
    items.append(
        EvaluationTransparencyItem(
            item_id="transparency-eligibility-breakdown",
            category="eligibility",
            status=(
                TransparencyStatus.AVAILABLE
                if eligibility_available
                else TransparencyStatus.MISSING
            ),
            statement=(
                "Frozen exclusion breakdown: "
                + (
                    json.dumps(reason_counts, ensure_ascii=False, sort_keys=True)
                    if eligibility_available
                    else (
                        f"{excluded_count} records were excluded, but the frozen "
                        "artifacts do not account for every exclusion by reason."
                    )
                )
            ),
            required_destination=(
                "results" if eligibility_available else "limitations"
            ),
            evidence_paths=evidence if eligibility_available else [],
            missing_reason=(
                None
                if eligibility_available
                else "exclusion-reason counts are absent or do not sum to exclusions"
            ),
            follow_up_action=(
                None
                if eligibility_available
                else "add immutable reason codes at the eligibility decision boundary"
            ),
        )
    )
    items.append(
        EvaluationTransparencyItem(
            item_id="transparency-abstentions",
            category="abstention",
            status=TransparencyStatus.AVAILABLE,
            statement=(
                f"The frozen evaluation records {abstention_count} abstentions "
                f"among {total_records} records."
            ),
            required_destination="results",
            evidence_paths=[evaluation_path],
        )
    )

    flattened_policy = _flatten(evaluator_policy)
    threshold_paths = _matching_paths(evaluator_policy, "threshold", "cutoff")
    learned_evaluator = _uses_learned_evaluator(evaluator_policy)
    if threshold_paths:
        provenance_paths = _matching_paths(
            evaluator_policy,
            "calibration",
            "selection",
            "source",
            "tuned",
            "locked",
        )
        provenance_available = bool(provenance_paths)
        items.append(
            EvaluationTransparencyItem(
                item_id="transparency-threshold-provenance",
                category="threshold_provenance",
                status=(
                    TransparencyStatus.AVAILABLE
                    if provenance_available
                    else TransparencyStatus.MISSING
                ),
                statement=(
                    "Frozen evaluator threshold fields: "
                    + json.dumps(
                        {
                            path: flattened_policy[path]
                            for path in threshold_paths
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + (
                        "; provenance fields: "
                        + json.dumps(provenance_paths, ensure_ascii=False)
                        if provenance_available
                        else "; threshold-selection provenance is not recorded"
                    )
                ),
                required_destination=(
                    "methods" if provenance_available else "limitations"
                ),
                evidence_paths=[contract_path] if provenance_available else [],
                missing_reason=(
                    None
                    if provenance_available
                    else "threshold values exist without a frozen selection source"
                ),
                follow_up_action=(
                    None
                    if provenance_available
                    else "freeze calibration corpus, selection split, objective, and lock time"
                ),
            )
        )
    else:
        items.append(
            EvaluationTransparencyItem(
                item_id="transparency-threshold-provenance",
                category="threshold_provenance",
                status=TransparencyStatus.NOT_APPLICABLE,
                statement="The frozen evaluator policy contains no thresholded decision rule.",
                required_destination="supplement",
                material=False,
            )
        )

    estimates = list(arm_estimates.values())
    exact_equal = len(estimates) >= 2 and len(set(estimates)) == 1
    precision_paths = _matching_paths(
        evaluator_policy, "tolerance", "precision", "rounding"
    )
    precision_available = (not exact_equal) or bool(precision_paths)
    items.append(
        EvaluationTransparencyItem(
            item_id="transparency-numeric-precision",
            category="numeric_precision",
            status=(
                TransparencyStatus.AVAILABLE
                if precision_available
                else TransparencyStatus.MISSING
            ),
            statement=(
                (
                    "Arm estimates are exactly equal in the frozen evaluation: "
                    if exact_equal
                    else "Frozen arm estimates differ: "
                )
                + json.dumps(arm_estimates, ensure_ascii=False, sort_keys=True)
                + (
                    "; numeric tolerance or precision metadata are recorded in "
                    "the evaluator policy."
                    if precision_paths
                    else (
                        "; no numeric tolerance or rounding metadata are frozen."
                        if exact_equal
                        else "; arm estimates differ."
                    )
                )
            ),
            required_destination=(
                "results" if precision_available else "limitations"
            ),
            evidence_paths=evidence if precision_available else [],
            missing_reason=(
                None
                if precision_available
                else "identical displayed means lack frozen precision or tolerance metadata"
            ),
            follow_up_action=(
                None
                if precision_available
                else "report computation precision and equality tolerance"
            ),
        )
    )

    if learned_evaluator:
        sensitivity_paths = _matching_paths(
            evaluator_policy,
            "sensitivity",
            "secondary_evaluator",
            "evaluator_famil",
            "alternate_model",
        )
        ablation_paths = _matching_paths(
            evaluator_policy,
            "ablation",
            "nli_only",
            "deterministic_only",
            "hybrid",
        )
        packet_paths = _matching_paths(
            evaluator_policy, "packet_schema", "evidence_packet", "context_field"
        )
        for item_id, category, paths, action in (
            (
                "transparency-evaluator-sensitivity",
                "evaluator_sensitivity",
                sensitivity_paths,
                "evaluate at least one second evaluator family and frozen threshold grid",
            ),
            (
                "transparency-verifier-ablation",
                "verifier_ablation",
                ablation_paths,
                "compare deterministic-only, learned-only, and precedence-aware hybrid verification",
            ),
            (
                "transparency-packet-examples",
                "packet_example",
                packet_paths,
                "freeze minimal before/after packets with task context and deterministic bindings",
            ),
        ):
            available = bool(paths)
            items.append(
                EvaluationTransparencyItem(
                    item_id=item_id,
                    category=category,  # type: ignore[arg-type]
                    status=(
                        TransparencyStatus.AVAILABLE
                        if available
                        else TransparencyStatus.MISSING
                    ),
                    statement=(
                        f"Frozen {category.replace('_', ' ')} fields: "
                        + (
                            json.dumps(paths, ensure_ascii=False)
                            if available
                            else "none recorded"
                        )
                    ),
                    required_destination=(
                        "methods" if available else "limitations"
                    ),
                    evidence_paths=[contract_path] if available else [],
                    missing_reason=(
                        None
                        if available
                        else f"no frozen {category.replace('_', ' ')} evidence"
                    ),
                    follow_up_action=None if available else action,
                )
            )

    if repair_present:
        items.extend(
            [
                EvaluationTransparencyItem(
                    item_id="transparency-prospective-validation",
                    category="prospective_validation",
                    status=(
                        TransparencyStatus.AVAILABLE
                        if prospective_successor_present
                        else TransparencyStatus.PLANNED
                    ),
                    statement=(
                        "A fresh prospective successor is present."
                        if prospective_successor_present
                        else (
                            "A repair is present, but no fresh prospective "
                            "successor is frozen; regression replay cannot "
                            "establish generalization."
                        )
                    ),
                    required_destination=(
                        "results"
                        if prospective_successor_present
                        else "limitations"
                    ),
                    evidence_paths=(
                        [evaluation_path]
                        if prospective_successor_present
                        else []
                    ),
                    missing_reason=(
                        None
                        if prospective_successor_present
                        else "only historical or same-case repair evidence is available"
                    ),
                    follow_up_action=(
                        None
                        if prospective_successor_present
                        else "run a fresh, frozen successor without reusing diagnosed cases"
                    ),
                ),
                EvaluationTransparencyItem(
                    item_id="transparency-fault-localization-baseline",
                    category="fault_localization_baseline",
                    status=(
                        TransparencyStatus.AVAILABLE
                        if "fault_localization_baseline" in artifacts
                        else TransparencyStatus.MISSING
                    ),
                    statement=(
                        "A frozen fault-localization baseline comparison is available."
                        if "fault_localization_baseline" in artifacts
                        else (
                            "No frozen comparison against ordinary logs, unit "
                            "tests, or another localization baseline is available."
                        )
                    ),
                    required_destination=(
                        "results"
                        if "fault_localization_baseline" in artifacts
                        else "limitations"
                    ),
                    evidence_paths=(
                        [evaluation_path]
                        if "fault_localization_baseline" in artifacts
                        else []
                    ),
                    missing_reason=(
                        None
                        if "fault_localization_baseline" in artifacts
                        else "fault-localization accuracy and efficiency were not benchmarked"
                    ),
                    follow_up_action=(
                        None
                        if "fault_localization_baseline" in artifacts
                        else "compare localization accuracy, time, and evidence cost with ordinary debugging baselines"
                    ),
                ),
            ]
        )

    if learned_evaluator:
        items.append(
            EvaluationTransparencyItem(
                item_id="transparency-semantic-preservation",
                category="semantic_preservation",
                status=(
                    TransparencyStatus.AVAILABLE
                    if "semantic_preservation_audit" in artifacts
                    else TransparencyStatus.MISSING
                ),
                statement=(
                    "A frozen informativeness or semantic-preservation audit is available."
                    if "semantic_preservation_audit" in artifacts
                    else (
                        "No frozen human informativeness or semantic-preservation "
                        "assessment is available beyond task-native safeguards."
                    )
                ),
                required_destination=(
                    "results"
                    if "semantic_preservation_audit" in artifacts
                    else "limitations"
                ),
                evidence_paths=(
                    [evaluation_path]
                    if "semantic_preservation_audit" in artifacts
                    else []
                ),
                missing_reason=(
                    None
                    if "semantic_preservation_audit" in artifacts
                    else "task-native performance does not by itself validate claim utility"
                ),
                follow_up_action=(
                    None
                    if "semantic_preservation_audit" in artifacts
                    else "add a blinded human utility or informativeness sample"
                ),
            )
        )

    release_kinds = {
        "packet_schema",
        "evaluator_manifest",
        "regression_fixtures",
    }
    released = sorted(release_kinds & artifacts)
    items.append(
        EvaluationTransparencyItem(
            item_id="transparency-release-assets",
            category="release_assets",
            status=(
                TransparencyStatus.AVAILABLE
                if release_kinds <= artifacts
                else TransparencyStatus.MISSING
            ),
            statement=(
                "Reusable release assets present: "
                + (", ".join(released) if released else "none")
                + "."
            ),
            required_destination=(
                "methods" if release_kinds <= artifacts else "limitations"
            ),
            evidence_paths=(
                [contract_path] if release_kinds <= artifacts else []
            ),
            missing_reason=(
                None
                if release_kinds <= artifacts
                else (
                    "the packet schema, evaluator manifest, and regression "
                    "fixtures are not all registered as release artifacts"
                )
            ),
            follow_up_action=(
                None
                if release_kinds <= artifacts
                else "register anonymized packet schema, evaluator manifest, and regression fixtures"
            ),
        )
    )

    return EvaluationTransparencyRegister(
        study_id=study_id,
        source_claim_envelope_id=source_claim_envelope_id,
        items=items,
        frozen=True,
        frozen_at=utc_now(),
    )


__all__ = [
    "EvaluationTransparencyCoverageReport",
    "EvaluationTransparencyItem",
    "EvaluationTransparencyRegister",
    "Stage4EvidenceBackfillRequest",
    "TransparencyStatus",
    "assess_stage4_evidence_sufficiency",
    "audit_evaluation_transparency_coverage",
    "build_evaluation_transparency_register",
    "infer_declared_transparency_item_ids",
    "restore_required_transparency_disclosures",
]
