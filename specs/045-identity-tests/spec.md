<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Unit tests for how a caller's identity is worked out

**Feature Branch**: `104-identity-tests`

**Created**: 2026-11-28

**Status**: Implemented.

**Input**: Issue #104. Finding 17 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: test every branch. This spec adds no behavior. It writes down, as tests, the rules of `specs/001-skeleton-and-pipeline/spec.md` and `specs/014-machine-methods/spec.md` that `resolve_identity` in `src/app.py` carries out.

## Why

`resolve_identity` runs on every request and decides who the caller is. Every permission check after it trusts its answer. The token's claims, the signing keys, and the table of roles were tested, but this function was not: the unit tests of endpoints hand in a ready-made identity. A change that made every service token an `ADMIN`, or let a deactivated user in, passed every unit test and the smoke test, which runs where the test token is meant to be `ADMIN`. With a sales team, this function will change: more roles, and users added and removed.

## User Scenarios and Testing

### User Story 1 - A change that widens access fails a test (Priority: P2)

**Independent Test**: `./unit-test.sh` runs `tests/test_identity.py`. Each rule below was also broken in the code once, on purpose, and a test failed each time.

**Acceptance Scenarios**:

1. **Given** a service token,\
   **When** the identity is worked out,\
   **Then** the caller is a machine caller with the role `MACHINE`, has no user, and no `app_user` row is created.
2. **Given** a service token and the variable `SERVICE_TOKENS_ARE_ADMIN`,\
   **When** the variable is exactly `true`,\
   **Then** the role is `ADMIN`; with any other value, such as `True`, `1`, or `yes`, it is `MACHINE`.
3. **Given** a token with no email and no common name,\
   **When** the identity is worked out,\
   **Then** the request is refused with 403.
4. **Given** a person whose user is deactivated,\
   **When** the identity is worked out,\
   **Then** the request is refused with 403, whatever the role.
5. **Given** a person who signs in for the first time,\
   **When** the identity is worked out,\
   **Then** the user is created with an id and an email only, and has the role that the database decides.
6. **Given** a person whose token holds the email in capitals,\
   **When** the identity is worked out,\
   **Then** the user is found by the email in lower case, and no second user is created.
7. **Given** the variable `DEV_USER_EMAIL`,\
   **When** a request has no `Cf-Ray` header,\
   **Then** the caller is that person and no token is checked; with the header, the variable is ignored and the request needs its own token.
8. **Given** a missing or bad token,\
   **When** the identity is worked out,\
   **Then** the answer is 401; when the token check or the database fails, the answer is 500.

### Edge Cases

- A person is never made `ADMIN` by `SERVICE_TOKENS_ARE_ADMIN`: the variable is for service tokens only.
- A service token that acts as `ADMIN` is still a machine caller.

## Requirements

### Functional Requirements

- **FR-001**: `tests/test_identity.py` calls `resolve_identity` with a stand-in request, a stand-in `app_user` table, and the token check replaced, and holds each scenario above in place.
- **FR-002**: No production code changes, and no endpoint or action changes.
- **FR-003**: A change to `resolve_identity` comes with a test here for each rule it adds or changes.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: Breaking any one of six rules in the code fails a test: the exact value `true`, the check of `active`, the `Cf-Ray` condition, the common name, the lower case email, and the first sign-in with no role. Checked on 2026-11-28.
