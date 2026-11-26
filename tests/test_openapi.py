# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the served OpenAPI specification and the Swagger UI. See specs/031-openapi/spec.md."""

import asyncio
import json
import pathlib
import sys
import tomllib
import unittest
from types import SimpleNamespace
from collections.abc import MutableMapping
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi.routing import APIRoute  # noqa: E402
from openapi_spec_validator import OpenAPIV31SpecValidator  # noqa: E402

import app as api  # noqa: E402
from roles import READ, READER  # noqa: E402

OWN = {api.OPENAPI_PATH, api.DOCS_PATH}


class Users:
    """A database that knows one user, with the given role."""

    def __init__(self, role: str) -> None:
        self.role = role

    def prepare(self, sql: str) -> "Users":
        return self

    def bind(self, *values: object) -> "Users":
        return self

    async def first(self) -> dict[str, Any]:
        return {"id": "u1", "email": "someone@test.example", "display_name": None, "role": self.role, "active": 1, "created_at": "2026-11-26T00:00:00Z"}


def signed_in(role: str) -> SimpleNamespace:
    # A local run's way to name the user; the request carries no Cf-Ray header, as on a local run.
    return SimpleNamespace(DEV_USER_EMAIL="someone@test.example", DB=Users(role))


NOBODY = SimpleNamespace(ACCESS_TEAM_DOMAIN="team.invalid", ACCESS_AUD="a" * 64)


def get(path: str, env: SimpleNamespace) -> tuple[int, bytes]:
    """One GET through the whole application: the middleware, authorize, and the route."""
    sent: list[MutableMapping[str, Any]] = []
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("test.invalid", 443),
        "client": ("test.invalid", 1),
        "env": env,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    asyncio.run(api.app(scope, receive, send))
    return int(sent[0]["status"]), b"".join(message.get("body", b"") for message in sent[1:])


class Specification(unittest.TestCase):
    def fetched(self) -> dict[str, Any]:
        status, body = get(api.OPENAPI_PATH, signed_in(READER))
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_the_lowest_role_reads_valid_openapi_3_1(self):
        spec = self.fetched()
        self.assertTrue(spec["openapi"].startswith("3.1."))
        OpenAPIV31SpecValidator(spec).validate()  # raises when the document is not valid

    def test_the_title_and_the_version_are_the_apps(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        info = self.fetched()["info"]
        self.assertEqual((info["title"], info["version"]), (project["name"], project["version"]))

    def test_it_lists_every_route_the_app_has(self):
        spec = self.fetched()
        listed = {(path, method.upper()) for path, item in spec["paths"].items() for method in item}
        routes = [r for r in api.app.routes if isinstance(r, APIRoute) and r.path not in OWN]
        served = {(r.path, method) for r in routes for method in r.methods or ()}
        self.assertEqual(listed, served)
        self.assertGreater(len(served), 70)

    def test_each_operation_names_its_action_and_its_real_errors(self):
        spec = self.fetched()
        for route in api.app.routes:
            if not isinstance(route, APIRoute) or route.path in OWN:
                continue
            for method in route.methods or ():
                operation = spec["paths"][route.path][method.lower()]
                self.assertEqual(operation["x-carmen-action"], getattr(route.endpoint, "action"), route.path)
                self.assertNotIn("422", operation["responses"], route.path)
        self.assertEqual(spec["paths"][api.HEALTH_PATH]["get"]["security"], [])
        self.assertIn("403", spec["paths"]["/api/v1/me"]["get"]["responses"])

    def test_a_request_with_no_credentials_is_refused(self):
        for path in (api.OPENAPI_PATH, api.DOCS_PATH):
            status, body = get(path, NOBODY)
            self.assertEqual(status, 401, path)
            self.assertNotIn(b"openapi", body)

    def test_a_role_that_may_not_read_is_refused(self):
        for path in (api.OPENAPI_PATH, api.DOCS_PATH):
            self.assertEqual(get(path, signed_in("MACHINE"))[0], 403, path)
            self.assertEqual(get(path, signed_in("NO_SUCH_ROLE"))[0], 403, path)

    def test_the_two_routes_need_the_read_action_and_redoc_is_not_served(self):
        routes = {r.path: r for r in api.app.routes if isinstance(r, APIRoute)}
        for path in OWN:
            self.assertEqual(getattr(routes[path].endpoint, "action"), READ)
        self.assertIsNone(api.app.redoc_url)
        self.assertEqual(get("/api/redoc", signed_in(READER))[0], 404)
        self.assertEqual(get("/openapi.json", signed_in(READER))[0], 404)

    def test_the_swagger_ui_reads_the_served_specification(self):
        status, body = get(api.DOCS_PATH, signed_in(READER))
        self.assertEqual(status, 200)
        self.assertIn(f"url: '{api.OPENAPI_PATH}'".encode(), body)


if __name__ == "__main__":
    unittest.main()
