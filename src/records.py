# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""What a caller may write to each kind of record. Pure: no input or output, so it is tested
without a Worker. See specs/002-companies-and-contacts/spec.md
and specs/003-activities-and-tasks/spec.md.
"""

import calendar
import importlib
import re
from datetime import UTC, date, datetime
from typing import Any, cast
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

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
FACT_FIELDS: dict[str, type] = dict.fromkeys(("key", "value", "source_detail", "notes", "observed_at"), str)
DEAL_FIELDS: dict[str, type] = {
    **dict.fromkeys(
        (
            "company_id",
            "name",
            "stage",
            "engagement_model",
            "primary_contact_id",
            "currency",
            "expected_close_date",
            "lost_reason",
            "notes",
            "owner_id",
        ),
        str,
    ),
    **dict.fromkeys(("one_time_value_cents", "monthly_value_cents"), int),
}
AGREEMENT_FIELDS: dict[str, type] = {
    **dict.fromkeys(
        (
            "company_id",
            "deal_id",
            "parent_agreement_id",
            "type",
            "title",
            "status",
            "engagement_model",
            "amount_period",
            "performance_terms",
            "currency",
            "signed_date",
            "effective_date",
            "end_date",
            "terminated_at",
            "notes",
            "owner_id",
            "renewal_policy",
            "end_basis",
            "end_basis_reason",
        ),
        str,
    ),
    **dict.fromkeys(("amount_cents", "setup_fee_cents", "renewal_notice_days", "term_months"), int),
}
MODEL_FIELDS: dict[str, type] = {
    **dict.fromkeys(("provider", "model_id", "label", "notes"), str),
    **dict.fromkeys(("can_research", "can_score", "web_search", "active"), bool),
    "sort_order": int,
}
RUN_FIELDS: dict[str, type] = {"user_rating": int, "user_note": str}

# Timeline kinds that only the API writes. An entry of one of these kinds is permanent.
AUTOMATIC_KINDS = ("STAGE_CHANGE", "SCORE_CHANGE", "SYSTEM")

# Columns that hold a code, and the System Reference Table (SRT) domain of each.
COMPANY_CODES = {"stage": "COMPANY_STAGE", "source": "LEAD_SOURCE", "meeting_mode": "MEETING_MODE"}
CONTACT_CODES = {"role": "CONTACT_ROLE"}
ACTIVITY_CODES = {"kind": "ACTIVITY_KIND"}
TASK_CODES = {"status": "TASK_STATUS"}
SERVICE_CODES = {"category": "SERVICE_CATEGORY"}
DEAL_CODES = {"stage": "DEAL_STAGE", "engagement_model": "ENGAGEMENT_MODEL"}
AGREEMENT_CODES = {
    "type": "AGREEMENT_TYPE",
    "status": "AGREEMENT_STATUS",
    "engagement_model": "ENGAGEMENT_MODEL",
    "amount_period": "AMOUNT_PERIOD",
    "renewal_policy": "RENEWAL_POLICY",
    "end_basis": "END_BASIS",
}
MODEL_CODES = {"provider": "AI_PROVIDER"}
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


def local_date(zone: str | None, now: datetime | None = None) -> tuple[str, str]:
    """Today's date in the time zone, and the zone that was used.

    zone is a name such as America/New_York. A missing or unknown zone falls back to UTC, and the
    second value says so. now is the current moment unless a test gives one.
    """
    moment = now or datetime.now(UTC)
    try:
        if zone:
            return moment.astimezone(ZoneInfo(zone)).date().isoformat(), zone
    except Exception:  # an unknown name; the zone data itself comes from the tzdata package
        pass
    return moment.astimezone(UTC).date().isoformat(), "UTC"


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


def id_list(body: Values, key: str) -> list[str] | None:
    """The list of ids under the key, without repeats and in order, or None when the key is absent.
    Raises BadBody when it is not a list of text."""
    if key not in body:
        return None
    given = body[key]
    if not isinstance(given, list) or not all(isinstance(item, str) and item for item in cast(list[Any], given)):
        raise BadBody(f"{key} must be a list of ids")
    return sorted(set(cast(list[str], given)))


def file_name_of(given: str | None) -> str:
    """A stored file name: the last part of a path, with no control characters, at most 255 long."""
    name = re.split(r"[/\\]", given or "")[-1]
    name = "".join(character for character in name if character.isprintable()).strip()
    return name[:255] or "document"


def download_header(file_name: str) -> str:
    """The Content-Disposition value that makes a browser save the file under its name.

    It carries a plain ASCII name for old clients and the exact name, percent-encoded, for the rest.
    """
    plain = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in file_name)
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(file_name, safe='')}"


def fact_key(text: str) -> str:
    """A fact key in lowercase snake case: "Has Website" and "has-website" both become has_website."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def fact_value(value: str, value_type: str | None) -> str:
    """The value as a matcher can use it: YES or NO in capitals for a yes-or-no key, and a date in
    ISO 8601 form for a date key. value_type is the well-known key's type, or None for any other key.
    Raises BadBody for a date that is not understood. Nothing else is checked: the key list is a hint.
    """
    if value_type == "YES_NO" and value.upper() in ("YES", "NO"):
        return value.upper()
    if value_type == "DATE":
        return iso_date(value)
    return value


