# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the scoring engine. Run with ./unit-test.sh."""

import json
import pathlib
import sys
import unittest
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import scoring  # noqa: E402

Row = dict[str, Any]
BANDS = {"bands": [{"min": 0, "max": 1, "points": 0.4}, {"min": 2, "max": 250, "points": 1.0}, {"min": 251, "max": None, "points": 0.3}]}
CHOICES = {"choices": [{"code": "NOBODY", "points": 1.0}, {"code": "AGENCY", "points": 0.3}, {"code": "IN_HOUSE", "points": 0}]}


def criterion(id: str, answer_type: str = "YES_NO", weight: int = 1, service: str | None = None, config: Row | None = None, active: int = 1) -> Row:
    return {
        "id": id,
        "section": "NEED" if service else "FIT",
        "service_id": service,
        "answer_type": answer_type,
        "weight": weight,
        "config_json": json.dumps(config) if config else None,
        "active": active,
    }


def service(id: str, offered: int = 1, active: int = 1) -> Row:
    return {"id": id, "offered": offered, "active": active}


class Points(unittest.TestCase):
    def test_the_points_table(self):
        cases: list[tuple[str, Row | None, object, float | None]] = [
            ("YES_NO", None, "YES", 1.0),
            ("YES_NO", None, "no", 0.0),
            ("YES_NO", {"invert": True}, "YES", 0.0),
            ("YES_NO", {"invert": True}, "NO", 1.0),
            ("YES_NO", None, "MAYBE", None),
            ("YES_NO", None, "", None),
            ("RATING", None, "5", 1.0),
            ("RATING", None, 0, 0.0),
            ("RATING", None, "2", 0.4),
            ("RATING", {"invert": True}, "5", 0.0),
            ("RATING", {"invert": True}, "1", 0.8),
            ("RATING", None, "6", None),
            ("RATING", None, "-1", None),
            ("RATING", None, "high", None),
            ("NUMBER", BANDS, "0", 0.4),
            ("NUMBER", BANDS, "12", 1.0),
            ("NUMBER", BANDS, "250", 1.0),
            ("NUMBER", BANDS, "5000", 0.3),
            ("NUMBER", BANDS, "1.5", 0.0),
            ("NUMBER", BANDS, "-3", 0.0),
            ("NUMBER", None, "12", 0.0),
            ("NUMBER", BANDS, "many", None),
            ("NUMBER", BANDS, "nan", None),
            ("CHOICE", CHOICES, "NOBODY", 1.0),
            ("CHOICE", CHOICES, "AGENCY", 0.3),
            ("CHOICE", CHOICES, "IN_HOUSE", 0.0),
            ("CHOICE", CHOICES, "nobody", None),
            ("CHOICE", None, "NOBODY", None),
            ("ESSAY", None, "YES", None),
        ]
        for answer_type, config, value, expected in cases:
            got = scoring.points(criterion("c", answer_type, config=config), value)
            if expected is None:
                self.assertIsNone(got, f"{answer_type} {value!r}")
            else:
                self.assertIsNotNone(got, f"{answer_type} {value!r}")
                self.assertAlmostEqual(got or 0.0, expected, msg=f"{answer_type} {value!r}")

    def test_damaged_configuration_is_empty(self):
        broken = {**criterion("c", "YES_NO"), "config_json": "{not json"}
        self.assertEqual(scoring.points(broken, "YES"), 1.0)

    def test_configuration_must_suit_the_type(self):
        good = [("YES_NO", None), ("YES_NO", ""), ("YES_NO", '{"invert": true}'), ("NUMBER", json.dumps(BANDS)), ("CHOICE", json.dumps(CHOICES)), ("NUMBER", "{}")]
        bad = [
            ("YES_NO", "{not json"),
            ("YES_NO", "[1]"),
            ("NUMBER", '{"bands": 5}'),
            ("NUMBER", '{"bands": [{"min": "a", "max": 1, "points": 1}]}'),
            ("NUMBER", '{"bands": [{"min": 0, "max": 1, "points": 2}]}'),
            ("NUMBER", '{"bands": [{"min": 0, "max": 1}]}'),
            ("CHOICE", '{"choices": [{"points": 1}]}'),
            ("CHOICE", '{"choices": [{"code": "A", "points": -1}]}'),
            ("CHOICE", '{"choices": ["A"]}'),
        ]
        for answer_type, text in good:
            self.assertTrue(scoring.valid_config(answer_type, text), f"{answer_type} {text}")
        for answer_type, text in bad:
            self.assertFalse(scoring.valid_config(answer_type, text), f"{answer_type} {text}")


