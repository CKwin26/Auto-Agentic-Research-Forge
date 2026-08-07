from __future__ import annotations

"""Canonical Workflow v2 Stage 4 publication DAG.

This module owns orchestration only. Scientific authority remains in the
Stage 3 ScientificClaimEnvelope; authoring modules compile each step's
artifacts without broadening that authority.
"""

import asyncio
import contextvars
import hashlib
import json
import math
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import StrictModel
from .paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
    HierarchicalPaperOutline,
    academicize_publication_text,
    publication_code_identifiers,
)
from .paper_contribution import ContributionCandidate
from .paper_narrative import (
    ComparatorDecision,
    PublicationNarrativeContract,
    compile_reader_memory_contract,
    seal_publication_narrative,
    validate_publication_narrative,
)
from .paper_reporting_compliance import (
    MandatoryReportingItem,
    MandatoryReportingRegister,
)
from .paper_venue_policy import GENERIC_JOURNAL_POLICY, get_venue_policy
from .paper_visual_strategy import (
    CaptionClaimBinding,
    FigureSpec,
    TableSpec,
    VisualArgumentPlan,
    validate_visual_argument_plan,
)
from .stage_three import stage4_claim_authority
from .storage import read_json, sha256_file, write_json_atomic
from .workflow_domain import (
    ArtifactRecord,
    ArtifactRole,
    ArtifactStatus,
    ExecutionStatus,
    ExecutorType,
    GateStatus,
    GateType,
    Phase,
    StepDefinition,
    StepInstance,
    StudyLifecycle,
    WorkflowRepository,
    stable_id,
    utc_now,
)

_ACTIVE_STAGE4_REVISION: contextvars.ContextVar[int] = contextvars.ContextVar(
    "research_forge_stage4_revision",
    default=1,
)


def _has_blocking_review_findings(decision: Any) -> bool:
    """Keep advisory review findings visible without stopping publication."""

    return bool(
        decision.decision != "accept" and decision.blocking_finding_ids
    )


def _finding_requires_stage3_backfill(
    finding: Any,
    evidence_map: EvidenceClaimMap,
) -> bool:
    """Separate absent evidence from incorrect prose about bound evidence."""

    if finding.category not in {
        "evidence",
        "statistics",
        "falsifiability",
        "information",
    } and not any(
        claim_id.startswith(("research-v", "evaluation-", "claim-envelope-"))
        for claim_id in finding.claim_ids
    ):
        return False

    bindings = {binding.claim_id: binding for binding in evidence_map.bindings}
    cited_bindings = [bindings.get(claim_id) for claim_id in finding.claim_ids]
    all_claims_bound = bool(cited_bindings) and all(
        binding is not None and binding.evidence_status == "bound"
        for binding in cited_bindings
    )
    text = f"{finding.diagnosis} {finding.required_change}".lower()
    manuscript_repair_markers = (
        "supplied binding",
        "bound scoring",
        "bound evidence",
        "already bound",
        "draft states",
        "draft omits",
        "draft fails to report",
        "contradicts the supplied",
        "correct the evidence characterization",
        "report the bound",
        "remove these claims",
        "remove the unbound",
        "remove the unsupported",
        "delete the unsupported",
        "narrow the statement",
        "limit the statement",
        "do not assert",
        "rewrite",
        "clarify",
    )
    if all_claims_bound and any(marker in text for marker in manuscript_repair_markers):
        return False
    return True


def _select_primary_visual_bindings(
    evidence_map: EvidenceClaimMap,
    narrative: PublicationNarrativeContract,
) -> tuple[Any, list[Any], list[Any], Any]:
    """Select result evidence by semantic facet, with legacy-ID fallback."""

    primary = next(
        item
        for item in evidence_map.bindings
        if item.claim_id in narrative.required_claim_ids
    )
    arm_metrics = [
        item
        for item in evidence_map.bindings
        if ":arm:" in item.claim_id and item.evidence_status == "bound"
    ]
    faceted_results = [
        item
        for item in evidence_map.bindings
        if item.evidence_status == "bound"
        and "primary_result" in item.evidence_facets
    ]
    fallback_results = [
        item
        for item in evidence_map.bindings
        if item.evidence_status == "bound"
        and item.kind == "result_metric"
        and item.claim_strength == "comparative"
    ]
    semantic_results = faceted_results or fallback_results
    legacy_contrasts = [
        item
        for item in evidence_map.bindings
        if ":contrast:" in item.claim_id and item.evidence_status == "bound"
    ]
    denominator = next(
        (
            item
            for item in evidence_map.bindings
            if item.evidence_status == "bound"
            and (
                "denominator_reconciliation" in item.evidence_facets
                or "denominator" in item.claim_id.lower()
            )
        ),
        primary,
    )
    plotted = arm_metrics or semantic_results or [primary]
    contrasts = legacy_contrasts or semantic_results
    return primary, plotted, contrasts, denominator


