<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Renewal policy

**Feature Branch**: `48-renewal-policy`

**Created**: 2026-11-24

**Status**: Implemented, and live on the preview.

**Input**: Issue #48. What happens when an agreement reaches its end is a policy that a person chooses for that agreement, with no default: some agreements are one-time work, most renew every month, and some run for a term and then need approval to renew. Decided 2026-11-24, question by question: a renewal on approval that nobody approved expires; an agreement that renews automatically has no end date; the policy is required before an agreement becomes active, so a draft may lack it; a one-time agreement gets an alert and is then completed; the end basis is a choice with a reason; approving a renewal is its own action. The data model change is migration `0012_renewal_policy` in `andexor-carmen-main` (its issue #38). This changes `specs/008-agreements-and-documents/spec.md` (the field `auto_renews` is gone, and four fields are new) and `specs/015-scheduler-and-notifications/spec.md` (its expiry step). Where this spec and an earlier document differ, this spec governs.

## The design

```
   renewal_policy      end date            before the end           after the end date
   --------------      --------            --------------           ------------------
   ONE_TIME            optional            alert and task           COMPLETED
                                           "Ending: <title>"

   AUTO_RENEW          none                nothing                  nothing: it runs until
                                                                    a person terminates it

   APPROVAL            required,           alert and task           EXPIRED, unless the
                       with term_months    "Renewal: <title>"       renewal was approved

   approve the renewal:  POST /agreements/{id}/renewal
                         end date = end date + term_months, the timeline says who approved
```

## User Scenarios and Testing

### User Story 1 - A policy is chosen on purpose (Priority: P1)

Before an agreement becomes active, someone decides how it ends and what that rests on. Carmen never fills that in.

**Independent Test**: `./unit-test.sh` covers every rule. `./preview-test.sh` has an agreement refused five ways before it is accepted.

**Acceptance Scenarios**:

1. **Given** an agreement with no renewal policy or no end basis,\
   **When** its status is set to `ACTIVE`,\
   **Then** the answer is 400, and the agreement is unchanged.
2. **Given** a draft agreement,\
   **When** it is created or edited with no renewal policy,\
   **Then** it is accepted.
3. **Given** an end basis of `INTERNAL_DECISION` and no reason,\
   **When** the agreement is made active,\
   **Then** the answer is 400.
4. **Given** the policy `AUTO_RENEW` and an end date,\
   **When** the agreement is made active,\
   **Then** the answer is 400.
5. **Given** the policy `APPROVAL` without an end date or without a term in months,\
   **When** the agreement is made active,\
   **Then** the answer is 400.

### User Story 2 - The scheduler follows the policy (Priority: P1)

Each agreement ends the way its policy says, without anyone watching the calendar.

**Independent Test**: `./preview-test.sh` creates one agreement of each ending kind, runs the scheduler, and reads the statuses, the alert, and the task.

**Acceptance Scenarios**:

1. **Given** an active `ONE_TIME` agreement past its end date,\
   **When** the scheduler runs,\
   **Then** its status is `COMPLETED`, and the change is recorded.
2. **Given** an active `APPROVAL` agreement past its end date,\
   **When** the scheduler runs,\
   **Then** its status is `EXPIRED`, and the change is recorded.
3. **Given** an active `AUTO_RENEW` agreement,\
   **When** the scheduler runs, on any day,\
   **Then** nothing happens to it, and no alert is made.
4. **Given** an active `ONE_TIME` agreement that reaches its notice date,\
   **When** the scheduler runs,\
   **Then** one alert is sent and one task "Ending: <title>" is created.
5. **Given** an active `APPROVAL` agreement that reaches its notice date,\
   **When** the scheduler runs,\
   **Then** one alert is sent and one task "Renewal: <title>" is created.

### User Story 3 - A renewal is approved (Priority: P1)

A person approves the renewal with one action, and the agreement runs for another term.

**Independent Test**: `./preview-test.sh` approves a renewal and reads the new end date, the timeline, and the task.

**Acceptance Scenarios**:

1. **Given** an active `APPROVAL` agreement with an end date and a term,\
   **When** its renewal is approved,\
   **Then** the end date moves forward by the term, the timeline says so and who approved it, and the open "Renewal: ..." task of the agreement is done.
2. **Given** an agreement that is not active, or whose policy is not `APPROVAL`,\
   **When** its renewal is approved,\
   **Then** the answer is 409.
