# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Who may do what. Pure: no input or output, so it is tested without a Worker.

Every route declares one action with @action. A route that declares none is refused for everyone.
"""

# Actions a route can perform.
PUBLIC = "PUBLIC"  # no identity needed; only the health check
READ = "READ"
WRITE = "WRITE"  # create or update a record
DELETE = "DELETE"
MANAGE = "MANAGE"  # users, settings, the rubric, the service catalog, the AI model list
ACTIONS = (PUBLIC, READ, WRITE, DELETE, MANAGE)

# Roles, the USER_ROLE codes in the System Reference Table (SRT).
READER = "READER"
MEMBER = "MEMBER"
ADMIN = "ADMIN"

_ALLOWED = {
    READER: {READ},
    MEMBER: {READ, WRITE},
    ADMIN: {READ, WRITE, DELETE, MANAGE},
}


def allowed(role, needed) -> bool:
    """True when the role may perform the action. An unknown role or action is refused."""
    return needed in _ALLOWED.get(role, ())


def action(name: str):
    """Mark an endpoint with the action it performs."""
    if name not in ACTIONS:
        raise ValueError(f"unknown action {name}")

    def mark(endpoint):
        endpoint.action = name
        return endpoint

    return mark
