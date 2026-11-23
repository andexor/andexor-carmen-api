# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""What a caller may write to each kind of record. Pure: no input or output, so it is tested
without a Worker. See specs/002-companies-and-contacts/spec.md
and specs/003-activities-and-tasks/spec.md.
"""

import importlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

Values = dict[str, Any]

# Writable columns and their JSON types. Every other column is set by the API or the database.
COMPANY_FIELDS: dict[str, type] = {
    **dict.fromkeys(
        (
            "name",
            "website",
            "stage",
            "source",
            "source_ref",
            "phone",
            "email",
            "street",
            "city",
            "region",
            "postal_code",
            "country",
            "industry",
            "naics_code",
            "meeting_mode",
            "summary",
            "disqualified_reason",
            "owner_id",
        ),
        str,
    ),
    "employee_count": int,
}
CONTACT_FIELDS: dict[str, type] = {
    **dict.fromkeys(("name", "title", "email", "phone", "role", "notes"), str),
    "is_primary": bool,
}

ACTIVITY_FIELDS: dict[str, type] = dict.fromkeys(("kind", "body", "occurred_at", "contact_id", "deal_id"), str)
TASK_FIELDS: dict[str, type] = dict.fromkeys(
    ("title", "company_id", "deal_id", "agreement_id", "due_date", "status", "owner_id"), str
)
SERVICE_FIELDS: dict[str, type] = {
    **dict.fromkeys(("id", "name", "category"), str),
    **dict.fromkeys(("offered", "active"), bool),
    "sort_order": int,
}
CRITERION_FIELDS: dict[str, type] = {
    **dict.fromkeys(("section", "service_id", "prompt", "help", "answer_type", "config_json", "enrichment_key"), str),
    **dict.fromkeys(("weight", "sort_order"), int),
    "active": bool,
}

# Timeline kinds that only the API writes. An entry of one of these kinds is permanent.
AUTOMATIC_KINDS = ("STAGE_CHANGE", "SCORE_CHANGE", "SYSTEM")

# Columns that hold a code, and the System Reference Table (SRT) domain of each.
COMPANY_CODES = {"stage": "COMPANY_STAGE", "source": "LEAD_SOURCE", "meeting_mode": "MEETING_MODE"}
CONTACT_CODES = {"role": "CONTACT_ROLE"}
ACTIVITY_CODES = {"kind": "ACTIVITY_KIND"}
TASK_CODES = {"status": "TASK_STATUS"}
SERVICE_CODES = {"category": "SERVICE_CATEGORY"}
CRITERION_CODES = {"section": "RUBRIC_SECTION", "answer_type": "ANSWER_TYPE"}


class BadBody(Exception):
    """A known key has a value of the wrong type, or a date that is not understood. The message is for the log only."""


# Formats tried, after ISO 8601, when a date arrives in another form. A date that starts with the
# month and day (12/01/2026, 12-01-2026) is read month first. Each may be followed by a time.
DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")
TIME_FORMATS = ("", " %H:%M", " %H:%M:%S", " %I:%M %p")


def fuzzy_moment(text: str, now: datetime) -> datetime | None:
    """What a phrase such as "next Tuesday" or "tomorrow" means, counted from now, or None.

    Only a phrase the library understands from its first character to its last is accepted, and only
    one that contains a letter: the library reads bare numbers, even an impossible date, as a time.
    """
    if not any(character.isalpha() for character in text):
        return None
    # Loaded by name: the package has no type information. Loaded here: it is needed only when all else failed.
    parsedatetime: Any = importlib.import_module("parsedatetime")
    calendar = parsedatetime.Calendar(version=parsedatetime.VERSION_CONTEXT_STYLE)
    found = calendar.nlp(text, sourceTime=now)
    if found and len(found) == 1 and found[0][2] == 0 and found[0][3] == len(text):
        return found[0][0]
    return None


def parse_moment(text: str, now: datetime | None = None) -> datetime:
    """The date or date and time the text names. Raises BadBody when it is not understood.

    now is the moment a fuzzy phrase is counted from: the current time in UTC unless a test gives one.
    """
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for date_format in DATE_FORMATS:
        for time_format in TIME_FORMATS:
            try:
                return datetime.strptime(text, date_format + time_format)
            except ValueError:
                pass
    moment = fuzzy_moment(text, now or datetime.now(UTC).replace(tzinfo=None))
    if moment:
        return moment
    raise BadBody("a date is not understood")  # the text itself is not logged: it is the caller's data


def iso_date(text: str, now: datetime | None = None) -> str:
    """The date as YYYY-MM-DD. Raises BadBody when the text is not understood."""
    return parse_moment(text, now).date().isoformat()


def iso_timestamp(text: str, now: datetime | None = None) -> str:
    """The moment as YYYY-MM-DDTHH:MM:SSZ in UTC. A time with no zone is taken as UTC. Raises BadBody
    when the text is not understood."""
    moment = parse_moment(text, now)
    if moment.tzinfo:
        moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(column: str, text: str) -> str:
    """The text in the stored form of the column. In the data model every date column is named
    *_date and every moment column *_at, so the name decides, for every table."""
    if column.endswith("_date"):
        return iso_date(text)
    if column.endswith("_at"):
        return iso_timestamp(text)
    return text


def clean(body: Values, fields: dict[str, type]) -> Values:
    """The writable keys of a request body. Unknown keys are dropped, a blank string becomes None, and
    a date is put in ISO 8601 form. Raises BadBody for a wrong type or a date that is not understood."""
    values: Values = {}
    for key, kind in fields.items():
        if key not in body:
            continue
        value = body[key]
        if isinstance(value, str) and kind is str:
            value = value.strip() or None
            if value:
                value = iso(key, value)
        elif kind is bool and value in (True, False):  # also accepts 0 and 1
            value = int(value)
        elif value is not None and (type(value) is not kind or kind is bool):
            raise BadBody(f"{key} must be {kind.__name__}")
        values[key] = value
    return values


def slug(name: str) -> str:
    """A service id from its name: lowercase words joined by hyphens, as in the page names on andexor.net."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def derive_domain(website: str | None) -> str | None:
    """The website's host: lowercase, with no scheme, no "www.", no port, and no path."""
    if not website:
        return None
    text = website.strip()
    try:
        host = urlsplit(text if "//" in text else f"//{text}").hostname
    except ValueError:
        return None
    return (host or "").removeprefix("www.") or None
