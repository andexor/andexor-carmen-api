<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Measured criteria

**Feature Branch**: `37-ai-adapter-typesafe`

**Created**: 2026-11-24

**Status**: Implemented. The rule is in the preview and the production database: the migrations were applied to production on 2026-11-24. Changed by `specs/026-uptime-checker/spec.md`: "Is the site reliably up?" is measured from the fact `uptime_percent`.

**Input**: Issue #37, asked for on 2026-11-24: "If 'Is the site fast?' is measured by pagespeed_mobile, then we need some criteria for judging the results to arrive at a YES or NO answer. What is the latest guidance from Google? Find out and go with that." It adds to `specs/004-services-rubric-and-scoring/spec.md` and `specs/009-ai-runs/spec.md`. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - "Is the site fast?" has a stated rule (Priority: P1)

The question was a bare text with nothing that said what fast means, so a model answered it from general impressions. Now a number decides: the PageSpeed score for mobile.

**Independent Test**: `./preview-test.sh` adds a PageSpeed fact of 95 to its test company, starts a scoring run, and checks the answer and its rationale.

**Acceptance Scenarios**:

1. **Given** a fact `pagespeed_mobile` of 90 or more,\
   **When** a scoring run is processed,\
   **Then** the answer to "Is the site fast?" is `YES`.
2. **Given** a fact `pagespeed_mobile` below 90,\
   **When** a scoring run is processed,\
   **Then** the answer is `NO`.
3. **Given** no such fact, or one that is not a number,\
   **When** a scoring run is processed,\
   **Then** the criterion is left unknown.
4. **Given** any model,\
   **When** it scores,\
   **Then** it is not asked "Is the site fast?", and an answer it gives to it anyway is dropped.
5. **Given** an answer taken from the fact,\
   **When** it is stored,\
   **Then** its rationale says so, with the number and the limit, and it has no probability.

### Edge Cases

- A score of exactly 90 is `YES`.
- A person can still type an answer to the question by hand. The rule applies to scoring runs.
- A criterion with a limit and no fact name is not measured, and is asked as before.
- `yes_at` on a criterion that is not yes-or-no is refused when the criterion is saved.

## Requirements

### The guidance that was followed

Google, for the PageSpeed performance score, read on 2026-11-24:

| Score | Google's rating |
| --- | --- |
| 90 to 100 | Good |
| 50 to 89 | Needs improvement |
| 0 to 49 | Poor |

"A score of 90 or above is considered good. 50 to 89 is a score that needs improvement, and below 50 is considered poor." Source: `https://developers.google.com/speed/docs/insights/v5/about`, last updated 2024-10-21. The Lighthouse scoring page gives the same three ranges and says that a site should strive for a good score, 90 to 100.

Decided 2026-11-24: fast means good, so `YES` is 90 or more, and `NO` is anything below, the "needs improvement" range included.

### Functional Requirements

- **FR-001**: A criterion is measured when its answer type is `YES_NO`, its configuration has `yes_at` with a number, and its `enrichment_key` names a fact.
- **FR-002**: A measured criterion is answered by code: `YES` when the fact's number is at or above `yes_at`, and `NO` when it is below. With no fact, or one that is not a number, it is unknown.
- **FR-003**: A scoring run does not send a measured criterion to a model, whatever the provider. An answer that a model gives to one is dropped.
- **FR-004**: The fact is read from the same facts the run's material is made of: the facts of the research run that is being scored, or the company's latest fact for each key.
- **FR-005**: The stored answer has no probability, and its rationale reads "Measured, not judged: the fact <key> is <number>, and <limit> or more is YES."
- **FR-006**: A measured answer earns points like any other yes-or-no answer.
- **FR-007**: `yes_at` MUST be a number and is allowed on a `YES_NO` criterion only. The rubric endpoints refuse anything else with 400.
- **FR-008**: The rule is data, not code: the limit is in the criterion's `config_json`. "Is the site fast?" gets `{"yes_at": 90}` and a help text that states the rule, by the migration `0006_site_fast_from_pagespeed` in `andexor-carmen-main` (issue #26 there).
- **FR-009**: The arithmetic is in `src/scoring.py` (`measured`, `from_fact`), which is pure.
- **FR-010**: A yes-or-no criterion whose configuration has `"measured": true` and no `yes_at` is measured with no rule yet: no model is asked it, and it stays unknown until a person answers it. Decided 2026-11-24 for "Is the site reliably up?": one visit to a website cannot show "reliably", which needs monitoring over months, and how to measure it is not decided. It is set by the migration `0008_reliably_up_unknown` in `andexor-carmen-main` (issue #30 there). This decision is to be revisited; the options are in `NOTES.md`.

### Endpoints

None are added or changed. `POST` and `PATCH` of a rubric criterion (`MANAGE`) now accept `yes_at` in `config_json` on a yes-or-no criterion.

### Not in this feature

- Measuring the PageSpeed score. Nothing in the API calls Google's PageSpeed service yet, so the fact exists only when a person enters it or a run proposes it. Until then the question is unknown, which lowers confidence and never changes a score.
- A rule for "Is the site secure?" and "Does the site pass Core Web Vitals on mobile?". Models still answer them.
- A way to measure "Is the site reliably up?". It is left unknown (FR-010).
- A separate limit for `NO`, with an unknown range between.
- Applying the rule to the company's answers when a fact is accepted, outside a scoring run.

### What is not tested

- A real model on a rubric with a measured criterion. The smoke test uses the stub; the filter is the same for every provider.
- Production. The migration is in both databases, but no check was made there: the smoke test runs on the preview only.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes.
- **SC-002**: `./preview-test.sh` passes, including a scoring run that answers "Is the site fast?" `YES` from a PageSpeed fact of 95.

## Manual steps

None are left. The migration pull requests in `andexor-carmen-main` (#27 and #31) were merged, and the migrations were applied to production on 2026-11-24, as recorded in `MANUAL-TASKS.md`.
