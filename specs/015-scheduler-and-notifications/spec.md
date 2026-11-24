<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Scheduler and notifications

**Feature Branch**: `44-scheduler-and-notifications`

**Created**: 2026-11-24

**Status**: Implemented, and live on the preview.

**Input**: Issue #44. Backlog feature 015: once an hour the Worker looks at what is due. It sends a daily digest, alerts before an agreement ends or renews, and marks an ended agreement as expired. Email goes through Cloudflare Email Sending, as decided in `andexor-carmen-main/specs/001-review-open-items/decisions.md` (Q5). Where this spec and an earlier document differ, this spec governs.

## The design

```
   Cron Trigger, every hour            an administrator, or the smoke test
              |                                      |
              |  scheduled                           |  POST /api/v1/scheduler/run
              v                                      v
   +------------------------------------------------------------------+
   |  run_scheduler, written once                                     |
   |                                                                  |
   |  1. expire      an ended agreement becomes EXPIRED               |
   |  2. alert       one alert and one task per agreement             |
   |  3. digest      once a day, at or after the digest hour          |
   +------------------------------------------------------------------+
              |                                      |
              v                                      v
      the provider sends the message      a notification row records it
      CLOUDFLARE: a real email            SENT, STUBBED, or FAILED
      STUB: the Worker log only
```

The code is the same in the preview and in production. The one difference is the variable `EMAIL_PROVIDER`: `CLOUDFLARE` in production, and `STUB` in the preview, so that the hourly runs and the pipeline never send test data to an inbox. A person who is testing asks for real email, one run at a time: `./email-test.sh`.

## User Scenarios and Testing

### User Story 1 - The daily digest (Priority: P1)

Each morning one email lists what needs attention, so the day starts without opening the application.

**Independent Test**: `./unit-test.sh` covers when the digest is due and what it says. `./preview-test.sh` runs the scheduler and reads its answer.

**Acceptance Scenarios**:

1. **Given** the local time in `digest.time_zone` is at or after `digest.hour_local`, and no digest was made today,\
   **When** the scheduler runs,\
   **Then** one digest is sent to `digest.recipient` and stored as a notification.
2. **Given** a digest was already made today,\
   **When** the scheduler runs again,\
   **Then** no second digest is made.
3. **Given** no task is due or overdue, no agreement is in its renewal window, and no lead waits for a score,\
   **When** the scheduler runs,\
   **Then** nothing is sent and nothing is stored.
4. **Given** the local time is before the digest hour,\
   **When** the scheduler runs,\
   **Then** no digest is made.

### User Story 2 - An alert before an agreement ends (Priority: P1)

Some days before an agreement ends, one email says so and one task appears, so a renewal is never a surprise.

**Independent Test**: `./preview-test.sh` creates an active agreement that ends in 10 days with 30 notice days, runs the scheduler, and reads the alert and the task.

**Acceptance Scenarios**:

1. **Given** an active agreement whose notice date is today or earlier and whose end date has not passed,\
   **When** the scheduler runs,\
   **Then** one alert is sent, and one open task "Renewal: <title>" is created, due on the notice date, for the agreement's owner.
2. **Given** an agreement that already has its alert,\
   **When** the scheduler runs again,\
   **Then** no second alert and no second task are made.
3. **Given** an agreement with no notice days of its own,\
   **When** its notice date is worked out,\
   **Then** the setting `renewal.default_notice_days` is used.

### User Story 3 - An ended agreement expires (Priority: P2)

An agreement that has ended and does not renew by itself stops being listed as active, without anyone editing it.

**Independent Test**: `./preview-test.sh` creates an active agreement that ended 3 days ago, runs the scheduler, and reads its status and the company's timeline.

**Acceptance Scenarios**:

1. **Given** an active agreement past its end date that does not renew automatically,\
   **When** the scheduler runs,\
   **Then** its status is `EXPIRED`, and the change is in the Worker log and on the company's timeline.
2. **Given** an active agreement past its end date that renews automatically,\
   **When** the scheduler runs,\
   **Then** it stays active.

### Edge Cases

- The digest is due at its hour or at any later hour of the same local day. A run that was missed is made up by the next one.
- "Today" is the date in `digest.time_zone`, as in the Today view. A missing or unknown zone falls back to UTC, and a missing or unreadable hour to 7.
- An alert that was missed on its notice date is sent on the next run, as long as the agreement has not ended.
- A new end date makes a new notice date, so a renewed agreement gets a new alert for its new term.
- A message that the provider refuses is stored with the status `FAILED` and the reason, and is not tried again. For an alert, the task is still created.
- With no `digest.recipient`, agreements still expire, nothing is sent, and the run names `recipient` as failed.
- One run handles at most 50 expiries and 50 alerts, and a digest shows at most 50 rows of each list. The rest waits for the next run.
- A step that fails does not stop the other steps. The run names it in `failed`, and the detail is in the Worker log.

## Requirements

### Discovery result

