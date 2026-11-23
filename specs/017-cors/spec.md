<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Cross-Origin Resource Sharing (CORS)

**Feature Branch**: `15-companies-and-contacts`

**Created**: 2026-11-22

**Status**: Implemented.

**Input**: Issue #15, delivered with feature 002. The conventions for every endpoint call for CORS, but no backlog feature carries it, so it has its own number after the backlog (001 to 016). Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - A page at an allowed origin can use the API (Priority: P1)

The deployed UI is served from the same hostname as the API and needs no CORS. A UI that runs somewhere else, such as on a developer's machine, is at another origin, and a browser lets it read the API's answers only when the API says so.

**Independent Test**: The smoke test calls the preview with an `Origin` header that is listed, and with one that is not.

**Acceptance Scenarios**:

1. **Given** an origin listed in `ALLOWED_ORIGINS`,\
   **When** any request carries it in `Origin`,\
   **Then** the response carries `Access-Control-Allow-Origin` with that origin and `Access-Control-Allow-Credentials: true`.
2. **Given** any other origin, or no `Origin` header,\
   **When** a request is made,\
   **Then** the response carries neither header, and is otherwise unchanged.
3. **Given** a listed origin,\
   **When** the browser sends a preflight request,\
   **Then** the answer is 204 with the allowed methods and headers, and no identity is needed.
4. **Given** production, where `ALLOWED_ORIGINS` is empty,\
   **When** any request is made,\
   **Then** no origin is ever allowed.

### Edge Cases

- An error response (401, 403, 404, and the rest) to a listed origin carries the same headers, so the page can read the error.
- A preflight request from an origin that is not listed is not answered as a preflight. It is treated as any other request: 401 with no sign-in, otherwise 405, and no CORS headers either way.
- Cloudflare Access stands in front of the API. A browser's preflight request carries no sign-in, so Access answers it itself, with 403, unless the Access application is set to pass such requests through. Measured on the preview on 2026-11-22: the 403 comes even when the request carries the service token. That setting is a manual step, needed only when a page at another origin calls a deployed API.
- CORS does not grant access. A request from a listed origin is still identified and its role is still checked.

## Requirements

### Functional Requirements

- **FR-001**: `ALLOWED_ORIGINS` is a list of exact origins (scheme, host, and port), separated by commas. There is no wildcard and no pattern. Empty means no origin is allowed.
- **FR-002**: When a request's `Origin` is in the list, every response to it, including errors, MUST carry `Access-Control-Allow-Origin: <that origin>` and `Access-Control-Allow-Credentials: true`. Credentials are allowed because the page must send its Cloudflare Access cookie.
- **FR-003**: When the `Origin` is absent or not in the list, the response MUST carry neither header.
- **FR-004**: An `OPTIONS` request from a listed origin with an `Access-Control-Request-Method` header is a preflight. It is answered with 204 before the caller is identified, with `Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE`, `Access-Control-Max-Age: 86400`, and `Access-Control-Allow-Headers` equal to the request's `Access-Control-Request-Headers` when it has one. A preflight is not a route, so it declares no action.
- **FR-005**: Every response carries `Vary: Origin`.
- **FR-006**: In `wrangler.jsonc`, `ALLOWED_ORIGINS` is empty for production and never contains `*` for the preview. A unit test fails otherwise.
- **FR-007**: The unit tests cover the origin rule. `preview-test.sh` checks a listed origin and an unlisted origin on the preview. A preflight cannot be checked there (see the edge cases); it was checked in a local run.

### What is not tested

- A real browser at another origin. The smoke test sends the headers a browser would send, together with the service token, which a browser does not have.
- A preflight on a deployed Worker, for the reason above.
- The Cloudflare Access setting that passes preflight requests through. It is listed in `MANUAL-TASKS.md` and is not turned on.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes.
- **SC-002**: `./preview-test.sh` passes, including the CORS checks.
- **SC-003**: In a local run, a preflight from a listed origin gets 204 with the allowed methods, and one from an unlisted origin gets no CORS headers (405 for the local user).
