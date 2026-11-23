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


class Routes(unittest.TestCase):
    def test_every_route_declares_a_known_action(self):
        routes = [r for r in api.app.routes if isinstance(r, APIRoute)]
        self.assertEqual(len(routes), len(api.app.routes), "a route that is not an APIRoute is being served")
        for route in routes:
            self.assertIn(getattr(route.endpoint, "action", None), roles.ACTIONS, f"{route.path} declares no action")

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
