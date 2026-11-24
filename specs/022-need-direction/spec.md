<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Need direction

**Feature Branch**: `37-ai-adapter-typesafe`

**Created**: 2026-11-24

**Status**: Implemented, and live on the preview. Production needs the migration and a recompute when this is deployed; see Manual steps.

**Input**: Issue #38. Found on 2026-11-24 while asking what "Is the site fast?" means: a `YES` on a yes-or-no question in the Need section raised the Need score, although the question is worded so that yes is the good outcome. In the test runs a company with an excellent website scored a Need of 100. Decided 2026-11-24: fix it in code, and keep the data in a recognizable form, so that the logic can be changed or replaced later without touching the data. This changes `specs/004-services-rubric-and-scoring/spec.md`, FR-003 and FR-004. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - A good answer means little need (Priority: P1)

Every question is worded so that yes, or a high value, is the good outcome. A need is where an answer is no or low. The scoring code makes that translation, for every Need question, and a person never has to turn anything around.

**Independent Test**: The unit tests of `src/scoring.py`. On the preview, a company whose website questions were all answered yes scored a Need of 0, where it scored 100 before.

**Acceptance Scenarios**:

1. **Given** a service whose Need questions are all answered `YES`,\
   **When** the scores are computed,\
   **Then** that service's Need is 0.
2. **Given** a service whose Need questions are all answered `NO`,\
   **When** the scores are computed,\
   **Then** that service's Need is 100.
3. **Given** a rating question in the Need section,\
   **When** it is answered 5, 4, or 0,\
   **Then** the Need from it is 0, 20, or 100.
4. **Given** a Fit question answered `YES`,\
   **When** the scores are computed,\
   **Then** Fit rises, as before.
5. **Given** the two rating questions that carried "invert",\
   **When** the code and the migration are both in place,\
   **Then** they score exactly as they did before.

### Edge Cases

- Need is still the highest Need among the services on offer, and confidence is computed as before.
- An unknown answer still changes no score.
- A measured criterion (`specs/021-measured-criteria/spec.md`) follows the same rule: a fast site means little need for hosting.
- A choice or a number in the Need section follows the same rule: its points say how good the answer is.

## Requirements

### What was wrong

The scoring engine computed Need as the share of points earned, the same way as Fit. A `YES` earns full points, so "Do they have a website?", "Is the site secure?", and the other 12 yes-or-no Need questions pushed Need up when the answer was the good one. The two rating questions scored the right way round only because each carried `{"invert": true}`, a setting a person had to remember per question. The constitution, Principle VII, says the code must do the translation.

### Functional Requirements

- **FR-001**: The points of an answer say how good the answer is, from 0 to 1, in the Fit section and in the Need section alike. `YES` is 1 and `NO` is 0. This is unchanged, so stored answers keep their meaning.
- **FR-002**: Fit is the share of the answered Fit weight that was earned. This is unchanged.
- **FR-003**: Need for a service is the share of its answered weight that was not earned: 100 minus the share that was. This replaces feature 004, FR-003.
- **FR-004**: The translation is in one place, `score` in `src/scoring.py`. No question carries a setting for it.
- **FR-005**: `invert` is still understood, for a question whose wording cannot be made positive. No seeded Need question carries it: the migration `0007_need_ratings_without_invert` in `andexor-carmen-main` (issue #28 there) removes it from the two rating questions, which would otherwise be turned around twice.
- **FR-006**: The code and that migration belong together. In a database that has one without the other, the two rating questions score the wrong way round.
- **FR-007**: After the change is deployed, the stored scores of every company are recomputed with `POST /api/v1/rubric/recompute`. The scores stored with past AI runs are not recomputed: a run keeps what it computed when it ran.

### Endpoints

None are added or changed.

### Not in this feature

- Recomputing the scores stored with past AI runs.
- Rewording any question.

### What is not tested

- Production.
- A real run that answers the two rating questions after the migration. Their arithmetic is covered by a unit test.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes.
- **SC-002**: `./preview-test.sh` passes.
- **SC-003**: On the preview, a company with a good website scores a low Need.

## Manual steps

Listed in `MANUAL-TASKS.md`: when this feature is deployed to production, apply the migrations to production and run the recompute.
