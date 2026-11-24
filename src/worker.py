# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API: the Worker entry point. The application is in app.py, which imports nothing
from the Workers runtime at load time, so the unit tests can import it."""

from typing import Any

from workers import asgi

from app import add_facts, app, create_lead, machine_call, process_import, process_run, run_scheduler


class Default(asgi.entrypoint(app)):
    """Requests go to the FastAPI application. A message on the RUNS queue carries one AI run or one CSV import.
    The Cron Trigger runs the scheduler every hour."""

    # The machine methods, for another Worker that calls over a Service Binding: await env.CARMEN.create_lead({...}).
    # See specs/014-machine-methods/spec.md. Only a Worker of this account that binds this one can call them.

    async def create_lead(self, lead: Any) -> dict[str, Any]:
        return await machine_call(getattr(self, "env"), create_lead, lead)

    async def add_facts(self, facts: Any) -> dict[str, Any]:
        return await machine_call(getattr(self, "env"), add_facts, facts)

    async def scheduled(self, *_: Any) -> None:
        await run_scheduler(getattr(self, "env"))  # never raises; see specs/015-scheduler-and-notifications/spec.md

    async def queue(self, batch: Any, *_: Any) -> None:
        env: Any = getattr(self, "env")
        for message in batch.messages:
            try:
                import_id = message.body["import_id"]
            except Exception:  # not an import: the message has no such key, so it carries a run
                import_id = None
            if import_id:
                await process_import(env, str(import_id))
            else:
                await process_run(env, str(message.body["run_id"]))
            message.ack()
