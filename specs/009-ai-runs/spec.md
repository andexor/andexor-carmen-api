<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: AI runs: framework and stub

**Feature Branch**: `29-ai-runs`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #29. Backlog feature 009: the AI models, research and scoring runs, what a user decides about their results, comparison, statistics, a stub model, and discovery task D2. The real providers are features 010 to 012 and plug into what is built here. The rules of the earlier specs, including the change log (`specs/018-change-log/spec.md`), apply unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Start runs and keep working (Priority: P1)

A salesperson asks one or several models to research or score a company. The answer comes back at once, and the work goes on after it.

**Independent Test**: The smoke test starts a research run and a scoring run with the stub model and waits until both have finished.

**Acceptance Scenarios**:

1. **Given** a company and an enabled model that can research,\
   **When** runs are started,\
   **Then** the answer is 202 with one run id per model, and each run is `QUEUED`.
2. **Given** a queued run,\
   **When** the queue delivers it,\
   **Then** it becomes `RUNNING`, and then `SUCCEEDED` or `FAILED`, with its times and token counts.
3. **Given** a research model and scoring models in one request,\
   **When** research-and-score is started,\
   **Then** the scoring runs wait for the research run, read its dossier when it succeeds, and fail when it fails.
4. **Given** a model that is disabled, cannot do the job, or belongs to a provider with no adapter,\
   **When** a run is started with it,\
   **Then** the answer is 400 and no run is created.

### User Story 2 - A run proposes, a person decides (Priority: P1)

Nothing a run produces changes the company until a user says so.

**Acceptance Scenarios**:

1. **Given** a finished research run,\
   **When** the company is read,\
   **Then** its facts are unchanged, and the run holds the dossier and the proposed facts.
2. **Given** proposed facts,\
   **When** some or all are accepted,\
   **Then** each accepted fact becomes a fact of the company with the source `AI` and the run it came from.
3. **Given** a finished scoring run,\
   **When** the company is read,\
   **Then** its answers and scores are unchanged, and the run holds its own answers and scores.
4. **Given** a finished scoring run,\
   **When** it is applied,\
   **Then** its answers replace the company's answers, the scores are recomputed, and the run is stamped as applied.

### User Story 3 - Learn which models to trust (Priority: P2)

**Acceptance Scenarios**:

1. **Given** two scoring runs of one company,\
   **When** they are compared,\
   **Then** each criterion shows the answer in effect and each run's answer, and is flagged where the runs differ.
2. **Given** runs of several models,\
   **When** the statistics are read,\
   **Then** each model and kind shows its runs, failures, average rating, duration, and tokens, and how often its answers equal the answers in effect.
3. **Given** a run,\
   **When** it is rated 1 to 5 with a note,\
   **Then** the rating and note are stored with it.

### Edge Cases

- A run delivered a second time while `RUNNING` was cut off before it finished. It is failed, not started again.
- A run that could not be put on the queue is failed at once, so that nothing stays `QUEUED` forever.
- A fact with no key or no value, and a date fact that is not understood, are dropped from a run's output.
- An answer for a criterion that does not exist or is retired, an answer of the wrong type, and a second answer for one criterion are dropped and counted in the log.
- A fact that was accepted or rejected cannot be decided again (409).
- Applying a run computes the points again, in case the rubric changed since the run.
- Deleting a company deletes its runs.

## Requirements

### Discovery result

- **D2 (answered 2026-11-23)**: Work started after a response with `wait_until` finished at 10 and at 25 seconds, and was cut off at 45, 70, and 120 seconds. Work delivered through a Cloudflare Queue to the same Worker finished at 70 and at 200 seconds. Runs therefore go through a queue.
- A buffered response is not sent until the application returns, so the framework's background tasks would delay the 202. They are not used.
- The queue handler receives two arguments beyond the batch. A handler that does not accept them fails every message.

### Endpoints and their actions

