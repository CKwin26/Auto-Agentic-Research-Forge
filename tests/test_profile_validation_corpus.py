from __future__ import annotations

from research_forge.profile_validation_corpus import profile_validation_cases


def test_validation_corpus_is_ten_independent_profile_pipelines() -> None:
    cases = profile_validation_cases()
    assert len(cases) == 10
    assert len({item.profile_id for item in cases}) == 10
    assert len({item.directory_name for item in cases}) == 10
    assert [item.ordinal for item in cases] == list(range(1, 11))
    assert {item.runner_kwargs.get("acceptance_variant") for item in cases} >= {
        "superiority",
        "equivalence",
        "bayesian",
        "multiplicity",
    }


def test_suite_report_is_an_index_not_a_combined_manuscript() -> None:
    cases = profile_validation_cases()
    assert all("paper" not in item.directory_name for item in cases)
    assert all(item.natural_language_name for item in cases)
