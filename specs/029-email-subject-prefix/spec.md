<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A preview email says PREV in its subject

**Feature Branch**: `62-email-subject-prefix`

**Created**: 2026-11-26

**Status**: Implemented.

**Input**: Issue #62. Asked for on 2026-11-26: in production a subject stays as it is, for example `Carmen digest for 2026-11-25`. On the preview it starts with `PREV`: `PREV: Carmen digest for 2026-11-25`. With that and the sender's name (`specs/028-email-names/spec.md`), an email shows where it came from. This spec changes `specs/015-scheduler-and-notifications/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

The preview sends real email on demand, built from test data. In the list of an inbox the subject is what is read first, and until now a preview digest and a production digest had the same one.

## User Scenarios and Testing

### User Story 1 - The subject shows the environment (Priority: P1)

**Independent Test**: `./email-test.sh` sends the digest from the preview. Its subject starts with `PREV: `.

**Acceptance Scenarios**:

1. **Given** the production Worker,\
   **When** it makes the digest for 2026-11-25,\
   **Then** the subject is `Carmen digest for 2026-11-25`.
2. **Given** the preview Worker,\
   **When** it makes the same digest,\
   **Then** the subject is `PREV: Carmen digest for 2026-11-25`.
3. **Given** the preview Worker,\
   **When** it makes a renewal alert or a proposals email,\
   **Then** that subject starts with `PREV: ` too.
4. **Given** the preview Worker with the stub provider,\
   **When** a message is made,\
   **Then** the stored message and the Worker log show the subject with `PREV: `.

### Edge Cases

- A renewal alert's subject already starts with a word and a colon. On the preview it reads `PREV: Renewal: <title> ends <date>`.
- A message stored before this feature keeps its subject.
- A local run has no prefix unless `.dev.vars` sets the variable.

## Requirements

### Functional Requirements

- **FR-001**: The variable `EMAIL_SUBJECT_PREFIX` names the mark of an environment. It is `PREV` under `env.preview` in `wrangler.jsonc`. It MUST NOT be set for production.
- **FR-002**: Where the variable is set, the subject of every message is the prefix, a colon, a space, and the subject from the template. Where it is not set, the subject is the template's, unchanged. The rule is `notify.subject_line`.
- **FR-003**: `notification(...)` in `src/app.py` applies the rule, so it holds for every kind of message and every provider. A template and a caller MUST NOT write the prefix.
- **FR-004**: The `subject` of a `notification` row is the subject as it was sent.

### Endpoints

None are added or changed. `GET /api/v1/notifications` (`READ`) shows the subject as it was sent.

### Not in this feature

- A mark in the body of a message.
- A setting for the prefix. It belongs to the environment, not to the data, so it is a variable.

### What is not tested

- The smoke test and the pipeline send no real email. What an inbox shows is seen with `./email-test.sh`, on demand.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: the proposals email and the renewal alert on the preview have subjects that start with `PREV: `.
- **SC-003**: A real digest from the preview arrives with `PREV: ` at the start of its subject.
