# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the AI adapters' pure parts. Run with ./unit-test.sh."""

import asyncio
import json
import pathlib
import sys
import unittest
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import ai  # noqa: E402

Row = dict[str, Any]
CHOICES = json.dumps({"choices": [{"code": "A", "points": 1}, {"code": "B", "points": 0.5}]})


def criterion(id: str, answer_type: str = "YES_NO", config: str | None = None, active: int = 1, service: str | None = None) -> Row:
    return {
        "id": id,
        "section": "NEED" if service else "FIT",
        "service_id": service,
        "prompt": f"Question {id}?",
        "answer_type": answer_type,
        "weight": 1,
        "config_json": config,
        "active": active,
    }


CRITERIA = [criterion("c1"), criterion("c2", "RATING"), criterion("c3", "NUMBER"), criterion("c4", "CHOICE", CHOICES), criterion("old", active=0)]


class StubAdapter(unittest.TestCase):
    def test_research_is_fixed_and_follows_the_company(self):
        with_site = ai.stub_research({"name": "Acme", "website": "https://acme.com"})
        self.assertIn("Acme", with_site["dossier"])
        self.assertEqual([fact["key"] for fact in with_site["facts"]], ["has_website", "employee_count"])
        self.assertEqual(with_site["facts"][0]["value"], "YES")
        self.assertEqual(ai.stub_research({"name": "Acme"})["facts"][0]["value"], "NO")
        self.assertEqual(ai.stub_research({"name": "Acme"}), ai.stub_research({"name": "Acme"}))

    def test_scoring_is_repeatable_and_every_answer_suits_its_criterion(self):
        first = ai.stub_score(CRITERIA)
        self.assertEqual(first, ai.stub_score(CRITERIA))
        kept, dropped = ai.clean_answers(first["answers"], CRITERIA)
        self.assertEqual(dropped, 1)  # the retired criterion
        self.assertEqual({answer["criterion_id"] for answer in kept}, {"c1", "c2", "c3", "c4"})

    def test_a_choice_criterion_with_no_choices_gets_no_answer(self):
        self.assertIsNone(ai.stub_answer(criterion("x", "CHOICE")))
        self.assertIsNone(ai.stub_answer(criterion("x", "CHOICE", "{broken")))

    def test_the_adapter_is_registered_and_runs(self):
        stub = ai.ADAPTERS["STUB"]
        self.assertEqual(asyncio.run(stub.research(None, {}, {"name": "Acme"})), ai.stub_research({"name": "Acme"}))
        self.assertEqual(asyncio.run(stub.score(None, {}, CRITERIA, "material")), ai.stub_score(CRITERIA))


class CleanFacts(unittest.TestCase):
    def test_facts_are_put_in_the_stored_form(self):
        raw = [
            {"key": "Has Website", "value": "yes", "source_url": " https://acme.com ", "notes": ""},
            {"key": "employee_count", "value": 12},
            {"key": "domain_expires", "value": "Dec 1, 2027"},
        ]
        types = {"has_website": "YES_NO", "employee_count": "NUMBER", "domain_expires": "DATE"}
        self.assertEqual(
            ai.clean_facts(raw, types),
            [
                {"key": "has_website", "value": "YES", "source_url": "https://acme.com", "notes": None},
                {"key": "employee_count", "value": "12", "source_url": None, "notes": None},
                {"key": "domain_expires", "value": "2027-12-01", "source_url": None, "notes": None},
            ],
        )

    def test_unusable_facts_are_dropped(self):
        raw: list[Any] = [
            {"key": "", "value": "x"},
            {"key": "k"},
            {"key": "k", "value": None},
            {"key": "k", "value": True},
            {"key": "k", "value": {"nested": 1}},
            {"key": "domain_expires", "value": "13/45/2026"},
            "not an object",
            None,
        ]
        self.assertEqual(ai.clean_facts(raw, {"domain_expires": "DATE"}), [])
        for not_a_list in (None, "facts", {"key": "k", "value": "v"}, 5):
            self.assertEqual(ai.clean_facts(not_a_list, {}), [])

    def test_long_text_is_cut(self):
        fact = ai.clean_facts([{"key": "k", "value": "x" * 5000}], {})[0]
        self.assertEqual(len(fact["value"]), 2000)


