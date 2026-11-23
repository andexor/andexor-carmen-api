<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Services, rubric, and scoring

**Feature Branch**: `19-services-rubric-and-scoring`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #19. Backlog feature 004: the service catalog, the rubric, a company's answers, and the scoring engine. The rules for bodies, codes, errors, and dates of `specs/002-companies-and-contacts/spec.md` and `specs/003-activities-and-tasks/spec.md` apply here unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Answer the rubric and get scores (Priority: P1)

A salesperson answers some of the questions about a company and at once sees Fit, Need, confidence, and a recommendation.

**Independent Test**: The smoke test answers one Fit question on its test company, reads the scores, and clears the answer.

**Acceptance Scenarios**:

1. **Given** a company and a criterion in use,\
   **When** an answer is put,\
   **Then** the answer is stored with source `MANUAL` and the caller, and the response carries the new scores.
2. **Given** several answers in one request,\
   **When** one of them is not valid,\
   **Then** the answer is 400 and none of them is stored.
3. **Given** an answered criterion,\
   **When** it is put with a null value,\
   **Then** the answer is removed and the criterion is unknown again.
4. **Given** a company,\
   **When** its answers are requested,\
   **Then** every criterion in use is listed, Fit first, each with the company's answer or a null value.

### User Story 2 - Unknown lowers confidence, never a score (Priority: P1)

A question left unanswered does not count against a company. It only makes the result less certain.

**Acceptance Scenarios**:

1. **Given** one Fit criterion answered Yes and the others unanswered,\
   **When** the scores are computed,\
   **Then** Fit is 100 and confidence is low.
2. **Given** Fit and Need both at or above their thresholds but confidence below its threshold,\
   **When** the scores are computed,\
   **Then** the recommendation is `MAYBE`, not `REACH_OUT`.

### User Story 5 - Scores are refined as answers come in (Priority: P1)

Nothing has to be complete before a score exists. Answers that arrive together are scored once; answers that arrive later refine the result.

**Acceptance Scenarios**:

1. **Given** several answers in one request,\
   **When** they are put,\
   **Then** the scores are computed once, from all of them.
2. **Given** a company that already has scores,\
   **When** more answers are put later,\
   **Then** the scores are computed again from all answers in effect.
3. **Given** one answer about a service with several questions,\
   **When** the scores are computed,\
   **Then** that service has a Need score and a low confidence of its own, and each further answer about it raises that confidence.

### User Story 3 - The catalog and the rubric can change (Priority: P2)

An administrator adds a service, switches one off, adds or reweights a criterion, or retires one. Scores follow.

**Acceptance Scenarios**:

1. **Given** companies with answers,\
   **When** a service's `offered` switch, or a criterion's weight, type, or configuration, is changed,\
   **Then** the scores of every company that has answers are recomputed before the response.
2. **Given** a criterion,\
   **When** it is patched with `active` false,\
   **Then** it no longer counts and no longer appears among a company's answers, and it stays in the rubric and in the database.
3. **Given** a `MEMBER`,\
   **When** they try to change a service or the rubric,\
   **Then** the answer is 403. They can still answer, and they can run a recompute.

### User Story 4 - A score change is on the timeline (Priority: P2)

**Acceptance Scenarios**:

1. **Given** a company,\
   **When** its Fit or Need moves by 5 or more, appears, or goes away, or its recommendation changes,\
   **Then** a `SCORE_CHANGE` entry with the old and new values is written in the same transaction.
2. **Given** a smaller move,\
   **When** the scores are stored,\
   **Then** no entry is written.

### Edge Cases

- A service that is not offered keeps its own Need score, so the need is on record, but it never counts toward the company's Need or toward confidence.
- A company with no answer at all has no Fit, no Need, and no recommendation, and its confidence is 0.
- With Fit known and Need unknown, the recommendation is `SKIP` when Fit is below its threshold and `MAYBE` otherwise. Unknown is not low.
- An answer whose value no longer suits its criterion, after the type or configuration changed, is kept but counts as unknown.
- A number that falls in no band earns 0 points. A choice that is not in the list is refused.
- Halves round up: 12.5 becomes 13.
- A threshold missing from the settings, or not a number, falls back to its default (60, 60, 30, 30, and 40).

