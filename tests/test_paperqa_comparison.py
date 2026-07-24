from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_paperqa_comparison.py"
SPEC = importlib.util.spec_from_file_location("paperqa_comparison", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_term_group_coverage_is_explicit_not_semantic() -> None:
    scored = MODULE.term_group_coverage(
        "AKT was inhibited with MK2206; STAT3 was also measured.",
        [["AKT", "MK2206"], ["STAT3", "pyridone 6"]],
    )

    assert scored == {
        "groups": 2,
        "hits": 1,
        "hit_vector": [True, False],
        "coverage": 0.5,
    }


def test_abstention_detection_requires_explicit_uncertainty() -> None:
    assert MODULE._is_abstention("No specific phase III trial can be named.")
    assert not MODULE._is_abstention("The intervention improved survival.")


def test_case_fixture_is_valid() -> None:
    cases = MODULE._load_cases(
        Path(__file__).parent / "fixtures" / "paperqa-comparison-cases.json"
    )

    assert len(cases) == 4
    assert cases[-1]["expected_answerable"] is False


def test_v2_suite_covers_single_multi_document_and_attacks() -> None:
    cases = MODULE._load_cases(
        Path(__file__).parent
        / "fixtures"
        / "paperqa-comparison-suite-v2.json"
    )

    assert len(cases) == 16
    assert {item["category"] for item in cases} == {
        "single_document",
        "multi_document",
        "evidence_attack",
    }
    attack_types = {
        item["attack_type"] for item in cases if item["category"] == "evidence_attack"
    }
    assert {
        "prompt_injection_in_evidence",
        "internal_identifier_leak",
        "out_of_scope_inference",
        "incorrect_page_binding",
        "snapshot_replacement",
    }.issubset(attack_types)


def test_percentiles_are_reported_without_external_statistics_package() -> None:
    values = [1.0, 2.0, 3.0, 4.0]

    assert MODULE._percentile(values, 0.50) == 2.5
    assert MODULE._percentile(values, 0.90) == pytest.approx(3.7)
    assert MODULE._percentile([], 0.99) is None


def test_full_answer_comparison_claim_requires_the_same_model() -> None:
    assert MODULE.full_answer_claim_policy("model-a", "model-a") == {
        "same_answer_model": True,
        "fair_end_to_end_comparison": True,
        "claim_ceiling": "paired end-to-end comparison",
    }
    assert MODULE.full_answer_claim_policy("model-a", "model-b") == {
        "same_answer_model": False,
        "fair_end_to_end_comparison": False,
        "claim_ceiling": "diagnostic only; answer models differ",
    }
