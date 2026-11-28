<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: An email is recorded before it is sent

**Feature Branch**: `86-record-email-first`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #86. Finding 9 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: record first and send second, let only one of two runs at the same moment send, and name in the digest a message that may not have been sent. This spec changes `specs/015-scheduler-and-notifications/spec.md`, `specs/023-weekday-digest/spec.md`, `specs/025-service-proposals/spec.md`, and `specs/036-proposals-once/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

Carmen knows that an email is still due by the absence of its record. The email was sent first and its record was stored second. When the record could not be stored, the email was in the inbox with no record, and the scheduler sent it again every hour until a record was stored. Two runs of the scheduler at the same moment both found the email due and both sent it. Today every email goes to Ed. Emails to contacts are planned, and a customer must never get the same notice twice.

## User Scenarios and Testing

### User Story 1 - An email whose record cannot be stored is not sent (Priority: P1)

**Independent Test**: The unit tests send a message with a database that cannot write the record, and with one that cannot write the result.

**Acceptance Scenarios**:

1. **Given** a renewal alert is due,\
   **When** the scheduler runs,\
   **Then** the record is stored as `QUEUED` together with the alert's task, the email is sent, and the record becomes `SENT`, or `STUBBED` where the provider is the stub.
2. **Given** the record cannot be stored,\
   **When** the scheduler runs,\
   **Then** nothing is sent, no task is stored, the step is named as failed, and the next run tries again.
3. **Given** the provider refuses the email,\
   **When** the scheduler runs,\
   **Then** the record becomes `FAILED` with the reason, and the email is not tried again, as before.
4. **Given** the email is sent and its result cannot be stored,\
   **When** the scheduler next runs,\
   **Then** the record is still `QUEUED`, and the email is not sent again.

### User Story 2 - Of two runs at the same moment, one sends (Priority: P2)

**Independent Test**: The unit tests send a message that another run has recorded.

**Acceptance Scenarios**:

1. **Given** the hourly run and a run started by an administrator both find the same renewal alert due,\
   **When** both record it,\
   **Then** one record and one task are stored, and one email is sent.
2. **Given** two runs both find today's digest due,\
   **When** both record it,\
   **Then** one digest is sent, and the other run answers `ALREADY_MADE`.
3. **Given** the queue delivers a proposals job twice and both deliveries reach the email at the same moment,\
   **When** both record it,\
   **Then** one email is sent.
4. **Given** an administrator asks for a digest now, or for a fresh proposals job,\
   **When** the email is recorded,\
   **Then** it is sent whatever was sent before, as before.

### User Story 3 - The digest names a message that may not have been sent (Priority: P2)

**Independent Test**: The unit tests make the digest's section from one such record. The query is run by every digest; `./email-test.sh` makes one on the preview.

**Acceptance Scenarios**:

1. **Given** a record that is still `QUEUED` more than an hour after it was stored,\
   **When** the next digest is made,\
   **Then** the digest has the section "Messages that may not have been sent", with the subject of the message and the time it was recorded.
2. **Given** no such record,\
   **When** the digest is made,\
   **Then** the section is left out.
3. **Given** a record that one digest has named,\
   **When** the digest after it is made,\
   **Then** that record is not named again.

### Edge Cases

- A run that is cut off after the record and before the send leaves a `QUEUED` record and no email. The email is not sent later: the trade is at most once, where it was at least once. For an alert the task is stored, and the digest lists the agreement in its renewal window, so the agreement stays in view.
- A record is named in the first digest that is made after it, also one that an administrator asked for. A digest that is itself cut off is named by the next one.
- "The same message" is the same kind and the same agreement, recorded at or after a moment the caller gives: the day before the notice date for an alert, the start of the local day for a digest, and the start of the job for a proposals email.
- A recipient with no name is logged and sent to, as before.

## Requirements

### Functional Requirements

- **FR-001**: `notification(...)` stores the record with the status `QUEUED`, then sends the email, then updates the record with the status, the provider's reference, the time it was sent, or the error. It answers the row as it is stored.
- **FR-002**: The record is one `INSERT` that stores nothing when `since` is given and a record of the same kind and agreement exists that was created at or after `since`. `notification(...)` then sends nothing and answers none.
- **FR-003**: A task given to `notification(...)` is stored in the same batch as the record, and only when the record was stored.
- **FR-004**: A record that cannot be stored raises, so the caller's step fails and nothing is sent. A result that cannot be stored is logged and does not raise; the answer is the record as `QUEUED`.
- **FR-005**: `renewal_alerts` answers the number of alerts this run made. `daily_digest` answers `ALREADY_MADE` when another run recorded today's digest. The proposals job logs "already recorded".
- **FR-006**: The digest has the list `unsent`: every record that is `QUEUED`, was created more than an hour ago, and was created later than one hour before the digest before this one. Its heading is "Messages that may not have been sent", and a line is the subject and the time of the record.
- **FR-007**: No status is added. `QUEUED` is in the domain `NOTIFICATION_STATUS` since the first migration and was not used.

### Endpoints

None are added, and no action changes. `GET /notifications` (`READ`) may show a record with the status `QUEUED`.

### Not in this feature

- Sending a `QUEUED` message later. Whether it went out is not known, and sending it again is what this feature ends.
- A way to mark a `QUEUED` record as dealt with.

### What is not tested

- Two runs at the same moment, and a run that is cut off: neither can be produced on the preview. The statement that decides is run by every email on the preview, and the unit tests cover each outcome.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: an alert and a proposals email are stored as `STUBBED` with their task and their runs, and a second run makes no second alert.
- **SC-003**: `./email-test.sh` sends one digest from the preview, and its record is `SENT`.