## Requirements

### Endpoints and their actions

All paths are under `/api/v1`.

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /services` | `READ` | The catalog, in catalog order |
| `POST /services` | `MANAGE` | Add a service |
| `PATCH /services/{id}` | `MANAGE` | Change a service, including `offered` |
| `GET /rubric` | `READ` | Every criterion, Fit first, then by service |
| `POST /rubric` | `MANAGE` | Add a criterion |
| `PATCH /rubric/{id}` | `MANAGE` | Change or retire a criterion |
| `POST /rubric/recompute` | `WRITE` | Recompute every company's scores |
| `GET /companies/{id}/answers` | `READ` | The answers in effect, with each criterion |
| `PUT /companies/{id}/answers` | `WRITE` | Set, change, or clear answers; returns the scores |

There is no delete for a service or a criterion. Both are retired with `active` false, so that old runs stay readable.

### Functional Requirements

The scoring engine (`src/scoring.py`, pure):

- **FR-001**: `points(criterion, value)` returns 0 to 1, or nothing when the value is not an answer of the criterion's type. `YES_NO`: Yes is 1 and No is 0, in any letter case. `RATING`: a number from 0 to 5, divided by 5. Both are reversed when the configuration has `"invert": true`. `NUMBER`: the points of the first band whose `min` and `max` contain the number, where a null `max` has no upper limit, and 0 when no band does. `CHOICE`: the points of the choice with that code.
- **FR-002**: `score(criteria, answers, services, thresholds)` returns Fit, Need per service, Need, confidence, and the recommendation. Retired criteria and retired services are ignored.
- **FR-003**: Fit is 100 x (sum of weight x points) / (sum of weights) over the answered Fit criteria, as a whole number. Need per service is the same over that service's answered criteria. Each is absent when nothing is answered.
- **FR-004**: Need is the highest Need per service among offered services.
- **FR-005**: Confidence is the weight of the answered criteria divided by the weight of all criteria in play, as a whole number from 0 to 100. In play means every Fit criterion in use, plus every criterion in use of each offered service that has at least one answer. A service that has not been looked at is not in play, so one need can be examined without the others. Once a service has an answer, all of its criteria are in play, so a need that is only partly examined lowers confidence until the rest is answered. The confidence of one service is the same ratio over that service's criteria. Decided 2026-11-23.
- **FR-006**: The recommendation is `REACH_OUT` when Fit and Need are both known and at or above `score.reach_out.min_fit` and `score.reach_out.min_need`, and confidence is at or above `score.min_confidence`. Otherwise it is `SKIP` when a known Fit is below `score.skip.max_fit` or a known Need is below `score.skip.max_need`. Otherwise it is `MAYBE`. It is absent when neither Fit nor Need is known.
- **FR-007**: The thresholds are read from the `setting` table on every call.
- **FR-008**: `notable(old, new)` decides whether a change belongs on the timeline (User Story 4).
- **FR-009**: The engine reads answer values, never stored points, so a change of configuration takes effect on the next computation. The `points` column of `rubric_answer` is kept up to date for display.

Storing scores:

- **FR-010**: Scores are stored in one transaction: the company's `company_service` rows are replaced, `fit_score`, `need_score`, `confidence`, `recommendation`, `scored_at`, and `updated_at` are set on the company, and the `SCORE_CHANGE` entry is added when the change is notable.
- **FR-011**: A recompute covers every company that has at least one answer. It runs inside the request that causes it. It is caused by: a change to a service's `offered` or `active`; a new criterion; a change to a criterion's `section`, `service_id`, `answer_type`, `weight`, `config_json`, or `active`; and `POST /rubric/recompute`.
- **FR-012**: `POST /rubric/recompute` returns `{"companies": <how many>}`. It exists so that scores can be repaired if a recompute fails after its change was stored. A `MEMBER` may run it, as well as an `ADMIN`: it changes no rule, only applies the rules in force. Decided 2026-11-23.

Services:

- **FR-013**: The writable fields are `name`, `category`, `offered`, `sort_order`, and `active`, and on create also `id`. Creating requires `name`. `category` MUST be an active `SERVICE_CATEGORY` code.
- **FR-014**: A service id is a lowercase slug. When none is given it is made from the name: lowercase, with every run of other characters replaced by a hyphen. An id cannot be changed. A name or id that exists gets 409.

The rubric:

- **FR-015**: The writable fields are `section`, `service_id`, `prompt`, `help`, `answer_type`, `weight`, `config_json`, `enrichment_key`, `sort_order`, and `active`. Creating requires `prompt`. `answer_type` defaults to `YES_NO` and `weight` to 1.
- **FR-016**: When `section` is not given, a criterion with a `service_id` is `NEED` and any other is `FIT`. A `FIT` criterion with a service, or a `NEED` criterion without one, gets 400. A weight outside 1 to 10 gets 400.
- **FR-017**: `config_json` is text holding a JSON object, in requests and in responses, as the column stores it. It MUST suit the answer type: `bands` is a list of objects with a numeric `min`, a numeric or null `max`, and `points` from 0 to 1; `choices` is a list of objects with a `code` and `points` from 0 to 1. Otherwise the answer is 400.

Answers:

- **FR-018**: `GET /companies/{id}/answers` returns `{"items": [...]}`: one item per criterion in use, with the criterion's columns and the answer's `value`, `points`, `source`, `ai_run_id`, `answered_by`, and `answered_at`, which are null when the criterion is unanswered.
- **FR-019**: `PUT /companies/{id}/answers` takes `{"answers": [{"criterion_id": ..., "value": ...}, ...]}` with at least one entry. A value is text or a number. A null or blank value clears the answer. A `YES_NO` value is stored in capitals.
- **FR-020**: Every entry MUST name a criterion in use and carry a value that suits it. Otherwise the answer is 400 and nothing is stored.
- **FR-021**: A stored answer has source `MANUAL`, no run, the caller as `answered_by`, and the time as `answered_at`.
- **FR-022**: The response is the scores: `fit_score`, `need_score`, `confidence`, `recommendation`, and `services`, a list of `service_id`, `name`, `offered`, `need_score`, and `confidence`, highest Need first.

Companies:

- **FR-023**: `GET /companies/{id}` also returns `services`, in the shape of FR-022.

Tests:

- **FR-024**: The unit tests cover the points table, the configuration check, every formula, the recommendation at and around each threshold, thresholds taken from settings, rounding, and the timeline rule.
- **FR-025**: `preview-test.sh` reads the catalog and the rubric, answers one Fit question on its test company, reads the answer and the scores, finds the `SCORE_CHANGE` entry, is refused a value that does not suit, clears the answer, is refused a criterion with a damaged configuration, and runs a recompute. It adds nothing to the catalog or the rubric, because neither can be deleted.

### Not in this feature

- Running a recompute in the background. It runs inside the request. If it becomes consistently slow, it moves to the background, or to a queue with a separate consumer. Decided 2026-11-23.
- Scores of a scoring run, and applying a run. Feature 009 calls the same engine.
- Deleting a service or a criterion.
- Changing the thresholds. Feature 016 adds the settings endpoints.

### What is not tested

- A recompute over many companies. It was run over one.
- Creating a service and creating or changing a criterion on the preview. They were checked in a local run, where the database can be thrown away.
- A real `MEMBER` or `READER` through Cloudflare Access. In a local run, a `MEMBER` could answer and was refused every change to services and the rubric, and a `READER` was refused answering. A `MEMBER` running a recompute is covered by the role rule and its unit tests, not by a run.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company behind and the catalog and rubric unchanged.
- **SC-003**: The check `Preview and smoke test` passes.
