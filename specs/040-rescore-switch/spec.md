<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A switch for automatic rescoring

**Feature Branch**: `94-rescore-switch`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #94. Asked for by Ed on 2026-11-28: "is there a way I could turn off auto re-scoring, make several changes to the rubric, and then turn auto re-scoring back on again and run a full re-scoring?" This spec changes `specs/004-services-rubric-and-scoring/spec.md`, `specs/015-scheduler-and-notifications/spec.md`, `specs/016-settings/spec.md`, and `specs/034-rescore-retry/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

A change to a rubric question, to a service, or to a score threshold rescores every company at once, inside the request. A manager who tunes the rubric in one sitting waits for a full rescoring after each change, and the companies are rescored as many times as there are changes. With the switch off, each change answers at once, and one rescoring at the end covers them all.

## User Scenarios and Testing

### User Story 1 - Several rubric changes, one rescoring (Priority: P2)

**Independent Test**: The unit tests rescore after a change with the switch `OFF` and `ON`. `./preview-test.sh` switches it `OFF`, changes a threshold, runs the scheduler, and switches it `ON`.

**Acceptance Scenarios**:

1. **Given** the setting `rescore.auto` is `OFF`,\
   **When** a rubric question, a service's `offered` or `active`, or a score threshold is changed,\
   **Then** the change is stored, `rescore.requested` is set, and no company is rescored.
2. **Given** the switch is `OFF` and a rescoring is waiting,\
   **When** the scheduler runs,\
   **Then** it rescores nothing, reports `rescored` 0, and the request stays.
3. **Given** the switch is `OFF` and a rescoring is waiting,\
   **When** the setting is changed to `ON`,\
   **Then** every company with answers is rescored in that request, and `rescore.requested` is empty.
4. **Given** the switch is `OFF` and no rescoring is waiting,\
   **When** the setting is changed to `ON`,\
   **Then** nothing is rescored.
5. **Given** the switch is `OFF`,\
   **When** a person calls `POST /rubric/recompute`,\
   **Then** every company is rescored and the request is cleared: a person asked for it.

### User Story 2 - A switch left off is noticed (Priority: P2)

**Independent Test**: The unit tests make the digest's section.

**Acceptance Scenarios**:

1. **Given** the switch is `OFF` and a rescoring is waiting,\
   **When** a digest is made,\
   **Then** it has the section "Scores waiting for a rescoring", which says that automatic rescoring is switched off.
2. **Given** the switch is `ON`, or nothing is waiting,\
   **When** a digest is made,\
   **Then** the section is left out.

### Edge Cases

- While the switch is `OFF`, the stored scores are those of the rubric as it was when the last rescoring ran. Recommendations, the digest, and proposals read them. A company whose own answers are saved meanwhile is scored with the rubric as it is now.
- The rescoring that follows switching `ON` fails as any other: the setting is stored as `ON`, the request stays, and the scheduler rescores at its next run.
- A change of a threshold and of the switch in one `PATCH /settings` is one change: with `OFF` nothing is rescored, with `ON` everything is.
- A missing setting counts as `ON`.
- Two people cannot each hold their own switch: there is one, for everyone. The Worker log names who changed it.

## Requirements

### Functional Requirements

- **FR-001**: The switch is the setting `rescore.auto`, created by the migration `0024_rescore_auto_setting` in `andexor-carmen-main` (issue #77 there). `PATCH /settings` accepts only `ON` and `OFF` for it.
- **FR-002**: `rescore_after` reads the switch and calls `rescore_all` unless it is `OFF`. The endpoints that change every score call it after their change: `POST /rubric`, `PATCH /rubric/{criterion_id}`, `PATCH /services/{service_id}`, and `PATCH /settings` for a `score.*` setting. Each still records the request with `rescore_mark` in the batch of its change.
- **FR-003**: `PATCH /settings` calls `rescore_after` also when `rescore.auto` changes to `ON` and `rescore.requested` was set.
- **FR-004**: The scheduler's step `rescore` runs only when a request is set and the switch is not `OFF`.
- **FR-005**: `POST /rubric/recompute` calls `rescore_all` whatever the switch says.
- **FR-006**: The digest has the list `rescoring_off`, with one line, while the switch is `OFF` and a request is set.

### Endpoints

None are added, and no action changes. `GET /settings` and `PATCH /settings` (`MANAGE`) list and accept the new setting.

### Not in this feature

- Switching the switch back on by itself after some time.
- A switch for each person.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: with the switch `OFF` a threshold change leaves a request and a scheduler run rescores nothing; `ON` again clears the request; and the switch is `ON` at the end.
- **SC-003**: Both databases hold the setting `rescore.auto` with the value `ON`.
