# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""The scoring engine. Pure: no input or output, so it is tested without a Worker.

All arithmetic on scores is here, never in a model. See specs/004-services-rubric-and-scoring/spec.md.
"""

import json
from typing import Any, cast

Row = dict[str, Any]

# Used when a threshold is missing from the setting table.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "score.reach_out.min_fit": 60,
    "score.reach_out.min_need": 60,
    "score.skip.max_fit": 30,
    "score.skip.max_need": 30,
    "score.min_confidence": 40,
}


def _number(value: object) -> float | None:
    """The value as a number, or None. True and False are not numbers."""
    if isinstance(value, bool):
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _config(criterion: Row) -> Row:
    """The criterion's configuration. Missing or damaged configuration is empty."""
    try:
        config = json.loads(criterion.get("config_json") or "{}")
    except ValueError:
        return {}
    return cast(Row, config) if isinstance(config, dict) else {}


def _rows(config: Row, key: str) -> list[Row]:
    """The bands or choices of a configuration. Anything that is not a list of objects is left out."""
    rows = config.get(key)
    return [cast(Row, row) for row in cast(list[Any], rows) if isinstance(row, dict)] if isinstance(rows, list) else []


def valid_config(answer_type: str, text: str | None) -> bool:
    """True when the configuration text suits the answer type. No configuration always does."""
    if not text:
        return True
    try:
        config = json.loads(text)
    except ValueError:
        return False
    if not isinstance(config, dict):
        return False
    # A measured yes-or-no criterion: see measured().
    if "yes_at" in config and (answer_type != "YES_NO" or _number(cast(Row, config)["yes_at"]) is None):
        return False
    if "measured" in config and (answer_type != "YES_NO" or not isinstance(cast(Row, config)["measured"], bool)):
        return False
    rows = cast(Row, config).get({"NUMBER": "bands", "CHOICE": "choices"}.get(answer_type, ""), [])
    if not isinstance(rows, list):
        return False
    for row in cast(list[Any], rows):
        if not isinstance(row, dict):
            return False
        item = cast(Row, row)
        points = _number(item.get("points"))
        if points is None or not 0 <= points <= 1:
            return False
        if answer_type == "CHOICE" and not (isinstance(item.get("code"), str) and item["code"]):
            return False
        if answer_type == "NUMBER" and (
            _number(item.get("min")) is None or (item.get("max") is not None and _number(item["max"]) is None)
        ):
            return False
    return True


def points(criterion: Row, value: object) -> float | None:
    """The points, 0 to 1, that the answer earns. None when the value is not an answer of the criterion's type."""
    config = _config(criterion)
    answer_type = criterion.get("answer_type")
    earned: float | None = None
    if answer_type == "YES_NO":
        text = str(value).strip().upper()
        if text in ("YES", "NO"):
            earned = 1.0 if text == "YES" else 0.0
    elif answer_type == "RATING":
        rating = _number(value)
        if rating is not None and 0 <= rating <= 5:
            earned = rating / 5
    elif answer_type == "NUMBER":
        number = _number(value)
        if number is not None:
            earned = 0.0  # a number outside every band earns nothing
            for band in _rows(config, "bands"):
                low, high = _number(band.get("min")), band.get("max")
                if low is not None and low <= number and (high is None or number <= (_number(high) or 0)):
                    earned = _number(band.get("points")) or 0.0
                    break
    elif answer_type == "CHOICE":
        for choice in _rows(config, "choices"):
            if choice.get("code") == str(value).strip():
                earned = _number(choice.get("points")) or 0.0
    if earned is None:
        return None
    if config.get("invert") and answer_type in ("YES_NO", "RATING"):
        earned = 1 - earned
    return min(1.0, max(0.0, earned))


def measured(criterion: Row) -> bool:
    """True when the criterion is measured, not judged: a yes-or-no question that names the fact
    that holds the measurement, and whose configuration has "yes_at" or "measured": true. No model
    is asked such a question. With "yes_at" the number decides. With only "measured": true there is
    no rule yet, so the criterion stays unknown until a person answers it: this is for a question,
    such as whether a site is reliably up, that reading a website cannot answer.
    See specs/021-measured-criteria/spec.md."""
    config = _config(criterion)
    return (
        criterion.get("answer_type") == "YES_NO"
        and bool(criterion.get("enrichment_key"))
        and (_number(config.get("yes_at")) is not None or config.get("measured") is True)
    )


