# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the CSV import rules. Run with ./unit-test.sh."""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import imports  # noqa: E402
from records import BadBody  # noqa: E402

FILE = "﻿Company,Website,First Name,Last Name,Email,Employees,Founded\r\nAcme,https://www.Acme.example/x,Ann,Lee,ann@acme.example,\"1,200\",1999\r\n,,,,,,\r\nBeta,,,,,,\r\n"
MAPPING = {
    "Company": "company.name",
    "Website": "company.website",
    "First Name": "contact.name",
    "Last Name": "contact.name",
    "Email": "contact.email",
    "Employees": "company.employee_count",
    "Founded": "fact",
}


class Parse(unittest.TestCase):
    def test_headers_rows_and_row_numbers(self):
        headers, rows = imports.parse(FILE)
        self.assertEqual(headers[0], "Company")  # the byte order mark is gone
        self.assertEqual([number for number, _ in rows], [2, 4])  # the blank row 3 is skipped, not renumbered

    def test_a_quoted_cell_keeps_its_comma_and_line_break(self):
        _, rows = imports.parse('Name,Note\n"Acme, Inc.","line one\nline two"\n')
        self.assertEqual(rows[0][1], ["Acme, Inc.", "line one\nline two"])

    def test_bad_files_are_refused(self):
        for text in ("", "Name,,City\nx,y,z\n", "Name,Name\nx,y\n"):
            with self.assertRaises(BadBody):
                imports.parse(text)


class Mapping(unittest.TestCase):
    def test_suggest(self):
        suggested = imports.suggest(["Company Name", "URL", "First Name", "Last Name", "E-mail Address", "Zip", "Shoe size"])
        self.assertEqual(
            list(suggested.values()),
            ["company.name", "company.website", "contact.name", "contact.name", "contact.email", "company.postal_code", None],
        )

    def test_every_suggestion_is_a_target(self):
        self.assertLessEqual(set(imports.SUGGESTIONS.values()), set(imports.TARGETS))

    def test_check_mapping_drops_ignored_columns(self):
        headers, _ = imports.parse(FILE)
        self.assertEqual(imports.check_mapping(headers, {**MAPPING, "Founded": None}).get("Founded"), None)

    def test_check_mapping_refuses(self):
        headers, _ = imports.parse(FILE)
        bad = (
            None,
            {"Nope": "company.name"},  # not a header
            {"Company": "company.stage"},  # not a target
            {"Email": "contact.email"},  # nothing for the company
        )
        for mapping in bad:
            with self.assertRaises(BadBody):
                imports.check_mapping(headers, mapping)
        with self.assertRaises(BadBody):
            imports.check_mapping(["Name", "???"], {"Name": "company.name", "???": "fact"})  # no fact key


class RowValues(unittest.TestCase):
    def setUp(self):
        self.headers, self.rows = imports.parse(FILE)

    def test_a_full_row(self):
        company, contact, facts = imports.row_values(self.headers, self.rows[0][1], MAPPING, {})
        self.assertEqual(company["domain"], "acme.example")
        self.assertEqual(company["employee_count"], 1200)
        self.assertEqual(contact, {"name": "Ann Lee", "email": "ann@acme.example"})  # two columns joined
        self.assertEqual(facts, [("founded", "1999")])

    def test_a_name_alone_is_enough_and_makes_no_contact(self):
        company, contact, facts = imports.row_values(self.headers, self.rows[1][1], MAPPING, {})
        self.assertEqual((company, contact, facts), ({"name": "Beta", "domain": None}, {}, []))

    def test_a_typed_fact(self):
        company, _, _ = imports.row_values(["Name", "Website"], ["Joe", "facebook.com/joes"], {"Name": "company.name", "Website": "company.website"}, {})
        self.assertEqual((company["website"], company["domain"]), ("facebook.com/joes", None))  # a shared site gives no domain
        _, _, facts = imports.row_values(["Name", "Has website"], ["Acme", "yes"], {"Name": "company.name", "Has website": "fact"}, {"has_website": "YES_NO"})
        self.assertEqual(facts, [("has_website", "YES")])

    def test_rows_that_cannot_be_imported(self):
        mapping = {"Name": "company.name", "Staff": "company.employee_count", "Title": "contact.title"}
        for cells in (["", "5", "CEO"], ["Acme", "many", ""]):
            with self.assertRaises(BadBody):
                imports.row_values(["Name", "Staff", "Title"], cells, mapping, {})

    def test_a_title_alone_makes_no_contact_and_a_short_row_is_fine(self):
        mapping = {"Name": "company.name", "Staff": "company.employee_count", "Title": "contact.title"}
        self.assertEqual(imports.row_values(["Name", "Staff", "Title"], ["Acme", "", "CEO"], mapping, {})[1], {})
        self.assertEqual(imports.row_values(["Name", "Staff", "Title"], ["Acme"], mapping, {})[0]["name"], "Acme")


if __name__ == "__main__":
    unittest.main()
