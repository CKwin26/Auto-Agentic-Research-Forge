"""Whitelist registries for Study Designs and composable inference modules."""

from __future__ import annotations

import json
from pathlib import Path

from .sdk import InferenceModule, StudyDesignProfile


_DESIGNS: dict[str, StudyDesignProfile] = {}
_INFERENCE_MODULES: dict[str, InferenceModule] = {}


def _verified_acceptance(component_id: str) -> dict[str, object] | None:
    """Load tamper-evident maturity evidence for one shipped component."""

    from .acceptance import (
        DefaultStudyDesignAcceptanceEvaluator,
        verify_acceptance_report,
    )

    path = Path(__file__).parent / "acceptance_reports" / f"{component_id}.json"
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not verify_acceptance_report(report):
        return None
    maturity = DefaultStudyDesignAcceptanceEvaluator().derive_maturity(report)
    return {
        "verified_maturity": maturity.value,
        "acceptance_report_present": True,
        "acceptance_hash": report.get("acceptance_hash"),
        "acceptance_study_id": report.get("study_id"),
    }

_DESCRIBED_DESIGNS: tuple[dict[str, object], ...] = (
    {"design_id": "factorial_experiment_v1", "title": "Factorial experiment", "summary": "Multiple factors and interactions.", "known_limits": ["schema and executable kernel not implemented"]},
    {"design_id": "longitudinal_repeated_measures_v1", "title": "Repeated measures and longitudinal study", "summary": "Correlated observations over time.", "known_limits": ["covariance and attrition handling not implemented"]},
    {"design_id": "survival_analysis_v1", "title": "Survival analysis", "summary": "Time-to-event outcomes with censoring.", "known_limits": ["risk-set and censoring kernel not implemented"]},
    {"design_id": "causal_inference_v1", "title": "Causal inference", "summary": "Identification-driven observational causal analysis.", "known_limits": ["DAG, positivity, and estimator kernels not implemented"]},
    {"design_id": "online_ab_test_v1", "title": "Online A/B test", "summary": "Sequential online controlled experiments.", "known_limits": ["exposure logging and sequential inference not implemented"]},
    {"design_id": "open_generation_human_rating_v1", "title": "Open generation with human ratings", "summary": "Human-scale evaluation of open-ended outputs.", "known_limits": ["rater reliability and adjudication kernel not implemented"]},
)


def register_study_design(profile: StudyDesignProfile) -> StudyDesignProfile:
    key = profile.descriptor.design_id
    if key in _DESIGNS:
        raise ValueError(f"duplicate Study Design id: {key}")
    _DESIGNS[key] = profile
    return profile


def register_inference_module(module: InferenceModule) -> InferenceModule:
    key = module.descriptor.module_id
    if key in _INFERENCE_MODULES:
        raise ValueError(f"duplicate Inference Module id: {key}")
    _INFERENCE_MODULES[key] = module
    return module


def study_design(design_id: str) -> StudyDesignProfile:
    try:
        return _DESIGNS[design_id]
    except KeyError as exc:
        raise ValueError("blocked_unsupported_study_design") from exc


def inference_module(module_id: str) -> InferenceModule:
    try:
        return _INFERENCE_MODULES[module_id]
    except KeyError as exc:
        raise ValueError("blocked_unsupported_inference_module") from exc


def study_design_catalog() -> list[dict[str, object]]:
    implemented = []
    for _, value in sorted(_DESIGNS.items()):
        acceptance = _verified_acceptance(value.descriptor.design_id)
        implemented.append({
            "design_id": value.descriptor.design_id,
            "version": value.descriptor.version,
            "title": value.descriptor.title,
            "summary": value.descriptor.summary,
            "maturity": value.descriptor.maturity.value,
            "formal_execution_supported": (
                value.descriptor.formal_execution_supported
            ),
            "known_limits": list(value.descriptor.known_limits),
            "verified_maturity": (
                acceptance["verified_maturity"]
                if acceptance is not None
                else value.descriptor.maturity.value
            ),
            "acceptance_report_present": acceptance is not None,
            "acceptance_hash": (
                acceptance.get("acceptance_hash") if acceptance else None
            ),
            "acceptance_study_id": (
                acceptance.get("acceptance_study_id") if acceptance else None
            ),
        })
    described = [
        {
            **item,
            "version": "1",
            "maturity": "c0_described",
            "formal_execution_supported": False,
        }
        for item in _DESCRIBED_DESIGNS
        if item["design_id"] not in _DESIGNS
    ]
    return implemented + described


def inference_module_catalog() -> list[dict[str, object]]:
    catalog: list[dict[str, object]] = []
    for _, value in sorted(_INFERENCE_MODULES.items()):
        acceptance = _verified_acceptance(value.descriptor.module_id)
        catalog.append({
            "module_id": value.descriptor.module_id,
            "version": value.descriptor.version,
            "title": value.descriptor.title,
            "summary": value.descriptor.summary,
            "maturity": value.descriptor.maturity.value,
            "formal_execution_supported": (
                value.descriptor.formal_execution_supported
            ),
            "sensitivity_only_by_default": (
                value.descriptor.sensitivity_only_by_default
            ),
            "known_limits": list(value.descriptor.known_limits),
            "verified_maturity": (
                acceptance["verified_maturity"]
                if acceptance is not None
                else value.descriptor.maturity.value
            ),
            "acceptance_report_present": acceptance is not None,
            "acceptance_hash": (
                acceptance.get("acceptance_hash") if acceptance else None
            ),
            "acceptance_study_id": (
                acceptance.get("acceptance_study_id") if acceptance else None
            ),
        })
    return catalog


__all__ = [
    "inference_module",
    "inference_module_catalog",
    "register_inference_module",
    "register_study_design",
    "study_design",
    "study_design_catalog",
]
