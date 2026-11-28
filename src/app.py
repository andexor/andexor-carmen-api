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
AI models and runs: specs/009-ai-runs/spec.md.
CSV import: specs/013-csv-import/spec.md.
Machine methods: specs/014-machine-methods/spec.md.
The scheduler and notifications: specs/015-scheduler-and-notifications/spec.md.
Settings: specs/016-settings/spec.md.
The renewal policy of an agreement: specs/024-renewal-policy/spec.md.
Cross-Origin Resource Sharing (CORS): specs/017-cors/spec.md.

Error handling rule (constitution, Principle VIII): every step that can raise is inside its own
inline try/except, which logs the step, the error, and the line. Detail stays in the log.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal, cast

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.exceptions import HTTPException

import access
import contract
import models
import ai
import imports
import notify
import pages
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
    MODEL_CODES,
    MODEL_FIELDS,
    RUN_FIELDS,
    SERVICE_CODES,
    SERVICE_FIELDS,
    TASK_CODES,
    TASK_FIELDS,
    BadBody,
    Values,
    add_months,
    clean,
    SHARED_HOSTS,
    derive_domain,
    primary_change,
    describe_change,
    download_header,
    fact_key,
    fact_value,
    file_name_of,
    id_list,
    iso_date,
    local_date,
    mailbox,
    renewal_problem,
    new_service_id,
    setting_problem,
)
from contract import Items, Page, takes
from roles import ADMIN, DELETE, MACHINE, MACHINE_CALLER, MANAGE, PUBLIC, READ, WRITE, action, allowed
from scoring import Row, from_fact, measured, notable, points, score, valid_config

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


# ---- The contract --------------------------------------------------------------------------------
# The OpenAPI 3.1 specification that FastAPI generates from the routes below is the API's contract.
# It and the Swagger UI are served like every other route: behind Cloudflare Access, with an action.
# See specs/031-openapi/spec.md.

# The name and the version in pyproject.toml, which is not packaged with the Worker. A unit test compares them.
APP_NAME = "andexor-carmen-api"
APP_VERSION = "1.0.0"
OPENAPI_PATH = "/api/openapi.json"
DOCS_PATH = "/api/docs"
ACCESS_SCHEME = "cloudflareAccess"


class ErrorBody(BaseModel):
    """What every refusal and failure answers."""

    ok: bool
    error: str
    request_id: str


class HealthBody(models.Row):
    ok: bool


class IdentityBody(models.Row):
    """The caller: the user's row, or a machine caller, which has no row."""

    id: str | None
    email: str | None
    display_name: str | None
    role: str
    active: int
    created_at: str | None
    machine: bool


def describe(schema: dict[str, Any], routes: list[Any]) -> None:
    """Make the generated specification say what the app does: the action each route needs, the
    errors it really answers, and that Cloudflare Access stands in front of all but the health check."""
    refusal = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorBody"}}}}
    found: dict[str, Any] = {}
    reasons = {"400": "The request is not valid", "401": "No valid Cloudflare Access token", "403": "The caller's role may not do this", "404": "No such record"}
    for route in routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        needed = str(getattr(route.endpoint, "action", ""))
        for method in route.methods or ():
            operation: dict[str, Any] | None = schema["paths"].get(route.path, {}).get(method.lower())
            if operation is None:
                continue
            operation["tags"] = [route.path.removeprefix("/api/v1/").split("/")[0]]
            operation["x-carmen-action"] = needed
            body: type[BaseModel] | None = getattr(route.endpoint, "body_model", None)
            if body is not None:
                # The handler reads the body as a free object and checks it itself; the declared model says what it takes.
                described: dict[str, Any] = body.model_json_schema(ref_template="#/components/schemas/{model}")
                found.update(described.pop("$defs", {}))
                found[body.__name__] = described
                operation["requestBody"]["content"]["application/json"]["schema"] = {"$ref": f"#/components/schemas/{body.__name__}"}
            operation["description"] = (operation.get("description", "") + f"\n\nNeeds the action {needed}.").strip()
            # A request that fails validation is answered 400 with the error body, never FastAPI's 422.
            invalid = operation["responses"].pop("422", None) is not None or "requestBody" in operation
            if needed == PUBLIC:
                operation["security"] = []
                continue
            wanted = ["401", "403"] + (["400"] if invalid else []) + (["404"] if "{" in route.path else [])
            for status in sorted(wanted):
                operation["responses"][status] = {"description": reasons[status], **refusal}
    components: dict[str, Any] = schema.setdefault("components", {})
    schemas: dict[str, Any] = components.setdefault("schemas", {})
    for unused in ("HTTPValidationError", "ValidationError"):
        schemas.pop(unused, None)
    schemas.update(found)
    schemas["ErrorBody"] = ErrorBody.model_json_schema()
    about = "The cookie that Cloudflare Access sets after sign-in. A service sends a service token instead."
    components["securitySchemes"] = {ACCESS_SCHEME: {"type": "apiKey", "in": "cookie", "name": "CF_Authorization", "description": about}}
    schema["security"] = [{ACCESS_SCHEME: []}]


@action(READ)
async def openapi_document(request: Request) -> JSONResponse:
    return JSONResponse(request.app.openapi())


@action(READ)
async def swagger_ui(request: Request) -> HTMLResponse:
    # No favicon is fetched from another site.
    return get_swagger_ui_html(openapi_url=request.app.openapi_url, title=f"{request.app.title}: Swagger UI", swagger_favicon_url="data:,")


class Api(FastAPI):
    def setup(self) -> None:
        """FastAPI adds the specification and the Swagger UI as plain routes, which skip the app's
        dependencies and so would skip authorize. Here they are routes like the others, with an action."""
        if self.openapi_url:
            self.add_api_route(self.openapi_url, openapi_document, include_in_schema=False)
            if self.docs_url:
                self.add_api_route(self.docs_url, swagger_ui, include_in_schema=False, response_class=HTMLResponse)

    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            describe(super().openapi(), self.routes)
        return cast(dict[str, Any], self.openapi_schema)


