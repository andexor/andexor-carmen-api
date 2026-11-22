# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API: the Worker entry point. The application is in app.py, which imports nothing
from the Workers runtime at load time, so the unit tests can import it."""

from workers import asgi

from app import app

Default = asgi.entrypoint(app)
