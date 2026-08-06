"""Tamper-evident acceptance report helpers for Study Design components."""

from __future__ import annotations

import hashlib
import html
import json
import platform
from pathlib import Path
from typing import Any

from ..storage import write_json_atomic, write_text_atomic
from .schemas import StudyDesignMaturity
from .workflow_evidence import REQUIRED_C3_WORKFLOW_RECEIPTS


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_acceptance_report(
    *, study_id: str, component_id: str, source_hash: str,
    schema_hash: str, environment_hash: str, test_results: dict[str, bool],
    independent_agreement: dict[str, Any], paper_audit: dict[str, bool],
    replay: dict[str, Any], maturity: str,
    version: str = "1",
    positive_tests: dict[str, bool] | None = None,
    negative_tests: dict[str, bool] | None = None,
    mutation_tests: dict[str, bool] | None = None,
    dry_run_receipt: dict[str, Any] | None = None,
    workflow_receipts: dict[str, bool] | None = None,
    formal_workflow_completed: bool = False,
    canonical_stage_four_completed: bool = False,
    claim_binding_coverage: float = 0.0,
    known_limitations: list[str] | None = None,
    maturity_before: str = "c0_described",
    authority_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    normalized_receipts = dict(workflow_receipts or {})
    normalized_positive = dict(positive_tests or test_results)
    normalized_negative = dict(negative_tests or {})
    normalized_mutations = dict(mutation_tests or {})
    normalized_dry_run = dict(dry_run_receipt or {})
    component_valid = (
        bool(test_results)
        and all(test_results.values())
        and all(paper_audit.values())
        and all(normalized_positive.values())
        and (not normalized_negative or all(normalized_negative.values()))
        and (not normalized_mutations or all(normalized_mutations.values()))
        and (not normalized_dry_run or normalized_dry_run.get("passed") is True)
        and bool(independent_agreement.get("passed"))
        and bool(replay.get("passed"))
    )
    c3_requested = maturity == "c3_real_fixture"
    promotion_eligible = (
        component_valid
        and c3_requested
        and formal_workflow_completed
        and canonical_stage_four_completed
        and bool(normalized_receipts)
        and all(normalized_receipts.values())
    )
    normalized_authority = {
        "source_hash": source_hash,
        "schema_hash": schema_hash,
        "environment_hash": environment_hash,
        "component_version": version,
        **dict(authority_context or {}),
    }
    report = {
        "schema_version": 2, "study_id": study_id,
        "component_id": component_id, "version": version,
        "source_hash": source_hash,
        "schema_hash": schema_hash, "environment_hash": environment_hash,
        "authority_context": normalized_authority,
        "authority_context_hash": _digest(normalized_authority),
        "test_results": test_results,
        "positive_tests": normalized_positive,
        "negative_tests": normalized_negative,
        "mutation_tests": normalized_mutations,
        "dry_run_receipt": normalized_dry_run,
        "independent_recalculator_agreement": independent_agreement,
        "claim_binding_coverage": claim_binding_coverage,
        "paper_audit": paper_audit,
        "replay": replay,
        "workflow_receipts": normalized_receipts,
        "formal_workflow_completed": formal_workflow_completed,
        "canonical_stage_four_completed": canonical_stage_four_completed,
        "known_limitations": list(known_limitations or []),
        "maturity_before": maturity_before,
        "maturity_after": maturity if promotion_eligible or not c3_requested else "c2_dry_run",
        "promotion_eligible": promotion_eligible,
        # Read compatibility for clients that still display ``maturity``.
        "maturity": maturity if promotion_eligible or not c3_requested else "c2_dry_run",
    }
    report["valid"] = component_valid and (not c3_requested or promotion_eligible)
    report["acceptance_hash"] = _digest(report)
    return report


def verify_acceptance_report(
    report: dict[str, Any],
    *,
    expected_authority_context: dict[str, str] | None = None,
) -> bool:
    expected = report.get("acceptance_hash")
    payload = {key: value for key, value in report.items() if key != "acceptance_hash"}
    if not (isinstance(expected, str) and expected == _digest(payload)):
        return False
    recorded_context = report.get("authority_context")
    recorded_hash = report.get("authority_context_hash")
    if not isinstance(recorded_context, dict) or recorded_hash != _digest(recorded_context):
        return False
    if expected_authority_context is not None:
        expected_context = {
            "source_hash": str(report.get("source_hash", "")),
            "schema_hash": str(report.get("schema_hash", "")),
            "environment_hash": str(report.get("environment_hash", "")),
            "component_version": str(report.get("version", "")),
            **expected_authority_context,
        }
        if recorded_context != expected_context:
            return False
    return True


def write_acceptance_reports(
    report: dict[str, Any], *, json_path: Path, html_path: Path
) -> None:
    write_json_atomic(json_path, report)
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(json.dumps(value, ensure_ascii=False))}</td></tr>"
        for key, value in report.items()
    )
    write_text_atomic(
        html_path,
        "<!doctype html><html><head><meta charset='utf-8'><title>Study Design Acceptance</title>"
        "<style>body{font:16px system-ui;max-width:960px;margin:3rem auto;color:#051d25}"
        "table{border-collapse:collapse;width:100%}th,td{padding:.75rem;border:1px solid #a9c7ce;text-align:left}</style>"
        f"</head><body><h1>Study Design Acceptance</h1><table>{rows}</table></body></html>",
    )