class CleanAnswers(unittest.TestCase):
    def test_answers_get_points_and_are_normalized(self):
        raw = [
            {"criterion_id": "c1", "value": "yes", "probability": 0.9, "rationale": " Because. "},
            {"criterion_id": "c2", "value": 4},
            {"criterion_id": "c4", "value": "B", "probability": "high"},
        ]
        kept, dropped = ai.clean_answers(raw, CRITERIA)
        self.assertEqual(dropped, 0)
        self.assertEqual(kept[0], {"criterion_id": "c1", "value": "YES", "points": 1.0, "probability": 0.9, "rationale": "Because."})
        self.assertEqual((kept[1]["value"], kept[1]["points"]), ("4", 0.8))
        self.assertEqual((kept[2]["points"], kept[2]["probability"]), (0.5, None))

    def test_bad_answers_are_dropped_and_counted(self):
        raw: list[Any] = [
            {"criterion_id": "nope", "value": "YES"},
            {"criterion_id": "old", "value": "YES"},
            {"criterion_id": "c1", "value": "MAYBE"},
            {"criterion_id": "c2", "value": "9"},
            {"criterion_id": "c4", "value": "Z"},
            {"criterion_id": "c1"},
            {"value": "YES"},
            "not an object",
            {"criterion_id": "c1", "value": "NO"},
            {"criterion_id": "c1", "value": "YES"},
        ]
        kept, dropped = ai.clean_answers(raw, CRITERIA)
        self.assertEqual([(answer["criterion_id"], answer["value"]) for answer in kept], [("c1", "NO")])
        self.assertEqual(dropped, 9)
        self.assertEqual(ai.clean_answers(None, CRITERIA), ([], 0))

    def test_probability_must_be_between_0_and_1(self):
        for bad in (1.5, -0.1, True, None, "0.5"):
            kept, _ = ai.clean_answers([{"criterion_id": "c1", "value": "YES", "probability": bad}], CRITERIA)
            self.assertIsNone(kept[0]["probability"], repr(bad))

    def test_token_counts(self):
        self.assertEqual(ai.token_counts({"input_tokens": 10, "output_tokens": 0}), (10, 0))
        self.assertEqual(ai.token_counts({"input_tokens": -1, "output_tokens": "5"}), (None, None))
        self.assertEqual(ai.token_counts({}), (None, None))


class Material(unittest.TestCase):
    def test_the_same_input_gives_the_same_text(self):
        company = {"name": "Acme", "website": "https://acme.com", "city": None, "phone": "555", "employee_count": 12}
        facts = [{"key": "has_website", "value": "YES"}, {"key": "in_metro_atlanta", "value": "NO"}]
        text = ai.material(company, facts)
        self.assertEqual(text, ai.material(dict(company), list(facts)))
        self.assertEqual(text, "COMPANY\nname: Acme\nwebsite: https://acme.com\nemployee_count: 12\n\nFACTS\nhas_website: YES\nin_metro_atlanta: NO")
        self.assertNotIn("555", text)  # a phone number is not material for scoring

    def test_a_dossier_goes_between_the_company_and_the_facts(self):
        text = ai.material({"name": "Acme"}, [], "  Found things.  ")
        self.assertEqual(text, "COMPANY\nname: Acme\n\nDOSSIER\nFound things.\n\nFACTS\n(none)")


class Compare(unittest.TestCase):
    def test_disagreement_is_flagged(self):
        in_effect = {"c1": {"value": "YES"}}
        runs = {
            "r1": {"c1": {"value": "YES"}, "c2": {"value": "4"}, "c3": {"value": "12"}},
            "r2": {"c1": {"value": "NO"}, "c2": {"value": "4"}},
        }
        rows = {row["criterion_id"]: row for row in ai.compare(CRITERIA, in_effect, runs)}
        self.assertEqual(set(rows), {"c1", "c2", "c3", "c4"})  # the retired criterion is left out
        self.assertTrue(rows["c1"]["disagreement"])
        self.assertEqual(rows["c1"]["in_effect"], "YES")
        self.assertFalse(rows["c2"]["disagreement"])
        self.assertTrue(rows["c3"]["disagreement"])  # one run answered, the other did not
        self.assertFalse(rows["c4"]["disagreement"])  # neither answered
        self.assertIsNone(rows["c4"]["in_effect"])
        self.assertEqual(rows["c3"]["runs"], {"r1": {"value": "12"}, "r2": None})


if __name__ == "__main__":
    unittest.main()
