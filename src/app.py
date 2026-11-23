# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API: the FastAPI application.

Every request except the health check is identified first (identify), then the route's declared
action is checked against the caller's role (authorize). See specs/001-skeleton-and-pipeline/spec.md.
Companies, contacts, and the reference codes: specs/002-companies-and-contacts/spec.md.
Cross-Origin Resource Sharing (CORS): specs/017-cors/spec.md.

Error handling rule (constitution, Principle VIII): every step that can raise is inside its own
inline try/except, which logs the step, the error, and the line. Detail stays in the log.
"""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

import access
from records import COMPANY_CODES, COMPANY_FIELDS, CONTACT_CODES, CONTACT_FIELDS, BadBody, Values, clean, derive_domain
from roles import ADMIN, DELETE, PUBLIC, READ, WRITE, action, allowed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carmen")

# Who is calling: the app_user row plus "machine". Kept in request.state.identity.
Identity = dict[str, Any]

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


def env_var(env: Any, name: str) -> str | None:
    """A Worker variable, or None when it is not set."""
    value: Any = getattr(env, name, None)
    return value if isinstance(value, str) and value else None


async def authorize(request: Request) -> None:
    """Refuse the request unless the route's declared action is allowed for the caller's role."""
    needed = getattr(request.scope.get("endpoint"), "action", None)
    if needed == PUBLIC:
        return
    identity: Identity | None = getattr(request.state, "identity", None)
    if needed is None or identity is None or not allowed(identity.get("role"), needed):
        raise HTTPException(403)


# The documentation pages and /openapi.json are not served: they would be routes with no action.
app = FastAPI(dependencies=[Depends(authorize)], docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error(request, exc.status_code)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # A query parameter of the wrong type, or a body that is not a JSON object.
    return error(request, 400)


async def resolve_identity(request: Request) -> tuple[Identity | None, JSONResponse | None]:
    """Return (identity, None), or (None, error response)."""
    env: Any = request.scope["env"]

    # Local runs only: act as the person named in .dev.vars. Never set in wrangler.jsonc (a test checks).
    # Cloudflare adds Cf-Ray to every request it carries, and a local run has none.
    email: str | None = None
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
        user: Identity = dict(row)  # the Workers runtime returns a row as a dict, and no row as nothing
    except Exception as e:
        log_error(request, "load_user", e)
        return None, error(request, 500)

    if not user["active"]:
        return None, error(request, 403)
    return {**user, "machine": False}, None


CORS_METHODS = "GET, POST, PUT, PATCH, DELETE"


def cors_headers(origin: str | None, allowed: str | None) -> dict[str, str]:
    """Headers that let a page at an allowed origin read the response. Any other origin gets none.

    allowed is the ALLOWED_ORIGINS variable: exact origins separated by commas. There is no wildcard.
    """
    if not origin or origin not in [item.strip() for item in (allowed or "").split(",")]:
        return {}
    # Credentials are allowed because the page must send its Cloudflare Access cookie.
    return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"}


@app.middleware("http")
async def identify(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    request.state.request_id = request.headers.get("cf-ray") or str(uuid.uuid4())
    cors: dict[str, str] = {}
    try:
        cors = cors_headers(request.headers.get("origin"), env_var(request.scope["env"], "ALLOWED_ORIGINS"))
    except Exception as e:
        log_error(request, "read_allowed_origins", e)

    response: Response | None = None
    if cors and request.method == "OPTIONS" and "access-control-request-method" in request.headers:
        # A preflight. A browser sends it with no credentials, so it is answered before identity.
        response = Response(status_code=204)
        response.headers["Access-Control-Allow-Methods"] = CORS_METHODS
        response.headers["Access-Control-Max-Age"] = "86400"
        wanted = request.headers.get("access-control-request-headers")
        if wanted:
            response.headers["Access-Control-Allow-Headers"] = wanted
    elif request.url.path != HEALTH_PATH:
        request.state.identity, response = await resolve_identity(request)
    if response is None:
        response = await call_next(request)
    response.headers.update(cors)
    response.headers["Vary"] = "Origin"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get(HEALTH_PATH)
@action(PUBLIC)
async def health() -> dict[str, bool]:
    # Liveness only: touches no dependency.
    return {"ok": True}


@app.get("/api/v1/me")
@action(READ)
async def me(request: Request) -> Identity:
    return request.state.identity


# ---- Data access helpers -------------------------------------------------------------------------

NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"


def db(request: Request) -> Any:
    return request.scope["env"].DB


def db_status(e: Exception) -> int:
    """The status for a failed write. The database's constraints and triggers are the last line of defense."""
    text = str(e)
    if isinstance(e, BadBody):
        return 400
    if "UNIQUE constraint failed" in text:
        return 409
    if "constraint failed" in text or "SRT violation" in text:
        return 400
    return 500


