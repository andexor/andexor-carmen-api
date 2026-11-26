# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""AI adapters, and what is done with their output. See specs/009-ai-runs/spec.md.

One adapter per AI_PROVIDER code. An adapter has two methods, research and score, and makes plain
HTTP requests: no provider's Software Development Kit (SDK) is used, because one may not load in a
Python Worker. The stub is in specs/009-ai-runs/spec.md, Claude in specs/010-ai-adapter-claude/spec.md,
Workers AI in specs/011-ai-adapter-workers-ai/spec.md, and TypeSafe in specs/012-ai-adapter-typesafe/spec.md.

Everything a model returns is untrusted. It is cleaned here, by pure functions, before it is stored:
nothing a model says is taken as an instruction, and nothing is applied to a company by a run.
"""

import importlib
import json
import logging
import pathlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

import pages as site
from records import BadBody, fact_key, fact_value
from scoring import Row, points


logger = logging.getLogger("carmen")


class RunError(Exception):
    """The run cannot be carried out. The message is safe to store with the run and show to a user."""


class BadOutput(Exception):
    """The model's output could not be read. The run retries once, then fails."""


class Adapter(Protocol):
    async def research(self, env: Any, model: Row, company: Row, pages: list[str] | None = None) -> Row:
        """Return {dossier, facts[{key, value, source_url?, notes?}], input_tokens, output_tokens}."""
        ...

    async def score(self, env: Any, model: Row, criteria: list[Row], material: str) -> Row:
        """Return {answers[{criterion_id, value, probability?, rationale?}], input_tokens, output_tokens}."""
        ...


# ---- The stub adapter ----------------------------------------------------------------------------
# Fixed, repeatable results with no outside service, for tests and the preview.


def stub_research(company: Row) -> Row:
    name = company.get("name") or company.get("domain") or "this company"
    website = company.get("website")
    return {
        "dossier": f"Stub dossier for {name}. This text is canned: no model wrote it and nothing was looked up.",
        "facts": [
            {"key": "has_website", "value": "YES" if website else "NO", "source_url": website, "notes": "Stub fact"},
            {"key": "employee_count", "value": "12", "notes": "Stub fact"},
        ],
        "input_tokens": 0,
        "output_tokens": 0,
    }


def stub_answer(criterion: Row) -> str | None:
    """A repeatable answer of the criterion's type, derived from its id. None when it has no choices."""
    seed = sum(str(criterion["id"]).encode())
    kind = criterion.get("answer_type")
    if kind == "YES_NO":
        return "YES" if seed % 2 == 0 else "NO"
    if kind == "RATING":
        return str(seed % 6)
    if kind == "NUMBER":
        return str(seed % 100)
    if kind == "CHOICE":
        try:
            config: Any = json.loads(criterion.get("config_json") or "{}")
            choices: list[Any] = config.get("choices") or []
            return str(choices[seed % len(choices)]["code"]) if choices else None
        except (ValueError, AttributeError, KeyError, TypeError):
            return None
    return None


def stub_score(criteria: list[Row]) -> Row:
    answers: list[Row] = []
    for criterion in criteria:
        value = stub_answer(criterion)
        if value is not None:
            seed = sum(str(criterion["id"]).encode())
            answers.append(
                {
                    "criterion_id": criterion["id"],
                    "value": value,
                    "probability": 0.5 + (seed % 50) / 100,
                    "rationale": "Stub answer, derived from the criterion id.",
                }
            )
    return {"answers": answers, "input_tokens": 0, "output_tokens": 0}


class Stub:
    async def research(self, env: Any, model: Row, company: Row, pages: list[str] | None = None) -> Row:
        return stub_research(company)

    async def score(self, env: Any, model: Row, criteria: list[Row], material: str) -> Row:
        return stub_score(criteria)


# ---- Prompts -------------------------------------------------------------------------------------
# Files in src/prompts, so that they can be edited without touching code. A placeholder is {{name}}.

PROMPTS = pathlib.Path(__file__).parent / "prompts"


def render(name: str, **values: str) -> str:
    """The prompt file with its placeholders filled in. A placeholder with no value is an error."""
    try:
        text = (PROMPTS / f"{name}.md").read_text()
    except OSError as e:
        logger.error(json.dumps({"event": "carmen_prompt_error", "prompt": name, "detail": f"{type(e).__name__}: {e}"[:300]}))
        raise RunError(f"The prompt {name} could not be read") from e
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise RunError(f"The prompt {name} has a placeholder with no value")
    return text


