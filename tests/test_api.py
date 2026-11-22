# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for identity and roles. Run with ./unit-test.sh. No Worker and no second user is needed."""

import asyncio
import base64
import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi.routing import APIRoute  # noqa: E402
from starlette.exceptions import HTTPException  # noqa: E402

import access  # noqa: E402
import app as api  # noqa: E402
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


def request_for(endpoint, identity):
    """A stand-in for a request that has matched a route and been identified."""
    state = SimpleNamespace() if identity is None else SimpleNamespace(identity=identity)
    return SimpleNamespace(scope={"endpoint": endpoint}, state=state)


def endpoint_with(needed):
    async def endpoint():
        pass

    return roles.action(needed)(endpoint) if needed else endpoint


class Authorize(unittest.TestCase):
    def refused(self, needed, identity) -> bool:
        try:
            asyncio.run(api.authorize(request_for(endpoint_with(needed), identity)))
            return False
        except HTTPException as e:
            self.assertEqual(e.status_code, 403)
            return True

    def test_simulated_users(self):
        for role in (READER, MEMBER, ADMIN):
            for needed in (READ, WRITE, DELETE, MANAGE):
                identity = {"role": role}
                self.assertEqual(self.refused(needed, identity), not roles.allowed(role, needed), f"{role} {needed}")

    def test_route_with_no_action_is_refused_for_everyone(self):
        for role in (READER, MEMBER, ADMIN):
            self.assertTrue(self.refused(None, {"role": role}))

    def test_no_identity_is_refused(self):
        self.assertTrue(self.refused(READ, None))
        self.assertTrue(self.refused(READ, {}))

    def test_public_needs_no_identity(self):
        self.assertFalse(self.refused(PUBLIC, None))


class Routes(unittest.TestCase):
    def test_every_route_declares_a_known_action(self):
        routes = [r for r in api.app.routes if isinstance(r, APIRoute)]
        self.assertEqual(len(routes), len(api.app.routes), "a route that is not an APIRoute is being served")
        for route in routes:
            self.assertIn(getattr(route.endpoint, "action", None), roles.ACTIONS, f"{route.path} declares no action")

    def test_only_health_is_public(self):
        public = [r.path for r in api.app.routes if getattr(r.endpoint, "action", None) == PUBLIC]
        self.assertEqual(public, [api.HEALTH_PATH])


class Claims(unittest.TestCase):
    def claims(self, **changes):
        return {"iss": f"https://{TEAM}", "aud": [AUD], "exp": NOW + 60, "nbf": NOW - 60, **changes}

    def test_good_claims_pass(self):
        access.check_claims(self.claims(), TEAM, AUD, NOW)
        access.check_claims(self.claims(aud=AUD), TEAM, AUD, NOW)

    def test_bad_claims_are_rejected(self):
        bad = {
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
        def part(obj):
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

        token = f"{part({'alg': 'none'})}.{part(self.claims())}."
        with self.assertRaises(access.AccessError):
            asyncio.run(access.verify(token, TEAM, AUD, NOW))


class Config(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "wrangler.jsonc").read_text()
        self.config = json.loads(self.text)

    def test_dev_user_is_never_in_the_config(self):
        self.assertNotIn("DEV_USER_EMAIL", self.text)

    def test_service_tokens_are_admin_on_preview_only(self):
        self.assertNotIn("SERVICE_TOKENS_ARE_ADMIN", self.config["vars"])
        for name, env in self.config["env"].items():
            if name != "preview":
                self.assertNotIn("SERVICE_TOKENS_ARE_ADMIN", env.get("vars", {}), name)


if __name__ == "__main__":
    unittest.main()