3. **Given** a renewal that was approved,\
   **When** the new notice date comes,\
   **Then** a new alert and a new task are made for the new term.

### Edge Cases

- A term is added in whole months. A day the later month does not have becomes its last day: January 31 and one month is February 28.
- A renewal can be approved at any time while the agreement is active, also long before its notice date, and more than once.
- An expired agreement is not renewed by approval. A person sets it back to active with a new end date.
- A `ONE_TIME` agreement with no end date gets no alert and is never completed by the scheduler. A person marks it completed.
- The rules are checked when the agreement is active after the write. An agreement with any other status may hold any mix of these fields.
- An active agreement with no policy cannot exist through the API. If one is put in the database another way, the scheduler leaves it alone.
- A refusal is a 400 with no detail in the answer, as everywhere. The reason is in the Worker log.

## Requirements

### Functional Requirements

The fields:

- **FR-001**: An agreement has four new writable fields: `renewal_policy` (a `RENEWAL_POLICY` code), `end_basis` (an `END_BASIS` code), `end_basis_reason` (text), and `term_months` (a whole number of at least 1). None has a default. The field `auto_renews` no longer exists.
- **FR-002**: An agreement whose status is `ACTIVE` after a create or an edit MUST have a `renewal_policy` and an `end_basis`.
- **FR-003**: With `end_basis` `INTERNAL_DECISION`, an active agreement MUST have an `end_basis_reason` that is not blank.
- **FR-004**: With the policy `AUTO_RENEW`, an active agreement MUST NOT have an `end_date`.
- **FR-005**: With the policy `APPROVAL`, an active agreement MUST have an `end_date` and `term_months`.
- **FR-006**: The rules are in one pure function, `records.renewal_problem`. The schema checks only the codes and the term.

The scheduler:

- **FR-007**: The first step of a run ends agreements: an active agreement whose `end_date` is before today becomes `COMPLETED` when its policy is `ONE_TIME`, and `EXPIRED` when its policy is `APPROVAL`. Each change is recorded as in feature 015.
- **FR-008**: The answer of a run has `completed` beside `expired`.
- **FR-009**: The alert and its task say what the alert is about: "Ending" for `ONE_TIME`, and "Renewal" for `APPROVAL`. The alert says what will happen after the end date.

Approving a renewal:

- **FR-010**: `POST /api/v1/agreements/{id}/renewal` sets the `end_date` to the old one plus `term_months`, for an active agreement with the policy `APPROVAL`, an end date, and a term. Anything else is refused with 409.
- **FR-011**: The approval is recorded: the Worker log first, then a `SYSTEM` entry on the company's timeline with the old and the new end date and the person who approved, in one transaction with the change.
- **FR-012**: In the same transaction, the open tasks of the agreement whose title begins with "Renewal:" are marked done.

### Endpoints

| Method and path | Action | Does |
| --- | --- | --- |
| `POST /api/v1/agreements` | `WRITE` | As before, with the new fields and the rules for an active agreement |
| `PATCH /api/v1/agreements/{id}` | `WRITE` | As before, with the new fields and the rules for an active agreement |
| `POST /api/v1/agreements/{id}/renewal` | `WRITE` | Approves the renewal |

### Not in this feature

- An email that proposes other services to a customer whose one-time agreement was completed, with the research and the scores behind each proposal, and the same list on the Company screen. It is the next feature; see `NOTES.md`.
- Moving a company from customer to former customer when the scheduler ends its last active agreement. An edit by a person does that; the scheduler does not yet.
- A term in anything but months, and a policy that renews automatically for a fixed number of periods.
- Approval that only an administrator may give.
- A message in the answer that says which rule refused an agreement.

### What is not tested

- An alert for a `ONE_TIME` agreement on a deployed Worker. Its wording is covered by unit tests, and the alert for an `APPROVAL` agreement by the smoke test.
- A new alert for the new term after an approval. The rule that an alert counts only from the day before its notice date is the one of feature 015.
- An agreement that renews automatically over many days. The smoke test checks only that it is accepted without an end date.
- The migration on production. The pipeline applies it with this code.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: an agreement is refused without a policy, without a basis, without a reason, with an end date it may not have, and without a term; a run completes one agreement, expires another, and alerts for a third; a renewal is approved.

## Manual steps

- [ ] Merge the pull request for the migration in `andexor-carmen-main` (#39) before the pull request of this feature is opened: the preview database already has the migration, and the pipeline checks the database against `main` there.