All paths are under `/api/v1`.

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /ai/models` | `READ` | The models |
| `POST /ai/models` | `MANAGE` | Add a model |
| `PATCH /ai/models/{id}` | `MANAGE` | Change a model, enable or disable it |
| `GET /ai/models/stats` | `READ` | Statistics per model and kind |
| `POST /companies/{id}/runs` | `WRITE` | Start runs |
| `POST /companies/{id}/runs/research-and-score` | `WRITE` | Research, then score the dossier |
| `GET /companies/{id}/runs` | `READ` | The company's runs |
| `GET /companies/{id}/runs/compare` | `READ` | Scoring runs side by side |
| `GET /runs/{id}` | `READ` | One run with its results |
| `PATCH /runs/{id}` | `WRITE` | Rate a run |
| `POST /runs/{id}/facts/decide` | `WRITE` | Accept or reject proposed facts |
| `POST /runs/{id}/apply` | `WRITE` | Make a scoring run's answers the answers in effect |

### Functional Requirements

Models:

- **FR-001**: `GET /ai/models` returns `{"items": [...]}` in catalog order, each with `runnable`, which is true when the model's provider has an adapter. Filters: `can_research`, `can_score`, `active`.
- **FR-002**: The writable fields are `provider`, `model_id`, `label`, `can_research`, `can_score`, `web_search`, `active`, `sort_order`, and `notes`. Creating requires `provider`, `model_id`, and `label`, and at least one of the two abilities. A provider with no adapter gets 400, on create and when a model is moved to it. A provider and model id that exist get 409.
- **FR-003**: A model is never deleted. Disabling it keeps its runs.
- **FR-004**: The providers with an adapter in this feature: `STUB`. Features 010, 011, and 012 add `CLAUDE`, `WORKERS_AI`, and `TYPESAFE`.

Starting runs:

- **FR-005**: `POST /companies/{id}/runs` takes `kind` (`RESEARCH` or `SCORING`), `ai_model_ids` (at least one), and for scoring an optional `input_run_id`, which MUST be a succeeded research run of the same company. It stores one run per model with status `QUEUED`, puts each on the queue, and answers 202 with `{"run_ids": [...]}`.
- **FR-006**: `POST /companies/{id}/runs/research-and-score` takes `research_model_id` and `scoring_model_ids`. It stores one research run and one scoring run per scoring model, each with the research run as its input. Only the research run is put on the queue.
- **FR-007**: Every model named MUST exist, be enabled, be able to do the job, and belong to a provider with an adapter. Otherwise the answer is 400 and nothing is stored.
- **FR-008**: A run records who requested it, and the model's own identifier at that moment, so that changing the model later does not rewrite history.
- **FR-009**: Runs are started only by a user. There are no automatic and no bulk runs.

Doing the work:

- **FR-010**: The queue `RUNS` is bound for production (`andexor-carmen-api-runs-prod`) and for the preview (`andexor-carmen-api-runs-prev`). Each Worker consumes its own queue, one message at a time, with one more delivery after a failure. A message carries one run id.
- **FR-011**: A run moves from `QUEUED` to `RUNNING` with `started_at`, then to `SUCCEEDED` or `FAILED` with `finished_at`. Both times are UTC with milliseconds. `duration_ms` is set by the database. `model_ms` is the time spent waiting for the model.
- **FR-012**: A research run stores the dossier and one `ai_run_fact` per proposed fact, with decision `PROPOSED`.
- **FR-013**: A scoring run stores one `ai_run_answer` per answer, with points, and with a probability and a rationale when the model gave them. It then runs the scoring engine on those answers and stores the run's own Fit, Need, confidence, and recommendation, and one `ai_run_service` per service.
- **FR-014**: The material a scoring model reads: with an input run, the company, that run's dossier, and all of that run's facts, accepted or not; without one, the company and its latest fact for each key. The same input gives the same text for every model.
- **FR-015**: Everything a model returns is cleaned before it is stored (see the edge cases). Text is cut to a maximum length. Output that cannot be read is asked for once more; a second failure fails the run.
- **FR-016**: A finished run adds a `SYSTEM` entry to the company's timeline, saying what it produced and that nothing was applied. A failed run adds one with the reason.
- **FR-017**: A run's `error` holds a message that is safe to show. Detail of an unexpected error stays in the Worker log.
- **FR-018**: When a research run succeeds, the scoring runs that wait for it are put on the queue. When it fails, they are failed with it.
- **FR-019**: A run changes nothing on the company.

Reading and deciding:

- **FR-020**: `GET /companies/{id}/runs` returns the runs, newest first, with the model's label, status, scores, tokens, duration, and rating, and without the dossier.
- **FR-021**: `GET /runs/{id}` returns the run and, for research, `dossier` and `facts`; for scoring, `answers` with each criterion's prompt, and `services`.
- **FR-022**: `PATCH /runs/{id}` sets `user_rating` (1 to 5) and `user_note`. Nothing else about a finished run can be changed.
- **FR-023**: `POST /runs/{id}/facts/decide` takes `{"all": "ACCEPTED" | "REJECTED"}`, which decides every fact still proposed, or `{"decisions": [{"id": ..., "decision": ...}]}`. An accepted fact is copied to the company's facts with source `AI`, the run's id, its source URL as the source detail, and its note. A fact that is not of this run, or is already decided, gets 409.
- **FR-024**: `POST /runs/{id}/apply` is allowed for a succeeded scoring run only (409 otherwise). It replaces the company's answers with the run's, with source `AI`, the run's id, and the caller, recomputes and stores the scores, stamps `applied_at`, and returns the scores.
- **FR-025**: `GET /companies/{id}/runs/compare?run_ids=a,b` returns the runs and, for every criterion in use, the answer in effect and each run's `value`, `points`, `probability`, and `rationale`. `disagreement` is true when the runs do not all give the same value, counting a run with no answer as different. Every id MUST be a scoring run of the company.
- **FR-026**: `GET /ai/models/stats` returns, per model and kind: `runs`, `failures`, `average_rating`, `average_duration_ms`, `average_tokens`, and `agreement_rate`, the share of the model's answers that equal the company's answer in effect.
- **FR-027**: Rating a run, deciding facts, and applying a run are recorded as the change log requires. A change to a model goes to the Worker log only, as it has no company.

The stub:

- **FR-028**: The `STUB` adapter calls nothing. Research returns a fixed dossier and two facts. Scoring returns, for every criterion in use, an answer of the right type derived from the criterion's id, with a probability and a rationale.
- **FR-029**: The stub model is disabled in production by the seed data. The smoke test enables it on the preview.

Tests and scripts:

- **FR-030**: The unit tests cover the stub, the cleaning of facts and answers, the material, the comparison, and that the preview and production share no queue.
- **FR-031**: `preview-test.sh` enables the stub, starts research-and-score, waits for the runs, accepts the facts and sees them on the company, is refused applying a research run, applies the scoring run and sees the answers, compares, rates, and reads the statistics and the run list.
- **FR-032**: `preview-create.sh` creates the preview queue when it is missing, and `preview-delete.sh` deletes it.

### Not in this feature

- The Claude, Workers AI, and TypeSafe adapters, and the prompt files they read. Features 010 to 012.
- Fetching a company's web pages. Feature 011.
- Cancelling a run, and running one again.
- Undoing an applied run or an accepted fact. The change log keeps what was replaced.
- A limit on how many runs can be queued at once.

### What is not tested

- A run that fails or is interrupted. The stub cannot fail, so those paths are covered by reading the code, not by a run. Feature 010 adds a provider that can fail.
- A run that takes minutes. D2 measured the queue with a 200-second wait, not with a model.
- Several runs at the same moment.
- A real `MEMBER` or `READER` through Cloudflare Access.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company and no run behind.
- **SC-003**: The check `Preview and smoke test` passes, which proves the pipeline's token can deploy a Worker bound to a queue.
