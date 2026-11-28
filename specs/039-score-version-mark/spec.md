<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Scores are written only while nothing changed

**Feature Branch**: `92-score-version-mark`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #92. Finding 12 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: a version mark on the company; a write goes through only while the mark is unchanged, and a writer whose mark has moved reads again and redoes its work. This spec changes `specs/004-services-rubric-and-scoring/spec.md`, `specs/010-ai-adapter-claude/spec.md`, and `specs/034-rescore-retry/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

Scores are computed in code from the answers and the rubric, in three steps: read, compute, write. Nothing stopped another writer between the read and the write. Two people who answered questions for one company at the same moment, a person who saved answers during a full rescoring, an AI run applied during an edit, and two rubric changes close together could each leave scores that did not follow from the stored answers, with no sign of it. Recommendations, the digest, and proposals all read the stored scores. With a sales team, two people on one customer is an ordinary day.

## User Scenarios and Testing

### User Story 1 - Two people answer questions for one company at the same moment (Priority: P1)

**Independent Test**: The unit tests save answers against a database whose mark moves. `./preview-test.sh` sends two saves at the same moment and then checks that a full rescoring changes nothing for the company.

**Acceptance Scenarios**:

1. **Given** Ann and Ben each save an answer to another question of one company at the same moment,\
   **When** both saves are done,\
   **Then** both are answered 200, both answers are stored, and the scores follow from both.
2. **Given** a save meets a mark that another writer has moved,\
   **When** it is done again,\
   **Then** it reads the answers as they are now, puts its own answers on them, and stores them once.
3. **Given** the database refuses a save and no other writer was the cause,\
   **When** the save fails,\
   **Then** it is answered with an error at once, and is not tried again.

### User Story 2 - Answers are saved while every company is rescored (Priority: P1)

**Independent Test**: The unit tests run a full rescoring against a database whose mark moves once, and one whose mark always moves.

**Acceptance Scenarios**:

1. **Given** a full rescoring has read the answers,\
   **When** a person saves answers for one company before the rescoring writes that company,\
   **Then** the rescoring's write for that company stores nothing, the company is read again, and its scores follow from the new answers.
2. **Given** two rubric changes close together, each with its own rescoring,\
   **When** both rescorings are done,\
   **Then** every company's scores follow from the rubric after the second change.
3. **Given** a full rescoring that still cannot write some companies after its tries,\
   **When** it ends,\
   **Then** it fails, the request `rescore.requested` stays set, and the scheduler rescores again.

### User Story 3 - An AI run is applied while someone edits the answers (Priority: P2)

**Independent Test**: The same code path as User Story 1; the smoke test applies a run.

**Acceptance Scenarios**:

1. **Given** a scoring run is applied while a person saves an answer for the same company,\
   **When** both are done,\
   **Then** the answers and the scores are those of whichever came last, as one whole, never a mix of one's answers and the other's scores.

### Edge Cases

- More writers of one company at the same moment than there are tries: the last one is answered with an error and can be sent again. The number of tries is 5. On the preview, 96 saves in groups of four at the same moment were all answered 200, 69 of them after being done again, and the scores followed from the answers each time. With 3 tries, one save of 48 was refused.
- A save that is done again writes its line in the Worker log once for each try. Only the last try is stored, with its entry on the timeline.
- A company whose scores were never written since the migration has no mark. Its first write goes through as any other, and gives it one.
- A rubric change alone moves no mark. The rescoring that follows it does, for every company with answers.
- The mark says nothing about who wrote or when. `scored_at` says when.

## Requirements

### Functional Requirements

- **FR-001**: The mark is the column `company.score_token`, added by the migration `0023_score_token` in `andexor-carmen-main` (issue #75 there).
- **FR-002**: `read_for_scoring` reads the rubric, the score thresholds, and whatever further statements a writer gives it in one `batch`. Every writer of scores reads the company's row with its mark, and the answers, through it, so all of it is one moment of the database.
- **FR-003**: `score_writes` puts the statement `SCORE_GUARD` first. It fails when the stored mark is not the mark that was read, and a failed statement undoes the whole batch: the answers, their record on the timeline, and the scores are stored together or not at all. The statement that stores the scores gives the company a new mark.
- **FR-004**: A writer whose batch failed asks `score_moved` whether the mark is another now. When it is, the writer reads again and redoes its work, at most `SCORE_TRIES` times in all. When it is not, or it cannot be told, the failure stands.
- **FR-005**: This holds for `PUT /companies/{company_id}/answers`, for `apply_scoring` (a person applying a run, and the proposals job), and for `rescore_all`.
- **FR-006**: `rescore_all` reads everything in one batch, writes in batches, and collects the companies of a batch that failed. It reads those again and writes them again. When some are left after its tries, it raises and does not clear `rescore.requested`. The request is cleared by its own statement after the last company is written.
- **FR-007**: A write that is done again is logged as `carmen_scores_again` with its step, and a rescoring that reads companies again as `carmen_rescore_again` with their number.

### Endpoints

None are added, and no action changes. `GET /companies/{company_id}` shows `score_token`.

### Not in this feature

- A switch that holds automatic rescoring back while the rubric is edited. It is the next feature.
- Moving the scoring to a queue.

### What is not tested

- Two full rescorings at the same moment on the preview: it would change the shared rubric. The unit tests cover a rescoring that meets a moved mark.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: two saves at the same moment are both answered 200, the company gets a new mark, and a full rescoring right after changes none of its scores.
- **SC-003**: Both databases hold the column `company.score_token`.
