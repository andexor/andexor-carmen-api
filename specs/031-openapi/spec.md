<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: The generated OpenAPI specification and the Swagger UI

**Feature Branch**: `66-openapi`

**Created**: 2026-11-26

**Status**: Implemented. Most routes still lack request and response models; they are listed below.

**Input**: Issue #66. Asked for on 2026-11-26: `andexor-carmen-main` no longer plans a hand-written contract; its `CLAUDE.md` says the API contract is the OpenAPI 3.1 specification that FastAPI generates from this repo's code. That specification was not served or saved anywhere. It is now served as JSON, with the Swagger UI, behind the same Cloudflare Access and the same authorization as every other route. This spec replaces FR-016 of `specs/001-skeleton-and-pipeline/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

A contract that nobody can read is no contract. The documentation routes were switched off in feature 001 because a route with no action is refused, and FastAPI's own documentation routes have none. They are worse than that: FastAPI adds them as plain routes, which skip the application's dependencies, so `authorize` would never run for them. The fix is to serve them as routes like the others, with an action, and not to exempt them.

## User Scenarios and Testing

### User Story 1 - A signed-in person reads the contract (Priority: P1)

**Independent Test**: Signed in through Cloudflare Access, open `https://carmen.andexor.net/api/docs`. The Swagger UI lists every route, grouped by resource.

**Acceptance Scenarios**:

1. **Given** a signed-in user with the role `READER`,\
   **When** they get `/api/openapi.json`,\
   **Then** the answer is 200 with an OpenAPI 3.1 document that lists every route of the app.
2. **Given** the same user,\
   **When** they open `/api/docs`,\
   **Then** the Swagger UI loads and reads `/api/openapi.json`.
3. **Given** the document,\
   **When** its `info` is read,\
   **Then** the title is `andexor-carmen-api` and the version is the app's version.
4. **Given** any operation in the document,\
   **When** it is read,\
   **Then** it names the action it needs and the errors the API really answers, with the error body.

### User Story 2 - Nobody else reads it (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a request with no Cloudflare Access credentials,\
   **When** it asks for `/api/openapi.json` or `/api/docs`,\
   **Then** Cloudflare Access turns it away, and a request that reaches the Worker with no valid token gets 401.
2. **Given** a service token in production, which is a machine caller,\
   **When** it asks for either path,\
   **Then** the answer is 403: a machine caller may call the machine methods and nothing else.
3. **Given** any caller,\
   **When** they ask for `/api/redoc`, `/openapi.json`, or `/docs`,\
   **Then** the answer is 404: ReDoc is not served, and nothing is served outside `/api/`.

### Edge Cases

- The Swagger UI's script and style sheet come from the public CDN `cdn.jsdelivr.net`, as FastAPI's page does by default. The page itself and the document come from the Worker. No favicon is fetched from another site.
- "Try it out" in the Swagger UI sends real requests as the signed-in user, in production too.
- The document is built once for each instance of the Worker and kept in memory.
- The two routes are not listed in the document: they are not part of the contract.
- The paths are the same on the preview, in production, and in a local run, because only `/api/*` reaches the Worker.

## Requirements

### Functional Requirements

- **FR-001**: The application is created with `openapi_url="/api/openapi.json"`, `docs_url="/api/docs"`, and `redoc_url=None`, in every environment.
- **FR-002**: The two routes are added by the application class `Api` in `src/app.py` as API routes, so the `authorize` dependency runs for them. Each declares `@action(READ)`. FastAPI's own plain routes for them MUST NOT be registered, and neither route is exempt from authorization.
- **FR-003**: The document is OpenAPI 3.1 and lists every route of the app with every method.
- **FR-004**: `info.title` is the app's name and `info.version` its version. They are the constants `APP_NAME` and `APP_VERSION` in `src/app.py`, because `pyproject.toml` is not packaged with the Worker; a unit test fails when they differ from `pyproject.toml`.
- **FR-005**: `describe` in `src/app.py` completes each operation after FastAPI has generated it: a tag (the first part of the path after `/api/v1/`), the action it needs (`x-carmen-action`, and a sentence in the description), and the errors the API answers (400, 401, 403, 404) with the schema `ErrorBody`. FastAPI's 422 is removed, because a request that fails validation is answered 400.
- **FR-006**: The document declares Cloudflare Access as the security scheme of every operation but the health check.
- **FR-007**: A new route gets its models where it is written: a `response_model`, the real `status_code`, and a typed body. A new route MUST NOT add to the lists below.

### Endpoints

| Method and path | Action | Answer |
|---|---|---|
| `GET /api/openapi.json` | `READ` | 200, the OpenAPI 3.1 document |
| `GET /api/docs` | `READ` | 200, the Swagger UI page |

### What was fixed in this feature

- The health check and `GET /me` have response models.
- The eight list routes that answer one page have the model `PageBody` (`items`, `next_cursor`).
- The eight deletes say 204 with no body, and eleven creating routes say 201, as the code answers.
- The document upload says that its body is the file.
- Every operation has its tag, its action, and its real errors.

### Routes whose description is still incomplete

