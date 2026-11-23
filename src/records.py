# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""What a caller may write to a company or a contact. Pure: no input or output, so it is tested
without a Worker. See specs/002-companies-and-contacts/spec.md.
"""

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

# Columns that hold a code, and the System Reference Table (SRT) domain of each.
COMPANY_CODES = {"stage": "COMPANY_STAGE", "source": "LEAD_SOURCE", "meeting_mode": "MEETING_MODE"}
CONTACT_CODES = {"role": "CONTACT_ROLE"}


class BadBody(Exception):
    """A known key has a value of the wrong type. The message is for the log only."""


def clean(body: Values, fields: dict[str, type]) -> Values:
    """The writable keys of a request body. Unknown keys are dropped; a blank string becomes None."""
    values: Values = {}
    for key, kind in fields.items():
        if key not in body:
            continue
        value = body[key]
        if isinstance(value, str) and kind is str:
            value = value.strip() or None
        elif kind is bool and value in (True, False):  # also accepts 0 and 1
            value = int(value)
        elif value is not None and (type(value) is not kind or kind is bool):
            raise BadBody(f"{key} must be {kind.__name__}")
        values[key] = value
    return values


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
