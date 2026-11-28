# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the links of a task: its deal and its agreement are under its own company.
Run with ./unit-test.sh."""

import asyncio
import pathlib
import sys
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app as api  # noqa: E402

# Company A has a deal and an agreement, and company B has a deal.
OWNERS = {"deal-a": "a", "agreement-a": "a", "deal-b": "b"}


class Links:
    """A database that knows the company of each deal and agreement."""

    def prepare(self, sql: str) -> "Links":
        return self

    def bind(self, *values: str) -> "Links":
        self.wanted = values[0]
        return self

    async def first(self) -> dict[str, Any] | None:
        owner = OWNERS.get(self.wanted)
        return {"company_id": owner} if owner else None


def company_of(**task: str | None) -> str | None:
    return asyncio.run(api.linked_company(Links(), task))


class TaskLinksTest(unittest.TestCase):
    def test_a_task_with_no_links_keeps_its_company_or_has_none(self):
        self.assertEqual(company_of(company_id="a"), "a")
        self.assertIsNone(company_of(title="Call back"))

    def test_links_under_the_tasks_company_are_accepted(self):
        self.assertEqual(company_of(company_id="a", deal_id="deal-a", agreement_id="agreement-a"), "a")

    def test_a_task_with_no_company_takes_the_company_of_its_deal_or_agreement(self):
        self.assertEqual(company_of(deal_id="deal-b"), "b")
        self.assertEqual(company_of(company_id=None, agreement_id="agreement-a"), "a")

    def test_a_deal_or_agreement_of_another_company_is_refused(self):
        for task in (
            {"company_id": "a", "deal_id": "deal-b"},
            {"company_id": "b", "agreement_id": "agreement-a"},
            {"deal_id": "deal-b", "agreement_id": "agreement-a"},  # no company, and the two disagree
        ):
            with self.subTest(task=task), self.assertRaises(api.BadBody):
                company_of(**task)

    def test_an_unknown_deal_is_refused(self):
        with self.assertRaises(api.BadBody):
            company_of(company_id="a", deal_id="no-such-deal")

    def test_the_databases_refusal_is_a_bad_request(self):
        self.assertEqual(api.db_status(RuntimeError("D1_ERROR: task link outside its company: SQLITE_CONSTRAINT")), 400)


if __name__ == "__main__":
    unittest.main()