The handlers answer with dictionaries built from database rows and read their bodies as free JSON objects, checked by the field lists in `src/records.py`. Describing them means a model for each resource, which is its own feature.

Of 79 operations, 61 have a success response with no model, and 30 take a JSON body described only as an object.

Success response with no model (paths are under `/api/v1`):

- **srt**: `GET /srt`
- **companies**: `POST /companies`, `GET /companies/{company_id}`, `PATCH /companies/{company_id}`, `POST /companies/{company_id}/archive`, `POST /companies/{company_id}/unarchive`, `GET /companies/{company_id}/contacts`, `POST /companies/{company_id}/contacts`, `POST /companies/{company_id}/activities`, `GET /companies/{company_id}/answers`, `PUT /companies/{company_id}/answers`, `POST /companies/{company_id}/facts`, `GET /companies/{company_id}/documents`, `POST /companies/{company_id}/documents`, `POST /companies/{company_id}/runs`, `POST /companies/{company_id}/runs/research-and-score`, `GET /companies/{company_id}/runs/compare`, `GET /companies/{company_id}/uptime`, `POST /companies/{company_id}/uptime`, `GET /companies/{company_id}/proposals`
- **contacts**: `PATCH /contacts/{contact_id}`
- **activities**: `PATCH /activities/{activity_id}`
- **tasks**: `POST /tasks`, `PATCH /tasks/{task_id}`
- **services**: `GET /services`, `POST /services`, `PATCH /services/{service_id}`
- **rubric**: `GET /rubric`, `POST /rubric`, `POST /rubric/recompute`, `PATCH /rubric/{criterion_id}`
- **facts**: `GET /facts/keys`, `PATCH /facts/{fact_id}`
- **today**: `GET /today`
- **deals**: `POST /deals`, `GET /deals/{deal_id}`, `PATCH /deals/{deal_id}`
- **agreements**: `POST /agreements`, `GET /agreements/{agreement_id}`, `PATCH /agreements/{agreement_id}`, `POST /agreements/{agreement_id}/renewal`, `POST /agreements/{agreement_id}/proposals`
- **documents**: `GET /documents/{document_id}`, `GET /documents/{document_id}/content`
- **ai**: `GET /ai/models`, `POST /ai/models`, `GET /ai/models/stats`, `PATCH /ai/models/{model_id}`
- **runs**: `GET /runs/{run_id}`, `PATCH /runs/{run_id}`, `POST /runs/{run_id}/facts/decide`, `POST /runs/{run_id}/apply`
- **imports**: `POST /imports/preview`, `POST /imports`, `GET /imports/{import_id}`, `DELETE /imports/{import_id}`
- **machine**: `POST /machine/leads`, `POST /machine/facts`
- **scheduler**: `POST /scheduler/run`
- **settings**: `GET /settings`, `PATCH /settings`

JSON body described only as an object:

- **companies**: `POST /companies`, `PATCH /companies/{company_id}`, `POST /companies/{company_id}/contacts`, `POST /companies/{company_id}/activities`, `PUT /companies/{company_id}/answers`, `POST /companies/{company_id}/facts`, `POST /companies/{company_id}/runs`, `POST /companies/{company_id}/runs/research-and-score`
- **contacts**: `PATCH /contacts/{contact_id}`
- **activities**: `PATCH /activities/{activity_id}`
- **tasks**: `POST /tasks`, `PATCH /tasks/{task_id}`
- **services**: `POST /services`, `PATCH /services/{service_id}`
- **rubric**: `POST /rubric`, `PATCH /rubric/{criterion_id}`
- **facts**: `PATCH /facts/{fact_id}`
- **deals**: `POST /deals`, `PATCH /deals/{deal_id}`
- **agreements**: `POST /agreements`, `PATCH /agreements/{agreement_id}`
- **ai**: `POST /ai/models`, `PATCH /ai/models/{model_id}`
- **runs**: `PATCH /runs/{run_id}`, `POST /runs/{run_id}/facts/decide`
- **imports**: `POST /imports/preview`, `POST /imports`
- **machine**: `POST /machine/leads`, `POST /machine/facts`
- **settings**: `PATCH /settings`

Also incomplete: `GET /documents/{document_id}/content` answers the file's bytes, and the document says JSON. A route that answers 201 or 202 through a helper is still shown as 200 (`POST /companies/{company_id}/runs`, `POST /companies/{company_id}/runs/research-and-score`, `POST /imports`, `POST /machine/leads`, `POST /agreements/{agreement_id}/proposals`).

### Not in this feature

- Models for the resources (the lists above).
- A copy of the document saved in a repo.
- ReDoc.

### What is not tested

- The Swagger UI in a browser, signed in through Cloudflare Access. The smoke test fetches the page with the service token and checks that it is the Swagger UI page.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes: the document validates against the OpenAPI 3.1 schema, lists the app's routes, carries the app's name and version, and is refused with 401 with no credentials and 403 for a role that may not read.
- **SC-002**: `./preview-test.sh` passes: the preview serves the document and the Swagger UI with the service token, and neither with no credentials.
