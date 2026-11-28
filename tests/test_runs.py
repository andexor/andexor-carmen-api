# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the queue's run handler: what it reports when a failure cannot be recorded.
Also for the record that a rescoring is waiting, and for the start of a proposals job.
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


class Statement:
    def __init__(self, database: "Scores", sql: str) -> None:
        self.database, self.sql, self.values = database, sql, ()

    def bind(self, *values: object) -> "Statement":
        self.values = values
        return self

    async def first(self) -> dict[str, Any]:
        return {"value": self.database.mark}


class Scores:
    """A database with a rubric, no answers, and the setting rescore.requested. It keeps what is written."""

    def __init__(self, mark: str) -> None:
        self.mark = mark
        self.written: list[Statement] = []

    def prepare(self, sql: str) -> Statement:
        return Statement(self, sql)

    async def batch(self, statements: list[Statement]) -> list[dict[str, Any]]:
        if not statements[0].sql.startswith("SELECT"):
            self.written += statements
        return [{"results": []} for _ in statements]


class RescoreTest(unittest.TestCase):
    def rescore(self, mark: str) -> list[Statement]:
        database = Scores(mark)
        asyncio.run(api.rescore_all(api.job_request(SimpleNamespace(DB=database), "test")))
        return database.written

    def test_a_rescoring_clears_the_request_it_found_and_no_other(self):
        (clear,) = self.rescore("2026-11-27T10:00:00Z 1a2b3c4d")
        # Cleared by its value: a request written while the rescoring ran has another value and stays.
        self.assertIn("SET value = ''", clear.sql)
        self.assertIn("AND value = ?", clear.sql)
        self.assertEqual(clear.values, (api.RESCORE, "2026-11-27T10:00:00Z 1a2b3c4d"))

    def test_with_no_request_nothing_is_cleared(self):
        self.assertEqual(self.rescore(""), [])

    def test_two_requests_never_write_the_same_value(self):
        first, second = api.rescore_mark(Scores("")), api.rescore_mark(Scores(""))
        self.assertEqual(first.values[1], api.RESCORE)
        self.assertNotEqual(first.values[0], second.values[0])


class Agreements:
    """A database with one agreement, whose proposals job may be started (free) or was started just now."""

    def __init__(self, free: bool) -> None:
        self.free = free
        self.last = ""
        self.values: tuple[object, ...] = ()
        self.ran: list[str] = []

    def prepare(self, sql: str) -> "Agreements":
        self.last = sql
        return self

    def bind(self, *values: object) -> "Agreements":
        self.values = values
        return self

    async def first(self) -> dict[str, Any] | None:
        if self.last.startswith("SELECT"):
            return {"value": "45"}  # the setting proposal.reuse_minutes
        return {"id": "a1"} if self.free else None

    async def run(self) -> None:
        self.ran.append(self.last)


class Sent:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


class ProposalsStartTest(unittest.TestCase):
    def test_a_start_is_recorded_and_a_second_one_soon_after_is_refused(self):
        database, queue = Agreements(free=True), Sent()
        self.assertEqual(asyncio.run(api.queue_proposals(SimpleNamespace(DB=database, RUNS=queue), "a1")), "QUEUED")
        self.assertEqual(database.values, ("a1", 0, "-45 minutes"))  # refused inside the time the setting gives
        self.assertEqual(queue.messages, [{"proposal_agreement_id": "a1", "real": False, "fresh": False}])
        database, queue = Agreements(free=False), Sent()
        with self.assertLogs("carmen"):
            self.assertEqual(asyncio.run(api.queue_proposals(SimpleNamespace(DB=database, RUNS=queue), "a1")), "RECENT")
        self.assertEqual(queue.messages, [])  # nothing is queued, so no model is paid and no email is sent

    def test_a_fresh_job_is_started_whatever_was_started_before(self):
        database, queue = Agreements(free=True), Sent()
        self.assertEqual(asyncio.run(api.queue_proposals(SimpleNamespace(DB=database, RUNS=queue), "a1", real=True, fresh=True)), "QUEUED")
        self.assertEqual(database.values[1], 1)  # the statement then takes the agreement at any time
        self.assertEqual(queue.messages, [{"proposal_agreement_id": "a1", "real": True, "fresh": True}])

    def test_a_start_that_cannot_be_queued_is_not_left_recorded(self):
        database = Agreements(free=True)
        with self.assertLogs("carmen", level="ERROR"):
            self.assertEqual(asyncio.run(api.queue_proposals(SimpleNamespace(DB=database, RUNS=Queue()), "a1")), "FAILED")
        # The record is taken back, or the next start would be refused although no job ran.
        self.assertEqual(len(database.ran), 1)
        self.assertIn("proposals_started_at = NULL", database.ran[0])
