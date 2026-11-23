<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Activities and tasks

**Feature Branch**: `17-activities-and-tasks`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #17. Backlog feature 003: the timeline of a company, and tasks. It builds on `specs/002-companies-and-contacts/spec.md`, whose rules for bodies, codes, errors, and paging apply here unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Log what happened (Priority: P1)

A note, call, meeting, or email is logged on a company with nothing but the company.

**Independent Test**: The smoke test logs a note on its test company and reads the timeline back.

**Acceptance Scenarios**:

1. **Given** a company,\
   **When** an empty body is posted to its activities,\
   **Then** the answer is 201 with an entry of kind `NOTE`, dated now, with the caller as `created_by`.
2. **Given** a company with entries,\
   **When** its activities are requested,\
   **Then** they are listed newest first, and `kind` narrows the list.
3. **Given** a hand-logged entry,\
   **When** it is patched or deleted,\
   **Then** it changes or goes.

### User Story 2 - Automatic entries are permanent (Priority: P1)

The timeline is also a record of what the system did. Nobody can add to, change, or remove that part by hand.

**Acceptance Scenarios**:

1. **Given** a company whose stage was changed,\
   **When** its activities are requested,\
   **Then** a `STAGE_CHANGE` entry is among them.
2. **Given** an automatic entry,\
   **When** it is patched or deleted,\
   **Then** the answer is 409 `CONFLICT` and nothing changes.
3. **Given** any caller,\
   **When** an entry is posted or patched with an automatic kind,\
   **Then** the answer is 400.

### User Story 3 - Next steps (Priority: P1)

A task needs only a title. It can point to a company, and it knows when it was done.

**Acceptance Scenarios**:

1. **Given** a body with only a title,\
   **When** it is posted to `/api/v1/tasks`,\
   **Then** the answer is 201 with status `OPEN`.
2. **Given** an open task,\
   **When** its status is patched to `DONE`,\
   **Then** `completed_at` is set to now.
3. **Given** a done task,\
   **When** its status is patched to `OPEN` or `CANCELLED`,\
   **Then** `completed_at` is cleared.
4. **Given** a company with open tasks,\
   **When** the company is requested,\
   **Then** the answer carries them in `open_tasks`.

### User Story 4 - A member can remove their own mistakes (Priority: P2)

A `MEMBER` cannot delete records in general, but can delete a note they logged or a task they own.

**Acceptance Scenarios**:

1. **Given** a `MEMBER` and an entry they logged,\
   **When** they delete it,\
   **Then** the answer is 204.
2. **Given** a `MEMBER` and an entry someone else logged, or a task owned by someone else or by nobody,\
   **When** they delete it,\
   **Then** the answer is 403 and nothing changes.
3. **Given** a `READER`,\
   **When** they delete anything,\
   **Then** the answer is 403.

### User Story 5 - Dates arrive in many forms (Priority: P2)

A date typed or imported as `12/1/2026` or `Dec 1, 2026` is stored the same way as `2026-12-01`, so that sorting and filtering by date work.

**Acceptance Scenarios**:

1. **Given** a task body with `due_date` `12/1/2026`,\
   **When** it is posted or patched,\
   **Then** the stored and returned value is `2026-12-01`.
2. **Given** an entry body with `occurred_at` `11/20/2026 3:15 PM`,\
   **When** it is posted or patched,\
   **Then** the stored and returned value is `2026-11-20T15:15:00Z`.
3. **Given** a task body with `due_date` `next Tuesday`,\
   **When** it is posted or patched,\
   **Then** the stored and returned value is the date of the Tuesday of next week.
4. **Given** a date the API does not understand, such as `13/45/2026`,\
   **When** it is posted, patched, or used as a filter,\
   **Then** the answer is 400 and nothing is stored.

### Edge Cases