def company_text(company: Row) -> str:
    """The company as data for a prompt: the same fields a scoring model is given."""
    return "\n".join(f"{column}: {company[column]}" for column in COMPANY_FACTS if company.get(column) is not None)


def keys_text(keys: list[Row]) -> str:
    return "\n".join(f"- {row['key']} ({row['value_type']}): {row.get('description') or row.get('label') or ''}" for row in keys)


def criteria_text(criteria: list[Row]) -> str:
    """The criteria as one JSON object per line: id, question, type, and for a choice its codes."""
    lines: list[str] = []
    for criterion in criteria:
        item: Row = {"criterion_id": criterion["id"], "question": criterion["prompt"], "type": criterion["answer_type"]}
        if criterion.get("help"):
            item["help"] = criterion["help"]
        if criterion["answer_type"] == "CHOICE":
            try:
                config: Any = json.loads(criterion.get("config_json") or "{}")
                choices: list[Any] = config.get("choices") or []
                item["choices"] = [{"code": choice["code"], "label": choice.get("label")} for choice in choices]
            except (ValueError, AttributeError, KeyError, TypeError):
                item["choices"] = []
        lines.append(json.dumps(item))
    return "\n".join(lines)


# ---- The Claude adapter --------------------------------------------------------------------------
# The Messages API over plain HTTPS. Research is two requests: one with the web search tool, which
# writes the dossier, and one with no tools, which lists the dossier's facts in a fixed JSON shape.
# They are separate because web search always returns citations, and the API refuses citations
# together with a fixed JSON shape. Scoring is one request with a fixed JSON shape.

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_VERSION = "2026-10-21"
CLAUDE_MAX_TOKENS = 16000
WEB_SEARCH_TOOL = "web_search_20260209"
DEFAULT_MAX_SEARCHES = 5  # each search is billed; the setting ai.research.max_searches overrides this
MAX_PAUSES = 3

# post(env, body) sends one request and returns (HTTP status, the response as a dict).
Poster = Callable[[Any, Row], Awaitable[tuple[int, Row]]]

_TEXT_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
FACTS_SCHEMA: Row = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "source_url": _TEXT_OR_NULL,
                    "notes": _TEXT_OR_NULL,
                },
                "required": ["key", "value", "source_url", "notes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}
ANSWERS_SCHEMA: Row = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion_id": {"type": "string"},
                    "value": {"type": "string"},
                    "probability": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    "rationale": _TEXT_OR_NULL,
                },
                "required": ["criterion_id", "value", "probability", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}


def claude_search_body(model_id: str, prompt: str, max_searches: int) -> Row:
    return {
        "model": model_id,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}],
    }


def claude_json_body(model_id: str, prompt: str, schema: Row) -> Row:
    return {
        "model": model_id,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }


def claude_check(status: int, reply: Row) -> None:
    """Raise RunError unless the reply is a complete answer. The message is safe to store and show."""
    if status != 200:
        problem: Any = reply.get("error") if isinstance(reply.get("error"), dict) else {}
        # The provider's own message stays in the log. Only its error type is shown.
        logger.error(json.dumps({"event": "carmen_claude_error", "status": status, "detail": str(problem.get("message"))[:500]}))
        raise RunError(f"The Claude API answered {status} ({problem.get('type') or 'no error type'})")
    reason = reply.get("stop_reason")
    if reason == "refusal":
        raise RunError("Claude declined the request")
    if reason == "max_tokens":
        raise RunError("Claude's answer was cut off at the token limit")


def claude_text(reply: Row) -> str:
    """The final answer: the text after the last tool block. Text before a search is only a note
    that the model is about to search."""
    parts: list[str] = []
    content: Any = reply.get("content")
    for item in cast(list[Any], content) if isinstance(content, list) else []:
        block: Row = cast(Row, item) if isinstance(item, dict) else {}
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif block.get("type") != "thinking":
            parts = []
    return "".join(parts).strip()


def claude_usage(replies: list[Row]) -> Row:
    """Tokens and searches over every request of one run. Cached input tokens are input tokens too."""
    tokens_in = tokens_out = searches = 0
    for reply in replies:
        usage: Any = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            tokens_in += _count(usage.get(key)) or 0
        tokens_out += _count(usage.get("output_tokens")) or 0
        tools: Any = usage.get("server_tool_use") if isinstance(usage.get("server_tool_use"), dict) else {}
        searches += _count(tools.get("web_search_requests")) or 0
    return {"input_tokens": tokens_in, "output_tokens": tokens_out, "search_count": searches}


