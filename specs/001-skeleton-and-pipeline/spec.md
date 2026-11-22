<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Skeleton: Access verification, role enforcement, and /me

**Feature Branch**: `10-skeleton-access-and-roles`

**Created**: 2026-11-22

**Status**: Implemented.

**Input**: Issue #10. Backlog feature 001. The Worker, the health endpoint, the preview scripts, and the pipeline already exist (`specs/000-repo-setup/spec.md`). This feature adds what every later endpoint stands on: a verified identity on every request and an enforced role. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Nothing answers without a verified identity (Priority: P1)

Every request except the health check must carry a valid Cloudflare Access token. The API verifies it itself and trusts nothing else.

**Independent Test**: On the preview, `GET /api/v1/me` with the service token returns 200. A token with a changed signature, a wrong audience, a wrong issuer, or a past expiry is rejected in the unit tests and in discovery task D8.

**Acceptance Scenarios**:

1. **Given** no token, **When** any path other than `/api/v1/health` is requested, **Then** the answer is 401 `UNAUTHORIZED`, also for a path that does not exist.
2. **Given** a valid token for a person, **When** `GET /api/v1/me` is requested, **Then** the answer is that person's `app_user` row, created on first sight.
3. **Given** any request, **When** `GET /api/v1/health` is requested, **Then** the answer is 200 `{"ok": true}` and no dependency is touched.

### User Story 2 - A role allows only what it should (Priority: P1)

A `READER` or `MEMBER` added later can never do more than the role allows, and an endpoint added later can never ship without a rule.

**Independent Test**: The unit tests check every role against every action, check the rule on requests from simulated users, and fail when a route declares no action.

**Acceptance Scenarios**:

1. **Given** a route that declares no action, **When** anyone calls it, **Then** the answer is 403 `FORBIDDEN`, and the unit tests fail.
2. **Given** a `READER`, **When** a route's action is `WRITE`, `DELETE`, or `MANAGE`, **Then** the answer is 403.
3. **Given** a `MEMBER`, **When** a route's action is `DELETE` or `MANAGE`, **Then** the answer is 403.
4. **Given** a user whose `active` is 0, **When** any route is called, **Then** the answer is 403.

### User Story 3 - The pipeline and a developer can still work (Priority: P2)

The smoke test signs in to the preview with the service token. A developer can run the Worker locally, where Cloudflare Access is not in front of it.

**Acceptance Scenarios**:

1. **Given** the preview, **When** the service token calls `GET /api/v1/me`, **Then** the answer is 200 with `"role": "ADMIN"` and `"machine": true`, and no `app_user` row is created.
2. **Given** production, **When** a verified service token arrives, **Then** the answer is 403.
3. **Given** a local run with `DEV_USER_EMAIL` in `.dev.vars`, **When** `GET /api/v1/me` is requested at `localhost:8787`, **Then** the answer is that user's row from the local database.

### Edge Cases

- The first `app_user` row in an empty database becomes `ADMIN` (a trigger in the data model). Only a person can create a row, and Cloudflare Access admits only allow-listed people, so the first person to sign in to each environment is its administrator. A machine caller never creates a row.
- Cloudflare Access rotates its signing keys. An unknown key id makes the Worker fetch the key list again.
- A demotion or deactivation takes effect on the next request, because nothing about the user is cached.

## Requirements

### Discovery results

- **D8 (answered 2026-11-22)**: An Access token is verified in a Python Worker with the Web Crypto API of the Workers runtime (`crypto.subtle`, RS256), called from Python. No Python cryptography package is used. Measured on the preview: 10 ms for the first verification, which fetches the signing keys, and 2 ms afterward. A changed signature and a wrong audience were both rejected.
- **D1 (first answer 2026-11-22)**: With one route, Wrangler reports a startup time of about 2.4 seconds at deploy, the first request after a deploy took 1.3 seconds, and a warm request took 0.09 seconds. Nothing needs trimming. Measure again as routes are added.
- **D3**: answered in `specs/000-repo-setup/spec.md`.

### Functional Requirements

Identity:

