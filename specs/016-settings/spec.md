<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Settings

**Feature Branch**: `52-settings`

**Created**: 2026-11-24

**Status**: Implemented, and live on the preview.

**Input**: Issue #52. Backlog feature 016: read and change the values in the `setting` table through the API, so that a threshold, the digest hour, or a limit changes without a migration or a deploy. The backlog also names model management; the model endpoints exist since `specs/009-ai-runs/spec.md`, so this feature adds none. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - An administrator changes a setting (Priority: P1)

An administrator moves the digest to another hour, or raises a threshold, and the change holds from the next use on.

**Independent Test**: `./preview-test.sh` reads the settings, changes one, and puts it back.

**Acceptance Scenarios**:

1. **Given** a signed-in user,\
   **When** they call `GET /api/v1/settings`,\
   **Then** every setting is listed with its key, value, description, and the time it last changed.
2. **Given** an administrator,\
   **When** they send one or more keys with new values to `PATCH /api/v1/settings`,\
   **Then** the values are stored in one transaction, and the answer is the full list.
3. **Given** a user who is not an administrator,\
   **When** they call `PATCH /api/v1/settings`,\
   **Then** the answer is 403.
4. **Given** a change to a score threshold,\
   **When** it is stored,\
   **Then** the scores and recommendations of every company that has answers are recomputed.

### User Story 2 - A wrong value is refused (Priority: P1)

A value that would make Carmen fall back silently, such as an unknown time zone, never gets into the table.

**Independent Test**: `./unit-test.sh` covers the rule of every setting. `./preview-test.sh` has four bad changes refused.

**Acceptance Scenarios**:

1. **Given** a key that is not in the `setting` table,\
   **When** it is sent,\
   **Then** the answer is 400, and nothing is stored.
2. **Given** a value that its setting cannot hold,\
   **When** it is sent,\
   **Then** the answer is 400, and nothing is stored.
3. **Given** a change with one good value and one bad value,\
   **When** it is sent,\
   **Then** the answer is 400, and the good value is not stored either.

### Edge Cases

- A value is stored as text. A whole number in the body is accepted and stored as its text. Spaces around a value are removed.
- A value that equals the stored one is not a change: nothing is written, recorded, or recomputed.
- An empty body changes nothing and answers the list.
- A setting cannot be created or deleted through the API. A new setting comes with a migration in `andexor-carmen-main`.
- A setting with no rule in code holds any text that is not empty.
- A refusal is a 400 with no detail in the answer, as everywhere. The reason is in the Worker log.

## Requirements

### Functional Requirements

- **FR-001**: `GET /api/v1/settings` returns `{"items": [...]}` with every row of `setting`, ordered by key.
- **FR-002**: `PATCH /api/v1/settings` takes an object of keys and values, stores the ones that differ in one transaction, sets their `updated_at`, and returns the list as `GET` does.
- **FR-003**: A key that is not in the table is refused with 400.
- **FR-004**: A value is checked by the rule of its key, in `records.setting_problem`:

| Setting | Rule |
| --- | --- |
| `score.*` | A whole number from 0 to 100 |
| `digest.hour_local` | A whole number from 0 to 23 |
| `digest.time_zone` | A time zone name that the zone data knows |
| `digest.days` | Day names from `MON` to `SUN`, separated by commas, none twice |
| `digest.recipient` | One email address, with or without a name: `First Last <address>` (`specs/028-email-names/spec.md`) |
| `renewal.default_notice_days` | A whole number of 0 or more |
| `document.max_size_bytes`, `import.max_rows`, `ai.research.max_pages` | A whole number of 1 or more |
| `ai.default_research_model`, `ai.default_scoring_model` | The id of a row in `ai_model` |
| Any other key | Text that is not empty |

- **FR-005**: Every change is recorded before it is stored: a line in the Worker log with the key, the old value, the new value, and who changed it. A setting belongs to no company, so there is no timeline entry.
- **FR-006**: When a key that begins with `score.` changed, the stored scores are recomputed with `rescore_all`.

### Endpoints

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /api/v1/settings` | `READ` | Lists the settings |
| `PATCH /api/v1/settings` | `MANAGE` | Changes settings |

### Not in this feature

- Model management. `GET`, `POST`, and `PATCH /api/v1/ai/models` exist since feature 009.
- Creating or deleting a setting.
- A history of a setting's values, beyond the Worker log.
- An upper limit for `import.max_rows` or `document.max_size_bytes`. 2,000 rows were measured; a larger value is the administrator's decision.
- A message in the answer that says which value was refused.

### What is not tested

- A call by a `READER` or a `MEMBER` against a deployed Worker. The preview's token is an administrator; the unit tests cover the declared actions.
- The recomputed scores after a threshold change. On the preview a threshold was changed and put back, and both calls answered 200; the arithmetic is the one of feature 004.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: it lists the settings, changes one and puts it back, has an unknown key, a bad hour, a bad time zone, and an unknown model refused, and finds nothing stored from a change with one bad value.