def json_list(text: str, key: str) -> list[Any]:
    """The list under the key in an answer that was asked for a fixed JSON shape. Raises BadOutput."""
    try:
        data: Any = json.loads(text)
        found: Any = data[key]
    except (ValueError, KeyError, TypeError) as e:
        raise BadOutput(f"the reply is not the JSON that was asked for: {type(e).__name__}") from e
    if not isinstance(found, list):
        raise BadOutput(f"{key} is not a list")
    return cast(list[Any], found)


def claude_json(reply: Row, key: str) -> list[Any]:
    return json_list(claude_text(reply), key)


async def research_inputs(env: Any, setting: str, default: int, most: int) -> tuple[str, list[Row], int]:
    """What every research prompt needs: the services on offer, the well-known fact keys, and a
    limit from the setting table, held between 1 and most."""
    try:
        found = await env.DB.batch(
            [
                env.DB.prepare("SELECT name FROM service WHERE active = 1 AND offered = 1 ORDER BY sort_order, name"),
                env.DB.prepare("SELECT key, label, value_type, description FROM enrichment_key WHERE active = 1 ORDER BY sort_order, key"),
                env.DB.prepare("SELECT value FROM setting WHERE key = ?").bind(setting),
            ]
        )
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_research_inputs_error", "setting": setting, "detail": f"{type(e).__name__}: {e}"[:300]}))
        raise RunError("The services, fact keys, and settings for research could not be read") from e
    stored = found[2]["results"]
    wanted = str(stored[0]["value"]) if stored else ""
    limit = min(most, max(1, int(wanted))) if wanted.isdigit() else default
    return ", ".join(row["name"] for row in found[0]["results"]), found[1]["results"], limit


async def claude_post(env: Any, body: Row) -> tuple[int, Row]:
    """Send one request to the Messages API. The key comes from Secrets Store and goes nowhere else."""
    # Loaded by name: the module exists only inside a Worker.
    workers: Any = importlib.import_module("workers")
    try:
        key = await env.CLAUDE_API_KEY.get()
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_claude_error", "detail": f"no API key: {type(e).__name__}"}))
        raise RunError("The Claude API key is not available to this Worker") from e
    headers = {"x-api-key": str(key), "anthropic-version": CLAUDE_VERSION, "content-type": "application/json"}
    try:
        response: Any = await workers.fetch(CLAUDE_URL, method="POST", headers=headers, body=json.dumps(body))
        data: Any = await response.json()
        return int(response.status), cast(Row, data) if isinstance(data, dict) else {}
    except Exception as e:
        # The request did not complete, or the reply was not JSON. The key is not part of the detail.
        logger.error(json.dumps({"event": "carmen_claude_error", "detail": f"request failed: {type(e).__name__}"}))
        raise RunError(f"The Claude API could not be reached ({type(e).__name__})") from e


class Claude:
    def __init__(self, post: Poster = claude_post) -> None:
        self.post = post

    async def ask(self, env: Any, body: Row) -> list[Row]:
        """Send a request, and send it again while the API pauses a long search turn."""
        replies: list[Row] = []
        messages: list[Row] = list(body["messages"])
        for _ in range(MAX_PAUSES + 1):
            status, reply = await self.post(env, {**body, "messages": messages})
            claude_check(status, reply)
            replies.append(reply)
            if reply.get("stop_reason") != "pause_turn":
                return replies
            # The paused answer goes back unchanged, and the API carries on from it.
            messages = [*body["messages"], {"role": "assistant", "content": reply["content"]}]
        raise RunError("Claude paused the search too many times")

    async def find_facts(self, env: Any, model_id: str, dossier: str, keys: list[Row]) -> tuple[list[Any], list[Row]]:
        """List the dossier's facts. Output that cannot be read is asked for once more: the search is
        the costly part, and it is not repeated for this."""
        body = claude_json_body(model_id, render("facts", dossier=dossier, keys=keys_text(keys)), FACTS_SCHEMA)
        replies: list[Row] = []
        for attempt in (1, 2):
            replies += await self.ask(env, body)
            try:
                return claude_json(replies[-1], "facts"), replies
            except BadOutput:
                if attempt == 2:
                    raise RunError("Claude's list of facts could not be read, twice") from None
        return [], replies

    async def run_research(self, env: Any, model_id: str, company: Row, services: str, keys: list[Row], max_searches: int) -> Row:
        prompt = render("research", services=services, company=company_text(company))
        replies = await self.ask(env, claude_search_body(model_id, prompt, max_searches))
        dossier = claude_text(replies[-1])
        if not dossier:
            raise RunError("Claude returned no dossier")
        facts, more = await self.find_facts(env, model_id, dossier, keys)
        return {"dossier": dossier, "facts": facts, **claude_usage(replies + more)}

    async def research(self, env: Any, model: Row, company: Row, pages: list[str] | None = None) -> Row:
        services, keys, max_searches = await research_inputs(env, "ai.research.max_searches", DEFAULT_MAX_SEARCHES, 20)
        return await self.run_research(env, model["model_id"], company, services, keys, max_searches)

    async def score(self, env: Any, model: Row, criteria: list[Row], material: str) -> Row:
        prompt = render("scoring", criteria=criteria_text(criteria), material=material)
        replies = await self.ask(env, claude_json_body(model["model_id"], prompt, ANSWERS_SCHEMA))
        return {"answers": claude_json(replies[-1], "answers"), **claude_usage(replies)}