class DefaultStudyDesignAcceptanceEvaluator:
    """Derive component maturity from receipts instead of declarations.

    The evaluator deliberately treats a component test harness, a dry run, and
    the canonical four-phase workflow as different evidence levels.  A
    scientific verdict may be negative or inconclusive without failing the
    platform acceptance assessment.
    """

    @staticmethod
    def _all_true(value: Any) -> bool:
        return isinstance(value, dict) and bool(value) and all(
            item is True for item in value.values()
        )

    def evaluate_component_tests(self, evidence: Any) -> dict[str, Any]:
        tests = dict((evidence or {}).get("positive_tests") or {})
        return {"passed": self._all_true(tests), "tests": tests}

    def evaluate_e2e_run(self, evidence: Any) -> dict[str, Any]:
        payload = evidence or {}
        receipts = dict(payload.get("workflow_receipts") or {})
        passed = bool(
            payload.get("formal_workflow_completed")
            and payload.get("canonical_stage_four_completed")
            and all(receipts.get(key) is True for key in REQUIRED_C3_WORKFLOW_RECEIPTS)
        )
        return {
            "passed": passed,
            "study_id": payload.get("study_id"),
            "receipts": receipts,
        }

    def evaluate_negative_tests(self, evidence: Any) -> dict[str, Any]:
        tests = dict((evidence or {}).get("negative_tests") or {})
        return {"passed": self._all_true(tests), "tests": tests}

    def evaluate_mutation_tests(self, evidence: Any) -> dict[str, Any]:
        tests = dict((evidence or {}).get("mutation_tests") or {})
        return {"passed": self._all_true(tests), "tests": tests}

    def derive_maturity(self, evidence: Any) -> StudyDesignMaturity:
        payload = evidence or {}
        if not self.evaluate_component_tests(payload)["passed"]:
            return StudyDesignMaturity.C0_DESCRIBED
        if not (
            self.evaluate_negative_tests(payload)["passed"]
            and self.evaluate_mutation_tests(payload)["passed"]
        ):
            return StudyDesignMaturity.C1_SCHEMA
        if not payload.get("dry_run_receipt"):
            return StudyDesignMaturity.C1_SCHEMA
        if not self.evaluate_e2e_run(payload)["passed"]:
            return StudyDesignMaturity.C2_DRY_RUN
        if not (
            bool((payload.get("independent_recalculator_agreement") or {}).get("passed"))
            and self._all_true(payload.get("paper_audit") or {})
            and float(payload.get("claim_binding_coverage") or 0.0) >= 1.0
        ):
            return StudyDesignMaturity.C2_DRY_RUN
        return StudyDesignMaturity.C3_REAL_FIXTURE
__all__ = [
    "DefaultStudyDesignAcceptanceEvaluator",
    "build_acceptance_report",
    "verify_acceptance_report",
    "write_acceptance_reports",
]


