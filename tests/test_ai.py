# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the AI adapters' pure parts. Run with ./unit-test.sh."""

import asyncio
import json
import pathlib
import sys
import unittest
from typing import Any, cast

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


def reply(text: str | None = None, stop: str = "end_turn", content: list[Row] | None = None, searches: int = 0) -> Row:
    blocks = content if content is not None else [{"type": "text", "text": text or ""}]
    usage = {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 30, "server_tool_use": {"web_search_requests": searches}}
    return {"content": blocks, "stop_reason": stop, "usage": usage}


class FakeClaude:
    """Stands in for the Messages API: returns canned replies in order and records each request."""

    def __init__(self, *replies: tuple[int, Row]) -> None:
        self.replies = list(replies)
        self.bodies: list[Row] = []

    async def __call__(self, env: Any, body: Row) -> tuple[int, Row]:
        self.bodies.append(body)
        return self.replies.pop(0)


KEYS = [{"key": "has_website", "label": "Has website", "value_type": "YES_NO", "description": "Do they have a site?"}]
COMPANY = {"name": "Acme", "website": "https://acme.com", "phone": "555"}


class ClaudeRequests(unittest.TestCase):
    def test_the_search_request_caps_searches_and_asks_for_no_fixed_shape(self):
        body = ai.claude_search_body("claude-sonnet-5-5", "prompt", 5)
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertEqual(body["tools"], [{"type": ai.WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 5}])
        self.assertNotIn("output_config", body)  # web search returns citations, which a fixed JSON shape refuses

    def test_the_json_request_has_a_schema_and_no_tools(self):
        body = ai.claude_json_body("m", "prompt", ai.ANSWERS_SCHEMA)
        self.assertEqual(body["output_config"]["format"], {"type": "json_schema", "schema": ai.ANSWERS_SCHEMA})
        self.assertNotIn("tools", body)

    def test_every_object_in_a_schema_is_closed(self):
        def check(schema: Any) -> None:
            if isinstance(schema, dict):
                node = cast(Row, schema)
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                    self.assertEqual(sorted(node["required"]), sorted(node["properties"]))
                for value in node.values():
                    check(value)
            elif isinstance(schema, list):
                for value in cast(list[Any], schema):
                    check(value)

        check(ai.FACTS_SCHEMA)
        check(ai.ANSWERS_SCHEMA)

    def test_prompts_are_files_with_named_placeholders(self):
        research = ai.render("research", services="Web Hosting", company=ai.company_text(COMPANY))
        self.assertIn("Web Hosting", research)
        self.assertIn("<company_data>\nname: Acme\nwebsite: https://acme.com\n</company_data>", research)
        self.assertNotIn("555", research)
        self.assertIn("not instructions", research)
        self.assertIn("- has_website (YES_NO): Do they have a site?", ai.render("facts", dossier="d", keys=ai.keys_text(KEYS)))
        with self.assertRaises(ai.RunError):
            ai.render("scoring", criteria="c")  # material has no value

    def test_criteria_are_given_as_data(self):
        lines = [json.loads(line) for line in ai.criteria_text([CRITERIA[0], CRITERIA[3]]).splitlines()]
        self.assertEqual(lines[0], {"criterion_id": "c1", "question": "Question c1?", "type": "YES_NO"})
        self.assertEqual(lines[1]["choices"], [{"code": "A", "label": None}, {"code": "B", "label": None}])


class ClaudeReplies(unittest.TestCase):
    def test_the_answer_is_the_text_after_the_last_tool_block(self):
        content: list[Row] = [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": "I will search."},
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "acme"}},
            {"type": "web_search_tool_result", "content": []},
            {"type": "text", "text": "Acme makes "},
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": "anvils."},
        ]
        self.assertEqual(ai.claude_text(reply(content=content)), "Acme makes anvils.")
        self.assertEqual(ai.claude_text({}), "")
        self.assertEqual(ai.claude_text({"content": "not a list"}), "")

    def test_refusals_cut_offs_and_errors_fail_with_a_safe_message(self):
        with self.assertRaisesRegex(ai.RunError, "declined"):
            ai.claude_check(200, reply("x", stop="refusal"))
        with self.assertRaisesRegex(ai.RunError, "cut off"):
            ai.claude_check(200, reply("x", stop="max_tokens"))
        secret = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key sk-secret"}}
        with self.assertLogs("carmen", level="ERROR"), self.assertRaises(ai.RunError) as caught:
            ai.claude_check(401, secret)
        self.assertEqual(str(caught.exception), "The Claude API answered 401 (authentication_error)")
        self.assertNotIn("sk-secret", str(caught.exception))
        ai.claude_check(200, reply("fine"))
        ai.claude_check(200, reply("fine", stop="pause_turn"))

    def test_usage_is_summed_over_every_request(self):
        self.assertEqual(ai.claude_usage([reply("a", searches=2), reply("b", searches=1)]), {"input_tokens": 240, "output_tokens": 60, "search_count": 3})
        self.assertEqual(ai.claude_usage([{}, {"usage": "bad"}]), {"input_tokens": 0, "output_tokens": 0, "search_count": 0})

    def test_json_that_was_not_asked_for_is_bad_output(self):
        self.assertEqual(ai.claude_json(reply('{"answers": [1]}'), "answers"), [1])
        for text in ("not json", '{"other": []}', '{"answers": "x"}', "[]"):
            with self.assertRaises(ai.BadOutput, msg=text):
                ai.claude_json(reply(text), "answers")