# ---- The Workers AI adapter ----------------------------------------------------------------------
# Open models on the Cloudflare account, through the AI binding. They cannot search the web, so the
# API fetches pages from the company's own website and the model reads them. Research is two
# requests, like Claude's: a dossier in prose, then its facts in a fixed JSON shape.

# Tokens each launch model can hold, from the Workers AI catalog. An unknown model gets the smallest.
WORKERS_AI_CONTEXT = {
    "@cf/openai/gpt-oss-120b": 128_000,
    "@cf/zai-org/glm-5.3": 1_048_576,
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": 24_000,
}
SMALLEST_CONTEXT = 24_000
PROMPT_TOKENS = 1_500  # room for the prompt around the pages
MAX_PAGE_TOKENS = 40_000  # a large context is not filled: input tokens are billed
CHARS_PER_TOKEN = 3  # shortcut: an estimate on the safe side, not a tokenizer; measure if a run is ever refused for length
DEFAULT_MAX_PAGES = 8  # used only when the setting ai.research.max_pages is missing; the setting is the place to change it

# run(env, model id, body) sends one request and returns the reply as a dict.
Runner = Callable[[Any, str, Row], Awaitable[Row]]
# fetch(url) returns (the address after redirects, the HTML), or a short reason why it cannot be read.
Fetcher = Callable[[str], Awaitable[tuple[str, str] | str]]