- **FR-001**: Every request except `GET /api/v1/health` MUST be identified before anything else happens, including before a 404. A request that cannot be identified gets 401 `UNAUTHORIZED`.
- **FR-002**: The token is read from the `Cf-Access-Jwt-Assertion` header and MUST pass all of: algorithm RS256; a signature made by a key published at `https://<ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs`; issuer `https://<ACCESS_TEAM_DOMAIN>`; audience containing `ACCESS_AUD`; not expired; not used before its start time.
- **FR-003**: A token with an `email` claim is a person. The API finds the `app_user` row by lowercase email and, when there is none, inserts one with only an id and the email. The role is whatever the database gives it. The API MUST NOT set a role.
- **FR-004**: A person whose `active` is 0 gets 403 `FORBIDDEN`.
- **FR-005**: A verified token with no `email` and a `common_name` is a service token, a machine caller. When the variable `SERVICE_TOKENS_ARE_ADMIN` is `"true"`, it acts as `ADMIN`. Otherwise it gets 403. The variable is `"true"` under `env.preview` in `wrangler.jsonc` and MUST NOT be set for production. A machine caller MUST NOT create an `app_user` row. Decided 2026-11-22.
- **FR-006**: For local runs only, when the variable `DEV_USER_EMAIL` is set and the request has no `Cf-Ray` header, the request acts as the person with that email, through FR-003 and FR-004, with no token. Cloudflare adds `Cf-Ray` to every request it carries, and a local run has none; the host cannot be used, because a local run reports the route's hostname. The variable lives in the git-ignored `.dev.vars` and MUST NOT appear in `wrangler.jsonc`. A unit test fails if it does. Decided 2026-11-22.
- **FR-007**: The role and the `active` flag are read from `app_user` on every request and never cached.

Roles:

- **FR-008**: Every route declares one action: `PUBLIC`, `READ`, `WRITE`, `DELETE`, or `MANAGE`. `WRITE` is creating or updating a record. `MANAGE` is changing users, settings, the rubric, the service catalog, or the AI model list.
- **FR-009**: One pure function, `allowed(role, action)`, holds the rule: `READER` may `READ`; `MEMBER` may `READ` and `WRITE`; `ADMIN` may do all four. An unknown role may do nothing.
- **FR-010**: A route with no declared action is refused with 403 for everyone. Only `GET /api/v1/health` is `PUBLIC`.
- **FR-011**: Each later feature's specification lists the action of every endpoint it adds.

Endpoints and responses:

- **FR-012**: `GET /api/v1/me` (action `READ`) returns the caller: `id`, `email`, `display_name`, `role`, `active`, `created_at`, and `machine`. For a machine caller the first three are null, `role` is `ADMIN`, and `machine` is true.
- **FR-013**: Every error response is `{"ok": false, "error": "<CODE>", "request_id": "<id>"}`. The request id is the `Cf-Ray` header when present, otherwise a new lowercase UUID. Codes: `BAD_REQUEST`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `CONFLICT`, `TOO_LARGE`, `INTERNAL`.
- **FR-014**: Every response carries `X-Robots-Tag: noindex, nofollow`.
- **FR-015**: Errors are logged as one line of JSON with the step, the error name, its description, the source line, and the request id. A token is never logged.
- **FR-016**: The interactive documentation pages and `/openapi.json` are not served. They would be routes with no action.

Tests and scripts:

- **FR-017**: `./unit-test.sh` runs the unit tests with the standard library's `unittest`: every role against every action; the claims check; the rule applied to simulated users; every route has a known action; `wrangler.jsonc` has no `DEV_USER_EMAIL` and no production `SERVICE_TOKENS_ARE_ADMIN`.
- **FR-018**: `preview-test.sh` runs the unit tests before it deploys, then checks `GET /api/v1/me` and a path that does not exist (404 with the error shape).
- **FR-019**: `run.sh` builds the local database from the migrations in `andexor-carmen-main` the first time it runs. Delete `.wrangler/state` to rebuild it after a new migration.

### What is not tested

- A real `MEMBER` or `READER` signing in through Cloudflare Access, because no second person is onboarded. A local run can act as any email (`DEV_USER_EMAIL`), which exercises the same code with a `READER` row.
- A rejected token arriving through Cloudflare Access, because Access never forwards one. Rejections are covered by the unit tests and by D8.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes.
- **SC-002**: On the preview, `GET /api/v1/me` with the service token returns 200, `"role": "ADMIN"`, `"machine": true`.
- **SC-003**: Locally, with `DEV_USER_EMAIL` set, `GET /api/v1/me` returns the user, and with it unset returns 401.
- **SC-004**: The check `Preview and smoke test` passes.

## Manual steps

Listed in `MANUAL-TASKS.md`: after this is deployed, Ed opens `/api/v1/me` on production and on the preview, so that he is the first user, and therefore the administrator, of each.