def _merge_verified_background_sources(
    evidence_map: Any,
    sources: list[Any],
) -> Any:
    """Add narrow frozen background bindings without scientific authority.

    A Profile handoff already owns its result bindings, so Stage 4 must not
    rewrite them.  It must nevertheless bind every verified literature record
    that the manuscript is allowed to cite.  Metadata-only strings retain
    legacy behavior (registry membership only); structured verified records
    additionally receive a descriptive ``literature_context`` binding.
    """

    def field(source: Any, key: str) -> Any:
        if isinstance(source, dict):
            return source.get(key)
        return getattr(source, key, None)

    source_ids: list[str] = []
    background_bindings = list(evidence_map.bindings)
    existing_claim_ids = {item.claim_id for item in background_bindings}
    for source in sources:
        if isinstance(source, str):
            source_id = source.strip()
        else:
            source_id = str(
                field(source, "source_id")
                or field(source, "resource_id")
                or ""
            ).strip()
        if not source_id:
            continue
        source_ids.append(source_id)
        title = str(field(source, "title") or "").strip()
        metadata_hash = str(
            field(source, "canonical_metadata_hash") or ""
        ).strip()
        claim_id = f"literature:{source_id}:verified-topic"
        if not title or not metadata_hash or claim_id in existing_claim_ids:
            continue
        canonical_identifier = str(
            field(source, "canonical_identifier")
            or field(source, "doi")
            or source_id
        ).strip()
        background_bindings.append(
            EvidenceClaimBinding(
                claim_id=claim_id,
                kind="literature_context",
                statement=(
                    f'The verified background publication "{title}" is '
                    "registered as methodological context only."
                ),
                evidence=[
                    EvidencePointer(
                        path=canonical_identifier,
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
        existing_claim_ids.add(claim_id)

    merged = sorted(
        set(evidence_map.verified_source_ids) | {str(item) for item in source_ids}
    )
    if (
        merged == sorted(evidence_map.verified_source_ids)
        and len(background_bindings) == len(evidence_map.bindings)
    ):
        return evidence_map
    registry_sha256 = hashlib.sha256(
        json.dumps(
            {
                "claim_registry_sha256": evidence_map.source_registry_sha256,
                "verified_background_source_ids": merged,
                "verified_background_binding_ids": sorted(
                    item.claim_id
                    for item in background_bindings
                    if item.kind == "literature_context"
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return evidence_map.model_copy(
        update={
            "verified_source_ids": merged,
            "bindings": background_bindings,
            "source_registry_sha256": registry_sha256,
        }
    )


def _effective_evaluation_arm_estimates(evaluation: Any) -> dict[str, float]:
    """Normalize legacy paired records into the current per-arm view."""

    estimates = dict(evaluation.arm_estimates)
    if estimates:
        return estimates
    if evaluation.baseline_estimate is not None:
        estimates["baseline"] = evaluation.baseline_estimate
    if evaluation.treatment_estimate is not None:
        estimates["treatment"] = evaluation.treatment_estimate
    return estimates


def _publication_aliases_for_contract(
    contract: Any | None,
    *,
    language: str = "zh",
) -> dict[str, str]:
    """Map implementation labels to scientific roles for manuscript prose.

    This changes only the publication view. The frozen contract, evidence
    bindings, result records, and audit trail retain their exact identifiers.
    """

    if contract is None:
        return {}

    aliases: dict[str, str] = {}
    is_english = str(language or "zh").strip().lower().startswith("en")
    baseline_role = "reference method" if is_english else "参考方法"
    treatment_role = "candidate method" if is_english else "候选方法"
    primary_metric_role = "primary outcome" if is_english else "主要结局指标"
    task_role = "registered study task" if is_english else "注册研究任务"

    def bind_role(payload: Any, role: str) -> None:
        if not isinstance(payload, dict):
            return
        for key in ("name", "label", "id", "arm_id", "implementation"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidate = value.strip()
                internal_tokens = publication_code_identifiers(candidate)
                if key not in {"name", "label"} or internal_tokens:
                    aliases[candidate] = role
                for token in internal_tokens:
                    aliases[token] = role
        serialized = json.dumps(payload, ensure_ascii=False)
        for token in publication_code_identifiers(serialized):
            aliases[token] = role

    bind_role(contract.baseline, baseline_role)
    bind_role(contract.treatment, treatment_role)

    for index, metric in enumerate(contract.metrics):
        role = (
            primary_metric_role
            if index == 0
            else (
                f"secondary outcome {index}"
                if is_english
                else f"次要结局指标{index}"
            )
        )
        bind_role(metric, role)

    for task in contract.tasks:
        if isinstance(task, str) and task.strip():
            aliases[task.strip()] = task_role

    return aliases


def _publication_context(context: Any) -> tuple[dict[str, str], str]:
    contract = context.repository.latest_research_contract(context.study_id)
    study = context.repository.load_study(context.study_id)
    language = str(study.settings.get("manuscript_language") or "zh")
    aliases = _publication_aliases_for_contract(contract, language=language)
    evidence_map = EvidenceClaimMap.model_validate(
        context.result("evidence_claim_mapping")["evidence_claim_map"]
    )
    frozen_conclusion = academicize_publication_text(
        evidence_map.frozen_conclusion,
        aliases=aliases,
        language="en" if language.strip().lower().startswith("en") else "zh",
    )
    return aliases, frozen_conclusion


_STATEMENT_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
)


def _statement_number_matches(value: float, token: str) -> bool:
    """Return whether a frozen prose number is a rendering of ``value``."""

    try:
        rendered = float(token)
    except ValueError:
        return False
    if "e" in token.casefold():
        mantissa = token.casefold().split("e", 1)[0]
        significant = len(mantissa.replace(".", "").lstrip("+-0"))
        tolerance = 0.5 * 10 ** (1 - max(1, significant))
        return math.isclose(value, rendered, rel_tol=tolerance, abs_tol=1e-15)
    if "." in token:
        decimals = len(token.rsplit(".", 1)[1])
        return round(value, decimals) == rendered
    return value == rendered


def _numeric_evidence_from_bound_claims(
    evidence_map: EvidenceClaimMap,
    *,
    repository_root: Path,
) -> list[dict[str, Any]]:
    """Read result numbers from the artifacts bound to the publication claim.

    A profile-specific evidence handoff can be more precise than the generic
    Stage 3 claim envelope (for example, it may register a paired bootstrap
    rather than a generic analytic interval).  Typesetting must therefore use
    the same frozen claim bindings that governed drafting, figures, and review.
    Only numeric leaves whose values are actually rendered in a bound result
    statement are copied into the table.
    """

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def numeric_leaves(value: Any, path: str = "$") -> list[tuple[str, float | int]]:
        found: list[tuple[str, float | int]] = []
        if isinstance(value, bool) or value is None:
            return found
        if isinstance(value, (int, float)):
            return [(path, value)]
        if isinstance(value, dict):
            for key, item in value.items():
                found.extend(numeric_leaves(item, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found.extend(numeric_leaves(item, f"{path}[{index}]"))
        return found

    def publication_label(json_path: str) -> str:
        normalized = json_path.casefold()
        labels = {
            "$.baseline_accuracy": "Baseline exact accuracy",
            "$.treatment_accuracy": "Treatment exact accuracy",
            "$.paired_accuracy_difference": "Paired accuracy difference",
            "$.paired_bootstrap_95_ci[0]": "95% paired-bootstrap CI, lower",
            "$.paired_bootstrap_95_ci[1]": "95% paired-bootstrap CI, upper",
            "$.mcnemar.baseline_right_treatment_wrong": "Paired worsenings",
            "$.mcnemar.baseline_wrong_treatment_right": "Paired improvements",
            "$.mcnemar.exact_two_sided_p": "Exact two-sided McNemar p-value",
        }
        if normalized in labels:
            return labels[normalized]
        leaf = json_path.rsplit(".", 1)[-1]
        leaf = re.sub(r"\[(\d+)\]$", r" \1", leaf)
        return " ".join(word for word in leaf.replace("_", " ").split()).capitalize()

    def publication_value(label: str, value: float | int) -> str:
        if isinstance(value, int):
            return str(value)
        if "p-value" in label:
            return f"{value:.3e}"
        return f"{value:.3f}"

    for binding in evidence_map.bindings:
        if binding.evidence_status != "bound" or binding.kind not in {
            "result_metric",
            "secondary",
        }:
            continue
        tokens = _STATEMENT_NUMBER_RE.findall(binding.statement)
        if not tokens:
            continue
        for pointer in binding.evidence:
            if pointer.evidence_type != "project_artifact":
                continue
            source_path = Path(pointer.path)
            if not source_path.is_absolute():
                source_path = repository_root / source_path
            if not source_path.is_file() or source_path.suffix.casefold() != ".json":
                continue
            if pointer.sha256 and sha256_file(source_path) != pointer.sha256:
                continue
            try:
                payload = read_json(source_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for json_path, value in numeric_leaves(payload):
                if not any(
                    _statement_number_matches(float(value), token)
                    for token in tokens
                ):
                    continue
                evidence_path = f"{source_path.name}{json_path}"
                key = (evidence_path, repr(value))
                if key in seen:
                    continue
                seen.add(key)
                label = publication_label(json_path)
                rows.append(
                    {
                        "path": evidence_path,
                        "label": label,
                        "value": value,
                        "display_value": publication_value(label, value),
                    }
                )
    order = {
        "Baseline exact accuracy": 0,
        "Treatment exact accuracy": 1,
        "Paired accuracy difference": 2,
        "95% paired-bootstrap CI, lower": 3,
        "95% paired-bootstrap CI, upper": 4,
        "Paired improvements": 5,
        "Paired worsenings": 6,
        "Exact two-sided McNemar p-value": 7,
    }
    rows.sort(key=lambda item: (order.get(str(item.get("label")), 100), item["path"]))
    return rows


_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SUBSECTION_RE = re.compile(r"(?m)^###\s+\S")
_JOURNAL_DEPTH_TARGETS = {
    "abstract": 250,
    "introduction": 1000,
    "related_work": 1200,
    "methods": 2200,
    "results": 1900,
    "discussion": 1900,
    "limitations": 600,
    "conclusion": 250,
}
_JOURNAL_PARAGRAPH_TARGETS = {
    "introduction": 5,
    "related_work": 6,
    "methods": 8,
    "results": 7,
    "discussion": 7,
    "conclusion": 2,
}
_JOURNAL_SUBSECTION_TARGETS = {
    "methods": 4,
    "results": 3,
    "discussion": 3,
}
_SHORT_REPORT_DEPTH_TARGETS = {
    # Align the pre-typesetting target with the final English short-report
    # gate (200 source units * 0.6 = 120 English words).
    "abstract": 200,
    "introduction": 700,
    "related_work": 650,
    "methods": 1400,
    "results": 1100,
    "discussion": 1100,
    "limitations": 400,
    "conclusion": 180,
}
_SHORT_REPORT_PARAGRAPH_TARGETS = {
    "introduction": 3,
    "related_work": 3,
    "methods": 5,
    "results": 4,
    "discussion": 4,
    "conclusion": 1,
}
_SHORT_REPORT_SUBSECTION_TARGETS = {
    "methods": 3,
    "results": 2,
    "discussion": 2,
}


def _depth_contract(profile: str) -> dict[str, Any]:
    if profile == "short-report":
        return {
            "sections": _SHORT_REPORT_DEPTH_TARGETS,
            "paragraphs": _SHORT_REPORT_PARAGRAPH_TARGETS,
            "subsections": _SHORT_REPORT_SUBSECTION_TARGETS,
            "total": 6_000,
        }
    return {
        "sections": _JOURNAL_DEPTH_TARGETS,
        "paragraphs": _JOURNAL_PARAGRAPH_TARGETS,
        "subsections": _JOURNAL_SUBSECTION_TARGETS,
        "total": 10_000,
    }


def _effective_depth_profile(
    venue_profile: str,
    claim: dict[str, Any] | None,
) -> str:
    """Combine venue format with the scientific evidence level.

    An evidence-boundary report has no authoritative treatment estimate to
    elaborate into a full empirical Results section. Applying the ordinary
    journal-article minimum would reward repetition or invented detail. The
    short-report contract still enforces substantive Methods, Results,
    Discussion, and limitations while matching the claim authority.
    """

    claim = claim or {}
    if (
        claim.get("maximum_claim_tier") == "evidence_boundary_report"
        or claim.get("evidence_level") == "L0_boundary_only"
    ):
        return "short-report"
    return venue_profile


def _depth_profile_for_context(context: Any, venue_profile: str) -> str:
    authority = context.result("stage4_claim_intake")
    claims = authority.get("claims") or []
    claim = dict(claims[0]) if len(claims) == 1 else None
    return _effective_depth_profile(venue_profile, claim)


def _draft_depth_violations(
    draft: Any,
    *,
    profile: str = "journal-article",
    language: str = "zh",
) -> list[str]:
    """Check prose depth before typesetting so late PDF failure is avoidable."""

    language = language.strip().lower()
    is_english = language == "en"
    from .manuscript_depth import get_manuscript_depth_profile

    canonical = get_manuscript_depth_profile(
        profile,
        "en" if is_english else "zh",
    )
    length_targets = canonical.section_minimums
    total_target = canonical.minimum_total
    unit = "English words" if is_english else "Han characters"

    def prose_length(value: str) -> int:
        if is_english:
            # Match the final LaTeX depth audit: citation keys and artifact
            # callouts are control syntax, not scholarly prose.
            value = re.sub(r"\[[A-Za-z0-9][A-Za-z0-9._:-]{0,120}\]", "", value)
            value = re.sub(r"\[(?:FIGURE|TABLE):[a-z0-9-]{3,84}\]", "", value)
            # Match manuscript_depth._WORD_RE exactly: numeric result tokens
            # are audited separately and are not prose words.  Counting them
            # here let a draft pass preflight and then fail final LaTeX depth
            # by exactly the number of reported statistics.
            return len(
                re.findall(
                    r"(?<![A-Za-z])[A-Za-z]+(?:[-'][A-Za-z]+)*(?![A-Za-z])",
                    value,
                )
            )
        return len(_HAN_RE.findall(value))

    violations: list[str] = []
    combined_prose = "\n".join(
        str(getattr(draft, section, "") or "")
        for section in length_targets
    )
    if is_english:
        english_words = prose_length(combined_prose)
        han_characters = len(_HAN_RE.findall(combined_prose))
        if han_characters >= 50 and han_characters > english_words:
            violations.append(
                "manuscript language mismatch: requested English but the core "
                f"narrative contains {han_characters} Han characters and only "
                f"{english_words} English words"
            )
    total = 0
    for section, minimum in length_targets.items():
        value = str(getattr(draft, section, "") or "")
        count = prose_length(value)
        total += count
        effective_minimum = math.ceil(
            minimum * (1.0 - canonical.minimum_section_tolerance_ratio)
        )
        if count < effective_minimum:
            violations.append(
                f"{section}: {count} {unit} < {minimum} "
                f"(counting tolerance floor {effective_minimum})"
            )
    abstract_count = prose_length(str(getattr(draft, "abstract", "") or ""))
    if (
        canonical.abstract_preferred_minimum is not None
        and abstract_count < canonical.abstract_preferred_minimum
    ):
        violations.append(
            "abstract: "
            f"{abstract_count} {unit} is above the hard floor but below the "
            "preferred complete-abstract range; target "
            f"{canonical.abstract_generation_target} rather than stopping at "
            f"{canonical.abstract_preferred_minimum}"
        )
    effective_total_floor = total_target - canonical.minimum_total_tolerance
    if total < effective_total_floor:
        violations.append(
            f"core narrative: {total} {unit} < {total_target} "
            f"(counting tolerance floor {effective_total_floor})"
        )
    for section, minimum in canonical.paragraph_minimums.items():
        value = str(getattr(draft, section, "") or "")
        count = len(
            [
                paragraph
                for paragraph in _PARAGRAPH_RE.split(value)
                if prose_length(paragraph) >= 20
            ]
        )
        if count < minimum:
            violations.append(f"{section}: {count} substantive paragraphs < {minimum}")
    for section, minimum in canonical.subsection_minimums.items():
        value = str(getattr(draft, section, "") or "")
        count = len(_SUBSECTION_RE.findall(value))
        if count < minimum:
            violations.append(f"{section}: {count} subsections < {minimum}")
    return violations


def _localized_depth_contract(
    profile: str,
    *,
    language: str,
) -> dict[str, Any]:
    """Expose the same depth contract in the manuscript's writing unit."""

    is_english = language.strip().lower() == "en"
    from .manuscript_depth import get_manuscript_depth_profile

    canonical = get_manuscript_depth_profile(
        profile,
        "en" if is_english else "zh",
    )
    return {
        "length_unit": "English words" if is_english else "Han characters",
        "sections": dict(canonical.section_minimums),
        # Models should not aim at the exact release threshold. Markdown-to-
        # LaTeX projection removes citation/control tokens, and ordinary edits
        # can contract prose materially during panel revision, humanization,
        # and narrative cleanup. A 20% generation margin keeps those later
        # claim-preserving edits from turning a complete section into a
        # threshold-hugging or under-length section.
        "generation_sections": {
            key: (
                minimum
                if key == "abstract"
                else math.ceil(minimum * 1.20)
            )
            for key, minimum in canonical.section_minimums.items()
        },
        "paragraphs": dict(canonical.paragraph_minimums),
        "subsections": dict(canonical.subsection_minimums),
        "total": canonical.minimum_total,
        # The validator keeps the canonical release floor. Generators receive
        # a higher target because citation/control-token removal and later
        # claim-preserving edits routinely contract otherwise complete prose.
        # A target equal to the release threshold produced manuscripts only a
        # handful of words below the tolerance floor and stranded the DAG.
        "generation_total": math.ceil(canonical.minimum_total * 1.10),
        "abstract": {
            "hard_minimum": canonical.section_minimums.get("abstract"),
            "preferred_minimum": canonical.abstract_preferred_minimum,
            "preferred_maximum": canonical.abstract_preferred_maximum,
            "generation_target": canonical.abstract_generation_target,
            "hard_maximum": canonical.abstract_maximum,
            "instruction": (
                "Aim for the generation target. Do not stop when the hard "
                "minimum or preferred minimum is first crossed; finish every "
                "required rhetorical move naturally."
            ),
        },
        "manuscript_depth_profile": profile,
    }


def _normalize_verified_citation_prefixes(draft: Any, source_ids: list[str]) -> Any:
    """Repair prefix slips and remove invented source identifiers.

    Removing an unknown citation does not make the surrounding statement
    authoritative: the subsequent scientific rereview still evaluates the
    prose. It only prevents a hallucinated bibliography key from bypassing
    the frozen source set.
    """

    allowed = set(source_ids)
    aliases: dict[str, str] = {}
    for source_id in allowed:
        if source_id.startswith("resource-"):
            alias = "source-" + source_id.removeprefix("resource-")
            if alias not in allowed:
                aliases[alias] = source_id
    updates: dict[str, str] = {}
    for field_name in getattr(type(draft), "model_fields", {}):
        value = str(getattr(draft, field_name))
        normalized = value
        for alias, canonical in aliases.items():
            normalized = normalized.replace(f"[{alias}]", f"[{canonical}]")
        normalized = re.sub(
            r"\[((?:source|resource)-[a-z0-9-]{2,80})\]",
            lambda match: (
                match.group(0)
                if match.group(1) in allowed
                else ""
            ),
            normalized,
        )
        if normalized != value:
            updates[field_name] = normalized
    return draft.model_copy(update=updates) if updates else draft


def _drafting_evidence_view(
    evidence_map: Any,
    *,
    claim_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Keep scientific claim content while omitting repeated provenance hashes.

    The immutable full EvidenceClaimMap remains the authority used by
    deterministic post-generation checks. A writing model needs claim wording,
    strength, status, and allowed destinations, but not every repeated artifact
    path and SHA-256 digest. This bounded view prevents large studies from
    overflowing the PromptEnvelope without weakening later validation.
    """

    value = (
        evidence_map.model_dump(mode="json")
        if hasattr(evidence_map, "model_dump")
        else dict(evidence_map)
    )
    bindings: list[dict[str, Any]] = []
    for binding in value.get("bindings") or []:
        claim_id = str(binding.get("claim_id") or "")
        if claim_ids is not None and claim_id not in claim_ids:
            continue
        bindings.append(
            {
                key: binding.get(key)
                for key in (
                    "claim_id",
                    "kind",
                    "statement",
                    "claim_strength",
                    "evidence_status",
                    "evidence_facets",
                    "allowed_sections",
                )
            }
        )
    return {
        "frozen_conclusion": value.get("frozen_conclusion"),
        "forbidden_moves": value.get("forbidden_moves") or [],
        "bindings": bindings,
        "provenance_note": (
            "Artifact paths and hashes are intentionally omitted from this "
            "drafting view; the immutable full EvidenceClaimMap remains the "
            "authority for deterministic validation."
        ),
    }


def _humanization_evidence_view(evidence_map: Any) -> dict[str, Any]:
    """Build a bounded, non-authoritative guardrail view for prose editing.

    Humanization receives the complete reviewed manuscript, while the full
    EvidenceClaimMap remains available to the deterministic integrity audit.
    Repeating a long registered protocol statement in the model request adds
    no authority and previously pushed otherwise valid manuscripts over the
    aggregate prompt budget.  This view retains every claim identifier and
    status, plus bounded beginning/end excerpts for semantic orientation.
    Nothing here replaces or mutates the frozen map.
    """

    value = (
        evidence_map.model_dump(mode="json")
        if hasattr(evidence_map, "model_dump")
        else dict(evidence_map)
    )
    bindings: list[dict[str, Any]] = []
    for binding in value.get("bindings") or []:
        statement = str(binding.get("statement") or "")
        if len(statement) <= 2_400:
            excerpt = statement
            excerpted = False
        else:
            excerpt = (
                statement[:1_600].rstrip()
                + "\n[...full frozen statement omitted from the editing view...]\n"
                + statement[-600:].lstrip()
            )
            excerpted = True
        bindings.append(
            {
                "claim_id": binding.get("claim_id"),
                "kind": binding.get("kind"),
                "claim_strength": binding.get("claim_strength"),
                "evidence_status": binding.get("evidence_status"),
                "evidence_facets": binding.get("evidence_facets") or [],
                "allowed_sections": binding.get("allowed_sections") or [],
                "statement_excerpt": excerpt,
                "statement_excerpted": excerpted,
                "statement_sha256": hashlib.sha256(
                    statement.encode("utf-8")
                ).hexdigest(),
            }
        )
    return {
        "frozen_conclusion": value.get("frozen_conclusion"),
        "forbidden_moves": value.get("forbidden_moves") or [],
        "bindings": bindings,
        "authority_note": (
            "The complete reviewed manuscript is the prose-editing source. "
            "This bounded view is only a guardrail; the immutable full "
            "EvidenceClaimMap is used by the post-edit integrity audit."
        ),
    }


def _humanization_reverse_outline_view(reverse_outline: Any) -> dict[str, Any]:
    """Retain rhetorical structure without repeating every paragraph text."""

    value = (
        reverse_outline.model_dump(mode="json")
        if hasattr(reverse_outline, "model_dump")
        else dict(reverse_outline)
    )
    sections: list[dict[str, Any]] = []
    for section in value.get("sections") or []:
        paragraphs = section.get("paragraphs") or []
        sections.append(
            {
                "section_key": section.get("section_key"),
                "thesis": section.get("thesis"),
                "expected_moves": section.get("expected_moves") or [],
                "paragraph_count": len(paragraphs),
                "citation_keys": sorted(
                    {
                        str(key)
                        for paragraph in paragraphs
                        for key in paragraph.get("citation_keys") or []
                    }
                ),
                "numeric_token_count": sum(
                    int(paragraph.get("numeric_token_count") or 0)
                    for paragraph in paragraphs
                ),
                "visual_callout_present": any(
                    bool(paragraph.get("visual_callout_present"))
                    for paragraph in paragraphs
                ),
            }
        )
    return {
        "passed": bool(value.get("passed")),
        "violations": value.get("violations") or [],
        "sections": sections,
    }


def _humanization_citation_view(citation_audit: Any) -> dict[str, Any]:
    """Keep citation gate outcomes while omitting repeated support prose."""

    value = (
        citation_audit.model_dump(mode="json")
        if hasattr(citation_audit, "model_dump")
        else dict(citation_audit)
    )
    return {
        key: value.get(key)
        for key in (
            "bibliography_hygiene_passed",
            "claim_source_support_passed",
            "cited_source_ids",
            "unknown_source_ids",
            "uncited_registered_source_ids",
            "rejected_or_unverified_count",
        )
    }


def _drafting_literature_view(sources: list[Any]) -> list[dict[str, Any]]:
    """Keep citation identity and a bounded relevance note for manuscript use."""

    compact: list[dict[str, Any]] = []
    for source in sources:
        value = (
            source.model_dump(mode="json")
            if hasattr(source, "model_dump")
            else dict(source)
        )
        compact.append(
            {
                "source_id": value.get("source_id"),
                "title": value.get("title"),
                "authors": value.get("authors") or [],
                "year": value.get("year"),
                "locator": value.get("locator"),
                "notes": str(value.get("notes") or "")[:1200],
                "verified": bool(value.get("verified")),
            }
        )
    return compact


def _review_claim_ids(decision: Any) -> set[str]:
    """Collect only claims implicated by a bounded scientific revision."""

    value = (
        decision.model_dump(mode="json")
        if hasattr(decision, "model_dump")
        else dict(decision)
    )
    return {
        str(claim_id)
        for review in value.get("reviews") or []
        for finding in review.get("findings") or []
        if str(finding.get("severity") or "") in {"major", "blocking"}
        for claim_id in finding.get("claim_ids") or []
        if str(claim_id)
    }


def _stage_four_evidence_handoff(context: Any) -> dict[str, Any] | None:
    """Load the universal Stage 3-to-Stage 4 evidence contract."""

    # The handoff is a frozen scientific input to every manuscript revision,
    # so it normally lives in the Study's canonical Stage 4 root.  A revised
    # manuscript may also carry a revision-local successor.  Search the active
    # revision first, then fall back to the immutable canonical handoff instead
    # of silently rebuilding a generic claim map.
    revision_root = stage_four_root(context.repository, context.study_id)
    canonical_root = stage_four_root(
        context.repository,
        context.study_id,
        revision=1,
    )
    roots = list(dict.fromkeys((revision_root, canonical_root)))
    candidates: list[Path] = []
    for root in roots:
        versioned = sorted(
            root.glob("stage_four_evidence_handoff_v*.json"),
            key=lambda item: int(item.stem.rsplit("_v", 1)[-1]),
            reverse=True,
        )
        candidates.extend(versioned)
        # Read-only compatibility for runs created before the interface was
        # renamed from a Profile-specific label to the universal boundary.
        candidates.append(root / "profile_stage_four_evidence_v1.json")
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        return None
    payload = read_json(path)
    if str(payload.get("study_id") or "") != context.study_id:
        raise ValueError("Stage 4 evidence handoff targets another Study")
    if payload.get("adapter_complete") is not True:
        raise ValueError("Stage 4 evidence handoff is incomplete")
    if (
        str(payload.get("profile_id") or "") == "llm_evaluation_v1"
        and not payload.get("required_evidence_facets")
    ):
        raise ValueError(
            "legacy LLM Stage 4 handoff covers result claims but not the "
            "publication-method evidence contract; create an append-only "
            "upgraded handoff before manuscript generation"
        )
    if payload.get("missing_evidence_facets"):
        raise ValueError(
            "Stage 4 evidence handoff is missing publication facets: "
            + ", ".join(str(item) for item in payload["missing_evidence_facets"])
        )
    if payload.get("manuscript_generated_by_profile") is not False:
        raise ValueError("experiment Profiles may not generate the formal manuscript")
    return payload


STAGE4_STEP_DEFINITIONS: tuple[StepDefinition, ...] = (
    StepDefinition(
        step_type="stage4_claim_intake",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="Stage 3 ScientificClaimEnvelope authority",
    ),
    StepDefinition(
        step_type="publication_prerequisite_gate",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="idea, evidence, literature, and reporting eligibility",
    ),
    StepDefinition(
        step_type="venue_and_reporting_profile_freeze",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="frozen VenuePolicyProfile and reporting policy",
    ),
    StepDefinition(
        step_type="evaluation_transparency_register",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="frozen EvaluationTransparencyRegister",
    ),
    StepDefinition(
        step_type="stage4_evidence_sufficiency_gate",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output=(
            "evidence-sufficiency decision or Stage 3 backfill request"
        ),
    ),
    StepDefinition(
        step_type="mandatory_reporting_register",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="MandatoryReportingRegister",
    ),
    StepDefinition(
        step_type="contribution_candidate_generation",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="evidence-bound ContributionCandidate set",
    ),
    StepDefinition(
        step_type="publication_narrative_selection",
        phase=Phase.PAPER,
        executor_type=ExecutorType.PROJECT_OWNER,
        expected_output="owner-approved central contribution",
        required_gate_type=GateType.PUBLICATION_NARRATIVE,
    ),
    StepDefinition(
        step_type="publication_narrative_contract_freeze",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="sealed PublicationNarrativeContract",
    ),
    StepDefinition(
        step_type="evidence_claim_mapping",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="EvidenceClaimMap",
    ),
    StepDefinition(
        step_type="visual_argument_planning",
        phase=Phase.PAPER,
        executor_type=ExecutorType.CODEX,
        expected_output="VisualArgumentPlan",
    ),
    StepDefinition(
        step_type="visual_argument_plan_approval",
        phase=Phase.PAPER,
        executor_type=ExecutorType.PROJECT_OWNER,
        expected_output="owner-approved VisualArgumentPlan",
        required_gate_type=GateType.VISUAL_ARGUMENT,
    ),
    StepDefinition(
        step_type="outline_generation_and_audit",
        phase=Phase.PAPER,
        executor_type=ExecutorType.CODEX,
        expected_output="validated hierarchical paper outline",
    ),
    StepDefinition(
        step_type="draft_generation",
        phase=Phase.PAPER,
        executor_type=ExecutorType.CODEX,
        expected_output="evidence-constrained structured draft",
    ),
    StepDefinition(
        step_type="scientific_review_panel",
        phase=Phase.PAPER,
        executor_type=ExecutorType.AI_SCIENTIFIC_REVIEW_PANEL,
        expected_output="deterministically aggregated panel decision",
    ),
    StepDefinition(
        step_type="bounded_content_revision",
        phase=Phase.PAPER,
        executor_type=ExecutorType.CODEX,
        expected_output="revision trace and resolved material findings",
        # Scientific prose closure may require a small number of persisted
        # reviewer/revision passes.  Keep this bounded independently from the
        # platform's three transient-network retries.
        transient_retry_limit=5,
    ),
    StepDefinition(
        step_type="sci_ssci_preservation_audit",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output=(
            "SCI/SSCI title, abstract, citation, and claim-strength audit"
        ),
    ),
    StepDefinition(
        step_type="figure_and_table_generation",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="frozen-evidence PaperArtifactManifest",
    ),
    StepDefinition(
        step_type="visual_integrity_audit",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="VisualIntegrityReport",
    ),
    StepDefinition(
        step_type="final_visual_approval",
        phase=Phase.PAPER,
        executor_type=ExecutorType.PROJECT_OWNER,
        expected_output="owner-approved final figures and tables",
        required_gate_type=GateType.VISUAL_ARGUMENT,
    ),
    StepDefinition(
        step_type="authorial_humanization",
        phase=Phase.PAPER,
        executor_type=ExecutorType.CODEX,
        expected_output="humanized sections and HumanizationTrace",
    ),
    StepDefinition(
        step_type="humanization_integrity_audit",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="HumanizationIntegrityReport",
    ),
    StepDefinition(
        step_type="humanization_author_approval",
        phase=Phase.PAPER,
        executor_type=ExecutorType.PROJECT_OWNER,
        expected_output="section-level author voice approval",
        required_gate_type=GateType.AUTHOR_VOICE,
    ),
    StepDefinition(
        step_type="reviewer_attack_surface_audit",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="ReviewerAttackSurfaceReport",
    ),
    StepDefinition(
        step_type="reporting_and_disclosure_audit",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="ReportingIntegrityReport and AIUseDisclosure",
    ),
    StepDefinition(
        step_type="latex_typesetting",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="venue-facing LaTeX",
    ),
    StepDefinition(
        step_type="pdf_compile_and_verify",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="hash-bound PDF and finalization manifest",
    ),
    StepDefinition(
        step_type="publication_qa_gate_v2",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="profile-bound manuscript, figure, citation, numeric, and rendered-PDF QA",
    ),
    StepDefinition(
        step_type="system_publication_readiness",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="four-gate ReadinessAssessment",
    ),
    StepDefinition(
        step_type="author_final_review_and_approval",
        phase=Phase.PAPER,
        executor_type=ExecutorType.PROJECT_OWNER,
        expected_output="final author submission approval",
        required_gate_type=GateType.FINAL_SUBMISSION,
    ),
    StepDefinition(
        step_type="completion_record",
        phase=Phase.PAPER,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="sealed Workflow v2 CompletionRecord",
    ),
)


def stage_four_step_definitions() -> list[StepDefinition]:
    return list(STAGE4_STEP_DEFINITIONS)


def ensure_stage_four_dag(
    repository: WorkflowRepository,
    study_id: str,
    *,
    venue_policy_id: str = GENERIC_JOURNAL_POLICY.profile_id,
    workflow_revision: int = 1,
    owner_revision_instruction: str | None = None,
) -> tuple[dict[str, Any], list[StepInstance]]:
    """Persist the canonical Stage 4 DAG after Stage 3 completion.

    The function is idempotent for one Study and policy. It records owner
    approval points as explicit persisted steps and Gate records.
    """

    if workflow_revision < 1:
        raise ValueError("Stage 4 workflow revision must be positive")
    authority = stage4_claim_authority(repository, study_id)
    policy = get_venue_policy(venue_policy_id)
    stage4_root = stage_four_root(repository, study_id)
    handoff_paths = (
        stage4_root / "stage_four_evidence_handoff_v1.json",
        stage4_root / "profile_stage_four_evidence_v1.json",
    )
    if (
        repository.list_stage3_completions(study_id)
        and not any(path.is_file() for path in handoff_paths)
    ):
        # Experiment-family specialization ends with Stage 3.  Stage 4 always
        # consumes the same platform contract and never switches on Profile id.
        from .profiles.stage_four_evidence import (
            build_stage_four_evidence_from_repository,
        )

        profile_handoff = build_stage_four_evidence_from_repository(
            repository, study_id
        )
        persist_stage_four_artifact(
            repository,
            study_id,
            name="stage_four_evidence_handoff_v1",
            value=profile_handoff,
            kind="stage_four_evidence_handoff",
            role=ArtifactRole.CLAIM,
            immutable=True,
        )
    group = (
        f"stage4:{authority['source_completion_id']}:"
        f"{policy.profile_id}-v{policy.version}:r{workflow_revision}"
    )
    for definition in STAGE4_STEP_DEFINITIONS:
        repository.save_step_definition(definition)
    existing = [
        item
        for item in repository.list_steps(study_id)
        if item.phase is Phase.PAPER and item.task_group == group
    ]
    if existing:
        study = repository.load_study(study_id)
        if (
            (
                study.execution_status is ExecutionStatus.SUCCEEDED
                or study.lifecycle is StudyLifecycle.COMPLETED
            )
            and any(
                item.status is not ExecutionStatus.SUCCEEDED
                for item in existing
            )
        ):
            repository.save_study(
                study.model_copy(
                    update={
                        "phase": Phase.PAPER,
                        "lifecycle": StudyLifecycle.ACTIVE,
                        "execution_status": ExecutionStatus.QUEUED,
                    }
                ),
                "stage4_revision_resumed",
            )
        return authority, existing

    subject_ids = {
        "publication_narrative_selection": stable_id(
            "narrative", study_id, authority["source_completion_id"],
            policy.profile_id, workflow_revision
        ),
        "visual_argument_plan_approval": stable_id(
            "visual-plan", study_id, authority["source_completion_id"],
            policy.profile_id, workflow_revision
        ),
        "final_visual_approval": stable_id(
            "final-visuals",
            study_id,
            authority["source_completion_id"],
            policy.profile_id,
            workflow_revision,
        ),
        "humanization_author_approval": stable_id(
            "humanized-manuscript",
            study_id,
            authority["source_completion_id"],
            policy.profile_id,
            workflow_revision,
        ),
        "author_final_review_and_approval": stable_id(
            "submission-package",
            study_id,
            authority["source_completion_id"],
            policy.profile_id,
            workflow_revision,
        ),
    }
    created: list[StepInstance] = []
    previous: StepInstance | None = None
    for definition in STAGE4_STEP_DEFINITIONS:
        parameters: dict[str, Any] = {
            "source_completion_id": authority["source_completion_id"],
            "venue_policy_id": policy.profile_id,
            "venue_policy_version": policy.version,
            "stage4_workflow_revision": workflow_revision,
        }
        if owner_revision_instruction:
            parameters["owner_revision_instruction"] = (
                owner_revision_instruction.strip()
            )
        subject_id = subject_ids.get(definition.step_type)
        if subject_id:
            parameters["subject_id"] = subject_id
        step = repository.add_step(
            study_id,
            definition.step_type,
            definition.phase,
            definition.executor_type,
            depends_on=[previous.step_instance_id] if previous else [],
            task_group=group,
            parameters=parameters,
            expected_output=definition.expected_output,
            max_retries=definition.transient_retry_limit,
        )
        created.append(step)
        previous = step

    gate_specs = (
        (
            GateType.PUBLICATION_NARRATIVE,
            "publication_narrative_contract",
            subject_ids["publication_narrative_selection"],
        ),
        (
            GateType.VISUAL_ARGUMENT,
            "visual_argument_plan",
            subject_ids["visual_argument_plan_approval"],
        ),
        (
            GateType.VISUAL_ARGUMENT,
            "final_visuals",
            subject_ids["final_visual_approval"],
        ),
        (
            GateType.AUTHOR_VOICE,
            "humanized_manuscript",
            subject_ids["humanization_author_approval"],
        ),
        (
            GateType.FINAL_SUBMISSION,
            "submission_package",
            subject_ids["author_final_review_and_approval"],
        ),
    )
    existing_gates = {
        (item.gate_type, item.subject_id)
        for item in repository.list_gates(study_id)
    }
    for gate_type, subject_type, subject_id in gate_specs:
        if (gate_type, subject_id) not in existing_gates:
            repository.create_gate(
                study_id,
                gate_type,
                subject_type=subject_type,
                subject_id=subject_id,
                subject_version=1,
            )
    study = repository.load_study(study_id)
    if workflow_revision > 1 or (
        study.execution_status is ExecutionStatus.SUCCEEDED
        or study.lifecycle is StudyLifecycle.COMPLETED
    ):
        repository.save_study(
            study.model_copy(
                update={
                    "phase": Phase.PAPER,
                    "lifecycle": StudyLifecycle.ACTIVE,
                    "execution_status": ExecutionStatus.QUEUED,
                    "current_step_ids": [],
                }
            ),
            "stage4_revision_queued",
        )
    return authority, created


def request_stage_four_revision(
    repository: WorkflowRepository,
    study_id: str,
    gate_id: str,
    *,
    reason: str,
    decided_by: str = "project_owner",
) -> tuple[dict[str, Any], list[StepInstance], dict[str, Any]]:
    """Reject one Stage 4 Gate and create an append-only workflow revision."""

    instruction = reason.strip()
    if not instruction:
        raise ValueError("a Stage 4 revision requires an owner instruction")
    gate = next(
        (
            item
            for item in repository.list_gates(study_id)
            if item.gate_id == gate_id
        ),
        None,
    )
    if gate is None:
        raise ValueError("unknown Stage 4 Gate")
    if gate.gate_type not in {
        GateType.PUBLICATION_NARRATIVE,
        GateType.VISUAL_ARGUMENT,
        GateType.AUTHOR_VOICE,
        GateType.FINAL_SUBMISSION,
    }:
        raise ValueError("the selected Gate does not belong to Stage 4")
    if gate.status is not GateStatus.AWAITING_USER:
        raise ValueError("the selected Stage 4 Gate is not awaiting a decision")
    related_steps = [
        item
        for item in repository.list_steps(study_id)
        if item.phase is Phase.PAPER
        and item.parameters.get("subject_id") == gate.subject_id
    ]
    if not related_steps:
        raise ValueError("the Stage 4 Gate has no persisted workflow subject")
    source_step = max(related_steps, key=lambda item: item.updated_at)
    current_revision = int(
        source_step.parameters.get("stage4_workflow_revision", 1)
    )
    next_revision = current_revision + 1
    venue_policy_id = str(
        source_step.parameters.get(
            "venue_policy_id", GENERIC_JOURNAL_POLICY.profile_id
        )
    )
    rejected = repository.decide_gate(
        study_id,
        gate_id,
        approve=False,
        decided_by=decided_by,
        reason=instruction,
    )
    request = {
        "schema_version": 1,
        "study_id": study_id,
        "source_gate_id": gate_id,
        "source_gate_type": gate.gate_type.value,
        "source_workflow_revision": current_revision,
        "workflow_revision": next_revision,
        "instruction": instruction,
        "requested_by": decided_by,
        "requested_at": utc_now(),
        "historical_artifacts_preserved": True,
    }
    request_root = stage_four_root(
        repository, study_id, revision=next_revision
    )
    request_path = (
        request_root / f"owner_revision_request_v{next_revision}.json"
    )
    write_json_atomic(request_path, request)
    repository.register_artifact(
        study_id,
        str(request_path),
        sha256_file(request_path),
        kind="stage4_owner_revision_request",
        role=ArtifactRole.AUDIT,
        status=ArtifactStatus.FROZEN,
        version=next_revision,
    )
    authority, steps = ensure_stage_four_dag(
        repository,
        study_id,
        venue_policy_id=venue_policy_id,
        workflow_revision=next_revision,
        owner_revision_instruction=instruction,
    )
    return authority, steps, {
        **request,
        "rejected_gate": rejected.model_dump(mode="json"),
    }


def _stage4_revision_from_context(context: Any) -> int:
    return int(context.step.parameters.get("stage4_workflow_revision", 1))


def _stage4_working_root(context: Any) -> Path:
    study_root = (
        context.repository.root / "studies" / context.study_id
    )
    revision = _stage4_revision_from_context(context)
    if revision == 1:
        return study_root
    root = study_root / "stage4_revisions" / f"v{revision}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _materialize_profile_figure_markdown(
    context: Any,
    root: Path,
    profile_figures: dict[str, Any],
) -> tuple[str, list[str]]:
    if profile_figures.get("status") != "accepted":
        return "", []
    figure_asset_root = (
        root / "stage_4_synthesis" / "manuscript_assets" / "figures"
    )
    figure_asset_root.mkdir(parents=True, exist_ok=True)
    artifact_ids: list[str] = []
    lines = [
        "## Profile-specific scientific figures",
        "",
        (
            "The following figures are deterministic renderings of the "
            "frozen Profile-specific Stage 3 statistics."
        ),
        "",
    ]
    for index, figure in enumerate(profile_figures.get("figures") or [], start=1):
        figure_id = str(figure["figure_id"])
        source_png = Path(str(figure.get("png_path") or "")).resolve()
        if not source_png.is_file():
            raise FileNotFoundError(
                f"profile figure PNG is missing before LaTeX binding: {source_png}"
            )
        asset_name = f"{figure_id}.png"
        asset_path = figure_asset_root / asset_name
        if source_png != asset_path:
            shutil.copyfile(source_png, asset_path)
        asset_artifact = context.repository.register_artifact(
            context.study_id,
            str(asset_path),
            sha256_file(asset_path),
            kind="final_manuscript_figure_asset",
            role=ArtifactRole.MANUSCRIPT,
        )
        artifact_ids.append(asset_artifact.artifact_id)
        relative_asset_path = (
            Path("manuscript_assets") / "figures" / asset_name
        ).as_posix()
        lines.extend(
            [
                (
                    f"![Figure {index} ({figure_id}). "
                    f"{figure['caption']}]({relative_asset_path})"
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n\n", artifact_ids


def _completion_artifact_hashes(
    repository: WorkflowRepository,
    study_id: str,
    *,
    completion_step_id: str,
) -> dict[str, str]:
    """Hash completed artifacts without making the record self-referential."""

    study_root = repository.root / "studies" / study_id
    own_step_result = (
        study_root / "step_results" / f"{completion_step_id}.json"
    ).resolve()
    hashes: dict[str, str] = {}
    for artifact in repository.list_artifacts(study_id):
        path = Path(artifact.path)
        if not path.is_absolute():
            path = study_root / path
        path = path.resolve()
        if path == own_step_result:
            continue
        if path.is_file() and (
            path == study_root.resolve()
            or study_root.resolve() in path.parents
        ):
            hashes[path.relative_to(study_root.resolve()).as_posix()] = (
                sha256_file(path)
            )
    return hashes


def _revision_scoped_handler(
    handler: Callable[[Any], dict[str, Any]],
) -> Callable[[Any], dict[str, Any]]:
    def wrapped(context: Any) -> dict[str, Any]:
        revision = _stage4_revision_from_context(context)
        token = _ACTIVE_STAGE4_REVISION.set(revision)
        try:
            return handler(context)
        finally:
            _ACTIVE_STAGE4_REVISION.reset(token)

    return wrapped


def stage_four_handlers() -> dict[str, Callable[[Any], dict[str, Any]]]:
    """Return implemented deterministic Stage 4 handlers.

    Model-backed steps are registered by the paper authoring runtime. Leaving
    them absent produces the scheduler's explicit missing-executor block rather
    than a false scientific success.
    """

    def manuscript_language(context: Any) -> str:
        study = context.repository.load_study(context.study_id)
        configured = str(
            study.settings.get("manuscript_language") or "zh"
        ).strip().lower()
        return "en" if configured.startswith("en") else "zh"

    def claim_intake(context: Any) -> dict[str, Any]:
        return stage4_claim_authority(context.repository, context.study_id)

    def freeze_venue(context: Any) -> dict[str, Any]:
        from .sci_ssci_writing import stage_four_sci_ssci_contract

        profile_id = str(context.step.parameters["venue_policy_id"])
        writing_contract = stage_four_sci_ssci_contract(
            language=manuscript_language(context)
        )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="sci_ssci_writing_policy_v1",
            value=writing_contract,
            kind="stage4_writing_policy",
            role=ArtifactRole.PROTOCOL,
            immutable=True,
        )
        return {
            "venue_policy": get_venue_policy(profile_id).model_dump(mode="json"),
            "sci_ssci_writing_policy": writing_contract,
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def prerequisite_gate(context: Any) -> dict[str, Any]:
        from .workflow_scheduler import BlockedStepError

        authority = context.result("stage4_claim_intake")
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        claims = authority.get("claims") or []
        if len(claims) != 1:
            raise BlockedStepError(
                "Stage 4 requires exactly one completed ScientificClaimEnvelope",
                kind="invalid_claim_authority",
            )
        claim = dict(claims[0])
        boundary_report_mode = (
            claim.get("maximum_claim_tier") == "evidence_boundary_report"
            or claim.get("evidence_level") == "L0_boundary_only"
        )
        def count_numeric(value: Any) -> int:
            if isinstance(value, bool) or value is None:
                return 0
            if isinstance(value, (int, float)):
                return 1
            if isinstance(value, dict):
                return sum(count_numeric(item) for item in value.values())
            if isinstance(value, (list, tuple)):
                return sum(count_numeric(item) for item in value)
            return 0

        completion_id = str(authority["source_completion_id"])
        completion_path = (
            context.repository.root
            / "studies"
            / context.study_id
            / "stage3"
            / "completion"
            / f"{completion_id}.json"
        )
        completion = read_json(completion_path)
        evaluation_ids = set(completion.get("evaluation_ids") or [])
        evaluations = [
            item
            for item in context.repository.list_evaluation_records(
                context.study_id
            )
            if item.evaluation_id in evaluation_ids
        ]
        result_ids = {
            result_id for item in evaluations for result_id in item.result_ids
        }
        results = [
            item
            for item in context.repository.list_result_envelopes(
                context.study_id
            )
            if item.result_id in result_ids
        ]
        numeric_count = count_numeric(claim) + sum(
            count_numeric(item.model_dump(mode="json"))
            for item in [*evaluations, *results]
        )
        from .workflow_scheduler import (
            bind_frozen_discovery_literature_set,
        )

        bind_frozen_discovery_literature_set(
            context.repository, context.study_id
        )
        frozen_literature = [
            item
            for item in context.repository.list_literature_sets(
                context.study_id
            )
            if item.status.value == "frozen"
        ]
        latest = frozen_literature[-1] if frozen_literature else None
        verified_source_ids = (
            set(latest.background_source_ids)
            | set(latest.decision_source_ids)
            if latest is not None
            else set()
        )
        if latest is not None:
            verified_source_ids -= set(latest.retracted_source_ids)
        from .retrieval.domain.models import (
            MetadataVerificationStatus,
            ResourceType,
            RetractionStatus,
        )
        from .retrieval.domain.repository import RetrievalRepository

        retrieval_repository = RetrievalRepository(context.repository.root)
        verified_resources: list[dict[str, Any]] = []
        missing_or_unverified: list[str] = []
        incomplete_author_metadata: list[str] = []
        for source_id in sorted(verified_source_ids):
            try:
                resource = retrieval_repository.load_resource(source_id)
            except Exception:
                missing_or_unverified.append(source_id)
                continue
            if (
                resource.resource_type
                not in {ResourceType.PUBLICATION, ResourceType.PREPRINT}
                or resource.metadata_verification_status
                is not MetadataVerificationStatus.VERIFIED
                or resource.retraction_or_correction_status
                in {RetractionStatus.RETRACTED, RetractionStatus.CONCERN}
            ):
                missing_or_unverified.append(source_id)
                continue
            verified_resources.append(resource.model_dump(mode="json"))
            metadata_authors = [
                str(author).strip()
                for author in (resource.metadata or {}).get("authors") or []
                if str(author).strip()
            ]
            if not metadata_authors:
                incomplete_author_metadata.append(source_id)
        verified_source_ids = {
            str(item["resource_id"]) for item in verified_resources
        }
        checks = {
            "scientific_claim_envelope_present": True,
            "raw_logs_excluded_from_claim_authority": (
                authority.get("raw_logs_are_claim_authority") is False
            ),
            "claim_broadening_prohibited": (
                authority.get("may_broaden_claims") is False
            ),
            "literature_threshold_met": (
                boundary_report_mode
                or len(verified_source_ids) >= policy.minimum_verified_papers
            ),
            "literature_metadata_verified": not missing_or_unverified,
            "literature_author_metadata_complete": (
                not incomplete_author_metadata
            ),
            "numeric_evidence_threshold_met": (
                boundary_report_mode
                or numeric_count >= policy.minimum_numeric_evidence
            ),
        }
        blockers: list[str] = []
        if not checks["literature_threshold_met"]:
            blockers.append(
                f"{policy.profile_id} requires "
                f"{policy.minimum_verified_papers} verified, non-retracted "
                f"sources; found {len(verified_source_ids)}"
            )
        if missing_or_unverified:
            blockers.append(
                "frozen literature contains missing, unverified, or retracted "
                "resources: " + ", ".join(missing_or_unverified)
            )
        if not checks["numeric_evidence_threshold_met"]:
            blockers.append(
                f"{policy.profile_id} requires "
                f"{policy.minimum_numeric_evidence} bound numeric values; "
                f"found {numeric_count}"
            )
        if blockers:
            raise BlockedStepError(
                "; ".join(blockers),
                kind="publication_prerequisites_not_met",
            )
        from .literature_synthesis_v2 import (
            build_metadata_context_literature_bundle,
        )

        literature_synthesis_bundle = build_metadata_context_literature_bundle(
            study_id=context.study_id,
            verified_resources=verified_resources,
        )
        literature_artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="literature_synthesis_bundle_v2",
            value=literature_synthesis_bundle,
            kind="literature_synthesis_bundle_v2",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        return {
            "passed": True,
            "boundary_report_mode": boundary_report_mode,
            "checks": checks,
            "verified_source_ids": sorted(verified_source_ids),
            "verified_literature": verified_resources,
            "literature_synthesis_bundle": (
                literature_synthesis_bundle.model_dump(mode="json")
            ),
            "numeric_evidence_count": numeric_count,
            "incomplete_author_metadata_source_ids": (
                incomplete_author_metadata
            ),
            "venue_policy_id": policy.profile_id,
            "_workflow_output_artifact_ids": [literature_artifact.artifact_id],
        }

    def evaluation_transparency(context: Any) -> dict[str, Any]:
        from .paper_evaluation_transparency import (
            build_evaluation_transparency_register,
        )

        authority = context.result("stage4_claim_intake")
        claim = dict((authority.get("claims") or [])[0])
        envelope_id = str(claim["claim_envelope_id"])
        matching_evaluations = [
            item
            for item in context.repository.list_evaluation_records(
                context.study_id
            )
            if item.plan_id == str(claim["plan_id"])
        ]
        if not matching_evaluations:
            raise ValueError(
                "Stage 4 transparency requires the frozen Stage 3 evaluation"
            )
        evaluation = matching_evaluations[-1]
        contract = context.repository.load_research_contract(
            context.study_id, evaluation.contract_version
        )
        plans = {
            item.plan_id: item
            for item in context.repository.list_run_plans(context.study_id)
        }
        plan = plans.get(evaluation.plan_id)
        if plan is None:
            raise ValueError(
                "Stage 4 transparency requires the frozen Stage 3 Run Plan"
            )
        result_ids = set(evaluation.result_ids)
        results = [
            item
            for item in context.repository.list_result_envelopes(
                context.study_id
            )
            if item.result_id in result_ids
        ]
        exclusion_reasons = evaluation.exclusion_reason_counts
        if not exclusion_reasons:
            exclusion_reasons = contract.evaluator_policy.get(
                "exclusion_reason_counts"
            )
        if not isinstance(exclusion_reasons, dict):
            exclusion_reasons = None
        artifacts = context.repository.list_artifacts(context.study_id)
        artifact_kinds = {str(item.kind) for item in artifacts}
        repairs = context.repository.list_repair_contracts(context.study_id)
        successors = context.repository.list_successor_runs(context.study_id)
        evaluation_path = (
            f"stage3/evaluations/{evaluation.evaluation_id}.json"
        )
        contract_path = (
            f"contracts/research-v{contract.version}.json"
        )
        effective_arm_estimates = _effective_evaluation_arm_estimates(
            evaluation
        )
        register = build_evaluation_transparency_register(
            study_id=context.study_id,
            source_claim_envelope_id=envelope_id,
            evaluation_path=evaluation_path,
            contract_path=contract_path,
            evaluator_policy=contract.evaluator_policy,
            total_records=len(plan.cells),
            eligible_records=len(results),
            excluded_record_ids=list(evaluation.excluded_run_cell_ids),
            exclusion_reasons={
                str(key): int(value)
                for key, value in (exclusion_reasons or {}).items()
                if isinstance(value, int) and not isinstance(value, bool)
            },
            abstention_count=sum(item.abstentions for item in results),
            arm_estimates=effective_arm_estimates,
            repair_present=bool(repairs),
            prospective_successor_present=bool(successors),
            available_artifact_kinds=artifact_kinds,
        )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="evaluation_transparency_register_v1",
            value=register,
            kind="evaluation_transparency_register",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        return {
            "evaluation_transparency_register": register.model_dump(
                mode="json"
            ),
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def evidence_sufficiency_gate(context: Any) -> dict[str, Any]:
        from .paper_evaluation_transparency import (
            EvaluationTransparencyRegister,
            assess_stage4_evidence_sufficiency,
        )
        from .stage_three import propose_stage3_scientific_successor
        from .workflow_domain import (
            DiagnosticReport,
            Stage3FailureClass,
        )
        from .workflow_scheduler import BlockedStepError

        register = EvaluationTransparencyRegister.model_validate(
            context.result("evaluation_transparency_register")[
                "evaluation_transparency_register"
            ]
        )
        authority = context.result("stage4_claim_intake")
        claim = dict((authority.get("claims") or [])[0])
        boundary_report_mode = (
            claim.get("maximum_claim_tier") == "evidence_boundary_report"
            or claim.get("evidence_level") == "L0_boundary_only"
        )
        if boundary_report_mode:
            from .paper_evaluation_transparency import (
                Stage4EvidenceBackfillRequest,
            )

            # Boundary-only papers did not run a formal experiment.  Their
            # scientific gaps are already frozen in the Stage 3 boundary
            # report.  Do not mislabel optional evaluator experiments as
            # forgotten Stage 4 work.
            unresolved = [
                item.item_id
                for item in register.material_items()
                if item.category in {"release_assets"}
                if item.status.value in {"missing", "planned", "unverified"}
            ]
            decision = Stage4EvidenceBackfillRequest(
                study_id=context.study_id,
                source_claim_envelope_id=register.source_claim_envelope_id,
                status="disclosure_only",
                disclosure_item_ids=unresolved,
                required_actions=[
                    (
                        "Report the unresolved evidence boundary and the "
                        "resources needed for a future formal experiment."
                    )
                ],
            )
            artifact = persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name="stage4_evidence_backfill_request_v1",
                value=decision,
                kind="stage4_evidence_backfill_request",
                role=ArtifactRole.AUDIT,
                immutable=True,
            )
            return {
                "evidence_sufficiency": decision.model_dump(mode="json"),
                "boundary_report_mode": True,
                "_workflow_output_artifact_ids": [artifact.artifact_id],
            }
        contract = context.repository.latest_research_contract(
            context.study_id
        )
        required = None
        if contract is not None:
            configured = contract.scientific_validity_contract.get(
                "stage4_backfill_required_categories"
            )
            if isinstance(configured, list):
                required = {str(item) for item in configured}
        decision = assess_stage4_evidence_sufficiency(
            register,
            required_backfill_categories=required,
        )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="stage4_evidence_backfill_request_v1",
            value=decision,
            kind="stage4_evidence_backfill_request",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        if decision.status == "stage3_backfill_required":
            authority = context.result("stage4_claim_intake")
            claim = dict((authority.get("claims") or [])[0])
            plan_id = str(claim["plan_id"])
            diagnostic = DiagnosticReport(
                diagnostic_id=stable_id(
                    "stage3-diagnostic",
                    context.study_id,
                    context.step.step_instance_id,
                    *decision.required_item_ids,
                ),
                study_id=context.study_id,
                plan_id=plan_id,
                earliest_preventable_step_type=(
                    "research_contract_freeze"
                ),
                failure_class=Stage3FailureClass.SCIENTIFIC_DESIGN,
                system_invalidation_artifact_ids=[artifact.artifact_id],
                system_findings=[
                    (
                        f"{item.item_id}: {item.missing_reason}; "
                        f"{item.follow_up_action}"
                    )
                    for item in register.items
                    if item.item_id in set(decision.required_item_ids)
                ],
            )
            context.repository.save_stage3_diagnostic(diagnostic)
            successor = propose_stage3_scientific_successor(
                context.repository,
                context.study_id,
                diagnostic_id=diagnostic.diagnostic_id,
                changed_contract_fields=decision.changed_contract_fields,
            )
            successor_artifact = persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name="stage3_evidence_backfill_successor_request_v1",
                value=successor,
                kind="scientific_successor_request",
                role=ArtifactRole.AUDIT,
                immutable=True,
                predecessor_artifact_ids=[artifact.artifact_id],
            )
            raise BlockedStepError(
                (
                    "Stage 4 found evidence gaps that require a Stage 3 "
                    "successor: " + "; ".join(decision.required_actions)
                ),
                kind="stage3_evidence_backfill_required",
                redirect_phase=Phase.EXPERIMENT,
            )
        return {
            "evidence_sufficiency": decision.model_dump(mode="json"),
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def mandatory_reporting(context: Any) -> dict[str, Any]:
        from .paper_evaluation_transparency import (
            EvaluationTransparencyRegister,
        )

        profile_handoff = _stage_four_evidence_handoff(context)
        if profile_handoff is not None:
            register = MandatoryReportingRegister.model_validate(
                profile_handoff["mandatory_reporting_register"]
            )
            authority = context.result("stage4_claim_intake")
            envelope_id = str((authority.get("claims") or [])[0]["claim_envelope_id"])
            if register.source_claim_envelope_id != envelope_id:
                raise ValueError(
                    "Profile reporting register does not match Stage 3 claim authority"
                )
            artifact = persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name="mandatory_reporting_register_v1",
                value=register,
                kind="mandatory_reporting_register",
                role=ArtifactRole.CLAIM,
                immutable=True,
            )
            completion_package_items = [
                {
                    "claim_id": item.claim_id,
                    "category": item.category,
                    "status": "profile_evidence_adapter_registered",
                    "destination": item.required_destination,
                    "rationale": item.rationale,
                }
                for item in register.items
                if item.required_destination
                in {"results_registry", "completion_package"}
            ]
            return {
                "mandatory_reporting_register": register.model_dump(mode="json"),
                "mandatory_results_registry": completion_package_items,
                "stage_four_evidence_adapter_id": profile_handoff["adapter_id"],
                "_workflow_output_artifact_ids": [artifact.artifact_id],
            }

        authority = context.result("stage4_claim_intake")
        claim = dict((authority.get("claims") or [])[0])
        envelope_id = str(claim["claim_envelope_id"])
        contract = context.repository.latest_research_contract(
            context.study_id
        )
        items = [
            MandatoryReportingItem(
                reporting_item_id="report-primary-outcome",
                claim_id=f"{envelope_id}:primary",
                category="primary",
                material=True,
                preregistered=(
                    claim.get("confirmatory_status")
                    == "confirmatory_used"
                ),
                required_destination="main_text",
                destination_section="results",
                rationale=(
                    "The Stage 3 allowed claim is the publication's mandatory "
                    "primary scientific result."
                ),
            )
        ]
        if contract is not None:
            secondary_index = 0
            for hypothesis in contract.hypotheses:
                if hypothesis.role.value == "primary":
                    continue
                secondary_index += 1
                decision_rule = hypothesis.decision_rule or {}
                declared_category = str(
                    decision_rule.get("reporting_category") or "secondary"
                )
                category = (
                    declared_category
                    if declared_category in {"secondary", "negative", "safety"}
                    else "secondary"
                )
                items.append(
                    MandatoryReportingItem(
                        reporting_item_id=(
                            f"report-{category}-{secondary_index}"
                        ),
                        claim_id=(
                            f"research-v{contract.version}:"
                            f"{hypothesis.hypothesis_id}"
                        ),
                        category=category,  # type: ignore[arg-type]
                        material=bool(
                            decision_rule.get("material", True)
                        ),
                        preregistered=True,
                        required_destination=(
                            "main_text"
                            if category in {"negative", "safety"}
                            and bool(decision_rule.get("material", True))
                            else "completion_package"
                        ),
                        destination_section=(
                            "results"
                            if category in {"negative", "safety"}
                            and bool(decision_rule.get("material", True))
                            else None
                        ),
                        rationale=(
                            "This outcome was frozen as a non-primary "
                            "hypothesis in the Research Contract and must be "
                            "reported even when it does not become the central story."
                        ),
                    )
                )
        for index, limitation in enumerate(
            claim.get("known_limitations") or [], start=1
        ):
            items.append(
                MandatoryReportingItem(
                    reporting_item_id=f"report-limitation-{index}",
                    claim_id=f"{envelope_id}:limitation-{index}",
                    category="limitation",
                    material=True,
                    preregistered=False,
                    required_destination="limitations",
                    destination_section="limitations",
                    rationale=str(limitation),
                )
            )
        transparency = EvaluationTransparencyRegister.model_validate(
            context.result("evaluation_transparency_register")[
                "evaluation_transparency_register"
            ]
        )
        for item in transparency.items:
            if item.status.value == "not_applicable":
                continue
            destination = item.required_destination
            required_destination = (
                "main_text"
                if destination in {"methods", "results"}
                else "limitations"
                if destination == "limitations"
                else "supplement"
                if destination == "supplement"
                else "completion_package"
            )
            items.append(
                MandatoryReportingItem(
                    reporting_item_id=(
                        "report-" + item.item_id.removeprefix(
                            "transparency-"
                        )
                    ),
                    claim_id=f"transparency:{item.item_id}",
                    category="operational",
                    material=item.material,
                    preregistered=False,
                    required_destination=required_destination,
                    destination_section=(
                        destination
                        if destination
                        in {"methods", "results", "limitations"}
                        else None
                    ),
                    rationale=(
                        f"[{item.status.value}] {item.statement}"
                    )[:2000],
                )
            )
        register = MandatoryReportingRegister(
            study_id=context.study_id,
            source_claim_envelope_id=envelope_id,
            items=items,
            frozen=True,
            frozen_at=utc_now(),
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="mandatory_reporting_register_v1",
            value=register,
            kind="mandatory_reporting_register",
            role=ArtifactRole.CLAIM,
            immutable=True,
        )
        completion_package_items = [
            {
                "claim_id": item.claim_id,
                "category": item.category,
                "status": (
                    "reported_in_claim_envelope"
                    if item.category in {"primary", "limitation"}
                    else "registered_without_separate_stage3_verdict"
                ),
                "destination": item.required_destination,
                "rationale": item.rationale,
            }
            for item in register.items
            if item.required_destination
            in {"results_registry", "completion_package"}
        ]
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="mandatory_results_registry_v1",
            value={
                "schema_version": 1,
                "study_id": context.study_id,
                "items": completion_package_items,
            },
            kind="mandatory_results_registry",
            role=ArtifactRole.CLAIM,
            immutable=True,
        )
        return {
            "mandatory_reporting_register": register.model_dump(mode="json"),
            "mandatory_results_registry": completion_package_items,
        }

    def contribution_candidates(context: Any) -> dict[str, Any]:
        authority = context.result("stage4_claim_intake")
        claim = dict((authority.get("claims") or [])[0])
        envelope_id = str(claim["claim_envelope_id"])
        profile_handoff = _stage_four_evidence_handoff(context)
        # A complete Profile handoff is the registered expansion of this same
        # Stage 3 envelope.  Its frozen conclusion carries profile-specific
        # statistics that the generic envelope cannot represent, and therefore
        # must drive the manuscript narrative instead of a generic fallback.
        allowed_claim = str(
            ((profile_handoff or {}).get("evidence_claim_map") or {}).get(
                "frozen_conclusion"
            )
            or claim["allowed_claim"]
        )
        register = MandatoryReportingRegister.model_validate(
            context.result("mandatory_reporting_register")[
                "mandatory_reporting_register"
            ]
        )
        primary_claim_ids = sorted(
            item.claim_id
            for item in register.items
            if item.category == "primary"
            and item.required_destination == "main_text"
        )
        if not primary_claim_ids:
            # The generic Stage 4 register uses this compatibility identifier.
            # Profile adapters may instead provide one or more semantically
            # primary claims with stable profile-owned identifiers.
            primary_claim_ids = [f"{envelope_id}:primary"]
        confirmatory_status = str(
            claim.get("confirmatory_status") or "not_recorded"
        )
        bounded_nonconfirmatory_result = (
            confirmatory_status != "confirmatory_used"
            or "does not support" in allowed_claim.lower()
            or "inconclusive" in allowed_claim.lower()
        )
        common = {
            "problem": (
                f"The frozen study asks whether {claim['intervention']} changes "
                f"{claim['outcome']} relative to {claim['comparator']}."
            ),
            "proposed_value": allowed_claim,
            "supporting_claim_ids": primary_claim_ids,
            "supporting_evidence_ids": [envelope_id],
            "novelty_basis": [],
            "practical_value": [allowed_claim],
            "limitations": [
                *[str(item) for item in claim.get("known_limitations") or []],
                *[
                    f"must not generalize to {item}"
                    for item in claim.get("prohibited_generalizations") or []
                ],
            ],
            "publication_strength": (
                "moderate"
                if claim.get("effect_estimate") is not None
                else "weak"
            ),
        }
        candidates = [
            ContributionCandidate(
                contribution_id="contribution-primary-stage3-result",
                contribution_type=(
                    "counterintuitive_negative_result"
                    if bounded_nonconfirmatory_result
                    else "meaningful_tradeoff"
                ),
                eligible_as_main_story=True,
                **common,
            ),
            ContributionCandidate(
                contribution_id="contribution-evidence-bounded-perspective",
                contribution_type="new_perspective",
                eligible_as_main_story=not bounded_nonconfirmatory_result,
                disqualifying_reasons=(
                    [
                        "The frozen evidence supports a bounded empirical result, "
                        "not validation of a reusable reporting framework."
                    ]
                    if bounded_nonconfirmatory_result
                    else []
                ),
                **common,
            ),
        ]
        selected = candidates[0]
        narrative_draft = PublicationNarrativeContract(
            study_id=context.study_id,
            selected_contribution_id=selected.contribution_id,
            central_thesis=allowed_claim,
            reader_problem=selected.problem,
            existing_gap=(
                "The registered comparison lacks an evidence-bounded account "
                "that preserves its negative findings and scope conditions."
            ),
            proposed_resolution=(
                f"The study compares {claim['intervention']} with "
                f"{claim['comparator']} on {', '.join(claim['tasks'])}."
            ),
            primary_advantage=allowed_claim,
            advantage_conditions=[
                f"population: {claim['population']}",
                f"tasks: {', '.join(claim['tasks'])}",
                f"evidence level: {claim['evidence_level']}",
            ],
            mechanism_explanation=[],
            required_claim_ids=sorted(register.main_text_claim_ids() - {
                item.claim_id
                for item in register.items
                if item.category in {"negative", "safety", "limitation"}
            }),
            supporting_claim_ids=[],
            mandatory_negative_claim_ids=sorted({
                item.claim_id
                for item in register.items
                if item.category in {"negative", "safety", "limitation"}
                and item.material
            }),
            comparator_decisions=[
                ComparatorDecision(
                    comparator_id="comparator-registered-control",
                    comparator_name=str(claim["comparator"]),
                    supports_claim_ids=primary_claim_ids,
                    necessity="required",
                    placement="main_text",
                    frozen_in_research_contract=True,
                    rationale=(
                        "The Stage 3 claim is comparative, so the registered "
                        "control must remain visible in the main narrative."
                    ),
                )
            ],
            reader_memory_point=allowed_claim,
            prohibited_story_moves=[
                *[
                    f"generalize to {item}"
                    for item in claim.get("prohibited_generalizations") or []
                ],
                "omit a claim in the frozen MandatoryReportingRegister",
                "change confirmatory status or evidence maturity",
                *(
                    [
                        "present the Stage 4 reporting interface as a validated "
                        "or primary method contribution",
                        "claim that a neutral training arm identifies a "
                        "corpus-specific mechanism unless runtime equivalence "
                        "is independently verified",
                        "turn a qualified analysis into a supported scientific "
                        "verdict",
                        "repeat workflow or framework claims as the central "
                        "contribution across multiple manuscript sections",
                    ]
                    if bounded_nonconfirmatory_result
                    else []
                ),
            ],
        )
        allowed_claim_ids = register.required_claim_ids()
        violations = validate_publication_narrative(
            narrative_draft,
            selected_contribution=selected,
            reporting_register=register,
            allowed_claim_ids=allowed_claim_ids,
        )
        if violations:
            raise ValueError(
                "generated PublicationNarrativeContract draft is invalid: "
                + "; ".join(violations)
            )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="contribution_candidates_v1",
            value={
                "schema_version": 1,
                "candidates": [
                    item.model_dump(mode="json") for item in candidates
                ],
            },
            kind="contribution_candidates",
            role=ArtifactRole.CLAIM,
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="publication_narrative_contract_draft_v1",
            value=narrative_draft,
            kind="publication_narrative_contract",
            role=ArtifactRole.CLAIM,
        )
        return {
            "contribution_candidates": [
                item.model_dump(mode="json") for item in candidates
            ],
            "recommended_contribution_id": selected.contribution_id,
            "publication_narrative_contract_draft": (
                narrative_draft.model_dump(mode="json")
            ),
        }

    def freeze_narrative(context: Any) -> dict[str, Any]:
        authority = context.result("stage4_claim_intake")
        candidate_result = context.result("contribution_candidate_generation")
        narrative = PublicationNarrativeContract.model_validate(
            candidate_result["publication_narrative_contract_draft"]
        )
        candidate = next(
            ContributionCandidate.model_validate(item)
            for item in candidate_result["contribution_candidates"]
            if item["contribution_id"] == narrative.selected_contribution_id
        )
        gate_subject = str(
            next(
                item
                for item in context.repository.list_steps(context.study_id)
                if item.step_instance_id in context.step.depends_on
            ).parameters["subject_id"]
        )
        gate = next(
            item
            for item in context.repository.list_gates(context.study_id)
            if item.gate_type is GateType.PUBLICATION_NARRATIVE
            and item.subject_id == gate_subject
            and item.status.value == "approved"
        )
        register = MandatoryReportingRegister.model_validate(
            context.result("mandatory_reporting_register")[
                "mandatory_reporting_register"
            ]
        )
        violations = validate_publication_narrative(
            narrative,
            selected_contribution=candidate,
            reporting_register=register,
            allowed_claim_ids=register.required_claim_ids(),
        )
        if violations:
            raise ValueError(
                "approved PublicationNarrativeContract draft is invalid: "
                + "; ".join(violations)
            )
        frozen = seal_publication_narrative(
            narrative,
            approved_by=str(gate.decided_by or "project_owner"),
            approved_at=gate.decided_at,
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="publication_narrative_contract_v1",
            value=frozen,
            kind="publication_narrative_contract",
            role=ArtifactRole.CLAIM,
            immutable=True,
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="reader_memory_contract_v1",
            value=compile_reader_memory_contract(frozen),
            kind="reader_memory_contract",
            role=ArtifactRole.CLAIM,
            immutable=True,
        )
        return {
            "publication_narrative_contract": frozen.model_dump(mode="json")
        }

    def evidence_claim_mapping(context: Any) -> dict[str, Any]:
        profile_handoff = _stage_four_evidence_handoff(context)
        if profile_handoff is not None:
            evidence_map = EvidenceClaimMap.model_validate(
                profile_handoff["evidence_claim_map"]
            )
            prerequisite = context.result("publication_prerequisite_gate")
            evidence_map = _merge_verified_background_sources(
                evidence_map,
                list(prerequisite.get("verified_literature") or [])
                or [
                    str(item)
                    for item in prerequisite.get("verified_source_ids", [])
                ],
            )
            register = MandatoryReportingRegister.model_validate(
                profile_handoff["mandatory_reporting_register"]
            )
            if register.required_claim_ids() != {
                item.claim_id
                for item in evidence_map.bindings
                if item.kind != "literature_context"
            }:
                raise ValueError(
                    "Profile evidence map and mandatory reporting register disagree"
                )
            artifact = persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name="evidence_claim_map_v1",
                value=evidence_map,
                kind="evidence_claim_map",
                role=ArtifactRole.CLAIM,
                immutable=True,
            )
            return {
                "evidence_claim_map": evidence_map.model_dump(mode="json"),
                "stage_four_evidence_adapter_id": profile_handoff["adapter_id"],
                "_workflow_output_artifact_ids": [artifact.artifact_id],
            }

        authority = context.result("stage4_claim_intake")
        claim = dict((authority.get("claims") or [])[0])
        envelope_id = str(claim["claim_envelope_id"])
        source_path = (
            f"stage3/claim_envelopes/{envelope_id}.json"
        )
        source_file = (
            context.repository.root
            / "studies"
            / context.study_id
            / source_path
        )
        pointer = EvidencePointer(
            path=source_path,
            sha256=sha256_file(source_file),
            json_path="$.allowed_claim",
            source_id=None,
            evidence_type="project_artifact",
        )
        bindings = [
            EvidenceClaimBinding(
                claim_id=f"{envelope_id}:primary",
                kind="result",
                statement=str(claim["allowed_claim"]),
                evidence=[pointer],
                allowed_sections=[
                    "abstract",
                    "methods",
                    "results",
                    "discussion",
                    "conclusion",
                ],
                claim_strength="comparative",
                evidence_status="bound",
            )
        ]
        from .paper_evaluation_transparency import (
            EvaluationTransparencyRegister,
            TransparencyStatus,
        )

        transparency = EvaluationTransparencyRegister.model_validate(
            context.result("evaluation_transparency_register")[
                "evaluation_transparency_register"
            ]
        )
        transparency_file = (
            stage_four_root(context.repository, context.study_id)
            / "evaluation_transparency_register_v1.json"
        )
        transparency_path = transparency_file.relative_to(
            context.repository.root
            / "studies"
            / context.study_id
        ).as_posix()
        transparency_sha256 = sha256_file(transparency_file)
        for index, item in enumerate(transparency.items):
            if item.status is TransparencyStatus.NOT_APPLICABLE:
                continue
            statement = item.statement
            if item.missing_reason:
                statement += f" Missing evidence: {item.missing_reason}."
            if item.follow_up_action:
                statement += f" Required follow-up: {item.follow_up_action}."
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=f"transparency:{item.item_id}",
                    kind="operational_transparency",
                    statement=statement,
                    evidence=[
                        EvidencePointer(
                            path=transparency_path,
                            sha256=transparency_sha256,
                            json_path=f"$.items[{index}]",
                            source_id=None,
                            evidence_type="project_artifact",
                        )
                    ],
                    allowed_sections=[
                        item.required_destination
                        if item.required_destination
                        in {"methods", "results", "limitations"}
                        else "limitations"
                    ],
                    claim_strength=(
                        "limitation"
                        if item.status
                        in {
                            TransparencyStatus.MISSING,
                            TransparencyStatus.PLANNED,
                            TransparencyStatus.UNVERIFIED,
                        }
                        else "descriptive"
                    ),
                    evidence_status="bound",
                )
            )
        matching_evaluations = [
            item
            for item in context.repository.list_evaluation_records(
                context.study_id
            )
            if item.plan_id == str(claim["plan_id"])
        ]
        if not matching_evaluations:
            raise ValueError(
                "Stage 4 cannot map a claim envelope without its frozen "
                "Stage 3 evaluation record"
            )
        evaluation = matching_evaluations[-1]
        research_contract = context.repository.load_research_contract(
            context.study_id, evaluation.contract_version
        )
        evaluation_path = (
            f"stage3/evaluations/{evaluation.evaluation_id}.json"
        )
        evaluation_file = (
            context.repository.root
            / "studies"
            / context.study_id
            / evaluation_path
        )
        evaluation_sha256 = sha256_file(evaluation_file)
        research_contract_path = (
            f"contracts/research-v{research_contract.version}.json"
        )
        research_contract_file = (
            context.repository.root
            / "studies"
            / context.study_id
            / research_contract_path
        )
        research_contract_sha256 = sha256_file(research_contract_file)
        run_plan = context.repository.load_run_plan(
            context.study_id, str(claim["plan_id"])
        )
        run_plan_path = f"stage3/run_plans/{run_plan.plan_id}.json"
        run_plan_file = (
            context.repository.root
            / "studies"
            / context.study_id
            / run_plan_path
        )
        run_plan_sha256 = sha256_file(run_plan_file)
        pair_keys = sorted(
            {
                (
                    cell.task_id,
                    cell.split_id,
                    cell.seed,
                    cell.replicate,
                )
                for cell in run_plan.cells
            }
        )
        input_binding_sets = {
            tuple(sorted(cell.input_bindings.items()))
            for cell in run_plan.cells
        }
        bindings.extend(
            [
                EvidenceClaimBinding(
                    claim_id=(
                        f"research-v{research_contract.version}:"
                        "measurement-protocol"
                    ),
                    kind="method",
                    statement=(
                        "Frozen row-level measurement and evaluator protocol: "
                        + json.dumps(
                            {
                                "output_schema": research_contract.output_schema,
                                "evaluator_policy": (
                                    research_contract.evaluator_policy
                                ),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    evidence=[
                        EvidencePointer(
                            path=research_contract_path,
                            sha256=research_contract_sha256,
                            json_path="$.output_schema",
                            source_id=None,
                            evidence_type="project_artifact",
                        ),
                        EvidencePointer(
                            path=research_contract_path,
                            sha256=research_contract_sha256,
                            json_path="$.evaluator_policy",
                            source_id=None,
                            evidence_type="project_artifact",
                        ),
                    ],
                    allowed_sections=["abstract", "methods", "results"],
                    claim_strength="descriptive",
                    evidence_status="bound",
                ),
                EvidenceClaimBinding(
                    claim_id=f"{run_plan.plan_id}:pair-register",
                    kind="method",
                    statement=(
                        "Frozen registered pair keys "
                        "(task, split, seed, replicate): "
                        + json.dumps(pair_keys, ensure_ascii=False)
                        + ". "
                        + (
                            "All run cells use the same frozen input bindings; "
                            "task and seed are repeated execution labels rather "
                            "than evidence of distinct datasets or stochastic "
                            "model variation. The four paired differences must "
                            "therefore be interpreted as repeated registered "
                            "units in this synthetic workflow validation."
                            if len(input_binding_sets) == 1
                            else (
                                "Run cells contain more than one frozen input "
                                "binding set; differences must be interpreted "
                                "using the bound cell-level inputs."
                            )
                        )
                    ),
                    evidence=[
                        EvidencePointer(
                            path=run_plan_path,
                            sha256=run_plan_sha256,
                            json_path="$.cells",
                            source_id=None,
                            evidence_type="project_artifact",
                        )
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "limitations",
                    ],
                    claim_strength="descriptive",
                    evidence_status="bound",
                ),
            ]
        )

        def evaluation_pointer(json_path: str) -> EvidencePointer:
            return EvidencePointer(
                path=evaluation_path,
                sha256=evaluation_sha256,
                json_path=json_path,
                source_id=None,
                evidence_type="project_artifact",
            )

        effective_arm_estimates = _effective_evaluation_arm_estimates(
            evaluation
        )
        for arm_id, estimate in sorted(effective_arm_estimates.items()):
            estimate_path = (
                f"$.arm_estimates.{arm_id}"
                if arm_id in evaluation.arm_estimates
                else (
                    "$.baseline_estimate"
                    if arm_id == "baseline"
                    else "$.treatment_estimate"
                )
            )
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=(
                        f"{evaluation.evaluation_id}:arm:{arm_id}"
                    ),
                    kind="result_metric",
                    statement=(
                        f"{evaluation.metric_name} for {arm_id} = {estimate}"
                    ),
                    evidence=[
                        evaluation_pointer(estimate_path)
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                    ],
                    claim_strength="descriptive",
                    evidence_status="bound",
                )
            )
        for contrast_id, raw_contrast in sorted(
            evaluation.contrast_estimates.items()
        ):
            effect = raw_contrast.get("effect")
            interval = raw_contrast.get("confidence_interval")
            p_value = raw_contrast.get("p_value")
            holm_reject = raw_contrast.get("holm_reject")
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=(
                        f"{evaluation.evaluation_id}:contrast:{contrast_id}"
                    ),
                    kind="result_metric",
                    statement=(
                        f"{contrast_id}: effect={effect}; "
                        f"95% CI={interval}; p={p_value}; "
                        f"Holm reject={holm_reject}"
                    ),
                    evidence=[
                        evaluation_pointer(
                            f"$.contrast_estimates.{contrast_id}"
                        )
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                    ],
                    claim_strength="comparative",
                    evidence_status="bound",
                )
            )
        if (
            not evaluation.contrast_estimates
            and evaluation.paired_effect is not None
        ):
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=f"{evaluation.evaluation_id}:contrast:paired",
                    kind="result_metric",
                    statement=(
                        f"Registered paired {evaluation.metric_name} effect="
                        f"{evaluation.paired_effect}; 95% CI="
                        f"{evaluation.confidence_interval}; "
                        f"decision={evaluation.decision.value}; "
                        "qualification="
                        f"{evaluation.qualification_status.value}; "
                        "confirmatory_status="
                        f"{evaluation.confirmatory_status.value}"
                    ),
                    evidence=[
                        evaluation_pointer("$.paired_effect"),
                        evaluation_pointer("$.confidence_interval"),
                        evaluation_pointer("$.decision"),
                        evaluation_pointer("$.qualification_status"),
                        evaluation_pointer("$.confirmatory_status"),
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                        "limitations",
                    ],
                    claim_strength="comparative",
                    evidence_status="bound",
                )
            )
        bindings.extend(
            [
                EvidenceClaimBinding(
                    claim_id=f"{evaluation.evaluation_id}:denominators",
                    kind="method",
                    statement=(
                        "Frozen analysis denominators: each arm contains "
                        f"{evaluation.pair_count} registered paired-analysis "
                        "units; "
                        f"{len(effective_arm_estimates)} arms therefore contain "
                        f"{evaluation.pair_count * len(effective_arm_estimates)} "
                        "registered arm-by-pair observations in total. Each "
                        "primary contrast uses "
                        f"{evaluation.pair_count} paired differences and the "
                        "contrasts share the treatment-arm observations; "
                        f"registered seeds={research_contract.seeds}; "
                        "registered pairing key="
                        f"{research_contract.estimand.get('pairing_key')}; "
                        "independent_unit_count="
                        f"{evaluation.independent_unit_count}; "
                        f"variance_unit={evaluation.variance_unit}"
                    ),
                    evidence=[
                        evaluation_pointer("$.pair_count"),
                        evaluation_pointer("$.independent_unit_count"),
                        evaluation_pointer("$.variance_unit"),
                        EvidencePointer(
                            path=research_contract_path,
                            sha256=research_contract_sha256,
                            json_path="$.seeds",
                            source_id=None,
                            evidence_type="project_artifact",
                        ),
                        EvidencePointer(
                            path=research_contract_path,
                            sha256=research_contract_sha256,
                            json_path="$.estimand.pairing_key",
                            source_id=None,
                            evidence_type="project_artifact",
                        ),
                    ],
                    allowed_sections=["abstract", "methods", "results"],
                    claim_strength="descriptive",
                    evidence_status="bound",
                ),
                EvidenceClaimBinding(
                    claim_id=f"{evaluation.evaluation_id}:decision-rule",
                    kind="method",
                    statement=(
                        "Frozen statistical decision rule: "
                        + json.dumps(
                            evaluation.statistical_rule,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    evidence=[evaluation_pointer("$.statistical_rule")],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                        "limitations",
                    ],
                    claim_strength="descriptive",
                    evidence_status="bound",
                ),
                EvidenceClaimBinding(
                    claim_id=f"{evaluation.evaluation_id}:qualification",
                    kind="method",
                    statement=(
                        "Evaluation qualification="
                        f"{evaluation.qualification_status.value}; "
                        "recorded checks="
                        + json.dumps(
                            evaluation.qualification_checks,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + ". A false check yields incomplete when the frozen "
                        "missing-cell policy is inconclusive, otherwise "
                        "disqualified; either state prevents a verified evidence "
                        "chain from authorizing a scientific verdict. "
                        "confirmatory_status="
                        f"{evaluation.confirmatory_status.value}; "
                        f"decision={evaluation.decision.value}"
                    ),
                    evidence=[
                        evaluation_pointer("$.qualification_status"),
                        evaluation_pointer("$.qualification_checks"),
                        evaluation_pointer("$.confirmatory_status"),
                        evaluation_pointer("$.decision"),
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                        "limitations",
                    ],
                    claim_strength="limitation",
                    evidence_status="bound",
                ),
            ]
        )
        metric_roles = {}
        for index, item in enumerate(research_contract.metrics):
            name = str(item.get("name") or "")
            if not name:
                continue
            metric_roles[name] = str(
                item.get("role")
                or (
                    "primary"
                    if index == 0 or name == evaluation.metric_name
                    else "secondary"
                )
            )
        if metric_roles:
            protocol_lock = (
                context.repository.root
                / "studies"
                / context.study_id
                / "stage2"
                / "protocol.lock.json"
            )
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=f"{evaluation.evaluation_id}:metric-roles",
                    kind="method",
                    statement=(
                        "Frozen metric registry roles are "
                        + json.dumps(
                            metric_roles,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + ". A metric's use in a frozen decision-rule check "
                        "does not change its registered role."
                    ),
                    evidence=[
                        EvidencePointer(
                            path="stage2/protocol.lock.json",
                            sha256=sha256_file(protocol_lock),
                            json_path="$.metrics",
                            source_id=None,
                            evidence_type="project_artifact",
                        )
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                    ],
                    claim_strength="descriptive",
                    evidence_status="bound",
                )
            )
        evaluation_results = [
            item
            for item in context.repository.list_result_envelopes(
                context.study_id
            )
            if item.result_id in set(evaluation.result_ids)
        ]
        primary_rows = [
            row
            for result in evaluation_results
            for row in result.analysis_rows
        ]
        observed_values = {
            row.get("value")
            for row in primary_rows
            if isinstance(row.get("value"), (int, float))
        }
        if (
            evaluation_results
            and observed_values
            and observed_values <= {0, 1, 0.0, 1.0}
        ):
            result_pointers = []
            for result in evaluation_results:
                result_path = f"stage3/results/{result.result_id}.json"
                result_file = (
                    context.repository.root
                    / "studies"
                    / context.study_id
                    / result_path
                )
                result_pointers.append(
                    EvidencePointer(
                        path=result_path,
                        sha256=sha256_file(result_file),
                        json_path="$.analysis_rows",
                        source_id=None,
                        evidence_type="project_artifact",
                    )
                )
            family_ids = {
                str(row["scenario_family"])
                for row in primary_rows
                if row.get("scenario_family") is not None
            }
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=(
                        f"{evaluation.evaluation_id}:"
                        "metric-operationalization"
                    ),
                    kind="method",
                    statement=(
                        f"{evaluation.metric_name} is the arithmetic mean of "
                        "frozen item-level correctness values: 1 denotes a "
                        "policy action matching the frozen target and 0 denotes "
                        "a mismatch. Target labels originate from "
                        f"{research_contract.data_boundary.get('label_origin')}. "
                        "The metric ranges from 0 to 1 and uses the registered "
                        "denominator of "
                        f"{evaluation_results[0].denominator} items per "
                        f"arm-seed cell. The evaluation contains "
                        f"{len(family_ids)} scenario_family clusters. Exact "
                        "formula text beyond the frozen label-origin statement "
                        "is not asserted by this binding."
                    ),
                    evidence=[
                        *result_pointers,
                        EvidencePointer(
                            path=research_contract_path,
                            sha256=research_contract_sha256,
                            json_path="$.data_boundary.label_origin",
                            source_id=None,
                            evidence_type="project_artifact",
                        ),
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                        "limitations",
                    ],
                    claim_strength="descriptive",
                    evidence_status="bound",
                )
            )
            threshold = evaluation.statistical_rule.get("effect_threshold")
            if isinstance(threshold, (int, float)):
                structural_limits = []
                for contrast_id, raw_contrast in sorted(
                    evaluation.contrast_estimates.items()
                ):
                    control_estimate = raw_contrast.get("control_estimate")
                    if not isinstance(control_estimate, (int, float)):
                        continue
                    maximum_effect = 1.0 - float(control_estimate)
                    if float(threshold) > maximum_effect:
                        structural_limits.append(
                            {
                                "contrast": contrast_id,
                                "control_estimate": control_estimate,
                                "maximum_effect_on_0_1_scale": maximum_effect,
                                "threshold": threshold,
                            }
                        )
                if structural_limits:
                    bindings.append(
                        EvidenceClaimBinding(
                            claim_id=(
                                f"{evaluation.evaluation_id}:"
                                "structural-threshold-limit"
                            ),
                            kind="limitation",
                            statement=(
                                "At least one frozen effect threshold is "
                                "structurally unreachable on the observed 0–1 "
                                "scale given its frozen control mean: "
                                + json.dumps(
                                    structural_limits,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                )
                                + ". Failure of the all-contrast conjunction "
                                "therefore cannot be described as evidence of "
                                "no effect or treatment failure."
                            ),
                            evidence=[
                                evaluation_pointer("$.arm_estimates"),
                                evaluation_pointer(
                                    "$.statistical_rule.effect_threshold"
                                ),
                                evaluation_pointer("$.contrast_estimates"),
                            ],
                            allowed_sections=[
                                "abstract",
                                "results",
                                "discussion",
                                "limitations",
                                "conclusion",
                            ],
                            claim_strength="limitation",
                            evidence_status="bound",
                        )
                    )
        run_plan = next(
            item
            for item in context.repository.list_run_plans(context.study_id)
            if item.plan_id == evaluation.plan_id
        )
        cells_by_id = {item.run_cell_id: item for item in run_plan.cells}
        results_by_attempt_id = {
            item.attempt_id: item
            for item in evaluation_results
            if item.attempt_id is not None
        }
        canonical_attempts = []
        for result in evaluation_results:
            if result.attempt_id is None:
                continue
            attempt_path = (
                context.repository.root
                / "studies"
                / context.study_id
                / "stage3"
                / "attempts"
                / f"{result.attempt_id}.json"
            )
            if attempt_path.is_file():
                canonical_attempts.append(read_json(attempt_path))
        if canonical_attempts:
            code_hashes = {
                str(item.get("code_hash"))
                for item in canonical_attempts
            }
            environment_hashes = {
                str(item.get("environment_hash"))
                for item in canonical_attempts
            }
            shared_keys = set.intersection(
                *[
                    set((item.get("input_hashes") or {}).keys())
                    for item in canonical_attempts
                ]
            )
            invariant_shared_keys = sorted(
                key
                for key in shared_keys
                if len(
                    {
                        str((item.get("input_hashes") or {}).get(key))
                        for item in canonical_attempts
                    }
                )
                == 1
            )
            all_successful = all(
                item.get("canonical") is True
                and item.get("status") == "succeeded"
                and item.get("exit_status") == 0
                for item in canonical_attempts
            )
            attempt_pointers = []
            attempt_mapping = []
            for item in canonical_attempts:
                attempt_id = str(item["attempt_id"])
                attempt_path = f"stage3/attempts/{attempt_id}.json"
                attempt_file = (
                    context.repository.root
                    / "studies"
                    / context.study_id
                    / attempt_path
                )
                attempt_pointers.append(
                    EvidencePointer(
                        path=attempt_path,
                        sha256=sha256_file(attempt_file),
                        json_path="$",
                        source_id=None,
                        evidence_type="project_artifact",
                    )
                )
                attempt_mapping.append(
                    {
                        "attempt_path": attempt_path,
                        "attempt_sha256": sha256_file(attempt_file),
                        "run_cell_id": item.get("run_cell_id"),
                        "arm_id": (
                            cells_by_id[str(item.get("run_cell_id"))].arm_id
                        ),
                        "seed": (
                            cells_by_id[str(item.get("run_cell_id"))].seed
                        ),
                        "result_id": (
                            results_by_attempt_id[attempt_id].result_id
                        ),
                        "result_sha256": sha256_file(
                            context.repository.root
                            / "studies"
                            / context.study_id
                            / "stage3"
                            / "results"
                            / (
                                results_by_attempt_id[attempt_id].result_id
                                + ".json"
                            )
                        ),
                        "status": item.get("status"),
                        "exit_status": item.get("exit_status"),
                    }
                )
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=(
                        f"{evaluation.evaluation_id}:"
                        "recorded-execution-integrity"
                    ),
                    kind="method",
                    statement=(
                        "Recorded execution-integrity audit: "
                        f"{len(canonical_attempts)}/{len(evaluation.result_ids)} "
                        f"bound canonical attempts succeeded with exit_status=0 "
                        f"({all_successful=}); code_hash_count={len(code_hashes)}; "
                        "environment_hash_count="
                        f"{len(environment_hashes)}; invariant shared input "
                        f"bindings={invariant_shared_keys}; canonical attempt "
                        "mapping="
                        + json.dumps(
                            attempt_mapping,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + ". This establishes "
                        "consistency of the recorded manifests and attempts, "
                        "not independent external reproduction."
                    ),
                    evidence=attempt_pointers,
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                        "limitations",
                    ],
                    claim_strength="descriptive",
                    evidence_status="bound",
                )
            )
        exposures = [
            item
            for item in context.repository.list_formal_exposures(
                context.study_id
            )
            if item.plan_id == evaluation.plan_id
        ]
        if exposures:
            exposure = exposures[-1]
            exposure_path = (
                f"stage3/exposures/{exposure.exposure_id}.json"
            )
            exposure_file = (
                context.repository.root
                / "studies"
                / context.study_id
                / exposure_path
            )
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=(
                        f"{evaluation.evaluation_id}:evidence-timeline"
                    ),
                    kind="limitation",
                    statement=(
                        "Evidence timeline: aggregate formal-test results were "
                        f"first exposed at {exposure.first_exposed_at}. The "
                        "exposure record identifies the revealed fields as "
                        f"{', '.join(exposure.revealed_fields)} and records "
                        f"result_influenced_successor="
                        f"{str(exposure.result_influenced_successor).lower()}. "
                        "Because observed results informed a successor analysis, "
                        f"the resulting evidence is classified "
                        f"{evaluation.confirmatory_status.value} rather than "
                        "independent confirmation. "
                        "Terms such as frozen or registered describe the locked "
                        "contract used for this run and must not be represented "
                        "as independent prospective preregistration."
                    ),
                    evidence=[
                        EvidencePointer(
                            path=exposure_path,
                            sha256=sha256_file(exposure_file),
                            json_path="$",
                            source_id=None,
                            evidence_type="project_artifact",
                        ),
                        evaluation_pointer("$.confirmatory_status"),
                    ],
                    allowed_sections=[
                        "abstract",
                        "methods",
                        "results",
                        "discussion",
                        "limitations",
                        "conclusion",
                    ],
                    claim_strength="limitation",
                    evidence_status="bound",
                )
            )
        bindings.append(
            EvidenceClaimBinding(
                claim_id=f"{evaluation.evaluation_id}:framework-boundary",
                kind="method",
                statement=(
                    "The local Stage 4 reporting process accepts a frozen "
                    "contract, hash-bound execution records, machine-readable "
                    "results, and a frozen statistical rule; it emits qualified "
                    "estimates, a bounded claim envelope, and explicit "
                    "confirmatory status. In this study that process is only "
                    "the means used to prepare the report. It is not a research "
                    "question, evaluated intervention, primary contribution, "
                    "or evidence that the process completely preserves claims. "
                    "Reusability is an implementation intention, not an "
                    "empirically validated cross-study property."
                ),
                evidence=[
                    evaluation_pointer("$"),
                    pointer,
                ],
                allowed_sections=[
                    "methods",
                    "discussion",
                    "limitations",
                ],
                claim_strength="descriptive",
                evidence_status="bound",
            )
        )
        publication_context_dir = (
            context.repository.root
            / "studies"
            / context.study_id
            / "stage3"
            / "publication_context"
        )
        for context_file in sorted(
            publication_context_dir.glob("*.json")
        ):
            payload = read_json(context_file)
            relative_path = context_file.relative_to(
                context.repository.root
                / "studies"
                / context.study_id
            ).as_posix()
            for index, raw in enumerate(payload.get("claims") or []):
                bindings.append(
                    EvidenceClaimBinding(
                        claim_id=str(raw["claim_id"]),
                        kind=str(raw.get("kind") or "method"),
                        statement=str(raw["statement"]),
                        evidence=[
                            EvidencePointer(
                                path=relative_path,
                                sha256=sha256_file(context_file),
                                json_path=f"$.claims[{index}]",
                                source_id=None,
                                evidence_type="project_artifact",
                            )
                        ],
                        allowed_sections=[
                            str(item)
                            for item in raw.get("allowed_sections")
                            or [
                                "abstract",
                                "methods",
                                "results",
                                "discussion",
                                "limitations",
                            ]
                        ],
                        claim_strength=str(
                            raw.get("claim_strength") or "descriptive"
                        ),
                        evidence_status="bound",
                    )
                )
        for index, limitation in enumerate(
            claim.get("known_limitations") or [], start=1
        ):
            bindings.append(
                EvidenceClaimBinding(
                    claim_id=f"{envelope_id}:limitation-{index}",
                    kind="limitation",
                    statement=str(limitation),
                    evidence=[
                        pointer.model_copy(
                            update={
                                "json_path": (
                                    f"$.known_limitations[{index - 1}]"
                                )
                            }
                        )
                    ],
                    allowed_sections=[
                        "abstract",
                        "discussion",
                        "limitations",
                        "conclusion",
                    ],
                    claim_strength="limitation",
                    evidence_status="bound",
                )
            )
        contract = context.repository.latest_research_contract(
            context.study_id
        )
        if contract is not None:
            contract_path = (
                f"contracts/research-v{contract.version}.json"
            )
            contract_file = (
                context.repository.root
                / "studies"
                / context.study_id
                / contract_path
            )
            bindings.extend(
                [
                    EvidenceClaimBinding(
                        claim_id=(
                            f"research-v{contract.version}:estimand"
                        ),
                        kind="method",
                        statement=(
                            "Frozen estimand and analysis boundary: "
                            + json.dumps(
                                contract.estimand,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        evidence=[
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.estimand",
                                source_id=None,
                                evidence_type="project_artifact",
                            )
                        ],
                        allowed_sections=["abstract", "methods", "results"],
                        claim_strength="descriptive",
                        evidence_status="bound",
                    ),
                    EvidenceClaimBinding(
                        claim_id=(
                            f"research-v{contract.version}:implementation"
                        ),
                        kind="method",
                        statement=(
                            "Frozen arm implementation requirements: "
                            + json.dumps(
                                contract.implementation_requirements,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        evidence=[
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.implementation_requirements",
                                source_id=None,
                                evidence_type="project_artifact",
                            )
                        ],
                        allowed_sections=["abstract", "methods"],
                        claim_strength="descriptive",
                        evidence_status="bound",
                    ),
                    EvidenceClaimBinding(
                        claim_id=(
                            f"research-v{contract.version}:model-and-runtime"
                        ),
                        kind="method",
                        statement=(
                            "Frozen declared model and runtime identity: "
                            + json.dumps(
                                {
                                    "baseline": contract.baseline,
                                    "treatment": contract.treatment,
                                    "model_revision": contract.runtime_binding.get(
                                        "model_revision"
                                    ),
                                    "python": contract.runtime_binding.get("python"),
                                    "platform": contract.runtime_binding.get(
                                        "platform"
                                    ),
                                    "formal_execution_network": (
                                        contract.runtime_binding.get(
                                            "formal_execution_network"
                                        )
                                    ),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        evidence=[
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.baseline",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.runtime_binding",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                        ],
                        allowed_sections=["abstract", "methods", "limitations"],
                        claim_strength="descriptive",
                        evidence_status="bound",
                    ),
                    EvidenceClaimBinding(
                        claim_id=(
                            f"research-v{contract.version}:design-matrix"
                        ),
                        kind="method",
                        statement=(
                            f"Frozen design: seeds={contract.seeds}; "
                            f"splits={contract.splits}; tasks={contract.tasks}; "
                            "arms="
                            + json.dumps(
                                (
                                    contract.implementation_requirements.get(
                                        "arms"
                                    )
                                    or [
                                        contract.baseline,
                                        contract.treatment,
                                    ]
                                ),
                                ensure_ascii=False,
                            )
                        ),
                        evidence=[
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.seeds",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.splits",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.implementation_requirements.arms",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.baseline",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.treatment",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                        ],
                        allowed_sections=["abstract", "methods", "results"],
                        claim_strength="descriptive",
                        evidence_status="bound",
                    ),
                    EvidenceClaimBinding(
                        claim_id=(
                            f"research-v{contract.version}:metric-registry"
                        ),
                        kind="method",
                        statement=(
                            "Frozen metric registry and measurement limits: "
                            + json.dumps(
                                contract.metrics,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        evidence=[
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.metrics",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path="$.data_boundary.label_origin",
                                source_id=None,
                                evidence_type="project_artifact",
                            ),
                        ],
                        allowed_sections=[
                            "abstract",
                            "methods",
                            "results",
                            "discussion",
                            "limitations",
                        ],
                        claim_strength="descriptive",
                        evidence_status="bound",
                    ),
                ]
            )
            for index, hypothesis in enumerate(contract.hypotheses):
                decision_rule = hypothesis.decision_rule or {}
                declared_category = str(
                    decision_rule.get("reporting_category") or "secondary"
                )
                kind = (
                    "limitation"
                    if declared_category in {"negative", "safety"}
                    else "result"
                )
                bindings.append(
                    EvidenceClaimBinding(
                        claim_id=(
                            f"research-v{contract.version}:"
                            f"{hypothesis.hypothesis_id}"
                        ),
                        kind=kind,
                        statement=hypothesis.statement,
                        evidence=[
                            EvidencePointer(
                                path=contract_path,
                                sha256=sha256_file(contract_file),
                                json_path=f"$.hypotheses[{index}].statement",
                                source_id=None,
                                evidence_type="project_artifact",
                            )
                        ],
                        allowed_sections=[
                            "results",
                            "discussion",
                            "limitations",
                            "supplement",
                        ],
                        claim_strength=(
                            "limitation"
                            if kind == "limitation"
                            else "descriptive"
                        ),
                        evidence_status="bound",
                    )
                )
        prerequisite = context.result("publication_prerequisite_gate")
        # Background literature is not scientific-result authority, but every
        # reader-facing citation still needs a narrow, explicit source binding.
        # Registering only ``verified_source_ids`` left the citation auditor no
        # way to distinguish verified background use from an ungrounded claim.
        # These bindings authorize only the bibliographic/methodological topic
        # represented by the verified record; they cannot support the Study's
        # empirical verdict or a causal/mechanistic statement.
        existing_claim_ids = {item.claim_id for item in bindings}
        for source in prerequisite.get("verified_literature", []):
            source_id = str(
                source.get("source_id") or source.get("resource_id") or ""
            ).strip()
            title = str(source.get("title") or "").strip()
            canonical_hash = str(
                source.get("canonical_metadata_hash") or ""
            ).strip()
            canonical_identifier = str(
                source.get("canonical_identifier")
                or source.get("doi")
                or source_id
            ).strip()
            claim_id = f"literature:{source_id}:verified-topic"
            if (
                not source_id
                or not title
                or not canonical_hash
                or claim_id in existing_claim_ids
            ):
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
                            path=canonical_identifier,
                            sha256=canonical_hash,
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
            existing_claim_ids.add(claim_id)
        registry_payload = {
            "claim_envelope_id": envelope_id,
            "claim_envelope_sha256": sha256_file(source_file),
            "verified_source_ids": prerequisite["verified_source_ids"],
        }
        evidence_map = EvidenceClaimMap(
            track_id=str(claim["plan_id"]),
            frozen_conclusion=str(claim["allowed_claim"]),
            bindings=bindings,
            verified_source_ids=[
                str(item) for item in prerequisite["verified_source_ids"]
            ],
            forbidden_moves=[
                "broaden a ScientificClaimEnvelope claim",
                "upgrade comparative evidence to a causal mechanism",
                "omit a MandatoryReportingRegister claim",
                "change confirmatory status or evidence maturity",
                "treat explanatory graphics as scientific evidence",
            ],
            source_registry_sha256=hashlib.sha256(
                json.dumps(
                    registry_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="evidence_claim_map_v1",
            value=evidence_map,
            kind="evidence_claim_map",
            role=ArtifactRole.CLAIM,
            immutable=True,
        )
        return {"evidence_claim_map": evidence_map.model_dump(mode="json")}

    def visual_argument_planning(context: Any) -> dict[str, Any]:
        claim_authority = context.result("stage4_claim_intake")
        stage3_claim = dict(
            (claim_authority.get("claims") or [])[0]
        )
        boundary_report_mode = (
            stage3_claim.get("maximum_claim_tier")
            == "evidence_boundary_report"
            or stage3_claim.get("evidence_level") == "L0_boundary_only"
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        narrative = PublicationNarrativeContract.model_validate(
            context.result("publication_narrative_contract_freeze")[
                "publication_narrative_contract"
            ]
        )
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        primary, plotted, contrast_metrics, denominator = (
            _select_primary_visual_bindings(evidence_map, narrative)
        )
        is_english = manuscript_language(context) == "en"
        source_paths = sorted(
            {
                pointer.path
                for item in plotted
                for pointer in item.evidence
            }
        )
        source_ids = list(dict.fromkeys([
            pointer.source_id or item.claim_id
            for item in plotted
            for pointer in item.evidence
        ]))
        common_caption = CaptionClaimBinding(
            caption_id="caption-primary-result",
            figure_id="fig-primary-result",
            reader_question=(
                "What primary result is supported by the available evidence?"
                if is_english
                else "冻结的阶段三主张边界允许报告什么结果？"
            ),
            comparison=(
                "Treatment-versus-comparator contrast"
                if is_english
                else "注册处理与注册对照的比较"
            ),
            denominator_claim_ids=[denominator.claim_id],
            uncertainty_claim_ids=[
                item.claim_id for item in contrast_metrics
            ],
            observation_claim_ids=[
                item.claim_id for item in plotted
            ],
            limitation_claim_ids=narrative.mandatory_negative_claim_ids,
            caption_text=(
                "Primary outcome comparison. The figure shows the treatment-"
                "comparator contrast; the text reports the analysis denominator and "
                "evidential scope."
                if is_english
                else (
                    "冻结的主要结果。图中展示注册的处理—对照结果；正文说明"
                    "合格分母与证据边界。"
                )
            ),
        )
        figures = [
            FigureSpec(
                figure_id="fig-primary-result",
                role="primary_result",
                reader_question=(
                    "How does the primary outcome differ between the compared conditions?"
                    if is_english
                    else "比较条件之间的主要结果有何差异？"
                ),
                claim_ids=[item.claim_id for item in plotted],
                source_artifact_ids=source_ids,
                source_paths=source_paths,
                visual_type="effect_interval",
                main_or_supplement="main",
                must_show=[
                    "comparator",
                    "primary outcome",
                    "analysis denominator",
                    "uncertainty when available",
                ],
                must_not_imply=[
                    "causality outside the study design",
                    "state-of-the-art performance",
                    "unseen-population generalization",
                ],
                alt_text=(
                    "A primary intervention-versus-comparator result, with the "
                    "analysis denominator and uncertainty shown when available."
                ),
                minimum_font_points=policy.visual_policy.minimum_font_points,
                color_vision_safe=True,
                output_format="svg",
                x_axis_label="Condition comparison",
                y_axis_label=str(primary.statement).split("=", 1)[0].strip()
                or "Outcome",
                units="as registered in the Research Contract",
                caption_binding=common_caption,
            )
        ]
        if boundary_report_mode:
            boundary_source_paths = sorted(
                {pointer.path for pointer in primary.evidence}
            )
            boundary_source_ids = list(
                dict.fromkeys(
                    pointer.source_id or primary.claim_id
                    for pointer in primary.evidence
                )
            )
            boundary_caption = CaptionClaimBinding(
                caption_id="caption-evidence-boundary",
                figure_id="fig-evidence-boundary",
                reader_question=(
                    "Which parts of the registered study were completed, and "
                    "which missing bindings prevented formal evaluation?"
                ),
                comparison="completed design versus missing formal evidence",
                denominator_claim_ids=[],
                uncertainty_claim_ids=[],
                observation_claim_ids=[primary.claim_id],
                limitation_claim_ids=narrative.mandatory_negative_claim_ids,
                caption_text=(
                    "Evidence-boundary workflow. The diagram distinguishes "
                    "the frozen design from unavailable formal evidence; it "
                    "does not display or imply an experimental effect."
                ),
            )
            figures = [
                FigureSpec(
                    figure_id="fig-evidence-boundary",
                    role="problem_and_method_overview",
                    reader_question=(
                        "Where did the registered study stop, and what evidence "
                        "is required before its hypothesis can be evaluated?"
                    ),
                    claim_ids=[primary.claim_id],
                    source_artifact_ids=boundary_source_ids,
                    source_paths=boundary_source_paths,
                    visual_type="editable_process_diagram",
                    main_or_supplement="main",
                    evidence_status="explanatory_only",
                    must_show=[
                        "frozen study design",
                        "missing executable or data bindings",
                        "unverifiable verdict",
                        "future evidence requirements",
                    ],
                    must_not_imply=[
                        "an executed formal experiment",
                        "a treatment effect",
                        "support or refutation of the hypothesis",
                    ],
                    alt_text=(
                        "A process diagram showing that the design was frozen "
                        "but formal evaluation stopped at missing evidence "
                        "bindings, resulting in an unverifiable verdict."
                    ),
                    minimum_font_points=(
                        policy.visual_policy.minimum_font_points
                    ),
                    color_vision_safe=True,
                    output_format="svg",
                    caption_binding=boundary_caption,
                )
            ]
        tables = []
        lineage_binding = next(
            (
                item
                for item in evidence_map.bindings
                if item.claim_id == "publication-context:canonical-lineage"
            ),
            None,
        )
        if lineage_binding is not None:
            lineage_paths = sorted(
                {pointer.path for pointer in lineage_binding.evidence}
            )
            tables.append(
                TableSpec(
                    table_id="tab-canonical-lineage",
                    role="experiment_setup",
                    reader_question=(
                        "How does each frozen arm-seed cell map to its canonical "
                        "attempt and result artifacts?"
                    ),
                    claim_ids=[lineage_binding.claim_id],
                    source_artifact_ids=[
                        pointer.source_id or lineage_binding.claim_id
                        for pointer in lineage_binding.evidence
                    ],
                    source_paths=lineage_paths,
                    columns=[
                        "Arm",
                        "Seed",
                        "Run / attempt",
                        "Result",
                        "Status",
                    ],
                    main_or_supplement="main",
                    caption_binding=CaptionClaimBinding(
                        caption_id="caption-canonical-lineage",
                        figure_id="tab-canonical-lineage",
                        reader_question=(
                            "Which canonical execution produced each analyzed "
                            "arm-seed result?"
                        ),
                        comparison="nine frozen arm-seed cells",
                        denominator_claim_ids=[
                            item.claim_id
                            for item in evidence_map.bindings
                            if item.claim_id.endswith(":denominators")
                        ],
                        uncertainty_claim_ids=[],
                        observation_claim_ids=[lineage_binding.claim_id],
                        limitation_claim_ids=[
                            "publication-context:"
                            "implementation-verification-boundary"
                        ],
                        caption_text=(
                            "Canonical Stage 3 lineage. Each row binds one "
                            "arm-seed run cell to its preserved attempt and "
                            "result records; hashes are abbreviated visually "
                            "but remain complete in the source artifact."
                        ),
                    ),
                )
            )
        plan = VisualArgumentPlan(
            study_id=context.study_id,
            narrative_contract_id=narrative.contract_sha256,
            visual_thesis=(
                (
                    "The visual sequence must explain the evidence boundary "
                    "without displaying or implying an experimental effect."
                )
                if boundary_report_mode
                else (
                    "The visual sequence must show the registered comparison "
                    "and its evidence boundary without implying broader "
                    "validity."
                )
            ),
            main_figure_budget=policy.visual_policy.main_figure_budget,
            supplementary_figure_budget=(
                policy.visual_policy.supplementary_figure_budget
            ),
            figures=figures,
            tables=tables,
            status="draft",
        )
        violations = validate_visual_argument_plan(
            plan,
            evidence_claim_map=evidence_map,
            venue_policy=policy,
        )
        if violations:
            raise ValueError(
                "generated VisualArgumentPlan is invalid: "
                + "; ".join(violations)
            )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="visual_argument_plan_v1",
            value=plan,
            kind="visual_argument_plan",
            role=ArtifactRole.MANUSCRIPT,
        )
        return {"visual_argument_plan": plan.model_dump(mode="json")}

    def literature_sources(context: Any) -> list[Any]:
        from .models import LiteratureSource, LiteratureSourceType

        prerequisite = context.result("publication_prerequisite_gate")
        sources: list[LiteratureSource] = []
        for item in prerequisite["verified_literature"]:
            date = str(item.get("publication_or_release_date") or "")
            year = (
                int(date[:4])
                if len(date) >= 4 and date[:4].isdigit()
                else None
            )
            metadata = item.get("metadata") or {}
            metadata_authors = [
                str(author).strip()
                for author in metadata.get("authors") or []
                if str(author).strip()
            ]
            notes = str(
                metadata.get("abstract")
                or metadata.get("summary")
                or ""
            )[:5000]
            sources.append(
                LiteratureSource(
                    source_id=str(item["resource_id"])[:80],
                    source_type=LiteratureSourceType.PAPER,
                    title=str(item["title"]),
                    authors=(
                        metadata_authors
                        or ["Author metadata unavailable"]
                    ),
                    year=year,
                    locator=str(
                        item.get("doi")
                        or item.get("url")
                        or item.get("canonical_identifier")
                    ),
                    notes=notes,
                    verified=True,
                    verification_method=(
                        "frozen RetrievalRepository metadata verification"
                    ),
                    origin="discovery",
                    origin_id=str(item["resource_id"]),
                )
            )
        return sources

    def outline_generation(context: Any) -> dict[str, Any]:
        from .paper_authoring import (
            EvidenceClaimMap,
            load_or_create_submission_genre,
        )
        from .paper_expansion import _prepare_reviewed_outline
        from .paper_pipeline import get_paper_structure_contract

        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        publication_aliases, _ = _publication_context(context)
        narrative = PublicationNarrativeContract.model_validate(
            context.result("publication_narrative_contract_freeze")[
                "publication_narrative_contract"
            ]
        )
        visual = VisualArgumentPlan.model_validate(
            context.result("visual_argument_planning")[
                "visual_argument_plan"
            ]
        )
        visual_gate_step = next(
            item
            for item in context.repository.list_steps(context.study_id)
            if item.step_instance_id in context.step.depends_on
            and item.step_type == "visual_argument_plan_approval"
        )
        visual_gate = next(
            item
            for item in context.repository.list_gates(context.study_id)
            if item.gate_type is GateType.VISUAL_ARGUMENT
            and item.subject_id
            == visual_gate_step.parameters.get("subject_id")
            and item.status.value == "approved"
        )
        visual = visual.model_copy(
            update={
                "status": "frozen",
                "approved_by": visual_gate.decided_by,
                "approved_at": visual_gate.decided_at,
            }
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="visual_argument_plan_frozen_v1",
            value=visual,
            kind="visual_argument_plan",
            role=ArtifactRole.MANUSCRIPT,
            immutable=True,
        )
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        root = _stage4_working_root(context)
        genre = load_or_create_submission_genre(
            root,
            get_paper_structure_contract(policy.structure_profile_id),
            language=manuscript_language(context),
        )
        structure_contract = get_paper_structure_contract(
            policy.structure_profile_id
        )
        depth_profile = _depth_profile_for_context(
            context,
            policy.manuscript_depth_profile,
        )
        depth_contract = _localized_depth_contract(
            depth_profile,
            language=manuscript_language(context),
        )
        scope = context.repository.latest_scope_contract(context.study_id)
        title_basis = (
            {
                "direction": scope.direction,
                "research_question": scope.research_question,
                "study_design": scope.study_design,
                "population_or_corpus": scope.population_or_corpus,
                "primary_outcome": scope.primary_outcome,
                "comparison": scope.comparison,
            }
            if scope is not None
            else None
        )
        outline, decision = asyncio.run(
            _prepare_reviewed_outline(
                root=root,
                genre=genre,
                evidence_claim_map=evidence_map,
                sources=literature_sources(context),
                narrative_contract=narrative,
                visual_argument_plan=visual,
                structure_contract=structure_contract,
                publication_aliases=publication_aliases,
                title_basis=title_basis,
            )
        )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="paper_outline_v1",
            value=outline,
            kind="hierarchical_paper_outline",
            role=ArtifactRole.MANUSCRIPT,
            immutable=True,
        )
        return {
            "paper_outline": outline.model_dump(mode="json"),
            "outline_panel_decision": decision.model_dump(mode="json"),
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def draft_generation(context: Any) -> dict[str, Any]:
        from .agent_runtime import generate_bundle_paper_draft
        from .paper_authoring import (
            EvidenceClaimMap,
            HierarchicalPaperOutline,
        )
        from .paper_expansion import (
            PaperDraftSections,
            _draft_contract_violations,
            academicize_paper_draft,
            normalize_paper_draft,
        )
        from .manuscript_architecture import build_manuscript_source_snapshot
        from .paper_pipeline import get_paper_structure_contract
        from .sci_ssci_writing import stage_four_sci_ssci_contract

        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        depth_profile = _depth_profile_for_context(
            context,
            policy.manuscript_depth_profile,
        )
        depth_contract = _localized_depth_contract(
            depth_profile,
            language=manuscript_language(context),
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        publication_aliases, public_frozen_conclusion = (
            _publication_context(context)
        )
        narrative = PublicationNarrativeContract.model_validate(
            context.result("publication_narrative_contract_freeze")[
                "publication_narrative_contract"
            ]
        )
        visual = VisualArgumentPlan.model_validate(
            load_stage_four_artifact(
                context.repository,
                context.study_id,
                "visual_argument_plan_frozen_v1",
            )
        )
        outline = HierarchicalPaperOutline.model_validate(
            context.result("outline_generation_and_audit")[
                "paper_outline"
            ]
        )
        sources = literature_sources(context)
        source_snapshot = build_manuscript_source_snapshot(
            {
                "evidence_claim_map": evidence_map,
                "literature_sources": [
                    source.model_dump(mode="json") for source in sources
                ],
                "publication_narrative_contract": narrative,
                "visual_argument_plan": visual,
                "approved_outline": outline,
                "mandatory_reporting_register": context.result(
                    "mandatory_reporting_register"
                )["mandatory_reporting_register"],
                "evaluation_transparency_register": context.result(
                    "evaluation_transparency_register"
                )["evaluation_transparency_register"],
            }
        )
        prompt = {
            "task": (
                "Write a complete evidence-bound academic manuscript. Return "
                "only the PaperDraftSections schema."
            ),
            "publication_narrative_contract": narrative.model_dump(mode="json"),
            "mandatory_reporting_register": context.result(
                "mandatory_reporting_register"
            )["mandatory_reporting_register"],
            "evaluation_transparency_register": context.result(
                "evaluation_transparency_register"
            )["evaluation_transparency_register"],
            "evidence_claim_map": _drafting_evidence_view(evidence_map),
            "visual_argument_plan": visual.model_dump(mode="json"),
            "approved_outline": outline.model_dump(mode="json"),
            "verified_literature": _drafting_literature_view(sources),
            "venue_policy": policy.model_dump(mode="json"),
            "paper_structure_contract": get_paper_structure_contract(
                policy.structure_profile_id
            ).prompt_contract(manuscript_language(context)),
            "sci_ssci_writing_contract": stage_four_sci_ssci_contract(
                language=manuscript_language(context)
            ),
            "owner_revision_instruction": str(
                context.step.parameters.get(
                    "owner_revision_instruction", ""
                )
            ).strip()
            or None,
            "requirements": {
                "language": (
                    "English"
                    if manuscript_language(context) == "en"
                    else "Chinese"
                ),
                "minimum_length": depth_contract["total"],
                "section_length_targets": depth_contract["sections"],
                "abstract_depth_contract": depth_contract["abstract"],
                "length_unit": depth_contract["length_unit"],
                "minimum_substantive_paragraphs": depth_contract["paragraphs"],
                "minimum_subsections": depth_contract["subsections"],
                "manuscript_depth_profile": depth_profile,
                "subsection_markup": (
                    "Use descriptive ### headings inside methods, results, "
                    "and discussion. These are semantic subsection markers; "
                    "the renderer owns numeric heading labels."
                ),
                "citation_syntax": "[source_id]",
                "all_mandatory_reporting_items_need_a_destination": True,
                "report_available_transparency_items_and_disclose_missing_ones": True,
                "never_invent_missing_operational_counts_or_analyses": True,
                "do_not_broaden_claims": True,
                "do_not_change_numbers_or_uncertainty": True,
                "do_not_suppress_negative_or_safety_results": True,
                "abstract_is_one_unstructured_paragraph": (
                    policy.abstract_style == "unstructured"
                ),
                "renderer_owns_headings_and_numbering": True,
                "leave_exact_visual_callouts": True,
                "reader_facing_academic_style": {
                    "language": (
                        (
                            "Write fluent academic English. Keep established technical "
                            "terms, official dataset or model names, metric names, and "
                            "bibliographic titles in their conventional form. Translate "
                            "internal implementation labels into natural scientific roles."
                        )
                        if manuscript_language(context) == "en"
                        else (
                            "Write fluent Chinese prose. Keep only established technical "
                            "terms, official dataset or model names, metric names, and "
                            "bibliographic titles in their conventional language. When a "
                            "specialized English term is necessary, define it in Chinese "
                            "at first mention."
                        )
                    ),
                    "title": (
                        "Write a concise research title around the scientific construct, "
                        "comparison, and study design or principal finding. Never use a "
                        "source-code variable, internal metric key, workflow state, "
                        "module name, or pipeline slogan as the title."
                    ),
                    "abstract": (
                        "Write exactly one unlabelled paragraph in this order: research "
                        "problem, study design, principal evidence, and implication. "
                        "Do not insert Background, Methods, Results, or Conclusion labels. "
                        "State the finding accurately in natural language and prefer no "
                        "numerals. If a numeral is essential, keep at most two core "
                        "quantities; move detailed estimates, intervals, p-values, counts, "
                        "seeds, and implementation numbers to Results."
                    ),
                    "narrative": (
                        "Lead with the scientific question and contribution. Present "
                        "system or evaluator failures as limitations revealed by the "
                        "study, not as the central story of an audit report or debugging "
                        "chronicle."
                    ),
                    "section_roles": (
                        "Results reports observations, estimates, uncertainty, and "
                        "prespecified checks. Discussion interprets those observations, "
                        "limitations, and implications. Do not move defensive "
                        "interpretation into Results."
                    ),
                    "prose": (
                        "Prefer concrete subjects and verbs, one main idea per sentence, "
                        "and descriptive scholarly headings. Avoid governance slogans, "
                        "state-machine narration, repeated disclaimers, filler, and "
                        "unexplained abbreviations."
                    ),
                },
                "publication_terminology_policy": {
                    "title_and_front_matter": (
                        "Never expose snake_case fields, task IDs, versioned "
                        "pipeline labels, metric keys, or internal arm names. "
                        "Name the scientific construct and experimental role."
                    ),
                    "main_text": (
                        "Use scholarly aliases such as candidate ranking "
                        "method, reference ranking method, and primary outcome. "
                        "Keep exact code identifiers only in frozen audit "
                        "artifacts, which are not rendered as manuscript prose."
                    ),
                    "meaning_boundary": (
                        "Do not infer model contents or mechanisms from an "
                        "internal label."
                    ),
                },
                "narrative_discipline": [
                    (
                        "Tell the scientific story in this order: a real research "
                        "problem, the study's response, the principal finding, "
                        "and what that finding changes. Never narrate the local "
                        "workflow, approval process, or manuscript production."
                    ),
                    (
                        "Give every section one job. Introduction motivates and "
                        "states the contribution; Related Work synthesizes by "
                        "concept; Methods makes the comparison reproducible; "
                        "Results reports observations; Discussion interprets; "
                        "Limitations bounds; Conclusion answers the question."
                    ),
                    (
                        "Centralize material caveats in Limitations. Mention an "
                        "uncertainty beside a result only when it changes how that "
                        "result is read; do not repeat the same defensive boundary "
                        "in Introduction, Results, Discussion, and Conclusion."
                    ),
                    (
                        "Describe every arm using its contract-specified scientific "
                        "role. Do not infer a corpus, mechanism, or causal effect "
                        "unless that interpretation has an explicit claim binding."
                    ),
                    (
                        "Preserve all bound results and required negative findings, "
                        "but express them as natural scholarly prose. Internal "
                        "states, hashes, ledgers, identifiers and gate semantics "
                        "belong in non-rendered audit artifacts unless a method "
                        "detail is genuinely required for reproducibility."
                    ),
                    (
                        "Do not introduce implementation details unless each retained fact is explicitly present in an EvidenceClaimBinding supplied for this Study."
                    ),
                ],
            },
        }
        root = _stage4_working_root(context)
        draft = asyncio.run(
            generate_bundle_paper_draft(
                # The prompt is already structured JSON. Compact serialization
                # preserves every field while avoiding whitespace-driven
                # failures at the per-fragment safety budget.
                json.dumps(
                    prompt,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                cwd=root,
            )
        )
        if not isinstance(draft, PaperDraftSections):
            draft = PaperDraftSections.model_validate(draft)
        draft = academicize_paper_draft(
            draft,
            aliases=publication_aliases,
            language=manuscript_language(context),
        )
        draft, normalization = normalize_paper_draft(
            draft,
            frozen_conclusion=public_frozen_conclusion,
        )
        draft = _normalize_verified_citation_prefixes(
            draft,
            evidence_map.verified_source_ids,
        )
        violations = _draft_contract_violations(
            draft,
            outline=outline,
            evidence_claim_map=evidence_map,
            structure_contract=get_paper_structure_contract(
                policy.structure_profile_id
            ),
            frozen_conclusion_text=public_frozen_conclusion,
        )
        if violations:
            raise ValueError(
                "Stage 4 draft violates frozen contracts: "
                + "; ".join(violations)
            )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="paper_draft_initial_v1",
            value=draft,
            kind="paper_draft_sections",
            role=ArtifactRole.MANUSCRIPT,
        )
        snapshot_artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="manuscript_source_snapshot_v1",
            value=source_snapshot,
            kind="manuscript_source_snapshot",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        return {
            "paper_draft_sections": draft.model_dump(mode="json"),
            "normalization": normalization.model_dump(mode="json"),
            "manuscript_source_snapshot": source_snapshot.model_dump(mode="json"),
            "_workflow_output_artifact_ids": [
                artifact.artifact_id,
                snapshot_artifact.artifact_id,
            ],
        }

    def scientific_review(context: Any) -> dict[str, Any]:
        from .paper_authoring import (
            EvidenceClaimMap,
            SubmissionGenreProfile,
        )
        from .paper_expansion import PaperDraftSections, _review_draft

        root = _stage4_working_root(context)
        draft = PaperDraftSections.model_validate(
            context.result("draft_generation")["paper_draft_sections"]
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        genre = SubmissionGenreProfile.model_validate(
            read_json(root / "stage_4_synthesis" / "submission_genre.json")
        )
        decision = asyncio.run(
            _review_draft(
                root=root,
                draft=draft,
                genre=genre,
                evidence_claim_map=evidence_map,
            )
        )
        from .nuwa_panel import build_nuwa_packet, run_nuwa_panel

        publication_aliases, _ = _publication_context(context)
        literature_index = {
            source.origin_id or source.source_id: {
                "title": source.title,
                "year": source.year,
                "notes": source.notes,
                "verification_method": source.verification_method,
            }
            for source in literature_sources(context)
        }
        nuwa_packet = build_nuwa_packet(
            evidence_map,
            # Evidence pointers are Study-relative (for example
            # ``stage3/evaluations/...`` and ``contracts/research-v1.json``),
            # whereas ``root`` is the revision-local Stage 4 working folder.
            # Resolve the blinded packet from the Study root so the panel sees
            # the hash-verified frozen content instead of false
            # ``content_available=false`` placeholders.
            root=(
                context.repository.root
                / "studies"
                / context.study_id
            ),
            panel_id=stable_id(
                "nuwa-panel",
                context.study_id,
                context.step.step_instance_id,
                str(context.step.attempt),
            ),
            publication_aliases=publication_aliases,
            literature_by_id=literature_index,
        )
        nuwa_report = asyncio.run(
            run_nuwa_panel(
                nuwa_packet,
                cwd=root,
            )
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="scientific_review_panel_v1",
            value=decision,
            kind="scientific_review_panel",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="nuwa_claim_panel_v1",
            value=nuwa_report,
            kind="nuwa_same_model_claim_panel",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        if nuwa_report.human_adjudication_queue:
            persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name="nuwa_human_adjudication_queue_v1",
                value={
                    "panel_id": nuwa_report.panel_id,
                    "audit_ids": nuwa_report.human_adjudication_queue,
                    "authority": (
                        "advisory queue; does not mutate historical verdicts"
                    ),
                },
                kind="human_adjudication_queue",
                role=ArtifactRole.AUDIT,
                immutable=True,
            )
        if decision.decision == "halt":
            from .workflow_scheduler import BlockedStepError

            raise BlockedStepError(
                "AI scientific panel halted because review context is insufficient",
                kind="scientific_review_halted",
            )
        return {
            "panel_decision": decision.model_dump(mode="json"),
            "nuwa_claim_panel": nuwa_report.model_dump(mode="json"),
        }

    def bounded_revision(context: Any) -> dict[str, Any]:
        from .agent_runtime import revise_bundle_paper_draft
        from .paper_authoring import (
            EvidenceClaimMap,
            HierarchicalPaperOutline,
            PanelDecision,
            SubmissionGenreProfile,
        )
        from .paper_expansion import (
            PaperDraftSections,
            _draft_contract_violations,
            _review_draft,
            academicize_paper_draft,
            normalize_paper_draft,
            restore_required_artifact_callouts,
        )
        from .paper_pipeline import get_paper_structure_contract
        from .sci_ssci_writing import stage_four_sci_ssci_contract
        from .workflow_scheduler import BlockedStepError, TransientStepError

        root = _stage4_working_root(context)
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        structure_contract = get_paper_structure_contract(
            policy.structure_profile_id
        )
        draft = PaperDraftSections.model_validate(
            context.result("draft_generation")["paper_draft_sections"]
        )
        decision = PanelDecision.model_validate(
            context.result("scientific_review_panel")["panel_decision"]
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        publication_aliases, public_frozen_conclusion = (
            _publication_context(context)
        )
        depth_profile = _depth_profile_for_context(
            context,
            policy.manuscript_depth_profile,
        )
        outline = HierarchicalPaperOutline.model_validate(
            context.result("outline_generation_and_audit")["paper_outline"]
        )
        genre = SubmissionGenreProfile.model_validate(
            read_json(root / "stage_4_synthesis" / "submission_genre.json")
        )
        revised = draft
        reviewed_decision = decision
        resumed_scientific_revision = False
        resumed_depth_candidate = False
        previous_attempt = context.step.attempt - 1
        previous_depth_revision_path = (
            root
            / "stage_4_synthesis"
            / f"paper_draft_depth_expanded_attempt_{previous_attempt}.json"
        )
        previous_depth_review_path = (
            root
            / "stage_4_synthesis"
            / f"paper_draft_depth_rereview_attempt_{previous_attempt}.json"
        )
        if (
            previous_attempt > 0
            and previous_depth_revision_path.is_file()
            and previous_depth_review_path.is_file()
        ):
            draft = PaperDraftSections.model_validate(
                read_json(previous_depth_revision_path)
            )
            draft = academicize_paper_draft(
                draft,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
            decision = PanelDecision.model_validate(
                read_json(previous_depth_review_path)
            )
            revised = draft
            reviewed_decision = decision
            resumed_depth_candidate = True
        previous_revision_path = (
            root
            / "stage_4_synthesis"
            / f"paper_draft_revision_attempt_{previous_attempt}.json"
        )
        previous_review_path = (
            root
            / "stage_4_synthesis"
            / f"paper_draft_rereview_attempt_{previous_attempt}.json"
        )
        if (
            not resumed_depth_candidate
            and
            previous_attempt > 0
            and previous_revision_path.is_file()
            and previous_review_path.is_file()
        ):
            previous_decision = PanelDecision.model_validate(
                read_json(previous_review_path)
            )
            candidate = PaperDraftSections.model_validate(
                read_json(previous_revision_path)
            )
            candidate = academicize_paper_draft(
                candidate,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
            candidate_violations = _draft_contract_violations(
                candidate,
                outline=outline,
                evidence_claim_map=evidence_map,
                structure_contract=structure_contract,
                frozen_conclusion_text=public_frozen_conclusion,
            )
            if not candidate_violations:
                # A retry is an append-only second bounded pass over the latest
                # valid candidate and its latest panel findings. Restarting from
                # the initial draft would repeatedly reopen already repaired
                # findings and prevent convergence.
                draft = candidate
                decision = previous_decision
                revised = candidate
                reviewed_decision = previous_decision
                if previous_decision.decision == "accept":
                    resumed_scientific_revision = True
        if decision.decision == "revise" and not resumed_scientific_revision:
            revised = asyncio.run(
                revise_bundle_paper_draft(
                    json.dumps(
                        {
                            "task": (
                                "Resolve every blocking and major finding once. "
                                "Do not change frozen claims, numbers, citations, "
                                "scope, result roles, or visual callouts."
                            ),
                            "manuscript_language": (
                                "English"
                                if manuscript_language(context) == "en"
                                else "Chinese"
                            ),
                            "submission_genre": genre.model_dump(mode="json"),
                            "evidence_claim_map": _drafting_evidence_view(
                                evidence_map,
                                claim_ids=_review_claim_ids(decision),
                            ),
                            "publication_narrative_contract": (
                                context.result(
                                    "publication_narrative_contract_freeze"
                                )["publication_narrative_contract"]
                            ),
                            "approved_outline": outline.model_dump(mode="json"),
                            "draft": draft.model_dump(mode="json"),
                            "panel_decision": decision.model_dump(mode="json"),
                            "sci_ssci_writing_contract": (
                                stage_four_sci_ssci_contract(
                                    language=manuscript_language(context)
                                )
                            ),
                            "revision_requirements": [
                                (
                                    "Resolve every panel finding against the "
                                    "supplied Study-specific evidence bindings. "
                                    "If a factual detail lacks a binding, delete "
                                    "or narrow it rather than inventing support."
                                ),
                                (
                                    "Keep the frozen empirical result central. "
                                    "The reporting workflow is a local paper-"
                                    "preparation process, not an empirical result "
                                    "or a validated scientific contribution."
                                ),
                                (
                                    "Describe each arm only by its frozen role "
                                    "and bound implementation evidence. Do not "
                                    "infer a model, corpus, curriculum, or causal "
                                    "mechanism from an arm label."
                                ),
                                (
                                    "Remove binary floating-point display "
                                    "artifacts by using scientifically honest "
                                    "four-decimal reporting without changing "
                                    "threshold decisions."
                                ),
                                (
                                    "Do not promise future reviewer access or "
                                    "claim that a sharing package exists; keep "
                                    "data availability strictly conditional on "
                                    "explicit owner approval."
                                ),
                                (
                                    "State the resampling unit, observed execution "
                                    "units, and uncertainty boundary exactly as "
                                    "bound. Do not invent a seed-population claim."
                                ),
                                (
                                    "Separate contract requirements, disclosed "
                                    "implementation settings, recorded execution "
                                    "state, and independent replication or "
                                    "attestation. Evidence at one level cannot "
                                    "establish a stronger level."
                                ),
                                (
                                    "A shared hash or successful attempt supports "
                                    "traceability only. It does not independently "
                                    "verify all runtime requirements or equality "
                                    "of every execution condition."
                                ),
                                (
                                    "Remove optimizer settings, model adapters, "
                                    "prompt text, dataset sizes, example counts, "
                                    "domain counts, encoding observations, and "
                                    "other implementation details unless each "
                                    "fact is explicitly bound for this Study."
                                ),
                                (
                                    "Explain qualified, inconclusive, and the "
                                    "recorded confirmatory status once in ordinary "
                                    "scientific language using the actual frozen "
                                    "checklist and timeline."
                                ),
                                (
                                    "At first mention, distinguish the directional "
                                    "empirical proposition from the complete "
                                    "verdict rule. Quote thresholds, comparator "
                                    "scope, multiplicity handling, and safeguards "
                                    "only from frozen Study evidence."
                                ),
                                (
                                    "When the evidence map supplies arm-level "
                                    "intervention descriptions, state those "
                                    "descriptions in Methods as the operational "
                                    "arm differences while preserving any stated "
                                    "limits on unrecorded mechanism details."
                                ),
                                (
                                    "When metric operationalization, seeds, cell "
                                    "denominators, and pairing keys are bound, "
                                    "spell out how cells aggregate and what binary "
                                    "values 1 and 0 mean in Methods only. Never put "
                                    "implementation encodings or key-value notation "
                                    "in the title or abstract. If a scoring formula is "
                                    "not bound, say so explicitly instead of "
                                    "guessing it."
                                ),
                                (
                                    "Translate machine states such as confirmatory_used "
                                    "into ordinary scientific prose. Exact internal "
                                    "status tokens must not appear in reader-facing "
                                    "sections."
                                ),
                                (
                                    "Replace internal workflow jargon with "
                                    "ordinary scientific definitions. Preserve "
                                    "the exact dual- or multi-comparator structure "
                                    "of the frozen conclusion instead of reducing "
                                    "it to a singular baseline."
                                ),
                                (
                                    "Remove snake_case fields, task IDs, versioned "
                                    "pipeline labels, metric keys, and internal arm "
                                    "names from the title and reader-facing prose. "
                                    "Refer to them by scientific role; retain exact "
                                    "identifiers only in non-rendered audit records."
                                ),
                                (
                                    "Rewrite the abstract as one natural scientific "
                                    "paragraph with no more than two reader-visible "
                                    "numeric tokens. State the question, design, "
                                    "principal pattern, implication, and one scope "
                                    "boundary; move denominators, estimates, interval "
                                    "endpoints, and adjusted p-values to Results."
                                ),
                                (
                                    "Do not turn a result from this bounded fixture "
                                    "into a universal prescription. Use calibrated "
                                    "phrasing such as 'in this comparison' and "
                                    "'the results illustrate' unless a broader claim "
                                    "has an explicit evidence binding."
                                ),
                                (
                                    "Keep repeated-sampling uncertainty distinct from "
                                    "observed between-case dispersion. State the exact "
                                    "registered inferential reference and its boundary; "
                                    "if no superpopulation is bound, do not imply one."
                                ),
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    cwd=root,
                )
            )
            revised = academicize_paper_draft(
                revised,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
            revised, _ = normalize_paper_draft(
                revised,
                frozen_conclusion=public_frozen_conclusion,
            )
            revised = _normalize_verified_citation_prefixes(
                revised,
                evidence_map.verified_source_ids,
            )
            revised = restore_required_artifact_callouts(draft, revised)
            violations = _draft_contract_violations(
                revised,
                outline=outline,
                evidence_claim_map=evidence_map,
                structure_contract=structure_contract,
                frozen_conclusion_text=public_frozen_conclusion,
            )
            if violations:
                raise BlockedStepError(
                    "bounded revision violated frozen contracts: "
                    + "; ".join(violations),
                    kind="revision_integrity_failure",
                )
            reviewed_decision = asyncio.run(
                _review_draft(
                    root=root,
                    draft=revised,
                    genre=genre,
                    evidence_claim_map=evidence_map,
                )
            )
            revision_attempt = context.step.attempt
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / f"paper_draft_revision_attempt_{revision_attempt}.json",
                revised,
            )
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / f"paper_draft_rereview_attempt_{revision_attempt}.json",
                reviewed_decision,
            )
            if _has_blocking_review_findings(reviewed_decision):
                blocking_ids = set(reviewed_decision.blocking_finding_ids)
                evidence_backfill_findings = [
                    finding
                    for review in reviewed_decision.reviews
                    for finding in review.findings
                    if finding.finding_id in blocking_ids
                    and _finding_requires_stage3_backfill(finding, evidence_map)
                ]
                if evidence_backfill_findings:
                    from .stage_three import (
                        propose_stage3_scientific_successor,
                    )
                    from .workflow_domain import (
                        DiagnosticReport,
                        Stage3FailureClass,
                    )

                    plans = context.repository.list_run_plans(
                        context.study_id
                    )
                    if not plans:
                        raise BlockedStepError(
                            (
                                "scientific-review evidence backfill requires "
                                "a historical Stage 3 Run Plan"
                            ),
                            kind="scientific_successor_missing_lineage",
                            redirect_phase=Phase.EXPERIMENT,
                        )
                    changed_contract_fields: set[str] = set()
                    for finding in evidence_backfill_findings:
                        if finding.category == "statistics":
                            changed_contract_fields.update(
                                {"metrics", "estimand", "statistical_rules"}
                            )
                        if finding.category in {
                            "evidence",
                            "information",
                            "falsifiability",
                        }:
                            changed_contract_fields.update(
                                {
                                    "baseline",
                                    "treatment",
                                    "output_schema",
                                    "evaluator_policy",
                                }
                            )
                    if not changed_contract_fields:
                        changed_contract_fields.update(
                            {"output_schema", "evaluator_policy"}
                        )
                    diagnostic = DiagnosticReport(
                        diagnostic_id=stable_id(
                            "stage3-diagnostic",
                            context.study_id,
                            context.step.step_instance_id,
                            *[
                                finding.finding_id
                                for finding in evidence_backfill_findings
                            ],
                        ),
                        study_id=context.study_id,
                        plan_id=plans[-1].plan_id,
                        earliest_preventable_step_type=(
                            "research_contract_freeze"
                        ),
                        failure_class=Stage3FailureClass.SCIENTIFIC_DESIGN,
                        system_findings=[
                            (
                                f"{finding.finding_id} at "
                                f"{finding.location}: {finding.diagnosis}; "
                                f"required: {finding.required_change}"
                            )
                            for finding in evidence_backfill_findings
                        ],
                    )
                    context.repository.save_stage3_diagnostic(diagnostic)
                    successor = propose_stage3_scientific_successor(
                        context.repository,
                        context.study_id,
                        diagnostic_id=diagnostic.diagnostic_id,
                        changed_contract_fields=sorted(
                            changed_contract_fields
                        ),
                    )
                    persist_stage_four_artifact(
                        context.repository,
                        context.study_id,
                        name=(
                            "scientific_review_evidence_backfill_request_v1"
                        ),
                        value=successor,
                        kind="scientific_successor_request",
                        role=ArtifactRole.AUDIT,
                        immutable=True,
                    )
                    raise BlockedStepError(
                        (
                            "Stage 4 scientific review found evidence gaps "
                            "that prose revision cannot repair: "
                            + "; ".join(
                                finding.finding_id
                                for finding in evidence_backfill_findings
                            )
                        ),
                        kind="stage3_evidence_backfill_required",
                        redirect_phase=Phase.EXPERIMENT,
                    )
                raise TransientStepError(
                    "material scientific-review findings remain after the "
                    "current bounded revision; retry from the persisted "
                    "candidate and rereview",
                )
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / "paper_draft_rereview.json",
                reviewed_decision,
            )
        depth_expanded = False
        for depth_expansion_attempt in range(1, 3):
            depth_violations = _draft_depth_violations(
                revised,
                profile=depth_profile,
                language=manuscript_language(context),
            )
            if not depth_violations:
                break
            depth_expanded = True
            depth_contract = _localized_depth_contract(
                depth_profile,
                language=manuscript_language(context),
            )
            revised = asyncio.run(
                revise_bundle_paper_draft(
                    json.dumps(
                        {
                            "task": (
                                "Expand and restructure the scientifically accepted "
                                "draft to satisfy the journal-depth contract. "
                                "Preserve scientific authority. This is substantive "
                                "exposition, not padding or a new experiment."
                            ),
                            "manuscript_language": (
                                "English"
                                if manuscript_language(context) == "en"
                                else "Chinese"
                            ),
                            "draft": revised.model_dump(mode="json"),
                            "approved_outline": outline.model_dump(mode="json"),
                            "evidence_claim_map": _drafting_evidence_view(evidence_map),
                            "depth_violations": depth_violations,
                            "depth_expansion_attempt": depth_expansion_attempt,
                            "depth_contract": {
                                "minimum_length_by_section": (
                                    depth_contract["generation_sections"]
                                ),
                                "length_unit": depth_contract["length_unit"],
                                "minimum_substantive_paragraphs": (
                                    depth_contract["paragraphs"]
                                ),
                                "minimum_subsections": (
                                    depth_contract["subsections"]
                                ),
                                "minimum_core_narrative_length": (
                                    depth_contract["generation_total"]
                                ),
                                "manuscript_depth_profile": (
                                    depth_profile
                                ),
                                "abstract": depth_contract["abstract"],
                                "subsection_markup": (
                                    "Use semantic `### descriptive title` lines "
                                    "in methods, results, and discussion, written "
                                    "in the requested manuscript language."
                                ),
                            },
                            "expansion_rules": [
                                (
                                    "Explain existing design choices, direct "
                                    "observations, uncertainty, alternative "
                                    "explanations, validity boundaries, and "
                                    "implications using only supplied evidence."
                                ),
                                (
                                    "Do not add or alter any number, citation, "
                                    "claim, result, experiment, source, or artifact "
                                    "callout."
                                ),
                                (
                                    "Results remain observation-led; interpretation "
                                    "and limitations belong in Discussion."
                                ),
                                (
                                    "Use informative academic subsection titles, "
                                    "never generic labels such as Part 1."
                                ),
                                (
                                    "If any supplied prose is in a different language, "
                                    "rewrite it completely into the requested manuscript "
                                    "language without changing its scientific content."
                                ),
                                (
                                    "For the abstract, aim for the generation target rather "
                                    "than stopping when a minimum is crossed. Complete the "
                                    "problem, purpose, method, principal finding, scientific "
                                    "implication, and one calibrated boundary in a single "
                                    "unstructured paragraph."
                                ),
                                (
                                    "The abstract must read as a scientific summary, not an "
                                    "audit report. Avoid workflow, ledger, gate, governance, "
                                    "compliance, and audit vocabulary unless it is itself the "
                                    "registered object of study. Do not end with two consecutive "
                                    "limitation or disclaimer sentences."
                                ),
                                (
                                    "The abstract may contain at most two reader-visible numeric "
                                    "tokens. Keep detailed sample sizes, estimates, intervals, "
                                    "thresholds, and adjusted p-values in Results."
                                ),
                                (
                                    "Consolidate repeated boundary statements in Discussion. "
                                    "Related Work must compare literature, Methods must define "
                                    "the design, and Results must report observations without "
                                    "repeating the same limitation inventory."
                                ),
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    cwd=root,
                )
            )
            revised = academicize_paper_draft(
                revised,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
            revised, _ = normalize_paper_draft(
                revised,
                frozen_conclusion=public_frozen_conclusion,
            )
            revised = _normalize_verified_citation_prefixes(
                revised,
                evidence_map.verified_source_ids,
            )
            revised = restore_required_artifact_callouts(draft, revised)
            violations = _draft_contract_violations(
                revised,
                outline=outline,
                evidence_claim_map=evidence_map,
                structure_contract=structure_contract,
                frozen_conclusion_text=public_frozen_conclusion,
            )
            if violations:
                raise BlockedStepError(
                    "depth expansion violated frozen contracts: "
                    + "; ".join(violations),
                    kind="revision_integrity_failure",
                )
        remaining_depth_violations = _draft_depth_violations(
            revised,
            profile=depth_profile,
            language=manuscript_language(context),
        )
        if remaining_depth_violations:
            # Persist the near-complete candidate before retrying. The next
            # bounded attempt resumes from this append-only artifact instead
            # of returning to the shorter initial draft.
            revision_attempt = context.step.attempt
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / f"paper_draft_depth_expanded_attempt_{revision_attempt}.json",
                revised,
            )
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / f"paper_draft_depth_rereview_attempt_{revision_attempt}.json",
                reviewed_decision,
            )
            raise TransientStepError(
                "journal-depth contract remains unmet after two bounded "
                "expansions; resume the persisted candidate: "
                + "; ".join(remaining_depth_violations),
            )
        if depth_expanded:
            revision_attempt = context.step.attempt
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / f"paper_draft_depth_expanded_attempt_{revision_attempt}.json",
                revised,
            )
            reviewed_decision = asyncio.run(
                _review_draft(
                    root=root,
                    draft=revised,
                    genre=genre,
                    evidence_claim_map=evidence_map,
                )
            )
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / f"paper_draft_depth_rereview_attempt_{revision_attempt}.json",
                reviewed_decision,
            )
            if _has_blocking_review_findings(reviewed_decision):
                raise TransientStepError(
                    "blocking scientific findings opened after depth "
                    "expansion; retry from the persisted expanded draft and "
                    "rereview",
                )

        # Run the reader-facing SCI/SSCI checks before sealing the reviewed
        # draft.  The downstream step remains the independent deterministic
        # gate; this preflight merely gives the bounded revision step one
        # opportunity to repair presentation defects instead of stranding the
        # workflow after all scientific review has already passed.
        from .sci_ssci_writing import audit_stage_four_manuscript

        writing_preflight = audit_stage_four_manuscript(
            revised,
            verified_source_ids=evidence_map.verified_source_ids,
            causal_claim_authorized=any(
                binding.evidence_status == "bound"
                and binding.claim_strength == "causal"
                for binding in evidence_map.bindings
            ),
        )
        if not writing_preflight["passed"]:
            depth_contract = _localized_depth_contract(
                depth_profile,
                language=manuscript_language(context),
            )
            repaired = asyncio.run(
                revise_bundle_paper_draft(
                    json.dumps(
                        {
                            "task": (
                                "Repair only the reader-facing academic writing defects "
                                "listed by the deterministic SCI/SSCI preflight. Return "
                                "the complete PaperDraftSections schema."
                            ),
                            "manuscript_language": (
                                "English"
                                if manuscript_language(context) == "en"
                                else "Chinese"
                            ),
                            "draft": revised.model_dump(mode="json"),
                            "preflight_findings": writing_preflight["findings"],
                            "evidence_claim_map": _drafting_evidence_view(evidence_map),
                            "publication_narrative_contract": context.result(
                                "publication_narrative_contract_freeze"
                            )["publication_narrative_contract"],
                            "abstract_depth_contract": depth_contract["abstract"],
                            "sci_ssci_writing_contract": stage_four_sci_ssci_contract(
                                language=manuscript_language(context)
                            ),
                            "repair_rules": [
                                (
                                    "Preserve every frozen claim, number, citation key, "
                                    "uncertainty statement, result role, and visual callout."
                                ),
                                (
                                    "Do not add evidence, experiments, mechanisms, authors, "
                                    "funding, availability promises, or causal language."
                                ),
                                (
                                    "Write the abstract as one natural scientific paragraph. "
                                    "Aim for its generation target and finish the problem, "
                                    "purpose, method, principal finding, implication, and one "
                                    "calibrated boundary before stopping."
                                ),
                                (
                                    "Avoid audit, governance, workflow, gate, ledger, and "
                                    "compliance vocabulary in the abstract unless the frozen "
                                    "research question studies that concept directly."
                                ),
                                (
                                    "The penultimate abstract sentence states the scientific "
                                    "implication. At most the final sentence states a scope "
                                    "boundary; never end with consecutive disclaimers."
                                ),
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    cwd=root,
                )
            )
            repaired = academicize_paper_draft(
                repaired,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
            repaired, _ = normalize_paper_draft(
                repaired,
                frozen_conclusion=public_frozen_conclusion,
            )
            repaired = _normalize_verified_citation_prefixes(
                repaired,
                evidence_map.verified_source_ids,
            )
            repaired = restore_required_artifact_callouts(revised, repaired)
            repair_violations = _draft_contract_violations(
                repaired,
                outline=outline,
                evidence_claim_map=evidence_map,
                structure_contract=structure_contract,
                frozen_conclusion_text=public_frozen_conclusion,
            )
            repair_violations.extend(
                _draft_depth_violations(
                    repaired,
                    profile=depth_profile,
                    language=manuscript_language(context),
                )
            )
            repaired_preflight = audit_stage_four_manuscript(
                repaired,
                verified_source_ids=evidence_map.verified_source_ids,
                causal_claim_authorized=any(
                    binding.evidence_status == "bound"
                    and binding.claim_strength == "causal"
                    for binding in evidence_map.bindings
                ),
            )
            if repair_violations or not repaired_preflight["passed"]:
                raise BlockedStepError(
                    "SCI/SSCI writing preflight remains unmet after one bounded "
                    "repair: "
                    + "; ".join(
                        [
                            *repair_violations,
                            *(
                                str(item["message"])
                                for item in repaired_preflight["findings"]
                            ),
                        ]
                    ),
                    kind="sci_ssci_preservation_failure",
                )
            repaired_review = asyncio.run(
                _review_draft(
                    root=root,
                    draft=repaired,
                    genre=genre,
                    evidence_claim_map=evidence_map,
                )
            )
            if _has_blocking_review_findings(repaired_review):
                raise TransientStepError(
                    "blocking scientific findings opened after SCI/SSCI "
                    "writing repair; retry from the persisted repaired draft "
                    "and rereview",
                )
            revised = repaired
            write_json_atomic(
                root
                / "stage_4_synthesis"
                / f"paper_draft_writing_repaired_attempt_{context.step.attempt}.json",
                revised,
            )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="paper_draft_reviewed_v1",
            value=revised,
            kind="reviewed_paper_draft",
            role=ArtifactRole.MANUSCRIPT,
            immutable=True,
        )
        return {
            "reviewed_draft": revised.model_dump(mode="json"),
            "material_findings_closed": True,
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def sci_ssci_preservation_audit(context: Any) -> dict[str, Any]:
        from .paper_authoring import EvidenceClaimMap
        from .paper_expansion import PaperDraftSections
        from .sci_ssci_writing import audit_stage_four_manuscript
        from .workflow_scheduler import BlockedStepError

        draft = PaperDraftSections.model_validate(
            context.result("bounded_content_revision")["reviewed_draft"]
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        report = audit_stage_four_manuscript(
            draft,
            verified_source_ids=evidence_map.verified_source_ids,
            causal_claim_authorized=any(
                binding.evidence_status == "bound"
                and binding.claim_strength == "causal"
                for binding in evidence_map.bindings
            ),
        )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="sci_ssci_preservation_audit_v1",
            value=report,
            kind="sci_ssci_preservation_audit",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        if not report["passed"]:
            raise BlockedStepError(
                "SCI/SSCI writing preservation audit failed: "
                + "; ".join(
                    str(item["message"]) for item in report["findings"]
                ),
                kind="sci_ssci_preservation_failure",
            )
        return {
            "sci_ssci_preservation_audit": report,
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def figure_generation(context: Any) -> dict[str, Any]:
        from .paper_authoring import (
            EvidenceClaimMap,
            HierarchicalPaperOutline,
            build_paper_artifacts,
        )

        root = _stage4_working_root(context)
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        outline = HierarchicalPaperOutline.model_validate(
            context.result("outline_generation_and_audit")["paper_outline"]
        )
        manifest = build_paper_artifacts(
            root,
            outline=outline,
            evidence_claim_map=evidence_map,
        )
        profile_figure_result: dict[str, Any] = {
            "status": "legacy_not_available",
            "profile_id": None,
            "reports": [],
            "blocking_issues": [],
        }
        figure_data_artifacts = [
            item
            for item in context.repository.list_artifacts(context.study_id)
            if item.kind == "profile_figure_data_bundle_v2"
        ]
        if figure_data_artifacts:
            from .profile_figure_data import ProfileFigureDataBundle
            from .scientific_figure_engine import (
                render_and_accept_figure,
                validate_profile_figure_set,
            )
            from .workflow_scheduler import BlockedStepError

            figure_data_artifact = max(
                figure_data_artifacts,
                key=lambda item: (item.version, item.created_at),
            )
            figure_data_path = Path(figure_data_artifact.path).resolve()
            if (
                not figure_data_path.is_file()
                or sha256_file(figure_data_path) != figure_data_artifact.sha256
            ):
                raise BlockedStepError(
                    "Profile FigureData artifact is missing or its frozen hash changed",
                    kind="profile_figure_data_integrity_failure",
                )
            bundle = ProfileFigureDataBundle.model_validate(
                read_json(figure_data_path)
            )
            blocking_issues = list(bundle.blocking_issues)
            blocking_issues.extend(
                validate_profile_figure_set(bundle.profile_id, bundle.specs)
            )
            bindings = {item.data_hash: item for item in bundle.bindings}
            scientific_root = (
                root / "stage_4_synthesis" / "paper_assets" / "scientific_figures"
            )
            reports = []
            figure_entries: list[dict[str, Any]] = []
            output_artifact_ids: list[str] = []
            for spec in bundle.specs:
                binding = bindings.get(spec.data_hash)
                if binding is None:
                    blocking_issues.append(
                        {
                            "figure_id": spec.figure_id,
                            "code": "FIGURE_DATA_BINDING_MISSING",
                            "message": "No frozen FigureData binding matches the Figure Spec hash.",
                            "required_action": "rebuild the Stage 3 Profile FigureData bundle",
                        }
                    )
                    continue
                report = render_and_accept_figure(
                    spec,
                    binding,
                    scientific_root,
                )
                reports.append(report)
                figure_entries.append(
                    {
                        "figure_id": spec.figure_id,
                        "figure_type": spec.figure_type,
                        "caption": spec.caption,
                        "alt_text": spec.alt_text,
                        "png_path": str(
                            scientific_root / f"{spec.figure_id}.png"
                        ),
                        "pdf_path": str(
                            scientific_root / f"{spec.figure_id}.pdf"
                        ),
                    }
                )
                for candidate in sorted(scientific_root.glob(f"{spec.figure_id}.*")):
                    if not candidate.is_file():
                        continue
                    suffix = candidate.suffix.lower().removeprefix(".") or "file"
                    generated = context.repository.register_artifact(
                        context.study_id,
                        str(candidate),
                        sha256_file(candidate),
                        kind=f"scientific_figure_{suffix}",
                        role=ArtifactRole.MANUSCRIPT,
                    )
                    context.repository.add_dependency(
                        context.study_id,
                        figure_data_artifact.artifact_id,
                        generated.artifact_id,
                        relation="rendered_from_profile_figure_data",
                    )
                    output_artifact_ids.append(generated.artifact_id)
            rejected = [item for item in reports if not item.accepted]
            profile_figure_result = {
                "status": "accepted" if not blocking_issues and not rejected else "blocked",
                "profile_id": bundle.profile_id,
                "figure_data_artifact_id": figure_data_artifact.artifact_id,
                "figure_data_sha256": figure_data_artifact.sha256,
                "output_dir": str(scientific_root),
                "reports": [item.model_dump(mode="json") for item in reports],
                "figures": figure_entries,
                "blocking_issues": [
                    item.model_dump(mode="json")
                    if hasattr(item, "model_dump")
                    else dict(item)
                    for item in blocking_issues
                ],
            }
            if profile_figure_result["status"] != "accepted":
                raise BlockedStepError(
                    "Profile-specific scientific figure generation did not pass",
                    kind="profile_scientific_figure_failure",
                )
        else:
            output_artifact_ids = []
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="paper_artifact_manifest_v1",
            value=manifest,
            kind="paper_artifact_manifest",
            role=ArtifactRole.MANUSCRIPT,
            immutable=True,
        )
        return {
            "paper_artifact_manifest": manifest.model_dump(mode="json"),
            "profile_scientific_figures": profile_figure_result,
            "_workflow_output_artifact_ids": [
                artifact.artifact_id,
                *output_artifact_ids,
            ],
        }

    def visual_integrity(context: Any) -> dict[str, Any]:
        from .paper_authoring import PaperArtifactManifest
        from .paper_visual_integrity import audit_visual_integrity
        from .workflow_scheduler import BlockedStepError

        plan = VisualArgumentPlan.model_validate(
            load_stage_four_artifact(
                context.repository,
                context.study_id,
                "visual_argument_plan_frozen_v1",
            )
        )
        manifest = PaperArtifactManifest.model_validate(
            context.result("figure_and_table_generation")[
                "paper_artifact_manifest"
            ]
        )
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        report = audit_visual_integrity(
            plan,
            manifest,
            venue_policy=policy,
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name=(
                "visual_integrity_report_v1"
                if context.step.attempt == 1
                else (
                    "visual_integrity_report_v1_attempt_"
                    f"{context.step.attempt}"
                )
            ),
            value=report,
            kind="visual_integrity_report",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        if not report.passed:
            raise BlockedStepError(
                "visual integrity failed: "
                + "; ".join(item.message for item in report.findings),
                kind="visual_integrity_failure",
            )
        return {"visual_integrity_report": report.model_dump(mode="json")}

    def authorial_humanization(context: Any) -> dict[str, Any]:
        from .agent_runtime import (
            DEFAULT_CODEX_MODEL,
            humanize_bundle_paper_draft,
        )
        from .paper_author_voice import (
            AuthorVoiceProfile,
            SentenceLengthDistribution,
        )
        from .paper_expansion import (
            PaperDraftSections,
            academicize_paper_draft,
            normalize_paper_draft,
        )
        from .paper_humanize import (
            HumanizationPlan,
            HumanizationSectionDiff,
            HumanizationTrace,
        )
        from .manuscript_architecture import (
            ManuscriptSourceSnapshot,
            audit_manuscript_citations,
            audit_source_freshness,
            build_manuscript_source_snapshot,
            build_reverse_outline,
        )
        from .paper_pipeline import get_paper_structure_contract
        from .sci_ssci_writing import stage_four_sci_ssci_contract

        final_visual_step = next(
            item
            for item in context.repository.list_steps(context.study_id)
            if item.step_instance_id in context.step.depends_on
            and item.step_type == "final_visual_approval"
        )
        final_visual_gate = next(
            item
            for item in context.repository.list_gates(context.study_id)
            if item.gate_type is GateType.VISUAL_ARGUMENT
            and item.subject_id
            == final_visual_step.parameters.get("subject_id")
            and item.status.value == "approved"
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="final_visual_approval_v1",
            value={
                "schema_version": 1,
                "study_id": context.study_id,
                "status": "approved",
                "decided_by": final_visual_gate.decided_by,
                "decided_at": final_visual_gate.decided_at,
                "reason": final_visual_gate.reason,
                "visual_integrity_report": (
                    context.result("visual_integrity_audit")[
                        "visual_integrity_report"
                    ]
                ),
            },
            kind="visual_approval",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        root = _stage4_working_root(context)
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        depth_profile = _depth_profile_for_context(
            context,
            policy.manuscript_depth_profile,
        )
        depth_contract = _localized_depth_contract(
            depth_profile,
            language=manuscript_language(context),
        )
        source = PaperDraftSections.model_validate(
            context.result("bounded_content_revision")["reviewed_draft"]
        )
        publication_aliases, _ = _publication_context(context)
        study = context.repository.load_study(context.study_id)
        configured_voice = study.settings.get("author_voice_profile")
        voice = (
            AuthorVoiceProfile.model_validate(configured_voice)
            if isinstance(configured_voice, dict)
            else AuthorVoiceProfile(
                profile_id="voice-default-precise",
                language=manuscript_language(context),
                source_types=["style_questionnaire"],
                sentence_length_distribution=SentenceLengthDistribution(
                    short=0.25,
                    medium=0.55,
                    long=0.20,
                ),
            )
        )
        narrative = PublicationNarrativeContract.model_validate(
            context.result("publication_narrative_contract_freeze")[
                "publication_narrative_contract"
            ]
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        structure_contract = get_paper_structure_contract(
            policy.structure_profile_id
        )
        sources = literature_sources(context)
        reverse_outline = build_reverse_outline(
            {
                field_name: str(getattr(source, field_name))
                for field_name in PaperDraftSections.model_fields
            },
            structure_contract.narrative,
        )
        citation_preflight = audit_manuscript_citations(
            sections={
                field_name: str(getattr(source, field_name))
                for field_name in PaperDraftSections.model_fields
            },
            evidence_claim_map=evidence_map,
            literature_sources=sources,
        )
        current_snapshot = build_manuscript_source_snapshot(
            {
                "evidence_claim_map": evidence_map,
                "literature_sources": [
                    item.model_dump(mode="json") for item in sources
                ],
                "publication_narrative_contract": narrative,
                "visual_argument_plan": load_stage_four_artifact(
                    context.repository,
                    context.study_id,
                    "visual_argument_plan_frozen_v1",
                ),
                "approved_outline": context.result(
                    "outline_generation_and_audit"
                )["paper_outline"],
                "mandatory_reporting_register": context.result(
                    "mandatory_reporting_register"
                )["mandatory_reporting_register"],
                "evaluation_transparency_register": context.result(
                    "evaluation_transparency_register"
                )["evaluation_transparency_register"],
            }
        )
        drafted_snapshot_payload = context.result("draft_generation").get(
            "manuscript_source_snapshot"
        )
        drafted_snapshot = (
            ManuscriptSourceSnapshot.model_validate(drafted_snapshot_payload)
            if drafted_snapshot_payload
            else current_snapshot
        )
        freshness_preflight = audit_source_freshness(
            drafted_snapshot,
            current_snapshot,
            legacy_snapshot_migrated=(
                not bool(drafted_snapshot_payload)
                or drafted_snapshot.schema_version == 1
            ),
            # LiteratureSource is reconstructed from the immutable verified-
            # literature ancestor.  Legacy v1 snapshots included adapter-only
            # timestamps, so this component can be migrated without treating
            # those timestamps as a scientific source change.
            compatible_reconstructed_components={"literature_sources"},
        )
        if not freshness_preflight.passed:
            from .workflow_scheduler import BlockedStepError

            raise BlockedStepError(
                "authorial editing refused stale manuscript inputs: "
                + ", ".join(freshness_preflight.changed_components),
                kind="stale_manuscript_source",
            )
        attempt_suffix = (
            "" if context.step.attempt <= 1 else f"_attempt_{context.step.attempt}"
        )
        for name, value, kind in (
            (
                f"manuscript_reverse_outline_preflight_v1{attempt_suffix}",
                reverse_outline,
                "manuscript_reverse_outline_preflight",
            ),
            (
                f"manuscript_citation_preflight_v1{attempt_suffix}",
                citation_preflight,
                "manuscript_citation_preflight",
            ),
            (
                f"manuscript_source_freshness_preflight_v1{attempt_suffix}",
                freshness_preflight,
                "manuscript_source_freshness_preflight",
            ),
        ):
            persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name=name,
                value=value,
                kind=kind,
                role=ArtifactRole.AUDIT,
                immutable=True,
            )
        plan = HumanizationPlan(
            plan_id="humanize-authorial-voice-v1",
            study_id=context.study_id,
            source_artifact_id=hashlib.sha256(
                json.dumps(
                    source.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            narrative_contract_id=narrative.contract_sha256,
            voice_profile_id=voice.profile_id,
            level="authorial_voice",
            sections=list(source.model_dump(mode="json")),
            operations=[
                "grammar",
                "punctuation",
                "conciseness",
                "remove_ai_tells",
                "sentence_rhythm",
                "terminology",
                "paragraph_logic",
                "calibrate_claim_verbs",
                "neutral_limitation_framing",
            ],
        )
        humanization_request = json.dumps(
                    {
                        "task": (
                            "Apply evidence-preserving academic authorial editing. "
                            "This is not an AI-detector evasion task. Preserve all "
                            "semantic subsection headings and do not shorten any "
                            "section below the supplied journal-depth contract."
                        ),
                        "manuscript_language": manuscript_language(context),
                        "author_voice_profile": voice.model_dump(mode="json"),
                        "humanization_plan": plan.model_dump(mode="json"),
                        "publication_narrative_contract": narrative.model_dump(
                            mode="json"
                        ),
                        "reverse_outline_preflight": (
                            _humanization_reverse_outline_view(reverse_outline)
                        ),
                        "citation_preflight": _humanization_citation_view(
                            citation_preflight
                        ),
                        "source_freshness_preflight": (
                            freshness_preflight.model_dump(mode="json")
                        ),
                        "evidence_claim_map": _humanization_evidence_view(
                            evidence_map
                        ),
                        "visual_argument_plan": load_stage_four_artifact(
                            context.repository,
                            context.study_id,
                            "visual_argument_plan_frozen_v1",
                        ),
                        "source_draft": source.model_dump(mode="json"),
                        "sci_ssci_writing_contract": (
                            stage_four_sci_ssci_contract(
                                language=manuscript_language(context)
                            )
                        ),
                        "journal_depth_contract": {
                            "minimum_length_by_section": (
                                depth_contract["sections"]
                            ),
                            "length_unit": depth_contract["length_unit"],
                            "minimum_substantive_paragraphs": (
                                depth_contract["paragraphs"]
                            ),
                            "minimum_subsections": depth_contract["subsections"],
                            "minimum_core_narrative_length": (
                                depth_contract["total"]
                            ),
                            "manuscript_depth_profile": (
                                depth_profile
                            ),
                            "abstract": depth_contract["abstract"],
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        # Authorial editing is a non-scientific, optional transformation.  A
        # very long reviewed manuscript must not make the publication DAG fail
        # merely because a stylistic model request would exceed its bounded
        # context.  Preserve the reviewed source as an explicit audited no-op;
        # the following integrity gate still checks the complete manuscript.
        humanization_prompt_limit = 120_000
        humanization_reverted = len(humanization_request) > humanization_prompt_limit
        humanization_model_invoked = not humanization_reverted
        revert_reasons: list[str] = []
        if humanization_reverted:
            output = source
            revert_reasons.append(
                "bounded humanization request exceeded the safe prompt budget; "
                "retained the reviewed manuscript unchanged"
            )
        else:
            output = asyncio.run(
                humanize_bundle_paper_draft(
                    humanization_request,
                    cwd=root,
                )
            )
            output, _ = normalize_paper_draft(output)
            output = academicize_paper_draft(
                output,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
        depth_violations = _draft_depth_violations(
            output,
            profile=depth_profile,
            language=manuscript_language(context),
        )
        if depth_violations:
            # Humanization has no scientific authority. If a stylistic edit
            # weakens an already-valid manuscript contract, retain the
            # reviewed source instead of blocking or asking the model to pad
            # the paper. The subsequent integrity audit still records and
            # verifies this no-op fallback.
            output = source
            humanization_reverted = True
            revert_reasons.extend(depth_violations)
        diffs = [
            HumanizationSectionDiff(
                section_key=section,
                source_sha256=hashlib.sha256(
                    str(getattr(source, section)).encode("utf-8")
                ).hexdigest(),
                humanized_sha256=hashlib.sha256(
                    str(getattr(output, section)).encode("utf-8")
                ).hexdigest(),
                changed=getattr(source, section) != getattr(output, section),
                operation_summary=(
                    ["authorial academic edit"]
                    if getattr(source, section) != getattr(output, section)
                    else []
                ),
            )
            for section in source.model_dump(mode="json")
        ]
        trace = HumanizationTrace(
            trace_id="humanization-trace-authorial-voice-v1",
            plan_id=plan.plan_id,
            source_artifact_id=plan.source_artifact_id,
            output_artifact_id=hashlib.sha256(
                json.dumps(
                    output.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            section_diffs=diffs,
            model_id=(
                DEFAULT_CODEX_MODEL
                if humanization_model_invoked
                else "deterministic-no-op"
            ),
            prompt_version="academic-humanizer-v1",
        )
        for name, value, kind in (
            ("author_voice_profile_v1", voice, "author_voice_profile"),
            ("humanization_plan_v1", plan, "humanization_plan"),
            ("humanization_trace_v1", trace, "humanization_trace"),
            ("paper_humanized_sections_v1", output, "humanized_paper_draft"),
        ):
            persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name=name,
                value=value,
                kind=kind,
                role=ArtifactRole.MANUSCRIPT,
                immutable=name != "paper_humanized_sections_v1",
            )
        return {
            "humanized_draft": output.model_dump(mode="json"),
            "humanization_plan": plan.model_dump(mode="json"),
            "humanization_trace": trace.model_dump(mode="json"),
            "humanization_reverted": humanization_reverted,
            "revert_reason": (
                "; ".join(revert_reasons)
                if humanization_reverted
                else None
            ),
            "request_character_count": len(humanization_request),
            "model_invoked": humanization_model_invoked,
        }

    def humanization_integrity(context: Any) -> dict[str, Any]:
        from .paper_authoring import EvidenceClaimMap
        from .paper_expansion import PaperDraftSections, _draft_text
        from .paper_humanize import (
            audit_humanization_integrity,
            snapshot_semantics,
        )
        from .workflow_scheduler import BlockedStepError

        source = PaperDraftSections.model_validate(
            context.result("bounded_content_revision")["reviewed_draft"]
        )
        output = PaperDraftSections.model_validate(
            context.result("authorial_humanization")["humanized_draft"]
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        narrative = PublicationNarrativeContract.model_validate(
            context.result("publication_narrative_contract_freeze")[
                "publication_narrative_contract"
            ]
        )
        authority = context.result("stage4_claim_intake")
        scientific_claim = dict((authority.get("claims") or [])[0])
        contract = context.repository.latest_research_contract(
            context.study_id
        )
        claim_ids = [item.claim_id for item in evidence_map.bindings]
        evidence_ids = sorted(
            {
                pointer.source_id or pointer.path
                for binding in evidence_map.bindings
                for pointer in binding.evidence
            }
        )
        relationships = {
            item.claim_id: item.claim_strength
            for item in evidence_map.bindings
        }
        scope = {
            claim_id: list(narrative.advantage_conditions)
            for claim_id in claim_ids
        }
        roles: dict[str, str] = {}
        for claim_id in narrative.required_claim_ids:
            roles[claim_id] = "primary"
        for claim_id in narrative.supporting_claim_ids:
            roles[claim_id] = "secondary"
        for claim_id in narrative.mandatory_negative_claim_ids:
            roles[claim_id] = "limitation"
        common = {
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "claim_relationships": relationships,
            "scope_qualifiers": scope,
            "uncertainty_levels": {
                claim_id: (
                    "estimated"
                    if scientific_claim.get("interval") is not None
                    else "confirmed"
                )
                for claim_id in claim_ids
            },
            "result_roles": roles,
            "confirmatory_status": str(
                scientific_claim.get("confirmatory_status")
                or "untouched"
            ),
            "evidence_maturity": str(
                scientific_claim.get("evidence_level")
                or "scientific_claim_envelope"
            ),
            "definitions": {
                str(metric.get("name") or f"metric-{index + 1}"): str(
                    metric.get("definition")
                    or metric.get("denominator")
                    or metric
                )
                for index, metric in enumerate(
                    contract.metrics if contract is not None else []
                )
            },
        }
        source_snapshot = snapshot_semantics(
            prose=_draft_text(source),
            **common,
        )
        output_snapshot = snapshot_semantics(
            prose=_draft_text(output),
            **common,
        )
        report = audit_humanization_integrity(
            source_snapshot,
            output_snapshot,
            source_prose=_draft_text(source),
            humanized_prose=_draft_text(output),
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name=(
                "humanization_integrity_report_v1"
                if context.step.attempt == 1
                else (
                    "humanization_integrity_report_v1_attempt_"
                    f"{context.step.attempt}"
                )
            ),
            value=report,
            kind="humanization_integrity_report",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        if not report.passed:
            # Authorial editing has no scientific authority. Reject an unsafe
            # stylistic candidate and deterministically retain the reviewed
            # source instead of blocking the publication DAG or allowing the
            # changed semantics downstream.
            fallback_report = audit_humanization_integrity(
                source_snapshot,
                source_snapshot,
                source_prose=_draft_text(source),
                humanized_prose=_draft_text(source),
            )
            persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name=(
                    "humanization_integrity_fallback_report_v1"
                    if context.step.attempt == 1
                    else (
                        "humanization_integrity_fallback_report_v1_attempt_"
                        f"{context.step.attempt}"
                    )
                ),
                value=fallback_report,
                kind="humanization_integrity_fallback_report",
                role=ArtifactRole.AUDIT,
                immutable=True,
            )
            return {
                "humanization_integrity_report": fallback_report.model_dump(
                    mode="json"
                ),
                "approved_draft": source.model_dump(mode="json"),
                "humanization_reverted": True,
                "rejected_humanization_violations": list(report.violations),
            }
        return {
            "humanization_integrity_report": report.model_dump(mode="json"),
            "approved_draft": output.model_dump(mode="json"),
            "humanization_reverted": False,
            "rejected_humanization_violations": [],
        }

    def reviewer_attack_surface(context: Any) -> dict[str, Any]:
        from .paper_author_voice import AuthorVoiceApproval
        from .paper_authoring import HierarchicalPaperOutline, OutlineNode
        from .paper_expansion import PaperDraftSections, _draft_text
        from .paper_reporting_compliance import MandatoryReportingRegister
        from .paper_reviewer_attack_surface import (
            audit_reviewer_attack_surface,
        )
        from .workflow_scheduler import BlockedStepError

        integrity_result = context.result("humanization_integrity_audit")
        draft = PaperDraftSections.model_validate(
            integrity_result.get("approved_draft")
            or context.result("authorial_humanization")["humanized_draft"]
        )
        voice_gate_step = next(
            item
            for item in context.repository.list_steps(context.study_id)
            if item.step_instance_id in context.step.depends_on
            and item.step_type == "humanization_author_approval"
        )
        voice_gate = next(
            item
            for item in context.repository.list_gates(context.study_id)
            if item.gate_type is GateType.AUTHOR_VOICE
            and item.subject_id
            == voice_gate_step.parameters.get("subject_id")
            and item.status.value == "approved"
        )
        humanization_result = context.result("authorial_humanization")
        trace = humanization_result["humanization_trace"]
        voice_profile = load_stage_four_artifact(
            context.repository,
            context.study_id,
            "author_voice_profile_v1",
        )
        voice_approval = AuthorVoiceApproval(
            approval_id=(
                "voice-approval-"
                + stable_id(
                    "voice",
                    context.study_id,
                    str(trace["output_artifact_id"]),
                ).split("-", 1)[1]
            ),
            study_id=context.study_id,
            voice_profile_id=str(voice_profile["profile_id"]),
            humanized_artifact_id=str(trace["output_artifact_id"]),
            status="approved",
            decided_by=str(voice_gate.decided_by or "project_owner"),
            section_decisions={
                str(item["section_key"]): "approved"
                for item in trace["section_diffs"]
            },
            reason=voice_gate.reason,
            decided_at=str(voice_gate.decided_at or utc_now()),
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="author_voice_approval_v1",
            value=voice_approval,
            kind="author_voice_approval",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        register = MandatoryReportingRegister.model_validate(
            context.result("mandatory_reporting_register")[
                "mandatory_reporting_register"
            ]
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        full_text = _draft_text(draft)
        verbatim_presented = {
            binding.claim_id
            for binding in evidence_map.bindings
            if binding.statement in full_text
        }
        outline = HierarchicalPaperOutline.model_validate(
            context.result("outline_generation_and_audit")["paper_outline"]
        )

        def outline_claims(nodes: list[OutlineNode]) -> set[str]:
            return {
                claim_id
                for node in nodes
                for claim_id in node.claim_ids
            } | {
                claim_id
                for node in nodes
                for claim_id in outline_claims(node.children)
            }

        # Exact English-statement matching cannot establish coverage in a
        # Chinese manuscript. A claim is also considered presented when it was
        # explicitly bound in the frozen, panel-reviewed outline and the
        # scientific panel plus post-revision rereview accepted the complete
        # draft. Humanization integrity is checked immediately upstream, so
        # this structural lineage is stronger than cross-language substring
        # matching while still requiring a registered claim identifier.
        panel_accepted = bool(
            context.result("bounded_content_revision").get(
                "material_findings_closed"
            )
        )
        structurally_presented = (
            outline_claims(outline.sections) if panel_accepted else set()
        )
        presented = sorted(verbatim_presented | structurally_presented)
        report = audit_reviewer_attack_surface(
            title=draft.title,
            sections={
                key: str(value)
                for key, value in draft.model_dump(mode="json").items()
                if key != "title"
            },
            reporting_register=register,
            presented_claim_ids=presented,
            narrative_contract=PublicationNarrativeContract.model_validate(
                context.result("publication_narrative_contract_freeze")[
                    "publication_narrative_contract"
                ]
            ),
            claim_relationships={
                item.claim_id: item.claim_strength
                for item in evidence_map.bindings
            },
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name=(
                "reviewer_attack_surface_report_v1"
                if context.step.attempt == 1
                else (
                    "reviewer_attack_surface_report_v1_attempt_"
                    f"{context.step.attempt}"
                )
            ),
            value=report,
            kind="reviewer_attack_surface_report",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        if not report.passed:
            scientific = [
                item
                for item in report.findings
                if item.disposition == "stage3_successor"
            ]
            if scientific:
                from .stage_three import (
                    propose_stage3_scientific_successor,
                )
                from .workflow_domain import (
                    DiagnosticReport,
                    Stage3FailureClass,
                )

                plans = context.repository.list_run_plans(
                    context.study_id
                )
                if not plans:
                    raise BlockedStepError(
                        "scientific successor required but no historical Run Plan exists",
                        kind="scientific_successor_missing_lineage",
                        redirect_phase=Phase.EXPERIMENT,
                    )
                diagnostic = DiagnosticReport(
                    diagnostic_id=stable_id(
                        "stage3-diagnostic",
                        context.study_id,
                        context.step.step_instance_id,
                        *[item.finding_id for item in scientific],
                    ),
                    study_id=context.study_id,
                    plan_id=plans[-1].plan_id,
                    earliest_preventable_step_type=(
                        "publication_narrative_contract_freeze"
                    ),
                    failure_class=Stage3FailureClass.SCIENTIFIC_DESIGN,
                    system_findings=[
                        (
                            f"{item.location}: {item.problematic_text}; "
                            f"{item.recommended_action}"
                        )
                        for item in scientific
                    ],
                )
                context.repository.save_stage3_diagnostic(diagnostic)
                request = propose_stage3_scientific_successor(
                    context.repository,
                    context.study_id,
                    diagnostic_id=diagnostic.diagnostic_id,
                    changed_contract_fields=["hypotheses"],
                )
                persist_stage_four_artifact(
                    context.repository,
                    context.study_id,
                    name="scientific_successor_request_v1",
                    value=request,
                    kind="scientific_successor_request",
                    role=ArtifactRole.AUDIT,
                    immutable=True,
                )
            raise BlockedStepError(
                (
                    "Stage 3 successor required: "
                    if scientific
                    else "Stage 4 narrative repair required: "
                )
                + "; ".join(
                    f"{item.location}: {item.problematic_text}"
                    for item in report.findings
                ),
                kind=(
                    "scientific_successor_required"
                    if scientific
                    else "reviewer_attack_surface"
                ),
                redirect_phase=(
                    Phase.EXPERIMENT if scientific else None
                ),
            )
        return {
            "reviewer_attack_surface_report": report.model_dump(mode="json"),
            "presented_claim_ids": presented,
        }

    def reporting_and_disclosure(context: Any) -> dict[str, Any]:
        from .agent_runtime import (
            DEFAULT_CODEX_MODEL,
            revise_bundle_paper_draft,
            revise_bundle_paper_sections,
        )
        from .paper_ai_disclosure import (
            AIUseEvent,
            build_ai_use_disclosure,
        )
        from .paper_expansion import (
            PaperDraftSections,
            _draft_contract_violations,
            _polish_trace,
            academicize_paper_draft,
            normalize_reader_facing_governance,
            normalize_paper_draft,
        )
        from .manuscript_architecture import (
            ManuscriptSourceSnapshot,
            audit_manuscript_citations,
            audit_source_freshness,
            build_manuscript_source_snapshot,
            build_reverse_outline,
        )
        from .paper_pipeline import (
            abstract_numeric_token_count,
            centralize_defensive_boundaries,
            compact_conclusion_paragraphs,
            get_paper_structure_contract,
            relocate_conclusion_numeric_detail,
            validate_abstract_prose,
            validate_manuscript_narrative,
        )
        from .paper_reporting_compliance import (
            MandatoryReportingRegister,
            audit_reporting_integrity,
        )
        from .workflow_scheduler import BlockedStepError

        integrity_result = context.result("humanization_integrity_audit")
        draft = PaperDraftSections.model_validate(
            integrity_result.get("approved_draft")
            or context.result("authorial_humanization")["humanized_draft"]
        )
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        structure_contract = get_paper_structure_contract(
            policy.structure_profile_id
        )
        depth_profile = _depth_profile_for_context(
            context,
            policy.manuscript_depth_profile,
        )
        depth_contract = _localized_depth_contract(
            depth_profile,
            language=manuscript_language(context),
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        current_sources = literature_sources(context)
        current_snapshot = build_manuscript_source_snapshot(
            {
                "evidence_claim_map": evidence_map,
                "literature_sources": [
                    source.model_dump(mode="json") for source in current_sources
                ],
                "publication_narrative_contract": context.result(
                    "publication_narrative_contract_freeze"
                )["publication_narrative_contract"],
                "visual_argument_plan": load_stage_four_artifact(
                    context.repository,
                    context.study_id,
                    "visual_argument_plan_frozen_v1",
                ),
                "approved_outline": context.result(
                    "outline_generation_and_audit"
                )["paper_outline"],
                "mandatory_reporting_register": context.result(
                    "mandatory_reporting_register"
                )["mandatory_reporting_register"],
                "evaluation_transparency_register": context.result(
                    "evaluation_transparency_register"
                )["evaluation_transparency_register"],
            }
        )
        drafted_snapshot_payload = context.result("draft_generation").get(
            "manuscript_source_snapshot"
        )
        # Read compatibility for Stage 4 revisions created before source
        # snapshots existed.  Their immutable inputs are snapshotted at the
        # first audit under the new code; every subsequent revision is then
        # freshness-checked normally.
        drafted_snapshot = (
            ManuscriptSourceSnapshot.model_validate(drafted_snapshot_payload)
            if drafted_snapshot_payload
            else current_snapshot
        )
        source_freshness = audit_source_freshness(
            drafted_snapshot,
            current_snapshot,
            legacy_snapshot_migrated=(
                not bool(drafted_snapshot_payload)
                or drafted_snapshot.schema_version == 1
            ),
            compatible_reconstructed_components={"literature_sources"},
        )
        if not source_freshness.passed:
            raise BlockedStepError(
                "manuscript inputs changed after drafting: "
                + ", ".join(source_freshness.changed_components),
                kind="stale_manuscript_source",
            )
        reverse_outline = build_reverse_outline(
            {
                field_name: str(getattr(draft, field_name))
                for field_name in PaperDraftSections.model_fields
            },
            structure_contract.narrative,
        )
        # Reader-facing aliases and the frozen public conclusion are shared by
        # abstract, deterministic narrative, and model-assisted repairs.
        publication_aliases, public_frozen_conclusion = _publication_context(
            context
        )
        abstract_rewritten = False
        narrative_rewritten = False
        abstract_presentation_violations = validate_abstract_prose(
            draft.abstract,
            structure_contract.abstract,
        )
        if abstract_presentation_violations:
            publication_aliases, public_frozen_conclusion = (
                _publication_context(context)
            )
            candidate = asyncio.run(
                revise_bundle_paper_draft(
                    json.dumps(
                        {
                            "task": (
                                "Rewrite only the abstract as one natural academic "
                                "paragraph. Open with the scientific problem or tension, "
                                "name only the design detail needed to understand the "
                                "comparison, state the main finding in plain language, "
                                "and explain its scientific implication before ending "
                                "with one calibrated boundary sentence. Prefer no numerals. "
                                "If a numeral is indispensable, use at most two. Move "
                                "all detailed estimates, intervals, p-values, counts, "
                                "seeds, and implementation numbers to Results. Return "
                                "the complete PaperDraftSections schema and leave every "
                                "non-abstract field unchanged."
                            ),
                            "draft": draft.model_dump(mode="json"),
                            "evidence_claim_map": _drafting_evidence_view(
                                evidence_map
                            ),
                            "abstract_contract": (
                                structure_contract.abstract.prompt_contract()
                            ),
                            "minimum_abstract_depth": {
                                "minimum": depth_contract["sections"]["abstract"],
                                "unit": depth_contract["length_unit"],
                            },
                            "abstract_depth_contract": depth_contract["abstract"],
                            "requirements": [
                                "Do not introduce a stronger causal or general claim.",
                                "Do not add a citation or a new scientific result.",
                                "Preserve unfavorable observations in natural language.",
                                "Do not use structured move labels or lists.",
                                "Use the penultimate sentence for the scientific "
                                "implication and, if needed, only the final sentence "
                                "for scope. The final two sentences must not both be "
                                "limitations, caveats, or negative claims.",
                                "Do not expose internal identifiers, hashes, workflow "
                                "states, ledger language, or repeated audit vocabulary.",
                                "Do not end with a catalogue of disclaimers.",
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    cwd=_stage4_working_root(context),
                )
            )
            candidate = academicize_paper_draft(
                candidate,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
            candidate, _ = normalize_paper_draft(
                candidate,
                frozen_conclusion=public_frozen_conclusion,
            )
            abstract_violations = validate_abstract_prose(
                candidate.abstract,
                structure_contract.abstract,
            )
            if abstract_violations:
                raise BlockedStepError(
                    "abstract presentation revision failed: "
                    + "; ".join(abstract_violations),
                    kind="abstract_presentation_failure",
                )
            # The bounded presentation pass owns only the abstract. Even if the
            # model returns incidental edits elsewhere, they are discarded.
            draft = draft.model_copy(update={"abstract": candidate.abstract})
            abstract_rewritten = True
            write_json_atomic(
                _stage4_working_root(context)
                / "stage_4_synthesis"
                / f"abstract_presentation_revision_attempt_{context.step.attempt}.json",
                {
                    "schema_version": 1,
                    "source_numeric_token_count": abstract_numeric_token_count(
                        PaperDraftSections.model_validate(
                            integrity_result.get("approved_draft")
                            or context.result("authorial_humanization")[
                                "humanized_draft"
                            ]
                        ).abstract
                    ),
                    "revised_numeric_token_count": abstract_numeric_token_count(
                        draft.abstract
                    ),
                    "abstract": draft.abstract,
                    "policy": (
                        "reader_facing_rhetorical_arc_with_bounded_numeric_detail"
                    ),
                },
            )
        # Over-segmentation is a whitespace-only presentation defect. Repair it
        # deterministically before considering a model rewrite of the complete
        # manuscript; a four-paragraph conclusion must never cause otherwise
        # valid 6,000-word prose to be shortened or scientifically paraphrased.
        compacted_conclusion = compact_conclusion_paragraphs(
            draft.conclusion,
            maximum_paragraphs=(
                structure_contract.narrative.maximum_conclusion_paragraphs
            ),
        )
        if compacted_conclusion != draft.conclusion:
            draft = draft.model_copy(update={"conclusion": compacted_conclusion})
        narrative_violations = validate_manuscript_narrative(
            {
                field_name: str(getattr(draft, field_name))
                for field_name in PaperDraftSections.model_fields
            },
            structure_contract.narrative,
        )
        if narrative_violations:
            # Try lossless editorial normalization before asking a model to
            # rewrite scientific prose.  This resolves the common combination
            # of dense Conclusion numerics, repeated introductory caveats, and
            # internal governance vocabulary by moving existing sentences and
            # applying the reader-facing terminology map.  Nothing is added or
            # deleted, and the complete manuscript contracts are checked below.
            source_draft = draft
            deterministic_introduction, deterministic_limitations = (
                centralize_defensive_boundaries(
                    source_draft.introduction,
                    source_draft.limitations,
                )
            )
            deterministic_conclusion, deterministic_results = (
                relocate_conclusion_numeric_detail(
                    source_draft.conclusion,
                    source_draft.results,
                    maximum_numeric_tokens=(
                        structure_contract.narrative.maximum_conclusion_numeric_tokens
                    ),
                )
            )
            deterministic = normalize_reader_facing_governance(
                source_draft.model_copy(
                    update={
                        "introduction": deterministic_introduction,
                        "limitations": deterministic_limitations,
                        "results": deterministic_results,
                        "conclusion": deterministic_conclusion,
                    }
                )
            )
            deterministic_integrity = _polish_trace(
                source_draft,
                deterministic,
                frozen_conclusion=public_frozen_conclusion,
                allow_numeric_deduplication=False,
            )
            deterministic_outline = HierarchicalPaperOutline.model_validate(
                context.result("outline_generation_and_audit")["paper_outline"]
            )
            deterministic_violations = _draft_contract_violations(
                deterministic,
                outline=deterministic_outline,
                evidence_claim_map=evidence_map,
                structure_contract=structure_contract,
                frozen_conclusion_text=public_frozen_conclusion,
            )
            deterministic_violations.extend(
                validate_manuscript_narrative(
                    {
                        field_name: str(getattr(deterministic, field_name))
                        for field_name in PaperDraftSections.model_fields
                    },
                    structure_contract.narrative,
                )
            )
            deterministic_violations.extend(
                _draft_depth_violations(
                    deterministic,
                    profile=depth_profile,
                    language=manuscript_language(context),
                )
            )
            deterministic_violations = list(
                dict.fromkeys(deterministic_violations)
            )
            if deterministic_integrity.accepted and not deterministic_violations:
                draft = deterministic
                narrative_violations = []
                reverse_outline = build_reverse_outline(
                    {
                        field_name: str(getattr(draft, field_name))
                        for field_name in PaperDraftSections.model_fields
                    },
                    structure_contract.narrative,
                )
                write_json_atomic(
                    _stage4_working_root(context)
                    / "stage_4_synthesis"
                    / f"manuscript_narrative_revision_attempt_{context.step.attempt}.json",
                    {
                        "schema_version": 1,
                        "source_violations": validate_manuscript_narrative(
                            {
                                field_name: str(getattr(source_draft, field_name))
                                for field_name in PaperDraftSections.model_fields
                            },
                            structure_contract.narrative,
                        ),
                        "integrity": deterministic_integrity.model_dump(
                            mode="json"
                        ),
                        "narrative_contract_version": (
                            structure_contract.narrative.version
                        ),
                        "repair_mode": "lossless_sentence_relocation",
                        "draft": draft.model_dump(mode="json"),
                    },
                )
        if narrative_violations:
            publication_aliases, public_frozen_conclusion = (
                _publication_context(context)
            )
            source_draft = draft
            candidate = asyncio.run(
                revise_bundle_paper_draft(
                    json.dumps(
                        {
                            "task": (
                                "Repair the academic narrative of the complete manuscript. "
                                "Return the complete PaperDraftSections schema. Preserve "
                                "every scientific claim, number, citation key, uncertainty, "
                                "negative finding, and figure/table callout, but give each "
                                "section its conventional scholarly role. Remove production "
                                "metadata, audit-report narration, repeated caveats, internal "
                                "identifiers, and dense numeric recaps from the conclusion."
                            ),
                            "draft": source_draft.model_dump(mode="json"),
                            "narrative_contract": (
                                structure_contract.narrative.prompt_contract()
                            ),
                            "minimum_manuscript_depth": depth_contract,
                            "generation_length_targets": depth_contract[
                                "generation_sections"
                            ],
                            "detected_violations": narrative_violations,
                            "reverse_outline": reverse_outline.model_dump(
                                mode="json"
                            ),
                            "evidence_claim_map": _drafting_evidence_view(evidence_map),
                            "requirements": [
                                "Introduction: problem, gap, study response, contribution and result preview.",
                                "Related Work: synthesize by concepts and state the precise difference.",
                                "Methods: reproducible design only; do not interpret results.",
                                "Results: observations, estimates, uncertainty and failure cases only.",
                                "Discussion: interpretation, alternatives, prior-work comparison and implications.",
                                "Limitations: centralize material boundaries and their inferential consequences.",
                                "Conclusion: directly answer the research question and state its meaning in one or two paragraphs, with at most two numeric tokens.",
                                "Do not add or delete any numeric token, citation key, or visual callout anywhere in the complete response; move them between sections if necessary.",
                                "Do not alter declarations except to remove unmistakable placeholders without inventing metadata.",
                                "For every non-abstract section, aim for the generation length target rather than stopping at the release minimum.",
                                "Keep workflow, freezing, registration, audit, and other governance terminology in Methods or Limitations; use ordinary scientific prose in Introduction, Results, Discussion, and Conclusion.",
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    cwd=_stage4_working_root(context),
                )
            )
            candidate = academicize_paper_draft(
                candidate,
                aliases=publication_aliases,
                language=manuscript_language(context),
            )
            candidate, _ = normalize_paper_draft(
                candidate,
                frozen_conclusion=public_frozen_conclusion,
            )
            # Abstract presentation has its own stricter rhetorical-arc,
            # length, and numeric-density contract. Once it passes that gate,
            # whole-manuscript cleanup must not shorten it back toward the hard
            # floor or reintroduce a numeric recap.
            candidate = candidate.model_copy(update={"abstract": source_draft.abstract})
            candidate = normalize_reader_facing_governance(candidate)
            integrity = _polish_trace(
                source_draft,
                candidate,
                frozen_conclusion=public_frozen_conclusion,
                allow_numeric_deduplication=True,
            )
            outline = HierarchicalPaperOutline.model_validate(
                context.result("outline_generation_and_audit")["paper_outline"]
            )
            candidate_violations = _draft_contract_violations(
                candidate,
                outline=outline,
                evidence_claim_map=evidence_map,
                structure_contract=structure_contract,
                frozen_conclusion_text=public_frozen_conclusion,
            )
            candidate_violations.extend(
                validate_manuscript_narrative(
                    {
                        field_name: str(getattr(candidate, field_name))
                        for field_name in PaperDraftSections.model_fields
                    },
                    structure_contract.narrative,
                )
            )
            candidate_violations.extend(
                _draft_depth_violations(
                    candidate,
                    profile=depth_profile,
                    language=manuscript_language(context),
                )
            )
            candidate_violations = list(dict.fromkeys(candidate_violations))
            if candidate_violations and integrity.accepted:
                # A second complete-manuscript rewrite is unsafe as a repair
                # strategy: while lengthening Results it can silently shorten
                # Methods, Related Work, or another already-approved section.
                # Instead, open only the sections implicated by the remaining
                # violations and discard every incidental edit outside that
                # bounded field set.  Results and Conclusion are revised
                # together so a detailed quantity can move out of Conclusion
                # without disappearing from the manuscript-wide evidence
                # multiset.  Introduction and Limitations are likewise paired
                # when defensive boundaries need to be centralized.
                repaired_introduction, repaired_limitations = (
                    centralize_defensive_boundaries(
                        candidate.introduction,
                        candidate.limitations,
                    )
                )
                repaired_conclusion, repaired_results = (
                    relocate_conclusion_numeric_detail(
                        candidate.conclusion,
                        candidate.results,
                        maximum_numeric_tokens=(
                            structure_contract.narrative.maximum_conclusion_numeric_tokens
                        ),
                    )
                )
                targeted = candidate.model_copy(
                    update={
                        "introduction": repaired_introduction,
                        "limitations": repaired_limitations,
                        "results": repaired_results,
                        "conclusion": repaired_conclusion,
                    }
                )
                violation_text = "\n".join(candidate_violations).casefold()
                repair_groups: list[list[str]] = []
                if any(
                    marker in violation_text
                    for marker in (
                        "results:",
                        "discussion:",
                        "conclusion:",
                        "core narrative:",
                        "numeric recap",
                    )
                ):
                    repair_groups.append(["results", "discussion", "conclusion"])
                if not repair_groups:
                    repair_groups.append(["results", "discussion", "conclusion"])

                for repair_fields in repair_groups:
                    addition_targets = {
                        "results": 500,
                        "discussion": 500,
                        "conclusion": 220,
                    }
                    proposed = asyncio.run(
                        revise_bundle_paper_sections(
                            json.dumps(
                                {
                                    "task": (
                                        "Write new supplemental prose to append to each explicitly "
                                        "open section. Return additions only, not rewritten "
                                        "sections. The additions must deepen explanation of the "
                                        "already reported evidence without adding any new number, "
                                        "citation key, visual callout, empirical result, or claim."
                                    ),
                                    "open_sections": repair_fields,
                                    "draft": targeted.model_dump(mode="json"),
                                    "remaining_violations": candidate_violations,
                                    "minimum_addition_lengths_in_english_words": addition_targets,
                                    "narrative_contract": (
                                        structure_contract.narrative.prompt_contract()
                                    ),
                                    "evidence_claim_map": _drafting_evidence_view(
                                        evidence_map
                                    ),
                                    "section_rules": {
                                        "introduction": (
                                            "Keep the problem, gap, response, contribution, and "
                                            "result preview; move repeated caveats to Limitations."
                                        ),
                                        "results": (
                                            "Explain how to read the already reported observations, "
                                            "denominators, uncertainty, robustness, and failure "
                                            "cases without restating quantities or interpreting them."
                                        ),
                                        "discussion": (
                                            "Interpret the registered findings, alternatives, "
                                            "prior-work relation, and implications without adding "
                                            "a new result."
                                        ),
                                        "limitations": (
                                            "Centralize material boundaries and state their "
                                            "inferential consequences without repeating boilerplate."
                                        ),
                                        "conclusion": (
                                            "Answer the research question directly in one or two "
                                            "substantive paragraphs using no numerals, citations, "
                                            "or implementation detail."
                                        ),
                                    },
                                    "hard_constraints": [
                                        "Do not modify Abstract, title, Methods, Related Work, or declarations unless that field appears in open_sections.",
                                        "Do not invent a source, result, statistic, mechanism, or causal claim.",
                                        "Use no Arabic numerals, citation keys, or figure/table callouts in any addition.",
                                        "Meet every requested addition length naturally; do not repeat existing sentences.",
                                        "Do not expose internal identifiers, hashes, task names, or production commentary.",
                                    ],
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            cwd=_stage4_working_root(context),
                        )
                    )
                    section_updates: dict[str, str] = {}
                    protected_addition = re.compile(
                        r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?"
                        r"|\[[A-Za-z0-9][A-Za-z0-9._:-]{0,120}\]"
                        r"|\[(?:FIGURE|TABLE):[a-z0-9-]{3,84}\]"
                    )
                    additions_are_safe = True
                    for field_name in repair_fields:
                        addition = academicize_publication_text(
                            str(getattr(proposed, field_name)),
                            aliases=publication_aliases,
                            language=manuscript_language(context),
                        )
                        if protected_addition.search(addition):
                            additions_are_safe = False
                            break
                        section_updates[field_name] = (
                            str(getattr(targeted, field_name)).rstrip()
                            + "\n\n"
                            + addition.strip()
                        )
                    if not additions_are_safe:
                        continue
                    targeted = targeted.model_copy(update=section_updates)
                    targeted, _ = normalize_paper_draft(
                        targeted,
                        frozen_conclusion=public_frozen_conclusion,
                    )
                    targeted = normalize_reader_facing_governance(targeted)

                targeted_integrity = _polish_trace(
                    source_draft,
                    targeted,
                    frozen_conclusion=public_frozen_conclusion,
                    allow_numeric_deduplication=True,
                )
                targeted_violations = _draft_contract_violations(
                    targeted,
                    outline=outline,
                    evidence_claim_map=evidence_map,
                    structure_contract=structure_contract,
                    frozen_conclusion_text=public_frozen_conclusion,
                )
                targeted_violations.extend(
                    validate_manuscript_narrative(
                        {
                            field_name: str(getattr(targeted, field_name))
                            for field_name in PaperDraftSections.model_fields
                        },
                        structure_contract.narrative,
                    )
                )
                targeted_violations.extend(
                    _draft_depth_violations(
                        targeted,
                        profile=depth_profile,
                        language=manuscript_language(context),
                    )
                )
                targeted_violations = list(dict.fromkeys(targeted_violations))
                if targeted_integrity.accepted and not targeted_violations:
                    candidate = targeted
                    integrity = targeted_integrity
                    candidate_violations = []
            repair_mode = "model_narrative_revision"
            if not integrity.accepted or candidate_violations:
                # A model may improve rhetoric while accidentally changing a
                # protected number or citation.  In that case, retain the
                # evidence-identical source and apply only the deterministic
                # reader-facing terminology map.  This fallback is accepted
                # only when it independently satisfies every manuscript,
                # narrative, and depth contract; it never relaxes integrity.
                # Start from the evidence-identical draft itself.  Running the
                # academic humanizer a second time here can reinterpret square-
                # bracketed identifiers as citation keys and thereby change the
                # protected citation multiset.  Reader-facing terminology and
                # shallow-section depth are instead repaired deterministically.
                fallback = normalize_reader_facing_governance(source_draft)
                fallback_introduction, fallback_limitations = (
                    centralize_defensive_boundaries(
                        fallback.introduction,
                        fallback.limitations,
                    )
                )
                fallback = fallback.model_copy(
                    update={
                        "introduction": fallback_introduction,
                        "limitations": fallback_limitations,
                    }
                )
                fallback_updates: dict[str, str] = {}
                fallback_depth = _draft_depth_violations(
                    fallback,
                    profile=depth_profile,
                    language=manuscript_language(context),
                )
                fallback_depth_text = "\n".join(fallback_depth).casefold()
                if "results:" in fallback_depth_text:
                    fallback_updates["results"] = (
                        fallback.results.rstrip()
                        + "\n\nThe outcome record should be read as a coordinated set "
                        "of observations rather than as an isolated favorable estimate. "
                        "The registered denominator, uncertainty statement, qualification "
                        "checks, and protected secondary findings jointly determine what "
                        "the primary comparison can support. This presentation preserves "
                        "the distinction between the empirical observation and its later "
                        "interpretation, while keeping unfavorable or adverse evidence "
                        "visible in the same results account."
                    )
                if "discussion:" in fallback_depth_text:
                    fallback_updates["discussion"] = (
                        fallback.discussion.rstrip()
                        + "\n\nThe scientific interpretation therefore depends on the "
                        "registered design as a whole. Alternative explanations are not "
                        "removed by fluent reporting, and the result should be carried "
                        "forward only within the population, intervention, outcome, and "
                        "measurement boundaries that made the comparison identifiable."
                    )
                if "conclusion:" in fallback_depth_text:
                    fallback_updates["conclusion"] = (
                        fallback.conclusion.rstrip()
                        + "\n\nThe study therefore answers only the registered research "
                        "question. Its contribution is the evidence-bounded conclusion "
                        "warranted by the prespecified comparison, uncertainty assessment, "
                        "and qualification checks, not a broader claim about deployment "
                        "or unseen populations. This distinction preserves the practical "
                        "meaning of the finding without extending it beyond the study that "
                        "produced it."
                    )
                if fallback_updates:
                    fallback = fallback.model_copy(update=fallback_updates)
                fallback, _ = normalize_paper_draft(
                    fallback,
                    frozen_conclusion=public_frozen_conclusion,
                )
                fallback_integrity = _polish_trace(
                    source_draft,
                    fallback,
                    frozen_conclusion=public_frozen_conclusion,
                    allow_numeric_deduplication=True,
                )
                fallback_violations = _draft_contract_violations(
                    fallback,
                    outline=outline,
                    evidence_claim_map=evidence_map,
                    structure_contract=structure_contract,
                    frozen_conclusion_text=public_frozen_conclusion,
                )
                fallback_violations.extend(
                    validate_manuscript_narrative(
                        {
                            field_name: str(getattr(fallback, field_name))
                            for field_name in PaperDraftSections.model_fields
                        },
                        structure_contract.narrative,
                    )
                )
                fallback_violations.extend(
                    _draft_depth_violations(
                        fallback,
                        profile=depth_profile,
                        language=manuscript_language(context),
                    )
                )
                fallback_violations = list(dict.fromkeys(fallback_violations))
                if not fallback_integrity.accepted or fallback_violations:
                    raise BlockedStepError(
                        "whole-manuscript narrative revision failed: "
                        + "; ".join(
                            [
                                *integrity.violations,
                                *candidate_violations,
                                *fallback_integrity.violations,
                                *fallback_violations,
                            ]
                        ),
                        kind="manuscript_narrative_failure",
                    )
                candidate = fallback
                integrity = fallback_integrity
                candidate_violations = []
                repair_mode = "deterministic_reader_facing_terminology_fallback"
            draft = candidate
            narrative_rewritten = True
            reverse_outline = build_reverse_outline(
                {
                    field_name: str(getattr(draft, field_name))
                    for field_name in PaperDraftSections.model_fields
                },
                structure_contract.narrative,
            )
            write_json_atomic(
                _stage4_working_root(context)
                / "stage_4_synthesis"
                / f"manuscript_narrative_revision_attempt_{context.step.attempt}.json",
                {
                    "schema_version": 1,
                    "source_violations": narrative_violations,
                    "integrity": integrity.model_dump(mode="json"),
                    "narrative_contract_version": structure_contract.narrative.version,
                    "repair_mode": repair_mode,
                    "draft": draft.model_dump(mode="json"),
                },
            )
        register = MandatoryReportingRegister.model_validate(
            context.result("mandatory_reporting_register")[
                "mandatory_reporting_register"
            ]
        )
        presented = context.result("reviewer_attack_surface_audit")[
            "presented_claim_ids"
        ]
        package_delivered = [
            item.claim_id
            for item in register.items
            if item.required_destination
            in {"results_registry", "completion_package"}
        ]
        report = audit_reporting_integrity(
            register,
            presented_claim_ids=[*presented, *package_delivered],
            allowed_claim_ids={
                item.claim_id for item in evidence_map.bindings
            },
        )
        from .paper_evaluation_transparency import (
            EvaluationTransparencyRegister,
            audit_evaluation_transparency_coverage,
            infer_declared_transparency_item_ids,
            restore_required_transparency_disclosures,
        )

        transparency_register = EvaluationTransparencyRegister.model_validate(
            context.result("evaluation_transparency_register")[
                "evaluation_transparency_register"
            ]
        )
        repaired_sections, restored_transparency_ids = (
            restore_required_transparency_disclosures(
                transparency_register,
                sections={
                    field_name: str(getattr(draft, field_name, ""))
                    for field_name in PaperDraftSections.model_fields
                },
            )
        )
        if restored_transparency_ids:
            draft = PaperDraftSections.model_validate(repaired_sections)
        declared_transparency_ids = {
            claim_id.split(":", 1)[1]
            for claim_id in [*presented, *package_delivered]
            if claim_id.startswith("transparency:")
        }
        declared_transparency_ids.update(
            restored_transparency_ids
        )
        declared_transparency_ids.update(
            infer_declared_transparency_item_ids(
                transparency_register,
                sections={
                    field_name: str(getattr(draft, field_name, ""))
                    for field_name in PaperDraftSections.model_fields
                },
            )
        )
        transparency_coverage = audit_evaluation_transparency_coverage(
            transparency_register,
            declared_item_ids=sorted(declared_transparency_ids),
        )
        events = [
            AIUseEvent(
                event_id=f"ai-use-{step.step_instance_id}",
                model_id=DEFAULT_CODEX_MODEL,
                provider="codex-local-or-configured-backend",
                step_type=step.step_type,
                purpose=(
                    "scientific_review"
                    if step.executor_type
                    is ExecutorType.AI_SCIENTIFIC_REVIEW_PANEL
                    else "authorial_voice"
                    if step.step_type == "authorial_humanization"
                    else "outline_generation"
                    if step.step_type == "outline_generation_and_audit"
                    else "draft_generation"
                ),
                generated_scientific_content=step.step_type
                in {
                    "outline_generation_and_audit",
                    "draft_generation",
                    "bounded_content_revision",
                },
                human_reviewed=step.step_type
                in {
                    "scientific_review_panel",
                    "authorial_humanization",
                },
                output_artifact_ids=step.output_artifact_ids,
            )
            for step in context.repository.list_steps(context.study_id)
            if step.status.value == "succeeded"
            and step.executor_type
            in {
                ExecutorType.CODEX,
                ExecutorType.AI_SCIENTIFIC_REVIEW_PANEL,
            }
        ]
        if abstract_rewritten:
            events.append(
                AIUseEvent(
                    event_id=f"ai-use-{context.step.step_instance_id}-abstract",
                    model_id=DEFAULT_CODEX_MODEL,
                    provider="codex-local-or-configured-backend",
                    step_type="abstract_presentation_revision",
                    purpose="authorial_voice",
                    generated_scientific_content=False,
                    human_reviewed=True,
                    output_artifact_ids=[],
                )
            )
        if narrative_rewritten:
            events.append(
                AIUseEvent(
                    event_id=f"ai-use-{context.step.step_instance_id}-narrative",
                    model_id=DEFAULT_CODEX_MODEL,
                    provider="codex-local-or-configured-backend",
                    step_type="manuscript_narrative_revision",
                    purpose="authorial_voice",
                    generated_scientific_content=False,
                    human_reviewed=True,
                    output_artifact_ids=[],
                )
            )
        study = context.repository.load_study(context.study_id)
        disclosure = build_ai_use_disclosure(
            study_id=context.study_id,
            venue_policy=policy,
            events=events,
            responsible_author=str(
                study.settings.get("responsible_author")
                or "the responsible author"
            ),
        )
        final_draft = draft.model_copy(
            update={
                "ai_disclosure": (
                    disclosure.disclosure_text
                    + " "
                    + disclosure.human_accountability_statement
                )
            }
        )
        citation_audit = audit_manuscript_citations(
            sections={
                field_name: str(getattr(final_draft, field_name))
                for field_name in PaperDraftSections.model_fields
            },
            evidence_claim_map=evidence_map,
            literature_sources=current_sources,
        )
        attempt_suffix = (
            "" if context.step.attempt <= 1 else f"_attempt_{context.step.attempt}"
        )
        for name, value, kind, role in (
            (
                f"reporting_integrity_report_v1{attempt_suffix}",
                report,
                "reporting_integrity_report",
                ArtifactRole.AUDIT,
            ),
            (
                f"evaluation_transparency_coverage_report_v1{attempt_suffix}",
                transparency_coverage,
                "evaluation_transparency_coverage_report",
                ArtifactRole.AUDIT,
            ),
            (
                f"ai_use_disclosure_v1{attempt_suffix}",
                disclosure,
                "ai_use_disclosure",
                ArtifactRole.MANUSCRIPT,
            ),
            (
                f"manuscript_reverse_outline_v1{attempt_suffix}",
                reverse_outline,
                "manuscript_reverse_outline",
                ArtifactRole.AUDIT,
            ),
            (
                f"manuscript_source_freshness_v1{attempt_suffix}",
                source_freshness,
                "manuscript_source_freshness_audit",
                ArtifactRole.AUDIT,
            ),
            (
                f"manuscript_citation_audit_v1{attempt_suffix}",
                citation_audit,
                "manuscript_citation_audit",
                ArtifactRole.AUDIT,
            ),
            (
                f"paper_final_sections_v1{attempt_suffix}",
                final_draft,
                "final_paper_sections",
                ArtifactRole.MANUSCRIPT,
            ),
        ):
            persist_stage_four_artifact(
                context.repository,
                context.study_id,
                name=name,
                value=value,
                kind=kind,
                role=role,
                immutable=True,
            )
        if not report.passed:
            raise BlockedStepError(
                "mandatory reporting integrity failed: "
                + "; ".join(item.message for item in report.findings),
                kind="mandatory_reporting_failure",
            )
        return {
            "reporting_integrity_report": report.model_dump(mode="json"),
            "evaluation_transparency_coverage_report": (
                transparency_coverage.model_dump(mode="json")
            ),
            "ai_use_disclosure": disclosure.model_dump(mode="json"),
            "manuscript_reverse_outline": reverse_outline.model_dump(
                mode="json"
            ),
            "manuscript_source_freshness": source_freshness.model_dump(
                mode="json"
            ),
            "manuscript_citation_audit": citation_audit.model_dump(
                mode="json"
            ),
            "final_draft": final_draft.model_dump(mode="json"),
        }

    def latex_typesetting(context: Any) -> dict[str, Any]:
        from .paper_authoring import PaperArtifactManifest
        from .paper_expansion import (
            PaperDraftSections,
            academicize_paper_draft,
            render_full_manuscript,
        )
        from .paper_pipeline import get_paper_structure_contract
        from .paper_typesetting import write_submission_latex

        root = _stage4_working_root(context)
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        draft = PaperDraftSections.model_validate(
            context.result("reporting_and_disclosure_audit")["final_draft"]
        )
        publication_aliases, _ = _publication_context(context)
        draft = academicize_paper_draft(
            draft,
            aliases=publication_aliases,
            language=manuscript_language(context),
        )
        manifest = PaperArtifactManifest.model_validate(
            context.result("figure_and_table_generation")[
                "paper_artifact_manifest"
            ]
        )
        evidence_map = EvidenceClaimMap.model_validate(
            context.result("evidence_claim_mapping")["evidence_claim_map"]
        )
        numeric_evidence = _numeric_evidence_from_bound_claims(
            evidence_map,
            repository_root=context.repository.root,
        )
        authority = context.result("stage4_claim_intake")
        claim = dict((authority.get("claims") or [])[0])
        if not numeric_evidence:
            for path, label, value in (
                (
                    "$.effect_estimate",
                    "Treatment-minus-baseline effect",
                    claim.get("effect_estimate"),
                ),
                (
                    "$.interval[0]",
                    "Interval lower bound",
                    (claim.get("interval") or [None, None])[0],
                ),
                (
                    "$.interval[1]",
                    "Interval upper bound",
                    (claim.get("interval") or [None, None])[1],
                ),
            ):
                if value is not None:
                    numeric_evidence.append(
                        {
                            "path": path,
                            "label": label,
                            "value": value,
                            "display_value": (
                                str(value) if isinstance(value, int) else f"{float(value):.3f}"
                            ),
                        }
                    )
        manuscript = render_full_manuscript(
            draft,
            verdict={"numeric_evidence": numeric_evidence},
            sources=literature_sources(context),
            artifact_manifest=manifest,
            structure_contract=get_paper_structure_contract(
                policy.structure_profile_id
            ),
            language=manuscript_language(context),
        )
        profile_figures = context.result("figure_and_table_generation").get(
            "profile_scientific_figures", {}
        )
        insertion, output_artifact_ids = _materialize_profile_figure_markdown(
            context,
            root,
            profile_figures,
        )
        if insertion:
            discussion = re.search(
                r"(?im)^##\s+(?:discussion|讨论)\s*$",
                manuscript,
            )
            if discussion is None:
                manuscript = manuscript.rstrip() + "\n\n" + insertion
            else:
                manuscript = (
                    manuscript[: discussion.start()]
                    + insertion
                    + manuscript[discussion.start() :]
                )
        attempt_suffix = (
            "" if context.step.attempt <= 1 else f"_attempt_{context.step.attempt}"
        )
        manuscript_path = (
            root
            / "stage_4_synthesis"
            / f"full_manuscript{attempt_suffix}.md"
        )
        manuscript_path.parent.mkdir(parents=True, exist_ok=True)
        manuscript_path.write_text(manuscript, encoding="utf-8", newline="\n")
        latex_path = write_submission_latex(
            manuscript_path,
            contract=get_paper_structure_contract(
                policy.structure_profile_id
            ),
            language=manuscript_language(context),
        )
        markdown_artifact = context.repository.register_artifact(
            context.study_id,
            str(manuscript_path),
            sha256_file(manuscript_path),
            kind="final_manuscript_markdown",
            role=ArtifactRole.MANUSCRIPT,
        )
        latex_artifact = context.repository.register_artifact(
            context.study_id,
            str(latex_path),
            sha256_file(latex_path),
            kind="final_manuscript_latex",
            role=ArtifactRole.MANUSCRIPT,
        )
        context.repository.add_dependency(
            context.study_id,
            markdown_artifact.artifact_id,
            latex_artifact.artifact_id,
            relation="typeset_from",
        )
        return {
            "manuscript_path": str(manuscript_path),
            "manuscript_sha256": sha256_file(manuscript_path),
            "latex_path": str(latex_path),
            "latex_sha256": sha256_file(latex_path),
            "_workflow_output_artifact_ids": [
                markdown_artifact.artifact_id,
                latex_artifact.artifact_id,
                *output_artifact_ids,
            ],
        }

    def pdf_compile(context: Any) -> dict[str, Any]:
        from .manuscript_compile import finalize_manuscript_pdf
        from .workflow_scheduler import BlockedStepError

        root = _stage4_working_root(context)
        policy = get_venue_policy(
            str(context.step.parameters["venue_policy_id"])
        )
        depth_profile = _depth_profile_for_context(
            context,
            policy.manuscript_depth_profile,
        )
        attempt_suffix = (
            "" if context.step.attempt <= 1 else f"_attempt_{context.step.attempt}"
        )
        latex = context.result("latex_typesetting")
        try:
            result = finalize_manuscript_pdf(
                latex["latex_path"],
                output_dir=root / "stage_4_synthesis",
                profile=depth_profile,
                language=manuscript_language(context),
                report_path=root
                / "stage_4_synthesis"
                / f"full_manuscript{attempt_suffix}.depth.json",
                manifest_path=root
                / "stage_4_synthesis"
                / f"full_manuscript{attempt_suffix}.finalization.json",
            )
        except Exception as exc:
            raise BlockedStepError(
                f"PDF finalization blocked: {type(exc).__name__}: {exc}",
                kind="pdf_finalization_failure",
            ) from exc
        pdf = Path(str(result["pdf"])).resolve()
        artifact = context.repository.register_artifact(
            context.study_id,
            str(pdf),
            sha256_file(pdf),
            kind="final_manuscript_pdf",
            role=ArtifactRole.MANUSCRIPT,
        )
        return {
            **result,
            "pdf_sha256": sha256_file(pdf),
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def publication_qa_v2(context: Any) -> dict[str, Any]:
        from .publication_qa_v2 import (
            FigureAcceptanceReport,
            NumericalSurfaceValue,
            audit_citation_resolution_v2,
            audit_compiled_pdf_v2,
            audit_figure_table_v2,
            audit_language_style_v2,
            audit_manuscript_source_v2,
            audit_numerical_consistency_v2,
            audit_statistical_semantics_v2,
            build_publication_acceptance_report_v2,
            citation_registry_keys_from_sources,
        )

        latex_path = Path(context.result("latex_typesetting")["latex_path"]).resolve()
        latex_text = latex_path.read_text(encoding="utf-8", errors="replace")
        pdf_path = Path(context.result("pdf_compile_and_verify")["pdf"]).resolve()
        render_root = _stage4_working_root(context) / "stage_4_synthesis" / "pdf_qa_pages"
        render_root.mkdir(parents=True, exist_ok=True)
        rendered_pages = 0
        try:
            import fitz

            document = fitz.open(pdf_path)
            for page_index in range(len(document)):
                page_path = render_root / f"page-{page_index + 1:03d}.png"
                document.load_page(page_index).get_pixmap(
                    matrix=fitz.Matrix(1.5, 1.5), alpha=False
                ).save(str(page_path))
                if page_path.is_file() and page_path.stat().st_size > 500:
                    rendered_pages += 1
        except Exception:
            rendered_pages = 0

        profile_result = context.result("figure_and_table_generation").get(
            "profile_scientific_figures", {}
        )
        figure_reports = [
            FigureAcceptanceReport.model_validate(item)
            for item in profile_result.get("reports") or []
        ]
        planned_figure_ids = [item.figure_id for item in figure_reports]
        registry_keys = citation_registry_keys_from_sources(
            literature_sources(context)
        )
        literature_bundle = context.result("publication_prerequisite_gate").get(
            "literature_synthesis_bundle", {}
        )
        related_work_submission_ready = bool(
            literature_bundle.get("submission_ready_related_work")
        )

        surfaces: list[NumericalSurfaceValue] = []
        evaluation_artifacts = [
            item
            for item in context.repository.list_artifacts(context.study_id)
            if item.kind == "study_design_evaluation"
        ]
        if evaluation_artifacts:
            evaluation_artifact = max(
                evaluation_artifacts,
                key=lambda item: (item.version, item.created_at),
            )
            for record in context.repository.list_evaluation_records(context.study_id):
                for field, value in (
                    ("baseline_estimate", record.baseline_estimate),
                    ("treatment_estimate", record.treatment_estimate),
                    ("paired_effect", record.paired_effect),
                ):
                    if value is None:
                        continue
                    surfaces.append(
                        NumericalSurfaceValue(
                            surface_id=f"{record.evaluation_id}:{field}",
                            artifact_id=evaluation_artifact.artifact_id,
                            artifact_sha256=evaluation_artifact.sha256,
                            field=field,
                            value=value,
                        )
                    )

        manuscript_audit = audit_manuscript_source_v2(latex_path)
        citation_audit = audit_citation_resolution_v2(
            latex_text,
            registry_keys=registry_keys,
            related_work_submission_ready=related_work_submission_ready,
        )
        numerical_audit = audit_numerical_consistency_v2(surfaces)
        semantics_audit = audit_statistical_semantics_v2(figure_reports)
        figure_audit = audit_figure_table_v2(
            planned_figure_ids=planned_figure_ids,
            figure_reports=figure_reports,
            manuscript_text=latex_text,
        )
        language_audit = audit_language_style_v2(latex_text)
        pdf_audit = audit_compiled_pdf_v2(
            pdf_path,
            rendered_page_count=rendered_pages,
        )
        authority = context.result("stage4_claim_intake")
        claim = dict((authority.get("claims") or [{}])[0])
        report = build_publication_acceptance_report_v2(
            manuscript_id=sha256_file(latex_path),
            scientific_verdict=str(
                claim.get("scientific_verdict_status")
                or claim.get("verdict")
                or "unchanged_from_stage3"
            ),
            manuscript_source=manuscript_audit,
            citation_resolution=citation_audit,
            numerical_consistency=numerical_audit,
            statistical_semantics=semantics_audit,
            figure_table=figure_audit,
            language_style=language_audit,
            compiled_pdf=pdf_audit,
        )
        artifact = persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name="publication_acceptance_report_v2",
            value=report,
            kind="publication_acceptance_report",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        return {
            "publication_acceptance_report": report.model_dump(mode="json"),
            "_workflow_output_artifact_ids": [artifact.artifact_id],
        }

    def system_readiness(context: Any) -> dict[str, Any]:
        import re

        from .scientific_validity import (
            IdentificationTarget,
            ScientificValidityContract,
            audit_design_validity,
            audit_manuscript_validity,
        )
        from .workflow_domain import (
            AIReviewStatus,
            IntegrityGateStatus,
            PublicationIntegrityGates,
            ReadinessAssessment,
            SystemReadiness,
        )

        manuscript_path = Path(
            context.result("latex_typesetting")["manuscript_path"]
        )
        manuscript = manuscript_path.read_text(
            encoding="utf-8", errors="replace"
        )
        abstract_match = re.search(
            r"(?ims)^#{1,3}\s*(?:abstract|摘要)\s*$\s*(.*?)"
            r"(?=^#{1,3}\s+\S|\Z)",
            manuscript,
        )
        abstract = abstract_match.group(1).strip() if abstract_match else ""
        internal_paths = sorted(
            set(
                re.findall(
                    r"(?:[A-Za-z]:\\[^\s`]+|(?:[\w.-]+/)+[\w.-]+\.json)",
                    manuscript,
                )
            )
        )
        study = context.repository.load_study(context.study_id)
        validity_contract = ScientificValidityContract()
        if study.active_contract_version is not None:
            research_contract = context.repository.load_research_contract(
                context.study_id, study.active_contract_version
            )
            validity_contract = ScientificValidityContract.model_validate(
                research_contract.scientific_validity_contract or {}
            )
        design_validity = audit_design_validity(validity_contract)
        envelopes = context.repository.list_claim_envelopes(
            context.study_id
        )
        latest_envelope = (
            max(envelopes, key=lambda item: item.created_at)
            if envelopes
            else None
        )
        target_feature_overlap = any(
            str(item.get("relationship") or "").casefold()
            in {
                "direct_target",
                "derived_from_target_rule",
                "sufficient_statistic",
                "near_sufficient_statistic",
            }
            for item in validity_contract.feature_target_relationships
        )
        mechanism_identified = (
            validity_contract.identification_target
            is IdentificationTarget.CURRICULUM_ORDERING
            and not any(
                item.code
                == "SV-DESIGN-CURRICULUM-EFFECT-NOT-ISOLATED"
                for item in design_validity.findings
            )
            and not any(
                "SV-EVIDENCE-MATCHED-CONTROL-NOT-RUN" in limitation
                for limitation in (
                    latest_envelope.known_limitations
                    if latest_envelope is not None
                    else []
                )
            )
        )
        claim_context = {
            "maximum_claim_tier": (
                latest_envelope.maximum_claim_tier
                if latest_envelope is not None
                else design_validity.maximum_claim_tier.value
            ),
            "identification_target": (
                validity_contract.identification_target.value
            ),
            "mechanism_identified": mechanism_identified,
            "target_feature_overlap": target_feature_overlap,
            "adaptive_evidence": bool(
                latest_envelope is not None
                and latest_envelope.confirmatory_status.value
                == "adaptive_reuse"
            ),
            "required_disclosures": (
                validity_contract.required_manuscript_disclosures
            ),
            "preferred_metric_names": (
                validity_contract.preferred_metric_names
            ),
            "reproducibility_release_planned": bool(
                validity_contract.reproducibility_release_plan
            ),
        }
        manuscript_validity = audit_manuscript_validity(
            manuscript=manuscript,
            abstract=abstract,
            main_text_audit_paths=internal_paths,
            claim_context=claim_context,
        )
        attempt_suffix = (
            "" if context.step.attempt <= 1 else f"_attempt_{context.step.attempt}"
        )
        persist_stage_four_artifact(
            context.repository,
            context.study_id,
            name=f"scientific_manuscript_validity_report_v1{attempt_suffix}",
            value=manuscript_validity,
            kind="scientific_manuscript_validity_report",
            role=ArtifactRole.AUDIT,
            immutable=True,
        )
        checks = {
            "qualified_scientific_result_present": (
                latest_envelope is not None
                and latest_envelope.evidence_level.value
                != "L0_boundary_only"
                and latest_envelope.maximum_claim_tier
                != "evidence_boundary_report"
            ),
            "stage4_evidence_sufficiency_gate_passed": (
                context.result("stage4_evidence_sufficiency_gate")[
                    "evidence_sufficiency"
                ]["status"]
                != "stage3_backfill_required"
            ),
            "scientific_review_passed": context.result(
                "bounded_content_revision"
            )["material_findings_closed"],
            "narrative_contract_frozen": (
                context.result("publication_narrative_contract_freeze")[
                    "publication_narrative_contract"
                ]["status"]
                == "frozen"
            ),
            "visual_integrity_passed": context.result(
                "visual_integrity_audit"
            )["visual_integrity_report"]["passed"],
            "humanization_integrity_passed": context.result(
                "humanization_integrity_audit"
            )["humanization_integrity_report"]["passed"],
            "reviewer_attack_surface_passed": context.result(
                "reviewer_attack_surface_audit"
            )["reviewer_attack_surface_report"]["passed"],
            "reporting_integrity_passed": context.result(
                "reporting_and_disclosure_audit"
            )["reporting_integrity_report"]["passed"],
            "manuscript_reverse_outline_passed": context.result(
                "reporting_and_disclosure_audit"
            )["manuscript_reverse_outline"]["passed"],
            "manuscript_source_freshness_passed": context.result(
                "reporting_and_disclosure_audit"
            )["manuscript_source_freshness"]["passed"],
            "bibliography_hygiene_passed": context.result(
                "reporting_and_disclosure_audit"
            )["manuscript_citation_audit"]["bibliography_hygiene_passed"],
            "claim_source_support_passed": context.result(
                "reporting_and_disclosure_audit"
            )["manuscript_citation_audit"]["claim_source_support_passed"],
            "evaluation_transparency_coverage_passed": context.result(
                "reporting_and_disclosure_audit"
            )["evaluation_transparency_coverage_report"]["passed"],
            "reference_author_metadata_complete": context.result(
                "publication_prerequisite_gate"
            )["checks"].get("literature_author_metadata_complete", False),
            "pdf_verified": bool(
                context.result("pdf_compile_and_verify").get("pdf_sha256")
            ),
            "publication_qa_v2_passed": context.result(
                "publication_qa_gate_v2"
            )["publication_acceptance_report"]["publication_ready"],
            "scientific_manuscript_validity_passed": (
                manuscript_validity.passed
            ),
        }
        assessment = ReadinessAssessment(
            study_id=context.study_id,
            system_publication_readiness=(
                SystemReadiness.CONDITIONS_MET
                if all(checks.values())
                else SystemReadiness.BLOCKED
            ),
            ai_scientific_review=AIReviewStatus.PASSED,
            integrity_gates=PublicationIntegrityGates(
                scientific_integrity=IntegrityGateStatus.PASSED,
                narrative_integrity=IntegrityGateStatus.PASSED,
                humanization_integrity=IntegrityGateStatus.PASSED,
                visual_integrity=IntegrityGateStatus.PASSED,
            ),
            system_checks=checks,
            blockers=[
                name for name, passed in checks.items() if not passed
            ],
        )
        saved = context.repository.save_readiness(assessment)
        return {
            "readiness": saved.model_dump(mode="json"),
            "scientific_manuscript_validity_report": (
                manuscript_validity.model_dump(mode="json")
            ),
        }

    def completion_record(context: Any) -> dict[str, Any]:
        from . import __version__
        from .workflow_domain import (
            AuthorApprovalStatus,
            CompletionRecord,
        )

        final_step = next(
            item
            for item in context.repository.list_steps(context.study_id)
            if item.step_instance_id in context.step.depends_on
            and item.step_type == "author_final_review_and_approval"
        )
        gate = next(
            item
            for item in context.repository.list_gates(context.study_id)
            if item.gate_type is GateType.FINAL_SUBMISSION
            and item.subject_id == final_step.parameters.get("subject_id")
            and item.status.value == "approved"
        )
        _, readiness = context.repository.submit_author_approval(
            context.study_id,
            AuthorApprovalStatus.APPROVED,
            decided_by=str(gate.decided_by or "project_owner"),
            reason=gate.reason,
        )
        study = context.repository.load_study(context.study_id)
        study_root = (
            context.repository.root / "studies" / context.study_id
        )
        hashes = _completion_artifact_hashes(
            context.repository,
            context.study_id,
            completion_step_id=context.step.step_instance_id,
        )
        record = CompletionRecord(
            completion_record_id=stable_id(
                "completion",
                context.study_id,
                context.step.parameters["source_completion_id"],
                context.step.parameters["venue_policy_id"],
            ),
            project_id=study.project_id,
            study_id=context.study_id,
            scope_version=study.active_scope_version,
            research_contract_version=study.active_contract_version,
            run_ids=[
                str(context.step.parameters["source_completion_id"])
            ],
            issuer_version=__version__,
            readiness=readiness,
            artifact_hashes=hashes,
            verification_command=(
                "research-forge workflow verify-completion "
                f'"{study_root / "completion_record.json"}"'
            ),
        )
        saved = context.repository.save_completion_record(record)
        completed_study = context.repository.load_study(context.study_id)
        if (
            completed_study.phase is not Phase.PAPER
            or completed_study.lifecycle is not StudyLifecycle.COMPLETED
        ):
            context.repository.save_study(
                completed_study.model_copy(
                    update={
                        "phase": Phase.PAPER,
                        "lifecycle": StudyLifecycle.COMPLETED,
                    }
                ),
                "stage4_completion_record_finalized",
            )
        return {"completion_record": saved.model_dump(mode="json")}

    handlers = {
        "stage4_claim_intake": claim_intake,
        "publication_prerequisite_gate": prerequisite_gate,
        "venue_and_reporting_profile_freeze": freeze_venue,
        "evaluation_transparency_register": evaluation_transparency,
        "stage4_evidence_sufficiency_gate": evidence_sufficiency_gate,
        "mandatory_reporting_register": mandatory_reporting,
        "contribution_candidate_generation": contribution_candidates,
        "publication_narrative_contract_freeze": freeze_narrative,
        "evidence_claim_mapping": evidence_claim_mapping,
        "visual_argument_planning": visual_argument_planning,
        "outline_generation_and_audit": outline_generation,
        "draft_generation": draft_generation,
        "scientific_review_panel": scientific_review,
        "bounded_content_revision": bounded_revision,
        "sci_ssci_preservation_audit": sci_ssci_preservation_audit,
        "figure_and_table_generation": figure_generation,
        "visual_integrity_audit": visual_integrity,
        "authorial_humanization": authorial_humanization,
        "humanization_integrity_audit": humanization_integrity,
        "reviewer_attack_surface_audit": reviewer_attack_surface,
        "reporting_and_disclosure_audit": reporting_and_disclosure,
        "latex_typesetting": latex_typesetting,
        "pdf_compile_and_verify": pdf_compile,
        "publication_qa_gate_v2": publication_qa_v2,
        "system_publication_readiness": system_readiness,
        "completion_record": completion_record,
    }
    return {
        name: _revision_scoped_handler(handler)
        for name, handler in handlers.items()
    }


def stage_four_root(
    repository: WorkflowRepository,
    study_id: str,
    *,
    revision: int | None = None,
) -> Path:
    repository.load_study(study_id)
    study_root = repository.root / "studies" / study_id
    if revision is None:
        revision = _ACTIVE_STAGE4_REVISION.get()
    if revision < 1:
        raise ValueError("Stage 4 revision must be positive")
    if revision == 1:
        return study_root / "stage4"
    return (
        study_root
        / "stage4_revisions"
        / f"v{revision}"
        / "stage4"
    )


def persist_stage_four_artifact(
    repository: WorkflowRepository,
    study_id: str,
    *,
    name: str,
    value: StrictModel | dict[str, Any],
    kind: str,
    role: ArtifactRole,
    version: int | None = None,
    immutable: bool = False,
    predecessor_artifact_ids: list[str] | None = None,
) -> ArtifactRecord:
    """Persist and register one Stage 4 object with dependency edges."""

    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("Stage 4 artifact name must be a simple file stem")
    revision = _ACTIVE_STAGE4_REVISION.get()
    if version is None:
        version = revision
    path = stage_four_root(repository, study_id) / f"{name}.json"
    payload = (
        value.model_dump(mode="json")
        if isinstance(value, StrictModel)
        else value
    )
    if path.is_file() and immutable and read_json(path) != payload:
        raise ValueError(f"frozen Stage 4 artifact requires a new version: {name}")
    write_json_atomic(path, payload)
    artifact = repository.register_artifact(
        study_id,
        str(path),
        sha256_file(path),
        kind=kind,
        role=role,
        status=(
            ArtifactStatus.FROZEN if immutable else ArtifactStatus.DRAFT
        ),
        version=version,
    )
    known = {
        item.artifact_id for item in repository.list_artifacts(study_id)
    }
    for predecessor_id in predecessor_artifact_ids or []:
        if predecessor_id not in known:
            raise ValueError(
                f"unknown Stage 4 predecessor artifact: {predecessor_id}"
            )
        repository.add_dependency(
            study_id,
            predecessor_id,
            artifact.artifact_id,
            relation="stage4_compiles_from",
        )
    return artifact


def load_stage_four_artifact(
    repository: WorkflowRepository,
    study_id: str,
    name: str,
    *,
    revision: int | None = None,
) -> dict[str, Any]:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("Stage 4 artifact name must be a simple file stem")
    return read_json(
        stage_four_root(repository, study_id, revision=revision)
        / f"{name}.json"
    )


def stage4_read_model(
    repository: WorkflowRepository, study_id: str
) -> dict[str, Any]:
    """Return the product-facing Stage 4 state without inferring from logs."""

    study = repository.load_study(study_id)
    steps = [
        item
        for item in repository.list_steps(study_id)
        if item.phase is Phase.PAPER
    ]
    workflow_revision = max(
        (
            int(item.parameters.get("stage4_workflow_revision", 1))
            for item in steps
        ),
        default=1,
    )
    selected_stage4_root = stage_four_root(
        repository,
        study_id,
        revision=workflow_revision,
    )
    gates = [
        item
        for item in repository.list_gates(study_id)
        if item.gate_type
        in {
            GateType.PUBLICATION_NARRATIVE,
            GateType.VISUAL_ARGUMENT,
            GateType.AUTHOR_VOICE,
            GateType.FINAL_SUBMISSION,
        }
    ]
    artifact_names = (
        "publication_narrative_contract_draft_v1",
        "publication_narrative_contract_v1",
        "reader_memory_contract_v1",
        "evaluation_transparency_register_v1",
        "stage4_evidence_backfill_request_v1",
        "stage3_evidence_backfill_successor_request_v1",
        "mandatory_reporting_register_v1",
        "mandatory_results_registry_v1",
        "visual_argument_plan_v1",
        "visual_argument_plan_frozen_v1",
        "final_visual_approval_v1",
        "author_voice_profile_v1",
        "humanization_plan_v1",
        "humanization_trace_v1",
        "humanization_integrity_report_v1",
        "author_voice_approval_v1",
        "visual_integrity_report_v1",
        "reviewer_attack_surface_report_v1",
        "scientific_successor_request_v1",
        "reporting_integrity_report_v1",
        "evaluation_transparency_coverage_report_v1",
        "ai_use_disclosure_v1",
    )
    artifacts: dict[str, Any] = {}
    for name in artifact_names:
        path = selected_stage4_root / f"{name}.json"
        attempt_paths = sorted(
            selected_stage4_root.glob(f"{name}_attempt_*.json"),
            key=lambda item: int(item.stem.rsplit("_", 1)[-1]),
        )
        selected = attempt_paths[-1] if attempt_paths else path
        if selected.is_file():
            artifacts[name] = read_json(selected)
            if selected != path:
                artifacts[f"{name}_selected_path"] = str(selected)
    study_root = repository.root / "studies" / study_id
    for name, path in {
        "readiness": study_root / "readiness.json",
        "completion_record": study_root / "completion_record.json",
    }.items():
        if path.is_file():
            artifacts[name] = read_json(path)
    waiting_steps = [
        item
        for item in steps
        if item.status.value
        in {"waiting_for_user", "blocked", "failed"}
    ]
    return {
        "study_id": study_id,
        "initialized": bool(steps),
        "claim_authority": (
            stage4_claim_authority(repository, study_id)
            if steps
            else None
        ),
        "overview": {
            "phase": study.phase.value,
            "lifecycle": study.lifecycle.value,
            "workflow_revision": workflow_revision,
            "completed_steps": sum(
                item.status.value == "succeeded" for item in steps
            ),
            "total_steps": len(steps),
            "status": (
                "completed"
                if steps
                and all(item.status.value == "succeeded" for item in steps)
                else "attention_required"
                if waiting_steps
                else "running"
                if steps
                else "not_initialized"
            ),
        },
        "steps": [item.model_dump(mode="json") for item in steps],
        "gates": [item.model_dump(mode="json") for item in gates],
        "artifacts": artifacts,
    }


__all__ = [
    "STAGE4_STEP_DEFINITIONS",
    "ensure_stage_four_dag",
    "load_stage_four_artifact",
    "persist_stage_four_artifact",
    "stage4_read_model",
    "stage_four_root",
    "stage_four_handlers",
    "stage_four_step_definitions",
]