- **Answered 2026-11-24, on the preview**: the `send_email` binding takes a JavaScript object from Python, `await env.EMAIL.send(to_js({...}, dict_converter=Object.fromEntries))`, with `to`, `from`, `subject`, and `text`. It returns an object with `messageId`. One real renewal alert was sent this way and accepted by the provider.
- A `.txt` file under `src/` is packaged with the Worker by default. It needs no entry under `rules` in `wrangler.jsonc`; adding one gives a "duplicate module" warning.

### Functional Requirements

The scheduler:

- **FR-001**: A Cron Trigger runs the scheduler every hour, on the hour, in the preview and in production.
- **FR-002**: One run does three steps in this order: expire, alert, digest. It never raises. It answers `expired` and `alerts` (counts), `digest`, and `failed` (the names of the steps that failed).
- **FR-003**: `digest` is `NOT_DUE`, `ALREADY_MADE`, `EMPTY`, or the status of the digest that was made.

Expiry:

- **FR-004**: An agreement with the status `ACTIVE`, with `auto_renews` off, and with an `end_date` before today becomes `EXPIRED`.
- **FR-005**: The change is recorded as every edit is: a line in the Worker log first, then a `SYSTEM` entry on the company's timeline in the same transaction as the change. The entry has no author, and the log line names the scheduler.

Renewal alerts:

- **FR-006**: The notice date of an agreement is its `end_date` less its `renewal_notice_days`, or less `renewal.default_notice_days` when it has none.
- **FR-007**: An active agreement gets an alert when its notice date is today or earlier, its end date is today or later, and it has no alert made since the day before its notice date.
- **FR-008**: With the alert, an open task is created: the title "Renewal: <title>" (the type when there is no title), the agreement's company and owner, the agreement, and the notice date as the due date. The notification and the task are stored in one transaction.

The daily digest:

- **FR-009**: A digest is made when the local hour is at or after `digest.hour_local` and no digest was made since local midnight.
- **FR-010**: It lists tasks due today, tasks overdue, agreements in their renewal window, and leads waiting for a score: the same lists as the Today view. A list that is empty is left out, and no digest is made when all four are empty.

Sending:

- **FR-011**: A provider takes a recipient, a subject, and a body, and returns the provider's reference. `CLOUDFLARE` sends an email through the `send_email` binding `EMAIL`, from the address in the variable `EMAIL_FROM`. `STUB` sends nothing and writes the message to the Worker log.
- **FR-012**: The variable `EMAIL_PROVIDER` names the provider. Any other value, or none, is the stub.
- **FR-016**: A run that is asked for with `email=true` sends real email whatever `EMAIL_PROVIDER` says, and makes the digest now: the hour and an earlier digest of the day do not matter. The Cron Trigger and the smoke test never ask for it.
- **FR-013**: Every message is stored as a `notification` row: `SENT` with `provider_ref` and `sent_at`, `STUBBED`, or `FAILED` with `error`.
- **FR-014**: The recipient is the setting `digest.recipient`. No address is written in code.
- **FR-015**: A message is plain text. Its template is a file in `src/templates/email/`: the first line is the subject, the rest is the body, and a placeholder is `{{name}}`.

### Endpoints

| Method and path | Action | Does |
| --- | --- | --- |
| `POST /api/v1/scheduler/run` | `MANAGE` | Runs the scheduler now and returns its answer. `email=true`: with real email, and the digest now |
| `GET /api/v1/notifications` | `READ` | Lists notifications, newest first. Filters: `kind`, `agreement_id` |

### Configuration

In `wrangler.jsonc`, in both environments: `triggers.crons` (`0 * * * *`), the `send_email` binding `EMAIL`, and the variables `EMAIL_PROVIDER` and `EMAIL_FROM`.

### Not in this feature

- Email in the HyperText Markup Language (HTML) with a branded template. Messages are plain text for now; the wish is parked in `NOTES.md`.
- Sending to anyone but `digest.recipient`.
- Trying a failed message again.
- An agreement that renews automatically and has passed its end date: it stays active with its old end date until a person gives it a new one.
- Deleting a notification.
- The scheduled uptime check of prospects' websites that is parked in `NOTES.md`.

### What is not tested

- The Cron Trigger itself. The smoke test calls the endpoint, which runs the same code.
- A real email in the pipeline. The preview uses the stub. Real email was sent by hand, with `./email-test.sh`.
- A digest made at the digest hour by the trigger. The rule for the hour is covered by unit tests.
- A message that the provider refuses.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: a run expires an ended agreement and records it, makes one alert and one task for an agreement in its notice period, and a second run makes no second alert.
- **SC-003**: On the preview, `./email-test.sh` sent a real digest, and the provider accepted it.

## Manual steps

- [ ] Confirm that the test alert of 2026-11-24, "Renewal: Test agreement from the Carmen preview", reached the inbox.
- [ ] After the first morning in production with something to report, confirm that the digest reached the inbox. That closes master question Q5.
