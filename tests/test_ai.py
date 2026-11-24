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
import pages  # noqa: E402

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

    def test_a_value_that_only_says_nothing_was_found_is_dropped(self):
        raw = [{"key": "cms", "value": "Not provided."}, {"key": "city", "value": "N/A"}, {"key": "note", "value": "Unknown to most people"}]
        self.assertEqual([fact["key"] for fact in ai.clean_facts(raw, {})], ["note"])

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

    def test_a_prompt_file_that_cannot_be_read_fails_the_run_and_is_logged(self):
        with self.assertLogs("carmen", level="ERROR") as logs, self.assertRaisesRegex(ai.RunError, "could not be read"):
            ai.render("no-such-prompt")
        self.assertIn("carmen_prompt_error", logs.output[0])

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
        failure = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key sk-secret"}}
        with self.assertLogs("carmen", level="ERROR"), self.assertRaises(ai.RunError) as caught:
            ai.claude_check(401, failure)
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


HOME = """<html><head><title>Acme Plumbing</title><style>p {color: red}</style><script>var hidden = 1;</script></head>
<body><h1>We fix pipes</h1><p>Serving   Springfield
since 1990.</p>
<a href="/about">About us</a> <a href="/blog/2024/about-pipes">A post</a> <a href="services.html#top">What we do</a>
<a href="https://www.acme.com/contact/">Contact</a> <a href="/">Home</a> <a href="/brochure-about.pdf">Brochure</a>
<a href="https://other.example/about">Partner</a> <a href="mailto:x@acme.com">Mail about</a> <a href="/careers">Jobs</a>
<a href="/about#team">Team</a></body></html>"""


def chat(text: str | None, finish: str = "stop", refusal: str | None = None) -> Row:
    message = {"role": "assistant", "content": text, "refusal": refusal, "reasoning": "thinking that is not the answer"}
    return {"choices": [{"message": message, "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": 30}}


class FakeModel:
    """Stands in for the AI binding: returns canned replies in order and records each request."""

    def __init__(self, *replies: Row) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, Row]] = []

    async def __call__(self, env: Any, model_id: str, body: Row) -> Row:
        self.calls.append((model_id, body))
        return self.replies.pop(0)


class FakeSite:
    """Stands in for the web: a page is its HTML, or (the address it redirects to, its HTML)."""

    def __init__(self, found: dict[str, Any]) -> None:
        self.found = found
        self.asked: list[str] = []

    async def __call__(self, url: str) -> tuple[str, str] | None:
        self.asked.append(url)
        page = self.found.get(url)
        return None if page is None else cast(tuple[str, str], page) if isinstance(page, tuple) else (url, str(page))


class Pages(unittest.TestCase):
    def test_a_page_is_stripped_to_its_visible_text(self):
        text, links = pages.read(HOME)
        self.assertEqual(text.split("\n")[:3], ["Acme Plumbing", "We fix pipes", "Serving Springfield since 1990."])
        self.assertNotIn("hidden", text)
        self.assertNotIn("color", text)
        self.assertIn(("/about", "About us"), links)

    def test_damaged_html_does_not_fail(self):
        self.assertEqual(pages.read("<p>Open <b>tags <a href='x'>and <script>never closed")[0], "Open tags and")
        self.assertEqual(pages.read(""), ("", []))

    def test_only_pages_of_the_same_site_that_look_useful_are_followed(self):
        picked = pages.pick_links("https://acme.com/", pages.read(HOME)[1], 8)
        self.assertEqual(
            picked,
            ["https://acme.com/about", "https://acme.com/services.html", "https://www.acme.com/contact/", "https://acme.com/blog/2024/about-pipes"],
        )
        self.assertEqual(pages.pick_links("https://acme.com/", pages.read(HOME)[1], 2), picked[:2])
        self.assertEqual(pages.pick_links("https://acme.com/", pages.read(HOME)[1], 0), [])

    def test_only_a_public_web_address_is_fetched(self):
        self.assertEqual(pages.site_url("acme.com"), "https://acme.com")
        self.assertEqual(pages.site_url(" http://acme.com/x "), "http://acme.com/x")
        for bad in (None, "", "localhost", "http://localhost:8787", "http://127.0.0.1/", "http://[::1]/", "http://0x7f.0.0.1/", "http://127.1/", "http://2130706433/", "http://acme.com./", "ftp://acme.com", "file:///etc/passwd", "javascript:alert(1)"):
            self.assertIsNone(pages.site_url(bad), bad)

    def test_pages_are_cut_to_fit_and_a_short_page_leaves_room_for_the_next(self):
        cut = pages.trim([("a", "x" * 10), ("b", "y" * 500), ("c", "z" * 500)], 210)
        self.assertEqual([len(text) for _, text in cut], [10, 100, 100])
        self.assertEqual(pages.trim([("a", "text")], 0), [])
        self.assertEqual(pages.as_text([("https://acme.com", "Hello")]), "PAGE https://acme.com\nHello")

    def test_a_site_is_read_from_its_home_page_and_a_failed_page_is_left_out(self):
        web = FakeSite(
            {
                "https://acme.com": ("https://acme.com/", HOME),
                "https://acme.com/about": "<p>About Acme</p>",
                "https://www.acme.com/contact/": ("https://elsewhere.example/", "<p>Not this site</p>"),
            }
        )
        read = asyncio.run(pages.read_site("acme.com", 8, web))
        self.assertEqual([url for url, _ in read], ["https://acme.com/", "https://acme.com/about"])
        self.assertEqual(len(web.asked), 5)
        self.assertEqual(len(FakeSite({}).asked), 0)
        limited = FakeSite({"https://acme.com": ("https://acme.com/", HOME)})
        asyncio.run(pages.read_site("acme.com", 2, limited))
        self.assertEqual(limited.asked, ["https://acme.com", "https://acme.com/about"])
        self.assertEqual(asyncio.run(pages.read_site("acme.com", 8, FakeSite({}))), [])
        self.assertEqual(asyncio.run(pages.read_site("localhost", 8, web)), [])
        # A home page that redirects to an address literal is not used.
        inside = FakeSite({"https://acme.com": ("http://169.254.169.254/latest/", HOME)})
        with self.assertLogs("carmen", level="WARNING"):
            self.assertEqual(asyncio.run(pages.read_site("acme.com", 8, inside)), [])
        self.assertEqual(len(inside.asked), 1)