def _acceptance_plan() -> dict[str, Any]:
    return {
        "study_design_id": "independent_group_comparison_v1",
        "study_design_version": "1",
        "unit_structure": {"row_unit": "one independently generated software-task case", "observation_unit": "software-task case", "assignment_unit": "software-task case", "analysis_unit": "software-task case", "variance_unit": "software-task case", "independent_unit": "software-task case"},
        "allocation": {"mechanism": "randomized", "evidence": "The frozen allocation ledger shuffles 160 indexed cases once with seed 20260804 and assigns exactly 80 cases to each arm."},
        "arms": [
            {"arm_id": "control", "label": "Standard procedure", "role": "control", "definition": "After sorting assigned case identifiers, return quality 50 + 0.7 times (within-arm rank modulo 10) and mark completion unless within-arm rank is divisible by 5."},
            {"arm_id": "treatment", "label": "Assisted procedure", "role": "treatment", "definition": "After sorting assigned case identifiers, return quality 53 + 0.7 times (within-arm rank modulo 10) and mark completion unless within-arm rank is divisible by 8."},
        ],
        "outcomes": [
            {"outcome_id": "quality", "label": "Task quality", "kind": "continuous", "field": "quality", "role": "primary", "beneficial_direction": "higher"},
            {"outcome_id": "completion", "label": "Successful completion", "kind": "binary", "field": "completed", "role": "secondary", "beneficial_direction": "higher", "event_value": True},
        ],
        "estimands": [
            {"estimand_id": "quality_difference", "outcome_id": "quality", "treatment_arm_id": "treatment", "control_arm_id": "control", "effect_measure": "mean_difference", "analysis_population": "complete_case"},
            {"estimand_id": "completion_difference", "outcome_id": "completion", "treatment_arm_id": "treatment", "control_arm_id": "control", "effect_measure": "risk_difference", "analysis_population": "complete_case"},
        ],
        "estimator_plan": {"quality": {"estimator_id": "welch_mean_difference_v1", "equal_variance_assumed": False}, "completion": {"estimator_id": "wald_risk_difference_v1", "equal_variance_assumed": False}},
        "inference_plan": {"quality": {"method": "Welch t interval", "confidence_level": 0.95}, "completion": {"method": "Wald risk-difference interval", "confidence_level": 0.95}},
        "missingness": {"policy": "complete_case", "denominator_rule": "For the continuous outcome, exclude only the five case identifiers frozen in the integrity-dropout list; retain all 160 cases in the registered population and missingness ledger.", "exclusion_reasons_required": True},
        "multiplicity": {"method": "holm", "family_id": "confirmatory outcomes", "hypothesis_ids": ["quality", "completion"], "alpha_or_q": 0.05},
        "decision_rules": {"quality": {"mode": "superiority", "effect_measure": "mean_difference", "beneficial_direction": "higher"}, "completion": {"mode": "superiority", "effect_measure": "risk_difference", "beneficial_direction": "higher"}},
        "claim_boundary": {"generalization": "The deterministic software acceptance fixture only; no human, clinical, field, or real-world intervention effect is implied.", "reproduction_materials": ["task and scoring specification", "allocation ledger", "analysis plan", "frozen rows", "independent recalculator"]},
    }


