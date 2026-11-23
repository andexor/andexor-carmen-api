# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API: the FastAPI application.

Every request except the health check is identified first (identify), then the route's declared
action is checked against the caller's role (authorize). See specs/001-skeleton-and-pipeline/spec.md.
Companies, contacts, and the reference codes: specs/002-companies-and-contacts/spec.md.
The timeline and tasks: specs/003-activities-and-tasks/spec.md.
Services, the rubric, and scores: specs/004-services-rubric-and-scoring/spec.md.
Enrichment facts: specs/005-enrichment-facts/spec.md.
The Today view: specs/006-today/spec.md.
Deals: specs/007-deals/spec.md.
Agreements and documents: specs/008-agreements-and-documents/spec.md.
Cross-Origin Resource Sharing (CORS): specs/017-cors/spec.md.

Error handling rule (constitution, Principle VIII): every step that can raise is inside its own
inline try/except, which logs the step, the error, and the line. Detail stays in the log.
"""

import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal, cast

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException

import access
from records import (
    ACTIVITY_CODES,
    ACTIVITY_FIELDS,
    AGREEMENT_CODES,
    AGREEMENT_FIELDS,
    AUTOMATIC_KINDS,
    COMPANY_CODES,
    COMPANY_FIELDS,
    CONTACT_CODES,
    CONTACT_FIELDS,
    CRITERION_CODES,
    CRITERION_FIELDS,
    DEAL_CODES,
    DEAL_FIELDS,
    FACT_FIELDS,
    SERVICE_CODES,
    SERVICE_FIELDS,
    TASK_CODES,
    TASK_FIELDS,
    BadBody,
    Values,
    clean,
    derive_domain,
    describe_change,
    download_header,
    fact_key,
    fact_value,
    file_name_of,
    id_list,
    iso_date,
    local_date,
    slug,
)
from roles import ADMIN, DELETE, MANAGE, PUBLIC, READ, WRITE, action, allowed
from scoring import Row, notable, points, score, valid_config

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


def update(database: Any, table: str, values: Values, row_id: str, stamp: bool = True) -> Any:
    """An UPDATE statement for one row. It also sets updated_at, unless the table has none (stamp=False)."""
    sets = ", ".join(f"{column} = ?" for column in values) + (f", updated_at = {NOW}" if stamp else "")
    sql = f"UPDATE {table} SET {sets} WHERE id = ? RETURNING *"
    return database.prepare(sql).bind(*values.values(), row_id)


async def unknown_code(database: Any, values: Values, codes: dict[str, str]) -> str | None:
    """The first column whose value is not an active code of its domain, or None."""
    for column, domain in codes.items():
        if values.get(column) is not None:
            sql = "SELECT 1 AS found FROM srt WHERE domain = ? AND code = ? AND active = 1"
            if not await database.prepare(sql).bind(domain, values[column]).first():
                return column
    return None


def page(result: Any, limit: int, cursor: int) -> JSONResponse:
    """One page of a list that was read with LIMIT limit + 1 OFFSET cursor."""
    # shortcut: the cursor is a row offset, so a row added between pages can repeat one; use a keyset when lists grow large
    rows: list[Any] = result["results"]
    next_cursor = str(cursor + limit) if len(rows) > limit else None
    return JSONResponse({"items": rows[:limit], "next_cursor": next_cursor})


def may_delete(request: Request, owner_id: object) -> bool:
    """True for a role that may delete anything, and for the caller's own record (a note they logged, a task they own)."""
    identity: Identity = request.state.identity
    return allowed(identity.get("role"), DELETE) or (owner_id is not None and owner_id == identity.get("id"))


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
    sql = f"SELECT * FROM company WHERE {' AND '.join(where)} ORDER BY {SORTS[sort]}, id LIMIT ? OFFSET ?"
    try:
        result = await db(request).prepare(sql).bind(*params, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_companies", e)
    return page(result, limit, cursor)


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
        tasks = await db(request).prepare(OPEN_TASKS_OF).bind(company_id).all()
        services = await db(request).prepare(SERVICE_SCORES_OF).bind(company_id).all()
        deals = await db(request).prepare(OPEN_DEALS_OF).bind(company_id).all()
        agreements = await db(request).prepare(ACTIVE_AGREEMENTS_OF).bind(company_id).all()
    except Exception as e:
        return failed(request, "load_company", e)
    if not row:
        return error(request, 404)
    return JSONResponse(
        {
            **row,
            "contacts": contacts["results"],
            "open_tasks": tasks["results"],
            "services": services["results"],
            "open_deals": [deal_out(deal) for deal in deals["results"]],
            "active_agreements": [agreement_out(agreement) for agreement in agreements["results"]],
        }
    )


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

    # Every edit is recorded: the Worker log first, then the timeline entry with the change, in one transaction.
    edits = {column: value for column, value in changed.items() if column != "stage"}
    statements = audit(request, company_label(current), company_id, current, edits) if edits else []
    statements.append(update(db(request), "company", values, company_id))
    if "stage" in changed:
        moved = f"Stage changed from {current['stage']} to {changed['stage']}"
        log_change(request, moved, company_id)
        entry = {
            "id": str(uuid.uuid4()),
            "company_id": company_id,
            "kind": "STAGE_CHANGE",
            "body": moved,
            "created_by": request.state.identity["id"],
        }
        statements.append(insert(db(request), "activity", entry))
    try:
        results = await db(request).batch(statements)
        row = results[len(statements) - (2 if "stage" in changed else 1)]["results"][0]
    except Exception as e:
        if db_status(e) == 409:
            return await duplicate(request, "domain = ?", values.get("domain"))
        return failed(request, "update_company", e)
    return JSONResponse(row)


def company_label(company: Row) -> str:
    return f"Company {company['name'] or company['domain'] or company['id']}"


@app.delete("/api/v1/companies/{company_id}")
@action(DELETE)
async def delete_company(request: Request, company_id: str) -> Response:
    sql = (
        "SELECT *, (SELECT COUNT(*) FROM agreement WHERE company_id = ?1)"
        " + (SELECT COUNT(*) FROM document WHERE company_id = ?1) AS blocked FROM company WHERE id = ?1"
    )
    try:
        row = await db(request).prepare(sql).bind(company_id).first()
    except Exception as e:
        return failed(request, "load_company", e)
    if not row:
        return error(request, 404)
    if row["blocked"]:
        return error(request, 409)

    try:
        # The timeline is deleted with the company, so the Worker log is the only record of this delete.
        log_change(request, describe_change(company_label(row), held(row, COMPANY_FIELDS), None), company_id)
        # Everything else under the company goes with it: the foreign keys cascade.
        await db(request).prepare("DELETE FROM company WHERE id = ?").bind(company_id).run()
    except Exception as e:
        return failed(request, "delete_company", e)
    return Response(status_code=204)


async def set_archived(request: Request, company_id: str, archive: bool) -> JSONResponse:
    try:
        current = await db(request).prepare("SELECT * FROM company WHERE id = ?").bind(company_id).first()
    except Exception as e:
        return failed(request, "load_company", e)
    if not current:
        return error(request, 404)
    if archive == (current["archived_at"] is not None):
        return JSONResponse(current)  # already so: nothing to change, nothing to record

    sql = f"UPDATE company SET archived_at = {NOW if archive else 'NULL'}, updated_at = {NOW} WHERE id = ? RETURNING *"
    try:
        change = f"{company_label(current)} {'archived' if archive else 'unarchived'}"
        log_change(request, change, company_id)
        entry = {
            "id": str(uuid.uuid4()),
            "company_id": company_id,
            "kind": "SYSTEM",
            "body": change,
            "created_by": request.state.identity["id"],
        }
        statements = [insert(db(request), "activity", entry), db(request).prepare(sql).bind(company_id)]
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        if db_status(e) == 409:
            return await duplicate(request, "domain = (SELECT domain FROM company WHERE id = ?)", company_id)
        return failed(request, "archive_company", e)
    return JSONResponse(row)


@app.post("/api/v1/companies/{company_id}/archive")
@action(WRITE)
async def archive_company(request: Request, company_id: str) -> JSONResponse:
    return await set_archived(request, company_id, True)


@app.post("/api/v1/companies/{company_id}/unarchive")
@action(WRITE)
async def unarchive_company(request: Request, company_id: str) -> JSONResponse:
    return await set_archived(request, company_id, False)


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
    if changed:
        statements = audit(request, contact_label(current), current["company_id"], current, changed) + statements
    try:
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "update_contact", e)
    return JSONResponse(row)


def contact_label(contact: Row) -> str:
    return f"Contact {contact['name'] or contact['email']}"