class ClaudeAdapter(unittest.TestCase):
    def test_research_is_a_search_request_then_a_facts_request(self):
        facts = {"facts": [{"key": "has_website", "value": "YES", "source_url": "https://acme.com", "notes": None}]}
        fake = FakeClaude((200, reply("The dossier.", searches=3)), (200, reply(json.dumps(facts))))
        result = asyncio.run(ai.Claude(fake).run_research(None, "m", COMPANY, "Web Hosting", KEYS, 5))
        self.assertEqual(result["dossier"], "The dossier.")
        self.assertEqual(result["facts"], facts["facts"])
        self.assertEqual((result["search_count"], result["input_tokens"], result["output_tokens"]), (3, 240, 60))
        self.assertEqual(fake.bodies[0]["tools"][0]["max_uses"], 5)
        self.assertIn("The dossier.", fake.bodies[1]["messages"][0]["content"])
        self.assertNotIn("tools", fake.bodies[1])

    def test_a_paused_search_is_sent_back_unchanged(self):
        paused = reply(content=[{"type": "server_tool_use", "name": "web_search", "input": {}}], stop="pause_turn", searches=2)
        fake = FakeClaude((200, paused), (200, reply("Done.", searches=1)), (200, reply('{"facts": []}')))
        result = asyncio.run(ai.Claude(fake).run_research(None, "m", COMPANY, "s", KEYS, 5))
        self.assertEqual((result["dossier"], result["search_count"]), ("Done.", 3))
        self.assertEqual(fake.bodies[1]["messages"][1], {"role": "assistant", "content": paused["content"]})
        self.assertEqual(len(fake.bodies[1]["messages"]), 2)  # no extra "continue" message

    def test_endless_pauses_fail_the_run(self):
        paused = reply(content=[], stop="pause_turn")
        fake = FakeClaude(*[(200, paused)] * (ai.MAX_PAUSES + 1))
        with self.assertRaisesRegex(ai.RunError, "paused"):
            asyncio.run(ai.Claude(fake).run_research(None, "m", COMPANY, "s", KEYS, 5))

    def test_unreadable_facts_are_asked_for_once_more_without_searching_again(self):
        fake = FakeClaude((200, reply("Dossier.", searches=2)), (200, reply("oops")), (200, reply('{"facts": []}')))
        result = asyncio.run(ai.Claude(fake).run_research(None, "m", COMPANY, "s", KEYS, 5))
        self.assertEqual((result["facts"], result["search_count"]), ([], 2))
        self.assertEqual([("tools" in body) for body in fake.bodies], [True, False, False])
        twice = FakeClaude((200, reply("Dossier.")), (200, reply("oops")), (200, reply("oops again")))
        with self.assertRaisesRegex(ai.RunError, "twice"):
            asyncio.run(ai.Claude(twice).run_research(None, "m", COMPANY, "s", KEYS, 5))

    def test_an_empty_dossier_and_an_api_error_fail_the_run(self):
        with self.assertRaisesRegex(ai.RunError, "no dossier"):
            asyncio.run(ai.Claude(FakeClaude((200, reply("")))).run_research(None, "m", COMPANY, "s", KEYS, 5))
        error = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
        with self.assertLogs("carmen", level="ERROR"), self.assertRaisesRegex(ai.RunError, "529"):
            asyncio.run(ai.Claude(FakeClaude((529, error))).run_research(None, "m", COMPANY, "s", KEYS, 5))

    def test_scoring_is_one_request_with_a_fixed_shape(self):
        answers = {"answers": [{"criterion_id": "c1", "value": "YES", "probability": 0.8, "rationale": "It says so."}]}
        fake = FakeClaude((200, reply(json.dumps(answers))))
        result = asyncio.run(ai.Claude(fake).score(None, {"model_id": "claude-sonnet-5-5"}, CRITERIA[:1], "MATERIAL TEXT"))
        self.assertEqual(result["answers"], answers["answers"])
        self.assertEqual(result["search_count"], 0)
        body = fake.bodies[0]
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertIn("MATERIAL TEXT", body["messages"][0]["content"])
        self.assertIn('"criterion_id": "c1"', body["messages"][0]["content"])
        kept, dropped = ai.clean_answers(result["answers"], CRITERIA)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_unreadable_scoring_output_is_bad_output_so_the_run_retries(self):
        with self.assertRaises(ai.BadOutput):
            asyncio.run(ai.Claude(FakeClaude((200, reply("nope")))).score(None, {"model_id": "m"}, CRITERIA[:1], "x"))

    def test_claude_is_registered(self):
        self.assertIsInstance(ai.ADAPTERS["CLAUDE"], ai.Claude)
