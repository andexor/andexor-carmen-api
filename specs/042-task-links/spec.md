<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A task's deal and agreement belong to its company

**Feature Branch**: `98-task-links`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #98. Finding 14 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: check in the API, give a task with no company the company of its deal or agreement, and hold the same rule in the database. The database part is `specs/023-task-link-triggers/spec.md` in `andexor-carmen-main` (migration `0025_task_link_triggers`). This spec changes `specs/003-activities-and-tasks/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

A task names a company, a deal, and an agreement, and each was taken on its own. A task under company A could point to a deal of company B. That deal then looked planned in the digest's list of deals with nothing planned, an edit of the task was recorded on company A's timeline, the task was missing from company B's list, and deleting company A deleted it. A timeline entry and an agreement already refuse such a link; a task was the one record that did not. More people and more clients will write tasks, so the rule is checked before a write and held by the database too.

## User Scenarios and Testing

### User Story 1 - A task shows under the company it is about (Priority: P2)

**Independent Test**: The unit tests give `linked_company` a task with links of its own company, of another company, and with no company. `./preview-test.sh` sends a task of a second company with the first company's deal, a task with only the deal, and a change of that task's company.

**Acceptance Scenarios**:

1. **Given** a deal of company B,\
   **When** a task is created with company A and that deal,\
   **Then** it is answered 400, and no task is stored.
2. **Given** a deal of company B,\
   **When** a task is created with that deal and no company,\
   **Then** the task is stored with company B.
3. **Given** a task of company A with a deal of company A,\
   **When** its company is changed to company B and its deal is left,\
   **Then** it is answered 400, and the task is unchanged.
4. **Given** a task of company A with a deal of company A,\
   **When** its company and its deal are changed together to company B and a deal of company B,\
   **Then** the task is stored.
5. **Given** a task with no deal and no agreement,\
   **When** it is created or edited, with or without a company,\
   **Then** it is stored as before.
6. **Given** a task whose agreement is under another company than the task's,\
   **When** it is created or edited,\
   **Then** it is answered 400, as for a deal.

### Edge Cases

- A task with a deal whose company is taken away keeps its company: the deal gives it back. The edit changes nothing, and nothing is recorded for it.
- A task with no company that names a deal and an agreement of two companies is refused.
- A deal or an agreement that does not exist is answered 400, as before.
- A task of company A is not moved to company B by giving it a deal of company B alone. That is answered 400; the caller sends the company with it.
- A deal and an agreement never change company, so a stored task cannot come to disagree with them later.
- The scheduler's renewal task names the agreement and the agreement's own company, so it passes.

## Requirements

### Functional Requirements

- **FR-001**: `linked_company(database, task)` in `src/app.py` answers the company of a whole task. For each of `deal_id` and `agreement_id` that the task holds, it reads that record's company. A task with no company takes the company of its first link. It raises `BadBody` when a link does not exist or is under another company.
- **FR-002**: `POST /tasks` stores the task with the company that `linked_company` answers, and answers 400 when it raises.
- **FR-003**: `PATCH /tasks/{task_id}` checks the stored task with the changes laid over it, not only the changed fields. A company that the check fills in is stored and recorded as a change.
- **FR-004**: `db_status` answers 400 for the database's own refusal, `task link outside its company`, the message of the triggers `task_links_ins` and `task_links_upd`. The triggers are the last line of defense for a writer that does not go through these endpoints.
- **FR-005**: No stored task is changed. Both databases held no task with a link under another company.

### Endpoints

None are added, and no action changes: `POST /tasks` and `PATCH /tasks/{task_id}` are `WRITE`. Both may now answer 400 for a link under another company.

### Not in this feature

- A task that names a contact. A task has no contact.
- Moving a task to another company by its deal alone.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: a task of another company with the deal is answered 400, a task with only the deal has the deal's company, and its company cannot be changed while it keeps the deal.
- **SC-003**: Both databases are at schema version 25 and hold both triggers.