def failed(request: Request, step: str, e: Exception) -> JSONResponse:
    log_error(request, step, e)
    return error(request, db_status(e))


def insert(database: Any, table: str, values: Values) -> Any:
    """An INSERT statement. A None value is left out, so the column takes its default."""
    given = {column: value for column, value in values.items() if value is not None}
    marks = ", ".join("?" * len(given))
    sql = f"INSERT INTO {table} ({', '.join(given)}) VALUES ({marks}) RETURNING *"
    return database.prepare(sql).bind(*given.values())


def update(database: Any, table: str, values: Values, row_id: str) -> Any:
    """An UPDATE statement for one row. It also sets updated_at."""
    sets = ", ".join(f"{column} = ?" for column in values)
    sql = f"UPDATE {table} SET {sets}, updated_at = {NOW} WHERE id = ? RETURNING *"
    return database.prepare(sql).bind(*values.values(), row_id)


async def unknown_code(database: Any, values: Values, codes: dict[str, str]) -> str | None:
    """The first column whose value is not an active code of its domain, or None."""
    for column, domain in codes.items():
        if values.get(column) is not None:
            sql = "SELECT 1 AS found FROM srt WHERE domain = ? AND code = ? AND active = 1"
            if not await database.prepare(sql).bind(domain, values[column]).first():
                return column
    return None


async def duplicate(request: Request, condition: str, value: str | None) -> JSONResponse:
    """409 with the id of the unarchived company that already holds the domain."""
    company_id: str | None = None
    try:
        sql = f"SELECT id FROM company WHERE archived_at IS NULL AND {condition}"
        row = await db(request).prepare(sql).bind(value).first()
        company_id = row["id"] if row else None
    except Exception as e:
        log_error(request, "find_duplicate", e)
    return JSONResponse(
        {"ok": False, "error": "CONFLICT", "request_id": request.state.request_id, "company_id": company_id},
        status_code=409,
    )


# ---- Reference codes -----------------------------------------------------------------------------


@app.get("/api/v1/srt")
@action(READ)
async def list_srt(request: Request) -> JSONResponse:
    sql = "SELECT domain, code, label, description, sort_order FROM srt WHERE active = 1 ORDER BY domain, sort_order, code"
    try:
        result = await db(request).prepare(sql).all()
    except Exception as e:
        return failed(request, "list_srt", e)
    return JSONResponse({"items": result["results"]})


# ---- Companies -----------------------------------------------------------------------------------

SORTS = {
    "name": "name COLLATE NOCASE",
    "created": "created_at DESC",
    "fit": "fit_score DESC NULLS LAST",
    "need": "need_score DESC NULLS LAST",
}


