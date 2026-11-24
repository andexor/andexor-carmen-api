# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Who may do what. Pure: no input or output, so it is tested without a Worker.

Every route declares one action with @action. A route that declares none is refused for everyone.
"""

from collections.abc import Callable
from typing import Any, TypeVar

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])

# Actions a route can perform.
PUBLIC = "PUBLIC"  # no identity needed; only the health check
READ = "READ"
WRITE = "WRITE"  # create or update a record
DELETE = "DELETE"
MANAGE = "MANAGE"  # users, settings, the rubric, the service catalog, the AI model list
MACHINE = "MACHINE"  # a machine method: another Andexor service sends a lead or facts
ACTIONS = (PUBLIC, READ, WRITE, DELETE, MANAGE, MACHINE)

# Roles, the USER_ROLE codes in the System Reference Table (SRT).
READER = "READER"
MEMBER = "MEMBER"
ADMIN = "ADMIN"
# Not a USER_ROLE code and never stored: the role of a service token. It may call the machine methods and nothing else.
MACHINE_CALLER = "MACHINE"

_ALLOWED: dict[str, frozenset[str]] = {
    READER: frozenset({READ}),
    MEMBER: frozenset({READ, WRITE, MACHINE}),
    ADMIN: frozenset({READ, WRITE, DELETE, MANAGE, MACHINE}),
    MACHINE_CALLER: frozenset({MACHINE}),
}


def allowed(role: object, needed: object) -> bool:
    """True when the role may perform the action. An unknown role or action is refused."""
    return isinstance(role, str) and needed in _ALLOWED.get(role, frozenset())


def action(name: str) -> Callable[[Endpoint], Endpoint]:
    """Mark an endpoint with the action it performs."""
    if name not in ACTIONS:
        raise ValueError(f"unknown action {name}")

    def mark(endpoint: Endpoint) -> Endpoint:
        setattr(endpoint, "action", name)
        return endpoint

    return mark