class WorkersAIAdapter(unittest.TestCase):
    LLAMA = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    WEB = {"https://acme.com": ("https://acme.com/", HOME), "https://acme.com/about": "<p>" + "About Acme. " * 20000 + "</p>"}

    def test_the_pages_are_sized_to_the_model(self):
        self.assertEqual(ai.workers_ai_limits(self.LLAMA), (6000, 16500 * 3))
        self.assertEqual(ai.workers_ai_limits("@cf/unknown/model"), ai.workers_ai_limits(self.LLAMA))
        self.assertEqual(ai.workers_ai_limits("@cf/zai-org/glm-5.3"), (16000, 40000 * 3))

    def test_research_reads_the_site_then_asks_for_a_dossier_and_its_facts(self):
        facts = {"facts": [{"key": "has_website", "value": "YES", "source_url": "https://acme.com/", "notes": None}]}
        model = FakeModel(chat("The dossier."), chat(json.dumps(facts)))
        result = asyncio.run(ai.WorkersAI(model, FakeSite(self.WEB)).run_research(None, self.LLAMA, COMPANY, "Web Hosting", KEYS, 8))
        self.assertEqual((result["dossier"], result["facts"]), ("The dossier.", facts["facts"]))
        self.assertEqual((result["input_tokens"], result["output_tokens"]), (200, 60))
        first, second = model.calls[0][1], model.calls[1][1]
        prompt = first["messages"][0]["content"]
        self.assertIn("PAGE https://acme.com/\nAcme Plumbing", prompt)
        self.assertIn("PAGE https://acme.com/about\nAbout Acme.", prompt)
        self.assertIn("Web Hosting", prompt)
        self.assertNotIn("555", prompt)
        self.assertLess(len(prompt), 16500 * 3 + 3000)
        self.assertEqual(first["max_tokens"], 6000)
        self.assertNotIn("response_format", first)
        self.assertEqual(second["response_format"]["json_schema"]["schema"], ai.FACTS_SCHEMA)
        self.assertIn("The dossier.", second["messages"][0]["content"])

    def test_no_readable_page_fails_the_run_before_any_model_is_called(self):
        model = FakeModel()
        for company in ({"name": "Acme"}, COMPANY):
            with self.assertRaisesRegex(ai.RunError, "No page"):
                asyncio.run(ai.WorkersAI(model, FakeSite({})).run_research(None, "m", company, "s", KEYS, 8))
        self.assertEqual(model.calls, [])

    def test_unreadable_facts_are_asked_for_once_more_without_fetching_again(self):
        web = FakeSite(self.WEB)
        model = FakeModel(chat("Dossier"), chat("not json"), chat('{"facts": []}'))
        self.assertEqual(asyncio.run(ai.WorkersAI(model, web).run_research(None, "m", COMPANY, "s", KEYS, 8))["facts"], [])
        self.assertEqual((len(model.calls), len(web.asked)), (3, 5))
        with self.assertRaisesRegex(ai.RunError, "twice"):
            twice = FakeModel(chat("Dossier"), chat("no"), chat("no"))
            asyncio.run(ai.WorkersAI(twice, FakeSite(self.WEB)).run_research(None, "m", COMPANY, "s", KEYS, 8))

    def test_a_refusal_a_cut_off_and_an_empty_dossier_fail_the_run(self):
        for canned, message in ((chat("x", refusal="No."), "declined"), (chat("x", finish="length"), "cut off"), (chat(None), "no dossier"), ({}, "no dossier")):
            with self.assertRaisesRegex(ai.RunError, message):
                asyncio.run(ai.WorkersAI(FakeModel(canned), FakeSite(self.WEB)).run_research(None, "m", COMPANY, "s", KEYS, 8))

    def test_scoring_is_one_request_with_a_fixed_shape(self):
        answers = {"answers": [{"criterion_id": "c1", "value": "YES", "probability": 0.8, "rationale": "It says so."}]}
        model = FakeModel(chat(json.dumps(answers)))
        result = asyncio.run(ai.WorkersAI(model).score(None, {"model_id": self.LLAMA}, CRITERIA[:1], "MATERIAL TEXT"))
        self.assertEqual((result["answers"], result["input_tokens"]), (answers["answers"], 100))
        model_id, body = model.calls[0]
        self.assertEqual(model_id, self.LLAMA)
        self.assertIn("MATERIAL TEXT", body["messages"][0]["content"])
        self.assertEqual(body["response_format"]["json_schema"]["schema"], ai.ANSWERS_SCHEMA)
        with self.assertRaises(ai.BadOutput):
            asyncio.run(ai.WorkersAI(FakeModel(chat("nope"))).score(None, {"model_id": "m"}, CRITERIA[:1], "x"))

    def test_workers_ai_is_registered(self):
        self.assertIsInstance(ai.ADAPTERS["WORKERS_AI"], ai.WorkersAI)


if __name__ == "__main__":
    unittest.main()
