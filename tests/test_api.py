# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for identity, roles, and record rules. Run with ./unit-test.sh. No Worker and no second user is needed."""

import asyncio
import base64
import datetime
import json
import pathlib
import sys
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi import Request  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from starlette.exceptions import HTTPException  # noqa: E402

import access  # noqa: E402
import app as api  # noqa: E402
import records  # noqa: E402
import roles  # noqa: E402
from roles import ADMIN, DELETE, MANAGE, MEMBER, PUBLIC, READ, READER, WRITE  # noqa: E402

TEAM = "team.invalid"
AUD = "a" * 64
NOW = 1_000_000.0


class RoleRule(unittest.TestCase):
    def test_every_role_against_every_action(self):
        expected = {
            READER: {READ},
            MEMBER: {READ, WRITE},
            ADMIN: {READ, WRITE, DELETE, MANAGE},
        }
        for role, may in expected.items():
            for needed in (READ, WRITE, DELETE, MANAGE):
                self.assertEqual(roles.allowed(role, needed), needed in may, f"{role} {needed}")

    def test_unknown_role_or_action_is_refused(self):
        for role in (None, "", "OWNER", "admin"):
            for needed in roles.ACTIONS:
                self.assertFalse(roles.allowed(role, needed), f"{role} {needed}")
        self.assertFalse(roles.allowed(ADMIN, "ANYTHING"))
        self.assertFalse(roles.allowed(ADMIN, None))

    def test_action_rejects_an_unknown_name(self):
        with self.assertRaises(ValueError):
            roles.action("READ_ALL")


def request_for(endpoint: Callable[..., Any], identity: dict[str, Any] | None) -> Request:
    """A stand-in for a request that has matched a route and been identified."""
    state = SimpleNamespace() if identity is None else SimpleNamespace(identity=identity)
    return cast(Request, SimpleNamespace(scope={"endpoint": endpoint}, state=state))


def endpoint_with(needed: str | None) -> Callable[..., Any]:
    async def endpoint() -> None:
        pass

    return roles.action(needed)(endpoint) if needed else endpoint


class Authorize(unittest.TestCase):
    def refused(self, needed: str | None, identity: dict[str, Any] | None) -> bool:
        try:
            asyncio.run(api.authorize(request_for(endpoint_with(needed), identity)))
            return False
        except HTTPException as e:
            self.assertEqual(e.status_code, 403)
            return True

    def test_simulated_users(self):
        for role in (READER, MEMBER, ADMIN):
            for needed in (READ, WRITE, DELETE, MANAGE):
                identity: dict[str, Any] = {"role": role}
                self.assertEqual(self.refused(needed, identity), not roles.allowed(role, needed), f"{role} {needed}")

    def test_route_with_no_action_is_refused_for_everyone(self):
        for role in (READER, MEMBER, ADMIN):
            self.assertTrue(self.refused(None, {"role": role}))

    def test_no_identity_is_refused(self):
        self.assertTrue(self.refused(READ, None))
        self.assertTrue(self.refused(READ, {}))

    def test_public_needs_no_identity(self):
        self.assertFalse(self.refused(PUBLIC, None))


class OwnRecords(unittest.TestCase):
    def may(self, role: str, caller: str | None, owner: str | None) -> bool:
        request = cast(Request, SimpleNamespace(state=SimpleNamespace(identity={"role": role, "id": caller})))
        return api.may_delete(request, owner)

    def test_a_member_deletes_only_their_own(self):
        self.assertTrue(self.may(MEMBER, "u1", "u1"))
        self.assertFalse(self.may(MEMBER, "u1", "u2"))
        self.assertFalse(self.may(MEMBER, "u1", None))
        self.assertFalse(self.may(MEMBER, None, None))

    def test_an_admin_deletes_anything(self):
        for owner in ("u1", "u2", None):
            self.assertTrue(self.may(ADMIN, "u1", owner))
        self.assertTrue(self.may(ADMIN, None, None))  # a machine caller on the preview


