# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API: the FastAPI application.

Every request except the health check is identified first (identify), then the route's declared
action is checked against the caller's role (authorize). See specs/001-skeleton-and-pipeline/spec.md.

Error handling rule (constitution, Principle VIII): every step that can raise is inside its own
inline try/except, which logs the step, the error, and the line. Detail stays in the log.
"""

import json
import logging
import time
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

import access
from roles import ADMIN, PUBLIC, READ, action, allowed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carmen")

HEALTH_PATH = "/api/v1/health"
ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "TOO_LARGE",
}


def error(request: Request, status: int) -> JSONResponse:
    code = ERROR_CODES.get(status, "INTERNAL")
    return JSONResponse({"ok": False, "error": code, "request_id": request.state.request_id}, status_code=status)


def log_error(request: Request, step: str, e: Exception) -> None:
    logger.error(
        json.dumps(
            {
                "event": "carmen_error",
                "step": step,
                "error": type(e).__name__,
                "description": str(e),
                "line": e.__traceback__.tb_lineno if e.__traceback__ else None,
                "request_id": request.state.request_id,
            }
        )
    )


def env_var(env, name: str) -> str | None:
    """A Worker variable, or None when it is not set."""
    value = getattr(env, name, None)
    return value if isinstance(value, str) and value else None


async def authorize(request: Request) -> None:
    """Refuse the request unless the route's declared action is allowed for the caller's role."""
    needed = getattr(request.scope.get("endpoint"), "action", None)
    if needed == PUBLIC:
        return
    identity = getattr(request.state, "identity", None)
    if needed is None or identity is None or not allowed(identity.get("role"), needed):
        raise HTTPException(403)


# The documentation pages and /openapi.json are not served: they would be routes with no action.
app = FastAPI(dependencies=[Depends(authorize)], docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error(request, exc.status_code)


async def resolve_identity(request: Request):
    """Return (identity, None), or (None, error response)."""
    env = request.scope["env"]

    # Local runs only: act as the person named in .dev.vars. Never set in wrangler.jsonc (a test checks).
    # Cloudflare adds Cf-Ray to every request it carries, and a local run has none.
    email = None
    try:
        if "cf-ray" not in request.headers:
            email = env_var(env, "DEV_USER_EMAIL")
    except Exception as e:
        log_error(request, "read_dev_user", e)
        return None, error(request, 500)

    if email is None:
        try:
            claims = await access.verify(
                request.headers.get("cf-access-jwt-assertion"),
                env.ACCESS_TEAM_DOMAIN,
                env.ACCESS_AUD,
                time.time(),
            )
        except access.AccessError as e:
            log_error(request, "verify_token", e)
            return None, error(request, 401)
        except Exception as e:
            log_error(request, "verify_token", e)
            return None, error(request, 500)

        email = claims.get("email")
        if not email:
            # A service token: a machine caller. It never gets an app_user row.
            try:
                is_admin = bool(claims.get("common_name")) and env_var(env, "SERVICE_TOKENS_ARE_ADMIN") == "true"
            except Exception as e:
                log_error(request, "read_service_token_rule", e)
                return None, error(request, 500)
            if not is_admin:
                return None, error(request, 403)
            return {
                "id": None,
                "email": None,
                "display_name": None,
                "role": ADMIN,
                "active": 1,
                "created_at": None,
                "machine": True,
            }, None

    # A person. The role is read on every request and is whatever the database says.
    email = email.lower()
    select = "SELECT id, email, display_name, role, active, created_at FROM app_user WHERE email = ?"
    try:
        row = await env.DB.prepare(select).bind(email).first()
        if not row:
            # First sign-in. No role is supplied: the database defaults and triggers decide it.
            insert = "INSERT INTO app_user (id, email) VALUES (?, ?) ON CONFLICT(email) DO NOTHING"
            await env.DB.prepare(insert).bind(str(uuid.uuid4()), email).run()
            row = await env.DB.prepare(select).bind(email).first()
        user = dict(row)  # the Workers runtime returns a row as a dict, and no row as nothing
    except Exception as e:
        log_error(request, "load_user", e)
        return None, error(request, 500)

    if not user["active"]:
        return None, error(request, 403)
    return {**user, "machine": False}, None


@app.middleware("http")
async def identify(request: Request, call_next):
    request.state.request_id = request.headers.get("cf-ray") or str(uuid.uuid4())
    response = None
    if request.url.path != HEALTH_PATH:
        request.state.identity, response = await resolve_identity(request)
    if response is None:
        response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get(HEALTH_PATH)
@action(PUBLIC)
async def health():
    # Liveness only: touches no dependency.
    return {"ok": True}


@app.get("/api/v1/me")
@action(READ)
async def me(request: Request):
    return request.state.identity