def workers_ai_limits(model_id: str) -> tuple[int, int]:
    """(tokens kept for the answer, characters of page text that fit) for a model."""
    context = WORKERS_AI_CONTEXT.get(model_id, SMALLEST_CONTEXT)
    answer = min(CLAUDE_MAX_TOKENS, context // 4)  # a reasoning model's thinking counts as answer tokens
    return answer, min(MAX_PAGE_TOKENS, context - answer - PROMPT_TOKENS) * CHARS_PER_TOKEN


def workers_ai_body(model_id: str, prompt: str, name: str | None = None, schema: Row | None = None) -> Row:
    body: Row = {"messages": [{"role": "user", "content": prompt}], "max_tokens": workers_ai_limits(model_id)[0]}
    if name and schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    return body


def workers_ai_text(reply: Row) -> str:
    """The answer in a reply. Raises RunError when the model declined or was cut off."""
    found: Any = reply.get("choices")
    choices = cast(list[Any], found) if isinstance(found, list) else []
    first: Row = cast(Row, choices[0]) if choices and isinstance(choices[0], dict) else {}
    message: Any = first.get("message") if isinstance(first.get("message"), dict) else {}
    if message.get("refusal"):
        raise RunError("The model declined the request")
    if first.get("finish_reason") == "length":
        raise RunError("The model's answer was cut off at the token limit")
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def workers_ai_usage(replies: list[Row]) -> Row:
    tokens_in = tokens_out = 0
    for reply in replies:
        usage: Any = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
        tokens_in += _count(usage.get("prompt_tokens")) or 0
        tokens_out += _count(usage.get("completion_tokens")) or 0
    return {"input_tokens": tokens_in, "output_tokens": tokens_out}


async def workers_ai_run(env: Any, model_id: str, body: Row) -> Row:
    """Send one request through the AI binding. The Workers runtime converts the body to a
    JavaScript object and returns the reply as a Python dict, as it does for a D1 row."""
    try:
        reply: Any = await env.AI.run(model_id, body)
    except Exception as e:
        # The provider's own message stays in the log.
        logger.error(json.dumps({"event": "carmen_workers_ai_error", "model": model_id, "detail": str(e)[:500]}))
        raise RunError(f"Workers AI could not run the model ({type(e).__name__})") from e
    return cast(Row, reply) if isinstance(reply, dict) else {}


class WorkersAI:
    def __init__(self, run: Runner = workers_ai_run, fetch: Fetcher = site.fetch_page) -> None:
        self.run = run
        self.fetch = fetch

    async def run_research(self, env: Any, model_id: str, company: Row, services: str, keys: list[Row], max_pages: int) -> Row:
        read, skipped = await site.read_site(company.get("website"), max_pages, self.fetch)
        if not read:
            # The reasons are short and safe to show; the details are in the Worker log.
            raise RunError(f"No page of the company's website could be read ({'; '.join(skipped[:3])}). This model cannot search the web")
        text = site.as_text(site.trim(read, workers_ai_limits(model_id)[1]))
        prompt = render("research-pages", services=services, company=company_text(company), pages=text)
        replies = [await self.run(env, model_id, workers_ai_body(model_id, prompt))]
        dossier = workers_ai_text(replies[0])
        if not dossier:
            raise RunError("The model returned no dossier")
        # Unreadable facts are asked for once more. The pages are not fetched again for this.
        body = workers_ai_body(model_id, render("facts", dossier=dossier, keys=keys_text(keys)), "facts", FACTS_SCHEMA)
        for attempt in (1, 2):
            replies.append(await self.run(env, model_id, body))
            try:
                facts = json_list(workers_ai_text(replies[-1]), "facts")
                return {"dossier": dossier, "facts": facts, "skipped_pages": skipped, **workers_ai_usage(replies)}
            except BadOutput:
                if attempt == 2:
                    raise RunError("The model's list of facts could not be read, twice") from None
        return {}

    async def research(self, env: Any, model: Row, company: Row, pages: list[str] | None = None) -> Row:
        services, keys, max_pages = await research_inputs(env, "ai.research.max_pages", DEFAULT_MAX_PAGES, 20)
        return await self.run_research(env, model["model_id"], company, services, keys, max_pages)

    async def score(self, env: Any, model: Row, criteria: list[Row], material: str) -> Row:
        prompt = render("scoring", criteria=criteria_text(criteria), material=material)
        reply = await self.run(env, model["model_id"], workers_ai_body(model["model_id"], prompt, "answers", ANSWERS_SCHEMA))
        return {"answers": json_list(workers_ai_text(reply), "answers"), **workers_ai_usage([reply])}


# ---- The TypeSafe adapter ------------------------------------------------------------------------
# Jev is a decision model: it answers typed questions with probabilities and writes no text. It
# scores only. One request carries the material as state and one question per criterion. The model
# gives judgments; what counts as an answer is decided here, in code.

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"

# The wording and the thresholds, in one place. A probability near 0.5 means the material does not
# settle the question, and such a criterion is left unknown, never guessed.
YES_AT = 0.7  # a yes-or-no question is YES at or above this
NO_AT = 0.3  # and NO at or below this
# The companion question. Jev always picks a level for a rating and an option for a choice, even
# when nothing in the material supports one: a pick is not evidence that the question can be
# answered. So every RATING and CHOICE question is sent with a second, yes-or-no question about the
# same criterion, the companion, that asks whether the material can answer it at all. The pick is
# kept only when the companion says yes strongly enough. Otherwise the criterion is left unknown,
# which lowers confidence and never lowers a score.
#
# A YES_NO question has a companion too, for another reason. Its own probability says how sure Jev
# is of yes or no, and that already decides whether it is kept. But Jev can be sure on thin
# material. The companion says whether the material held the evidence, so it is written into the
# answer's rationale, and an answer whose companion is low is marked "weak evidence" there. It does
# not remove a YES_NO answer.
ANSWERABLE_AT = 0.7  # a rating or a choice is kept when its companion question reaches this
ANSWERABLE = "Is there enough information here to answer this question: {prompt}"
# A rating is 0 to 5. Each level is worded so that it makes sense read by itself.
RATING_LEVELS = [
    "Not at all, or the lowest possible",
    "Very little, or very low",
    "A little, or low",
    "A moderate amount, or medium",
    "A lot, or high",
    "Completely, or the highest possible",
]


def material_parts(text: str) -> Row:
    """The text of material() as named parts: company, dossier, facts. An empty part is left out,
    because a model can read an empty part as a signal."""

    def pairs(block: str) -> dict[str, str]:
        return {key: value for key, _, value in (line.partition(": ") for line in block.split("\n")) if value}

    head, found, facts = text.rpartition("\n\nFACTS\n")
    if not found:
        return {"material": text}
    company, _, dossier = head.partition("\n\nDOSSIER\n")
    parts: Row = {"company": pairs(company), "dossier": dossier.strip(), "facts": pairs(facts)}
    return {name: part for name, part in parts.items() if part}


def _choices(criterion: Row) -> dict[str, str]:
    """A choice criterion's codes with their labels. Empty when it has none."""
    try:
        config: Any = json.loads(criterion.get("config_json") or "{}")
        choices: list[Any] = config.get("choices") or []
        return {str(choice["code"]): str(choice.get("label") or choice["code"]) for choice in choices}
    except (ValueError, AttributeError, KeyError, TypeError):
        return {}


def typesafe_questions(criteria: list[Row]) -> Row:
    """One typed question per criterion, named by the criterion's id, and a companion question for
    each (see ANSWERABLE_AT). A NUMBER criterion is not asked: it is answered from the facts."""
    questions: Row = {}
    for criterion in criteria:
        prompt = str(criterion["prompt"]) + (f" ({criterion['help']})" if criterion.get("help") else "")
        kind = criterion.get("answer_type")
        if kind == "YES_NO":
            questions[criterion["id"]] = {"type": "noul", "instructions": prompt}
        elif kind == "RATING":
            questions[criterion["id"]] = {"type": "score", "instructions": prompt, "criteria": RATING_LEVELS}
        elif kind == "CHOICE" and _choices(criterion):
            questions[criterion["id"]] = {"type": "choice", "instructions": prompt, "criteria": _choices(criterion)}
        else:
            continue
        # The companion question of this criterion: can the material answer it at all?
        # It is named by the criterion's id and ".answerable", and typesafe_answers reads it.
        questions[f"{criterion['id']}.answerable"] = {"type": "noul", "instructions": ANSWERABLE.format(prompt=prompt)}
    return questions


def _chance(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return None
    return float(value)


def typesafe_answers(criteria: list[Row], replies: Row, parts: Row) -> list[Row]:
    """The answers to store, from the model's judgments and, for a NUMBER criterion, from the facts.
    A criterion that is not settled is left out and counts as unknown."""
    facts: dict[str, str] = {**parts.get("company", {}), **parts.get("facts", {})}
    answers: list[Row] = []

    def keep(criterion: Row, value: str, probability: float | None, rationale: str | None = None) -> None:
        answers.append({"criterion_id": criterion["id"], "value": value, "probability": probability, "rationale": rationale})

    for criterion in criteria:
        kind = criterion.get("answer_type")
        found: Any = replies.get(criterion["id"])
        reply: Row = cast(Row, found) if isinstance(found, dict) else {}
        if kind == "NUMBER":
            key = criterion.get("enrichment_key")
            if key and key in facts:
                keep(criterion, facts[key], None, f"Taken from the fact {key}.")
            continue
        other: Any = replies.get(f"{criterion['id']}.answerable")
        companion: Row = cast(Row, other) if isinstance(other, dict) else {}
        answerable = _chance(companion.get("noul"))
        # What the companion said, for the rationale: Jev writes no reason, so this is the one place
        # where a reader sees that an answer rests on little.
        if answerable is None:
            evidence = ""
        else:
            evidence = f" Enough information to answer: {answerable:.2f}{' (weak evidence)' if answerable < ANSWERABLE_AT else ''}."
        if kind == "YES_NO":
            # Kept or not by its own probability. The companion only marks the evidence.
            yes = _chance(reply.get("noul"))
            if yes is not None and yes >= YES_AT:
                keep(criterion, "YES", yes, f"Jev: yes {yes:.2f}.{evidence}")
            elif yes is not None and yes <= NO_AT:
                keep(criterion, "NO", 1 - yes, f"Jev: no {1 - yes:.2f}.{evidence}")
            continue
        # A rating or a choice. Its companion question decides whether the pick is kept: below
        # ANSWERABLE_AT the material does not settle it, and the criterion is left unknown.
        chances: Any = reply.get("probabilities")
        if answerable is None or answerable < ANSWERABLE_AT or not isinstance(chances, dict) or not chances:
            continue
        # The most likely level or option. A rating's average is not used: it is pulled to the middle.
        ranked = {str(name): _chance(chance) or 0.0 for name, chance in cast(dict[Any, Any], chances).items()}
        top = max(ranked, key=lambda name: ranked[name])
        keep(criterion, top, ranked[top], f"Jev: {'level ' if kind == 'RATING' else ''}{top} at {ranked[top]:.2f}.{evidence}")
    return answers


async def typesafe_post(env: Any, body: Row) -> tuple[int, Row]:
    """Send one request to TypeSafe. The key comes from Secrets Store and goes nowhere else."""
    # Loaded by name: the module exists only inside a Worker.
    workers: Any = importlib.import_module("workers")
    try:
        key = await env.TYPESAFE_API_KEY.get()
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_typesafe_error", "detail": f"no API key: {type(e).__name__}"}))
        raise RunError("The TypeSafe API key is not available to this Worker") from e
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    try:
        response: Any = await workers.fetch(TYPESAFE_URL, method="POST", headers=headers, body=json.dumps(body))
        data: Any = await response.json()
        return int(response.status), cast(Row, data) if isinstance(data, dict) else {}
    except Exception as e:
        # The request did not complete, or the reply was not JSON. The key is not part of the detail.
        logger.error(json.dumps({"event": "carmen_typesafe_error", "detail": f"request failed: {type(e).__name__}"}))
        raise RunError(f"The TypeSafe API could not be reached ({type(e).__name__})") from e


class TypeSafe:
    def __init__(self, post: Poster = typesafe_post) -> None:
        self.post = post

    async def research(self, env: Any, model: Row, company: Row, pages: list[str] | None = None) -> Row:
        raise RunError("This model answers questions and cannot research")

    async def score(self, env: Any, model: Row, criteria: list[Row], material: str) -> Row:
        parts = material_parts(material)
        questions = typesafe_questions(criteria)
        tokens: Row = {"input_tokens": 0, "output_tokens": 0}
        judged: Row = {}
        if questions:
            status, reply = await self.post(env, {"model": model["model_id"], "state": parts, "questions": questions})
            if status != 200:
                # The provider's own message stays in the log. A failed call is an error, never an answer.
                logger.error(json.dumps({"event": "carmen_typesafe_error", "status": status, "detail": json.dumps(reply.get("detail"))[:500]}))
                limited = " (too many requests; try again later)" if status == 429 else ""
                raise RunError(f"The TypeSafe API answered {status}{limited}")
            found: Any = reply.get("answers")
            if not isinstance(found, dict):
                raise BadOutput("the reply has no answers")
            judged = cast(Row, found)
            # Every judgment is logged, the unsettled ones included, so that a threshold can be checked later.
            logger.info(json.dumps({"event": "carmen_typesafe_judgments", "model": reply.get("model"), "answers": judged})[:8000])
            usage: Any = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
            tokens = {"input_tokens": _count(usage.get("input_tokens")) or 0, "output_tokens": _count(usage.get("output_tokens")) or 0}
            # The reply names the version that answered. It is the pinned one: see specs/027-pin-jev-version/spec.md.
            tokens["model_id_used"] = _text(reply.get("model"), 200)
        return {"answers": typesafe_answers(criteria, judged, parts), **tokens}


# The providers that can run. A model of any other provider cannot start a run.
ADAPTERS: dict[str, Adapter] = {"STUB": Stub(), "CLAUDE": Claude(), "WORKERS_AI": WorkersAI(), "TYPESAFE": TypeSafe()}


# ---- Cleaning what a model returns ---------------------------------------------------------------


def _text(value: object, limit: int) -> str | None:
    """Text from a model, trimmed and cut to a length. Anything that is not text or a number is dropped."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value).strip()[:limit] or None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


NO_VALUE = ("not provided", "not found", "not available", "not specified", "not stated", "not mentioned", "unknown", "n/a", "none", "null")


def clean_facts(raw: object, key_types: dict[str, str]) -> list[Row]:
    """The facts a research run proposed, in the stored form. A fact with no key or value is dropped,
    and so is a date that is not understood. key_types maps a well-known key to its value type."""
    facts: list[Row] = []
    for item in cast(list[Any], raw) if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        fact = cast(Row, item)
        key = fact_key(_text(fact.get("key"), 100) or "")
        value = _text(fact.get("value"), 2000)
        # A model that has nothing to say sometimes says so in the value. That is not a fact.
        if not key or value is None or value.lower().strip(" .") in NO_VALUE:
            continue
        try:
            value = fact_value(value, key_types.get(key))
        except BadBody:
            continue
        facts.append(
            {
                "key": key,
                "value": value,
                "source_url": _text(fact.get("source_url"), 2000),
                "notes": _text(fact.get("notes"), 2000),
            }
        )
    return facts


def clean_answers(raw: object, criteria: list[Row]) -> tuple[list[Row], int]:
    """The answers a scoring run gave, with their points, and how many were dropped.

    An answer is dropped when its criterion does not exist or is retired, when its value does not
    suit the criterion's type, or when the criterion was already answered in this output.
    """
    in_use = {criterion["id"]: criterion for criterion in criteria if criterion.get("active", 1)}
    answers: dict[str, Row] = {}
    dropped = 0
    for item in cast(list[Any], raw) if isinstance(raw, list) else []:
        answer: Row = cast(Row, item) if isinstance(item, dict) else {}
        criterion = in_use.get(answer.get("criterion_id"))
        value = _text(answer.get("value"), 200)
        if criterion is None or value is None or criterion["id"] in answers:
            dropped += 1
            continue
        value = value.upper() if criterion["answer_type"] == "YES_NO" else value
        earned = points(criterion, value)
        if earned is None:
            dropped += 1
            continue
        probability = answer.get("probability")
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
            probability = None
        answers[criterion["id"]] = {
            "criterion_id": criterion["id"],
            "value": value,
            "points": earned,
            "probability": probability,
            "rationale": _text(answer.get("rationale"), 2000),
        }
    return list(answers.values()), dropped


def skipped_note(output: Row, most: int = 5) -> str:
    """What a run's timeline entry says about pages that could not be read. Empty when there were none."""
    found: Any = output.get("skipped_pages")
    skipped = [str(item)[:300] for item in cast(list[Any], found)] if isinstance(found, list) else []
    if not skipped:
        return ""
    more = f"; and {len(skipped) - most} more" if len(skipped) > most else ""
    pages = "1 page" if len(skipped) == 1 else f"{len(skipped)} pages"
    return f"; {pages} could not be read: {'; '.join(skipped[:most])}{more}"


def token_counts(output: Row) -> tuple[int | None, int | None]:
    return _count(output.get("input_tokens")), _count(output.get("output_tokens"))


# ---- The material a scoring model reads ----------------------------------------------------------
# The same input gives the same material, so every model that scores it reads identical text.

COMPANY_FACTS = ("name", "website", "industry", "naics_code", "employee_count", "city", "region", "country", "summary")


def material(company: Row, facts: list[Row], dossier: str | None = None) -> str:
    """What a scoring model is given: the company, then a dossier when one is the input, then facts.

    facts are rows with key and value, in the order given. With a dossier they are that research run's
    facts, accepted or not; without one they are the company's latest accepted fact for each key.
    """
    lines = ["COMPANY"]
    lines += [f"{column}: {company[column]}" for column in COMPANY_FACTS if company.get(column) is not None]
    if dossier:
        lines += ["", "DOSSIER", dossier.strip()]
    lines += ["", "FACTS"]
    lines += [f"{fact['key']}: {fact['value']}" for fact in facts] or ["(none)"]
    return "\n".join(lines)


# ---- Comparing runs ------------------------------------------------------------------------------


def compare(criteria: list[Row], in_effect: dict[str, Row], runs: dict[str, dict[str, Row]]) -> list[Row]:
    """Criterion by criterion: the answer in effect and each run's answer.

    in_effect maps a criterion id to the company's answer; runs maps a run id to that run's answers by
    criterion id. disagreement is true when the runs do not all give the same value, counting a run
    with no answer as different from a run with one.
    """
    rows: list[Row] = []
    for criterion in criteria:
        if not criterion.get("active", 1):
            continue
        given = {run_id: answers.get(criterion["id"]) for run_id, answers in runs.items()}
        values = {answer["value"] if answer else None for answer in given.values()}
        effect = in_effect.get(criterion["id"])
        rows.append(
            {
                "criterion_id": criterion["id"],
                "section": criterion["section"],
                "service_id": criterion["service_id"],
                "prompt": criterion["prompt"],
                "answer_type": criterion["answer_type"],
                "in_effect": effect["value"] if effect else None,
                "runs": given,
                "disagreement": len(values) > 1,
            }
        )
    return rows