class MachineCaller(unittest.TestCase):
    def test_a_service_token_may_call_the_machine_methods_and_nothing_else(self):
        self.assertTrue(roles.allowed(roles.MACHINE_CALLER, roles.MACHINE))
        for needed in (READ, WRITE, DELETE, MANAGE):
            self.assertFalse(roles.allowed(roles.MACHINE_CALLER, needed), needed)

    def test_of_the_people_a_member_and_an_admin_may_call_them(self):
        for role in (MEMBER, ADMIN):
            self.assertTrue(roles.allowed(role, roles.MACHINE), role)
        self.assertFalse(roles.allowed(READER, roles.MACHINE))

    def test_only_the_two_machine_endpoints_declare_the_action(self):
        routes = [r for r in api.app.routes if isinstance(r, APIRoute)]
        paths = sorted(r.path for r in routes if getattr(r.endpoint, "action", None) == roles.MACHINE)
        self.assertEqual(paths, ["/api/v1/machine/facts", "/api/v1/machine/leads"])

    def test_the_binding_door_answers_a_refusal_and_does_not_raise(self):
        env = SimpleNamespace(DB=None)
        answer = asyncio.run(api.machine_call(env, api.create_lead, {"name": "No source"}))
        self.assertEqual(answer, {"ok": False, "error": "BAD_REQUEST", "message": "source is required"})
        answer = asyncio.run(api.machine_call(env, api.create_lead, "not an object"))
        self.assertEqual(answer["error"], "BAD_REQUEST")
        answer = asyncio.run(api.machine_call(env, api.add_facts, {"source": "API", "company_id": "x", "facts": []}))
        self.assertEqual(answer["error"], "INTERNAL")  # no database here: the failure is an answer too


class Settings(unittest.TestCase):
    def test_a_value_its_setting_can_hold_is_accepted(self):
        good = {
            "score.min_confidence": 40,
            "score.skip.max_fit": "0",
            "score.reach_out.min_need": "100",
            "digest.hour_local": "23",
            "digest.time_zone": "Europe/Berlin",
            "digest.days": "mon, wed ,FRI",
            "digest.recipient": "someone@test.example",
            "renewal.default_notice_days": 0,
            "import.max_rows": "5000",
            "document.max_size_bytes": 1,
            "ai.research.max_pages": 8,
            "proposal.max_tries": 3,
            "uptime.window_days": "14",
            "uptime.sites_per_run": 25,
            "uptime.hours_between_checks": "23",
            "some.future.setting": "any text",
        }
        for key, value in good.items():
            self.assertIsNone(records.setting_problem(key, value), key)

    def test_a_value_its_setting_cannot_hold_is_refused(self):
        bad: list[tuple[str, object]] = [
            ("score.min_confidence", 101),
            ("score.min_confidence", "-1"),
            ("score.min_confidence", "forty"),
            ("digest.hour_local", 24),
            ("digest.time_zone", "Mars/Olympus_Mons"),
            ("digest.days", "MON,FUNDAY"),
            ("digest.days", "MON,MON"),
            ("digest.recipient", "not an address"),
            ("digest.recipient", "a@test.example, b@test.example"),
            ("renewal.default_notice_days", "1.5"),
            ("import.max_rows", 0),
            ("proposal.max_tries", 0),
            ("uptime.window_days", "0"),
            ("uptime.sites_per_run", "many"),
            ("uptime.hours_between_checks", 0),
            ("some.future.setting", ""),
            ("some.future.setting", None),
            ("score.min_confidence", True),
            ("digest.days", ["MON"]),
        ]
        for key, value in bad:
            self.assertIsNotNone(records.setting_problem(key, value), f"{key} = {value!r}")

    def test_only_an_administrator_changes_settings(self):
        actions = {(r.path, tuple(r.methods or ())): getattr(r.endpoint, "action") for r in api.app.routes if isinstance(r, APIRoute)}
        self.assertEqual(actions[("/api/v1/settings", ("GET",))], READ)
        self.assertEqual(actions[("/api/v1/settings", ("PATCH",))], MANAGE)


