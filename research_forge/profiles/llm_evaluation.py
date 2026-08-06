"""Deterministic, target-isolated evaluation of frozen LLM responses.

This component intentionally does not invoke a model.  It establishes the
task identity, reference isolation, normalization and paired scoring boundary
that a later model-runner adapter must satisfy.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..models import StrictModel
from ..storage import safe_relative
from .contracts import LLMEvaluationParameters


class LLMTaskScore(StrictModel):
    task_id: str
    score: Literal[0.0, 1.0]


class LLMEvaluationResult(StrictModel):
    schema_version: int = 1
    profile_id: Literal["llm_evaluation_v1"] = "llm_evaluation_v1"
    arm: Literal["baseline", "treatment"]
    rows: list[LLMTaskScore] = Field(min_length=1)
    denominator: int = Field(ge=1)
    normalized_exact_match: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def result_is_self_consistent(self) -> "LLMEvaluationResult":
        if self.denominator != len(self.rows):
            raise ValueError("task count must equal denominator")
        if len({row.task_id for row in self.rows}) != len(self.rows):
            raise ValueError("task IDs must be unique")
        mean = sum(row.score for row in self.rows) / len(self.rows)
        if not math.isclose(mean, self.normalized_exact_match, abs_tol=1e-12):
            raise ValueError("aggregate score does not match task rows")
        return self


class LLMEvaluationContrast(StrictModel):
    baseline_value: float
    treatment_value: float
    effect: float
    threshold: float
    denominator: int = Field(ge=1)
    verdict: Literal["supported", "refuted", "inconclusive"]


def normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise ValueError(f"LLM evaluation input is missing: {path}")
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL row {line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"JSONL row {line_number} must be an object")
        rows.append(payload)
    if not rows:
        raise ValueError(f"LLM evaluation input is empty: {path}")
    return rows


def run_llm_response_evaluation(
    parameters: LLMEvaluationParameters,
    *,
    arm: Literal["baseline", "treatment"],
    root: str | Path,
) -> LLMEvaluationResult:
    root_path = Path(root).resolve()
    response_rows = _read_jsonl(
        safe_relative(root_path, parameters.candidate_response_path)
    )
    reference_rows = _read_jsonl(
        safe_relative(root_path, parameters.evaluator_reference_path)
    )
    references: dict[str, set[str]] = {}
    for row in reference_rows:
        task_id = str(row.get(parameters.task_id_field) or "").strip()
        answers = row.get(parameters.reference_answers_field)
        if not task_id or task_id in references:
            raise ValueError("reference task IDs must be non-empty and unique")
        if not isinstance(answers, list) or not answers:
            raise ValueError("each task requires at least one reference answer")
        references[task_id] = {normalize_answer(str(item)) for item in answers}
    response_field = (
        parameters.baseline_response_field
        if arm == "baseline"
        else parameters.treatment_response_field
    )
    responses: dict[str, str] = {}
    for row in response_rows:
        task_id = str(row.get(parameters.task_id_field) or "").strip()
        if not task_id or task_id in responses:
            raise ValueError("candidate task IDs must be non-empty and unique")
        responses[task_id] = str(row.get(response_field) or "")
    if set(responses) != set(references):
        raise ValueError("candidate and evaluator task sets must match exactly")
    scores = [
        LLMTaskScore(
            task_id=task_id,
            score=(
                1.0
                if normalize_answer(responses[task_id]) in references[task_id]
                else 0.0
            ),
        )
        for task_id in sorted(references)
    ]
    return LLMEvaluationResult(
        arm=arm,
        rows=scores,
        denominator=len(scores),
        normalized_exact_match=sum(item.score for item in scores) / len(scores),
    )


def evaluate_llm_response_contrast(
    baseline: LLMEvaluationResult,
    treatment: LLMEvaluationResult,
    parameters: LLMEvaluationParameters,
) -> LLMEvaluationContrast:
    if [row.task_id for row in baseline.rows] != [
        row.task_id for row in treatment.rows
    ]:
        raise ValueError("baseline and treatment task rows must pair exactly")
    effect = treatment.normalized_exact_match - baseline.normalized_exact_match
    tolerance = 1e-12
    verdict = (
        "supported"
        if effect > parameters.effect_threshold + tolerance
        else "refuted"
        if effect < parameters.effect_threshold - tolerance
        else "inconclusive"
    )
    return LLMEvaluationContrast(
        baseline_value=baseline.normalized_exact_match,
        treatment_value=treatment.normalized_exact_match,
        effect=effect,
        threshold=parameters.effect_threshold,
        denominator=baseline.denominator,
        verdict=verdict,
    )


__all__ = [
    "LLMEvaluationContrast",
    "LLMEvaluationResult",
    "LLMTaskScore",
    "evaluate_llm_response_contrast",
    "normalize_answer",
    "run_llm_response_evaluation",
]