- An entry's `contact_id` or `deal_id` must name a record of the entry's own company. Another company's contact is refused with 400.
- A task created with status `DONE` gets `completed_at` at once.
- A member who changed a company's stage is the `created_by` of that automatic entry, and still cannot delete it.
- A task with no company is allowed. Deleting a company deletes its tasks and its timeline.
- `due_before` is exclusive: a task due on that date is not listed.
- A task with no due date sorts last and never matches `due_before`.

## Requirements

### Endpoints and their actions

All paths are under `/api/v1`.

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /companies/{id}/activities` | `READ` | The timeline, newest first |
| `POST /companies/{id}/activities` | `WRITE` | Log an entry by hand |
| `PATCH /activities/{id}` | `WRITE` | Change a hand-logged entry |
| `DELETE /activities/{id}` | `WRITE`, then FR-017 | Delete a hand-logged entry |
| `GET /tasks` | `READ` | List and filter tasks |
| `POST /tasks` | `WRITE` | Create a task |
| `PATCH /tasks/{id}` | `WRITE` | Change a task |
| `DELETE /tasks/{id}` | `WRITE`, then FR-017 | Delete a task |

An `ADMIN` may delete any entry or task. A `MEMBER` may delete only their own: an entry they logged, or a task they own (FR-017). A `READER` may delete nothing. Decided 2026-11-23.

### Functional Requirements

The timeline:

- **FR-001**: The automatic kinds are `STAGE_CHANGE`, `SCORE_CHANGE`, and `SYSTEM`. Only the API writes them. Every other active `ACTIVITY_KIND` code is a hand kind, so a hand kind added to the reference table later needs no code change.
- **FR-002**: `GET /companies/{id}/activities` returns `{"items": [...], "next_cursor": ...}`, ordered by `occurred_at`, newest first. `kind` is an exact filter. `limit` and `cursor` work as for companies.
- **FR-003**: The writable fields are `kind`, `body`, `occurred_at`, `contact_id`, and `deal_id`. None is required. `kind` defaults to `NOTE` and `occurred_at` to now. `created_by` is the caller, and NULL for a machine caller.
- **FR-004**: `kind` MUST be an active `ACTIVITY_KIND` code that is not automatic. Otherwise the answer is 400.
- **FR-005**: `contact_id` and `deal_id`, when given, MUST name a contact or a deal of the same company. Otherwise the answer is 400.
- **FR-006**: `PATCH` and `DELETE` on an automatic entry get 409 `CONFLICT`. On a hand-logged entry they change it or delete it (204).
- **FR-007**: The automatic entries are written by the feature that owns each event. Stage changes of a company are written since feature 002. Deal stages, score changes, agreement statuses, imports, and AI runs add theirs in features 004, 007, 008, 009, and 013.

Tasks:

- **FR-008**: `GET /tasks` returns `{"items": [...], "next_cursor": ...}`, soonest due date first, tasks with no due date last. Filters: `status`, `company_id`, and `owner_id` are exact; `due_before` lists tasks whose `due_date` is earlier than the given date.
- **FR-009**: The writable fields are `title`, `company_id`, `deal_id`, `agreement_id`, `due_date`, `status`, and `owner_id`. Creating requires `title`. A patch that blanks the title gets 400.
- **FR-010**: `status` MUST be an active `TASK_STATUS` code. A `company_id`, `deal_id`, `agreement_id`, or `owner_id` that names nothing gets 400.
- **FR-011**: `completed_at` is set by the API only. It is set to now when the status becomes `DONE`, on create or on patch, and cleared when the status changes to anything else.
- **FR-012**: `DELETE /tasks/{id}` answers 204.

Companies:

- **FR-013**: `GET /companies/{id}` also returns `open_tasks`: the company's tasks with status `OPEN`, in the order of FR-008.

Common:

- **FR-014**: An id that names nothing gets 404.

Own records:

- **FR-017**: The two delete endpoints declare the action `WRITE`, so a `READER` is refused. The endpoint then allows the delete when the caller's role has the `DELETE` action, or when the record is the caller's own: `activity.created_by`, or `task.owner_id`, equals the caller's id. Otherwise the answer is 403. A record with no owner is nobody's own. The automatic-entry rule (FR-006) still applies to everyone.
- **FR-018**: A task created with no `owner_id` key is owned by the caller. An explicit null leaves it unowned. A machine caller has no id, so its tasks are unowned.

Dates:

- **FR-019**: Before a write, the API tries to put every date in the form `YYYY-MM-DD` and every moment in the form `YYYY-MM-DDTHH:MM:SSZ` in UTC. This holds for every endpoint, present and later, not only for activities and tasks: the rule is applied by column name where every body is read. In the data model every date column is named `*_date` and every moment column `*_at`. Today the writable ones are `task.due_date` and `activity.occurred_at`. A filter that takes a date, today `due_before`, gets the same treatment.
- **FR-020**: Understood forms: ISO 8601 (with or without a time or a zone), `M/D/YYYY`, `M/D/YY`, `M-D-YYYY`, `YYYY/M/D`, `Month D, YYYY`, and `D Month YYYY` with the month's name or its three-letter form, each optionally followed by a time as `HH:MM`, `HH:MM:SS`, or `H:MM AM`. Leading zeros are optional, so `12/01/2026`, `12-01-2026`, `01-01-2026`, and `01 Dec 2026` are all understood. A date that starts with the month and day is read month first. A time with a zone is converted to UTC. A time with no zone is taken as UTC. A date with no time becomes midnight UTC in `occurred_at`.
- **FR-021**: When no form of FR-020 fits, the API tries the text as a fuzzy phrase, such as `tomorrow`, `next week`, `next Tuesday`, `in 3 days`, or `friday at 3pm`, with the `parsedatetime` library (Apache 2.0, no dependencies of its own). The phrase is counted from the current time in UTC. A phrase is accepted only when the library understands all of it and it contains a letter, because the library reads bare numbers, even an impossible date such as `13/45/2026`, as a time. A phrase with no time of day gets the library's default, 09:00, which matters only for a moment column. Decided 2026-11-23.
- **FR-023**: A value that is still not understood is refused with 400, in a body and in a filter. Decided 2026-11-23. The value is not written to the log.
- **FR-022**: A later feature gets this for its body fields with no extra code. It MUST name a new date column `*_date` or `*_at`, as the data model does, and MUST pass a date filter through `iso_date` or `iso_timestamp`.

Tests:

- **FR-015**: The unit tests check that the code columns are writable, that the columns the API sets are not, the own-record rule for each role, the date forms of FR-020, fuzzy phrases counted from a fixed moment, and texts that must be refused.
- **FR-016**: `preview-test.sh` logs a note, is refused an automatic kind, finds the `STAGE_CHANGE` entry and is refused a change and a delete of it, creates a task with a date in another form and gets it back in ISO form, sees it on the company, marks it done, and lists it. Deleting the test company removes all of it, and the script checks that no task is left.

### Not in this feature

- Checking that a task's deal or agreement belongs to the task's company.
- Telling the UI that a date was translated from a fuzzy phrase, so the user can check it and change it. Requested 2026-11-23 for later. Until then the response carries the stored date, and nothing marks it as translated.
- A time zone on a company or a contact, so that a fuzzy phrase and a time with no zone are read in that zone instead of UTC. Requested 2026-11-23 for later. It needs a change to the common data model.
- Letting a `MEMBER` change only their own records. Any `MEMBER` may change any entry or task.
- A list of activities across companies.

### What is not tested

- A real `MEMBER` or `READER` through Cloudflare Access. In a local run, a `READER` was refused every write, and a `MEMBER` could log and change an entry and create a task. The `MEMBER` could delete their own note and task, and was refused another user's note, another user's task, and an unowned task. The service token on the preview is an `ADMIN`, so the smoke test cannot show a refusal.
- An entry that points to a deal, until feature 007 can create one.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company and no test task behind.
- **SC-003**: The check `Preview and smoke test` passes.