class RenewalPolicy(unittest.TestCase):
    GOOD = {"status": "ACTIVE", "renewal_policy": "APPROVAL", "end_basis": "SIGNED_CONTRACT", "end_date": "2027-05-31", "term_months": 6}

    def problem(self, **changes: Any) -> str | None:
        return records.renewal_problem({**self.GOOD, **changes})

    def test_a_draft_may_lack_everything(self):
        self.assertIsNone(records.renewal_problem({"status": "DRAFT"}))
        self.assertIsNone(records.renewal_problem({}))

    def test_an_active_agreement_needs_a_policy_and_a_basis(self):
        self.assertIsNone(self.problem())
        self.assertIn("renewal policy", self.problem(renewal_policy=None) or "")
        self.assertIn("end basis", self.problem(end_basis=None) or "")

    def test_an_internal_decision_needs_a_reason(self):
        self.assertIn("reason", self.problem(end_basis="INTERNAL_DECISION") or "")
        self.assertIn("reason", self.problem(end_basis="INTERNAL_DECISION", end_basis_reason="  ") or "")
        self.assertIsNone(self.problem(end_basis="INTERNAL_DECISION", end_basis_reason="Until the site is rebuilt"))

    def test_each_policy_has_its_own_rule_for_the_end_date(self):
        self.assertIn("no end date", self.problem(renewal_policy="AUTO_RENEW") or "")
        self.assertIsNone(self.problem(renewal_policy="AUTO_RENEW", end_date=None, term_months=None))
        self.assertIn("term in months", self.problem(term_months=None) or "")
        self.assertIn("term in months", self.problem(end_date=None) or "")
        self.assertIsNone(self.problem(renewal_policy="ONE_TIME", end_date=None, term_months=None))
        self.assertIsNone(self.problem(renewal_policy="ONE_TIME"))

    def test_a_term_is_added_in_whole_months(self):
        self.assertEqual(records.add_months("2026-11-24", 6), "2027-05-24")
        self.assertEqual(records.add_months("2026-12-15", 1), "2027-01-15")
        self.assertEqual(records.add_months("2027-01-31", 1), "2027-02-28")  # February has no 31st
        self.assertEqual(records.add_months("2024-02-29", 12), "2025-02-28")
        self.assertEqual(records.add_months("2026-03-31", 12), "2027-03-31")

    def test_approving_a_renewal_is_a_write(self):
        routes = {r.path: r for r in api.app.routes if isinstance(r, APIRoute)}
        self.assertEqual(getattr(routes["/api/v1/agreements/{agreement_id}/renewal"].endpoint, "action"), WRITE)


