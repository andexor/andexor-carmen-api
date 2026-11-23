<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Today

**Feature Branch**: `23-today`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #23. Backlog feature 006: one call for the Today view, the home screen. It reads what earlier features store and writes nothing. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - What needs attention today (Priority: P1)

A salesperson opens the app and sees, from one request, the follow-ups that are due, the leads waiting to be scored, the leads worth a call, the deals with nothing planned, the agreements coming up for renewal, and the AI runs still working.

**Independent Test**: The smoke test gives its test company an overdue task and finds it in the answer, with all seven lists present.

**Acceptance Scenarios**:

1. **Given** open tasks due yesterday, today, and tomorrow, and one with no date,\
   **When** `GET /api/v1/today` is requested,\
   **Then** yesterday's is in `tasks_overdue`, today's is in `tasks_due`, and the other two are in neither.
2. **Given** a lead with no score, a lead recommended `MAYBE`, and a lead recommended `SKIP`,\
   **When** the answer is read,\
   **Then** the first is in `leads_unscored`, the second is in `top_leads`, and the third is in neither.
3. **Given** an open deal with no open task, an open deal with one, and a won deal,\
   **When** the answer is read,\
   **Then** only the first is in `deals_without_task`.
4. **Given** active agreements that end in 10 days and in 40 days, with a default notice of 30 days,\
   **When** the answer is read,\
   **Then** the first is in `agreements_renewing` and the second is not, unless its own notice is 40 days or more.
5. **Given** runs that are queued, running, and failed,\
   **When** the answer is read,\
   **Then** the first two are in `runs_in_progress`.

### User Story 2 - Today is the user's today (Priority: P1)

The day changes at midnight where the business is, not at midnight UTC.

**Acceptance Scenarios**:

1. **Given** the setting `digest.time_zone` is `America/New_York`,\
   **When** it is 22:30 there and already the next day in UTC,\
   **Then** `date` is the New York date, and a task due on that date is due, not overdue.
2. **Given** a time zone name that does not exist,\
   **When** the answer is read,\
   **Then** `date` is the UTC date and `time_zone` is `UTC`, so the caller can see what was used.

### Edge Cases

- A done or cancelled task is in neither task list, whatever its date.
- An archived company is in neither lead list. A company that is not a `LEAD` is in neither.
- A lead whose answers were all cleared has no score again, so it is back in `leads_unscored`.
- An active agreement past its end date stays in `agreements_renewing` until its status changes.
- An agreement with no end date is never renewing.
- Deals, agreements, and runs cannot be created through the API yet. Their lists are empty until features 007, 008, and 009, and need no change then.

## Requirements

### Endpoint and its action

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /api/v1/today` | `READ` | Everything for the Today view, in one answer |

### Functional Requirements

- **FR-001**: The answer is one object with `date`, `time_zone`, and seven lists: `tasks_overdue`, `tasks_due`, `leads_unscored`, `top_leads`, `deals_without_task`, `agreements_renewing`, and `runs_in_progress`. A list with nothing in it is an empty list.
- **FR-002**: `date` is the current date, as `YYYY-MM-DD`, in the time zone named by the setting `digest.time_zone`. `time_zone` is the zone that was used. A missing or unknown zone falls back to UTC.
- **FR-003**: The date is computed in pure Python with `zoneinfo`, from the current moment in UTC and the named zone. It does not depend on where the Worker runs or on the Worker's own clock zone. Python inside a Worker has no time zone data of its own, so the `tzdata` package (the standard time zone database, data only, no dependencies) is a dependency. Decided 2026-11-23.
- **FR-004**: `tasks_overdue` holds tasks with status `OPEN` and a `due_date` before `date`. `tasks_due` holds those due on `date`. Both are ordered by due date, and each task carries `company_name`, which is null for a task with no company.
- **FR-005**: `leads_unscored` holds unarchived companies in stage `LEAD` with neither a Fit nor a Need score, newest first.
- **FR-006**: `top_leads` holds unarchived companies in stage `LEAD` whose recommendation is `REACH_OUT` or `MAYBE`: `REACH_OUT` first, then by the sum of Fit and Need, highest first.
- **FR-007**: `deals_without_task` holds deals whose stage is neither `WON` nor `LOST` and that have no task with status `OPEN`, the longest untouched first, each with `company_name`.
- **FR-008**: `agreements_renewing` holds agreements with status `ACTIVE` and an `end_date`, whose notice date is on or before `date`. The notice date is the end date minus the agreement's `renewal_notice_days`, or minus the setting `renewal.default_notice_days` (30 when that is missing). Each carries `company_name` and `notice_date`, ordered by end date.
- **FR-009**: `runs_in_progress` holds AI runs with status `QUEUED` or `RUNNING`, oldest first, with `id`, `company_id`, `company_name`, `kind`, `ai_model_id`, `status`, `created_at`, and `started_at`. The dossier is left out.
- **FR-010**: `limit` caps every list: 1 to 200, default 50. A value outside that gets 400. There is no paging; a list that is full has more.
- **FR-011**: The lists cover all users. Nothing is filtered by owner.
- **FR-012**: The unit tests cover the date in a time zone and the fallback, with the same code that runs in the Worker. `preview-test.sh` gives its test company an overdue task, finds it in the answer, checks that all seven lists are present, and checks that `date` is today in the reported zone and that the zone is not the UTC fallback, which proves that the time zone data is present in the Worker.

### Not in this feature

- Filtering by owner ("my day"). It matters once there is a second user.
- Counts of how many more a full list has.
- A time zone per user, company, or contact. The one setting is used.

### What is not tested

- The deal, agreement, and run lists on the preview, where no such rows can exist yet. In a local run they were filled with rows written straight to the database, and each list held exactly the rows it should.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company and no test task behind.
- **SC-003**: The check `Preview and smoke test` passes.
