<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Change log

**Feature Branch**: `25-deals`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #25, delivered with feature 007. Decided 2026-11-23: every edit and every delete goes to the Worker log and to the timeline. Features 005 and 007 did this for facts and deals. This spec makes it the rule for every kind of record, including those of features already delivered, and for every later feature. It has its own number after the backlog (001 to 016). Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Nothing is changed or removed without a trace (Priority: P1)

Whatever a user changes or deletes, what it said before can be found afterward.

**Independent Test**: The smoke test changes its test company's city and a task's title, and finds each old value on the timeline.

**Acceptance Scenarios**:

1. **Given** any record that belongs to a company,\
   **When** it is edited,\
   **Then** the Worker log, and then the company's timeline, name the record and each changed field with its old and new value.
2. **Given** any record that belongs to a company,\
   **When** it is deleted,\
   **Then** the Worker log, and then the company's timeline, name the record and the values it held.
3. **Given** an edit that the database refuses,\
   **When** the request ends,\
   **Then** the Worker log shows what was attempted, and the timeline shows nothing, because nothing happened.
4. **Given** a request that changes nothing,\
   **When** it ends,\
   **Then** nothing is recorded.

### User Story 2 - Records with no company (Priority: P2)

The timeline belongs to a company. A record that has none can only be logged.

**Acceptance Scenarios**:

1. **Given** a service, a rubric question, or a task with no company,\
   **When** it is edited or deleted,\
   **Then** the Worker log names the record and the change, and no timeline entry is written.
2. **Given** a company,\
   **When** it is deleted,\
   **Then** the Worker log names the company and what it held. Its timeline is deleted with it.

### Edge Cases

- The record is a timeline entry of kind `SYSTEM`. It is automatic, so nobody can change or delete it.
- Creating a record is not recorded here. The record itself, with its creation time, is the trace.
- A first answer to a rubric question is a creation. A changed or cleared answer is an edit.
- Marking a contact primary also clears the previous primary contact. Only the contact that was asked for is recorded.
- A stage change and a notable score change keep their own entry kinds, `STAGE_CHANGE` and `SCORE_CHANGE`. They now go to the Worker log first as well.
- Entries written within the same second can appear in either order on the timeline.

## Requirements

### What is recorded

| Record | Edit | Delete |
| --- | --- | --- |
| Company | Worker log and timeline; archive and unarchive too | Worker log only |
| Contact | Worker log and timeline | Worker log and timeline |
| Hand-logged timeline entry | Worker log and timeline | Worker log and timeline |
| Task with a company | Worker log and timeline | Worker log and timeline |
| Task with no company | Worker log only | Worker log only |
| Rubric answer | Worker log and timeline | Worker log and timeline (cleared) |
| Fact | Worker log and timeline | Worker log and timeline |
| Deal | Worker log and timeline | Worker log and timeline |
| Service | Worker log only | cannot be deleted |
| Rubric question | Worker log only | cannot be deleted |

### Functional Requirements

- **FR-001**: Every endpoint that edits or deletes a stored record MUST record the change. The order is fixed: the Worker log first, written before anything is sent to the database; then the timeline entry and the change, together in one transaction.
- **FR-002**: The Worker log line is one line of JSON with the event `carmen_audit`, the text of the change, the company id when there is one, the caller's id, and the request id.
- **FR-003**: The timeline entry has kind `SYSTEM`, the same text as its body, and the caller as `created_by`. It is the first statement of the transaction that makes the change.
- **FR-004**: The text of an edit names the record and, for each changed field, its old and new value. The text of a delete names the record and each value it held.
- **FR-005**: A record with no company has no timeline entry. This covers services, rubric questions, and tasks with no company.
- **FR-006**: The delete of a company is written to the Worker log only, because its timeline is deleted with it.
- **FR-007**: A change that leaves every value as it was records nothing and writes nothing. Archiving an archived company, and unarchiving one that is not archived, are such changes.
- **FR-008**: Stage changes of companies and deals, and notable score changes, are written to the Worker log before their own timeline entries.
- **FR-009**: Every later feature that adds an endpoint that edits or deletes a record MUST follow this spec, and says so in its own spec.
- **FR-010**: The unit tests cover the text of a record. `preview-test.sh` edits a company and a task and finds each record on the timeline, in addition to the fact and deal checks.

### Not in this feature

- A record on the timeline for records with no company. The timeline's company is required by the data model; changing that starts in the shared data model.
- Keeping the record of a deleted company anywhere but the Worker log.
- Recording who read what.
- Recording creations.

### What is not tested

- How long the Worker log keeps a line. That is set by Cloudflare's log retention, not by this code.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes.
- **SC-002**: `./preview-test.sh` passes, including the change log checks.
- **SC-003**: In a local run, an edit and a delete of each kind of record in the table left the records the table names.