class Routes(unittest.TestCase):
    def test_every_route_declares_a_known_action(self):
        routes = [r for r in api.app.routes if isinstance(r, APIRoute)]
        self.assertEqual(len(routes), len(api.app.routes), "a route that is not an APIRoute is being served")
        for route in routes:
            self.assertIn(getattr(route.endpoint, "action", None), roles.ACTIONS, f"{route.path} declares no action")

    def test_an_uptime_check_stores_the_fact_only_when_the_share_changed(self):
        class Database:
            def __init__(self):
                self.sent: list[tuple[str, tuple[object, ...]]] = []

            def prepare(self, sql: str) -> "Database":
                self.sql = sql
                return self

            def bind(self, *values: object) -> None:
                self.sent.append((self.sql, values))

        company = {"id": "c1", "checks": 3, "ups": 3, "stored": "100"}
        up = {"up": 1, "status": 200, "duration_ms": 5, "detail": None}
        down = {"up": 0, "status": None, "duration_ms": 5, "detail": "no answer in 10 seconds"}
        same, changed = Database(), Database()
        self.assertEqual(len(api.uptime_writes(same, company, up)), 1)  # still 100: the check alone
        self.assertIn("uptime_check", same.sent[0][0])
        self.assertEqual(len(api.uptime_writes(changed, company, down)), 2)  # 3 of 4: the check and the fact
        self.assertIn("enrichment_fact", changed.sent[1][0])
        self.assertIn("75", changed.sent[1][1])
        self.assertIn("UPTIME_CHECK", changed.sent[1][1])

    def test_only_a_lead_or_a_prospect_with_a_public_website_is_measured(self):
        lead = {"stage": "LEAD", "archived_at": None, "website": "acme.com"}
        self.assertTrue(api.may_measure(lead))
        self.assertTrue(api.may_measure({**lead, "stage": "PROSPECT"}))
        for change in ({"stage": "CUSTOMER"}, {"stage": "DISQUALIFIED"}, {"archived_at": "2026-11-26T00:00:00Z"}, {"website": None}, {"website": "localhost"}):
            self.assertFalse(api.may_measure({**lead, **change}), change)

    def test_a_setting_of_the_uptime_checker_is_used_first_and_falls_back_to_its_constant(self):
        self.assertEqual(api.uptime_setting({"uptime.window_days": "7"}, "uptime.window_days"), 7)
        self.assertEqual(api.uptime_setting({}, "uptime.window_days"), 14)
        self.assertEqual(api.uptime_setting({"uptime.window_days": "many"}, "uptime.window_days"), 14)
        self.assertEqual(api.uptime_setting({"uptime.sites_per_run": "5"}, "uptime.sites_per_run"), 5)
        self.assertEqual(api.uptime_setting({}, "uptime.sites_per_run"), 25)
        self.assertEqual(api.uptime_setting({"uptime.hours_between_checks": "0"}, "uptime.hours_between_checks"), 23)

    def test_proposals_are_read_by_anyone_and_started_by_an_administrator(self):
        routes = {r.path: r for r in api.app.routes if isinstance(r, APIRoute)}
        self.assertEqual(getattr(routes["/api/v1/companies/{company_id}/proposals"].endpoint, "action"), roles.READ)
        self.assertEqual(getattr(routes["/api/v1/agreements/{agreement_id}/proposals"].endpoint, "action"), roles.MANAGE)

    def test_a_job_has_a_request_with_no_caller_that_names_the_job(self):
        env = SimpleNamespace(DB="the database")
        request = api.job_request(env, "proposals")
        self.assertEqual(api.db(request), "the database")
        self.assertIsNone(request.state.identity["id"])
        self.assertEqual(request.state.request_id, "proposals")

    def test_a_proposals_job_that_cannot_be_queued_says_so_and_does_not_raise(self):
        class Queue:
            async def send(self, message: dict[str, Any]) -> None:
                raise RuntimeError("the queue is down")

        self.assertFalse(asyncio.run(api.queue_proposals(SimpleNamespace(RUNS=Queue()), "a1")))

    def test_the_scheduler_is_run_by_an_administrator_only(self):
        routes = {r.path: r for r in api.app.routes if isinstance(r, APIRoute)}
        self.assertEqual(getattr(routes["/api/v1/scheduler/run"].endpoint, "action"), MANAGE)
        self.assertEqual(getattr(routes["/api/v1/notifications"].endpoint, "action"), READ)

    def test_a_scheduler_run_with_no_database_fails_as_an_answer(self):
        answer = asyncio.run(api.run_scheduler(SimpleNamespace(DB=None)))
        self.assertEqual(answer, {"completed": 0, "expired": 0, "uptime": 0, "alerts": 0, "digest": None, "failed": ["load_settings"]})

    def test_only_health_is_public(self):
        routes = [r for r in api.app.routes if isinstance(r, APIRoute)]
        public = [r.path for r in routes if getattr(r.endpoint, "action", None) == PUBLIC]
        self.assertEqual(public, [api.HEALTH_PATH])


class Claims(unittest.TestCase):
    def claims(self, **changes: Any) -> dict[str, Any]:
        return {"iss": f"https://{TEAM}", "aud": [AUD], "exp": NOW + 60, "nbf": NOW - 60, **changes}

    def test_good_claims_pass(self):
        access.check_claims(self.claims(), TEAM, AUD, NOW)
        access.check_claims(self.claims(aud=AUD), TEAM, AUD, NOW)

    def test_bad_claims_are_rejected(self):
        bad: dict[str, dict[str, Any]] = {
            "wrong issuer": {"iss": "https://other.invalid"},
            "no issuer": {"iss": None},
            "wrong audience": {"aud": ["b" * 64]},
            "no audience": {"aud": None},
            "expired": {"exp": NOW - 1},
            "expires now": {"exp": NOW},
            "no expiry": {"exp": None},
            "not valid yet": {"nbf": NOW + 60},
        }
        for name, changes in bad.items():
            with self.assertRaises(access.AccessError, msg=name):
                access.check_claims(self.claims(**changes), TEAM, AUD, NOW)

    def test_missing_or_malformed_token_is_rejected(self):
        for token in (None, "", "abc", "a.b", "a.b.c", "a.b.c.d"):
            with self.assertRaises(access.AccessError, msg=repr(token)):
                asyncio.run(access.verify(token, TEAM, AUD, NOW))

    def test_unsigned_algorithm_is_rejected(self):
        def part(obj: dict[str, Any]) -> str:
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

        token = f"{part({'alg': 'none'})}.{part(self.claims())}."
        with self.assertRaises(access.AccessError):
            asyncio.run(access.verify(token, TEAM, AUD, NOW))


