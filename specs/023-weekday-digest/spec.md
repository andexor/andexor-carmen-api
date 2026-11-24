<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Weekday digest

**Feature Branch**: `46-weekday-digest`

**Created**: 2026-11-24

**Status**: Implemented, and live on the preview.

**Input**: Issue #46. The morning digest is sent on every weekday, and it is sent also when there is nothing to report, saying so. Decided 2026-11-24: the days are a setting, `digest.days`, added by migration `0011_digest_days` in `andexor-carmen-main` (its issue #36). Only production sends the email; the preview keeps its stub. This changes `specs/015-scheduler-and-notifications/spec.md`: its FR-003 loses the answer `EMPTY`, its FR-009 gains the day, and its FR-010 no longer leaves out an empty digest. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - A digest every working day (Priority: P1)

Every working morning one email arrives. On a quiet day it says that nothing needs attention, so a missing email always means that something is wrong, never that the day is empty.

**Independent Test**: `./unit-test.sh` covers the days and the text of an empty digest. `./email-test.sh` sends a real digest from the preview.

**Acceptance Scenarios**:

1. **Given** today is a day named in `digest.days`, the digest hour has come, and no digest was made today,\
   **When** the scheduler runs,\
   **Then** one digest is sent.
2. **Given** no task is due or overdue, no agreement is in its renewal window, and no lead waits for a score,\
   **When** the digest is made,\
   **Then** it is sent, and its body says that nothing needs attention today.
3. **Given** today is not a day named in `digest.days`,\
   **When** the scheduler runs,\
   **Then** no digest is made, and the answer is `NOT_DUE`.
4. **Given** a person asks for a run with `email=true`,\
   **When** the scheduler runs,\
   **Then** the digest is made whatever the day.

### Edge Cases

- The day is the local day in `digest.time_zone`. Friday evening in New York is still Friday, though it is Saturday in UTC.
- The setting is a list of three-letter English day names separated by commas. Upper and lower case and spaces do not matter. A name that is not a day is ignored.
- A missing setting, or one that names no day, means Monday to Friday.
- A public holiday on a weekday is a day like any other: the digest is sent.
- Renewal alerts and expiry are not affected. They run every day.

## Requirements

### Functional Requirements

- **FR-001**: A digest is made when today is a day named in the setting `digest.days`, the local hour is at or after `digest.hour_local`, and no digest was made since local midnight.
- **FR-002**: `digest.days` holds day names from `MON`, `TUE`, `WED`, `THU`, `FRI`, `SAT`, `SUN`. Its value is `MON,TUE,WED,THU,FRI`. Code holds the same list as the fallback for a missing or unreadable setting.
- **FR-003**: A digest is sent also when all four lists are empty. Its body then says: "Nothing needs you today: no task is due or overdue, no agreement is in its renewal window, and no lead is waiting for a score."
- **FR-004**: The answer `digest` of a run is `NOT_DUE`, `ALREADY_MADE`, or the status of the digest that was made. `EMPTY` is no longer an answer.
- **FR-005**: The provider is unchanged: `CLOUDFLARE` in production, and `STUB` in the preview, where a real email is sent only on demand with `./email-test.sh`.

### Endpoints

No endpoint is added or changed. `POST /api/v1/scheduler/run` (`MANAGE`) and `GET /api/v1/notifications` (`READ`) are as in feature 015.

### Not in this feature

- Leaving out public holidays.
- A separate hour for each day.
- Editing the setting through the API. Feature 016 adds that.

### What is not tested

- A digest made by the Cron Trigger on a weekday morning, and none on a Saturday. The rule is covered by unit tests.
- The setting read from the database with a value other than Monday to Friday.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes.
- **SC-003**: On the preview, `./email-test.sh` sent a real digest on a day with nothing to report, and it said so.

## Manual steps

- [ ] Merge the pull request for the migration in `andexor-carmen-main` (#37). The pipeline of this repo then applies it to the preview and to production. Until then the fallback, Monday to Friday, is used.
