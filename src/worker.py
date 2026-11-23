# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API: the Worker entry point. The application is in app.py, which imports nothing
from the Workers runtime at load time, so the unit tests can import it."""

from typing import Any

from workers import asgi

from app import app, process_run


class Default(asgi.entrypoint(app)):
    """Requests go to the FastAPI application. Messages on the RUNS queue each carry one AI run."""

    async def queue(self, batch: Any, *_: Any) -> None:
        env: Any = getattr(self, "env")
        for message in batch.messages:
            await process_run(env, str(message.body["run_id"]))
            message.ack()