class Records(unittest.TestCase):
    def test_domain_is_derived_from_the_website(self):
        cases = {
            "https://WWW.Acme.com/about?x=1": "acme.com",
            "acme.com": "acme.com",
            "www.acme.com/": "acme.com",
            "http://shop.acme.com:8080/a": "shop.acme.com",
            "  Acme.com  ": "acme.com",
            "": None,
            None: None,
            "https://": None,
        }
        for website, domain in cases.items():
            self.assertEqual(records.derive_domain(website), domain, repr(website))

    def test_clean_keeps_only_writable_keys(self):
        body = {"name": " Acme ", "city": "", "employee_count": 12, "summary": None, "id": "x", "fit_score": 99, "junk": 1}
        expected = {"name": "Acme", "city": None, "employee_count": 12, "summary": None}
        self.assertEqual(records.clean(body, records.COMPANY_FIELDS), expected)
        self.assertEqual(records.clean({"is_primary": True}, records.CONTACT_FIELDS), {"is_primary": 1})
        self.assertEqual(records.clean({"is_primary": 0}, records.CONTACT_FIELDS), {"is_primary": 0})

    def test_clean_rejects_a_wrong_type(self):
        bad: list[tuple[dict[str, Any], dict[str, type]]] = [
            ({"name": 5}, records.COMPANY_FIELDS),
            ({"employee_count": "12"}, records.COMPANY_FIELDS),
            ({"employee_count": True}, records.COMPANY_FIELDS),
            ({"employee_count": 1.5}, records.COMPANY_FIELDS),
            ({"is_primary": "yes"}, records.CONTACT_FIELDS),
            ({"is_primary": 2}, records.CONTACT_FIELDS),
        ]
        for body, fields in bad:
            with self.assertRaises(records.BadBody, msg=repr(body)):
                records.clean(body, fields)

    def test_code_columns_are_writable_columns(self):
        self.assertLessEqual(set(records.COMPANY_CODES), set(records.COMPANY_FIELDS))
        self.assertLessEqual(set(records.CONTACT_CODES), set(records.CONTACT_FIELDS))
        self.assertLessEqual(set(records.ACTIVITY_CODES), set(records.ACTIVITY_FIELDS))
        self.assertLessEqual(set(records.TASK_CODES), set(records.TASK_FIELDS))
        self.assertLessEqual(set(records.DEAL_CODES), set(records.DEAL_FIELDS))
        self.assertLessEqual(set(records.AGREEMENT_CODES), set(records.AGREEMENT_FIELDS))

    def test_set_by_the_api_is_not_writable(self):
        for key in ("id", "company_id", "created_by", "created_at"):
            self.assertNotIn(key, records.ACTIVITY_FIELDS)
        for key in ("id", "completed_at", "created_at"):
            self.assertNotIn(key, records.TASK_FIELDS)

    def test_dates_are_put_in_iso_form(self):
        dates = {
            "2026-12-01": "2026-12-01",
            "12/1/2026": "2026-12-01",
            "12/01/26": "2026-12-01",
            "12/01/2026": "2026-12-01",
            "12-01-2026": "2026-12-01",
            "01-01-2026": "2026-01-01",
            "1-5-2026": "2026-01-05",
            "01 Dec 2026": "2026-12-01",
            "01 December 2026": "2026-12-01",
            "2026/12/01": "2026-12-01",
            "December 1, 2026": "2026-12-01",
            "Dec 1, 2026": "2026-12-01",
            "1 Dec 2026": "2026-12-01",
            "2026-12-01T14:30:00Z": "2026-12-01",
        }
        for text, expected in dates.items():
            self.assertEqual(records.iso_date(text), expected, text)
        moments = {
            "2026-12-01T14:30:00Z": "2026-12-01T14:30:00Z",
            "2026-12-01 14:30": "2026-12-01T14:30:00Z",
            "2026-12-01T09:30:00-05:00": "2026-12-01T14:30:00Z",
            "2026-12-01": "2026-12-01T00:00:00Z",
            "12/1/2026 2:30 PM": "2026-12-01T14:30:00Z",
            "Dec 1, 2026 14:30": "2026-12-01T14:30:00Z",
            "12-01-2026 14:30": "2026-12-01T14:30:00Z",
            "01 Dec 2026 2:30 PM": "2026-12-01T14:30:00Z",
        }
        for text, expected in moments.items():
            self.assertEqual(records.iso_timestamp(text), expected, text)

    def test_a_fuzzy_date_is_tried_last(self):
        now = datetime.datetime(2026, 11, 23, 15, 0, 0)  # a Monday
        dates = {
            "tomorrow": "2026-11-24",
            "today": "2026-11-23",
            "yesterday": "2026-11-22",
            "next week": "2026-11-30",
            "next Tuesday": "2026-12-01",
            "Friday": "2026-11-27",
            "in 3 days": "2026-11-26",
            "next month": "2026-12-01",
            "end of month": "2026-11-30",
        }
        for text, expected in dates.items():
            self.assertEqual(records.iso_date(text, now), expected, text)
        self.assertEqual(records.iso_timestamp("friday at 3pm", now), "2026-11-27T15:00:00Z")
        self.assertEqual(records.iso_timestamp("noon tomorrow", now), "2026-11-24T12:00:00Z")
        # A real date is never read as a phrase.
        self.assertEqual(records.iso_date("Dec 1, 2026", now), "2026-12-01")

    def test_a_date_that_is_not_understood_is_refused(self):
        for text in ("13/45/2026", "2026-02-30", "2026", "soon", "12/01", "x", "TBD", "Acme next week please", "next"):
            for convert in (records.iso_date, records.iso_timestamp):
                with self.assertRaises(records.BadBody, msg=text):
                    convert(text)
        with self.assertRaises(records.BadBody):
            records.clean({"due_date": "13/45/2026"}, records.TASK_FIELDS)

    def test_clean_reformats_date_columns_only(self):
        self.assertEqual(records.clean({"due_date": " 12/1/2026 ", "title": "12/1/2026"}, records.TASK_FIELDS), {"due_date": "2026-12-01", "title": "12/1/2026"})
        self.assertEqual(records.clean({"occurred_at": "12/1/2026", "body": None}, records.ACTIVITY_FIELDS), {"occurred_at": "2026-12-01T00:00:00Z", "body": None})
        self.assertEqual(records.clean({"due_date": None}, records.TASK_FIELDS), {"due_date": None})

    def test_every_date_column_is_reformatted_by_its_name(self):
        # The rule is by column name, so a date field added by any later feature is covered.
        self.assertEqual(records.iso("signed_date", "12/01/2026"), "2026-12-01")
        self.assertEqual(records.iso("expected_close_date", "01 Dec 2026"), "2026-12-01")
        self.assertEqual(records.iso("observed_at", "12-01-2026"), "2026-12-01T00:00:00Z")
        self.assertEqual(records.iso("name", "12/01/2026"), "12/01/2026")
        for fields in (records.COMPANY_FIELDS, records.CONTACT_FIELDS, records.ACTIVITY_FIELDS, records.TASK_FIELDS):
            for column in fields:
                expected = "2026-12-01" if column.endswith("_date") else "2026-12-01T00:00:00Z" if column.endswith("_at") else "12/01/2026"
                if fields[column] is str:
                    self.assertEqual(records.clean({column: "12/01/2026"}, fields)[column], expected, column)

    def test_fact_keys_and_values_are_normalized(self):
        keys = {"Has Website": "has_website", "has-website": "has_website", " employee_count ": "employee_count", "A.B/C": "a_b_c", "--": "", "": ""}
        for text, key in keys.items():
            self.assertEqual(records.fact_key(text), key, text)
        values: list[tuple[str, str | None, str]] = [
            ("yes", "YES_NO", "YES"),
            ("No", "YES_NO", "NO"),
            ("probably", "YES_NO", "probably"),
            ("yes", "TEXT", "yes"),
            ("yes", None, "yes"),
            ("12/01/2026", "DATE", "2026-12-01"),
            ("12/01/2026", "TEXT", "12/01/2026"),
            ("12", "NUMBER", "12"),
        ]
        for value, value_type, expected in values:
            self.assertEqual(records.fact_value(value, value_type), expected, f"{value} {value_type}")
        with self.assertRaises(records.BadBody):
            records.fact_value("13/45/2026", "DATE")

    def test_a_change_is_described_with_the_old_values(self):
        before = {"value": "YES", "notes": None, "source": "MANUAL"}
        self.assertEqual(records.describe_change("Fact has_website", before, {"value": "NO"}), "Fact has_website edited: value 'YES' to 'NO'")
        self.assertEqual(
            records.describe_change("Fact has_website", before, {"value": "NO", "notes": "checked"}),
            "Fact has_website edited: value 'YES' to 'NO', notes None to 'checked'",
        )
        self.assertEqual(records.describe_change("Fact has_website", before, None), "Fact has_website deleted. It held: value 'YES', source 'MANUAL'")

    def test_today_is_the_date_in_the_time_zone(self):
        late = datetime.datetime(2026, 11, 24, 3, 30, tzinfo=datetime.UTC)  # 22:30 on the 23rd in New York
        self.assertEqual(records.local_date("America/New_York", late), ("2026-11-23", "America/New_York"))
        self.assertEqual(records.local_date("Asia/Tokyo", late), ("2026-11-24", "Asia/Tokyo"))
        self.assertEqual(records.local_date("UTC", late), ("2026-11-24", "UTC"))
        for zone in (None, "", "Not/AZone", "../etc/passwd"):
            self.assertEqual(records.local_date(zone, late), ("2026-11-24", "UTC"), repr(zone))

    def test_a_list_of_ids(self):
        self.assertIsNone(records.id_list({}, "service_ids"))
        self.assertEqual(records.id_list({"service_ids": []}, "service_ids"), [])
        self.assertEqual(records.id_list({"service_ids": ["b", "a", "b"]}, "service_ids"), ["a", "b"])
        for bad in ("a", None, [1], ["a", ""], {"a": 1}):
            with self.assertRaises(records.BadBody, msg=repr(bad)):
                records.id_list({"service_ids": bad}, "service_ids")

    def test_file_names(self):
        names = {
            "Signed MSA.pdf": "Signed MSA.pdf",
            "../../etc/passwd": "passwd",
            "C:\\Users\\ed\\contract.docx": "contract.docx",
            " spaced.txt ": "spaced.txt",
            "tab\tname.txt": "tabname.txt",
            "": "document",
            None: "document",
            "dir/": "document",
        }
        for given, expected in names.items():
            self.assertEqual(records.file_name_of(given), expected, repr(given))
        self.assertEqual(len(records.file_name_of("x" * 400)), 255)

    def test_download_header(self):
        self.assertEqual(records.download_header("smoke.txt"), "attachment; filename=\"smoke.txt\"; filename*=UTF-8''smoke.txt")
        accented = records.download_header("Signed MSA \u00e9 \"final\".txt")
        self.assertEqual(accented, "attachment; filename=\"Signed MSA _ _final_.txt\"; filename*=UTF-8''Signed%20MSA%20%C3%A9%20%22final%22.txt")
        self.assertNotIn("\n", records.download_header("a\nb"))

    def test_database_errors_map_to_statuses(self):
        cases = {
            "D1_ERROR: UNIQUE constraint failed: company.domain: SQLITE_CONSTRAINT": 409,
            "D1_ERROR: FOREIGN KEY constraint failed: SQLITE_CONSTRAINT": 400,
            "D1_ERROR: NOT NULL constraint failed: company.stage: SQLITE_CONSTRAINT": 400,
            "D1_ERROR: SRT violation: company.stage not in COMPANY_STAGE": 400,
            "D1_ERROR: no such table: company": 500,
        }
        for text, status in cases.items():
            self.assertEqual(api.db_status(Exception(text)), status, text)
        self.assertEqual(api.db_status(records.BadBody("x")), 400)


