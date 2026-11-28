<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: An unchanged answer keeps its origin

**Feature Branch**: `96-unchanged-answers`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #96. Finding 13 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: leave an unchanged answer alone. This spec changes `specs/004-services-rubric-and-scoring/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

Each answer records where it came from: its source (`AI` or `MANUAL`), the AI run that gave it, the person, and the time. `PUT /companies/{company_id}/answers` wrote every answer of the request as `MANUAL`, by the caller, now, also an answer whose value did not change. A screen that sends the whole form after one answer was changed turned every answer of an AI run into a person's. The origin was lost, the model's reason for an answer no longer reached the proposals email, and a comparison of models took the model's own answer for a person's judgment that agrees with it.

## User Scenarios and Testing

### User Story 1 - A whole form is saved, and one answer changed (Priority: P1)

**Independent Test**: The unit tests save an answer with the value it has, and with another. `./preview-test.sh` sends an answer of an applied AI run back unchanged and reads its source and its run.

**Acceptance Scenarios**:

1. **Given** a company whose 20 answers came from an applied AI run,\
   **When** a person changes one answer and the screen sends all 20,\
   **Then** one answer is `MANUAL`, by that person, now, and 19 keep their source `AI`, their run, and their time.
2. **Given** an answer sent with the value it has,\
   **When** the answers are saved,\
   **Then** nothing is written for that answer, and nothing is put on the timeline for it.
3. **Given** a save in which no answer changes,\
   **When** it is sent,\
   **Then** it is answered 200 with the scores, as before.
4. **Given** an answer sent with another value, or cleared,\
   **When** the answers are saved,\
   **Then** it is stored or removed and recorded as an edit, as before.

### Edge Cases

- A yes-or-no value is compared after it is put in capitals, so `yes` is the stored `YES`. Any other value is compared as text after the spaces around it are taken off: `12` and `12.0` are two values.
- An unchanged answer keeps its stored points. A change to the rubric brings points up to date through the rescoring, as before.
- A person who agrees with an AI answer and sends it back leaves no trace of having looked at it. Recording that is another feature.

## Requirements

### Functional Requirements

- **FR-001**: `PUT /companies/{company_id}/answers` writes no statement for an answer whose value, after it is put in the stored form, equals the stored value.
- **FR-002**: The value is still checked: a value that does not suit its criterion is refused with 400 before it is compared.
- **FR-003**: The scores are computed from all answers and stored, as before.

### Endpoints

| Endpoint | Action |
|---|---|
| `PUT /companies/{company_id}/answers` | `WRITE` |

No endpoint is added, and no action changes.

### Not in this feature

- Recording that a person confirmed an AI answer. `NOTES.md` has the idea of a flag for how an answer was obtained.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: an answer of an applied run that is sent back unchanged keeps the source `AI` and its run.
