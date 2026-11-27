<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A failed rescoring is made again

**Feature Branch**: `80-rescore-retry`

**Created**: 2026-11-27

**Status**: Implemented, and live on the preview.

**Input**: Issue #80. Finding 6 of the audit of 2026-11-27. Ed chose the fix on 2026-11-27: record that a rescoring is waiting, and have the scheduler make it. This spec changes `specs/004-services-rubric-and-scoring/spec.md`, `specs/015-scheduler-and-notifications/spec.md`, and `specs/016-settings/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

A change to a rubric question, to a service, or to a score threshold changes the scores of every company. The change was stored first and the scores were recomputed after it, as two separate writes. When the recomputing failed, the caller got an error, and sending the same change again did nothing, because the change was already stored. The rubric was new and the scores were old, or mixed, and nothing showed it. Recommendations, the digest, and alerts all read the stored scores.

## User Scenarios and Testing

### User Story 1 - The scores follow the rubric, also after a failure (Priority: P1)

**Independent Test**: The unit tests run a rescoring with a request waiting and with none. `./preview-test.sh` changes a threshold and finds no request left, then leaves a request and finds that the scheduler carried it out and cleared it.

**Acceptance Scenarios**:

1. **Given** a change to the weight of a rubric question,\
   **When** the change is stored,\
   **Then** the setting `rescore.requested` is set in the same transaction: both are stored, or neither.
2. **Given** the rescoring after that change succeeds,\
   **When** the settings are listed,\
   **Then** `rescore.requested` is empty.
3. **Given** the rescoring after that change fails,\
   **When** the scheduler next runs,\
   **Then** every company with answers is rescored, the run reports how many, and `rescore.requested` is empty.
4. **Given** the scheduler's rescoring fails too,\
   **When** the run ends,\
   **Then** the run names the step `rescore` as failed, the request stays, and the next run tries again.
5. **Given** a second change is stored while a rescoring is running,\
   **When** that rescoring ends,\
   **Then** the request of the second change stays, and is cleared only by a rescoring that began after it.

### User Story 2 - A person can ask for a rescoring at the next run (Priority: P3)

**Independent Test**: `./preview-test.sh` sets `rescore.requested` with `PATCH /settings` and runs the scheduler.

**Acceptance Scenarios**:

1. **Given** an administrator sets `rescore.requested` to any text,\
   **When** the scheduler next runs,\
   **Then** every company with answers is rescored, and the setting is empty.

### Edge Cases

- The caller of a change whose rescoring failed still gets an error, as before. The change is stored, and the scores follow within the hour. Sending the change again still does nothing, and no longer needs to.
- `POST /rubric/recompute` clears a request too, when it succeeds.
- A database with no setting `rescore.requested` behaves as before this feature: the change is stored, nothing is recorded, and nothing fails.
- The request cannot be emptied with `PATCH /settings`, which refuses an empty value. Only a rescoring empties it.
- Two changes at the same moment each run their own rescoring. What two rescorings at once do to the scores is finding 12 of the audit, not this feature.

## Requirements

### Functional Requirements

- **FR-001**: The request is the setting `rescore.requested`, created by the migration `0021_rescore_requested_setting` in `andexor-carmen-main` (issue #69 there). Empty means that nothing is waiting.
- **FR-002**: `rescore_mark` gives the statement that sets the request. Its value is the time and a tag, so that two requests never write the same value.
- **FR-003**: The statement is in the same `batch` as the change in: `POST /rubric`; `PATCH /rubric/{criterion_id}` when a column that changes scores changes; `PATCH /services/{service_id}` when `offered` or `active` changes; and `PATCH /settings` when a `score.*` setting changes.
- **FR-004**: `rescore_all` reads the request before it reads the rubric, and clears it in the same batch as its last scores, only where the setting still holds the value it read.
- **FR-005**: The scheduler has the step `rescore`: when the request is set, it calls `rescore_all`. The run's answer has `rescored`, the number of companies rescored, which is 0 when nothing was waiting. A failure is logged, named in `failed`, and does not stop the other steps.
- **FR-006**: A rescoring by the scheduler records a notable score change on the company's timeline as any rescoring does, with no person as its author.

### Endpoints

None are added, and no action changes. `POST /scheduler/run` (`MANAGE`) answers with `rescored` too. `GET /settings` and `PATCH /settings` (`MANAGE`) list and accept the new setting.

### Not in this feature

- Moving the rescoring out of the request to the `RUNS` queue. Ed decides that next.
- A different answer for a change whose rescoring failed.

### What is not tested

- A rescoring that fails on a deployed Worker: a failed database write cannot be produced on the preview. The scheduler's step is tested with a request that a person left.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: a threshold change leaves no request, and a request that was left is carried out by the scheduler and cleared.
- **SC-003**: Both databases hold the setting `rescore.requested`.
