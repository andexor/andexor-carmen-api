# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""The rules of a Comma-Separated Values (CSV) import. Pure: no input or output, so it is tested
without a Worker. See specs/013-csv-import/spec.md.

A mapping says where each column goes: {"<header>": "<target>"}. A target is "company.<field>",
"contact.<field>", or "fact". A column that is left out of the mapping is ignored. Columns that
share a target are joined with a space, in file order: "First name" and "Last name" both go to
contact.name.
"""

import csv
import io
from typing import cast

from records import SHARED_HOSTS, BadBody, Values, derive_domain, fact_key, fact_value

COMPANY_TARGETS = (
    "name",
    "website",
    "phone",
    "email",
    "street",
    "city",
    "region",
    "postal_code",
    "country",
    "industry",
    "naics_code",
    "employee_count",
    "summary",
)
CONTACT_TARGETS = ("name", "title", "email", "phone", "notes")
FACT = "fact"
TARGETS = (*(f"company.{f}" for f in COMPANY_TARGETS), *(f"contact.{f}" for f in CONTACT_TARGETS), FACT)

# The header of a column, in the form fact_key gives it, and the target that is suggested for it.
SUGGESTIONS: dict[str, str] = {
    **dict.fromkeys(("company", "company_name", "business", "business_name", "organization", "account"), "company.name"),
    **dict.fromkeys(("website", "web", "url", "site", "domain", "web_site", "company_website"), "company.website"),
    **dict.fromkeys(("company_phone", "main_phone", "office_phone"), "company.phone"),
    **dict.fromkeys(("company_email", "general_email"), "company.email"),
    **dict.fromkeys(("street", "address", "street_address", "address_1", "address_2", "address_line_1"), "company.street"),
    "city": "company.city",
    **dict.fromkeys(("state", "region", "province"), "company.region"),
    **dict.fromkeys(("zip", "zip_code", "postal_code", "postcode"), "company.postal_code"),
    "country": "company.country",
    "industry": "company.industry",
    **dict.fromkeys(("naics", "naics_code"), "company.naics_code"),
    **dict.fromkeys(("employees", "employee_count", "number_of_employees", "headcount"), "company.employee_count"),
    **dict.fromkeys(("summary", "description", "about"), "company.summary"),
    **dict.fromkeys(("contact", "contact_name", "full_name", "first_name", "last_name"), "contact.name"),
    **dict.fromkeys(("title", "job_title", "position"), "contact.title"),
    **dict.fromkeys(("email", "email_address", "e_mail", "e_mail_address", "contact_email"), "contact.email"),
    **dict.fromkeys(("phone", "telephone", "phone_number", "contact_phone", "mobile", "cell"), "contact.phone"),
}


def parse(text: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """The headers, and each row that holds anything with its row number. The header line is row 1,
    as in a spreadsheet. Raises BadBody for a file that cannot be read or has no usable header line.
    """
    try:
        lines = list(csv.reader(io.StringIO(text.removeprefix("﻿"), newline="")))
    except csv.Error as e:
        raise BadBody(f"the file is not CSV: {e}") from e
    if not lines:
        raise BadBody("the file is empty")
    headers = [header.strip() for header in lines[0]]
    if not all(headers) or len(set(headers)) != len(headers):
        raise BadBody("every column needs a header, and no two columns may share one")
    rows = [(number, cells) for number, cells in enumerate(lines[1:], start=2) if any(cell.strip() for cell in cells)]
    return headers, rows


def suggest(headers: list[str]) -> dict[str, str | None]:
    """A target for each header that is recognized, and None for the others."""
    return {header: SUGGESTIONS.get(fact_key(header)) for header in headers}


def check_mapping(headers: list[str], mapping: object) -> dict[str, str]:
    """The mapping with its ignored columns removed. Raises BadBody for a header that is not in the
    file, an unknown target, a fact column whose header gives no key, or no column for the company.
    """
    if not isinstance(mapping, dict):
        raise BadBody("mapping must be an object")
    kept: dict[str, str] = {}
    for header, target in cast(dict[object, object], mapping).items():
        if target is None or target == "":
            continue
        if not isinstance(header, str) or header not in headers or target not in TARGETS:
            raise BadBody(f"mapping entry {header!r} is not a header of the file, or its target is unknown")
        if target == FACT and not fact_key(header):
            raise BadBody(f"the header {header!r} cannot be a fact key")
        kept[header] = str(target)
    if not {"company.name", "company.website"} & set(kept.values()):
        raise BadBody("a column must be mapped to company.name or company.website")
    return kept


def row_values(
    headers: list[str], cells: list[str], mapping: dict[str, str], types: dict[str, str], shared: str = SHARED_HOSTS
) -> tuple[Values, Values, list[tuple[str, str]]]:
    """One row as company values, contact values (empty for no contact), and facts as key and value.

    types holds the value type of each well-known fact key. Raises BadBody for a row that cannot be
    imported. Its message is shown to the user, so it names the problem and holds nothing else.
    """
    company: Values = {}
    contact: Values = {}
    facts: list[tuple[str, str]] = []
    for header, cell in zip(headers, cells):  # a short row simply has no value for its last columns
        value, target = cell.strip(), mapping.get(header)
        if not value or not target:
            continue
        if target == FACT:
            key = fact_key(header)
            try:
                facts.append((key, fact_value(value, types.get(key))))
            except BadBody as e:
                raise BadBody(f"The column {header} holds a date that is not understood") from e
            continue
        record, field = (company, target[8:]) if target.startswith("company.") else (contact, target[8:])
        record[field] = f"{record[field]} {value}" if field in record else value

    if not (company.get("name") or company.get("website")):
        raise BadBody("The row has no company name and no website")
    if "employee_count" in company:
        try:
            company["employee_count"] = int(str(company["employee_count"]).replace(",", ""))
        except ValueError as e:
            raise BadBody("The employee count is not a whole number") from e
    company["domain"] = derive_domain(company.get("website"), shared)
    if not (contact.get("name") or contact.get("email")):
        contact = {}  # a contact needs a name or an email; a title or a phone alone is dropped
    return company, contact, facts
