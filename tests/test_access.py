# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the signing keys of Cloudflare Access: what a request finds while they are fetched.
Run with ./unit-test.sh."""

import asyncio
import json
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import access  # noqa: E402


class Response:
    ok = True
    status = 200

    async def text(self) -> str:
        return json.dumps({"keys": [{"kid": "current"}, {"kid": "next"}]})  # Access publishes two keys


fetches = 0


async def fetch(url: str) -> Response:
    """The network. Each fetch takes a little longer than the one before, so one request's list
    arrives while another request is still importing its keys."""
    global fetches
    fetches += 1
    for _ in range(fetches):
        await asyncio.sleep(0)
    return Response()


async def import_key(kind: str, jwk: dict[str, Any], *_: object) -> str:
    await asyncio.sleep(0)  # importing a key pauses too
    return f"key {jwk['kid']}"


def convert(value: object, **_: object) -> object:
    return value


class SigningKeysTest(unittest.TestCase):
    def setUp(self) -> None:
        access._keys.clear()  # pyright: ignore[reportPrivateUsage]
        global fetches
        fetches = 0
        # The Workers runtime is not here: its JavaScript globals and its converter are stood in for.
        ffi = types.ModuleType("pyodide.ffi")
        setattr(ffi, "to_js", convert)
        js = SimpleNamespace(fetch=fetch, crypto=SimpleNamespace(subtle=SimpleNamespace(importKey=import_key)), Object=SimpleNamespace(fromEntries=dict))
        for patch in (mock.patch.dict(sys.modules, {"pyodide": types.ModuleType("pyodide"), "pyodide.ffi": ffi}), mock.patch.object(access, "js_module", lambda: js)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_requests_that_fetch_the_keys_together_all_find_their_key(self):
        async def together() -> list[str]:
            # Several requests on a cold start, signed with either key: each fetches the list itself.
            kids = ["current", "next", "current", "next", "current"]
            return await asyncio.gather(*(access._signing_key("team.example", kid) for kid in kids))  # pyright: ignore[reportPrivateUsage]

        self.assertEqual(asyncio.run(together()), ["key current", "key next", "key current", "key next", "key current"])

    def test_a_key_that_access_does_not_publish_is_refused(self):
        with self.assertRaises(access.AccessError):
            asyncio.run(access._signing_key("team.example", "forged"))  # pyright: ignore[reportPrivateUsage]


if __name__ == "__main__":
    unittest.main()