@app.delete("/api/v1/contacts/{contact_id}")
@action(DELETE)
async def delete_contact(request: Request, contact_id: str) -> Response:
    try:
        current = await db(request).prepare("SELECT * FROM contact WHERE id = ?").bind(contact_id).first()
    except Exception as e:
        return failed(request, "load_contact", e)
    if not current:
        return error(request, 404)

    try:
        statements = [
            *audit(request, contact_label(current), current["company_id"], held(current, CONTACT_FIELDS), None),
            db(request).prepare("DELETE FROM contact WHERE id = ?").bind(contact_id),
        ]
        await db(request).batch(statements)
    except Exception as e:
        return failed(request, "delete_contact", e)
    return Response(status_code=204)


# ---- Timeline ------------------------------------------------------------------------------------

# What a timeline entry may point to. Each must be a record of the entry's own company.
ACTIVITY_LINKS = {"contact_id": "contact", "deal_id": "deal"}


async def outside_company(database: Any, values: Values, company_id: str) -> str | None:
    """The first link whose record is not under the company, or None."""
    for column, table in ACTIVITY_LINKS.items():
        if values.get(column) is not None:
            sql = f"SELECT 1 AS found FROM {table} WHERE id = ? AND company_id = ?"
            if not await database.prepare(sql).bind(values[column], company_id).first():
                return column
    return None


