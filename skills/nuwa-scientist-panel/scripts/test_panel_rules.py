#!/usr/bin/env python3
"""Unit tests for the deterministic panel aggregation rules."""

from __future__ import annotations

import unittest

from aggregate_reviews import aggregate


def vote(verdict: str, *modes: str) -> dict[str, object]:
    return {"verdict": verdict, "failure_modes": list(modes)}


class AggregationTests(unittest.TestCase):
    def test_hard_veto_overrides_two_supports(self) -> None:
        actual = aggregate(
            [
                vote("supported"),
                vote("supported"),
                vote("unsupported", "missing_evidence"),
            ],
            {"missing_evidence"},
        )
        self.assertEqual(actual, ("unsupported", ["missing_evidence"]))

    def test_two_unsupported_votes_win_without_veto(self) -> None:
        actual = aggregate(
            [
                vote("unsupported", "overgeneralization"),
                vote("unsupported", "unsupported_novelty"),
                vote("supported"),
            ],
            {"missing_evidence"},
        )
        self.assertEqual(actual, ("unsupported", []))

    def test_one_soft_unsupported_vote_forces_abstention(self) -> None:
        actual = aggregate(
            [
                vote("unsupported", "overgeneralization"),
                vote("supported"),
                vote("supported"),
            ],
            {"missing_evidence"},
        )
        self.assertEqual(actual, ("abstain", []))

    def test_two_supported_votes_win(self) -> None:
        actual = aggregate(
            [vote("supported"), vote("supported"), vote("abstain")],
            {"missing_evidence"},
        )
        self.assertEqual(actual, ("supported", []))

    def test_no_majority_abstains(self) -> None:
        actual = aggregate(
            [vote("supported"), vote("abstain"), vote("abstain")],
            {"missing_evidence"},
        )
        self.assertEqual(actual, ("abstain", []))


if __name__ == "__main__":
    unittest.main()
