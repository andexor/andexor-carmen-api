# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""AI adapters, and what is done with their output. See specs/009-ai-runs/spec.md.

One adapter per AI_PROVIDER code. An adapter has two methods, research and score, and makes plain
HTTP requests: no provider's Software Development Kit (SDK) is used. This feature holds the stub
adapter only; the real ones are added by features 010 to 012.

Everything a model returns is untrusted. It is cleaned here, by pure functions, before it is stored:
nothing a model says is taken as an instruction, and nothing is applied to a company by a run.
"""

import json
from typing import Any, Protocol, cast

from records import BadBody, fact_key, fact_value
from scoring import Row, points


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


# The providers that can run. A model of any other provider cannot start a run.
ADAPTERS: dict[str, Adapter] = {"STUB": Stub()}


# ---- Cleaning what a model returns ---------------------------------------------------------------


def _text(value: object, limit: int) -> str | None:
    """Text from a model, trimmed and cut to a length. Anything that is not text or a number is dropped."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value).strip()[:limit] or None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


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
        if not key or value is None:
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