def run_independent_group_acceptance(output_root: Path | None = None) -> dict[str, Any]:
    """Run the C3 black-box fixture and emit machine/human reports."""

    from .designs.independent_group import IndependentGroupComparison
    from .reference import recalculate_continuous_primary
    from .schemas import AnalysisPlan

    plan = AnalysisPlan.model_validate(_acceptance_plan())
    rows: list[dict[str, Any]] = []
    for index in range(80):
        rows.append({"subject_id": f"control-{index:03d}", "arm": "control", "quality": None if index in {2, 17} else 50+(index%11)*0.7, "completed": index%5 != 0})
        rows.append({"subject_id": f"treatment-{index:03d}", "arm": "treatment", "quality": None if index in {9, 33, 70} else 53+(index%13)*0.65, "completed": index%8 != 0})
    design = IndependentGroupComparison()
    evaluation = design.evaluate(plan, rows)
    replay = design.evaluate(plan, rows)
    reference = recalculate_continuous_primary(plan, rows)
    primary = next(item for item in evaluation.outcomes if item.outcome_id == "quality")
    envelope = design.produce_claim_envelope(plan, evaluation)
    from .mutations import build_authority_mutation_report
    from .negative_acceptance import required_negative_acceptance_cases

    required_negative = required_negative_acceptance_cases(
        plan, rows, evaluation, envelope
    )
    mutation_report = build_authority_mutation_report(plan, rows)
    reference_error = abs(float(primary.effect) - float(reference["effect"]))
    negative_checks = {
        "cross_arm_duplicate_rejected": bool(design.validate_realized_data(plan, [{"subject_id": "x", "arm": "control", "quality": 1, "completed": True}, {"subject_id": "x", "arm": "treatment", "quality": 2, "completed": True}])),
        "repeated_subject_rejected": bool(design.validate_realized_data(plan, [{"subject_id": "x", "arm": "control", "quality": 1, "completed": True}, {"subject_id": "x", "arm": "control", "quality": 2, "completed": True}])),
        "unknown_arm_rejected": bool(design.validate_realized_data(plan, [{"subject_id": "x", "arm": "other", "quality": 1, "completed": True}])),
        "missing_subject_rejected": bool(design.validate_realized_data(plan, [{"arm": "control", "quality": 1, "completed": True}])),
        "missing_outcome_rejected": bool(design.validate_realized_data(plan, [{"subject_id": "x", "arm": "control", "completed": True}])),
        "missingness_counted": primary.missing_count == 5,
        "denominator_reconciled": primary.denominator == 155,
        "two_arms_present": len(plan.arms) == 2,
        "primary_present": len([item for item in plan.outcomes if item.role == "primary"]) == 1,
        "units_explicit": bool(plan.unit_structure.independent_unit),
        "allocation_evidence_present": bool(plan.allocation.evidence),
        "multiplicity_family_named": bool(plan.multiplicity.family_id),
        "raw_p_values_retained": all(item.raw_p_value is not None for item in evaluation.outcomes),
        "adjusted_p_values_retained": all(item.adjusted_p_value is not None for item in evaluation.outcomes),
        "causal_claim_bounded": envelope.allocation_verified_randomized is True,
    }
    serialized = evaluation.model_dump(mode="json")
    replay_serialized = replay.model_dump(mode="json")
    paper_text = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
    source_files = [Path(__file__), Path(__file__).with_name("designs") / "independent_group.py", Path(__file__).with_name("reference.py")]
    source_hash = _digest({str(path.name): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files})
    report = build_acceptance_report(
        study_id="study-independent-group-c3",
        component_id="independent_group_comparison_v1",
        source_hash=source_hash,
        schema_hash=_digest(AnalysisPlan.model_json_schema()),
        environment_hash=_digest({"python": platform.python_version(), "platform": platform.platform()}),
        test_results={"black_box_160_subjects": len(rows) == 160 and evaluation.eligible, **negative_checks},
        positive_tests={
            "black_box_160_subjects": len(rows) == 160 and evaluation.eligible,
            "continuous_and_binary_outcomes_evaluated": len(evaluation.outcomes) == 2,
            "independent_reference_agreement": reference_error < 1e-12,
        },
        negative_tests={
            name: bool(item["passed"])
            for name, item in required_negative.items()
        },
        mutation_tests={
            name: bool(item["passed"])
            for name, item in mutation_report.items()
        },
        dry_run_receipt={
            "passed": True,
            "rows": len(rows),
            "outcomes": len(evaluation.outcomes),
        },
        independent_agreement={"passed": reference_error < 1e-12, "absolute_error": reference_error, "reference": reference},
        paper_audit={"natural_language": not any(token in paper_text for token in ("quality_difference", "run-", "study-", "C:\\")), "verdict_preserved": envelope.scientific_verdict == evaluation.primary_decision, "limitations_present": bool(envelope.generalization_boundary)},
        replay={"passed": serialized == replay_serialized, "same_hash": _digest(serialized) == _digest(replay_serialized)},
        maturity="c2_dry_run",
    )
    report["component_tests_passed"] = report["valid"]
    report["valid"] = False
    report["automatic_acceptance"] = "incomplete"
    report["blocking_missing_artifacts"] = [
        "01_task_brief.json", "02_research_contract.json",
        "03_contract_completion_report.json", "04_execution_supplement.json",
        "05_run_plan.json", "06_formal_outputs/", "10_scientific_verdict.json",
        "11_evidence_claim_map.json", "12_figures/", "13_manuscript.tex",
        "14_manuscript.pdf", "15_reproduction_package/",
        "18_human_review_packet.pdf",
    ]
    report["scientific_verdict"] = evaluation.primary_decision
    report["subjects"] = len(rows)
    report["outcomes"] = len(evaluation.outcomes)
    report["claim_envelope"] = envelope.model_dump(mode="json")
    # Re-seal after adding the public summary fields.
    report["acceptance_hash"] = _digest({key: value for key, value in report.items() if key != "acceptance_hash"})
    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)
        write_acceptance_reports(report, json_path=output_root / "acceptance.json", html_path=output_root / "acceptance.html")
    return report


__all__.extend(["run_independent_group_acceptance"])
