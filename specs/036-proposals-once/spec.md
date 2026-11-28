<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: An agreement's proposals job runs once

**Feature Branch**: `84-proposals-once`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #84. Finding 8 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: the job uses fresh results again, a second start is refused with a plain answer, and `fresh=true` still starts a fully fresh job. This spec changes `specs/025-service-proposals/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

The proposals job researches and scores a company with paid models and sends one email. Nothing recorded that it had been started, and every job did all of its work from the top. A double click, a second person, a retry after an error that was not one, or a message that the queue delivered again each paid for the models once more and sent one more email. The production UI starts every job with real models, and a sales team will press the button for the same customer more than once.

## User Scenarios and Testing

### User Story 1 - A second start soon after the first is refused, and says why (Priority: P1)

**Independent Test**: The unit tests start a job twice. `./preview-test.sh` starts the job of an agreement whose job has just finished and is answered 409 with both times.

**Acceptance Scenarios**:

1. **Given** an agreement whose proposals job was started ten minutes ago and is still working,\
   **When** a manager starts it again,\
   **Then** the answer is 409 with `started_at`, and `finished_at` is empty.
2. **Given** an agreement whose proposals job was started ten minutes ago and has stored its email,\
   **When** a manager starts it again,\
   **Then** the answer is 409 with `started_at` and `finished_at`, no model is paid, and no email is sent.
3. **Given** two managers who start the job of one agreement at the same moment,\
   **When** both requests arrive,\
   **Then** one is answered 202 and the other 409.
4. **Given** an agreement whose job was started more than `proposal.reuse_minutes` ago,\
   **When** a manager starts it again,\
   **Then** a job is started, and the answer is 202.

### User Story 2 - A manager can ask for a fresh job (Priority: P2)

**Independent Test**: `./preview-test.sh` starts the job with `fresh=true` right after the refusal, and a second email is stored.

**Acceptance Scenarios**:

1. **Given** an agreement whose job finished ten minutes ago,\
   **When** a manager starts it with `fresh=true`,\
   **Then** a job is started, it makes new research and new scoring, and it sends its email.

### User Story 3 - A job never pays twice for what is fresh (Priority: P1)

**Independent Test**: The unit tests check the line of a run that was used again. The reuse itself is read on the preview by every job.

**Acceptance Scenarios**:

1. **Given** a job that stopped after its research succeeded,\
   **When** the queue delivers its message again,\
   **Then** the job uses that research run again, pays only for the scoring, and sends one email.
2. **Given** a job whose email is stored,\
   **When** the queue delivers its message again,\
   **Then** the job ends, and no second email is sent.
3. **Given** a person ran research on the company a few minutes ago with the default research model,\
   **When** the proposals job starts,\
   **Then** it uses that run again, and the email says so.
4. **Given** a run that was used again,\
   **When** the email lists the runs,\
   **Then** its line reads "succeeded at" with the time, "and was used again".

### Edge Cases

- A scoring run is used again only when it read the research run that is used again, so the two always belong together. A scoring run that is used again and is already applied is not applied a second time.
- Only a run by one of the job's own models is used again: a job with real models never uses a run of the stub, and the other way round.
- A start that cannot be put on the queue takes its record back, so the next start is not refused.
- A completed agreement that is made active and completed again within the time starts no second job by itself. A manager starts one with `fresh=true`.
- With `proposal.reuse_minutes` at 0, no start is refused and no run is used again.
- The record of a start is not an edit by a person, so it is not on the company's timeline. The Worker log has every start and every refusal.
- A message that the queue delivers twice while its first delivery is still working can still pay twice for the part that is not finished. The queue gives no way to tell a second delivery from the first while both run.

## Requirements

### Functional Requirements

- **FR-001**: `queue_proposals(env, agreement_id, real, fresh)` records the start in `agreement.proposals_started_at` and then puts the job on the queue. It answers `QUEUED`, `RECENT`, or `FAILED`, and never raises.
- **FR-002**: The record is one statement that takes the agreement only when it has no start, or its start is older than the setting `proposal.reuse_minutes`, or `fresh` is set. Of two starts at the same moment, one gets it.
- **FR-003**: The column and the setting come from the migration `0022_proposals_started_at` in `andexor-carmen-main` (issue #73 there). The fallback for a missing setting is 60 minutes.
- **FR-004**: `POST /agreements/{agreement_id}/proposals` takes `fresh` beside `real`. For `RECENT` it answers 409 with the error body and two more fields: `started_at`, and `finished_at`, the time the email of that start was stored, or null.
- **FR-005**: The two starts that no person makes (an agreement edited to `COMPLETED`, and the scheduler ending a one-time agreement) go through `queue_proposals` too, without `fresh`.
- **FR-006**: The queue message holds `fresh`, and the job gets it.
- **FR-007**: A job that is not fresh ends at once when a `SERVICE_PROPOSALS` email of the agreement is stored at or after `proposals_started_at`.
- **FR-008**: A job that is not fresh uses again the newest research run of the company that one of its research models finished within `proposal.reuse_minutes`, and the newest scoring run by one of its scoring models that read that research run (`recent_run`). What is missing is made as before.
- **FR-009**: `notify.run_lines` marks a run that was used again.
- **FR-010**: `PATCH /settings` accepts for `proposal.reuse_minutes` a whole number from 0.

### Endpoints

| Endpoint | Action |
|---|---|
| `POST /agreements/{agreement_id}/proposals` | `MANAGE` |

No endpoint is added, and no action changes. `GET /agreements/{agreement_id}` shows `proposals_started_at`.

### Not in this feature

- A screen in the UI. The UI reads `started_at` and `finished_at` from the 409 to say "running since" or "sent at".
- Telling the caller of the two automatic starts that a job was refused. The Worker log has it.

### What is not tested

- A message delivered twice by the queue: it cannot be produced on the preview. Every job on the preview runs the query that looks for a run to use again, and the unit tests cover the record of the start and the email's line.
- A job with real models. It costs money, and no adapter, prompt, or model changed.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: a second start is answered 409 with both times, and a fresh job stores a second email.
- **SC-003**: Both databases hold the column `agreement.proposals_started_at` and the setting `proposal.reuse_minutes`.
