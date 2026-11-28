# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API: the Worker entry point. The application is in app.py, which imports nothing
from the Workers runtime at load time, so the unit tests can import it."""

from typing import Any

from workers import asgi

from app import add_facts, app, create_lead, machine_call, process_import, process_proposals, process_run, run_scheduler


def field(body: Any, name: str) -> Any:
    """One field of a queue message, or None: a message holds import_id, proposal_agreement_id, or run_id."""
    try:
        return body[name]
    except Exception:  # the message has no such key
        return None


class Default(asgi.entrypoint(app)):
    """Requests go to the FastAPI application. A message on the RUNS queue carries one AI run, one CSV import,
    or one proposals job.
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
            import_id, agreement_id = field(message.body, "import_id"), field(message.body, "proposal_agreement_id")
            if import_id:
                await process_import(env, str(import_id))
            elif agreement_id:
                await process_proposals(env, str(agreement_id), field(message.body, "real") is True, field(message.body, "fresh") is True)
            elif not await process_run(env, str(message.body["run_id"])):
                message.retry()  # the failure could not be recorded: on the next delivery the run is marked interrupted
                continue
            message.ack()