@app.get("/api/v1/companies")
@action(READ)
async def list_companies(
    request: Request,
    stage: str | None = None,
    source: str | None = None,
    recommendation: str | None = None,
    owner_id: str | None = None,
    archived: bool = False,
    q: str | None = None,
    sort: Literal["name", "created", "fit", "need"] = "name",
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> JSONResponse:
    where = ["archived_at IS NOT NULL" if archived else "archived_at IS NULL"]
    params: list[Any] = []
    filters = {"stage": stage, "source": source, "recommendation": recommendation, "owner_id": owner_id}
    for column, value in filters.items():
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    if q:
        where.append(
            "(name LIKE ? OR domain LIKE ? OR EXISTS (SELECT 1 FROM contact"
            " WHERE contact.company_id = company.id AND (contact.name LIKE ? OR contact.email LIKE ?)))"
        )
        params += [f"%{q}%"] * 4
    # shortcut: the cursor is a row offset, so a row added between pages can repeat one; use a keyset when lists grow large
    sql = f"SELECT * FROM company WHERE {' AND '.join(where)} ORDER BY {SORTS[sort]}, id LIMIT ? OFFSET ?"
    try:
        result = await db(request).prepare(sql).bind(*params, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_companies", e)
    rows: list[Any] = result["results"]
    next_cursor = str(cursor + limit) if len(rows) > limit else None
    return JSONResponse({"items": rows[:limit], "next_cursor": next_cursor})


@app.post("/api/v1/companies")
@action(WRITE)
async def create_company(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, COMPANY_FIELDS)
        if not (values.get("name") or values.get("website")):
            raise BadBody("name or website is required")
    except BadBody as e:
        return failed(request, "read_company", e)
    values["id"] = str(uuid.uuid4())
    values["domain"] = derive_domain(values.get("website"))

    try:
        column = await unknown_code(db(request), values, COMPANY_CODES)
    except Exception as e:
        return failed(request, "check_company_codes", e)
    if column:
        return error(request, 400)

    try:
        row = await insert(db(request), "company", values).first()
    except Exception as e:
        if db_status(e) == 409:
            return await duplicate(request, "domain = ?", values["domain"])
        return failed(request, "insert_company", e)
    return JSONResponse(row, status_code=201)


@app.get("/api/v1/companies/{company_id}")
@action(READ)
async def get_company(request: Request, company_id: str) -> JSONResponse:
    try:
        row = await db(request).prepare("SELECT * FROM company WHERE id = ?").bind(company_id).first()
        contacts = await db(request).prepare(CONTACTS_OF).bind(company_id).all()
    except Exception as e:
        return failed(request, "load_company", e)
    if not row:
        return error(request, 404)
    return JSONResponse({**row, "contacts": contacts["results"]})


@app.patch("/api/v1/companies/{company_id}")
@action(WRITE)
async def update_company(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, COMPANY_FIELDS)
    except BadBody as e:
        return failed(request, "read_company", e)
    if "website" in values:
        values["domain"] = derive_domain(values["website"])

    try:
        current = await db(request).prepare("SELECT * FROM company WHERE id = ?").bind(company_id).first()
    except Exception as e:
        return failed(request, "load_company", e)
    if not current:
        return error(request, 404)
    if not values:
        return JSONResponse(current)

    # Only a changed code is checked, so a company that holds a retired code can still be edited.
    changed = {column: value for column, value in values.items() if value != current[column]}
    try:
        column = await unknown_code(db(request), changed, COMPANY_CODES)
    except Exception as e:
        return failed(request, "check_company_codes", e)
    if column:
        return error(request, 400)

    statements = [update(db(request), "company", values, company_id)]
    if "stage" in changed:
        # The timeline entry is written with the change, in one transaction.
        entry = {
            "id": str(uuid.uuid4()),
            "company_id": company_id,
            "kind": "STAGE_CHANGE",
            "body": f"Stage changed from {current['stage']} to {changed['stage']}",
            "created_by": request.state.identity["id"],
        }
        statements.append(insert(db(request), "activity", entry))
    try:
        results = await db(request).batch(statements)
        row = results[0]["results"][0]
    except Exception as e:
        if db_status(e) == 409:
            return await duplicate(request, "domain = ?", values.get("domain"))
        return failed(request, "update_company", e)
    return JSONResponse(row)


@app.delete("/api/v1/companies/{company_id}")
@action(DELETE)
async def delete_company(request: Request, company_id: str) -> Response:
    held = (
        "SELECT (SELECT COUNT(*) FROM agreement WHERE company_id = ?1)"
        " + (SELECT COUNT(*) FROM document WHERE company_id = ?1) AS held FROM company WHERE id = ?1"
    )
    try:
        row = await db(request).prepare(held).bind(company_id).first()
    except Exception as e:
        return failed(request, "load_company", e)
    if not row:
        return error(request, 404)
    if row["held"]:
        return error(request, 409)

    try:
        # Everything else under the company goes with it: the foreign keys cascade.
        await db(request).prepare("DELETE FROM company WHERE id = ?").bind(company_id).run()
    except Exception as e:
        return failed(request, "delete_company", e)
    return Response(status_code=204)


async def set_archived(request: Request, company_id: str, archived_at: str) -> JSONResponse:
    sql = f"UPDATE company SET archived_at = {archived_at}, updated_at = {NOW} WHERE id = ? RETURNING *"
    try:
        row = await db(request).prepare(sql).bind(company_id).first()
    except Exception as e:
        if db_status(e) == 409:
            return await duplicate(request, "domain = (SELECT domain FROM company WHERE id = ?)", company_id)
        return failed(request, "archive_company", e)
    return JSONResponse(row) if row else error(request, 404)


@app.post("/api/v1/companies/{company_id}/archive")
@action(WRITE)
async def archive_company(request: Request, company_id: str) -> JSONResponse:
    return await set_archived(request, company_id, f"COALESCE(archived_at, {NOW})")


@app.post("/api/v1/companies/{company_id}/unarchive")
@action(WRITE)
async def unarchive_company(request: Request, company_id: str) -> JSONResponse:
    return await set_archived(request, company_id, "NULL")


# ---- Contacts ------------------------------------------------------------------------------------

CONTACTS_OF = "SELECT * FROM contact WHERE company_id = ? ORDER BY is_primary DESC, name COLLATE NOCASE, id"
# A company has at most one primary contact, so the others are cleared in the same transaction.
CLEAR_PRIMARY = f"UPDATE contact SET is_primary = 0, updated_at = {NOW} WHERE company_id = ? AND is_primary = 1 AND id != ?"


@app.get("/api/v1/companies/{company_id}/contacts")
@action(READ)
async def list_contacts(request: Request, company_id: str) -> JSONResponse:
    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        contacts = await db(request).prepare(CONTACTS_OF).bind(company_id).all()
    except Exception as e:
        return failed(request, "list_contacts", e)
    if not company:
        return error(request, 404)
    return JSONResponse({"items": contacts["results"]})


@app.post("/api/v1/companies/{company_id}/contacts")
@action(WRITE)
async def create_contact(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, CONTACT_FIELDS)
        if not (values.get("name") or values.get("email")):
            raise BadBody("name or email is required")
    except BadBody as e:
        return failed(request, "read_contact", e)
    values["id"] = str(uuid.uuid4())
    values["company_id"] = company_id

    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        column = await unknown_code(db(request), values, CONTACT_CODES)
    except Exception as e:
        return failed(request, "check_contact", e)
    if not company:
        return error(request, 404)
    if column:
        return error(request, 400)

    statements = [insert(db(request), "contact", values)]
    if values.get("is_primary"):
        statements.insert(0, db(request).prepare(CLEAR_PRIMARY).bind(company_id, values["id"]))
    try:
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "insert_contact", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/contacts/{contact_id}")
@action(WRITE)
async def update_contact(request: Request, contact_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, CONTACT_FIELDS)
    except BadBody as e:
        return failed(request, "read_contact", e)

    try:
        current = await db(request).prepare("SELECT * FROM contact WHERE id = ?").bind(contact_id).first()
    except Exception as e:
        return failed(request, "load_contact", e)
    if not current:
        return error(request, 404)
    if not values:
        return JSONResponse(current)

    changed = {column: value for column, value in values.items() if value != current[column]}
    try:
        column = await unknown_code(db(request), changed, CONTACT_CODES)
    except Exception as e:
        return failed(request, "check_contact", e)
    if column:
        return error(request, 400)

    statements = [update(db(request), "contact", values, contact_id)]
    if values.get("is_primary"):
        statements.insert(0, db(request).prepare(CLEAR_PRIMARY).bind(current["company_id"], contact_id))
    try:
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "update_contact", e)
    return JSONResponse(row)


@app.delete("/api/v1/contacts/{contact_id}")
@action(DELETE)
async def delete_contact(request: Request, contact_id: str) -> Response:
    try:
        row = await db(request).prepare("DELETE FROM contact WHERE id = ? RETURNING id").bind(contact_id).first()
    except Exception as e:
        return failed(request, "delete_contact", e)
    return Response(status_code=204) if row else error(request, 404)