# The same paths in every environment: only /api/* reaches the Worker. ReDoc is not served.
app = Api(
    title=APP_NAME,
    version=APP_VERSION,
    description="The Carmen API. Every path but the health check is behind Cloudflare Access, and each needs an action that the caller's role allows.",
    dependencies=[Depends(authorize)],
    openapi_url=OPENAPI_PATH,
    docs_url=DOCS_PATH,
    redoc_url=None,
)


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
            # A service token: a machine caller. It never gets an app_user row. In every environment it
            # may call the machine methods and nothing else. The preview's test token is the one
            # exception: it acts as ADMIN, so that the smoke test can call every endpoint.
            if not claims.get("common_name"):
                return None, error(request, 403)
            try:
                is_admin = env_var(env, "SERVICE_TOKENS_ARE_ADMIN") == "true"
            except Exception as e:
                log_error(request, "read_service_token_rule", e)
                return None, error(request, 500)
            return {
                "id": None,
                "email": None,
                "display_name": None,
                "role": ADMIN if is_admin else MACHINE_CALLER,
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


@app.get(HEALTH_PATH, response_model=HealthBody)
@action(PUBLIC)
async def health() -> dict[str, bool]:
    # Liveness only: touches no dependency.
    return {"ok": True}


@app.get("/api/v1/me", response_model=IdentityBody)
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
    if "constraint failed" in text or "SRT violation" in text or "task link outside" in text:
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


@app.get("/api/v1/srt", response_model=Items[models.Srt])
@action(READ)
async def list_srt(request: Request) -> JSONResponse:
    sql = "SELECT domain, code, label, description, sort_order FROM srt WHERE active = 1 ORDER BY domain, sort_order, code"
    try:
        result = await db(request).prepare(sql).all()
    except Exception as e:
        return failed(request, "list_srt", e)
    return JSONResponse({"items": result["results"]})


async def shared_hosts(database: Any) -> str:
    """The setting company.shared_hosts: the sites whose pages do not say which business they are, so
    that they give a company no domain. The fallback when it is missing or cannot be read: a write
    does not fail because of this setting."""
    try:
        row = await database.prepare("SELECT value FROM setting WHERE key = 'company.shared_hosts'").first()
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_setting_error", "step": "read_shared_hosts", "error": type(e).__name__}))
        return SHARED_HOSTS
    return str(row["value"]) if row else SHARED_HOSTS


# ---- Companies -----------------------------------------------------------------------------------

SORTS = {
    "name": "name COLLATE NOCASE",
    "created": "created_at DESC",
    "fit": "fit_score DESC NULLS LAST",
    "need": "need_score DESC NULLS LAST",
}


@app.get("/api/v1/companies", response_model=Page[models.Company])
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


@app.post("/api/v1/companies", response_model=models.Company, status_code=201)
@action(WRITE)
@takes(models.CompanyBody)
async def create_company(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, COMPANY_FIELDS)
        if not (values.get("name") or values.get("website")):
            raise BadBody("name or website is required")
    except BadBody as e:
        return failed(request, "read_company", e)
    values["id"] = str(uuid.uuid4())
    values["domain"] = derive_domain(values.get("website"), await shared_hosts(db(request)))

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


@app.get("/api/v1/companies/{company_id}", response_model=contract.CompanyDetail)
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


@app.patch("/api/v1/companies/{company_id}", response_model=models.Company)
@action(WRITE)
@takes(models.CompanyBody)
async def update_company(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, COMPANY_FIELDS)
    except BadBody as e:
        return failed(request, "read_company", e)
    if "website" in values:
        values["domain"] = derive_domain(values["website"], await shared_hosts(db(request)))

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


@app.delete("/api/v1/companies/{company_id}", status_code=204)
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


@app.post("/api/v1/companies/{company_id}/archive", response_model=models.Company)
@action(WRITE)
async def archive_company(request: Request, company_id: str) -> JSONResponse:
    return await set_archived(request, company_id, True)


@app.post("/api/v1/companies/{company_id}/unarchive", response_model=models.Company)
@action(WRITE)
async def unarchive_company(request: Request, company_id: str) -> JSONResponse:
    return await set_archived(request, company_id, False)


# ---- Contacts ------------------------------------------------------------------------------------

CONTACTS_OF = "SELECT * FROM contact WHERE company_id = ? ORDER BY is_primary DESC, name COLLATE NOCASE, id"
# A company has at most one primary contact, so the others are cleared in the same transaction.
CLEAR_PRIMARY = f"UPDATE contact SET is_primary = 0, updated_at = {NOW} WHERE company_id = ? AND is_primary = 1 AND id != ?"
# The company's name, and the primary contact that is not the given one (name and email are NULL when there is none).
PRIMARY_BEFORE = (
    "SELECT c.name AS company, p.name, p.email FROM company c"
    " LEFT JOIN contact p ON p.company_id = c.id AND p.is_primary = 1 AND p.id != ? WHERE c.id = ?"
)


def primary_entry(request: Request, company_id: str, company: str | None, old: str | None, new: str | None) -> list[Any]:
    """Record a change of the primary contact in plain words: the Worker log first, then the timeline.

    It stands for the entries of both contacts, the one that gets the mark and the one that loses it
    (specs/043-primary-contact-entry/spec.md).
    """
    body = primary_change(company, old, new)
    log_change(request, body, company_id)
    entry = {"id": str(uuid.uuid4()), "company_id": company_id, "kind": "SYSTEM", "body": body, "created_by": request.state.identity["id"]}
    return [insert(db(request), "activity", entry)]


@app.get("/api/v1/companies/{company_id}/contacts", response_model=Items[models.Contact])
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


@app.post("/api/v1/companies/{company_id}/contacts", response_model=models.Contact, status_code=201)
@action(WRITE)
@takes(models.ContactBody)
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
        company = await db(request).prepare(PRIMARY_BEFORE).bind(values["id"], company_id).first()
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
        replaced = company["name"] or company["email"]
        if replaced:
            # A new contact is not recorded, but the contact it replaces as primary is edited, so that is.
            new = values.get("name") or values.get("email")
            statements = primary_entry(request, company_id, company["company"], replaced, new) + statements
    try:
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "insert_contact", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/contacts/{contact_id}", response_model=models.Contact)
@action(WRITE)
@takes(models.ContactBody)
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

    company_id = current["company_id"]
    before: Row | None = None
    if "is_primary" in changed:
        try:
            before = await db(request).prepare(PRIMARY_BEFORE).bind(contact_id, company_id).first()
        except Exception as e:
            return failed(request, "load_primary", e)

    statements = [update(db(request), "contact", values, contact_id)]
    if values.get("is_primary"):
        statements.insert(0, db(request).prepare(CLEAR_PRIMARY).bind(company_id, contact_id))
    # The primary mark gets its own sentence, which names both contacts; the other fields are recorded as always.
    other = {column: value for column, value in changed.items() if column != "is_primary"}
    if other:
        statements = audit(request, contact_label(current), company_id, current, other) + statements
    if before:
        after = {**current, **changed}
        this = after["name"] or after["email"]
        replaced = before["name"] or before["email"]
        names = (replaced, this) if changed["is_primary"] else (this, None)
        statements = primary_entry(request, company_id, before["company"], *names) + statements
    try:
        results = await db(request).batch(statements)
        row = results[-1]["results"][0]
    except Exception as e:
        return failed(request, "update_contact", e)
    return JSONResponse(row)


def contact_label(contact: Row) -> str:
    return f"Contact {contact['name'] or contact['email']}"


@app.delete("/api/v1/contacts/{contact_id}", status_code=204)
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


@app.get("/api/v1/companies/{company_id}/activities", response_model=Page[models.Activity])
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


@app.post("/api/v1/companies/{company_id}/activities", response_model=models.Activity, status_code=201)
@action(WRITE)
@takes(models.ActivityBody)
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


@app.patch("/api/v1/activities/{activity_id}", response_model=models.Activity)
@action(WRITE)
@takes(models.ActivityBody)
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
@app.delete("/api/v1/activities/{activity_id}", status_code=204)
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


# What a task may point to. Each must be a record of the task's own company (specs/042-task-links/spec.md).
TASK_LINKS = {"deal_id": "deal", "agreement_id": "agreement"}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def linked_company(database: Any, task: Values) -> str | None:
    """The company of a whole task. A task with no company takes the company of its deal or agreement.

    Raises BadBody when a link is unknown or is under another company. The triggers task_links_ins and
    task_links_upd hold the same rule in the database.
    """
    company_id = task.get("company_id")
    for column, table in TASK_LINKS.items():
        if task.get(column) is None:
            continue
        row = await database.prepare(f"SELECT company_id FROM {table} WHERE id = ?").bind(task[column]).first()
        if not row:
            raise BadBody(f"{column} is unknown")
        company_id = company_id or row["company_id"]
        if row["company_id"] != company_id:
            raise BadBody(f"{column} is under another company")
    return company_id


@app.get("/api/v1/tasks", response_model=Page[models.Task])
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


@app.post("/api/v1/tasks", response_model=models.Task, status_code=201)
@action(WRITE)
@takes(models.TaskBody)
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
        values["company_id"] = await linked_company(db(request), values)
    except Exception as e:
        return failed(request, "check_task", e)
    if column:
        return error(request, 400)

    try:
        row = await insert(db(request), "task", values).first()
    except Exception as e:
        return failed(request, "insert_task", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/tasks/{task_id}", response_model=models.Task)
@action(WRITE)
@takes(models.TaskBody)
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
        # The whole task is checked, not only what changed: a new company must fit the deal it keeps.
        company_id = await linked_company(db(request), {**current, **changed})
    except Exception as e:
        return failed(request, "check_task", e)
    if column:
        return error(request, 400)
    if company_id != current["company_id"]:
        values["company_id"] = changed["company_id"] = company_id
    else:
        # Taking the company away from a task that has a deal changes nothing: the deal gives it back.
        values.pop("company_id", None)
        changed.pop("company_id", None)
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
@app.delete("/api/v1/tasks/{task_id}", status_code=204)
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


async def read_for_scoring(database: Any, *more: Any) -> tuple[list[Row], list[Row], dict[str, float], list[list[Row]]]:
    """Every criterion, every service, the score thresholds, and the rows of each further statement,
    read on every call and in one batch. One batch is one moment of the database, so a writer of
    scores sees the rubric, the answers, and the company's mark as they were together
    (specs/039-score-version-mark/spec.md)."""
    results = await database.batch(
        [
            database.prepare("SELECT * FROM rubric_criterion"),
            database.prepare("SELECT * FROM service"),
            database.prepare("SELECT key, value FROM setting WHERE key LIKE 'score.%'"),
            *more,
        ]
    )
    thresholds: dict[str, float] = {}
    for row in results[2]["results"]:
        try:
            thresholds[row["key"]] = float(row["value"])
        except ValueError:
            pass  # a setting that is not a number falls back to the default
    return results[0]["results"], results[1]["results"], thresholds, [result["results"] for result in results[3:]]


async def load_rubric(database: Any) -> tuple[list[Row], list[Row], dict[str, float]]:
    """Every criterion, every service, and the score thresholds, for a caller that writes no scores."""
    criteria, services, thresholds, _ = await read_for_scoring(database)
    return criteria, services, thresholds


# A company's scores are written only while its mark, score_token, is the one the writer read with
# the answers and the rubric; every write gives a new mark. This statement fails when the mark has
# moved (json_extract of a broken text), and a failed statement undoes its whole batch. The writer
# then reads again and redoes its work, at most SCORE_TRIES times in all.
SCORE_GUARD = "SELECT json_extract(CASE WHEN score_token IS ? THEN '{}' ELSE '{' END, '$') FROM company WHERE id = ?"
SCORE_TRIES = 5  # enough for five writers of one company at the same moment; measured on the preview
# What a writer of scores reads of the company. score_writes needs all of it.
SCORED_COMPANY = "SELECT id, fit_score, need_score, recommendation, score_token FROM company"


async def score_moved(database: Any, company: Row) -> bool:
    """Whether another writer wrote the company's scores since company was read. A batch that failed
    is then no error: the writer reads again. False when it cannot be told, and the failure stands."""
    try:
        row = await database.prepare("SELECT score_token FROM company WHERE id = ?").bind(company["id"]).first()
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_error", "step": "check_score_mark", "error": type(e).__name__, "description": str(e)}))
        return False
    return bool(row) and row["score_token"] != company["score_token"]


def score_writes(request: Request, company: Row, scores: Row) -> list[Any]:
    """The statements that store a company's scores, with a timeline entry when the change is notable.

    company is a row of SCORED_COMPANY: the id, the scores before the change, and the mark that was
    read with the answers. The statements go through only while that mark is still the stored one.
    """
    database, company_id = db(request), company["id"]
    statements = [
        database.prepare(SCORE_GUARD).bind(company["score_token"], company_id),
        database.prepare("DELETE FROM company_service WHERE company_id = ?").bind(company_id),
    ]
    for service_id, own in scores["services"].items():
        row = {"company_id": company_id, "service_id": service_id, **own}
        statements.append(insert(database, "company_service", row))
    sql = (
        "UPDATE company SET fit_score = ?, need_score = ?, confidence = ?, recommendation = ?, score_token = ?,"
        f" scored_at = {NOW}, updated_at = {NOW} WHERE id = ?"
    )
    values = [scores[key] for key in ("fit_score", "need_score", "confidence", "recommendation")]
    statements.append(database.prepare(sql).bind(*values, str(uuid.uuid4()), company_id))
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


# The setting that says every score must be recomputed (specs/034-rescore-retry/spec.md). Empty: nothing waits.
RESCORE = "rescore.requested"


def rescore_mark(database: Any) -> Any:
    """A statement that records that every score must be recomputed. It goes in the same batch as the
    change that makes it necessary, so the two are stored together or not at all. rescore_all clears it.

    The value is the time and a tag: two requests never write the same value, so a rescoring can
    clear its own request and leave a later one. With no such setting the statement changes nothing.
    """
    value = f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {uuid.uuid4().hex[:8]}"
    return database.prepare(f"UPDATE setting SET value = ?, updated_at = {NOW} WHERE key = ?").bind(value, RESCORE)


# The switch for automatic rescoring (specs/040-rescore-switch/spec.md): ON, or OFF while the rubric is edited.
RESCORE_AUTO = "rescore.auto"


async def rescore_after(request: Request) -> None:
    """Rescore every company after a change that moves every score, unless automatic rescoring is
    switched off. The change has recorded the request with rescore_mark, so nothing is lost: the
    rescoring runs when the switch is ON again. A missing setting is ON."""
    found = await db(request).prepare("SELECT value FROM setting WHERE key = ?").bind(RESCORE_AUTO).first()
    if found and found["value"] == "OFF":
        return
    await rescore_all(request)


async def rescore_all(request: Request) -> int:
    """Recompute and store the scores of every company that has answers. Returns how many.

    Called after a change to a service or to the rubric, and by the scheduler while the setting
    rescore.requested is set. Stored points are brought up to date too.
    """
    # shortcut: runs inside the request; move it to the background or a queue when it is consistently slow
    database = db(request)
    # Read before the rubric: a change stored after this point writes another value, which is not cleared.
    mark = await database.prepare("SELECT value FROM setting WHERE key = ?").bind(RESCORE).first()
    waiting: set[str] | None = None  # the companies whose scores are still to be written; None: all that have answers
    count = 0
    problem: Exception | None = None
    for _ in range(SCORE_TRIES):
        criteria, services, thresholds, (rows, found) = await read_for_scoring(
            database,
            database.prepare("SELECT company_id, criterion_id, value, points FROM rubric_answer"),
            database.prepare(f"{SCORED_COMPANY} WHERE id IN (SELECT company_id FROM rubric_answer)"),
        )
        by_id = {criterion["id"]: criterion for criterion in criteria}
        answers: dict[str, Row] = {}
        pending: list[Any] = []
        for row in rows:
            answers.setdefault(row["company_id"], {})[row["criterion_id"]] = row["value"]
            earned = points(by_id[row["criterion_id"]], row["value"])
            if earned is not None and abs(earned - row["points"]) > 1e-9:
                sql = "UPDATE rubric_answer SET points = ? WHERE company_id = ? AND criterion_id = ?"
                pending.append(database.prepare(sql).bind(earned, row["company_id"], row["criterion_id"]))
        companies: list[Row] = [company for company in found if waiting is None or company["id"] in waiting]
        if waiting is None:
            count = len(companies)
        waiting = set()
        ids: list[str] = []
        for company in [*companies, None]:  # None: the end, where what is left is written
            if company:
                pending += score_writes(request, company, score(criteria, answers.get(company["id"], {}), services, thresholds))
                ids.append(company["id"])
            if pending and (company is None or len(pending) >= 50):  # each batch is one transaction; a company's statements stay together
                try:
                    await database.batch(pending)
                except Exception as e:
                    # Another writer was first for one of these companies, or the database refused: they are read again.
                    waiting.update(ids)
                    problem = e
                pending, ids = [], []
        if not waiting:
            break
        logger.info(json.dumps({"event": "carmen_rescore_again", "companies": len(waiting), "error": type(problem).__name__}))
    if waiting:
        # The request stays set, so the scheduler rescores again.
        raise RuntimeError(f"{len(waiting)} companies could not be rescored") from problem
    if mark and mark["value"]:  # cleared only when it is still the request found above
        sql = f"UPDATE setting SET value = '', updated_at = {NOW} WHERE key = ? AND value = ?"
        await database.prepare(sql).bind(RESCORE, mark["value"]).run()
    return count


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


@app.get("/api/v1/services", response_model=Items[models.Service])
@action(READ)
async def list_services(request: Request) -> JSONResponse:
    try:
        result = await db(request).prepare("SELECT * FROM service ORDER BY sort_order, name, id").all()
    except Exception as e:
        return failed(request, "list_services", e)
    return JSONResponse({"items": result["results"]})


@app.post("/api/v1/services", response_model=models.Service, status_code=201)
@action(MANAGE)
@takes(models.ServiceBody)
async def create_service(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, SERVICE_FIELDS)
        if not values.get("name"):
            raise BadBody("name is required")
        values["id"] = new_service_id(values.get("id"), values["name"])
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


@app.patch("/api/v1/services/{service_id}", response_model=models.Service)
@action(MANAGE)
@takes(models.ServiceBody)
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
    rescore = "offered" in changed or "active" in changed
    try:
        audit(request, f"Service {current['name']}", None, current, changed)  # no company: the Worker log only
        statements = [update(db(request), "service", changed, service_id, stamp=False)]
        results = await db(request).batch(statements + ([rescore_mark(db(request))] if rescore else []))
        row = results[0]["results"][0]
    except Exception as e:
        return failed(request, "update_service", e)

    if rescore:  # when this fails the request stays recorded, and the scheduler rescores
        try:
            await rescore_after(request)
        except Exception as e:
            return failed(request, "rescore_after_service", e)
    return JSONResponse(row)


# ---- Rubric --------------------------------------------------------------------------------------

# A change to one of these changes scores.
SCORING_COLUMNS = {"section", "service_id", "answer_type", "weight", "config_json", "active"}


@app.get("/api/v1/rubric", response_model=Items[models.RubricCriterion])
@action(READ)
async def list_rubric(request: Request) -> JSONResponse:
    try:
        result = await db(request).prepare(f"SELECT c.* {RUBRIC_FROM} {RUBRIC_ORDER}").all()
    except Exception as e:
        return failed(request, "list_rubric", e)
    return JSONResponse({"items": result["results"]})


@app.post("/api/v1/rubric/recompute", response_model=contract.Recomputed)
@action(WRITE)
async def recompute_scores(request: Request) -> JSONResponse:
    try:
        count = await rescore_all(request)
    except Exception as e:
        return failed(request, "rescore", e)
    return JSONResponse({"companies": count})


@app.post("/api/v1/rubric", response_model=models.RubricCriterion, status_code=201)
@action(MANAGE)
@takes(models.CriterionBody)
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
        results = await db(request).batch([insert(db(request), "rubric_criterion", values), rescore_mark(db(request))])
        row = results[0]["results"][0]
    except Exception as e:
        return failed(request, "insert_criterion", e)

    try:
        # A new criterion is unanswered everywhere, which lowers confidence.
        await rescore_after(request)
    except Exception as e:
        return failed(request, "rescore_after_criterion", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/rubric/{criterion_id}", response_model=models.RubricCriterion)
@action(MANAGE)
@takes(models.CriterionBody)
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
    rescore = bool(SCORING_COLUMNS & set(changed))
    try:
        audit(request, f"Rubric question {current['prompt']}", None, current, changed)  # no company: the Worker log only
        statements = [update(db(request), "rubric_criterion", changed, criterion_id, stamp=False)]
        results = await db(request).batch(statements + ([rescore_mark(db(request))] if rescore else []))
        row = results[0]["results"][0]
    except Exception as e:
        return failed(request, "update_criterion", e)

    if rescore:
        try:
            await rescore_after(request)
        except Exception as e:
            return failed(request, "rescore_after_criterion", e)
    return JSONResponse(row)


# ---- Answers -------------------------------------------------------------------------------------


@app.get("/api/v1/companies/{company_id}/answers", response_model=Items[contract.Answer])
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


@app.put("/api/v1/companies/{company_id}/answers", response_model=contract.Scores)
@action(WRITE)
@takes(contract.AnswersBody)
async def set_answers(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    entries: Any = body.get("answers")
    if not isinstance(entries, list) or not entries:
        return error(request, 400)

    database = db(request)
    for attempt in range(SCORE_TRIES):
        try:
            # One read, so the answers, the rubric, and the company's mark are of one moment.
            criteria, services, thresholds, (found, stored) = await read_for_scoring(
                database,
                database.prepare(f"{SCORED_COMPANY} WHERE id = ?").bind(company_id),
                database.prepare("SELECT criterion_id, value FROM rubric_answer WHERE company_id = ?").bind(company_id),
            )
        except Exception as e:
            return failed(request, "load_answers", e)
        if not found:
            return error(request, 404)
        company = found[0]

        in_use = {criterion["id"]: criterion for criterion in criteria if criterion["active"]}
        answers: Row = {row["criterion_id"]: row["value"] for row in stored}
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
                if answers.get(criterion["id"]) == text:
                    # Unchanged, so it is not written: its source, its run, the person, and the time stay
                    # as they were. A screen may send the whole form (specs/041-unchanged-answers/spec.md).
                    continue
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
            if attempt + 1 < SCORE_TRIES and await score_moved(database, company):
                # Another writer was first: read again, and put these answers on what is stored now.
                logger.info(json.dumps({"event": "carmen_scores_again", "step": "store_answers", "company_id": company_id}))
                continue
            return failed(request, "store_answers", e)
        return JSONResponse(scores_body(scores, services))
    return error(request, 500)  # not reached: the last try answers


# ---- Enrichment facts ----------------------------------------------------------------------------

FACT_COLUMNS = "id, company_id, key, value, source, source_detail, notes, ai_run_id, observed_at, created_at"
NEWEST_FACT = "observed_at DESC, created_at DESC, id"
# The company's latest fact for each key.
LATEST_FACTS = (
    "SELECT key, value FROM (SELECT key, value, ROW_NUMBER() OVER (PARTITION BY key ORDER BY"
    f" {NEWEST_FACT}) AS place FROM enrichment_fact WHERE company_id = ?) WHERE place = 1 ORDER BY key"
)


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


@app.get("/api/v1/facts/keys", response_model=Items[contract.FactKey])
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


@app.get("/api/v1/companies/{company_id}/facts", response_model=Page[models.EnrichmentFact])
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


def fact_inserts(database: Any, company_id: str, given: object, source: str, types: dict[str, str]) -> list[Any]:
    """One INSERT for each fact of a request. types holds the value type of each well-known key.
    Raises BadBody for anything but a list of facts that each have a key and a value."""
    if not isinstance(given, list) or not given:
        raise BadBody("facts must be a list with at least one fact")
    statements: list[Any] = []
    for entry in cast(list[Any], given):
        values = fact_fields(entry)
        values["key"] = fact_key(values.get("key") or "")
        if not values["key"] or not values.get("value"):
            raise BadBody("a fact needs a key and a value")
        values["value"] = fact_value(values["value"], types.get(values["key"]))
        values.update(id=str(uuid.uuid4()), company_id=company_id, source=source)
        statements.append(insert(database, "enrichment_fact", values))
    return statements


@app.post("/api/v1/companies/{company_id}/facts", response_model=Items[models.EnrichmentFact], status_code=201)
@action(WRITE)
@takes(contract.FactsBody)
async def create_facts(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        known = await db(request).prepare("SELECT key, value_type FROM enrichment_key").all()
    except Exception as e:
        return failed(request, "load_fact_keys", e)
    types: dict[str, str] = {row["key"]: row["value_type"] for row in known["results"]}

    try:
        # Many facts under "facts", or the body is one fact.
        statements = fact_inserts(db(request), company_id, body.get("facts", [body]), "MANUAL", types)
    except BadBody as e:
        return failed(request, "read_facts", e)
    if not company:
        return error(request, 404)

    try:
        # All of them or none: one transaction.
        results = await db(request).batch(statements)
        rows = [result["results"][0] for result in results]
    except Exception as e:
        return failed(request, "insert_facts", e)
    return JSONResponse({"items": rows}, status_code=201)


@app.patch("/api/v1/facts/{fact_id}", response_model=models.EnrichmentFact)
@action(WRITE)
@takes(contract.FactIn)
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


@app.delete("/api/v1/facts/{fact_id}", status_code=204)
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
TODAY_UNSCORED = TODAY_LEADS.format("fit_score IS NULL AND need_score IS NULL ORDER BY created_at DESC, id")
# Binds: the default notice days twice, today's date, and the limit.
TODAY_RENEWING = (
    f"SELECT a.*, c.name AS company_name, {NOTICE_DATE} AS notice_date FROM agreement a"
    " JOIN company c ON c.id = a.company_id WHERE a.status = 'ACTIVE' AND a.end_date IS NOT NULL"
    f" AND {NOTICE_DATE} <= ? ORDER BY a.end_date, a.id LIMIT ?"
)


@app.get("/api/v1/today", response_model=contract.Today)
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
        database.prepare(TODAY_UNSCORED).bind(limit),
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
        database.prepare(TODAY_RENEWING).bind(notice_days, notice_days, date, limit),
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


@app.get("/api/v1/deals", response_model=Page[contract.DealOut])
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


@app.post("/api/v1/deals", response_model=contract.DealOut, status_code=201)
@action(WRITE)
@takes(contract.DealBody)
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


@app.get("/api/v1/deals/{deal_id}", response_model=contract.DealOut)
@action(READ)
async def get_deal(request: Request, deal_id: str) -> JSONResponse:
    try:
        row = await db(request).prepare(f"{DEAL_SELECT} WHERE d.id = ?").bind(deal_id).first()
    except Exception as e:
        return failed(request, "load_deal", e)
    return JSONResponse(deal_out(row)) if row else error(request, 404)


@app.patch("/api/v1/deals/{deal_id}", response_model=contract.DealOut)
@action(WRITE)
@takes(contract.DealBody)
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
@app.delete("/api/v1/deals/{deal_id}", status_code=204)
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


@app.get("/api/v1/agreements", response_model=Page[contract.AgreementOut])
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


@app.post("/api/v1/agreements", response_model=contract.AgreementOut, status_code=201)
@action(WRITE)
@takes(contract.AgreementBody)
async def create_agreement(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, AGREEMENT_FIELDS)
        services = id_list(body, "service_ids") or []
        if not values.get("company_id") or not values.get("type"):
            raise BadBody("company_id and type are required")
        if problem := renewal_problem(values):
            raise BadBody(problem)
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


@app.get("/api/v1/agreements/{agreement_id}", response_model=contract.AgreementDetail)
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


@app.patch("/api/v1/agreements/{agreement_id}", response_model=contract.AgreementOut)
@action(WRITE)
@takes(contract.AgreementBody)
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
    if problem := renewal_problem({**current, **changed}):
        return failed(request, "check_renewal_policy", BadBody(problem))
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
    if changed.get("status") == "COMPLETED" and row["renewal_policy"] == "ONE_TIME":
        await queue_proposals(request.scope["env"], agreement_id)  # logs a failure itself; the edit stands
    return JSONResponse(agreement_out(row))


# Approving a renewal is its own action, not an edit of the end date: the end moves forward by the
# agreement's term, and the timeline says who approved it. See specs/024-renewal-policy/spec.md.
@app.post("/api/v1/agreements/{agreement_id}/renewal", response_model=contract.AgreementOut)
@action(WRITE)
async def approve_renewal(request: Request, agreement_id: str) -> JSONResponse:
    database = db(request)
    try:
        found = await database.prepare(f"{AGREEMENT_SELECT} WHERE a.id = ?").bind(agreement_id).first()
    except Exception as e:
        return failed(request, "load_agreement", e)
    if not found:
        return error(request, 404)
    renewable = found["status"] == "ACTIVE" and found["renewal_policy"] == "APPROVAL"
    if not (renewable and found["end_date"] and found["term_months"]):
        return error(request, 409)  # only an active agreement that renews on approval, with its end and its term

    try:
        end_date = add_months(str(found["end_date"]), int(found["term_months"]))
        change = f"{agreement_label(found)}: renewal approved for {found['term_months']} months."
        change = f"{change} end_date {found['end_date']!r} to {end_date!r}"
        log_change(request, change, found["company_id"])
        entry = {
            "id": str(uuid.uuid4()),
            "company_id": found["company_id"],
            "kind": "SYSTEM",
            "body": change,
            "created_by": request.state.identity["id"],
        }
        statements = [
            insert(database, "activity", entry),
            update(database, "agreement", {"end_date": end_date}, agreement_id),
            # The task that the alert made is done: the renewal it asked for was decided.
            database.prepare(
                f"UPDATE task SET status = 'DONE', completed_at = {NOW} WHERE agreement_id = ? AND status = 'OPEN'"
                " AND title LIKE 'Renewal:%'"
            ).bind(agreement_id),
        ]
        await database.batch(statements)
        row = await database.prepare(f"{AGREEMENT_SELECT} WHERE a.id = ?").bind(agreement_id).first()
    except Exception as e:
        return failed(request, "approve_renewal", e)
    return JSONResponse(agreement_out(row))


# WRITE, not DELETE: a MEMBER may delete an agreement they own. may_delete decides.
@app.delete("/api/v1/agreements/{agreement_id}", status_code=204)
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


@app.get("/api/v1/companies/{company_id}/documents", response_model=Items[models.Document])
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


@app.post(
    "/api/v1/companies/{company_id}/documents",
    response_model=models.Document,
    status_code=201,
    # The body is the file itself, read as a stream, so FastAPI cannot see it.
    openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
)
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


@app.get("/api/v1/documents/{document_id}", response_model=models.Document)
@action(READ)
async def get_document(request: Request, document_id: str) -> JSONResponse:
    sql = f"SELECT {DOCUMENT_COLUMNS} FROM document WHERE id = ?"
    try:
        row = await db(request).prepare(sql).bind(document_id).first()
    except Exception as e:
        return failed(request, "load_document", e)
    return JSONResponse(row) if row else error(request, 404)


@app.get(
    "/api/v1/documents/{document_id}/content",
    response_class=Response,
    responses={200: {"description": "The file", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
)
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
@app.delete("/api/v1/documents/{document_id}", status_code=204)
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


# ---- AI models -----------------------------------------------------------------------------------


@app.get("/api/v1/ai/models", response_model=Items[contract.ModelOut])
@action(READ)
async def list_models(
    request: Request, can_research: bool | None = None, can_score: bool | None = None, active: bool | None = None
) -> JSONResponse:
    where = ["1 = 1"]
    params: list[Any] = []
    for column, value in {"can_research": can_research, "can_score": can_score, "active": active}.items():
        if value is not None:
            where.append(f"{column} = ?")
            params.append(int(value))
    sql = f"SELECT * FROM ai_model WHERE {' AND '.join(where)} ORDER BY sort_order, label, id"
    try:
        result = await db(request).prepare(sql).bind(*params).all()
    except Exception as e:
        return failed(request, "list_models", e)
    # runnable says whether the model's provider has an adapter in this version of the API.
    return JSONResponse({"items": [{**row, "runnable": row["provider"] in ai.ADAPTERS} for row in result["results"]]})


@app.get("/api/v1/ai/models/stats", response_model=Items[contract.ModelStats])
@action(READ)
async def model_stats(request: Request) -> JSONResponse:
    # agreement_rate: of a model's answers, the share that equal the company's answer in effect.
    sql = (
        "SELECT m.id AS ai_model_id, m.label, m.provider, r.kind, COUNT(*) AS runs,"
        " SUM(r.status = 'FAILED') AS failures, AVG(r.user_rating) AS average_rating,"
        " AVG(r.duration_ms) AS average_duration_ms, AVG(r.input_tokens + r.output_tokens) AS average_tokens,"
        " (SELECT AVG(a.value = e.value) FROM ai_run_answer a JOIN ai_run x ON x.id = a.ai_run_id"
        " JOIN rubric_answer e ON e.company_id = x.company_id AND e.criterion_id = a.criterion_id"
        " WHERE x.ai_model_id = m.id AND x.kind = r.kind) AS agreement_rate"
        " FROM ai_run r JOIN ai_model m ON m.id = r.ai_model_id GROUP BY m.id, r.kind"
        " ORDER BY m.sort_order, m.label, r.kind"
    )
    try:
        result = await db(request).prepare(sql).all()
    except Exception as e:
        return failed(request, "model_stats", e)
    return JSONResponse({"items": result["results"]})


@app.post("/api/v1/ai/models", response_model=models.AiModel, status_code=201)
@action(MANAGE)
@takes(models.ModelBody)
async def create_model(request: Request, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, MODEL_FIELDS)
        if not (values.get("provider") and values.get("model_id") and values.get("label")):
            raise BadBody("provider, model_id, and label are required")
        if values["provider"] not in ai.ADAPTERS:
            raise BadBody("the provider has no adapter")
    except BadBody as e:
        return failed(request, "read_model", e)
    values["id"] = str(uuid.uuid4())

    try:
        column = await unknown_code(db(request), values, MODEL_CODES)
    except Exception as e:
        return failed(request, "check_model", e)
    if column:
        return error(request, 400)

    try:
        row = await insert(db(request), "ai_model", values).first()
    except Exception as e:
        return failed(request, "insert_model", e)
    return JSONResponse(row, status_code=201)


@app.patch("/api/v1/ai/models/{model_id}", response_model=models.AiModel)
@action(MANAGE)
@takes(models.ModelBody)
async def update_model(request: Request, model_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, MODEL_FIELDS)
    except BadBody as e:
        return failed(request, "read_model", e)

    try:
        current = await db(request).prepare("SELECT * FROM ai_model WHERE id = ?").bind(model_id).first()
    except Exception as e:
        return failed(request, "load_model", e)
    if not current:
        return error(request, 404)
    changed = {column: value for column, value in values.items() if value != current[column]}
    if not changed:
        return JSONResponse(current)
    if "provider" in changed and changed["provider"] not in ai.ADAPTERS:
        return error(request, 400)  # a model can only be moved to a provider that has an adapter

    try:
        audit(request, f"AI model {current['label']}", None, current, changed)  # no company: the Worker log only
        row = await update(db(request), "ai_model", changed, model_id, stamp=False).first()
    except Exception as e:
        return failed(request, "update_model", e)
    return JSONResponse(row)


# ---- AI runs: starting them ----------------------------------------------------------------------
# A request only records the runs and puts them on the RUNS queue. The work is done by process_run,
# called from the queue handler in worker.py: work started after a response is cut off at about
# 30 seconds, and research can take longer (discovery D2).

RUN_SELECT = (
    "SELECT r.id, r.company_id, r.kind, r.ai_model_id, m.label AS model_label, m.provider, r.model_id_used,"
    " r.input_run_id, r.status, r.fit_score, r.need_score, r.confidence, r.recommendation, r.input_tokens,"
    " r.output_tokens, r.search_count, r.duration_ms, r.model_ms, r.user_rating, r.user_note, r.applied_at,"
    " r.requested_by, r.error, r.created_at, r.started_at, r.finished_at"
    " FROM ai_run r JOIN ai_model m ON m.id = r.ai_model_id"
)


def now_ms() -> str:
    """The current moment in UTC with milliseconds, as a run's started_at and finished_at are stored."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def usable_models(database: Any, model_ids: list[str], capability: str) -> list[Row] | None:
    """The models, in the order given, or None unless every one exists, is enabled, has the
    capability (can_research or can_score), and belongs to a provider that has an adapter."""
    if not model_ids:
        return None
    marks = ", ".join("?" * len(model_ids))
    found = await database.prepare(f"SELECT * FROM ai_model WHERE id IN ({marks})").bind(*model_ids).all()
    by_id = {row["id"]: row for row in found["results"]}
    models = [by_id.get(model_id) for model_id in model_ids]
    for model in models:
        if model is None or not model["active"] or not model[capability] or model["provider"] not in ai.ADAPTERS:
            return None
    return cast(list[Row], models)


def new_run(request: Request, company_id: str, kind: str, model: Row, input_run_id: str | None) -> Row:
    return {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "kind": kind,
        "ai_model_id": model["id"],
        "model_id_used": model["model_id"],  # kept, so a later change to the model does not rewrite history
        "input_run_id": input_run_id,
        "requested_by": request.state.identity["id"],
    }


async def store_and_queue(request: Request, runs: list[Row], queue_now: list[Row]) -> JSONResponse:
    """Store the runs as QUEUED, put queue_now on the queue, and answer 202 with every run id."""
    database = db(request)
    try:
        await database.batch([insert(database, "ai_run", run) for run in runs])
    except Exception as e:
        return failed(request, "insert_runs", e)
    try:
        for run in queue_now:
            await request.scope["env"].RUNS.send({"run_id": run["id"]})
    except Exception as e:
        log_error(request, "queue_runs", e)
        try:
            # A run that never reached the queue would stay QUEUED forever, so it is failed now.
            marks = ", ".join("?" * len(runs))
            sql = (
                "UPDATE ai_run SET status = 'FAILED', error = 'The run could not be queued', finished_at = ?"
                f" WHERE status = 'QUEUED' AND id IN ({marks})"
            )
            await database.prepare(sql).bind(now_ms(), *[run["id"] for run in runs]).run()
        except Exception as inner:
            log_error(request, "fail_unqueued_runs", inner)
        return error(request, 500)
    return JSONResponse({"run_ids": [run["id"] for run in runs]}, status_code=202)


@app.post("/api/v1/companies/{company_id}/runs", response_model=contract.RunIds, status_code=202)
@action(WRITE)
@takes(contract.StartRunsBody)
async def start_runs(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    kind, input_run_id = body.get("kind"), body.get("input_run_id")
    try:
        model_ids = id_list(body, "ai_model_ids")
        if kind not in ("RESEARCH", "SCORING") or not model_ids:
            raise BadBody("kind and ai_model_ids are required")
        if input_run_id is not None and (kind != "SCORING" or not isinstance(input_run_id, str)):
            raise BadBody("input_run_id is for a scoring run")
    except BadBody as e:
        return failed(request, "read_runs", e)

    database = db(request)
    try:
        company = await database.prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        models = await usable_models(database, model_ids, "can_research" if kind == "RESEARCH" else "can_score")
        input_ok = True
        if input_run_id is not None:
            # Only a finished research run of the same company can be scored.
            sql = "SELECT 1 AS found FROM ai_run WHERE id = ? AND company_id = ? AND kind = 'RESEARCH' AND status = 'SUCCEEDED'"
            input_ok = bool(await database.prepare(sql).bind(input_run_id, company_id).first())
    except Exception as e:
        return failed(request, "check_runs", e)
    if not company:
        return error(request, 404)
    if models is None or not input_ok:
        return error(request, 400)

    runs = [new_run(request, company_id, kind, model, input_run_id) for model in models]
    return await store_and_queue(request, runs, runs)


@app.post("/api/v1/companies/{company_id}/runs/research-and-score", response_model=contract.RunIds, status_code=202)
@action(WRITE)
@takes(contract.ResearchAndScoreBody)
async def research_and_score(request: Request, company_id: str, body: dict[str, Any]) -> JSONResponse:
    research_id = body.get("research_model_id")
    try:
        scoring_ids = id_list(body, "scoring_model_ids")
        if not isinstance(research_id, str) or not scoring_ids:
            raise BadBody("research_model_id and scoring_model_ids are required")
    except BadBody as e:
        return failed(request, "read_runs", e)

    database = db(request)
    try:
        company = await database.prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        researchers = await usable_models(database, [research_id], "can_research")
        scorers = await usable_models(database, scoring_ids, "can_score")
    except Exception as e:
        return failed(request, "check_runs", e)
    if not company:
        return error(request, 404)
    if researchers is None or scorers is None:
        return error(request, 400)

    # Only the research run is queued now. When it succeeds, process_run queues the scoring runs that wait for it.
    research = new_run(request, company_id, "RESEARCH", researchers[0], None)
    scoring = [new_run(request, company_id, "SCORING", model, research["id"]) for model in scorers]
    return await store_and_queue(request, [research, *scoring], [research])


# ---- AI runs: doing the work ---------------------------------------------------------------------
# Called from the queue, with no request. Nothing here changes the company: a run only records what
# the model proposed, and a timeline entry that it finished.


def run_log(run_id: str, step: str, detail: str) -> None:
    logger.info(json.dumps({"event": "carmen_run", "run_id": run_id, "step": step, "detail": detail}))


def run_entry(run: Row, body: str) -> Row:
    return {
        "id": str(uuid.uuid4()),
        "company_id": run["company_id"],
        "kind": "SYSTEM",
        "body": body,
        "created_by": run["requested_by"],
    }


async def call_twice(run: Row, call: Callable[[], Awaitable[Row]]) -> Row:
    """Ask the model. Output that cannot be read is asked for once more, then the run fails."""
    try:
        return await call()
    except ai.BadOutput as e:
        run_log(run["id"], "retry", str(e))
    try:
        return await call()
    except ai.BadOutput as e:
        raise ai.RunError("The model's output could not be read, twice") from e


async def research_statements(env: Any, run: Row, model: Row) -> tuple[list[Any], Row, str]:
    database = env.DB
    company = await database.prepare("SELECT * FROM company WHERE id = ?").bind(run["company_id"]).first()
    known = await database.prepare("SELECT key, value_type FROM enrichment_key").all()
    output = await call_twice(run, lambda: ai.ADAPTERS[model["provider"]].research(env, model, company))
    facts = ai.clean_facts(output.get("facts"), {row["key"]: row["value_type"] for row in known["results"]})
    dossier = str(output.get("dossier") or "").strip()[:200000] or None
    statements = [
        insert(database, "ai_run_fact", {"id": str(uuid.uuid4()), "ai_run_id": run["id"], **fact}) for fact in facts
    ]
    return statements, {**output, "dossier": dossier}, f"{len(facts)} facts proposed{ai.skipped_note(output)}"


async def scoring_statements(env: Any, run: Row, model: Row) -> tuple[list[Any], Row, str]:
    database = env.DB
    company = await database.prepare("SELECT * FROM company WHERE id = ?").bind(run["company_id"]).first()
    if run["input_run_id"]:
        # The dossier of one research run and all of its facts, accepted or not.
        source = await database.prepare("SELECT dossier FROM ai_run WHERE id = ?").bind(run["input_run_id"]).first()
        sql = "SELECT key, value FROM ai_run_fact WHERE ai_run_id = ? ORDER BY key, id"
        facts = await database.prepare(sql).bind(run["input_run_id"]).all()
        text = ai.material(company, facts["results"], source["dossier"] if source else None)
    else:
        # The company's latest fact for each key.
        facts = await database.prepare(LATEST_FACTS).bind(run["company_id"]).all()
        text = ai.material(company, facts["results"])
    criteria, services, thresholds = await load_rubric(database)
    in_use = [criterion for criterion in criteria if criterion["active"]]
    # A measured criterion is answered by code from its fact, and no model is asked it: a model that
    # reads text cannot measure. With no such fact it stays unknown.
    asked = [criterion for criterion in in_use if not measured(criterion)]
    output = await call_twice(run, lambda: ai.ADAPTERS[model["provider"]].score(env, model, asked, text))
    answers, dropped = ai.clean_answers(output.get("answers"), asked)
    if dropped:
        run_log(run["id"], "answers_dropped", str(dropped))
    known = {fact["key"]: fact["value"] for fact in facts["results"]}
    answers += ai.clean_answers([from_fact(criterion, known) for criterion in in_use], in_use)[0]
    # The run's own scores, from the same engine as the company's. Nothing is applied to the company.
    scores = score(criteria, {answer["criterion_id"]: answer["value"] for answer in answers}, services, thresholds)
    statements = [insert(database, "ai_run_answer", {"ai_run_id": run["id"], **answer}) for answer in answers]
    statements += [
        insert(database, "ai_run_service", {"ai_run_id": run["id"], "service_id": service_id, **own})
        for service_id, own in scores["services"].items()
    ]
    said = (
        f"{len(answers)} answers; Fit {scores['fit_score']}, Need {scores['need_score']},"
        f" recommendation {scores['recommendation']}. Nothing was applied"
    )
    return statements, {**output, **scores}, said


async def fail_run(env: Any, run: Row, message: str) -> None:
    """Mark the run FAILED, and with it every run that was waiting to score its dossier."""
    database, now = env.DB, now_ms()
    run_log(run["id"], "failed", message)
    waiting = "UPDATE ai_run SET status = 'FAILED', error = ?, finished_at = ? WHERE input_run_id = ? AND status = 'QUEUED'"
    statements = [
        database.prepare("UPDATE ai_run SET status = 'FAILED', error = ?, finished_at = ? WHERE id = ?").bind(
            message, now, run["id"]
        ),
        database.prepare(waiting).bind("The research run it was to score failed", now, run["id"]),
        insert(database, "activity", run_entry(run, f"{run['kind'].capitalize()} run by {run['label']} failed: {message}")),
    ]
    await database.batch(statements)


async def start_waiting(env: Any, run_id: str) -> None:
    """Queue the scoring runs that waited for this research run's dossier. The research is stored by
    now, so a failure here fails only the run that could not be started, never the research run."""
    database = env.DB
    fail = "UPDATE ai_run SET status = 'FAILED', error = 'The run could not be started', finished_at = ? WHERE status = 'QUEUED' AND "
    try:
        waiting = await database.prepare("SELECT id FROM ai_run WHERE input_run_id = ? AND status = 'QUEUED'").bind(run_id).all()
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_run", "run_id": run_id, "step": "find_waiting", "error": type(e).__name__}))
        await database.prepare(fail + "input_run_id = ?").bind(now_ms(), run_id).run()
        return
    for row in waiting["results"]:
        try:
            await env.RUNS.send({"run_id": row["id"]})
        except Exception as e:
            logger.error(json.dumps({"event": "carmen_run", "run_id": row["id"], "step": "send_waiting", "error": type(e).__name__}))
            await database.prepare(fail + "id = ?").bind(now_ms(), row["id"]).run()


async def process_run(env: Any, run_id: str) -> bool:
    """Carry out one queued run. Never raises: a failure is recorded on the run. False when the failure
    could not be recorded, so that the queue delivers the message again and the run is then marked
    as interrupted, not left RUNNING."""
    database = env.DB
    run: Row | None = None
    stored = False  # the run's result is in the database
    try:
        sql = "SELECT r.*, m.label, m.provider FROM ai_run r JOIN ai_model m ON m.id = r.ai_model_id WHERE r.id = ?"
        run = await database.prepare(sql).bind(run_id).first()
        if not run:
            run_log(run_id, "skipped", "no such run")
            return True
        if run["status"] == "RUNNING":
            # Delivered a second time: the first attempt was cut off before it could finish.
            await fail_run(env, run, "The run was interrupted and did not finish")
            return True
        if run["status"] != "QUEUED":
            run_log(run_id, "skipped", f"status {run['status']}")
            return True
        if run["provider"] not in ai.ADAPTERS:
            await fail_run(env, run, "The model's provider has no adapter")
            return True

        started = now_ms()
        await database.prepare("UPDATE ai_run SET status = 'RUNNING', started_at = ? WHERE id = ?").bind(started, run_id).run()
        model = await database.prepare("SELECT * FROM ai_model WHERE id = ?").bind(run["ai_model_id"]).first()
        clock = time.monotonic()
        if run["kind"] == "RESEARCH":
            statements, output, said = await research_statements(env, run, model)
        else:
            statements, output, said = await scoring_statements(env, run, model)
        model_ms = int((time.monotonic() - clock) * 1000)

        tokens_in, tokens_out = ai.token_counts(output)
        finish = (
            "UPDATE ai_run SET status = 'SUCCEEDED', finished_at = ?, dossier = ?, fit_score = ?, need_score = ?,"
            " confidence = ?, recommendation = ?, input_tokens = ?, output_tokens = ?, search_count = ?, model_ms = ?,"
            " model_id_used = COALESCE(?, model_id_used)"
            " WHERE id = ?"
        )
        results = [output.get(key) for key in ("dossier", "fit_score", "need_score", "confidence", "recommendation")]
        searches = output.get("search_count") if isinstance(output.get("search_count"), int) else None
        # An alias can move, so the version that answered replaces the name that was asked for.
        used = output.get("model_id_used") if isinstance(output.get("model_id_used"), str) else None
        statements.append(database.prepare(finish).bind(now_ms(), *results, tokens_in, tokens_out, searches, model_ms, used, run_id))
        statements.append(
            insert(database, "activity", run_entry(run, f"{run['kind'].capitalize()} run by {run['label']} finished: {said}"))
        )
        await database.batch(statements)
        stored = True
        run_log(run_id, "succeeded", said)

        if run["kind"] == "RESEARCH":
            await start_waiting(env, run_id)
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_run", "run_id": run_id, "step": "error", "error": type(e).__name__, "detail": str(e)}))
        try:
            if run and not stored:  # a run whose result is stored stays SUCCEEDED
                # Our own messages are safe to show. Anything else stays in the log.
                shown = str(e) if isinstance(e, ai.RunError) else f"The run failed unexpectedly ({type(e).__name__}); see the Worker log"
                await fail_run(env, run, shown)
        except Exception as inner:
            logger.error(json.dumps({"event": "carmen_run", "run_id": run_id, "step": "fail_run", "error": type(inner).__name__}))
            return False
    return True


# ---- AI runs: reading and deciding ---------------------------------------------------------------


@app.get("/api/v1/companies/{company_id}/runs", response_model=Page[contract.RunOut])
@action(READ)
async def list_runs(
    request: Request, company_id: str, limit: int = Query(50, ge=1, le=200), cursor: int = Query(0, ge=0)
) -> JSONResponse:
    sql = f"{RUN_SELECT} WHERE r.company_id = ? ORDER BY r.created_at DESC, r.id LIMIT ? OFFSET ?"
    try:
        company = await db(request).prepare("SELECT id FROM company WHERE id = ?").bind(company_id).first()
        result = await db(request).prepare(sql).bind(company_id, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_runs", e)
    if not company:
        return error(request, 404)
    return page(result, limit, cursor)


@app.get("/api/v1/companies/{company_id}/runs/compare", response_model=contract.Comparison)
@action(READ)
async def compare_runs(request: Request, company_id: str, run_ids: str) -> JSONResponse:
    wanted = [run_id for run_id in run_ids.split(",") if run_id]
    if not wanted:
        return error(request, 400)
    database, marks = db(request), ", ".join("?" * len(wanted))
    try:
        sql = f"{RUN_SELECT} WHERE r.company_id = ? AND r.kind = 'SCORING' AND r.id IN ({marks})"
        runs = await database.prepare(sql).bind(company_id, *wanted).all()
        sql = f"SELECT ai_run_id, criterion_id, value, points, probability, rationale FROM ai_run_answer WHERE ai_run_id IN ({marks})"
        answers = await database.prepare(sql).bind(*wanted).all()
        sql = "SELECT criterion_id, value, points, source, ai_run_id FROM rubric_answer WHERE company_id = ?"
        effect = await database.prepare(sql).bind(company_id).all()
        criteria = await database.prepare(f"SELECT c.* {RUBRIC_FROM} {RUBRIC_ORDER}").all()
    except Exception as e:
        return failed(request, "compare_runs", e)
    found: list[Row] = runs["results"]
    if len(found) != len(set(wanted)):
        return error(request, 400)  # every id must be a scoring run of this company

    by_run: dict[str, dict[str, Row]] = {run_id: {} for run_id in wanted}
    for answer in answers["results"]:
        own = {column: answer[column] for column in ("value", "points", "probability", "rationale")}
        by_run[answer["ai_run_id"]][answer["criterion_id"]] = own
    in_effect = {row["criterion_id"]: row for row in effect["results"]}
    return JSONResponse({"runs": found, "items": ai.compare(criteria["results"], in_effect, by_run)})


@app.get("/api/v1/runs/{run_id}", response_model=contract.RunDetail)
@action(READ)
async def get_run(request: Request, run_id: str) -> JSONResponse:
    database = db(request)
    try:
        run = await database.prepare(f"{RUN_SELECT} WHERE r.id = ?").bind(run_id).first()
        if not run:
            return error(request, 404)
        if run["kind"] == "RESEARCH":
            dossier = await database.prepare("SELECT dossier FROM ai_run WHERE id = ?").bind(run_id).first()
            sql = "SELECT id, key, value, source_url, notes, decision FROM ai_run_fact WHERE ai_run_id = ? ORDER BY key, id"
            facts = await database.prepare(sql).bind(run_id).all()
            return JSONResponse({**run, "dossier": dossier["dossier"], "facts": facts["results"]})
        sql = (
            "SELECT a.criterion_id, c.section, c.service_id, c.prompt, c.answer_type, a.value, a.points, a.probability,"
            f" a.rationale FROM ai_run_answer a JOIN rubric_criterion c ON c.id = a.criterion_id"
            " LEFT JOIN service s ON s.id = c.service_id WHERE a.ai_run_id = ?"
            " ORDER BY c.section = 'NEED', s.sort_order, s.id, c.sort_order, c.id"
        )
        answers = await database.prepare(sql).bind(run_id).all()
        sql = (
            "SELECT x.service_id, s.name, s.offered, x.need_score, x.confidence FROM ai_run_service x"
            " JOIN service s ON s.id = x.service_id WHERE x.ai_run_id = ? ORDER BY x.need_score DESC, s.sort_order, s.id"
        )
        services = await database.prepare(sql).bind(run_id).all()
    except Exception as e:
        return failed(request, "load_run", e)
    return JSONResponse({**run, "answers": answers["results"], "services": services["results"]})


@app.patch("/api/v1/runs/{run_id}", response_model=contract.RunOut)
@action(WRITE)
@takes(models.RunBody)
async def rate_run(request: Request, run_id: str, body: dict[str, Any]) -> JSONResponse:
    try:
        values = clean(body, RUN_FIELDS)
    except BadBody as e:
        return failed(request, "read_run", e)

    database = db(request)
    try:
        current = await database.prepare(f"{RUN_SELECT} WHERE r.id = ?").bind(run_id).first()
    except Exception as e:
        return failed(request, "load_run", e)
    if not current:
        return error(request, 404)
    changed = {column: value for column, value in values.items() if value != current[column]}
    if not changed:
        return JSONResponse(current)

    try:
        what = f"{current['kind'].capitalize()} run by {current['model_label']}"
        statements = [
            *audit(request, what, current["company_id"], current, changed),
            update(database, "ai_run", changed, run_id, stamp=False),
        ]
        await database.batch(statements)
        row = await database.prepare(f"{RUN_SELECT} WHERE r.id = ?").bind(run_id).first()
    except Exception as e:
        return failed(request, "update_run", e)
    return JSONResponse(row)


@app.post("/api/v1/runs/{run_id}/facts/decide", response_model=Items[models.AiRunFact])
@action(WRITE)
@takes(contract.DecideBody)
async def decide_facts(request: Request, run_id: str, body: dict[str, Any]) -> JSONResponse:
    database = db(request)
    try:
        run = await database.prepare(f"{RUN_SELECT} WHERE r.id = ?").bind(run_id).first()
        proposed = await database.prepare("SELECT * FROM ai_run_fact WHERE ai_run_id = ? AND decision = 'PROPOSED'").bind(run_id).all()
    except Exception as e:
        return failed(request, "load_run_facts", e)
    if not run:
        return error(request, 404)
    open_facts: dict[str, Row] = {row["id"]: row for row in proposed["results"]}

    # Either one decision for every fact still proposed, or a list of decisions by fact id.
    everything: Any = body.get("all")
    listed: Any = body.get("decisions")
    decisions: dict[str, Any] = {}
    if everything is not None:
        decisions = {fact_id: everything for fact_id in open_facts}
    elif isinstance(listed, list):
        for item in cast(list[Any], listed):
            entry: Row = cast(Row, item) if isinstance(item, dict) else {}
            decisions[str(entry.get("id"))] = entry.get("decision")
    allowed_decisions = ("ACCEPTED", "REJECTED")
    if everything is None and not decisions:
        return error(request, 400)
    if (everything is not None and everything not in allowed_decisions) or any(
        decision not in allowed_decisions for decision in decisions.values()
    ):
        return error(request, 400)
    if any(fact_id not in open_facts for fact_id in decisions):
        return error(request, 409)  # not a fact of this run, or already decided

    try:
        statements: list[Any] = []
        if decisions:
            before = {open_facts[fact_id]["key"]: "PROPOSED" for fact_id in decisions}
            after = {open_facts[fact_id]["key"]: decision for fact_id, decision in decisions.items()}
            statements += audit(request, f"Proposed facts of the run by {run['model_label']}", run["company_id"], before, after)
        for fact_id, decision in decisions.items():
            fact = open_facts[fact_id]
            statements.append(database.prepare("UPDATE ai_run_fact SET decision = ? WHERE id = ?").bind(decision, fact_id))
            if decision == "ACCEPTED":
                # An accepted fact becomes one of the company's facts, and keeps where it came from.
                accepted = {
                    "id": str(uuid.uuid4()),
                    "company_id": run["company_id"],
                    "key": fact["key"],
                    "value": fact["value"],
                    "source": "AI",
                    "source_detail": fact["source_url"],
                    "notes": fact["notes"],
                    "ai_run_id": run_id,
                }
                statements.append(insert(database, "enrichment_fact", accepted))
        if statements:
            await database.batch(statements)
        sql = "SELECT id, key, value, source_url, notes, decision FROM ai_run_fact WHERE ai_run_id = ? ORDER BY key, id"
        facts = await database.prepare(sql).bind(run_id).all()
    except Exception as e:
        return failed(request, "decide_facts", e)
    return JSONResponse({"items": facts["results"]})


async def apply_scoring(request: Request, run: Row) -> tuple[Row, list[Row]]:
    """Make a finished scoring run's answers the company's answers, and store the scores that follow.
    Returns the scores and the services. Raises StepFailed. Written once: a person applies a run
    through apply_run, and the proposals job applies its own (specs/025-service-proposals/spec.md)."""
    database, run_id, company_id = db(request), run["id"], run["company_id"]
    for attempt in range(SCORE_TRIES):
        try:
            # One read, so the answers, the rubric, and the company's mark are of one moment.
            criteria, services, thresholds, (found, stored, given) = await read_for_scoring(
                database,
                database.prepare(f"{SCORED_COMPANY} WHERE id = ?").bind(company_id),
                database.prepare("SELECT criterion_id, value FROM rubric_answer WHERE company_id = ?").bind(company_id),
                database.prepare("SELECT criterion_id, value FROM ai_run_answer WHERE ai_run_id = ?").bind(run_id),
            )
            company = found[0]
        except Exception as e:
            raise StepFailed("load_run", e) from e

        by_id = {criterion["id"]: criterion for criterion in criteria}
        old: Row = {row["criterion_id"]: row["value"] for row in stored}
        new: Row = {}
        try:
            sql = (
                "INSERT INTO rubric_answer (company_id, criterion_id, value, points, source, ai_run_id, answered_by)"
                " VALUES (?, ?, ?, ?, 'AI', ?, ?)"
            )
            caller = request.state.identity["id"]
            inserts: list[Any] = []
            for row in given:
                # Points are computed again, in case the rubric changed since the run.
                earned = points(by_id[row["criterion_id"]], row["value"])
                if earned is not None:
                    new[row["criterion_id"]] = row["value"]
                    inserts.append(database.prepare(sql).bind(company_id, row["criterion_id"], row["value"], earned, run_id, caller))
            # An answer that changes or goes away is an edit, and is recorded first. A first answer is not.
            before = {by_id[key]["prompt"]: value for key, value in old.items() if new.get(key) != value}
            after = {by_id[key]["prompt"]: new.get(key) for key, value in old.items() if new.get(key) != value}
            scores = score(criteria, new, services, thresholds)
            statements = audit(request, f"Answers, replaced by the run by {run['model_label']}", company_id, before, after) if after else []
            statements.append(database.prepare("DELETE FROM rubric_answer WHERE company_id = ?").bind(company_id))
            statements += inserts
            statements += score_writes(request, company, scores)
            statements.append(database.prepare(f"UPDATE ai_run SET applied_at = {NOW} WHERE id = ?").bind(run_id))
            await database.batch(statements)
        except Exception as e:
            if attempt + 1 < SCORE_TRIES and await score_moved(database, company):
                # Another writer was first: read again, and apply the run to what is stored now.
                logger.info(json.dumps({"event": "carmen_scores_again", "step": "apply_run", "company_id": company_id}))
                continue
            raise StepFailed("apply_run", e) from e
        return scores, services
    raise StepFailed("apply_run", RuntimeError("the scores kept changing"))  # not reached: the last try raises


@app.post("/api/v1/runs/{run_id}/apply", response_model=contract.Scores)
@action(WRITE)
async def apply_run(request: Request, run_id: str) -> JSONResponse:
    try:
        run = await db(request).prepare(f"{RUN_SELECT} WHERE r.id = ?").bind(run_id).first()
    except Exception as e:
        return failed(request, "load_run", e)
    if not run:
        return error(request, 404)
    if run["kind"] != "SCORING" or run["status"] != "SUCCEEDED":
        return error(request, 409)  # only a finished scoring run can be applied
    try:
        scores, services = await apply_scoring(request, run)
    except StepFailed as e:
        return failed(request, e.step, e.cause)
    return JSONResponse(scores_body(scores, services))


# ---- CSV import ----------------------------------------------------------------------------------
# specs/013-csv-import/spec.md. import_batch holds only the counts, so the file with its mapping and
# the result (which rows were duplicates, which failed) are objects in the bucket, under imports/<id>/.
# The result object is also the sign that the import is finished.

IMPORT_CHUNK = 50  # rows stored in one transaction
DEFAULT_MAX_ROWS = 2000  # used only when the setting import.max_rows is missing
PREVIEW_ROWS = 10
# A company the import created that nobody has worked on since: undo may delete it.
UNTOUCHED = "import_batch_id = ?1 AND stage = 'LEAD' AND archived_at IS NULL AND updated_at = created_at" + "".join(
    f" AND NOT EXISTS (SELECT 1 FROM {table} WHERE company_id = company.id)"
    for table in ("activity", "task", "deal", "agreement", "document", "ai_run", "rubric_answer")
)

CsvRows = list[tuple[int, list[str]]]


def import_key(import_id: str, name: str) -> str:
    return f"imports/{import_id}/{name}.json"


def import_log(import_id: str, step: str, detail: str) -> None:
    logger.info(json.dumps({"event": "carmen_import", "import_id": import_id, "step": step, "detail": detail}))


async def put_json(store: Any, key: str, value: object) -> None:
    from pyodide.ffi import to_js

    # An ArrayBuffer, as for a document: bytes would be walked one at a time by the Workers Python layer.
    await store.put(key, to_js(json.dumps(value).encode()).buffer)


async def get_json(store: Any, key: str) -> Any:
    stored = await store.get(key)
    return json.loads(str(await stored.text())) if stored else None


async def read_csv(request: Request, body: dict[str, Any]) -> tuple[str, list[str], CsvRows] | JSONResponse:
    """The CSV text of a request with its headers and rows, or the response that refuses it."""
    text: object = body.get("csv")
    try:
        if not isinstance(text, str):
            raise BadBody("csv must be the text of the file")
        headers, rows = imports.parse(text)
    except BadBody as e:
        return failed(request, "read_csv", e)
    try:
        limit = await db(request).prepare("SELECT value FROM setting WHERE key = 'import.max_rows'").first()
    except Exception as e:
        return failed(request, "load_import_limit", e)
    most = int(limit["value"]) if limit and str(limit["value"]).isdigit() else DEFAULT_MAX_ROWS
    if len(rows) > most:
        return error(request, 413)
    return text, headers, rows


@app.post("/api/v1/imports/preview", response_model=contract.ImportPreview)
@action(WRITE)
@takes(contract.CsvBody)
async def preview_import(request: Request, body: dict[str, Any]) -> JSONResponse:
    read = await read_csv(request, body)
    if isinstance(read, JSONResponse):
        return read
    _, headers, rows = read
    return JSONResponse(
        {
            "headers": headers,
            "mapping": imports.suggest(headers),
            "targets": list(imports.TARGETS),
            "row_count": len(rows),
            "rows": [cells for _, cells in rows[:PREVIEW_ROWS]],
        }
    )


@app.post("/api/v1/imports", response_model=contract.Created, status_code=202)
@action(WRITE)
@takes(contract.ImportBody)
async def create_import(request: Request, body: dict[str, Any]) -> JSONResponse:
    read = await read_csv(request, body)
    if isinstance(read, JSONResponse):
        return read
    text, headers, rows = read
    file_name: object = body.get("file_name")
    try:
        mapping = imports.check_mapping(headers, body.get("mapping"))
        if file_name is not None and not isinstance(file_name, str):
            raise BadBody("file_name must be text")
    except BadBody as e:
        return failed(request, "read_mapping", e)

    import_id = str(uuid.uuid4())
    batch: Row = {
        "id": import_id,
        "source": "CSV",
        "file_name": file_name_of(file_name),
        "row_count": len(rows),
        "created_by": request.state.identity["id"],
    }
    store, key = bucket(request), import_key(import_id, "input")
    try:
        # The object first, then the row, as for a document.
        await put_json(store, key, {"csv": text, "mapping": mapping})
    except Exception as e:
        return failed(request, "store_import", e)
    try:
        await insert(db(request), "import_batch", batch).run()
    except Exception as e:
        log_error(request, "insert_import", e)
        try:
            await store.delete(key)
        except Exception as cleanup:
            log_error(request, "remove_orphan_import", cleanup)
        return error(request, db_status(e))

    try:
        await request.scope["env"].RUNS.send({"import_id": import_id})
    except Exception as e:
        log_error(request, "queue_import", e)
        try:
            # An import that never reached the queue would look as if it were running forever.
            failure: Row = {"status": "FAILED", "message": "The import could not be queued", "duplicates": [], "errors": []}
            await put_json(store, import_key(import_id, "result"), failure)
        except Exception as inner:
            log_error(request, "fail_unqueued_import", inner)
        return error(request, 500)
    return JSONResponse({"id": import_id}, status_code=202)


async def import_rows(database: Any, batch: Row, given: Row, result: Row) -> None:
    """Create the companies of one import, and put its duplicates and errors in result."""
    import_id: str = batch["id"]
    headers, rows = imports.parse(given["csv"])
    mapping: dict[str, str] = given["mapping"]
    known = await database.prepare("SELECT key, value_type FROM enrichment_key").all()
    types: dict[str, str] = {row["key"]: row["value_type"] for row in known["results"]}
    # A domain is taken by an unarchived company, as for a company made by hand, or by an earlier row.
    existing = await database.prepare("SELECT domain, id FROM company WHERE domain IS NOT NULL AND archived_at IS NULL").all()
    taken: dict[str, Row] = {row["domain"]: {"company_id": row["id"]} for row in existing["results"]}
    duplicates: list[Row] = result["duplicates"]
    errors: list[Row] = result["errors"]
    shared = await shared_hosts(database)

    ready: list[tuple[int, str | None, list[Any]]] = []
    for number, cells in rows:
        try:
            company, contact, facts = imports.row_values(headers, cells, mapping, types, shared)
        except BadBody as e:
            errors.append({"row": number, "error": str(e)})
            continue
        domain: str | None = company["domain"]
        if domain and domain in taken:
            duplicates.append({"row": number, "domain": domain, **taken[domain]})
            continue
        if domain:
            taken[domain] = {"of_row": number}
        company.update(id=str(uuid.uuid4()), source="CSV", import_batch_id=import_id)
        statements = [insert(database, "company", company)]
        if contact:
            contact.update(id=str(uuid.uuid4()), company_id=company["id"], is_primary=1)
            statements.append(insert(database, "contact", contact))
        for key, value in facts:
            fact = {"id": str(uuid.uuid4()), "company_id": company["id"], "key": key, "value": value}
            fact.update(source="CSV", source_detail=batch["file_name"])
            statements.append(insert(database, "enrichment_fact", fact))
        ready.append((number, domain, statements))

    created = 0
    counts = "UPDATE import_batch SET created_count = ?, duplicate_count = ?, error_count = ? WHERE id = ?"
    for start in range(0, len(ready), IMPORT_CHUNK):
        chunk = ready[start : start + IMPORT_CHUNK]
        try:
            await database.batch([statement for _, _, statements in chunk for statement in statements])
            created += len(chunk)
        except Exception as e:
            import_log(import_id, "chunk_refused", f"{type(e).__name__}: {e}")
            # shortcut: a refused chunk is stored one row at a time, and an invocation may make 1,000 queries; halve refused chunks instead if imports grow past a few thousand rows
            for number, domain, statements in chunk:
                try:
                    await database.batch(statements)
                    created += 1
                except Exception as inner:
                    import_log(import_id, "row_refused", f"row {number}: {type(inner).__name__}: {inner}")
                    if db_status(inner) == 409:  # someone added the company while the import ran
                        duplicates.append({"row": number, "domain": domain})
                    else:
                        errors.append({"row": number, "error": "The database refused the row"})
        # After every chunk, so that the counts show progress while the import runs.
        await database.prepare(counts).bind(created, len(duplicates), len(errors), import_id).run()
    if not ready:
        await database.prepare(counts).bind(0, len(duplicates), len(errors), import_id).run()
    duplicates.sort(key=lambda entry: entry["row"])
    errors.sort(key=lambda entry: entry["row"])
    import_log(import_id, "finished", f"created {created}, duplicates {len(duplicates)}, errors {len(errors)}")


async def process_import(env: Any, import_id: str) -> None:
    """Carry out one queued import. Never raises: a failure is recorded in the result."""
    database, store = env.DB, env.DOCS
    result: Row = {"status": "DONE", "message": None, "duplicates": [], "errors": []}
    try:
        batch = await database.prepare("SELECT * FROM import_batch WHERE id = ?").bind(import_id).first()
        if not batch or await get_json(store, import_key(import_id, "result")):
            return import_log(import_id, "skipped", "no such import, or it is finished")
        begun = await database.prepare("SELECT COUNT(*) AS n FROM company WHERE import_batch_id = ?").bind(import_id).first()
        if begun["n"]:
            # Delivered a second time: the first attempt was cut off. Its companies stay, and undo removes them.
            result.update(status="FAILED", message="The import was interrupted and did not finish")
        else:
            await import_rows(database, batch, await get_json(store, import_key(import_id, "input")), result)
    except Exception as e:
        import_log(import_id, "failed", f"{type(e).__name__}: {e}")
        result.update(status="FAILED", message="The import failed before it finished")
    try:
        await put_json(store, import_key(import_id, "result"), result)
    except Exception as e:
        import_log(import_id, "result_not_stored", f"{type(e).__name__}: {e}")


@app.get("/api/v1/imports/{import_id}", response_model=contract.ImportStatus)
@action(READ)
async def get_import(request: Request, import_id: str) -> JSONResponse:
    try:
        batch = await db(request).prepare("SELECT * FROM import_batch WHERE id = ?").bind(import_id).first()
    except Exception as e:
        return failed(request, "load_import", e)
    if not batch:
        return error(request, 404)
    try:
        result = await get_json(bucket(request), import_key(import_id, "result"))
    except Exception as e:
        return failed(request, "load_import_result", e)
    return JSONResponse({**batch, **(result or {"status": "RUNNING", "message": None, "duplicates": [], "errors": []})})


# WRITE, not DELETE: a MEMBER may undo an import they made. may_delete decides.
@app.delete("/api/v1/imports/{import_id}", response_model=contract.ImportUndone)
@action(WRITE)
async def undo_import(request: Request, import_id: str) -> JSONResponse:
    database = db(request)
    try:
        batch = await database.prepare("SELECT * FROM import_batch WHERE id = ?").bind(import_id).first()
        finished = batch and await get_json(bucket(request), import_key(import_id, "result"))
    except Exception as e:
        return failed(request, "load_import", e)
    if not batch:
        return error(request, 404)
    if not may_delete(request, batch["created_by"]):
        return error(request, 403)
    if not finished:
        return error(request, 409)  # still running

    try:
        found = await database.prepare(f"SELECT * FROM company WHERE {UNTOUCHED}").bind(import_id).all()
        rows: list[Row] = found["results"]
        for row in rows:
            # The timeline is deleted with the company, so the Worker log is the only record of this delete.
            log_change(request, describe_change(company_label(row), held(row, COMPANY_FIELDS), None), row["id"])
        outcome = await database.batch(
            [
                database.prepare(f"DELETE FROM company WHERE {UNTOUCHED}").bind(import_id),
                database.prepare("SELECT COUNT(*) AS n FROM company WHERE import_batch_id = ?").bind(import_id),
            ]
        )
        kept = outcome[1]["results"][0]["n"]
    except Exception as e:
        return failed(request, "undo_import", e)
    return JSONResponse({"deleted": len(rows), "kept": kept})


# ---- Machine methods -----------------------------------------------------------------------------
# specs/014-machine-methods/spec.md. Another Andexor service sends a lead, or facts about a company.
# create_lead and add_facts are written once, here, with no request. Each has two doors onto it: a
# method on the Worker entry point (src/worker.py) for a Worker that calls over a Service Binding,
# and an endpoint for a caller with a Cloudflare Access service token.

LEAD_FIELDS: dict[str, type] = dict.fromkeys(("source", "name", "website", "source_ref", "note"), str)
LEAD_CONTACT_FIELDS: dict[str, type] = {field: kind for field, kind in CONTACT_FIELDS.items() if field != "is_primary"}


class StepFailed(Exception):
    """A database step of a machine method failed. The door that was used logs it and answers."""

    def __init__(self, step: str, cause: Exception) -> None:
        super().__init__(step)
        self.step, self.cause = step, cause


async def fact_types(database: Any, source: str) -> tuple[dict[str, str], str]:
    """The value type of each well-known fact key, and the fact source for a lead source: the same
    code when facts may carry it, and API otherwise."""
    try:
        known = await database.prepare("SELECT key, value_type FROM enrichment_key").all()
        sql = "SELECT 1 AS found FROM srt WHERE domain = 'ENRICHMENT_SOURCE' AND code = ? AND active = 1"
        same = await database.prepare(sql).bind(source).first()
    except Exception as e:
        raise StepFailed("load_fact_keys", e) from e
    return {row["key"]: row["value_type"] for row in known["results"]}, source if same else "API"


async def find_lead(database: Any, domain: str | None, source: str, source_ref: str | None) -> Row | None:
    """The company a lead belongs to: the unarchived one with its domain, or else the one this source sent before."""
    try:
        company: Row | None = None
        if domain:
            sql = "SELECT id FROM company WHERE domain = ? AND archived_at IS NULL"
            company = await database.prepare(sql).bind(domain).first()
        if not company and source_ref:
            sql = "SELECT id FROM company WHERE source = ? AND source_ref = ? ORDER BY archived_at IS NOT NULL, created_at LIMIT 1"
            company = await database.prepare(sql).bind(source, source_ref).first()
    except Exception as e:
        raise StepFailed("find_lead", e) from e
    return company or None


async def create_lead(database: Any, body: Row) -> Row:
    """Create a company from a lead, or add the lead's contact, note, and facts to the company it matches.
    Raises BadBody for a lead that cannot be read, and StepFailed when the database fails."""
    lead = clean(body, LEAD_FIELDS)
    given: object = body.get("contact")
    if given is not None and not isinstance(given, dict):
        raise BadBody("contact must be an object")
    contact = clean(cast(Row, given or {}), LEAD_CONTACT_FIELDS)
    source: str | None = lead.get("source")
    if not source:
        raise BadBody("source is required")
    if contact and not (contact.get("name") or contact.get("email")):
        raise BadBody("a contact needs a name or an email")
    # A form may give only a person. Then the company is named after them until someone learns more.
    name: str | None = lead.get("name") or (None if lead.get("website") else contact.get("name") or contact.get("email"))
    if not (name or lead.get("website")):
        raise BadBody("a lead needs a name, a website, or a contact")
    domain = derive_domain(lead.get("website"), await shared_hosts(database))
    types, fact_source = await fact_types(database, source)

    for attempt in (1, 2):
        company = await find_lead(database, domain, source, lead.get("source_ref"))
        company_id: str = company["id"] if company else str(uuid.uuid4())
        statements: list[Any] = []
        known: list[Row] = []
        if company:
            try:
                found = await database.prepare("SELECT name, email, is_primary FROM contact WHERE company_id = ?").bind(company_id).all()
                known = found["results"]
            except Exception as e:
                raise StepFailed("load_contacts", e) from e
        else:
            values = {"id": company_id, "name": name, "website": lead.get("website"), "domain": domain}
            statements.append(insert(database, "company", {**values, "source": source, "source_ref": lead.get("source_ref")}))

        # The same person is not added twice: matched on the email, or on the name when there is no email.
        who = "email" if contact.get("email") else "name"
        contact_id: str | None = None
        if contact and not any((row[who] or "").lower() == contact[who].lower() for row in known):
            contact_id = str(uuid.uuid4())
            primary = 0 if any(row["is_primary"] for row in known) else 1
            statements.append(insert(database, "contact", {**contact, "id": contact_id, "company_id": company_id, "is_primary": primary}))
        if lead.get("note"):
            entry = {"id": str(uuid.uuid4()), "company_id": company_id, "contact_id": contact_id, "kind": "NOTE", "body": lead["note"]}
            statements.append(insert(database, "activity", entry))
        if body.get("facts"):
            statements += fact_inserts(database, company_id, body["facts"], fact_source, types)

        try:
            if statements:
                await database.batch(statements)  # all of it or none: one transaction
            return {"company_id": company_id, "created": not company}
        except Exception as e:
            if not company and attempt == 1 and db_status(e) == 409:
                continue  # another call made the company with this domain first: match it, and add to it
            raise StepFailed("store_lead", e) from e
    raise StepFailed("store_lead", RuntimeError("the lead matched no company and could not be created"))


async def add_facts(database: Any, body: Row) -> Row:
    """Add facts to the company with an id, or to the unarchived company with a domain.
    Raises BadBody, LookupError for no such company, and StepFailed when the database fails."""
    wanted = clean(body, dict.fromkeys(("company_id", "domain", "source"), str))
    if not wanted.get("source"):
        raise BadBody("source is required")
    try:
        if wanted.get("company_id"):
            company = await database.prepare("SELECT id FROM company WHERE id = ?").bind(wanted["company_id"]).first()
        elif derive_domain(wanted.get("domain")):
            sql = "SELECT id FROM company WHERE domain = ? AND archived_at IS NULL"
            company = await database.prepare(sql).bind(derive_domain(wanted["domain"])).first()
        else:
            raise BadBody("company_id or domain is required")
    except BadBody:
        raise
    except Exception as e:
        raise StepFailed("find_company", e) from e
    if not company:
        raise LookupError("no such company")
    types, _ = await fact_types(database, wanted["source"])
    statements = fact_inserts(database, company["id"], body.get("facts"), wanted["source"], types)
    try:
        await database.batch(statements)
    except Exception as e:
        raise StepFailed("store_facts", e) from e
    return {"added": len(statements)}


MachineMethod = Callable[[Any, Row], Awaitable[Row]]


async def machine_call(env: Any, method: MachineMethod, given: Any) -> Row:
    """The Service Binding door. It never raises: an error that crosses a Service Binding arrives with a
    whole traceback (measured 2026-11-24), so a refusal is an answer instead: ok false, an error code
    as on the endpoints, and a message that is safe to show."""

    def refused(code: str, message: str) -> Row:
        return {"ok": False, "error": code, "message": message}

    try:
        body: Any = given.to_py() if hasattr(given, "to_py") else given  # a JavaScript object, or already a dict
        if not isinstance(body, dict):
            return refused("BAD_REQUEST", "The argument must be an object")
        return {"ok": True, **(await method(env.DB, cast(Row, body)))}
    except BadBody as e:
        return refused("BAD_REQUEST", str(e))
    except LookupError:
        return refused("NOT_FOUND", "No such company")
    except Exception as e:
        cause = e.cause if isinstance(e, StepFailed) else e
        step = e.step if isinstance(e, StepFailed) else "machine_call"
        logger.error(json.dumps({"event": "carmen_error", "step": step, "error": type(cause).__name__, "description": str(cause)}))
        if db_status(cause) == 400:
            return refused("BAD_REQUEST", "The database refused a value, such as a code it does not know")
        return refused("INTERNAL", "Carmen could not store it")


async def machine_answer(request: Request, method: MachineMethod, body: Row) -> JSONResponse:
    """The endpoint door."""
    try:
        result = await method(db(request), body)
    except BadBody as e:
        return failed(request, "read_machine_call", e)
    except LookupError:
        return error(request, 404)
    except StepFailed as e:
        return failed(request, e.step, e.cause)
    return JSONResponse(result, status_code=201 if result.get("created") else 200)


@app.post("/api/v1/machine/leads", response_model=contract.LeadResult, responses={201: {"model": contract.LeadResult, "description": "A new company was made"}})
@action(MACHINE)
@takes(contract.LeadBody)
async def machine_lead(request: Request, body: dict[str, Any]) -> JSONResponse:
    return await machine_answer(request, create_lead, body)


@app.post("/api/v1/machine/facts", response_model=contract.FactsAdded)
@action(MACHINE)
@takes(contract.MachineFactsBody)
async def machine_facts(request: Request, body: dict[str, Any]) -> JSONResponse:
    return await machine_answer(request, add_facts, body)


# ---- Scheduler and notifications -----------------------------------------------------------------
# Run every hour by the Cron Trigger (src/worker.py), with no request and no caller: env is used
# directly, and nothing here may raise. See specs/015-scheduler-and-notifications/spec.md.

DIGEST_ROWS = 50  # the most rows of one list in a digest, and the most alerts of one run
# An alert made up to a day before the notice date counts: the stored moment is UTC, the date is local.
ALERTS_DUE = (
    f"SELECT a.*, c.name AS company_name, {NOTICE_DATE} AS notice_date FROM agreement a"
    " JOIN company c ON c.id = a.company_id WHERE a.status = 'ACTIVE' AND a.end_date >= ?"
    f" AND {NOTICE_DATE} <= ? AND NOT EXISTS (SELECT 1 FROM notification n WHERE n.kind = 'RENEWAL_ALERT'"
    f" AND n.agreement_id = a.id AND n.created_at >= date({NOTICE_DATE}, '-1 day'))"
    " ORDER BY a.end_date, a.id LIMIT ?"
)


def scheduler_log(event: str, detail: str) -> None:
    logger.info(json.dumps({"event": f"carmen_scheduler_{event}", "detail": detail[:300]}))


async def notification(
    env: Any,
    provider: notify.Provider,
    kind: str,
    recipient: str,
    subject: str,
    body: str,
    agreement_id: str | None = None,
    since: str | None = None,
    task: Values | None = None,
) -> Row | None:
    """Record one message, send it, and record what happened (specs/037-record-email-first/spec.md).
    Returns the row as it is stored, or None when nothing was sent: with since, a message of the same
    kind and agreement that was recorded at or after that moment means another run has this one.

    The record comes first, as QUEUED, so a message is never sent twice because its record could not
    be stored: with no record nothing is sent, and the caller's step fails and is tried again. A task
    is stored with the record, or not at all. A message that could not be sent is FAILED with the
    reason, and is not tried again. One that stays QUEUED was cut off: the next digest names it.
    """
    # The preview marks its subjects, so its messages are told from production's (specs/029-email-subject-prefix/spec.md).
    subject = notify.subject_line(env_var(env, "EMAIL_SUBJECT_PREFIX"), subject)
    database, row_id = env.DB, str(uuid.uuid4())
    if not mailbox(recipient)[0]:
        # Sent all the same, to the bare address (specs/028-email-names/spec.md, FR-005).
        scheduler_log("no_recipient_name", f"{kind}: the recipient has no name, so the To line shows the address alone")
    # One statement decides and records, so of two runs at the same moment only one sends.
    sql = (
        "INSERT INTO notification (id, kind, recipient, subject, body, agreement_id, status)"
        " SELECT ?, ?, ?, ?, ?, ?, 'QUEUED' WHERE ? IS NULL OR NOT EXISTS"
        " (SELECT 1 FROM notification WHERE kind = ? AND agreement_id IS ? AND created_at >= ?) RETURNING *"
    )
    statements = [database.prepare(sql).bind(row_id, kind, recipient, subject, body, agreement_id, since, kind, agreement_id, since)]
    if task:
        given = {column: value for column, value in task.items() if value is not None}
        marks = ", ".join("?" * len(given))
        also = f"INSERT INTO task ({', '.join(given)}) SELECT {marks} WHERE EXISTS (SELECT 1 FROM notification WHERE id = ?)"
        statements.append(database.prepare(also).bind(*given.values(), row_id))
    recorded = (await database.batch(statements))[0]["results"]
    if not recorded:
        scheduler_log("already_recorded", f"{kind}: another run recorded this message, so it is not sent again")
        return None

    outcome: Values = {"status": provider.status}
    try:
        outcome["provider_ref"] = await provider.send(env, recipient, subject, body)
        if provider.status == "SENT":
            outcome["sent_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as e:
        scheduler_log("send_failed", f"{kind}: {type(e).__name__}: {e}")
        outcome = {"status": "FAILED", "error": f"{type(e).__name__}: {e}"[:300]}
    try:
        return await update(database, "notification", outcome, row_id, stamp=False).first()
    except Exception as e:
        # The message is out, or it failed, and the row still says QUEUED. It is not sent again.
        scheduler_log("record_failed", f"{kind}: the result {outcome['status']} could not be stored: {type(e).__name__}: {e}")
        return recorded[0]


# A message that was recorded more than an hour ago and never got its result: the run was cut off
# between the record and the send, or after the send. Each one is named in one digest, the first
# that is made after it: the list starts an hour before the digest before this one.
STAMP = "'%Y-%m-%dT%H:%M:%SZ'"
UNSENT = (
    "SELECT kind, subject, created_at FROM notification WHERE status = 'QUEUED'"
    f" AND created_at <= strftime({STAMP}, 'now', '-1 hour') AND created_at > coalesce((SELECT strftime({STAMP}, max(created_at), '-1 hour')"
    " FROM notification WHERE kind = 'DAILY_DIGEST'), '') ORDER BY created_at, id LIMIT ?"
)


# What an active agreement becomes after its end date, by its renewal policy. One that renews
# automatically has no end date, so it is not here.
ENDED_STATUS = {"ONE_TIME": "COMPLETED", "APPROVAL": "EXPIRED"}
# In an alert and its task: what the alert is about, and what will happen at the end date.
ALERT_WORDS = {
    "ONE_TIME": ("Ending", "It is a one-time agreement: after that date it is marked completed."),
    "APPROVAL": ("Renewal", "Its renewal needs approval: without it, the agreement expires after that date."),
}


async def end_agreements(env: Any, date: str) -> dict[str, int]:
    """An active agreement past its end date becomes what its renewal policy says: COMPLETED or EXPIRED.
    Returns how many of each. A completed one gets its proposals job (specs/025-service-proposals/spec.md)."""
    database = env.DB
    sql = "SELECT * FROM agreement WHERE status = 'ACTIVE' AND end_date < ? AND renewal_policy IN ('ONE_TIME', 'APPROVAL') LIMIT ?"
    found = await database.prepare(sql).bind(date, DIGEST_ROWS).all()
    statements: list[Any] = []
    counts = {"COMPLETED": 0, "EXPIRED": 0}
    for agreement in found["results"]:
        status = ENDED_STATUS[agreement["renewal_policy"]]
        counts[status] += 1
        change = describe_change(agreement_label(agreement), {"status": "ACTIVE"}, {"status": status})
        change = f"{change}. It ended on {agreement['end_date']}"
        # The record of the change: the Worker log first, then the timeline, in one transaction with the change.
        record = {"event": "carmen_audit", "change": change, "company_id": agreement["company_id"], "by": "scheduler"}
        logger.info(json.dumps(record))
        entry = {"id": str(uuid.uuid4()), "company_id": agreement["company_id"], "kind": "SYSTEM", "body": change}
        statements += [insert(database, "activity", entry), update(database, "agreement", {"status": status}, agreement["id"])]
    if statements:
        await database.batch(statements)
    for agreement in found["results"]:
        if ENDED_STATUS[agreement["renewal_policy"]] == "COMPLETED":
            await queue_proposals(env, agreement["id"])  # logs a failure itself; the agreement stays completed
    return counts


async def renewal_alerts(env: Any, provider: notify.Provider, date: str, notice_days: int, recipient: str) -> int:
    """One alert and one task for each active agreement that has reached its notice date. An agreement
    that renews automatically has no end date, so it gets none. Returns how many alerts this run made."""
    found = await env.DB.prepare(ALERTS_DUE).bind(notice_days, date, notice_days, date, notice_days, DIGEST_ROWS).all()
    made = 0
    for agreement in found["results"]:
        title = str(agreement["title"] or agreement["type"])
        heading, what_happens = ALERT_WORDS.get(agreement["renewal_policy"], ALERT_WORDS["APPROVAL"])
        subject, body = notify.render(
            "renewal",
            heading=heading,
            title=title,
            company=str(agreement["company_name"]),
            end_date=str(agreement["end_date"]),
            notice_date=str(agreement["notice_date"]),
            what_happens=what_happens,
        )
        task = {
            "id": str(uuid.uuid4()),
            "title": f"{heading}: {title}",
            "company_id": agreement["company_id"],
            "agreement_id": agreement["id"],
            "due_date": agreement["notice_date"],
            "owner_id": agreement["owner_id"],
        }
        # The day before the notice date, as in ALERTS_DUE: a run that found the agreement at the same moment makes no second alert.
        since = (datetime.fromisoformat(str(agreement["notice_date"])) - timedelta(days=1)).date().isoformat()
        if await notification(env, provider, "RENEWAL_ALERT", recipient, subject, body, agreement["id"], since, task):
            made += 1
    return made


async def daily_digest(
    env: Any, provider: notify.Provider, settings: dict[str, str], date: str, notice_days: int, recipient: str, now: bool
) -> str:
    """Send today's digest when its day and hour have come and none was made today. Says what was done.
    With now, the day, the hour, and an earlier digest do not matter: a person asked for it."""
    database = env.DB
    due, began = notify.digest_window(
        settings.get("digest.time_zone"), settings.get("digest.hour_local"), settings.get("digest.days")
    )
    if not now:
        if not due:
            return "NOT_DUE"
        sql = "SELECT 1 AS found FROM notification WHERE kind = 'DAILY_DIGEST' AND created_at >= ?"
        if await database.prepare(sql).bind(began).first():
            return "ALREADY_MADE"
    names = ("tasks_due", "tasks_overdue", "agreements_renewing", "leads_unscored", "unsent")
    results = await database.batch(
        [
            database.prepare(TODAY_TASKS.format("=")).bind(date, DIGEST_ROWS),
            database.prepare(TODAY_TASKS.format("<")).bind(date, DIGEST_ROWS),
            database.prepare(TODAY_RENEWING).bind(notice_days, notice_days, date, DIGEST_ROWS),
            database.prepare(TODAY_UNSCORED).bind(DIGEST_ROWS),
            database.prepare(UNSENT).bind(DIGEST_ROWS),
        ]
    )
    # A day with nothing to report still gets its digest, which says so (specs/023-weekday-digest/spec.md).
    lists: dict[str, list[Row]] = {name: result["results"] for name, result in zip(names, results, strict=True)}
    if settings.get(RESCORE_AUTO) == "OFF" and settings.get(RESCORE):
        lists["rescoring_off"] = [{"requested": settings[RESCORE]}]  # so the switch is not left OFF unnoticed
    sections = notify.digest_sections(lists)
    sections = sections or notify.NOTHING
    subject, body = notify.render("digest", date=date, sections=sections)
    # Another run at the same moment may have made today's digest since the check above.
    stored = await notification(env, provider, "DAILY_DIGEST", recipient, subject, body, since=None if now else began)
    return str(stored["status"]) if stored else "ALREADY_MADE"


async def run_scheduler(env: Any, email: bool = False) -> Row:
    """One run of the scheduler. Never raises: a step that fails is logged and named in the answer,
    and the other steps still run.

    email is for a person who wants to see the messages: this run sends real email whatever the
    variable EMAIL_PROVIDER says, and makes the digest now. The Cron Trigger never sets it.
    """
    provider = notify.PROVIDERS["CLOUDFLARE"] if email else notify.provider(getattr(env, "EMAIL_PROVIDER", None))
    summary: Row = {"completed": 0, "expired": 0, "uptime": 0, "rescored": 0, "alerts": 0, "digest": None, "failed": []}
    sql = (
        "SELECT key, value FROM setting WHERE key LIKE 'digest.%' OR key = 'renewal.default_notice_days'"
        f" OR key LIKE 'uptime.%' OR key IN ('{RESCORE}', '{RESCORE_AUTO}')"
    )
    try:
        found = await env.DB.prepare(sql).all()
        settings: dict[str, str] = {row["key"]: row["value"] for row in found["results"]}
    except Exception as e:
        scheduler_log("failed", f"load_settings: {type(e).__name__}: {e}")
        return {**summary, "failed": ["load_settings"]}
    notice = settings.get("renewal.default_notice_days", "")
    notice_days = int(notice) if notice.isdigit() else 30
    date, _ = local_date(settings.get("digest.time_zone"))
    recipient = settings.get("digest.recipient", "")

    try:
        ended = await end_agreements(env, date)
        summary.update(completed=ended["COMPLETED"], expired=ended["EXPIRED"])
    except Exception as e:
        scheduler_log("failed", f"end_agreements: {type(e).__name__}: {e}")
        summary["failed"].append("end_agreements")
    try:
        summary["uptime"] = await uptime_checks(env, settings)
    except Exception as e:
        scheduler_log("failed", f"uptime_checks: {type(e).__name__}: {e}")
        summary["failed"].append("uptime_checks")
    try:
        # A rescoring after a change failed, or a person asked for one. Not while the switch is OFF.
        if settings.get(RESCORE) and settings.get(RESCORE_AUTO) != "OFF":
            summary["rescored"] = await rescore_all(job_request(env, "scheduler"))
    except Exception as e:
        scheduler_log("failed", f"rescore: {type(e).__name__}: {e}")
        summary["failed"].append("rescore")
    if not recipient:
        scheduler_log("failed", "the setting digest.recipient is missing, so nothing is sent")
        summary["failed"].append("recipient")
        return summary
    try:
        summary["alerts"] = await renewal_alerts(env, provider, date, notice_days, recipient)
    except Exception as e:
        scheduler_log("failed", f"renewal_alerts: {type(e).__name__}: {e}")
        summary["failed"].append("renewal_alerts")
    try:
        summary["digest"] = await daily_digest(env, provider, settings, date, notice_days, recipient, email)
    except Exception as e:
        scheduler_log("failed", f"daily_digest: {type(e).__name__}: {e}")
        summary["failed"].append("daily_digest")
    scheduler_log("finished", json.dumps(summary))
    return summary


# For an administrator, and for the smoke test: the same run the Cron Trigger makes, now.
# With email=true the run sends real email in any environment, and makes the digest now. The smoke test never sets it.
@app.post("/api/v1/scheduler/run", response_model=contract.SchedulerRun)
@action(MANAGE)
async def run_scheduler_now(request: Request, email: bool = False) -> JSONResponse:
    return JSONResponse(await run_scheduler(request.scope["env"], email))


@app.get("/api/v1/notifications", response_model=Page[models.Notification])
@action(READ)
async def list_notifications(
    request: Request,
    kind: str | None = None,
    agreement_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> JSONResponse:
    where = ["1 = 1"]
    params: list[Any] = []
    for condition, value in {"kind = ?": kind, "agreement_id = ?": agreement_id}.items():
        if value is not None:
            where.append(condition)
            params.append(value)
    sql = f"SELECT * FROM notification WHERE {' AND '.join(where)} ORDER BY created_at DESC, id LIMIT ? OFFSET ?"
    try:
        result = await db(request).prepare(sql).bind(*params, limit + 1, cursor).all()
    except Exception as e:
        return failed(request, "list_notifications", e)
    return page(result, limit, cursor)


# ---- Uptime checker ------------------------------------------------------------------------------
# specs/026-uptime-checker/spec.md. The scheduler checks the website of each lead and prospect once
# a day until the company has uptime.window_days checks, and keeps the share of successful checks
# as the fact uptime_percent. A scoring run answers "Is the site reliably up?" from that fact.

# The settings of the checker, each with the value that is used when the setting is missing.
UPTIME_SETTINGS = {
    "uptime.window_days": 14,  # checks a company gets, one a day
    "uptime.sites_per_run": 25,  # sites checked in one run of the scheduler; the rest wait for the next hour
    "uptime.hours_between_checks": 23,  # hours before a site is checked again
}
# The due companies are a queue, and the query cannot tell whether a website can be checked: only
# pages.site_url can ("localhost" or an address in numbers cannot). So one run reads UPTIME_SCAN
# times as many companies as it checks, drops the ones whose website cannot be checked, and checks
# the first sites_per_run of the rest. With 25 sites per run it reads 50.
# shortcut: a company whose website cannot be checked stays at the front of the queue, so when one run reads more than sites_per_run of them, fewer sites are checked per run; leave them out in the query then
UPTIME_SCAN = 2
UPTIME_COUNTS = (
    "SELECT c.id, c.website, c.stage, c.archived_at, COUNT(u.id) AS checks, COALESCE(SUM(u.up), 0) AS ups,"
    " (SELECT f.value FROM enrichment_fact f WHERE f.company_id = c.id AND f.key = 'uptime_percent'"
    " ORDER BY f.observed_at DESC, f.created_at DESC LIMIT 1) AS stored"
    " FROM company c LEFT JOIN uptime_check u ON u.company_id = c.id"
)
# Due: a lead or a prospect whose days are not full and whose last check is about a day old. A
# company that was never checked comes first, the newest first.
UPTIME_DUE = (
    f"{UPTIME_COUNTS} WHERE c.stage IN ('LEAD', 'PROSPECT') AND c.archived_at IS NULL AND c.website IS NOT NULL"
    " GROUP BY c.id HAVING COUNT(u.id) < ?"
    " AND (MAX(u.checked_at) IS NULL OR MAX(u.checked_at) < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?))"
    " ORDER BY MAX(u.checked_at) IS NOT NULL, MAX(u.checked_at), c.created_at DESC LIMIT ?"
)


def uptime_setting(settings: dict[str, str], key: str) -> int:
    """A setting of the checker: the stored value first, and the fallback when it is missing or not a whole number of at least 1."""
    wanted = settings.get(key, "")
    return int(wanted) if wanted.isdecimal() and int(wanted) > 0 else UPTIME_SETTINGS[key]


def uptime_writes(database: Any, company: Row, check: Row) -> list[Any]:
    """The statements that store one check of a company, and the fact uptime_percent when the share
    of successful checks changed. company holds its earlier checks, ups, and the stored fact."""
    checks, ups = int(company["checks"]) + 1, int(company["ups"]) + int(check["up"])
    value = f"{pages.percent_up(ups, checks):g}"
    statements = [insert(database, "uptime_check", {"id": str(uuid.uuid4()), "company_id": company["id"], **check})]
    if str(company["stored"]) != value:
        fact: Values = {
            "id": str(uuid.uuid4()),
            "company_id": company["id"],
            "key": "uptime_percent",
            "value": value,
            "source": "UPTIME_CHECK",
            "source_detail": "Carmen uptime checker",
            "notes": f"{ups} of {checks} daily checks succeeded",
        }
        statements.append(insert(database, "enrichment_fact", fact))
    return statements


async def uptime_checks(env: Any, settings: dict[str, str]) -> int:
    """The scheduler's step: check every site that is due, and store the checks in one batch.
    Returns how many sites were checked."""
    database = env.DB
    days, sites = uptime_setting(settings, "uptime.window_days"), uptime_setting(settings, "uptime.sites_per_run")
    wait = f"-{uptime_setting(settings, 'uptime.hours_between_checks')} hours"
    found = await database.prepare(UPTIME_DUE).bind(days, wait, sites * UPTIME_SCAN).all()
    due = [(company, url) for company in found["results"] if (url := pages.site_url(company["website"]))][:sites]
    if not due:
        return 0
    checks = await asyncio.gather(*(pages.check_site(url) for _, url in due))  # check_site never raises
    statements: list[Any] = []
    for (company, _), check in zip(due, checks):
        statements += uptime_writes(database, company, check)
    await database.batch(statements)
    return len(due)


async def uptime_company(request: Request, company_id: str) -> Row | None:
    found = await db(request).prepare(f"{UPTIME_COUNTS} WHERE c.id = ? GROUP BY c.id").bind(company_id).first()
    return found or None


def may_measure(company: Row) -> bool:
    return company["stage"] in ("LEAD", "PROSPECT") and not company["archived_at"] and pages.site_url(company["website"]) is not None


@app.get("/api/v1/companies/{company_id}/uptime", response_model=contract.Uptime)
@action(READ)
async def read_uptime(request: Request, company_id: str) -> JSONResponse:
    database = db(request)
    try:
        company = await uptime_company(request, company_id)
        if not company:
            return error(request, 404)
        setting, checks = await database.batch(
            [
                database.prepare("SELECT value FROM setting WHERE key = 'uptime.window_days'"),
                database.prepare("SELECT * FROM uptime_check WHERE company_id = ? ORDER BY checked_at DESC, id").bind(company_id),
            ]
        )
    except Exception as e:
        return failed(request, "read_uptime", e)
    days = uptime_setting({"uptime.window_days": row["value"] for row in setting["results"]}, "uptime.window_days")
    count, ups = int(company["checks"]), int(company["ups"])
    answer: Row = {
        "percent": pages.percent_up(ups, count) if count else None,
        "checks": count,
        "up": ups,
        "window_days": days,
        "measuring": may_measure(company) and count < days,  # the scheduler will check this site again
        "items": checks["results"],
    }
    return JSONResponse(answer)


# A new measurement: the earlier checks are deleted, the first check is made now, and the scheduler
# makes the others, one a day. For a person who wants the answer measured again.
@app.post("/api/v1/companies/{company_id}/uptime", response_model=contract.Uptime)
@action(WRITE)
async def start_uptime(request: Request, company_id: str) -> JSONResponse:
    database = db(request)
    try:
        company = await uptime_company(request, company_id)
    except Exception as e:
        return failed(request, "load_company", e)
    if not company:
        return error(request, 404)
    if not may_measure(company):
        return error(request, 409)  # not a lead or a prospect, archived, or no public website
    check = await pages.check_site(str(pages.site_url(company["website"])))  # never raises
    try:
        statements: list[Any] = []
        if company["checks"]:
            before: Row = {"checks": company["checks"], "uptime_percent": company["stored"]}
            statements += audit(request, "Uptime checks", company_id, before, None)
            statements.append(database.prepare("DELETE FROM uptime_check WHERE company_id = ?").bind(company_id))
        statements += uptime_writes(database, {**company, "checks": 0, "ups": 0}, check)
        await database.batch(statements)
    except Exception as e:
        return failed(request, "start_uptime", e)
    return await read_uptime(request, company_id)


# ---- Service proposals ---------------------------------------------------------------------------
# specs/025-service-proposals/spec.md. The list is read from the stored scores, for the Company
# screen at any time. The job, run from the queue when a one-time agreement is completed, first makes
# those scores fresh and then sends the list as an email.

# A service is worth proposing from the Need score below which a company is skipped.
PROPOSAL_MIN_NEED = "COALESCE((SELECT CAST(value AS INTEGER) FROM setting WHERE key = 'score.skip.max_need'), 30)"
# Offered services the company does not already have, the best match first. ?2 is an agreement whose
# services are left out too: the one that was just completed, in the email.
PROPOSED_SERVICES = (
    "SELECT s.id AS service_id, s.name, s.category, cs.need_score, cs.confidence FROM company_service cs"
    " JOIN service s ON s.id = cs.service_id WHERE cs.company_id = ?1 AND s.offered = 1 AND s.active = 1"
    f" AND cs.need_score >= {PROPOSAL_MIN_NEED}"
    " AND NOT EXISTS (SELECT 1 FROM agreement_service x JOIN agreement a ON a.id = x.agreement_id"
    " WHERE x.service_id = s.id AND a.company_id = ?1 AND (a.status = 'ACTIVE' OR a.id = ?2))"
    " ORDER BY cs.need_score DESC, cs.confidence DESC, s.sort_order, s.id"
)
# The scoring behind a service: the company's answers to its Need questions, with the model's reason.
PROPOSAL_ANSWERS = (
    "SELECT c.service_id, c.id AS criterion_id, c.prompt, c.enrichment_key, ra.value, ra.points, ra.source, x.rationale"
    " FROM rubric_answer ra JOIN rubric_criterion c ON c.id = ra.criterion_id"
    " LEFT JOIN ai_run_answer x ON x.ai_run_id = ra.ai_run_id AND x.criterion_id = ra.criterion_id"
    " WHERE ra.company_id = ? AND c.section = 'NEED' AND c.active = 1 ORDER BY c.sort_order, c.id"
)


def proposal_log(agreement_id: str, step: str, detail: str) -> None:
    logger.info(json.dumps({"event": "carmen_proposals", "agreement_id": agreement_id, "step": step, "detail": detail[:300]}))


async def proposals_of(database: Any, company_id: str, agreement_id: str | None = None) -> Row | None:
    """The services worth proposing to a company, the best match between a need and a service first,
    each with the answers and the facts behind its score. None when there is no such company."""
    company, services, answers, facts = await database.batch(
        [
            database.prepare("SELECT scored_at FROM company WHERE id = ?").bind(company_id),
            database.prepare(PROPOSED_SERVICES).bind(company_id, agreement_id),
            database.prepare(PROPOSAL_ANSWERS).bind(company_id),
            database.prepare(LATEST_FACTS).bind(company_id),
        ]
    )
    if not company["results"]:
        return None
    known = {fact["key"]: fact["value"] for fact in facts["results"]}
    items: list[Row] = []
    for service in services["results"]:
        own = [answer for answer in answers["results"] if answer["service_id"] == service["service_id"]]
        keys = dict.fromkeys(answer["enrichment_key"] for answer in own if answer["enrichment_key"] in known)
        shown = ("criterion_id", "prompt", "value", "points", "source", "rationale")
        items.append(
            {
                **service,
                "answers": [{key: answer[key] for key in shown} for answer in own],
                "facts": [{"key": key, "value": known[key]} for key in keys],  # the research behind the answers
            }
        )
    return {"scored_at": company["results"][0]["scored_at"], "items": items}


@app.get("/api/v1/companies/{company_id}/proposals", response_model=contract.Proposals)
@action(READ)
async def list_proposals(request: Request, company_id: str) -> JSONResponse:
    try:
        found = await proposals_of(db(request), company_id)
    except Exception as e:
        return failed(request, "list_proposals", e)
    return error(request, 404) if found is None else JSONResponse(found)


PROPOSAL_REUSE = 60  # minutes; the fallback for a missing setting proposal.reuse_minutes


async def queue_proposals(env: Any, agreement_id: str, real: bool = False, fresh: bool = False) -> str:
    """Put the proposals job of an agreement on the queue. Never raises. Answers QUEUED; RECENT when a
    job of the agreement was started within proposal.reuse_minutes and no fresh job is asked for; or FAILED.

    The start is recorded on the agreement first (specs/036-proposals-once/spec.md), so a second start
    soon after pays for no model and sends no second email.
    """
    try:
        database = env.DB
        found = await database.prepare("SELECT value FROM setting WHERE key = 'proposal.reuse_minutes'").first()
        minutes = int(found["value"]) if found and str(found["value"]).isdecimal() else PROPOSAL_REUSE
        # One statement, so of two starts at the same moment only one gets the agreement.
        sql = (
            f"UPDATE agreement SET proposals_started_at = {NOW} WHERE id = ? AND (? = 1 OR proposals_started_at IS NULL"
            " OR proposals_started_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)) RETURNING id"
        )
        if not await database.prepare(sql).bind(agreement_id, 1 if fresh else 0, f"-{minutes} minutes").first():
            proposal_log(agreement_id, "recent", f"a job was started in the last {minutes} minutes, so none is started")
            return "RECENT"
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_proposals", "agreement_id": agreement_id, "step": "record_start", "error": type(e).__name__}))
        return "FAILED"
    try:
        await env.RUNS.send({"proposal_agreement_id": agreement_id, "real": real, "fresh": fresh})
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_proposals", "agreement_id": agreement_id, "step": "queue", "error": type(e).__name__}))
        try:
            # No job was started, so the next start must not be refused.
            await database.prepare("UPDATE agreement SET proposals_started_at = NULL WHERE id = ?").bind(agreement_id).run()
        except Exception as e:
            logger.error(json.dumps({"event": "carmen_proposals", "agreement_id": agreement_id, "step": "clear_start", "error": type(e).__name__}))
        return "FAILED"
    return "QUEUED"


# For an administrator, and for the smoke test: the same job a completed one-time agreement starts.
# With real=true the job uses the default models and sends real email in any environment. It costs
# money, so the smoke test never sets it. A start within proposal.reuse_minutes of the last one is
# answered 409 with started_at and finished_at; fresh=true starts a job all the same, and that job
# uses no earlier run again.
@app.post("/api/v1/agreements/{agreement_id}/proposals", response_model=contract.Queued, status_code=202)
@action(MANAGE)
async def start_proposals(request: Request, agreement_id: str, real: bool = False, fresh: bool = False) -> JSONResponse:
    try:
        found = await db(request).prepare("SELECT id FROM agreement WHERE id = ?").bind(agreement_id).first()
    except Exception as e:
        return failed(request, "load_agreement", e)
    if not found:
        return error(request, 404)
    outcome = await queue_proposals(request.scope["env"], agreement_id, real, fresh)
    if outcome == "RECENT":
        # When the job was started, and when its email was stored: none yet means it is still working.
        started: Row | None = None
        try:
            sql = (
                "SELECT a.proposals_started_at AS started_at, (SELECT max(n.created_at) FROM notification n"
                " WHERE n.agreement_id = a.id AND n.kind = 'SERVICE_PROPOSALS' AND n.created_at >= a.proposals_started_at) AS finished_at"
                " FROM agreement a WHERE a.id = ?"
            )
            started = await db(request).prepare(sql).bind(agreement_id).first()
        except Exception as e:
            log_error(request, "load_proposals_start", e)
        times = {"started_at": started["started_at"] if started else None, "finished_at": started["finished_at"] if started else None}
        return JSONResponse({"ok": False, "error": "CONFLICT", "request_id": request.state.request_id, **times}, status_code=409)
    if outcome != "QUEUED":
        return error(request, 500)
    return JSONResponse({"queued": True}, status_code=202)


def job_request(env: Any, name: str) -> Request:
    """A request for a job that has none, so the job can use the code a person's request uses. It has
    no caller: the record of a change says the job's name where a request has its id."""
    return Request({"type": "http", "env": env, "state": {"identity": {"id": None, "machine": True}, "request_id": name}})


async def proposal_run(env: Any, company_id: str, kind: str, model: Row, input_run_id: str | None = None) -> Row:
    """Carry out one run inside the job, and return it as it was stored."""
    run: Row = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "kind": kind,
        "ai_model_id": model["id"],
        "model_id_used": model["model_id"],
        "input_run_id": input_run_id,
    }
    await insert(env.DB, "ai_run", run).run()
    await process_run(env, run["id"])  # never raises: a failure is on the run
    return await env.DB.prepare(f"{RUN_SELECT} WHERE r.id = ?").bind(run["id"]).first()


PROPOSAL_TRIES = 3  # the fallback for a missing setting proposal.max_tries; a job must end inside one queue invocation (15 minutes)


async def proposal_models(database: Any, default_id: str, capability: str, tries: int) -> list[Row]:
    """The models that can do a part (can_research or can_score): the default first, then every other
    enabled model in catalog order, at most tries of them. The stub is never one of them."""
    sql = f"SELECT * FROM ai_model WHERE active = 1 AND {capability} = 1 ORDER BY id = ? DESC, sort_order, id"
    found = await database.prepare(sql).bind(default_id).all()
    return [model for model in found["results"] if model["provider"] in ai.ADAPTERS and model["provider"] != "STUB"][:tries]


async def first_success(env: Any, agreement_id: str, company_id: str, kind: str, models: list[Row], runs: list[Row], input_run_id: str | None = None) -> Row | None:
    """Try the models in order until a run succeeds. Each try is a run of its own, with its own model,
    so runs stay comparable per model. Every run is logged and added to runs."""
    for model in models:
        run = await proposal_run(env, company_id, kind, model, input_run_id)
        runs.append(run)
        proposal_log(agreement_id, "run", f"{kind} with {run['model_label']}: {run['status']} {run['error'] or ''}")
        if run["status"] == "SUCCEEDED":
            return run
    return None


async def recent_run(database: Any, company_id: str, kind: str, models: list[Row], minutes: int, input_run_id: str | None = None) -> Row | None:
    """The newest run of the company that one of the models finished in the last minutes, or None. A
    scoring run must have read the given research run. Such a run is used again and marked reused,
    so a job that is started twice, or delivered twice by the queue, pays once."""
    if minutes <= 0 or not models:
        return None
    marks = ", ".join("?" * len(models))
    sql = (
        f"{RUN_SELECT} WHERE r.company_id = ? AND r.kind = ? AND r.status = 'SUCCEEDED' AND r.ai_model_id IN ({marks})"
        " AND r.finished_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) AND (? IS NULL OR r.input_run_id = ?)"
        " ORDER BY r.finished_at DESC, r.id LIMIT 1"
    )
    ids = [model["id"] for model in models]
    row = await database.prepare(sql).bind(company_id, kind, *ids, f"-{minutes} minutes", input_run_id, input_run_id).first()
    return {**row, "reused": True} if row else None


async def fresh_scores(
    env: Any, agreement_id: str, company_id: str, settings: dict[str, str], real: bool, fresh: bool = False
) -> tuple[str | None, list[Row]]:
    """Research the company, score it, and apply the scores. Returns why it failed, in words that are
    safe to show, or None; and every run that was made or used again. When a run fails, the next model
    that can do the part is tried, and the failure stays on its own run.

    A run that one of the job's models finished within proposal.reuse_minutes is used again, unless
    the job is a fresh one (specs/036-proposals-once/spec.md).

    The models are the real ones where the variable PROPOSAL_MODELS is DEFAULT, or when a person
    asked for a real run. Anywhere else the stub is used, so the preview and the pipeline pay nothing.
    """
    database = env.DB
    runs: list[Row] = []
    if real or str(getattr(env, "PROPOSAL_MODELS", "")) == "DEFAULT":
        wanted = settings.get("proposal.max_tries", "")
        tries = int(wanted) if wanted.isdecimal() and int(wanted) > 0 else PROPOSAL_TRIES
        researchers = await proposal_models(database, settings.get("ai.default_research_model", ""), "can_research", tries)
        scorers = await proposal_models(database, settings.get("ai.default_scoring_model", ""), "can_score", tries)
    else:
        found = await database.prepare("SELECT * FROM ai_model WHERE provider = 'STUB' ORDER BY sort_order LIMIT 1").all()
        researchers = scorers = cast(list[Row], found["results"])
    wanted = settings.get("proposal.reuse_minutes", "")
    minutes = 0 if fresh else int(wanted) if wanted.isdecimal() else PROPOSAL_REUSE
    research = await recent_run(database, company_id, "RESEARCH", researchers, minutes)
    scoring = await recent_run(database, company_id, "SCORING", scorers, minutes, research["id"]) if research else None
    for run in (research, scoring):
        if run:
            runs.append(run)
            proposal_log(agreement_id, "reused", f"{run['kind']} with {run['model_label']}, finished {run['finished_at']}")
    research = research or await first_success(env, agreement_id, company_id, "RESEARCH", researchers, runs)
    if not research:
        return "No model could do the research", runs
    scoring = scoring or await first_success(env, agreement_id, company_id, "SCORING", scorers, runs, research["id"])
    if not scoring:
        return "No model could do the scoring", runs
    if scoring.get("reused") and scoring["applied_at"]:
        return None, runs  # its answers are the company's answers already
    try:
        await apply_scoring(job_request(env, "proposals"), scoring)
    except StepFailed as e:
        logger.error(json.dumps({"event": "carmen_error", "step": e.step, "error": type(e.cause).__name__, "description": str(e.cause)}))
        return "The new scores could not be stored", runs
    return None, runs


async def process_proposals(env: Any, agreement_id: str, real: bool = False, fresh: bool = False) -> None:
    """The proposals job of one agreement: fresh research and scoring, then one email with the other
    services worth proposing. Never raises. With real, a person asked for the default models and real
    email, whatever the environment says. With fresh, a person asked for a job that uses no earlier
    run again. Any other job that the queue delivers a second time sends no second email."""
    try:
        database = env.DB
        sql = (
            "SELECT a.id, a.title, a.type, a.company_id, a.proposals_started_at, c.name AS company_name FROM agreement a"
            " JOIN company c ON c.id = a.company_id WHERE a.id = ?"
        )
        agreement = await database.prepare(sql).bind(agreement_id).first()
        if not agreement:
            return proposal_log(agreement_id, "skipped", "no such agreement")
        found = await database.prepare("SELECT key, value FROM setting WHERE key IN ('digest.recipient', 'proposal.max_tries', 'proposal.reuse_minutes') OR key LIKE 'ai.default_%'").all()
        settings: dict[str, str] = {row["key"]: row["value"] for row in found["results"]}
        recipient = settings.get("digest.recipient", "")
        if not recipient:
            return proposal_log(agreement_id, "skipped", "the setting digest.recipient is missing, so nothing is sent")

        if not fresh and agreement["proposals_started_at"]:
            sql = "SELECT created_at FROM notification WHERE agreement_id = ? AND kind = 'SERVICE_PROPOSALS' AND created_at >= ? LIMIT 1"
            sent = await database.prepare(sql).bind(agreement_id, agreement["proposals_started_at"]).first()
            if sent:
                return proposal_log(agreement_id, "skipped", f"the email of this start was stored at {sent['created_at']}")

        problem, runs = await fresh_scores(env, agreement_id, agreement["company_id"], settings, real, fresh)
        proposals = await proposals_of(database, agreement["company_id"], agreement_id)
        if proposals is None:
            return proposal_log(agreement_id, "skipped", "the company is gone")
        note = ""
        if problem:
            proposal_log(agreement_id, "not_fresh", problem)
            age = f"This list comes from the scores of {proposals['scored_at']}." if proposals["scored_at"] else "No earlier scores are stored."
            note = f"Fresh research and scoring failed. {problem}. {age}\n\n"
        subject, body = notify.render(
            "proposals",
            company=str(agreement["company_name"]),
            title=str(agreement["title"] or agreement["type"]),
            note=note,
            runs=notify.run_lines(runs),
            sections=notify.proposal_sections(proposals["items"]) or notify.NO_PROPOSALS,
        )
        provider = notify.PROVIDERS["CLOUDFLARE"] if real else notify.provider(getattr(env, "EMAIL_PROVIDER", None))
        # A second delivery of this start that got this far at the same moment sends no second email.
        since = None if fresh else agreement["proposals_started_at"]
        stored = await notification(env, provider, "SERVICE_PROPOSALS", recipient, subject, body, agreement_id, since)
        proposal_log(agreement_id, "finished", f"{len(runs)} runs, {len(proposals['items'])} services, email {stored['status'] if stored else 'already recorded'}")
    except Exception as e:
        logger.error(json.dumps({"event": "carmen_proposals", "agreement_id": agreement_id, "step": "error", "error": type(e).__name__, "detail": str(e)[:300]}))


# ---- Settings ------------------------------------------------------------------------------------
# Tunable values that code reads on every use. A setting is created by a migration, never here.
# See specs/016-settings/spec.md.


@app.get("/api/v1/settings", response_model=Items[models.Setting])
@action(READ)
async def list_settings(request: Request) -> JSONResponse:
    try:
        result = await db(request).prepare("SELECT * FROM setting ORDER BY key").all()
    except Exception as e:
        return failed(request, "list_settings", e)
    return JSONResponse({"items": result["results"]})


@app.patch("/api/v1/settings", response_model=Items[models.Setting])
@action(MANAGE)
@takes(contract.SettingsBody)
async def update_settings(request: Request, body: dict[str, Any]) -> JSONResponse:
    database = db(request)
    try:
        stored = await database.prepare("SELECT key, value FROM setting").all()
        current: dict[str, str] = {row["key"]: row["value"] for row in stored["results"]}
    except Exception as e:
        return failed(request, "load_settings", e)
    try:
        changed: dict[str, str] = {}
        for key, value in body.items():
            if key not in current:
                raise BadBody(f"there is no setting {key}")
            if problem := setting_problem(key, value):
                raise BadBody(problem)
            if str(value).strip() != current[key]:
                changed[key] = str(value).strip()
        for key in ("ai.default_research_model", "ai.default_scoring_model"):
            if key in changed and not await database.prepare("SELECT 1 AS found FROM ai_model WHERE id = ?").bind(changed[key]).first():
                raise BadBody(f"{key} must be the id of an AI model")
    except Exception as e:
        return failed(request, "check_settings", e)

    if changed:
        rescore = any(key.startswith("score.") for key in changed)
        try:
            # A setting belongs to no company, so the Worker log is the record of the change. It is written first.
            for key, value in changed.items():
                audit(request, f"Setting {key}", None, {"value": current[key]}, {"value": value})
            sql = f"UPDATE setting SET value = ?, updated_at = {NOW} WHERE key = ?"
            statements = [database.prepare(sql).bind(value, key) for key, value in changed.items()]
            await database.batch(statements + ([rescore_mark(database)] if rescore else []))
        except Exception as e:
            return failed(request, "update_settings", e)
        # A threshold moved, so stored recommendations may be out of date. Or the switch is ON again
        # with a rescoring waiting: this is the one rescoring for everything changed while it was OFF.
        if rescore or (changed.get(RESCORE_AUTO) == "ON" and current.get(RESCORE)):
            try:
                await rescore_after(request)
            except Exception as e:
                return failed(request, "rescore_after_settings", e)
    return await list_settings(request)