def from_fact(criterion: Row, facts: dict[str, Any]) -> Row | None:
    """The answer to a measured criterion, from the fact it names: YES when the number is at or above
    "yes_at", NO when it is below. None when there is no such fact or it is not a number, and then
    the criterion is unknown."""
    if not measured(criterion):
        return None
    key = str(criterion["enrichment_key"])
    number, limit = _number(facts.get(key)), _number(_config(criterion).get("yes_at"))
    if number is None or limit is None:
        return None
    return {
        "criterion_id": criterion["id"],
        "value": "YES" if number >= limit else "NO",
        "probability": None,
        "rationale": f"Measured, not judged: the fact {key} is {number:g}, and {limit:g} or more is YES.",
    }


def _percent(part: float, whole: float) -> int:
    return int(100 * part / whole + 0.5)  # halves round up


def _tally(criteria: list[Row], answers: dict[str, Any]) -> tuple[float, float, float]:
    """(weight x points earned, weight answered, weight of all) over the criteria."""
    earned = answered = total = 0.0
    for criterion in criteria:
        weight = float(criterion["weight"])
        total += weight
        if criterion["id"] in answers:
            got = points(criterion, answers[criterion["id"]])
            if got is not None:  # a value that no longer suits the criterion counts as unknown
                earned += weight * got
                answered += weight
    return earned, answered, total


def score(criteria: list[Row], answers: dict[str, Any], services: list[Row], thresholds: dict[str, float]) -> Row:
    """Fit, Need per service, Need, confidence, and the recommendation.

    criteria are rubric_criterion rows, answers map a criterion id to its answer value, services are
    service rows, and thresholds are the score.* settings. Retired criteria and services are ignored.
    Unknown never lowers a score; it lowers confidence. Fit is the share of points earned. Need is
    the share of points not earned: a good answer to a Need question means little need.
    """
    active = [criterion for criterion in criteria if criterion.get("active", 1)]
    fit_earned, fit_answered, fit_total = _tally([c for c in active if c["section"] == "FIT"], answers)
    fit = _percent(fit_earned, fit_answered) if fit_answered else None

    per_service: dict[str, Row] = {}
    need: int | None = None
    answered, in_play = fit_answered, fit_total
    for service in services:
        if not service.get("active", 1):
            continue
        own = [c for c in active if c["section"] == "NEED" and c["service_id"] == service["id"]]
        earned, done, total = _tally(own, answers)
        if done:
            # Every question is worded so that yes, or a high value, is the good outcome, and points
            # say how good an answer is. A need is where an answer is false or low. That translation
            # is made here, once, for every Need question: Need is the share of the points that were
            # NOT earned. No question carries a setting for it, and nobody has to flip anything in
            # their head (constitution, Principle VII; specs/022-need-direction/spec.md).
            service_need = _percent(done - earned, done)
            per_service[service["id"]] = {"need_score": service_need, "confidence": _percent(done, total)}
        # A service that is not offered is recorded but never counts toward Need or confidence.
        # A service with no answer yet is not in play: looking into one need never requires the others.
        # Once it has an answer, all of its criteria are in play, so a half-examined need lowers confidence.
        if service.get("offered", 1) and done:
            answered += done
            in_play += total
            if need is None or per_service[service["id"]]["need_score"] > need:
                need = per_service[service["id"]]["need_score"]

    limits = {**DEFAULT_THRESHOLDS, **thresholds}
    confidence = _percent(answered, in_play) if in_play else 0
    recommendation: str | None = None
    if fit is not None or need is not None:
        if (
            fit is not None
            and need is not None
            and fit >= limits["score.reach_out.min_fit"]
            and need >= limits["score.reach_out.min_need"]
            and confidence >= limits["score.min_confidence"]
        ):
            recommendation = "REACH_OUT"
        elif (fit is not None and fit < limits["score.skip.max_fit"]) or (
            need is not None and need < limits["score.skip.max_need"]
        ):
            recommendation = "SKIP"
        else:
            recommendation = "MAYBE"
    return {
        "fit_score": fit,
        "need_score": need,
        "confidence": confidence,
        "recommendation": recommendation,
        "services": per_service,
    }


def notable(old: Row, new: Row) -> bool:
    """True when the change belongs on the timeline: Fit or Need moved by 5 or more, appeared, or
    went away, or the recommendation changed."""
    for key in ("fit_score", "need_score"):
        before, after = old.get(key), new.get(key)
        if (before is None) != (after is None) or (before is not None and after is not None and abs(after - before) >= 5):
            return True
    return old.get("recommendation") != new.get("recommendation")