class Score(unittest.TestCase):
    def setUp(self):
        self.criteria = [
            criterion("f1", weight=3),
            criterion("f2", weight=1),
            criterion("f3", "RATING", weight=2),
            criterion("a1", weight=4, service="web"),
            criterion("a2", weight=1, service="web"),
            criterion("b1", weight=2, service="seo"),
        ]
        self.services = [service("web"), service("seo")]

    def score(self, answers: Row, **changes: Any) -> Row:
        return scoring.score(changes.get("criteria", self.criteria), answers, changes.get("services", self.services), changes.get("thresholds", {}))

    def test_nothing_answered(self):
        self.assertEqual(self.score({}), {"fit_score": None, "need_score": None, "confidence": 0, "recommendation": None, "services": {}})

    def test_fit_is_weighted_over_answered_criteria_only(self):
        self.assertEqual(self.score({"f1": "YES"})["fit_score"], 100)
        self.assertEqual(self.score({"f1": "YES", "f2": "NO"})["fit_score"], 75)
        self.assertEqual(self.score({"f1": "NO", "f2": "YES", "f3": "5"})["fit_score"], 50)

    def test_need_is_the_highest_offered_service(self):
        result = self.score({"a1": "YES", "a2": "NO", "b1": "NO"})
        self.assertEqual(result["services"], {"web": {"need_score": 80, "confidence": 100}, "seo": {"need_score": 0, "confidence": 100}})
        self.assertEqual(result["need_score"], 80)
        self.assertIsNone(result["fit_score"])

    def test_a_service_not_offered_is_recorded_but_does_not_count(self):
        services = [service("web", offered=0), service("seo")]
        result = self.score({"a1": "YES", "b1": "NO", "f1": "YES"}, services=services)
        self.assertEqual(result["services"]["web"], {"need_score": 100, "confidence": 80})
        self.assertEqual(result["need_score"], 0)
        self.assertEqual(result["confidence"], 63)  # (3 + 2) of (6 + 2); the web criteria are not in play

    def test_confidence_is_answered_weight_over_weight_in_play(self):
        self.assertEqual(self.score({"f1": "YES"})["confidence"], 50)  # 3 of the 6 Fit points; no service is in play
        # One answer puts all of that service's criteria in play, and only that service's.
        self.assertEqual(self.score({"f1": "YES", "a1": "YES"})["confidence"], 64)  # (3 + 4) of (6 + 5)
        self.assertEqual(self.score({"f1": "YES", "a1": "YES", "a2": "NO"})["confidence"], 73)  # (3 + 5) of (6 + 5)
        self.assertEqual(self.score({"f1": "YES", "a1": "YES", "b1": "NO"})["confidence"], 69)  # (3 + 4 + 2) of (6 + 5 + 2)
        everything = {"f1": "YES", "f2": "YES", "f3": "5", "a1": "YES", "a2": "YES", "b1": "YES"}
        self.assertEqual(self.score(everything)["confidence"], 100)

    def test_retired_criteria_and_services_are_ignored(self):
        criteria = [*self.criteria, criterion("old", weight=10, active=0), criterion("gone", weight=10, service="dead")]
        services = [*self.services, service("dead", active=0)]
        answers = {"f1": "YES", "old": "NO", "gone": "YES"}
        self.assertEqual(self.score(answers, criteria=criteria, services=services), self.score({"f1": "YES"}))

    def test_a_value_that_no_longer_suits_counts_as_unknown(self):
        self.assertEqual(self.score({"f1": "YES", "f3": "YES"}), self.score({"f1": "YES"}))

    def test_recommendation(self):
        high = {"f1": "YES", "f2": "YES", "f3": "5", "a1": "YES", "a2": "YES", "b1": "YES"}
        self.assertEqual(self.score(high)["recommendation"], "REACH_OUT")
        self.assertEqual(self.score({"f1": "YES", "a1": "YES"})["recommendation"], "REACH_OUT")  # confidence 64
        self.assertEqual(self.score({"f2": "YES", "a2": "YES"})["recommendation"], "MAYBE")  # confidence 18 is too low
        self.assertEqual(self.score({"f1": "NO", "a1": "YES"})["recommendation"], "SKIP")
        self.assertEqual(self.score({"f1": "YES", "a1": "NO"})["recommendation"], "SKIP")
        self.assertEqual(self.score({"f1": "YES"})["recommendation"], "MAYBE")  # Need is unknown, not low
        self.assertEqual(self.score({"f1": "NO"})["recommendation"], "SKIP")
        self.assertEqual(self.score({"f3": "2", "a1": "YES"})["recommendation"], "MAYBE")  # Fit 40

    def test_thresholds_come_from_the_settings(self):
        answers = {"f3": "2", "a1": "YES"}  # Fit 40, Need 100, confidence 55
        self.assertEqual(self.score(answers, thresholds={"score.reach_out.min_fit": 40})["recommendation"], "REACH_OUT")
        self.assertEqual(self.score(answers, thresholds={"score.skip.max_fit": 41})["recommendation"], "SKIP")
        strict = {"score.reach_out.min_fit": 40, "score.min_confidence": 56}
        self.assertEqual(self.score(answers, thresholds=strict)["recommendation"], "MAYBE")

    def test_halves_round_up(self):
        criteria = [criterion("x", "RATING", weight=1), criterion("y", weight=7)]
        self.assertEqual(self.score({"x": "5", "y": "NO"}, criteria=criteria)["fit_score"], 13)  # 12.5


class Notable(unittest.TestCase):
    def test_when_a_change_belongs_on_the_timeline(self):
        base: Row = {"fit_score": 50, "need_score": 50, "recommendation": "MAYBE"}
        cases: list[tuple[Row, bool]] = [
            ({}, False),
            ({"fit_score": 54}, False),
            ({"fit_score": 55}, True),
            ({"need_score": 45}, True),
            ({"need_score": 46}, False),
            ({"recommendation": "SKIP"}, True),
            ({"fit_score": None}, True),
        ]
        for change, expected in cases:
            self.assertEqual(scoring.notable(base, {**base, **change}), expected, repr(change))
        empty: Row = {"fit_score": None, "need_score": None, "recommendation": None}
        self.assertFalse(scoring.notable(empty, empty))
        self.assertTrue(scoring.notable(empty, base))


if __name__ == "__main__":
    unittest.main()
