# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the queue's run handler: what it reports when a failure cannot be recorded.
Also for the record that a rescoring is waiting, for the start of a proposals job, and for the order in which an email is recorded and sent.
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


class Result:
    def __init__(self, mail: "Mail") -> None:
        self.mail = mail
        self.values: tuple[object, ...] = ()

    def bind(self, *values: object) -> "Result":
        self.values = values
        return self

    async def first(self) -> dict[str, Any]:
        if not self.mail.results:
            raise RuntimeError("the database is away")
        self.mail.events.append("result")
        return {"id": "n1", "status": self.values[0]}


class Mail:
    """A database for one message. It notes the order of what happens. taken: another run recorded
    the message first. records, results: whether the record and the result can be written."""

    def __init__(self, taken: bool = False, records: bool = True, results: bool = True) -> None:
        self.taken, self.records, self.results = taken, records, results
        self.events: list[str] = []
        self.recorded = 0

    def prepare(self, sql: str) -> Result:
        return Result(self)

    async def batch(self, statements: list[Any]) -> list[dict[str, Any]]:
        if not self.records:
            raise RuntimeError("the database is away")
        self.events.append("record")
        self.recorded = len(statements)
        return [{"results": [] if self.taken else [{"id": "n1", "status": "QUEUED"}]}] + [{"results": []} for _ in statements[1:]]


class Post:
    """An email provider that notes when it sends, and can fail."""

    status = "SENT"

    def __init__(self, mail: Mail, works: bool = True) -> None:
        self.mail, self.works = mail, works

    async def send(self, env: Any, recipient: str, subject: str, body: str) -> str | None:
        self.mail.events.append("send")
        if not self.works:
            raise RuntimeError("the provider is away")
        return "ref-1"


class EmailOrderTest(unittest.TestCase):
    def message(self, mail: Mail, works: bool = True, task: dict[str, Any] | None = None) -> dict[str, Any] | None:
        env = SimpleNamespace(DB=mail)
        to = "Some One <someone@test.example>"
        return asyncio.run(api.notification(env, Post(mail, works), "RENEWAL_ALERT", to, "Subject", "Body", "a1", "2026-11-27", task))

    def test_a_message_is_recorded_then_sent_and_then_gets_its_result(self):
        mail = Mail()
        stored = self.message(mail, task={"id": "t1", "title": "Renewal", "owner_id": None})
        self.assertEqual(mail.events, ["record", "send", "result"])
        self.assertEqual(mail.recorded, 2)  # the task is stored with the record
        self.assertEqual(stored, {"id": "n1", "status": "SENT"})

    def test_a_message_that_cannot_be_recorded_is_not_sent(self):
        mail = Mail(records=False)
        with self.assertRaises(RuntimeError):  # the scheduler's step fails, and the next run tries again
            self.message(mail)
        self.assertEqual(mail.events, [])

    def test_a_message_that_another_run_recorded_is_not_sent(self):
        mail = Mail(taken=True)
        self.assertIsNone(self.message(mail))
        self.assertEqual(mail.events, ["record"])

    def test_a_message_that_cannot_be_sent_is_recorded_as_failed(self):
        mail = Mail()
        self.assertEqual(self.message(mail, works=False), {"id": "n1", "status": "FAILED"})
        self.assertEqual(mail.events, ["record", "send", "result"])

    def test_a_result_that_cannot_be_stored_leaves_the_message_queued_and_does_not_raise(self):
        mail = Mail(results=False)
        self.assertEqual(self.message(mail), {"id": "n1", "status": "QUEUED"})  # the digest names it later
        self.assertEqual(mail.events, ["record", "send"])