@app.get("/api/v1/companies/{company_id}/activities")
@action(READ)
async def list_activities(
    request: Request,
    company_id: str,
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> JSONResponse:
    params: list[Any] = [company_id] if kind is None else [company_id, kind]
    sql = (
        f"SELECT * FROM activity WHERE company_id = ?{'' if kind is None else ' AND kind = ?'}"
        " ORDER BY occurred_at DESC, created_at DESC, id LIMIT ? OFFSET ?"
    )
    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        result = await db(request).prepare(sql).bind(*params, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_activities", e)
    if not company:
        return error(request, 404)
    return page(result, limit, cursor)


@app.post("/api/v1/companies/{company_id}/activities")
@action(WRITE)
async def create_activity(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, ACTIVITY_FIELDS)
        if values.get("kind") in AUTOMATIC_KINDS:
            raise BadBody("an automatic kind cannot be logged by hand")
    except BadBody as e:
        return failed(request, "read_activity", e)
    values["id"] = str(uuid.uuid4())
    values["company_id"] = company_id
    values["created_by"] = request.state.identity["id"]

    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        column = await unknown_code(db(request), values, ACTIVITY_CODES)
        column = column or await outside_company(db(request), values, company_id)
    except Exception as e:
        return failed(request, "check_activity", e)
    if not company:
        return error(request, 404)
    if column:
        return error(request, 400)

    try:
        row = await insert(db(request), "activity", values).first()
    except Exception as e:
        return failed(request, "insert_activity", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/activities/{activity_id}")
@action(WRITE)
async def update_activity(request: Request, activity_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, ACTIVITY_FIELDS)
        if values.get("kind") in AUTOMATIC_KINDS:
            raise BadBody("an entry cannot be made automatic")
    except BadBody as e:
        return failed(request, "read_activity", e)

    try:
        current = await db(request).prepare("SELECT * FROM activity WHERE id = ?").bind(activity_id).first()
    except Exception as e:
        return failed(request, "load_activity", e)
    if not current:
        return error(request, 404)
    if current["kind"] in AUTOMATIC_KINDS:
        return error(request, 409)  # automatic entries are permanent
    if not values:
        return JSONResponse(current)

    changed = {column: value for column, value in values.items() if value != current[column]}
    try:
        column = await unknown_code(db(request), changed, ACTIVITY_CODES)
        column = column or await outside_company(db(request), changed, current["company_id"])
    except Exception as e:
        return failed(request, "check_activity", e)
    if column:
        return error(request, 400)

    if not changed:
        return JSONResponse(current)
    try:
        statements = [
            *audit(request, f"{current['kind']} entry", current["company_id"], current, changed),
            update(db(request), "activity", changed, activity_id, stamp=False),
        ]
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "update_activity", e)
    return JSONResponse(row)


# WRITE, not DELETE: a MEMBER may delete an entry they logged. may_delete decides.
@app.delete("/api/v1/activities/{activity_id}")
@action(WRITE)
async def delete_activity(request: Request, activity_id: str) -> Response:
    sql = "SELECT * FROM activity WHERE id = ?"
    try:
        current = await db(request).prepare(sql).bind(activity_id).first()
    except Exception as e:
        return failed(request, "load_activity", e)
    if not current:
        return error(request, 404)
    if not may_delete(request, current["created_by"]):
        return error(request, 403)
    if current["kind"] in AUTOMATIC_KINDS:
        return error(request, 409)  # automatic entries are permanent

    try:
        statements = [
            *audit(request, f"{current['kind']} entry", current["company_id"], held(current, ACTIVITY_FIELDS), None),
            db(request).prepare("DELETE FROM activity WHERE id = ?").bind(activity_id),
        ]
        await db(request).batch(statements)
    except Exception as e:
        return failed(request, "delete_activity", e)
    return Response(status_code=204)


# ---- Tasks ---------------------------------------------------------------------------------------

TASK_ORDER = "due_date IS NULL, due_date, created_at, id"  # soonest first, undated last
OPEN_TASKS_OF = f"SELECT * FROM task WHERE company_id = ? AND status = 'OPEN' ORDER BY {TASK_ORDER}"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@app.get("/api/v1/tasks")
@action(READ)
async def list_tasks(
    request: Request,
    status: str | None = None,
    company_id: str | None = None,
    owner_id: str | None = None,
    due_before: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> JSONResponse:
    try:
        due_before = iso_date(due_before) if due_before else None
    except BadBody as e:
        return failed(request, "read_due_before", e)
    where = ["1 = 1"]
    params: list[Any] = []
    filters = {"status = ?": status, "company_id = ?": company_id, "owner_id = ?": owner_id, "due_date < ?": due_before}
    for condition, value in filters.items():
        if value is not None:
            where.append(condition)
            params.append(value)
    sql = f"SELECT * FROM task WHERE {' AND '.join(where)} ORDER BY {TASK_ORDER} LIMIT ? OFFSET ?"
    try:
        result = await db(request).prepare(sql).bind(*params, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_tasks", e)
    return page(result, limit, cursor)


@app.post("/api/v1/tasks")
@action(WRITE)
async def create_task(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, TASK_FIELDS)
        if not values.get("title"):
            raise BadBody("title is required")
    except BadBody as e:
        return failed(request, "read_task", e)
    values["id"] = str(uuid.uuid4())
    values.setdefault("owner_id", request.state.identity["id"])  # the creator owns it unless the body says otherwise
    if values.get("status") == "DONE":
        values["completed_at"] = utc_now()

    try:
        column = await unknown_code(db(request), values, TASK_CODES)
    except Exception as e:
        return failed(request, "check_task", e)
    if column:
        return error(request, 400)

    try:
        row = await insert(db(request), "task", values).first()
    except Exception as e:
        return failed(request, "insert_task", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/tasks/{task_id}")
@action(WRITE)
async def update_task(request: Request, task_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, TASK_FIELDS)
    except BadBody as e:
        return failed(request, "read_task", e)

    try:
        current = await db(request).prepare("SELECT * FROM task WHERE id = ?").bind(task_id).first()
    except Exception as e:
        return failed(request, "load_task", e)
    if not current:
        return error(request, 404)
    if not values:
        return JSONResponse(current)

    changed = {column: value for column, value in values.items() if value != current[column]}
    try:
        column = await unknown_code(db(request), changed, TASK_CODES)
    except Exception as e:
        return failed(request, "check_task", e)
    if column:
        return error(request, 400)
    if "status" in changed:
        # Done records when; any other status clears it.
        values["completed_at"] = utc_now() if changed["status"] == "DONE" else None

    if not changed:
        return JSONResponse(current)
    try:
        # A task with no company has no timeline to write to: the Worker log is then its only record.
        statements = [
            *audit(request, f"Task {current['title']}", current["company_id"], current, changed),
            update(db(request), "task", values, task_id, stamp=False),
        ]
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "update_task", e)
    return JSONResponse(row)


# WRITE, not DELETE: a MEMBER may delete a task they own. may_delete decides.
@app.delete("/api/v1/tasks/{task_id}")
@action(WRITE)
async def delete_task(request: Request, task_id: str) -> Response:
    try:
        current = await db(request).prepare("SELECT * FROM task WHERE id = ?").bind(task_id).first()
    except Exception as e:
        return failed(request, "load_task", e)
    if not current:
        return error(request, 404)
    if not may_delete(request, current["owner_id"]):
        return error(request, 403)

    try:
        statements = [
            *audit(request, f"Task {current['title']}", current["company_id"], held(current, TASK_FIELDS), None),
            db(request).prepare("DELETE FROM task WHERE id = ?").bind(task_id),
        ]
        await db(request).batch(statements)
    except Exception as e:
        return failed(request, "delete_task", e)
    return Response(status_code=204)


# ---- Scores --------------------------------------------------------------------------------------
# The arithmetic is in scoring.py. Here the rubric is read, and the scores are written.

SERVICE_SCORES_OF = (
    "SELECT cs.service_id, s.name, s.offered, cs.need_score, cs.confidence FROM company_service cs"
    " JOIN service s ON s.id = cs.service_id WHERE cs.company_id = ? ORDER BY cs.need_score DESC, s.sort_order, s.id"
)
# Fit first, then each service in catalog order.
RUBRIC_FROM = "FROM rubric_criterion c LEFT JOIN service s ON s.id = c.service_id"
RUBRIC_ORDER = "ORDER BY c.section = 'NEED', s.sort_order, s.id, c.sort_order, c.id"


async def load_rubric(database: Any) -> tuple[list[Row], list[Row], dict[str, float]]:
    """Every criterion, every service, and the score thresholds, read on every call."""
    results = await database.batch(
        [
            database.prepare("SELECT * FROM rubric_criterion"),
            database.prepare("SELECT * FROM service"),
            database.prepare("SELECT key, value FROM setting WHERE key LIKE 'score.%'"),
        ]
    )
    thresholds: dict[str, float] = {}
    for row in results[2]["results"]:
        try:
            thresholds[row["key"]] = float(row["value"])
        except ValueError:
            pass  # a setting that is not a number falls back to the default
    return results[0]["results"], results[1]["results"], thresholds


def score_writes(request: Request, company: Row, scores: Row) -> list[Any]:
    """The statements that store a company's scores, with a timeline entry when the change is notable.

    company holds the id and the scores before the change.
    """
    database, company_id = db(request), company["id"]
    statements = [database.prepare("DELETE FROM company_service WHERE company_id = ?").bind(company_id)]
    for service_id, own in scores["services"].items():
        row = {"company_id": company_id, "service_id": service_id, **own}
        statements.append(insert(database, "company_service", row))
    sql = (
        "UPDATE company SET fit_score = ?, need_score = ?, confidence = ?, recommendation = ?,"
        f" scored_at = {NOW}, updated_at = {NOW} WHERE id = ?"
    )
    values = [scores[key] for key in ("fit_score", "need_score", "confidence", "recommendation")]
    statements.append(database.prepare(sql).bind(*values, company_id))
    if notable(company, scores):

        def moved(key: str) -> str:
            return f"{company.get(key) if company.get(key) is not None else 'none'} to {scores[key] if scores[key] is not None else 'none'}"

        change = f"Fit {moved('fit_score')}, Need {moved('need_score')}, recommendation {moved('recommendation')}"
        log_change(request, change, company_id)
        entry = {
            "id": str(uuid.uuid4()),
            "company_id": company_id,
            "kind": "SCORE_CHANGE",
            "body": change,
            "created_by": request.state.identity["id"],
        }
        statements.append(insert(database, "activity", entry))
    return statements


async def rescore_all(request: Request) -> int:
    """Recompute and store the scores of every company that has answers. Returns how many.

    Called after a change to a service or to the rubric. Stored points are brought up to date too.
    """
    # shortcut: runs inside the request; move it to the background or a queue when it is consistently slow
    database = db(request)
    criteria, services, thresholds = await load_rubric(database)
    by_id = {criterion["id"]: criterion for criterion in criteria}
    results = await database.batch(
        [
            database.prepare("SELECT company_id, criterion_id, value, points FROM rubric_answer"),
            database.prepare(
                "SELECT id, fit_score, need_score, recommendation FROM company"
                " WHERE id IN (SELECT company_id FROM rubric_answer)"
            ),
        ]
    )
    answers: dict[str, Row] = {}
    pending: list[Any] = []
    for row in results[0]["results"]:
        answers.setdefault(row["company_id"], {})[row["criterion_id"]] = row["value"]
        earned = points(by_id[row["criterion_id"]], row["value"])
        if earned is not None and abs(earned - row["points"]) > 1e-9:
            sql = "UPDATE rubric_answer SET points = ? WHERE company_id = ? AND criterion_id = ?"
            pending.append(database.prepare(sql).bind(earned, row["company_id"], row["criterion_id"]))
    companies: list[Row] = results[1]["results"]
    for company in companies:
        pending += score_writes(request, company, score(criteria, answers[company["id"]], services, thresholds))
        if len(pending) >= 50:  # each batch is one transaction; a company's statements stay together
            await database.batch(pending)
            pending = []
    if pending:
        await database.batch(pending)
    return len(companies)


def scores_body(scores: Row, services: list[Row]) -> Row:
    """The scores as returned to a caller, with the per-service part in the shape of SERVICE_SCORES_OF."""
    named = {service["id"]: service for service in services}
    per_service = [
        {"service_id": service_id, "name": named[service_id]["name"], "offered": named[service_id]["offered"], **own}
        for service_id, own in scores["services"].items()
    ]
    per_service.sort(key=lambda row: -row["need_score"])
    return {**scores, "services": per_service}


# ---- Services ------------------------------------------------------------------------------------


@app.get("/api/v1/services")
@action(READ)
async def list_services(request: Request) -> JSONResponse:
    try:
        result = await db(request).prepare("SELECT * FROM service ORDER BY sort_order, name, id").all()
    except Exception as e:
        return failed(request, "list_services", e)
    return JSONResponse({"items": result["results"]})


@app.post("/api/v1/services")
@action(MANAGE)
async def create_service(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, SERVICE_FIELDS)
        if not values.get("name"):
            raise BadBody("name is required")
        values["id"] = values.get("id") or slug(values["name"])
        if not values["id"]:
            raise BadBody("the name gives no id")
    except BadBody as e:
        return failed(request, "read_service", e)

    try:
        column = await unknown_code(db(request), values, SERVICE_CODES)
    except Exception as e:
        return failed(request, "check_service", e)
    if column:
        return error(request, 400)

    try:
        # A new service has no criteria yet, so no score changes.
        row = await insert(db(request), "service", values).first()
    except Exception as e:
        return failed(request, "insert_service", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/services/{service_id}")
@action(MANAGE)
async def update_service(request: Request, service_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, SERVICE_FIELDS)
    except BadBody as e:
        return failed(request, "read_service", e)
    values.pop("id", None)  # the id is in other tables and in page names; it does not change

    try:
        current = await db(request).prepare("SELECT * FROM service WHERE id = ?").bind(service_id).first()
    except Exception as e:
        return failed(request, "load_service", e)
    if not current:
        return error(request, 404)
    if not values:
        return JSONResponse(current)

    changed = {column: value for column, value in values.items() if value != current[column]}
    try:
        column = await unknown_code(db(request), changed, SERVICE_CODES)
    except Exception as e:
        return failed(request, "check_service", e)
    if column:
        return error(request, 400)

    if not changed:
        return JSONResponse(current)
    try:
        audit(request, f"Service {current['name']}", None, current, changed)  # no company: the Worker log only
        row = await update(db(request), "service", changed, service_id, stamp=False).first()
    except Exception as e:
        return failed(request, "update_service", e)

    if "offered" in changed or "active" in changed:
        try:
            await rescore_all(request)
        except Exception as e:
            return failed(request, "rescore_after_service", e)
    return JSONResponse(row)


# ---- Rubric --------------------------------------------------------------------------------------

# A change to one of these changes scores.
SCORING_COLUMNS = {"section", "service_id", "answer_type", "weight", "config_json", "active"}


@app.get("/api/v1/rubric")
@action(READ)
async def list_rubric(request: Request) -> JSONResponse:
    try:
        result = await db(request).prepare(f"SELECT c.* {RUBRIC_FROM} {RUBRIC_ORDER}").all()
    except Exception as e:
        return failed(request, "list_rubric", e)
    return JSONResponse({"items": result["results"]})


@app.post("/api/v1/rubric/recompute")
@action(WRITE)
async def recompute_scores(request: Request) -> JSONResponse:
    try:
        count = await rescore_all(request)
    except Exception as e:
        return failed(request, "rescore", e)
    return JSONResponse({"companies": count})


@app.post("/api/v1/rubric")
@action(MANAGE)
async def create_criterion(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, CRITERION_FIELDS)
        if not values.get("prompt"):
            raise BadBody("prompt is required")
        if not valid_config(values.get("answer_type") or "YES_NO", values.get("config_json")):
            raise BadBody("the configuration does not suit the answer type")
    except BadBody as e:
        return failed(request, "read_criterion", e)
    values["id"] = str(uuid.uuid4())
    # A criterion of a service is a Need criterion; any other is a Fit criterion.
    values["section"] = values.get("section") or ("NEED" if values.get("service_id") else "FIT")

    try:
        column = await unknown_code(db(request), values, CRITERION_CODES)
    except Exception as e:
        return failed(request, "check_criterion", e)
    if column:
        return error(request, 400)

    try:
        row = await insert(db(request), "rubric_criterion", values).first()
    except Exception as e:
        return failed(request, "insert_criterion", e)

    try:
        # A new criterion is unanswered everywhere, which lowers confidence.
        await rescore_all(request)
    except Exception as e:
        return failed(request, "rescore_after_criterion", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/rubric/{criterion_id}")
@action(MANAGE)
async def update_criterion(request: Request, criterion_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, CRITERION_FIELDS)
    except BadBody as e:
        return failed(request, "read_criterion", e)

    try:
        current = await db(request).prepare("SELECT * FROM rubric_criterion WHERE id = ?").bind(criterion_id).first()
    except Exception as e:
        return failed(request, "load_criterion", e)
    if not current:
        return error(request, 404)
    if not values:
        return JSONResponse(current)

    changed = {column: value for column, value in values.items() if value != current[column]}
    merged = {**current, **values}
    try:
        column = await unknown_code(db(request), changed, CRITERION_CODES)
    except Exception as e:
        return failed(request, "check_criterion", e)
    if column or not valid_config(merged["answer_type"], merged["config_json"]):
        return error(request, 400)

    if not changed:
        return JSONResponse(current)
    try:
        audit(request, f"Rubric question {current['prompt']}", None, current, changed)  # no company: the Worker log only
        row = await update(db(request), "rubric_criterion", changed, criterion_id, stamp=False).first()
    except Exception as e:
        return failed(request, "update_criterion", e)

    if SCORING_COLUMNS & set(changed):
        try:
            await rescore_all(request)
        except Exception as e:
            return failed(request, "rescore_after_criterion", e)
    return JSONResponse(row)


# ---- Answers -------------------------------------------------------------------------------------


@app.get("/api/v1/companies/{company_id}/answers")
@action(READ)
async def list_answers(request: Request, company_id: str) -> JSONResponse:
    # Every criterion in use, with the company's answer beside it. An unanswered one has a null value.
    sql = (
        "SELECT c.*, a.value, a.points, a.source, a.ai_run_id, a.answered_by, a.answered_at"
        f" {RUBRIC_FROM} LEFT JOIN rubric_answer a ON a.criterion_id = c.id AND a.company_id = ?"
        f" WHERE c.active = 1 {RUBRIC_ORDER}"
    )
    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        result = await db(request).prepare(sql).bind(company_id).all()
    except Exception as e:
        return failed(request, "list_answers", e)
    if not company:
        return error(request, 404)
    return JSONResponse({"items": result["results"]})


@app.put("/api/v1/companies/{company_id}/answers")
@action(WRITE)
async def set_answers(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    entries: Any = body.get("answers")
    if not isinstance(entries, list) or not entries:
        return error(request, 400)

    database = db(request)
    try:
        company = await database.prepare(
            "SELECT id, fit_score, need_score, recommendation FROM company WHERE id = ?"
        ).bind(company_id).first()
        criteria, services, thresholds = await load_rubric(database)
        stored = await database.prepare("SELECT criterion_id, value FROM rubric_answer WHERE company_id = ?").bind(company_id).all()
    except Exception as e:
        return failed(request, "load_answers", e)
    if not company:
        return error(request, 404)

    in_use = {criterion["id"]: criterion for criterion in criteria if criterion["active"]}
    answers: Row = {row["criterion_id"]: row["value"] for row in stored["results"]}
    before: Row = {}
    after: Values = {}
    statements: list[Any] = []
    try:
        for entry in cast(list[Any], entries):
            given: Row = cast(Row, entry) if isinstance(entry, dict) else {}
            criterion = in_use.get(given.get("criterion_id"))
            if criterion is None:
                raise BadBody("an answer names no criterion in use")
            value = given.get("value")
            if value is None or value == "":
                # Cleared: the criterion is unknown again.
                if criterion["id"] in answers:
                    before[criterion["prompt"]], after[criterion["prompt"]] = answers[criterion["id"]], None
                answers.pop(criterion["id"], None)
                sql = "DELETE FROM rubric_answer WHERE company_id = ? AND criterion_id = ?"
                statements.append(database.prepare(sql).bind(company_id, criterion["id"]))
                continue
            text = str(value).strip()
            text = text.upper() if criterion["answer_type"] == "YES_NO" else text
            earned = None if isinstance(value, bool) else points(criterion, text)
            if earned is None:
                raise BadBody("a value does not suit its criterion")
            if answers.get(criterion["id"], text) != text:
                before[criterion["prompt"]], after[criterion["prompt"]] = answers[criterion["id"]], text
            answers[criterion["id"]] = text
            sql = (
                "INSERT INTO rubric_answer (company_id, criterion_id, value, points, source, answered_by)"
                " VALUES (?, ?, ?, ?, 'MANUAL', ?) ON CONFLICT (company_id, criterion_id) DO UPDATE SET"
                " value = excluded.value, points = excluded.points, source = 'MANUAL', ai_run_id = NULL,"
                f" answered_by = excluded.answered_by, answered_at = {NOW}"
            )
            caller = request.state.identity["id"]
            statements.append(database.prepare(sql).bind(company_id, criterion["id"], text, earned, caller))
        scores = score(criteria, answers, services, thresholds)
    except BadBody as e:
        return failed(request, "read_answers", e)

    try:
        # A changed or cleared answer is recorded first. The answers and their scores are one transaction.
        record = audit(request, "Answers", company_id, before, after) if after else []
        await database.batch(record + statements + score_writes(request, company, scores))
    except Exception as e:
        return failed(request, "store_answers", e)
    return JSONResponse(scores_body(scores, services))


# ---- Enrichment facts ----------------------------------------------------------------------------

FACT_COLUMNS = "id, company_id, key, value, source, source_detail, notes, ai_run_id, observed_at, created_at"
NEWEST_FACT = "observed_at DESC, created_at DESC, id"


def fact_fields(entry: object) -> Values:
    """The writable keys of one fact in a request. Raises BadBody for a wrong type."""
    fact: Row = dict(cast(Row, entry)) if isinstance(entry, dict) else {}
    if isinstance(fact.get("value"), (int, float)) and not isinstance(fact["value"], bool):
        fact["value"] = str(fact["value"])  # a number is stored as text, like every value
    return clean(fact, FACT_FIELDS)


def log_change(request: Request, change: str, company_id: str | None) -> None:
    """Write a change to the Worker log. Called before the change, and its timeline entry, are sent to the database."""
    record = {
        "event": "carmen_audit",
        "change": change,
        "company_id": company_id,
        "by": request.state.identity["id"],
        "request_id": request.state.request_id,
    }
    logger.info(json.dumps(record))


def held(row: Row, fields: dict[str, type]) -> Row:
    """What a record holds, for the record of its deletion: its writable columns that have a value."""
    return {column: row[column] for column in fields if row.get(column) is not None}


def audit(request: Request, what: str, company_id: str | None, before: Row, after: Values | None) -> list[Any]:
    """Record an edit (after is the new values) or a delete (after is None) before it is carried out.

    The order is fixed: first a line in the Worker log, written here and now; then the timeline entry
    and the change itself, sent together. This returns the statement that adds the permanent SYSTEM
    entry, holding the old values, to the company's timeline. The caller puts that statement first in
    the same transaction as the change, so the change cannot happen without its record.

    The result is a list of statements. It is empty when there is no company (a service, a rubric
    question, a task with no company): the timeline belongs to a company, so the Worker log is the
    only record of such a change.
    """
    body = describe_change(what, before, after)
    log_change(request, body, company_id)
    if company_id is None:
        return []
    entry = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "kind": "SYSTEM",
        "body": body,
        "created_by": request.state.identity["id"],
    }
    return [insert(db(request), "activity", entry)]


@app.get("/api/v1/facts/keys")
@action(READ)
async def list_fact_keys(request: Request) -> JSONResponse:
    # For auto-complete: the well-known keys, then any other key already in use.
    sql = (
        "SELECT * FROM (SELECT key, label, value_type, description, sort_order FROM enrichment_key WHERE active = 1"
        " UNION ALL SELECT DISTINCT key, NULL, 'TEXT', NULL, NULL FROM enrichment_fact"
        " WHERE key NOT IN (SELECT key FROM enrichment_key)) ORDER BY sort_order IS NULL, sort_order, key"
    )
    try:
        result = await db(request).prepare(sql).all()
    except Exception as e:
        return failed(request, "list_fact_keys", e)
    return JSONResponse({"items": result["results"]})


@app.get("/api/v1/companies/{company_id}/facts")
@action(READ)
async def list_facts(
    request: Request,
    company_id: str,
    latest: bool = False,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> JSONResponse:
    if latest:
        # The newest fact of each key, by key.
        sql = (
            f"SELECT {FACT_COLUMNS} FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY key ORDER BY {NEWEST_FACT}) AS place"
            " FROM enrichment_fact WHERE company_id = ?) WHERE place = 1 ORDER BY key LIMIT ? OFFSET ?"
        )
    else:
        sql = f"SELECT {FACT_COLUMNS} FROM enrichment_fact WHERE company_id = ? ORDER BY {NEWEST_FACT} LIMIT ? OFFSET ?"
    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        result = await db(request).prepare(sql).bind(company_id, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_facts", e)
    if not company:
        return error(request, 404)
    return page(result, limit, cursor)


@app.post("/api/v1/companies/{company_id}/facts")
@action(WRITE)
async def create_facts(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    given: Any = body.get("facts", [body])  # many facts under "facts", or the body is one fact
    if not isinstance(given, list) or not given:
        return error(request, 400)

    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        known = await db(request).prepare("SELECT key, value_type FROM enrichment_key").all()
    except Exception as e:
        return failed(request, "load_fact_keys", e)
    if not company:
        return error(request, 404)
    types: dict[str, str] = {row["key"]: row["value_type"] for row in known["results"]}

    statements: list[Any] = []
    try:
        for entry in cast(list[Any], given):
            values = fact_fields(entry)
            values["key"] = fact_key(values.get("key") or "")
            if not values["key"] or not values.get("value"):
                raise BadBody("a fact needs a key and a value")
            values["value"] = fact_value(values["value"], types.get(values["key"]))
            values.update(id=str(uuid.uuid4()), company_id=company_id, source="MANUAL")
            statements.append(insert(db(request), "enrichment_fact", values))
    except BadBody as e:
        return failed(request, "read_facts", e)

    try:
        # All of them or none: one transaction.
        results = await db(request).batch(statements)
        rows = [result["results"][0] for result in results]
    except Exception as e:
        return failed(request, "insert_facts", e)
    return JSONResponse({"items": rows}, status_code=201)


@app.patch("/api/v1/facts/{fact_id}")
@action(WRITE)
async def update_fact(request: Request, fact_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = fact_fields(body)
        if "key" in values:
            values["key"] = fact_key(values["key"] or "")
        if ("key" in values and not values["key"]) or ("value" in values and not values["value"]):
            raise BadBody("a fact needs a key and a value")
    except BadBody as e:
        return failed(request, "read_fact", e)

    try:
        current = await db(request).prepare(f"SELECT {FACT_COLUMNS} FROM enrichment_fact WHERE id = ?").bind(fact_id).first()
    except Exception as e:
        return failed(request, "load_fact", e)
    if not current:
        return error(request, 404)

    if "value" in values:
        key = values.get("key", current["key"])
        try:
            known = await db(request).prepare("SELECT value_type FROM enrichment_key WHERE key = ?").bind(key).first()
            values["value"] = fact_value(values["value"], known["value_type"] if known else None)
        except Exception as e:
            return failed(request, "check_fact", e)
    changed = {column: value for column, value in values.items() if value != current[column]}
    if not changed:
        return JSONResponse(current)

    try:
        # The record of the old values is written first, in the same transaction as the edit.
        statements = [
            *audit(request, f"Fact {current['key']}", current["company_id"], current, changed),
            update(db(request), "enrichment_fact", changed, fact_id, stamp=False),
        ]
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "update_fact", e)
    return JSONResponse(row)


@app.delete("/api/v1/facts/{fact_id}")
@action(WRITE)
async def delete_fact(request: Request, fact_id: str) -> Response:
    sql = "SELECT company_id, key, value, source, source_detail, notes, observed_at FROM enrichment_fact WHERE id = ?"
    try:
        current = await db(request).prepare(sql).bind(fact_id).first()
    except Exception as e:
        return failed(request, "load_fact", e)
    if not current:
        return error(request, 404)

    try:
        # The record of what is deleted is written first, in the same transaction as the delete.
        kept = {column: value for column, value in current.items() if column not in ("company_id", "key")}
        statements = [
            *audit(request, f"Fact {current['key']}", current["company_id"], kept, None),
            db(request).prepare("DELETE FROM enrichment_fact WHERE id = ?").bind(fact_id),
        ]
        await db(request).batch(statements)
    except Exception as e:
        return failed(request, "delete_fact", e)
    return Response(status_code=204)


# ---- Today ---------------------------------------------------------------------------------------

# The date an agreement enters its renewal window: its own notice days before its end, or the default.
NOTICE_DATE = "date(a.end_date, '-' || COALESCE(a.renewal_notice_days, ?) || ' days')"
TODAY_TASKS = (
    "SELECT t.*, c.name AS company_name FROM task t LEFT JOIN company c ON c.id = t.company_id"
    " WHERE t.status = 'OPEN' AND t.due_date {} ? ORDER BY t.due_date, t.created_at, t.id LIMIT ?"
)
TODAY_LEADS = "SELECT * FROM company WHERE stage = 'LEAD' AND archived_at IS NULL AND {} LIMIT ?"


@app.get("/api/v1/today")
@action(READ)
async def today(request: Request, limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
    database = db(request)
    sql = "SELECT key, value FROM setting WHERE key IN ('digest.time_zone', 'renewal.default_notice_days')"
    try:
        found = await database.prepare(sql).all()
        settings: dict[str, str] = {row["key"]: row["value"] for row in found["results"]}
        notice = settings.get("renewal.default_notice_days", "")
        notice_days = int(notice) if notice.isdigit() else 30
    except Exception as e:
        return failed(request, "load_today_settings", e)

    # The answer says which zone was used: an unknown name falls back to UTC.
    date, zone = local_date(settings.get("digest.time_zone"))

    statements = [
        database.prepare(TODAY_TASKS.format("<")).bind(date, limit),
        database.prepare(TODAY_TASKS.format("=")).bind(date, limit),
        # Leads waiting for a first score, newest first.
        database.prepare(
            TODAY_LEADS.format("fit_score IS NULL AND need_score IS NULL ORDER BY created_at DESC, id")
        ).bind(limit),
        # Reach out before Maybe, then the highest scores.
        database.prepare(
            TODAY_LEADS.format(
                "recommendation IN ('REACH_OUT', 'MAYBE') ORDER BY recommendation = 'REACH_OUT' DESC,"
                " COALESCE(fit_score, 0) + COALESCE(need_score, 0) DESC, id"
            )
        ).bind(limit),
        # Open deals with nothing planned, the longest untouched first.
        database.prepare(
            "SELECT d.*, c.name AS company_name FROM deal d JOIN company c ON c.id = d.company_id"
            " WHERE d.stage NOT IN ('WON', 'LOST') AND NOT EXISTS"
            " (SELECT 1 FROM task t WHERE t.deal_id = d.id AND t.status = 'OPEN') ORDER BY d.updated_at, d.id LIMIT ?"
        ).bind(limit),
        database.prepare(
            f"SELECT a.*, c.name AS company_name, {NOTICE_DATE} AS notice_date FROM agreement a"
            " JOIN company c ON c.id = a.company_id WHERE a.status = 'ACTIVE' AND a.end_date IS NOT NULL"
            f" AND {NOTICE_DATE} <= ? ORDER BY a.end_date, a.id LIMIT ?"
        ).bind(notice_days, notice_days, date, limit),
        # The dossier is left out: it can be long.
        database.prepare(
            "SELECT r.id, r.company_id, c.name AS company_name, r.kind, r.ai_model_id, r.status, r.created_at,"
            " r.started_at FROM ai_run r JOIN company c ON c.id = r.company_id"
            " WHERE r.status IN ('QUEUED', 'RUNNING') ORDER BY r.created_at, r.id LIMIT ?"
        ).bind(limit),
    ]
    names = (
        "tasks_overdue",
        "tasks_due",
        "leads_unscored",
        "top_leads",
        "deals_without_task",
        "agreements_renewing",
        "runs_in_progress",
    )
    try:
        results = await database.batch(statements)
    except Exception as e:
        return failed(request, "load_today", e)
    lists = {name: result["results"] for name, result in zip(names, results, strict=True)}
    return JSONResponse({"date": date, "time_zone": zone, **lists})


# ---- Deals ---------------------------------------------------------------------------------------

DEAL_SELECT = (
    "SELECT d.*, c.name AS company_name,"
    " (SELECT group_concat(service_id) FROM deal_service ds WHERE ds.deal_id = d.id) AS service_ids"
    " FROM deal d JOIN company c ON c.id = d.company_id"
)
OPEN_DEALS_OF = f"{DEAL_SELECT} WHERE d.company_id = ? AND d.stage NOT IN ('WON', 'LOST') ORDER BY d.created_at DESC, d.id"


def deal_out(row: Row) -> Row:
    """A deal as returned to a caller: the services it covers are a list."""
    return {**row, "service_ids": sorted(row["service_ids"].split(",")) if row["service_ids"] else []}


def deal_services(database: Any, deal_id: str, service_ids: list[str]) -> list[Any]:
    sql = "INSERT INTO deal_service (deal_id, service_id) VALUES (?, ?)"
    return [database.prepare(sql).bind(deal_id, service_id) for service_id in service_ids]


async def contact_outside(database: Any, values: Values, company_id: str) -> bool:
    """True when the deal's primary contact is not a contact of the deal's company."""
    if values.get("primary_contact_id") is None:
        return False
    sql = "SELECT 1 AS found FROM contact WHERE id = ? AND company_id = ?"
    return not await database.prepare(sql).bind(values["primary_contact_id"], company_id).first()


@app.get("/api/v1/deals")
@action(READ)
async def list_deals(
    request: Request,
    stage: str | None = None,
    company_id: str | None = None,
    owner_id: str | None = None,
    open: bool = False,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> JSONResponse:
    where = ["d.stage NOT IN ('WON', 'LOST')" if open else "1 = 1"]
    params: list[Any] = []
    for condition, value in {"d.stage = ?": stage, "d.company_id = ?": company_id, "d.owner_id = ?": owner_id}.items():
        if value is not None:
            where.append(condition)
            params.append(value)
    sql = f"{DEAL_SELECT} WHERE {' AND '.join(where)} ORDER BY d.created_at DESC, d.id LIMIT ? OFFSET ?"
    try:
        result = await db(request).prepare(sql).bind(*params, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_deals", e)
    result["results"] = [deal_out(row) for row in result["results"]]
    return page(result, limit, cursor)


@app.post("/api/v1/deals")
@action(WRITE)
async def create_deal(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, DEAL_FIELDS)
        services = id_list(body, "service_ids") or []
        if not values.get("company_id"):
            raise BadBody("company_id is required")
    except BadBody as e:
        return failed(request, "read_deal", e)
    company_id = values["company_id"]

    database = db(request)
    try:
        sql = "SELECT id, name, domain, stage FROM company WHERE id = ?"
        company = await database.prepare(sql).bind(company_id).first()
        column = await unknown_code(database, values, DEAL_CODES)
        outside = await contact_outside(database, values, company_id)
    except Exception as e:
        return failed(request, "check_deal", e)
    if not company or column or outside:
        return error(request, 400)

    values["id"] = str(uuid.uuid4())
    values["name"] = values.get("name") or f"{company['name'] or company['domain'] or 'New'} deal"
    values.setdefault("owner_id", request.state.identity["id"])  # the creator owns it unless the body says otherwise
    statements = [insert(database, "deal", values), *deal_services(database, values["id"], services)]
    if company["stage"] == "LEAD":
        # The first deal means the company is in conversation. The change is on the timeline.
        sql = f"UPDATE company SET stage = 'PROSPECT', updated_at = {NOW} WHERE id = ? AND stage = 'LEAD'"
        statements.append(database.prepare(sql).bind(company_id))
        moved = "Stage changed from LEAD to PROSPECT: a deal was created"
        log_change(request, moved, company_id)
        entry = {
            "id": str(uuid.uuid4()),
            "company_id": company_id,
            "deal_id": values["id"],
            "kind": "STAGE_CHANGE",
            "body": moved,
            "created_by": request.state.identity["id"],
        }
        statements.append(insert(database, "activity", entry))
    try:
        await database.batch(statements)
        # Read back: the database sets closed_at for a deal created as WON or LOST.
        row = await database.prepare(f"{DEAL_SELECT} WHERE d.id = ?").bind(values["id"]).first()
    except Exception as e:
        return failed(request, "insert_deal", e)
    return JSONResponse(deal_out(row), status_code=201)


@app.get("/api/v1/deals/{deal_id}")
@action(READ)
async def get_deal(request: Request, deal_id: str) -> JSONResponse:
    try:
        row = await db(request).prepare(f"{DEAL_SELECT} WHERE d.id = ?").bind(deal_id).first()
    except Exception as e:
        return failed(request, "load_deal", e)
    return JSONResponse(deal_out(row)) if row else error(request, 404)


@app.patch("/api/v1/deals/{deal_id}")
@action(WRITE)
async def update_deal(request: Request, deal_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, DEAL_FIELDS)
        services = id_list(body, "service_ids")
    except BadBody as e:
        return failed(request, "read_deal", e)
    values.pop("company_id", None)  # a deal stays with its company

    database = db(request)
    try:
        found = await database.prepare(f"{DEAL_SELECT} WHERE d.id = ?").bind(deal_id).first()
    except Exception as e:
        return failed(request, "load_deal", e)
    if not found:
        return error(request, 404)
    current = deal_out(found)

    changed = {column: value for column, value in values.items() if value != current[column]}
    if services is not None and services == current["service_ids"]:
        services = None
    if not changed and services is None:
        return JSONResponse(current)
    try:
        column = await unknown_code(database, changed, DEAL_CODES)
        outside = await contact_outside(database, changed, current["company_id"])
    except Exception as e:
        return failed(request, "check_deal", e)
    if column or outside:
        return error(request, 400)

    statements: list[Any] = []
    edits: Values = {column: value for column, value in changed.items() if column != "stage"}
    if services is not None:
        edits["service_ids"] = services
    try:
        if edits:
            # The record of the old values comes first, in the same transaction as the edit.
            statements += audit(request, f"Deal {current['name']}", current["company_id"], current, edits)
        if "stage" in changed:
            moved = f"Deal {current['name']}: stage changed from {current['stage']} to {changed['stage']}"
            log_change(request, moved, current["company_id"])  # the Worker log first, then the timeline
            entry = {
                "id": str(uuid.uuid4()),
                "company_id": current["company_id"],
                "deal_id": deal_id,
                "kind": "STAGE_CHANGE",
                "body": moved,
                "created_by": request.state.identity["id"],
            }
            statements.append(insert(database, "activity", entry))
        if changed:
            statements.append(update(database, "deal", changed, deal_id))
        if services is not None:
            statements.append(database.prepare(f"UPDATE deal SET updated_at = {NOW} WHERE id = ?").bind(deal_id))
            statements.append(database.prepare("DELETE FROM deal_service WHERE deal_id = ?").bind(deal_id))
            statements += deal_services(database, deal_id, services)
        await database.batch(statements)
        # Read back: the database sets closed_at when a deal closes and clears it when it reopens.
        row = await database.prepare(f"{DEAL_SELECT} WHERE d.id = ?").bind(deal_id).first()
    except Exception as e:
        return failed(request, "update_deal", e)
    return JSONResponse(deal_out(row))


# WRITE, not DELETE: a MEMBER may delete a deal they own. may_delete decides.
@app.delete("/api/v1/deals/{deal_id}")
@action(WRITE)
async def delete_deal(request: Request, deal_id: str) -> Response:
    database = db(request)
    try:
        found = await database.prepare(f"{DEAL_SELECT} WHERE d.id = ?").bind(deal_id).first()
    except Exception as e:
        return failed(request, "load_deal", e)
    if not found:
        return error(request, 404)
    if not may_delete(request, found["owner_id"]):
        return error(request, 403)

    current = deal_out(found)
    kept = ("stage", "engagement_model", "one_time_value_cents", "monthly_value_cents", "currency")
    kept += ("expected_close_date", "closed_at", "lost_reason", "notes", "owner_id", "service_ids")
    was = {column: current[column] for column in kept if current[column] not in (None, [])}
    try:
        # The record of what is deleted comes first, in the same transaction as the delete.
        statements = [
            *audit(request, f"Deal {current['name']}", current["company_id"], was, None),
            database.prepare("DELETE FROM deal WHERE id = ?").bind(deal_id),
        ]
        await database.batch(statements)
    except Exception as e:
        return failed(request, "delete_deal", e)
    return Response(status_code=204)


# ---- Agreements ----------------------------------------------------------------------------------

AGREEMENT_SELECT = (
    "SELECT a.*, c.name AS company_name,"
    " (SELECT group_concat(service_id) FROM agreement_service s WHERE s.agreement_id = a.id) AS service_ids"
    " FROM agreement a JOIN company c ON c.id = a.company_id"
)
ACTIVE_AGREEMENTS_OF = f"{AGREEMENT_SELECT} WHERE a.company_id = ? AND a.status = 'ACTIVE' ORDER BY a.end_date IS NULL, a.end_date, a.id"
# What an agreement may point to. Each must be a record of the agreement's own company.
AGREEMENT_LINKS = {"parent_agreement_id": "agreement", "deal_id": "deal"}
DOCUMENT_COLUMNS = "id, company_id, agreement_id, file_name, content_type, size_bytes, sha256, uploaded_by, uploaded_at"


def agreement_out(row: Row) -> Row:
    """An agreement as returned to a caller: the services it covers are a list."""
    return {**row, "service_ids": sorted(row["service_ids"].split(",")) if row["service_ids"] else []}


def agreement_label(agreement: Row) -> str:
    return f"Agreement {agreement['title'] or agreement['type']}"


def agreement_services(database: Any, agreement_id: str, service_ids: list[str]) -> list[Any]:
    sql = "INSERT INTO agreement_service (agreement_id, service_id) VALUES (?, ?)"
    return [database.prepare(sql).bind(agreement_id, service_id) for service_id in service_ids]


async def link_outside(database: Any, values: Values, company_id: str) -> str | None:
    """The first link (parent agreement, deal) whose record is not under the company, or None."""
    for column, table in AGREEMENT_LINKS.items():
        if values.get(column) is not None:
            sql = f"SELECT 1 AS found FROM {table} WHERE id = ? AND company_id = ?"
            if not await database.prepare(sql).bind(values[column], company_id).first():
                return column
    return None


def company_stage_move(request: Request, company_id: str, old: str, new: str, why: str) -> list[Any]:
    """The statements for a stage change that follows from a fact: the Worker log first, then the timeline."""
    moved = f"Stage changed from {old} to {new}: {why}"
    log_change(request, moved, company_id)
    entry = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "kind": "STAGE_CHANGE",
        "body": moved,
        "created_by": request.state.identity["id"],
    }
    sql = f"UPDATE company SET stage = ?, updated_at = {NOW} WHERE id = ?"
    return [db(request).prepare(sql).bind(new, company_id), insert(db(request), "activity", entry)]


@app.get("/api/v1/agreements")
@action(READ)
async def list_agreements(
    request: Request,
    company_id: str | None = None,
    type: str | None = None,
    status: str | None = None,
    engagement_model: str | None = None,
    renewing_within_days: int | None = Query(None, ge=0, le=3650),
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> JSONResponse:
    where = ["1 = 1"]
    params: list[Any] = []
    filters = {"a.company_id = ?": company_id, "a.type = ?": type, "a.status = ?": status, "a.engagement_model = ?": engagement_model}
    for condition, value in filters.items():
        if value is not None:
            where.append(condition)
            params.append(value)
    try:
        if renewing_within_days is not None:
            # Active agreements that end within the window, counted from today where the business is.
            sql = "SELECT value FROM setting WHERE key = 'digest.time_zone'"
            zone = await db(request).prepare(sql).first()
            where.append("a.status = 'ACTIVE' AND a.end_date IS NOT NULL AND a.end_date <= date(?, ?)")
            params += [local_date(zone["value"] if zone else None)[0], f"+{renewing_within_days} days"]
        sql = (
            f"{AGREEMENT_SELECT} WHERE {' AND '.join(where)}"
            " ORDER BY a.end_date IS NULL, a.end_date, a.created_at DESC, a.id LIMIT ? OFFSET ?"
        )
        result = await db(request).prepare(sql).bind(*params, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_agreements", e)
    result["results"] = [agreement_out(row) for row in result["results"]]
    return page(result, limit, cursor)


@app.post("/api/v1/agreements")
@action(WRITE)
async def create_agreement(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, AGREEMENT_FIELDS)
        services = id_list(body, "service_ids") or []
        if not values.get("company_id") or not values.get("type"):
            raise BadBody("company_id and type are required")
    except BadBody as e:
        return failed(request, "read_agreement", e)
    company_id = values["company_id"]

    database = db(request)
    try:
        company = await database.prepare("SELECT id, stage FROM company WHERE id = ?").bind(company_id).first()
        column = await unknown_code(database, values, AGREEMENT_CODES)
        column = column or await link_outside(database, values, company_id)
    except Exception as e:
        return failed(request, "check_agreement", e)
    if not company or column:
        return error(request, 400)

    values["id"] = str(uuid.uuid4())
    values.setdefault("owner_id", request.state.identity["id"])  # the creator owns it unless the body says otherwise
    if values.get("status") == "TERMINATED" and not values.get("terminated_at"):
        values["terminated_at"] = utc_now()
    statements = [insert(database, "agreement", values), *agreement_services(database, values["id"], services)]
    if values.get("status") == "ACTIVE" and company["stage"] != "CUSTOMER":
        statements += company_stage_move(request, company_id, company["stage"], "CUSTOMER", "an agreement became active")
    try:
        await database.batch(statements)
        row = await database.prepare(f"{AGREEMENT_SELECT} WHERE a.id = ?").bind(values["id"]).first()
    except Exception as e:
        return failed(request, "insert_agreement", e)
    return JSONResponse(agreement_out(row), status_code=201)


@app.get("/api/v1/agreements/{agreement_id}")
@action(READ)
async def get_agreement(request: Request, agreement_id: str) -> JSONResponse:
    database = db(request)
    try:
        row = await database.prepare(f"{AGREEMENT_SELECT} WHERE a.id = ?").bind(agreement_id).first()
        sql = f"{AGREEMENT_SELECT} WHERE a.parent_agreement_id = ? ORDER BY a.created_at, a.id"
        children = await database.prepare(sql).bind(agreement_id).all()
        sql = f"SELECT {DOCUMENT_COLUMNS} FROM document WHERE agreement_id = ? ORDER BY uploaded_at DESC, id"
        documents = await database.prepare(sql).bind(agreement_id).all()
    except Exception as e:
        return failed(request, "load_agreement", e)
    if not row:
        return error(request, 404)
    return JSONResponse(
        {
            **agreement_out(row),
            "children": [agreement_out(child) for child in children["results"]],
            "documents": documents["results"],
        }
    )


@app.patch("/api/v1/agreements/{agreement_id}")
@action(WRITE)
async def update_agreement(request: Request, agreement_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, AGREEMENT_FIELDS)
        services = id_list(body, "service_ids")
    except BadBody as e:
        return failed(request, "read_agreement", e)
    values.pop("company_id", None)  # an agreement stays with its company

    database = db(request)
    try:
        found = await database.prepare(f"{AGREEMENT_SELECT} WHERE a.id = ?").bind(agreement_id).first()
    except Exception as e:
        return failed(request, "load_agreement", e)
    if not found:
        return error(request, 404)
    current = agreement_out(found)
    company_id = current["company_id"]

    changed = {column: value for column, value in values.items() if value != current[column]}
    if services is not None and services == current["service_ids"]:
        services = None
    if not changed and services is None:
        return JSONResponse(current)
    if "status" in changed and "terminated_at" not in values:
        # Terminating records when; any other status clears it.
        changed["terminated_at"] = utc_now() if changed["status"] == "TERMINATED" else None
        if changed["terminated_at"] == current["terminated_at"]:
            del changed["terminated_at"]
    try:
        column = await unknown_code(database, changed, AGREEMENT_CODES)
        column = column or await link_outside(database, changed, company_id)
        sql = (
            "SELECT stage, (SELECT COUNT(*) FROM agreement WHERE company_id = c.id AND status = 'ACTIVE' AND id != ?)"
            " AS other_active FROM company c WHERE c.id = ?"
        )
        company = await database.prepare(sql).bind(agreement_id, company_id).first()
    except Exception as e:
        return failed(request, "check_agreement", e)
    if column:
        return error(request, 400)

    edits: Values = dict(changed)
    if services is not None:
        edits["service_ids"] = services
    try:
        # The record of the old values comes first, in the same transaction as the edit.
        statements = audit(request, agreement_label(current), company_id, current, edits)
        if changed:
            statements.append(update(database, "agreement", changed, agreement_id))
        if services is not None:
            statements.append(database.prepare(f"UPDATE agreement SET updated_at = {NOW} WHERE id = ?").bind(agreement_id))
            statements.append(database.prepare("DELETE FROM agreement_service WHERE agreement_id = ?").bind(agreement_id))
            statements += agreement_services(database, agreement_id, services)
        if changed.get("status") == "ACTIVE" and company["stage"] != "CUSTOMER":
            statements += company_stage_move(request, company_id, company["stage"], "CUSTOMER", "an agreement became active")
        ended = "status" in changed and current["status"] == "ACTIVE"
        if ended and not company["other_active"] and company["stage"] == "CUSTOMER":
            statements += company_stage_move(request, company_id, "CUSTOMER", "FORMER", "the last active agreement ended")
        await database.batch(statements)
        row = await database.prepare(f"{AGREEMENT_SELECT} WHERE a.id = ?").bind(agreement_id).first()
    except Exception as e:
        return failed(request, "update_agreement", e)
    return JSONResponse(agreement_out(row))


# WRITE, not DELETE: a MEMBER may delete an agreement they own. may_delete decides.
@app.delete("/api/v1/agreements/{agreement_id}")
@action(WRITE)
async def delete_agreement(request: Request, agreement_id: str) -> Response:
    database = db(request)
    try:
        sql = (
            f"SELECT *, (SELECT COUNT(*) FROM agreement k WHERE k.parent_agreement_id = x.id) AS children"
            f" FROM ({AGREEMENT_SELECT}) x WHERE x.id = ?"
        )
        found = await database.prepare(sql).bind(agreement_id).first()
    except Exception as e:
        return failed(request, "load_agreement", e)
    if not found:
        return error(request, 404)
    if not may_delete(request, found["owner_id"]):
        return error(request, 403)
    if found["status"] != "DRAFT" or found["children"]:
        return error(request, 409)  # only a draft with no children can be deleted

    current = agreement_out(found)
    was = {**held(current, AGREEMENT_FIELDS), "service_ids": current["service_ids"]}
    try:
        statements = [
            *audit(request, agreement_label(current), current["company_id"], was, None),
            database.prepare("DELETE FROM agreement WHERE id = ?").bind(agreement_id),
        ]
        await database.batch(statements)
    except Exception as e:
        return failed(request, "delete_agreement", e)
    return Response(status_code=204)


# ---- Documents -----------------------------------------------------------------------------------
# The file is an object in the private R2 bucket DOCS, at <company_id>/<document_id>. The row holds
# its name, type, size, and checksum. The object is written first, then the row; the row is deleted
# first, then the object.


def bucket(request: Request) -> Any:
    return request.scope["env"].DOCS


@app.get("/api/v1/companies/{company_id}/documents")
@action(READ)
async def list_documents(request: Request, company_id: str) -> JSONResponse:
    sql = f"SELECT {DOCUMENT_COLUMNS} FROM document WHERE company_id = ? ORDER BY uploaded_at DESC, id"
    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        result = await db(request).prepare(sql).bind(company_id).all()
    except Exception as e:
        return failed(request, "list_documents", e)
    if not company:
        return error(request, 404)
    return JSONResponse({"items": result["results"]})


@app.post("/api/v1/companies/{company_id}/documents")
@action(WRITE)
async def upload_document(
    request: Request, company_id: str, file_name: str | None = None, agreement_id: str | None = None
) -> JSONResponse:
    database = db(request)
    try:
        company = await database.prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        limit = await database.prepare("SELECT value FROM setting WHERE key = 'document.max_size_bytes'").first()
        max_size = int(limit["value"]) if limit and str(limit["value"]).isdigit() else 26214400
        attached = True
        if agreement_id is not None:
            sql = "SELECT 1 AS found FROM agreement WHERE id = ? AND company_id = ?"
            attached = bool(await database.prepare(sql).bind(agreement_id, company_id).first())
    except Exception as e:
        return failed(request, "check_document", e)
    if not company:
        return error(request, 404)
    if not attached:
        return error(request, 400)  # the agreement is not one of this company's

    try:
        # The body is the file itself. It is read in pieces, so only one full copy is held here.
        data = bytearray()
        digest = hashlib.sha256()
        async for piece in request.stream():
            data += piece
            digest.update(piece)
            if len(data) > max_size:
                return error(request, 413)
    except Exception as e:
        return failed(request, "read_document", e)
    if not data:
        return error(request, 400)

    document = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "agreement_id": agreement_id,
        "file_name": file_name_of(file_name),
        "content_type": request.headers.get("content-type") or "application/octet-stream",
        "size_bytes": len(data),
        "sha256": digest.hexdigest(),
        "uploaded_by": request.state.identity["id"],
    }
    document["r2_key"] = f"{company_id}/{document['id']}"
    try:
        from pyodide.ffi import to_js

        js = access.js_module()
        # The bucket checks the checksum it is given and refuses a body that does not match it.
        options = {"httpMetadata": {"contentType": document["content_type"]}, "sha256": document["sha256"]}
        # The body is handed over as an ArrayBuffer. A byte array would be walked one byte at a time by the
        # Workers Python layer, which checks every value given to a binding: about 4 seconds per MiB.
        body = to_js(data).buffer
        await bucket(request).put(document["r2_key"], body, to_js(options, dict_converter=js.Object.fromEntries))
    except Exception as e:
        return failed(request, "store_document", e)

    try:
        row = await insert(database, "document", document).first()
    except Exception as e:
        log_error(request, "insert_document", e)
        try:
            # No row points to the object, so it is removed again.
            await bucket(request).delete(document["r2_key"])
        except Exception as cleanup:
            log_error(request, "remove_orphan_document", cleanup)
        return error(request, db_status(e))
    return JSONResponse({column: row[column] for column in DOCUMENT_COLUMNS.split(", ")}, status_code=201)


@app.get("/api/v1/documents/{document_id}")
@action(READ)
async def get_document(request: Request, document_id: str) -> JSONResponse:
    sql = f"SELECT {DOCUMENT_COLUMNS} FROM document WHERE id = ?"
    try:
        row = await db(request).prepare(sql).bind(document_id).first()
    except Exception as e:
        return failed(request, "load_document", e)
    return JSONResponse(row) if row else error(request, 404)


@app.get("/api/v1/documents/{document_id}/content")
@action(READ)
async def download_document(request: Request, document_id: str) -> Response:
    try:
        row = await db(request).prepare("SELECT * FROM document WHERE id = ?").bind(document_id).first()
    except Exception as e:
        return failed(request, "load_document", e)
    if not row:
        return error(request, 404)

    try:
        stored = await bucket(request).get(row["r2_key"])
    except Exception as e:
        return failed(request, "fetch_document", e)
    if not stored:
        log_error(request, "fetch_document", LookupError(f"no object at {row['r2_key']}"))
        return error(request, 404)

    async def pieces() -> AsyncIterator[bytes]:
        # Streamed: the file is passed on piece by piece and never held whole.
        async for piece in stored.body:
            yield piece.to_bytes()

    # The type is sent exactly as it was stored: set as a header, the framework adds no character set to it.
    headers = {
        "Content-Type": row["content_type"],
        "Content-Length": str(row["size_bytes"]),
        "Content-Disposition": download_header(row["file_name"]),
    }
    return StreamingResponse(pieces(), headers=headers)


# WRITE, not DELETE: a MEMBER may delete a document they uploaded. may_delete decides.
@app.delete("/api/v1/documents/{document_id}")
@action(WRITE)
async def delete_document(request: Request, document_id: str) -> Response:
    database = db(request)
    try:
        row = await database.prepare("SELECT * FROM document WHERE id = ?").bind(document_id).first()
    except Exception as e:
        return failed(request, "load_document", e)
    if not row:
        return error(request, 404)
    if not may_delete(request, row["uploaded_by"]):
        return error(request, 403)

    was = {column: row[column] for column in ("content_type", "size_bytes", "sha256", "agreement_id") if row[column] is not None}
    try:
        statements = [
            *audit(request, f"Document {row['file_name']}", row["company_id"], was, None),
            database.prepare("DELETE FROM document WHERE id = ?").bind(document_id),
        ]
        await database.batch(statements)
    except Exception as e:
        return failed(request, "delete_document", e)

    try:
        # The row is gone first, so a failure here leaves an object nobody can reach, never a row with no file.
        await bucket(request).delete(row["r2_key"])
    except Exception as e:
        log_error(request, "remove_document_object", e)
    return Response(status_code=204)
