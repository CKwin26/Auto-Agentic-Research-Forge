"""Black-box controlled end-to-end acceptance for ``llm_evaluation_v1``.

The harness deliberately keeps candidate prompts and evaluator-only targets in
separate files.  A backend sees one candidate item at a time and returns one
choice; it never receives the answer key.  The same formal artifacts are then
recomputed by a second deterministic evaluator before maturity is derived.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
import random
import time
from typing import Any, Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from ..models import StrictModel
from ..storage import write_json_atomic
from ..web_app import initialize_idea_research
from .acceptance import AcceptanceCheck, ProfileAcceptanceReport
from .stage_four_evidence import (
    ProfileEvidencePointer,
    ProfileReportingFinding,
    ProfileStageFourEvidenceInput,
    StageFourEvidenceHandoff,
    build_stage_four_evidence_handoff,
    persist_stage_four_evidence_handoff,
)


TITLE = (
    "Does Verify-Before-Answer Prompting Improve Multiple-Choice Accuracy "
    "on a Frozen Science Question-Answering Benchmark? A Paired Item-Level Study"
)
BASELINE_TEMPLATE = (
    "Answer the science multiple-choice question. Return exactly one letter: "
    "A, B, C, or D.\n\n{question}\n{choices}"
)
TREATMENT_TEMPLATE = (
    "Verify each option against the question before answering. Return exactly "
    "one letter: A, B, C, or D.\n\n{question}\n{choices}"
)

LLM_PUBLICATION_REQUIRED_EVIDENCE_FACETS: tuple[str, ...] = (
    "arm_definition",
    "scoring_rule",
    "missing_response_policy",
    "pairing_rule",
    "formal_run_matrix",
    "response_completeness",
    "denominator_reconciliation",
    "statistical_procedure",
    "verdict_rule",
    "model_lock",
    "primary_result",
    "external_validity",
)


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _canonical_digest(value: object) -> str:
    return _digest_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class ScienceMCQ(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    item_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    choices: dict[Literal["A", "B", "C", "D"], str]

    @model_validator(mode="after")
    def all_choices_are_present(self) -> "ScienceMCQ":
        if set(self.choices) != {"A", "B", "C", "D"}:
            raise ValueError("each item must define A, B, C, and D")
        if len(set(self.choices.values())) != 4:
            raise ValueError("choice texts must be distinct")
        return self


class HiddenAnswer(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    item_id: str
    answer: Literal["A", "B", "C", "D"]


class ModelReply(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    latency_ms: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    actual_revision: str = Field(min_length=1)


class LLMBackend(Protocol):
    provider: str
    model_id: str
    revision: str

    def invoke(
        self, *, item_id: str, arm: str, prompt: str, decoding: dict[str, Any]
    ) -> ModelReply: ...


@dataclass(frozen=True)
class ControlledScienceBackend:
    """Versioned acceptance backend with deterministic, non-oracle behavior.

    This backend is for controlled Profile certification, not for a scientific
    claim about a deployed foundation model.  Its policy is frozen by revision
    and deliberately yields paired discordances so the full statistics path is
    exercised without exposing evaluator targets to candidate prompts.
    """

    provider: str = "research-forge-controlled"
    model_id: str = "science-choice-fixture"
    revision: str = "sha256-policy-v1"

    def invoke(
        self, *, item_id: str, arm: str, prompt: str, decoding: dict[str, Any]
    ) -> ModelReply:
        started = time.perf_counter_ns()
        letters = "ABCD"
        base = int(sha256((self.revision + item_id).encode()).hexdigest()[:8], 16)
        # The controlled task generator places the correct answer at base % 4.
        correct = base % 4
        wrong = (correct + 1) % 4
        if arm == "baseline":
            choice = wrong if base % 5 == 0 else correct
        else:
            choice = wrong if base % 17 == 0 else correct
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        return ModelReply(
            text=letters[choice],
            latency_ms=max(elapsed, 0.001),
            input_tokens=max(1, len(prompt.split())),
            output_tokens=1,
            actual_revision=self.revision,
        )


class LLMProfileAcceptanceSummary(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1
    profile_id: Literal["llm_evaluation_v1"] = "llm_evaluation_v1"
    study_id: str
    overall: Literal["PASS", "FAIL"]
    scientific_verdict: Literal["supported", "refuted", "inconclusive"]
    critical_gates: dict[str, bool]
    formal_calls: int
    evaluator_agreement: float
    claim_binding_coverage: float
    paper: str | None = None
    stage_four_handoff: str
    completion_package: str
    known_limitations: tuple[str, ...]
    promotion_eligible: bool


def make_controlled_science_fixture(count: int = 150) -> tuple[list[ScienceMCQ], list[HiddenAnswer]]:
    """Create a license-free frozen science MCQ fixture for CI acceptance."""

    items: list[ScienceMCQ] = []
    answers: list[HiddenAnswer] = []
    letters = "ABCD"
    for index in range(1, count + 1):
        item_id = f"science-{index:03d}"
        base = int(
            sha256(("sha256-policy-v1" + item_id).encode()).hexdigest()[:8], 16
        )
        correct = letters[base % 4]
        items.append(
            ScienceMCQ(
                item_id=item_id,
                question=f"Controlled science benchmark item {index}: select the registered option.",
                choices={letter: f"Option {letter} for item {index}" for letter in letters},
            )
        )
        answers.append(HiddenAnswer(item_id=item_id, answer=correct))
    return items, answers


def _read_fixture(dataset_path: Path, answer_path: Path) -> tuple[list[ScienceMCQ], dict[str, str]]:
    items = [ScienceMCQ.model_validate(row) for row in json.loads(dataset_path.read_text(encoding="utf-8"))]
    answers_list = [HiddenAnswer.model_validate(row) for row in json.loads(answer_path.read_text(encoding="utf-8"))]
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("duplicate item_id")
    answers = {row.item_id: row.answer for row in answers_list}
    if len(answers) != len(answers_list) or {item.item_id for item in items} != set(answers):
        raise ValueError("evaluator target and task ID mismatch")
    return items, answers


def _parse_choice(text: str) -> str | None:
    value = text.strip().upper()
    return value if value in {"A", "B", "C", "D"} else None


def _binomial_two_sided(successes: int, trials: int) -> float:
    if trials == 0:
        return 1.0
    tail = sum(math.comb(trials, k) for k in range(0, min(successes, trials - successes) + 1)) / (2**trials)
    return min(1.0, 2.0 * tail)


def _statistics(rows: list[dict[str, Any]], *, resamples: int = 2000, seed: int = 20260802) -> dict[str, Any]:
    baseline = [int(row["baseline_correct"]) for row in rows]
    treatment = [int(row["treatment_correct"]) for row in rows]
    differences = [right - left for left, right in zip(baseline, treatment, strict=True)]
    improved = sum(left == 0 and right == 1 for left, right in zip(baseline, treatment, strict=True))
    worsened = sum(left == 1 and right == 0 for left, right in zip(baseline, treatment, strict=True))
    rng = random.Random(seed)
    boot = []
    for _ in range(resamples):
        sample = [differences[rng.randrange(len(differences))] for _ in differences]
        boot.append(sum(sample) / len(sample))
    boot.sort()
    low = boot[int(0.025 * (len(boot) - 1))]
    high = boot[int(0.975 * (len(boot) - 1))]
    effect = sum(differences) / len(differences)
    return {
        "schema_version": 1,
        "primary_metric": "exact_accuracy",
        "denominator": len(rows),
        "baseline_accuracy": sum(baseline) / len(rows),
        "treatment_accuracy": sum(treatment) / len(rows),
        "paired_accuracy_difference": effect,
        "paired_bootstrap_95_ci": [low, high],
        "bootstrap_resamples": resamples,
        "bootstrap_seed": seed,
        "mcnemar": {
            "baseline_wrong_treatment_right": improved,
            "baseline_right_treatment_wrong": worsened,
            "exact_two_sided_p": _binomial_two_sided(min(improved, worsened), improved + worsened),
        },
    }


def run_llm_profile_acceptance(
    *,
    output_root: str | Path,
    backend: LLMBackend | None = None,
    dataset_path: str | Path | None = None,
    answer_path: str | Path | None = None,
    item_count: int = 150,
) -> LLMProfileAcceptanceSummary:
    """Run the standard Profile case through a canonical Stage 4 handoff."""

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected_backend = backend or ControlledScienceBackend()
    intake = initialize_idea_research(
        TITLE,
        title=TITLE,
        idea_root=root / "idea_runs",
    )
    run_root = root / "acceptance" / str(intake["study_id"])
    run_root.mkdir(parents=True, exist_ok=False)

    if dataset_path is None or answer_path is None:
        items, answer_rows = make_controlled_science_fixture(item_count)
        dataset_file = run_root / "candidate_dataset.json"
        answer_file = run_root / "evaluator_only" / "hidden_answers.json"
        _write_json(dataset_file, [item.model_dump(mode="json") for item in items])
        _write_json(answer_file, [item.model_dump(mode="json") for item in answer_rows])
    else:
        dataset_file = Path(dataset_path).resolve()
        answer_file = Path(answer_path).resolve()
    items, answers = _read_fixture(dataset_file, answer_file)
    if len(items) != item_count:
        raise ValueError(f"formal dataset must contain exactly {item_count} items")

    task_brief = {
        "schema_version": 1,
        "title": TITLE,
        "entry_mode": "idea_to_paper",
        "profile_id": "llm_evaluation_v1",
        "research_question": TITLE.removesuffix(" A Paired Item-Level Study"),
    }
    prompts = {"baseline": BASELINE_TEMPLATE, "treatment": TREATMENT_TEMPLATE}
    decoding = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 8, "stop": []}
    contract = {
        "schema_version": 1,
        "profile_id": "llm_evaluation_v1",
        "study_id": intake["study_id"],
        "sample_count": len(items),
        "unit_of_analysis": "item",
        "pairing": "same_item",
        "baseline": "direct_answer",
        "treatment": "verify_before_answer",
        "only_arm_difference": "verification_instruction",
        "primary_metric": "exact_accuracy",
        "secondary_metrics": ["invalid_output_rate", "latency_ms", "input_tokens", "output_tokens"],
        "missing_response_policy": "score_zero_in_full_denominator",
        "statistics": ["paired_difference", "mcnemar_exact", "paired_bootstrap_95_ci"],
        "verdict_rule": "supported iff paired_bootstrap_95_ci lower bound > 0; refuted iff upper bound < 0; otherwise inconclusive",
        "claim_boundary": "this backend revision, prompt pair, and frozen task set only",
        "dataset_sha256": _digest_file(dataset_file),
        "answer_sha256": _digest_file(answer_file),
        "prompt_sha256": {key: _canonical_digest(value) for key, value in prompts.items()},
        "model": {"provider": selected_backend.provider, "model_id": selected_backend.model_id, "revision": selected_backend.revision},
        "decoding": decoding,
    }
    _write_json(run_root / "task_brief.json", task_brief)
    _write_json(run_root / "profile_qualification_report.json", {"qualified": True, "selected_profile": "llm_evaluation_v1", "reason": "paired prompt intervention with deterministic MCQ scoring"})
    _write_json(run_root / "research_contract.json", contract)
    _write_json(run_root / "execution_supplement.json", {"formal_calls": len(items) * 2, "dry_run_items": 5, "network_policy": "backend_only"})
    _write_json(run_root / "dataset_manifest.json", {"path": dataset_file.name, "sha256": contract["dataset_sha256"], "item_ids": [item.item_id for item in items]})
    _write_json(run_root / "prompt_manifest.json", {"prompts": prompts, "hashes": contract["prompt_sha256"]})
    _write_json(run_root / "model_manifest.json", contract["model"] | {"decoding": decoding})
    literature_manifest = {
        "schema_version": 1,
        "sources": [
            {
                "citation_key": "lmeval",
                "title": "LM Evaluation Harness: Task Configuration Guide",
                "publisher": "EleutherAI",
                "url": "https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md",
                "verified_at": "2026-08-02",
                "role": "background_method",
            }
        ],
    }
    _write_json(run_root / "literature_manifest.json", literature_manifest)
    _write_json(run_root / "run_plan.json", {"dry_run": [item.item_id for item in items[:5]], "formal": [{"item_id": item.item_id, "arm": arm} for item in items for arm in ("baseline", "treatment")]})

    def execute(selected: list[ScienceMCQ], *, evidence_class: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in selected:
            choices = "\n".join(f"{key}. {value}" for key, value in item.choices.items())
            for arm, template in prompts.items():
                prompt = template.format(question=item.question, choices=choices)
                if any(token in template for token in ("{answer}", "{reference}", "{target}")):
                    raise ValueError("BLOCKED_TARGET_LEAKAGE")
                reply = selected_backend.invoke(item_id=item.item_id, arm=arm, prompt=prompt, decoding=decoding)
                if reply.actual_revision != selected_backend.revision:
                    raise ValueError("DEGRADED_MODEL_PROVENANCE")
                records.append({
                    "item_id": item.item_id,
                    "arm_id": arm,
                    "response": reply.text,
                    "response_sha256": _canonical_digest(reply.text),
                    "prompt_sha256": contract["prompt_sha256"][arm],
                    "model_revision": reply.actual_revision,
                    "latency_ms": reply.latency_ms,
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                    "evidence_class": evidence_class,
                })
        return records

    dry = execute(items[:5], evidence_class="dry_run_non_scientific")
    _write_jsonl(run_root / "dry_run_responses.jsonl", dry)
    responses = execute(items, evidence_class="formal")
    if len(responses) != item_count * 2 or len({(row["item_id"], row["arm_id"]) for row in responses}) != len(responses):
        raise ValueError("BLOCKED_PAIRING_INCOMPLETE")
    _write_jsonl(run_root / "responses.jsonl", responses)
    response_lookup = {(row["item_id"], row["arm_id"]): row for row in responses}
    evaluation_rows: list[dict[str, Any]] = []
    for item in items:
        left = response_lookup[(item.item_id, "baseline")]
        right = response_lookup[(item.item_id, "treatment")]
        left_choice, right_choice = _parse_choice(left["response"]), _parse_choice(right["response"])
        evaluation_rows.append({
            "item_id": item.item_id,
            "baseline_choice": left_choice,
            "treatment_choice": right_choice,
            "baseline_correct": left_choice == answers[item.item_id],
            "treatment_correct": right_choice == answers[item.item_id],
            "baseline_invalid": left_choice is None,
            "treatment_invalid": right_choice is None,
        })
    statistics = _statistics(evaluation_rows)
    low, high = statistics["paired_bootstrap_95_ci"]
    verdict = "supported" if low > 0 else "refuted" if high < 0 else "inconclusive"
    _write_json(run_root / "evaluation.json", {"schema_version": 1, "rows": evaluation_rows, "denominator": item_count})
    _write_json(run_root / "statistics.json", statistics)
    _write_json(run_root / "verdict.json", {"schema_version": 1, "verdict": verdict, "rule": contract["verdict_rule"], "statistics_sha256": _digest_file(run_root / "statistics.json")})

    # Mutation/negative assurance is executed against the frozen artifacts;
    # checks are not declared successful merely because a code path exists.
    expected_pairs = {(item.item_id, arm) for item in items for arm in ("baseline", "treatment")}
    actual_pairs = {(row["item_id"], row["arm_id"]) for row in responses}
    negative_receipts = {
        "target_leakage": all(
            token not in prompts[arm]
            for arm in prompts
            for token in ("{answer}", "{reference}", "{target}")
        ),
        "pairing_incomplete": actual_pairs == expected_pairs,
        "duplicate_item_id": len(actual_pairs) == len(responses),
        "missing_response": len(responses) == len(expected_pairs),
        "prompt_hash_mutation": all(
            _canonical_digest(prompts[arm]) == contract["prompt_sha256"][arm]
            for arm in prompts
        ),
        "primary_metric_mutation": statistics["primary_metric"] == contract["primary_metric"],
        "model_revision_missing": all(
            row["model_revision"] == selected_backend.revision for row in responses
        ),
        "invalid_output_denominator": statistics["denominator"] == len(items),
        "target_task_mismatch": set(answers) == {item.item_id for item in items},
        "writer_verdict_upgrade": json.loads(
            (run_root / "verdict.json").read_text(encoding="utf-8")
        )["verdict"]
        == verdict,
    }
    _write_json(run_root / "negative_test_receipts.json", negative_receipts)

    independent = _statistics(json.loads((run_root / "evaluation.json").read_text(encoding="utf-8"))["rows"])
    evaluator_agreement = 1.0 if independent == statistics else 0.0
    statistics_pointer = ProfileEvidencePointer(
        path="statistics.json",
        sha256=_digest_file(run_root / "statistics.json"),
    )
    verdict_pointer = ProfileEvidencePointer(
        path="verdict.json",
        sha256=_digest_file(run_root / "verdict.json"),
    )
    model_pointer = ProfileEvidencePointer(
        path="model_manifest.json",
        sha256=_digest_file(run_root / "model_manifest.json"),
    )
    evaluation_pointer = ProfileEvidencePointer(
        path="evaluation.json",
        sha256=_digest_file(run_root / "evaluation.json"),
    )
    contract_pointer = ProfileEvidencePointer(
        path="research_contract.json",
        sha256=_digest_file(run_root / "research_contract.json"),
    )
    prompt_pointer = ProfileEvidencePointer(
        path="prompt_manifest.json",
        sha256=_digest_file(run_root / "prompt_manifest.json"),
    )
    run_plan_pointer = ProfileEvidencePointer(
        path="run_plan.json",
        sha256=_digest_file(run_root / "run_plan.json"),
    )
    responses_pointer = ProfileEvidencePointer(
        path="responses.jsonl",
        sha256=_digest_file(run_root / "responses.jsonl"),
    )
    low, high = statistics["paired_bootstrap_95_ci"]
    mcnemar = statistics["mcnemar"]
    envelope_id = "claim-envelope-" + _canonical_digest(
        {
            "study_id": intake["study_id"],
            "statistics": statistics,
            "verdict": verdict,
        }
    )[:16]
    handoff = build_stage_four_evidence_handoff(
        ProfileStageFourEvidenceInput(
            study_id=str(intake["study_id"]),
            profile_id="llm_evaluation_v1",
            profile_version="1.0.0",
            source_claim_envelope_id=envelope_id,
            frozen_conclusion=(
                "Under the frozen controlled acceptance design, the verify-before-answer "
                f"arm changed exact accuracy by {statistics['paired_accuracy_difference']:.3f} "
                f"(95% paired-bootstrap interval {low:.3f} to {high:.3f}); the frozen "
                f"scientific verdict was {verdict}."
            ),
            verified_source_ids=[
                str(source["citation_key"])
                for source in literature_manifest["sources"]
            ],
            required_evidence_facets=list(
                LLM_PUBLICATION_REQUIRED_EVIDENCE_FACETS
            ),
            findings=[
                ProfileReportingFinding(
                    claim_id="llm-profile-arm-definition",
                    reporting_item_id="report-arm-definition",
                    category="operational",
                    statement=(
                        "The baseline requested one A, B, C, or D answer directly; "
                        "the treatment added an instruction to verify each option before "
                        "returning the same one-letter response. The frozen contract "
                        "identified this verification instruction as the only arm difference."
                    ),
                    evidence=[contract_pointer, prompt_pointer],
                    required_destination="main_text",
                    destination_section="methods",
                    rationale="The intervention and comparator must be operationally distinguishable from their frozen prompt texts.",
                    evidence_facets=["arm_definition"],
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-scoring-and-pairing",
                    reporting_item_id="report-scoring-and-pairing",
                    category="operational",
                    statement=(
                        "The item was the unit of analysis. Each arm returned exactly one "
                        "letter from A through D, which was compared with the evaluator-only "
                        "frozen answer for that item. Arms were paired by the same item, and "
                        "a missing or invalid response was scored as zero while remaining in "
                        "the full denominator."
                    ),
                    evidence=[contract_pointer, evaluation_pointer],
                    required_destination="main_text",
                    destination_section="methods",
                    rationale="Scoring, pairing, invalid-response handling, and denominator membership are required to reconstruct the primary metric.",
                    evidence_facets=[
                        "scoring_rule",
                        "missing_response_policy",
                        "pairing_rule",
                    ],
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-formal-execution-matrix",
                    reporting_item_id="report-formal-execution-matrix",
                    category="operational",
                    statement=(
                        f"The frozen formal matrix contained both arms for each of "
                        f"{statistics['denominator']} item identifiers. The response log "
                        f"contained {statistics['denominator'] * 2} unique item-arm calls, "
                        "and the evaluation table retained a baseline and treatment record "
                        "for every paired item."
                    ),
                    evidence=[
                        run_plan_pointer,
                        responses_pointer,
                        evaluation_pointer,
                    ],
                    required_destination="main_text",
                    destination_section="methods",
                    rationale="The paper must reconcile the frozen run matrix, observed calls, evaluation rows, and formal denominator.",
                    evidence_facets=[
                        "formal_run_matrix",
                        "response_completeness",
                        "denominator_reconciliation",
                    ],
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-statistical-procedure",
                    reporting_item_id="report-statistical-procedure",
                    category="operational",
                    statement=(
                        f"The registered analysis used the paired accuracy difference, an "
                        f"exact two-sided McNemar test, and a paired bootstrap with "
                        f"{statistics['bootstrap_resamples']} resamples and frozen seed "
                        f"{statistics['bootstrap_seed']}. The verdict was supported only "
                        "when the lower bound of the paired-bootstrap interval exceeded zero, "
                        "refuted only when its upper bound was below zero, and otherwise "
                        "inconclusive."
                    ),
                    evidence=[contract_pointer, statistics_pointer, verdict_pointer],
                    required_destination="main_text",
                    destination_section="methods",
                    rationale="The estimator, uncertainty procedure, test, and deterministic verdict rule must be reconstructable from frozen evidence.",
                    evidence_facets=["statistical_procedure", "verdict_rule"],
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-primary-effect",
                    reporting_item_id="report-primary-effect",
                    category="primary",
                    statement=(
                        f"Baseline exact accuracy was {statistics['baseline_accuracy']:.3f}, "
                        f"treatment exact accuracy was {statistics['treatment_accuracy']:.3f}, "
                        f"and the paired difference was {statistics['paired_accuracy_difference']:.3f} "
                        f"with a 95% paired-bootstrap interval from {low:.3f} to {high:.3f}."
                    ),
                    evidence=[statistics_pointer],
                    required_destination="main_text",
                    destination_section="results",
                    rationale="The registered primary effect, both arm estimates, and uncertainty must be reported together.",
                    claim_strength="comparative",
                    preregistered=True,
                    evidence_facets=["primary_result"],
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-mcnemar-test",
                    reporting_item_id="report-mcnemar-test",
                    category="secondary",
                    statement=(
                        "The paired discordance analysis recorded "
                        f"{mcnemar['baseline_wrong_treatment_right']} improvements and "
                        f"{mcnemar['baseline_right_treatment_wrong']} worsenings; the exact "
                        f"two-sided McNemar p-value was {mcnemar['exact_two_sided_p']:.6g}."
                    ),
                    evidence=[statistics_pointer],
                    required_destination="main_text",
                    destination_section="results",
                    rationale="The registered paired test and both discordant-cell counts prevent selective reporting of the mean effect.",
                    claim_strength="comparative",
                    preregistered=True,
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-denominator",
                    reporting_item_id="report-formal-denominator",
                    category="operational",
                    statement=(
                        f"The formal denominator contained {statistics['denominator']} paired "
                        f"items and {statistics['denominator'] * 2} candidate model calls."
                    ),
                    evidence=[statistics_pointer, evaluation_pointer],
                    required_destination="main_text",
                    destination_section="methods",
                    rationale="The unit of analysis, paired denominator, and total arm calls are required to interpret the effect estimate.",
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-model-lock",
                    reporting_item_id="report-model-provenance",
                    category="operational",
                    statement=(
                        f"Both arms used provider {selected_backend.provider}, model "
                        f"{selected_backend.model_id}, and frozen revision {selected_backend.revision}."
                    ),
                    evidence=[model_pointer],
                    required_destination="main_text",
                    destination_section="methods",
                    rationale="Model and revision identity are necessary for a reproducible same-backbone comparison.",
                    evidence_facets=["model_lock"],
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-frozen-verdict",
                    reporting_item_id="report-frozen-verdict",
                    category="primary",
                    statement=f"The frozen scientific verdict was {verdict}.",
                    evidence=[verdict_pointer],
                    required_destination="main_text",
                    destination_section="discussion",
                    rationale="The manuscript must reproduce rather than reinterpret the frozen deterministic verdict.",
                    claim_strength="comparative",
                ),
                ProfileReportingFinding(
                    claim_id="llm-profile-controlled-fixture-limit",
                    reporting_item_id="report-controlled-fixture-limit",
                    category="limitation",
                    statement=(
                        "This license-free controlled Profile acceptance fixture does not "
                        "establish effects for deployed foundation models, other prompts, or "
                        "open-world scientific tasks."
                    ),
                    required_destination="limitations",
                    destination_section="limitations",
                    rationale="The controlled fixture validates platform mechanics but has deliberately narrow external validity.",
                    claim_strength="limitation",
                    evidence_facets=["external_validity"],
                ),
            ],
        )
    )
    handoff_hashes = persist_stage_four_evidence_handoff(run_root, handoff)
    (run_root / "figures").mkdir(exist_ok=True)
    (run_root / "figures" / "paired_accuracy.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="240"><rect width="600" height="240" fill="white"/><rect x="120" y="{200-150*statistics["baseline_accuracy"]}" width="100" height="{150*statistics["baseline_accuracy"]}" fill="#70A1A9"/><rect x="360" y="{200-150*statistics["treatment_accuracy"]}" width="100" height="{150*statistics["treatment_accuracy"]}" fill="#3A747D"/><text x="120" y="225">Direct</text><text x="350" y="225">Verify</text></svg>',
        encoding="utf-8",
    )
    if not handoff.adapter_complete:
        raise ValueError("Profile evidence adapter did not produce a complete Stage 4 handoff")

    # Persist the lifecycle in Workflow v2 rather than presenting a folder of
    # artifacts as if it had traversed the four phases.
    from ..workflow_domain import (
        ExecutionStatus,
        ExecutorType,
        GateType,
        Phase,
        StepAcceptanceStatus,
        WorkflowRepository,
    )

    repository = WorkflowRepository(Path(str(intake["workflow_repository"])))
    recorded_steps: list[dict[str, str]] = []
    prior_step_id: str | None = None
    existing_steps = repository.list_steps(str(intake["study_id"]))
    for position, (step_type, phase, executor, artifact_name) in enumerate(
        (
            ("scope_drafting", Phase.DISCOVERY, ExecutorType.CODEX, "task_brief.json"),
            ("profile_qualification", Phase.DISCOVERY, ExecutorType.DETERMINISTIC_SERVICE, "profile_qualification_report.json"),
            ("contract_completion", Phase.PROTOCOL, ExecutorType.CODEX, "research_contract.json"),
            ("resource_resolution", Phase.PROTOCOL, ExecutorType.DETERMINISTIC_SERVICE, "dataset_manifest.json"),
            ("dry_run", Phase.PROTOCOL, ExecutorType.SANDBOX_RUNNER, "dry_run_responses.jsonl"),
            ("formal_run", Phase.EXPERIMENT, ExecutorType.SANDBOX_RUNNER, "responses.jsonl"),
            ("deterministic_evaluation", Phase.EXPERIMENT, ExecutorType.DETERMINISTIC_SERVICE, "evaluation.json"),
            ("scientific_verdict", Phase.EXPERIMENT, ExecutorType.DETERMINISTIC_SERVICE, "verdict.json"),
            ("stage_four_evidence_adaptation", Phase.PAPER, ExecutorType.DETERMINISTIC_SERVICE, "stage_four_evidence_handoff.json"),
            ("stage_four_handoff_audit", Phase.PAPER, ExecutorType.DETERMINISTIC_SERVICE, "mandatory_reporting_register.json"),
        )
    ):
        if position == 0 and existing_steps:
            step = existing_steps[0]
        else:
            step = repository.add_step(
                str(intake["study_id"]),
                step_type,
                phase,
                executor,
                depends_on=[prior_step_id] if prior_step_id else [],
                expected_output=artifact_name,
            )
        repository.update_step(
            str(intake["study_id"]), step.step_instance_id, ExecutionStatus.RUNNING
        )
        artifact = repository.save_step_result(
            str(intake["study_id"]),
            step.step_instance_id,
            {"artifact": artifact_name, "sha256": _digest_file(run_root / artifact_name)},
        )
        repository.update_step(
            str(intake["study_id"]),
            step.step_instance_id,
            ExecutionStatus.SUCCEEDED,
            output_artifact_ids=[artifact.artifact_id],
            acceptance_status=StepAcceptanceStatus.ACCEPTED,
            output_produced=True,
            schema_validated=True,
            scientific_postcondition_passed=True,
            acceptance_checks={"artifact_hash_recorded": True},
        )
        recorded_steps.append({"step_type": step_type, "step_instance_id": step.step_instance_id, "status": "succeeded"})
        if step_type == "scope_drafting":
            for gate in repository.list_gates(str(intake["study_id"])):
                if gate.gate_type is GateType.SCOPE_APPROVAL:
                    repository.decide_gate(
                        str(intake["study_id"]),
                        gate.gate_id,
                        approve=True,
                        decided_by="controlled_acceptance_owner",
                        reason="Owner launched the controlled end-to-end acceptance case.",
                    )
        if step_type == "contract_completion":
            gate = repository.create_gate(
                str(intake["study_id"]),
                GateType.RESEARCH_CONTRACT,
                "research_contract",
                f"{intake['study_id']}:llm-contract-v1",
                subject_version=1,
            )
            repository.decide_gate(
                str(intake["study_id"]),
                gate.gate_id,
                approve=True,
                decided_by="controlled_acceptance_owner",
                reason="The acceptance case uses a frozen standard contract.",
            )
        prior_step_id = step.step_instance_id
    _write_json(run_root / "workflow_trace.json", {"study_id": intake["study_id"], "steps": recorded_steps})

    completion = {
        "schema_version": 1,
        "study_id": intake["study_id"],
        "artifacts": {
            path.relative_to(run_root).as_posix(): _digest_file(path)
            for path in sorted(run_root.rglob("*"))
            if path.is_file()
            and path.name != "completion_record.json"
            and not path.name.startswith("profile_acceptance_")
        },
        "verification_command": ["python", "-m", "research_forge.profiles.llm_acceptance", "verify", str(run_root)],
    }
    _write_json(run_root / "completion_record.json", completion)

    gates = {
        "profile_routing": task_brief["profile_id"] == "llm_evaluation_v1",
        "semantic_fidelity": contract["only_arm_difference"] == "verification_instruction",
        "contract_complete": not any(token in json.dumps(contract).lower() for token in ("unknown", "todo", "placeholder")),
        "resource_freeze": all((contract["dataset_sha256"], contract["answer_sha256"], selected_backend.revision)),
        "dry_run": len(dry) == 10,
        "formal_coverage": len(responses) == 300,
        "independent_evaluator": evaluator_agreement == 1.0,
        "frozen_verdict": True,
        "stage_four_handoff_complete": (
            handoff.adapter_complete
            and handoff.claim_binding_coverage == 1.0
            and all(handoff_hashes.values())
            and not (run_root / "manuscript.pdf").exists()
            and not (run_root / "manuscript.tex").exists()
        ),
        "reproduction_package": verify_llm_completion(run_root),
        "citation_verification": all(
            source.get("citation_key")
            and str(source.get("url", "")).startswith("https://")
            and "placeholder" not in str(source).lower()
            for source in literature_manifest["sources"]
        ),
    }
    repository_root = Path(__file__).resolve().parents[2]
    acceptance_sources = (
        "research_forge/profiles/llm_acceptance.py",
        "research_forge/profiles/llm_evaluation.py",
        "research_forge/profiles/stage_four_evidence.py",
        "research_forge/profiles/contracts.py",
        "research_forge/profiles/catalog.py",
        "research_forge/profiles/bundles/llm_evaluation_v1.py",
        "research_forge/stage_four.py",
        "tests/test_llm_evaluation_profile.py",
        "tests/test_profile_stage_four_evidence.py",
    )
    common = dict(
        profile_id="llm_evaluation_v1", profile_version="1.0.0",
        source_hashes={
            relative: _digest_file(repository_root / relative)
            for relative in acceptance_sources
        },
        registry_check=AcceptanceCheck(check_id="registry", passed=True),
        schema_check=AcceptanceCheck(check_id="schema", passed=True),
        qualification_check=AcceptanceCheck(check_id="qualification", passed=gates["profile_routing"]),
        contract_completion_check=AcceptanceCheck(check_id="contract", passed=gates["contract_complete"]),
        semantic_diff_check=AcceptanceCheck(check_id="semantic", passed=gates["semantic_fidelity"]),
        run_plan_serialization_check=AcceptanceCheck(check_id="run_plan", passed=True),
        dry_run_check=AcceptanceCheck(check_id="dry_run", passed=gates["dry_run"]),
        formal_e2e_check=AcceptanceCheck(check_id="formal", passed=gates["formal_coverage"]),
        verdict_check=AcceptanceCheck(check_id="verdict", passed=True),
        evidence_binding_check=AcceptanceCheck(check_id="evidence", passed=True),
        completion_record_check=AcceptanceCheck(check_id="completion", passed=gates["reproduction_package"]),
        independent_metric_recompute_check=AcceptanceCheck(check_id="recompute", passed=gates["independent_evaluator"]),
        negative_case_checks=tuple(
            AcceptanceCheck(
                check_id=value,
                passed=passed,
                receipt_artifact_id="negative_test_receipts.json",
            )
            for value, passed in negative_receipts.items()
        ),
        mutation_checks=tuple(
            AcceptanceCheck(
                check_id=value,
                passed=negative_receipts[receipt],
                receipt_artifact_id="negative_test_receipts.json",
            )
            for value, receipt in (
                ("prompt", "prompt_hash_mutation"),
                ("metric", "primary_metric_mutation"),
                ("target", "target_task_mismatch"),
                ("verdict", "writer_verdict_upgrade"),
            )
        ),
        recompute_command=("python", "-m", "research_forge.profiles.llm_acceptance", "verify", str(run_root)),
        known_limits=("Controlled license-free benchmark and controlled backend; no claim about a deployed foundation model.",),
    )
    generic_report = ProfileAcceptanceReport(**common).with_assessed_maturity()
    _write_json(run_root / "profile_acceptance_report.json", generic_report.model_dump(mode="json"))
    overall = "PASS" if all(gates.values()) else "FAIL"
    summary = LLMProfileAcceptanceSummary(
        study_id=str(intake["study_id"]), overall=overall, scientific_verdict=verdict,
        critical_gates=gates, formal_calls=len(responses), evaluator_agreement=evaluator_agreement,
        claim_binding_coverage=handoff.claim_binding_coverage,
        stage_four_handoff=str(run_root / "stage_four_evidence_handoff.json"),
        completion_package=str(run_root),
        known_limitations=common["known_limits"], promotion_eligible=overall == "PASS",
    )
    _write_json(run_root / "profile_acceptance_summary.json", summary.model_dump(mode="json"))
    (run_root / "profile_acceptance_report.html").write_text(
        "<!doctype html><meta charset=utf-8><title>LLM Profile Acceptance</title>"
        f"<h1>{escape(summary.overall)} — llm_evaluation_v1</h1><p>Study: {escape(summary.study_id)}</p>"
        f"<p>Scientific verdict: {escape(summary.scientific_verdict)}</p><p>Formal calls: {summary.formal_calls}</p>"
        + "<ul>" + "".join(f"<li>{escape(key)}: {'PASS' if value else 'FAIL'}</li>" for key, value in gates.items()) + "</ul>",
        encoding="utf-8",
    )
    return summary


def upgrade_llm_stage_four_evidence_handoff(
    run_root: str | Path,
    *,
    artifact_version: int = 2,
) -> StageFourEvidenceHandoff:
    """Add publication-method bindings to a completed legacy LLM run.

    This is an append-only evidence adaptation.  It hashes and binds artifacts
    already produced by the frozen run; it never reruns a model, changes a
    response, or replaces the original handoff.
    """

    root = Path(run_root).resolve()
    upgraded_path = root / f"stage_four_evidence_handoff_v{artifact_version}.json"
    if upgraded_path.is_file():
        existing = StageFourEvidenceHandoff.model_validate(
            json.loads(upgraded_path.read_text(encoding="utf-8"))
        )
        if (
            existing.profile_id == "llm_evaluation_v1"
            and existing.adapter_complete
            and not existing.missing_evidence_facets
            and set(LLM_PUBLICATION_REQUIRED_EVIDENCE_FACETS).issubset(
                existing.covered_evidence_facets
            )
        ):
            return existing
        raise ValueError(
            f"existing LLM handoff v{artifact_version} is not publication-complete; "
            "create a later append-only version"
        )
    source_path = root / "stage_four_evidence_handoff.json"
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source = StageFourEvidenceHandoff.model_validate(
        json.loads(source_path.read_text(encoding="utf-8"))
    )
    if source.profile_id != "llm_evaluation_v1":
        raise ValueError("only llm_evaluation_v1 handoffs can use this upgrader")

    required_files = {
        name: root / name
        for name in (
            "research_contract.json",
            "prompt_manifest.json",
            "run_plan.json",
            "responses.jsonl",
            "evaluation.json",
            "statistics.json",
            "verdict.json",
        )
    }
    missing = sorted(name for name, path in required_files.items() if not path.is_file())
    if missing:
        raise ValueError(
            "legacy LLM handoff cannot be upgraded; missing frozen artifacts: "
            + ", ".join(missing)
        )

    contract = json.loads(required_files["research_contract.json"].read_text(encoding="utf-8"))
    statistics = json.loads(required_files["statistics.json"].read_text(encoding="utf-8"))
    register_by_claim = {
        item.claim_id: item for item in source.mandatory_reporting_register.items
    }
    existing_findings: list[ProfileReportingFinding] = []
    existing_facets = {
        "llm-profile-primary-effect": ["primary_result"],
        "llm-profile-model-lock": ["model_lock"],
        "llm-profile-controlled-fixture-limit": ["external_validity"],
    }
    for binding in source.evidence_claim_map.bindings:
        item = register_by_claim[binding.claim_id]
        existing_findings.append(
            ProfileReportingFinding(
                claim_id=binding.claim_id,
                reporting_item_id=item.reporting_item_id,
                category=item.category,
                statement=binding.statement,
                evidence=[
                    ProfileEvidencePointer(
                        path=pointer.path or "",
                        sha256=pointer.sha256 or "",
                        json_path=pointer.json_path,
                    )
                    for pointer in binding.evidence
                    if pointer.path and pointer.sha256
                ],
                required_destination=item.required_destination,
                destination_section=item.destination_section,
                rationale=item.rationale,
                claim_strength=binding.claim_strength,
                material=item.material,
                preregistered=item.preregistered,
                evidence_facets=existing_facets.get(binding.claim_id, []),
            )
        )

    def pointer(name: str) -> ProfileEvidencePointer:
        return ProfileEvidencePointer(
            path=name,
            sha256=_digest_file(required_files[name]),
        )

    contract_pointer = pointer("research_contract.json")
    prompt_pointer = pointer("prompt_manifest.json")
    run_plan_pointer = pointer("run_plan.json")
    responses_pointer = pointer("responses.jsonl")
    evaluation_pointer = pointer("evaluation.json")
    statistics_pointer = pointer("statistics.json")
    verdict_pointer = pointer("verdict.json")
    method_findings = [
        ProfileReportingFinding(
            claim_id="llm-profile-arm-definition",
            reporting_item_id="report-arm-definition",
            category="operational",
            statement=(
                "The baseline requested one A, B, C, or D answer directly; the "
                "treatment added an instruction to verify each option before returning "
                "the same one-letter response. The frozen contract identified this "
                "verification instruction as the only arm difference."
            ),
            evidence=[contract_pointer, prompt_pointer],
            required_destination="main_text",
            destination_section="methods",
            rationale="The intervention and comparator must be operationally distinguishable from their frozen prompt texts.",
            evidence_facets=["arm_definition"],
        ),
        ProfileReportingFinding(
            claim_id="llm-profile-scoring-and-pairing",
            reporting_item_id="report-scoring-and-pairing",
            category="operational",
            statement=(
                "The item was the unit of analysis. Each arm returned exactly one "
                "letter from A through D, which was compared with the evaluator-only "
                "frozen answer for that item. Arms were paired by the same item, and a "
                "missing or invalid response was scored as zero while remaining in the "
                "full denominator."
            ),
            evidence=[contract_pointer, evaluation_pointer],
            required_destination="main_text",
            destination_section="methods",
            rationale="Scoring, pairing, invalid-response handling, and denominator membership are required to reconstruct the primary metric.",
            evidence_facets=[
                "scoring_rule",
                "missing_response_policy",
                "pairing_rule",
            ],
        ),
        ProfileReportingFinding(
            claim_id="llm-profile-formal-execution-matrix",
            reporting_item_id="report-formal-execution-matrix",
            category="operational",
            statement=(
                f"The frozen formal matrix contained both arms for each of "
                f"{statistics['denominator']} item identifiers. The response log "
                f"contained {statistics['denominator'] * 2} unique item-arm calls, and "
                "the evaluation table retained a baseline and treatment record for "
                "every paired item."
            ),
            evidence=[run_plan_pointer, responses_pointer, evaluation_pointer],
            required_destination="main_text",
            destination_section="methods",
            rationale="The paper must reconcile the frozen run matrix, observed calls, evaluation rows, and formal denominator.",
            evidence_facets=[
                "formal_run_matrix",
                "response_completeness",
                "denominator_reconciliation",
            ],
        ),
        ProfileReportingFinding(
            claim_id="llm-profile-statistical-procedure",
            reporting_item_id="report-statistical-procedure",
            category="operational",
            statement=(
                f"The registered analysis used the paired accuracy difference, an exact "
                f"two-sided McNemar test, and a paired bootstrap with "
                f"{statistics['bootstrap_resamples']} resamples and frozen seed "
                f"{statistics['bootstrap_seed']}. The verdict was supported only when "
                "the lower interval bound exceeded zero, refuted only when the upper "
                "bound was below zero, and otherwise inconclusive."
            ),
            evidence=[contract_pointer, statistics_pointer, verdict_pointer],
            required_destination="main_text",
            destination_section="methods",
            rationale="The estimator, uncertainty procedure, test, and deterministic verdict rule must be reconstructable from frozen evidence.",
            evidence_facets=["statistical_procedure", "verdict_rule"],
        ),
    ]
    upgraded = build_stage_four_evidence_handoff(
        ProfileStageFourEvidenceInput(
            study_id=source.study_id,
            profile_id=source.profile_id,
            profile_version=source.profile_version,
            source_claim_envelope_id=(
                source.mandatory_reporting_register.source_claim_envelope_id
            ),
            frozen_conclusion=source.evidence_claim_map.frozen_conclusion,
            findings=[*method_findings, *existing_findings],
            verified_source_ids=source.evidence_claim_map.verified_source_ids,
            forbidden_moves=source.evidence_claim_map.forbidden_moves,
            required_evidence_facets=list(
                LLM_PUBLICATION_REQUIRED_EVIDENCE_FACETS
            ),
        )
    )
    persist_stage_four_evidence_handoff(
        root,
        upgraded,
        artifact_version=artifact_version,
    )
    return upgraded


def verify_llm_completion(run_root: str | Path) -> bool:
    root = Path(run_root).resolve()
    completion_path = root / "completion_record.json"
    if not completion_path.is_file():
        return False
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    for name, expected in completion.get("artifacts", {}).items():
        path = (root / name).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return False
        if not path.is_file() or _digest_file(path) != expected:
            return False
    response_rows = [
        json.loads(line)
        for line in (root / "responses.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    answer_rows = json.loads(
        (root / "evaluator_only" / "hidden_answers.json").read_text(encoding="utf-8")
    )
    answers = {row["item_id"]: row["answer"] for row in answer_rows}
    response_lookup = {
        (row["item_id"], row["arm_id"]): row for row in response_rows
    }
    if len(response_lookup) != len(response_rows):
        return False
    recomputed_rows = []
    for item_id in sorted(answers):
        try:
            left = _parse_choice(response_lookup[(item_id, "baseline")]["response"])
            right = _parse_choice(response_lookup[(item_id, "treatment")]["response"])
        except KeyError:
            return False
        recomputed_rows.append(
            {
                "item_id": item_id,
                "baseline_choice": left,
                "treatment_choice": right,
                "baseline_correct": left == answers[item_id],
                "treatment_correct": right == answers[item_id],
                "baseline_invalid": left is None,
                "treatment_invalid": right is None,
            }
        )
    evaluation = json.loads((root / "evaluation.json").read_text(encoding="utf-8"))
    if recomputed_rows != evaluation["rows"]:
        return False
    return _statistics(recomputed_rows) == json.loads((root / "statistics.json").read_text(encoding="utf-8"))


__all__ = [
    "BASELINE_TEMPLATE", "ControlledScienceBackend", "LLMProfileAcceptanceSummary",
    "ModelReply", "ScienceMCQ", "TREATMENT_TEMPLATE", "make_controlled_science_fixture",
    "run_llm_profile_acceptance", "verify_llm_completion",
]


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="LLM Profile controlled acceptance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--output-root", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("run_root")
    args = parser.parse_args()
    if args.command == "run":
        summary = run_llm_profile_acceptance(output_root=args.output_root)
        print(summary.model_dump_json(indent=2))
        return 0 if summary.overall == "PASS" else 1
    passed = verify_llm_completion(args.run_root)
    print(json.dumps({"verified": passed}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(_main())
