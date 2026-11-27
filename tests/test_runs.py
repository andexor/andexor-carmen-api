# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the queue's run handler: what it reports when a failure cannot be recorded.
Run with ./unit-test.sh."""

import asyncio
import pathlib
import sys
import unittest
from types import SimpleNamespace
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app as api  # noqa: E402

RUN = {"id": "r1", "status": "QUEUED", "provider": "NO_SUCH_PROVIDER", "kind": "RESEARCH", "label": "Model", "company_id": "c1", "requested_by": "u1"}


class Database:
    """A database with one run. It records the statements it is given, and can refuse to write."""

    def __init__(self, run: dict[str, Any] | None, writes: bool = True) -> None:
        self.run_row, self.writes = run, writes
        self.sql: list[str] = []
        self.last = ""

    def prepare(self, sql: str) -> "Database":
        self.last = sql
        return self

    def bind(self, *values: object) -> "Database":
        return self

    async def first(self) -> dict[str, Any] | None:
        return self.run_row

    async def all(self) -> dict[str, Any]:
        return {"results": [{"id": "r2"}]}

    async def run(self) -> None:
        if not self.writes:
            raise RuntimeError("the database is away")
        self.sql.append(self.last)

    async def batch(self, statements: list[Any]) -> None:
        if not self.writes:
            raise RuntimeError("the database is away")


class Queue:
    async def send(self, message: dict[str, Any]) -> None:
        raise RuntimeError("the queue is away")


class RunHandlerTest(unittest.TestCase):
    def test_a_recorded_failure_is_settled_and_an_unrecorded_one_is_delivered_again(self):
        with self.assertLogs("carmen"):
            self.assertTrue(asyncio.run(api.process_run(SimpleNamespace(DB=Database(None)), "r1")))  # no such run
            self.assertTrue(asyncio.run(api.process_run(SimpleNamespace(DB=Database(dict(RUN))), "r1")))  # failed, and recorded
            # The failure cannot be written: False, so that the queue delivers the message again.
            self.assertFalse(asyncio.run(api.process_run(SimpleNamespace(DB=Database(dict(RUN), writes=False)), "r1")))

    def test_a_scoring_run_that_cannot_be_started_fails_alone(self):
        database = Database(None)
        with self.assertLogs("carmen", level="ERROR"):
            asyncio.run(api.start_waiting(SimpleNamespace(DB=database, RUNS=Queue()), "r1"))
        # Only the waiting run is failed, by its own id. The research run is not touched.
        self.assertEqual(len(database.sql), 1)
        self.assertIn("status = 'QUEUED' AND id = ?", database.sql[0])


if __name__ == "__main__":
    unittest.main()
