# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for how a caller's identity is worked out (specs/045-identity-tests/spec.md):
a person, a service token, the local shortcut, and every refusal. Run with ./unit-test.sh."""

import asyncio
import pathlib
import sys
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi import Request  # noqa: E402

import access  # noqa: E402
import app as api  # noqa: E402
from roles import ADMIN, MACHINE_CALLER, MEMBER  # noqa: E402

PERSON = "someone@test.example"
RAY = {"cf-ray": "ray-1", "cf-access-jwt-assertion": "token"}  # a request that Cloudflare carried


class Users:
    """The app_user table. A new row gets the role that the database decides, here READER."""

    def __init__(self, *rows: dict[str, Any], away: bool = False) -> None:
        self.rows = {row["email"]: row for row in rows}
        self.away = away
        self.inserts: list[tuple[str, tuple[Any, ...]]] = []
        self.sql = ""
        self.values: tuple[Any, ...] = ("",)

    def prepare(self, sql: str) -> "Users":
        self.sql = sql
        return self

    def bind(self, *values: Any) -> "Users":
        self.values = values
        return self

    async def first(self) -> dict[str, Any] | None:
        if self.away:
            raise RuntimeError("the database is away")
        return self.rows.get(self.values[0])

    async def run(self) -> None:
        self.inserts.append((self.sql, self.values))
        new_id, email = self.values
        self.rows[email] = user(email, role="READER", id=new_id)


def user(email: str = PERSON, role: str = MEMBER, active: int = 1, id: str = "u1") -> dict[str, Any]:
    return {"id": id, "email": email, "display_name": None, "role": role, "active": active, "created_at": "2026-01-01T00:00:00Z"}


def resolve(claims: dict[str, Any] | Exception | None, users: Users | None = None, headers: dict[str, str] | None = None, **variables: str) -> tuple[Any, int | None]:
    """(the identity, the status of the refusal). claims is what the token check answers or raises;
    None means the token check must not be called at all."""
    env = SimpleNamespace(DB=users or Users(), ACCESS_TEAM_DOMAIN="team.example", ACCESS_AUD="aud", **variables)
    request = cast(Request, SimpleNamespace(scope={"env": env}, headers=RAY if headers is None else headers, state=SimpleNamespace(request_id="t")))

    async def verify(*_: object) -> dict[str, Any]:
        if claims is None:
            raise AssertionError("the token was checked")
        if isinstance(claims, Exception):
            raise claims
        return claims

    with mock.patch.object(access, "verify", verify):
        identity, refusal = asyncio.run(api.resolve_identity(request))
    return identity, refusal.status_code if refusal else None


class ServiceTokenTest(unittest.TestCase):
    TOKEN = {"common_name": "client-id.access"}

    def test_a_service_token_is_a_machine_caller_with_no_user(self):
        users = Users()
        identity, refusal = resolve(self.TOKEN, users)
        self.assertIsNone(refusal)
        self.assertEqual((identity["role"], identity["machine"], identity["id"], identity["email"]), (MACHINE_CALLER, True, None, None))
        self.assertEqual(users.inserts, [])  # it never gets an app_user row

    def test_a_service_token_is_admin_only_when_the_variable_is_exactly_true(self):
        self.assertEqual(resolve(self.TOKEN, SERVICE_TOKENS_ARE_ADMIN="true")[0]["role"], ADMIN)
        for value in ("True", "TRUE", "1", "yes", "false", " true"):
            self.assertEqual(resolve(self.TOKEN, SERVICE_TOKENS_ARE_ADMIN=value)[0]["role"], MACHINE_CALLER, value)

    def test_an_admin_service_token_is_still_a_machine_caller(self):
        self.assertTrue(resolve(self.TOKEN, SERVICE_TOKENS_ARE_ADMIN="true")[0]["machine"])

    def test_a_token_with_no_email_and_no_common_name_is_refused(self):
        for claims in ({}, {"common_name": ""}, {"email": "", "common_name": None}):
            self.assertEqual(resolve(claims, SERVICE_TOKENS_ARE_ADMIN="true"), (None, 403), claims)


class PersonTest(unittest.TestCase):
    def test_a_known_person_has_the_role_that_the_database_holds(self):
        identity, refusal = resolve({"email": PERSON}, Users(user(role=ADMIN)))
        self.assertIsNone(refusal)
        self.assertEqual((identity["id"], identity["role"], identity["machine"]), ("u1", ADMIN, False))

    def test_a_person_is_never_made_admin_by_the_service_token_variable(self):
        identity, _ = resolve({"email": PERSON}, Users(user(role=MEMBER)), SERVICE_TOKENS_ARE_ADMIN="true")
        self.assertEqual(identity["role"], MEMBER)

    def test_the_email_is_looked_up_in_lower_case(self):
        users = Users(user())
        identity, _ = resolve({"email": "Someone@Test.Example"}, users)
        self.assertEqual(identity["id"], "u1")
        self.assertEqual(users.inserts, [])

    def test_a_first_sign_in_creates_the_user_and_names_no_role(self):
        users = Users()
        identity, refusal = resolve({"email": "New@Test.Example"}, users)
        self.assertIsNone(refusal)
        self.assertEqual(len(users.inserts), 1)
        sql, values = users.inserts[0]
        self.assertIn("INSERT INTO app_user (id, email)", sql)  # the database decides the role
        self.assertEqual(values[1], "new@test.example")
        self.assertEqual((identity["email"], identity["role"], identity["machine"]), ("new@test.example", "READER", False))

    def test_a_deactivated_person_is_refused(self):
        self.assertEqual(resolve({"email": PERSON}, Users(user(role=ADMIN, active=0))), (None, 403))

    def test_a_database_that_is_away_answers_500(self):
        self.assertEqual(resolve({"email": PERSON}, Users(away=True)), (None, 500))


class TokenTest(unittest.TestCase):
    def test_a_missing_or_bad_token_answers_401(self):
        self.assertEqual(resolve(access.AccessError("no token")), (None, 401))

    def test_a_token_check_that_breaks_answers_500(self):
        self.assertEqual(resolve(RuntimeError("the keys could not be fetched")), (None, 500))


class LocalShortcutTest(unittest.TestCase):
    def test_a_local_run_acts_as_the_person_in_the_variable_with_no_token(self):
        identity, refusal = resolve(None, Users(user()), headers={}, DEV_USER_EMAIL=PERSON)
        self.assertIsNone(refusal)
        self.assertEqual((identity["id"], identity["machine"]), ("u1", False))

    def test_the_variable_is_ignored_for_a_request_that_cloudflare_carried(self):
        # Were the variable ever set in a deployed Worker, a request still needs its own token.
        self.assertEqual(resolve(access.AccessError("no token"), Users(user(role=ADMIN)), DEV_USER_EMAIL=PERSON), (None, 401))
        other = user("other@test.example", id="u2")
        identity, _ = resolve({"email": "other@test.example"}, Users(user(role=ADMIN), other), DEV_USER_EMAIL=PERSON)
        self.assertEqual(identity["id"], "u2")

    def test_a_local_run_with_no_variable_needs_a_token(self):
        self.assertEqual(resolve(access.AccessError("no token"), headers={}), (None, 401))


if __name__ == "__main__":
    unittest.main()