def describe_change(what: str, before: Values, after: Values | None) -> str:
    """One line for the timeline that keeps what an edit or a delete is about to replace.

    before holds the stored values. after holds the new values of an edit, or None for a delete.
    """
    if after is None:
        kept = ", ".join(f"{column} {value!r}" for column, value in before.items() if value is not None)
        return f"{what} deleted. It held: {kept}"
    moved = ", ".join(f"{column} {before.get(column)!r} to {value!r}" for column, value in after.items())
    return f"{what} edited: {moved}"


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


def renewal_problem(agreement: Values) -> str | None:
    """Why the agreement may not be active as it stands, or None. See specs/024-renewal-policy/spec.md.

    The policy and the basis have no default: a person chooses them before the agreement becomes
    active. Any other status may lack them, so a draft can be written first.
    """
    if agreement.get("status") != "ACTIVE":
        return None
    policy = agreement.get("renewal_policy")
    if not policy:
        return "an active agreement needs a renewal policy"
    if not agreement.get("end_basis"):
        return "an active agreement needs an end basis"
    if agreement["end_basis"] == "INTERNAL_DECISION" and not str(agreement.get("end_basis_reason") or "").strip():
        return "an end that rests on an internal decision needs a reason"
    if policy == "AUTO_RENEW" and agreement.get("end_date"):
        return "an agreement that renews automatically has no end date"
    if policy == "APPROVAL" and not (agreement.get("end_date") and agreement.get("term_months")):
        return "an agreement that renews on approval needs an end date and a term in months"
    return None


def add_months(day: str, months: int) -> str:
    """The date that many months later. A day the later month does not have becomes its last day."""
    start = date.fromisoformat(day)
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    return start.replace(year=year, month=month + 1, day=min(start.day, calendar.monthrange(year, month + 1)[1])).isoformat()


# ---- Settings ------------------------------------------------------------------------------------
# What the value of a setting may be, by key. See specs/016-settings/spec.md. A setting with no rule
# here holds any text; a new setting comes with a migration, and its rule is added with it.

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _whole(text: str, low: int, high: int | None = None) -> bool:
    return text.isdigit() and low <= int(text) and (high is None or int(text) <= high)


def _zone(text: str) -> bool:
    try:
        ZoneInfo(text)
    except Exception:  # an unknown or malformed name
        return False
    return True


def _days(text: str) -> bool:
    names = [name.strip().upper() for name in text.split(",")]
    return all(name in DAYS for name in names) and len(set(names)) == len(names)


def _email(text: str) -> bool:
    # No regular expression: a pattern with two repeats around the dot backtracks on a long value.
    local, _, domain = text.partition("@")
    host, _, tail = domain.rpartition(".")
    return bool(local and host and tail) and not any(c in "@,;" or c.isspace() for c in local + domain)


def setting_problem(key: str, value: object) -> str | None:
    """Why the setting may not hold the value, or None. The value is text, or a whole number."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return f"{key} must be text or a whole number"
    text = str(value).strip()
    if not text:
        return f"{key} must not be empty"
    if key.startswith("score."):
        ok, wanted = _whole(text, 0, 100), "a whole number from 0 to 100"
    elif key == "digest.hour_local":
        ok, wanted = _whole(text, 0, 23), "an hour from 0 to 23"
    elif key == "digest.time_zone":
        ok, wanted = _zone(text), "a time zone name such as America/New_York"
    elif key == "digest.days":
        ok, wanted = _days(text), "day names such as MON,TUE,WED,THU,FRI"
    elif key == "digest.recipient":
        ok, wanted = _email(text), "one email address"
    elif key == "renewal.default_notice_days":
        ok, wanted = _whole(text, 0), "a whole number of days"
    elif key in ("document.max_size_bytes", "import.max_rows", "ai.research.max_pages", "proposal.max_tries") or key.startswith("uptime."):
        ok, wanted = _whole(text, 1), "a whole number of at least 1"
    else:
        return None
    return None if ok else f"{key} must be {wanted}"