class Cors(unittest.TestCase):
    def test_only_a_listed_origin_gets_headers(self):
        allowed = "http://localhost:3000, https://app.example.invalid"
        for origin in ("http://localhost:3000", "https://app.example.invalid"):
            headers = api.cors_headers(origin, allowed)
            self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
            self.assertEqual(headers["Access-Control-Allow-Credentials"], "true")
        for origin in (None, "", "http://localhost:3001", "https://evil.invalid", "localhost:3000", "*", "null"):
            self.assertEqual(api.cors_headers(origin, allowed), {}, repr(origin))

    def test_no_allowed_origins_means_no_headers(self):
        for allowed in (None, "", " "):
            self.assertEqual(api.cors_headers("http://localhost:3000", allowed), {}, repr(allowed))


class Config(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "wrangler.jsonc").read_text()
        self.config = json.loads(self.text)

    def test_dev_user_is_never_in_the_config(self):
        self.assertNotIn("DEV_USER_EMAIL", self.text)

    def test_preview_never_shares_data_with_production(self):
        preview = self.config["env"]["preview"]
        self.assertNotEqual(preview["d1_databases"][0]["database_id"], self.config["d1_databases"][0]["database_id"])
        self.assertNotEqual(preview["r2_buckets"][0]["bucket_name"], self.config["r2_buckets"][0]["bucket_name"])
        self.assertNotEqual(preview["queues"]["producers"][0]["queue"], self.config["queues"]["producers"][0]["queue"])
        for environment in (self.config, preview):
            self.assertEqual(environment["d1_databases"][0]["binding"], "DB")
            self.assertEqual(environment["r2_buckets"][0]["binding"], "DOCS")
            self.assertEqual(environment["queues"]["producers"][0]["binding"], "RUNS")
            # The provider keys are bound in both, so that an adapter can be tried on the preview first.
            self.assertEqual([secret["binding"] for secret in environment["secrets_store_secrets"]], ["CLAUDE_API_KEY", "TYPESAFE_API_KEY"])
            # Workers AI is an account-wide service: the binding is the same in both.
            self.assertEqual(environment["ai"], {"binding": "AI"})
            # The prompt files are packaged with the Worker.
            self.assertIn("prompts/*.md", environment["rules"][0]["globs"])
            # Each Worker consumes its own queue, one run at a time, with one more delivery after a crash.
            consumer = environment["queues"]["consumers"][0]
            self.assertEqual(consumer["queue"], environment["queues"]["producers"][0]["queue"])
            self.assertEqual((consumer["max_batch_size"], consumer["max_retries"]), (1, 1))

    def test_no_secret_value_is_in_the_config(self):
        # A binding names a secret; it never holds one. An Anthropic key starts with sk-ant.
        self.assertNotIn("sk-ant", self.text.lower())
        for environment in (self.config, self.config["env"]["preview"]):
            for secret in environment["secrets_store_secrets"]:
                self.assertEqual(set(secret), {"binding", "store_id", "secret_name"})

    def test_the_smoke_test_calls_no_paid_model(self):
        smoke = (ROOT / "preview-test.sh").read_text()
        self.assertIn('select(.provider == "STUB")', smoke)
        for provider in ("CLAUDE", "WORKERS_AI", "TYPESAFE"):
            self.assertNotIn(provider, smoke)

    def test_production_allows_no_other_origin(self):
        self.assertEqual(self.config["vars"]["ALLOWED_ORIGINS"], "")
        self.assertNotIn("*", self.config["env"]["preview"]["vars"]["ALLOWED_ORIGINS"])

    def test_service_tokens_are_admin_on_preview_only(self):
        self.assertNotIn("SERVICE_TOKENS_ARE_ADMIN", self.config["vars"])
        for name, env in self.config["env"].items():
            if name != "preview":
                self.assertNotIn("SERVICE_TOKENS_ARE_ADMIN", env.get("vars", {}), name)


if __name__ == "__main__":
    unittest.main()
