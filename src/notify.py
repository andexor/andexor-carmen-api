# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Notifications: when the daily digest is due, what a message says, and the providers that send it.

Everything here is pure except a provider's send. See specs/015-scheduler-and-notifications/spec.md.
A message is plain text. Its template is a file in src/templates/email: the first line is the
subject, the rest is the body, and a placeholder is {{name}}.
"""

import json
import logging
import pathlib
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import access
import records

logger = logging.getLogger("carmen")

TEMPLATES = pathlib.Path(__file__).parent / "templates" / "email"
DEFAULT_HOUR = 7  # used only when the setting digest.hour_local is missing or is not an hour
DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DEFAULT_DAYS = frozenset(DAY_NAMES[:5])  # used only when the setting digest.days is missing or names no day
# What a digest says on a day with nothing to report. It is still sent: silence would not say that Carmen looked.
NOTHING = "Nothing to report"

# The lists of a digest, in the order they are shown, each with its heading.
SECTIONS = (
    ("tasks_due", "Tasks due today"),
    ("tasks_overdue", "Tasks overdue"),
    ("agreements_renewing", "Agreements in their renewal window"),
    ("leads_unscored", "Leads waiting for a score"),
)


class NotifyError(Exception):
    """A message could not be written. The text is safe to log."""


def digest_window(zone: str | None, hour: str | None, days: str | None, now: datetime | None = None) -> tuple[bool, str]:
    """Whether the digest is due now in the time zone, and the moment today began, in UTC.

    The digest is due on a day named in days (such as "MON,TUE,WED,THU,FRI"), at its hour or any
    later hour of that day, so a missed run is made up by the next one. A missing or unknown zone
    falls back to UTC. now is the current moment unless a test gives one.
    """
    local = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        if zone:
            local = local.astimezone(ZoneInfo(zone))
    except Exception:  # an unknown name; UTC is used, as records.local_date does
        pass
    at = int(hour) if hour and hour.isdigit() and int(hour) < 24 else DEFAULT_HOUR
    began = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    on = {name.strip().upper() for name in (days or "").split(",")} & set(DAY_NAMES) or DEFAULT_DAYS
    return DAY_NAMES[local.weekday()] in on and local.hour >= at, began.strftime("%Y-%m-%dT%H:%M:%SZ")


def render(name: str, **values: str) -> tuple[str, str]:
    """The subject and the body of a message, from its template with the placeholders filled in."""
    try:
        text = (TEMPLATES / f"{name}.txt").read_text()
    except OSError as e:
        raise NotifyError(f"the template {name} could not be read: {type(e).__name__}") from e
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise NotifyError(f"the template {name} has a placeholder with no value")
    subject, _, body = text.partition("\n")
    return subject.strip(), body.strip() + "\n"


def _line(name: str, row: dict[str, Any]) -> str:
    company = f" ({row['company_name']})" if row.get("company_name") else ""
    if name == "agreements_renewing":
        return f"{row.get('title') or row.get('type')}{company}, ends {row.get('end_date')}"
    if name == "leads_unscored":
        return str(row.get("name") or row.get("domain"))
    return f"{row.get('title')}{company}" + (f", due {row.get('due_date')}" if name == "tasks_overdue" else "")


def digest_sections(lists: dict[str, list[dict[str, Any]]]) -> str:
    """The lists of a digest as text. Empty when every list is empty: the digest then says NOTHING."""
    parts: list[str] = []
    for name, heading in SECTIONS:
        rows = lists.get(name) or []
        if rows:
            parts.append(f"{heading} ({len(rows)})\n" + "\n".join(f"- {_line(name, row)}" for row in rows))
    return "\n\n".join(parts)


NO_PROPOSALS = "No other service is a good match."


def proposal_sections(items: list[dict[str, Any]]) -> str:
    """The services worth proposing as text, in the order given: the score of each, then the answers
    and the facts behind it. Empty when there is none: the message then says NO_PROPOSALS."""
    parts: list[str] = []
    for place, item in enumerate(items, start=1):
        sure = f", confidence {item['confidence']}" if item.get("confidence") is not None else ""
        lines = [f"{place}. {item['name']}: Need {item['need_score']}{sure}"]
        for answer in item["answers"]:
            reason = f". {answer['rationale']}" if answer.get("rationale") else ""
            lines.append(f"   - {answer['prompt']} {answer['value']}{reason}")
        facts = "; ".join(f"{fact['key']} = {fact['value']}" for fact in item["facts"])
        if facts:
            lines.append(f"   Facts: {facts}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def run_lines(runs: list[dict[str, Any]]) -> str:
    """The runs a proposals job made, one line each, in the order they were made, failed ones too."""
    lines: list[str] = []
    for run in runs:
        result = "succeeded" if run["status"] == "SUCCEEDED" else f"failed. {run['error'] or 'No reason was stored'}"
        lines.append(f"- {str(run['kind']).capitalize()} with {run['model_label']}: {result}")
    return "\n".join(lines) or "- None. No model could be used."


def party(text: str) -> str | dict[str, str]:
    """One side of a message for the send_email binding: the name with the address, or the bare
    address when no name is known. See specs/028-email-names/spec.md."""
    name, address = records.mailbox(text)
    return {"email": address, "name": name} if name else address


class Provider(Protocol):
    status: str  # the NOTIFICATION_STATUS of a message this provider accepted

    async def send(self, env: Any, recipient: str, subject: str, body: str) -> str | None: ...


class Stub:
    """Sends nothing: the message is written to the Worker log."""

    status = "STUBBED"

    async def send(self, env: Any, recipient: str, subject: str, body: str) -> str | None:
        sender = str(getattr(env, "EMAIL_FROM", ""))
        logger.info(json.dumps({"event": "carmen_email_stub", "from": sender, "recipient": recipient, "subject": subject, "body": body}))
        return None


class CloudflareEmail:
    """Cloudflare Email Sending, through the send_email binding EMAIL. Returns the provider's message id."""

    status = "SENT"

    async def send(self, env: Any, recipient: str, subject: str, body: str) -> str | None:
        from pyodide.ffi import to_js

        message = {"to": party(recipient), "from": party(str(env.EMAIL_FROM)), "subject": subject, "text": body}
        result = await env.EMAIL.send(to_js(message, dict_converter=access.js_module().Object.fromEntries))
        return str(result.messageId)


PROVIDERS: dict[str, Provider] = {"STUB": Stub(), "CLOUDFLARE": CloudflareEmail()}


def provider(name: object) -> Provider:
    """The provider named by the variable EMAIL_PROVIDER. Anything else is the stub, which sends nothing."""
    return PROVIDERS.get(str(name), PROVIDERS["STUB"])
