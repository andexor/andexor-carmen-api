<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: AI adapter: TypeSafe (Jev)

**Feature Branch**: `37-ai-adapter-typesafe`

**Created**: 2026-11-24

**Status**: Implemented.

**Input**: Issue #37. Backlog feature 012: the adapter for the provider `TYPESAFE`, and discovery task D6. It plugs into the run framework of `specs/009-ai-runs/spec.md`. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Jev answers the rubric from a dossier (Priority: P1)

A salesperson has a dossier that another model wrote, and starts a scoring run with Jev. Jev is a decision model: it answers typed questions with probabilities and writes no text. The run stores an answer for each criterion that the material settles, with its probability, and its own scores.

**Independent Test**: `./ai-test.sh run <the Jev model id> --research <a model that researches>` on the preview. On 2026-11-24 the scoring run succeeded in under a second with 8 answers.

**Acceptance Scenarios**:

1. **Given** a `YES_NO` criterion,\
   **When** Jev gives a probability of yes of 0.7 or more,\
   **Then** the answer is `YES` with that probability.
2. **Given** a `YES_NO` criterion,\
   **When** Jev gives a probability of yes of 0.3 or less,\
   **Then** the answer is `NO`, and its probability is one minus what Jev gave.
3. **Given** a `RATING` or a `CHOICE` criterion that the material settles,\
   **When** the run is processed,\
   **Then** the answer is the most likely level or option, with its probability.
4. **Given** a `NUMBER` criterion whose `enrichment_key` names a fact in the material,\
   **When** the run is processed,\
   **Then** the answer is that fact's value, taken by code, and Jev is not asked.
5. **Given** any answer from Jev,\
   **When** it is stored,\
   **Then** its rationale holds Jev's numbers: the answer's probability, and how sure Jev is that the material had enough information to answer.
6. **Given** a `YES_NO` answer that Jev is sure of, from material that Jev says does not have enough information,\
   **When** it is stored,\
   **Then** the answer is kept, and its rationale is marked "weak evidence".

### User Story 2 - What the material does not settle stays unknown (Priority: P1)

An unknown answer never lowers a score; it lowers confidence. So a judgment that is not settled is left out and never guessed.

**Acceptance Scenarios**:

1. **Given** a `YES_NO` criterion,\
   **When** Jev gives a probability between 0.3 and 0.7,\
   **Then** the criterion is left unknown.
2. **Given** a `RATING` or a `CHOICE` criterion,\
   **When** Jev says with less than 0.7 that the material has enough information to answer it,\
   **Then** the criterion is left unknown, whatever level or option Jev picked.
3. **Given** a `NUMBER` criterion with no matching fact,\
   **When** the run is processed,\
   **Then** the criterion is left unknown.

### User Story 3 - A failure is an error, never an answer (Priority: P1)

**Acceptance Scenarios**:

1. **Given** an error from the TypeSafe API,\
   **When** the run is processed,\
   **Then** the run is `FAILED` with the status, no answer is stored, and the API's own message is only in the Worker log.
2. **Given** a research run started with Jev,\
   **When** it is asked for,\
   **Then** it is refused, because the model's row says that it cannot research.

### Edge Cases

- A `CHOICE` criterion with no options is not asked.
- A rubric of only `NUMBER` criteria sends no request.
- A damaged answer, such as a probability that is not a number, is left unknown.
- A criterion's `help` is added to its question in brackets.
- `jev-latest` is an alias that moves. The run stores the version that answered, for example `jev-1.14.0`.
- Too many requests gets status 429. The run fails and says to try again later; nothing is tried again by itself.

## Requirements

### Discovery result

- **D6 (answered 2026-11-24)**: One endpoint, `POST https://api.typesafe.ai/v1/systemone`, with the header `Authorization: Bearer <key>`. The body has `model`, `state`, and `questions`. `state` is text, an object, or a list. `questions` is an object: each name is ours, and each question has a `type`, `instructions`, and for some types `criteria`.
- The three question types this adapter uses: `noul`, a yes-or-no question, answered with `noul`, the probability of yes; `score`, with `criteria` as an ordered list of at most 10 level descriptions, answered with `score` and `probabilities` per level; and `choice`, with `criteria` as an object of at most 255 option names with descriptions, answered with `choice` and `probabilities` per option. The answers come back under the same names.
- Limits for Jev 1.14: 64,000 tokens for one request, which is the state and every question, and 32,000 for the state and any one question. There is no limit on the number of questions. Rate limits are per organization and answer 429. A header raises the context to 262,000 tokens at a cost in speed and accuracy; it is not used.
- Price: $0.042 per million input tokens. Output tokens are free.
- Access route: a key from the TypeSafe console, held in Secrets Store. The model takes no fine-tuning; everything it knows about a company is in the request.
- Questions in one request cannot see each other. A `choice` always picks an option and a `score` always picks a level, even when nothing fits, so each is paired with a `noul` that asks whether the material can answer it.
- **Measured on the preview on 2026-11-24**, one company, 25 questions for 19 criteria, before yes-or-no questions had companions: 0.5 seconds, 2,222 input tokens, about $0.0001. The version that answered was `jev-1.14.0`.
- **Measured again after every question had a companion**: 38 questions for 19 criteria, 0.4 seconds, 2,808 input tokens, still about $0.0001. Of 9 yes-or-no answers kept, 7 were marked weak evidence.
- What Jev judged in the first run: 8 of 16 yes-or-no questions were settled and 8 were between the thresholds. All 3 ratings and the 1 choice were left unknown by their companion question. Two of those look answerable to a person: Jev picked "an in-house team" with probability 1.00 and a top search visibility with 0.84, and gave their companions 0.37 and 0.21. The companion question is strict.

### Functional Requirements

Requests:

- **FR-001**: The adapter calls the endpoint above over plain HTTPS. No provider package is used. The model is the `model_id` of the `ai_model` row.
- **FR-002**: A scoring run is one request. `state` is the material as named parts: `company`, `dossier`, and `facts`. A part with nothing in it is left out.
- **FR-003**: There is one question per criterion, named by the criterion's id. `YES_NO` is a `noul`. `RATING` is a `score` with six levels, 0 to 5. `CHOICE` is a `choice` whose option names are the criterion's codes and whose descriptions are their labels. The question's text is the criterion's prompt, with its `help` in brackets.
- **FR-004**: Every question has a companion `noul`, named by the criterion's id and `.answerable`, that asks whether there is enough information to answer the question. Decided 2026-11-24: a `YES_NO` question has one too.
- **FR-005**: A `NUMBER` criterion is not sent. Code answers it from the material: the fact, or the company field, named by the criterion's `enrichment_key`. Its probability is empty and its rationale says which fact it came from.

Reading the answers:

- **FR-006**: The model gives judgments, and code decides what is an answer. The wording of the questions and the thresholds are in one place in `src/ai.py`.
- **FR-007**: A `YES_NO` answer is `YES` at a probability of 0.7 or more and `NO` at 0.3 or less. Between them the criterion is left unknown. The stored probability is that of the stored answer.
- **FR-008**: A `RATING` or a `CHOICE` answer is kept when its companion is 0.7 or more. The answer is the most likely level or option, with its probability. A rating's average is not used, because it is pulled toward the middle.
- **FR-009**: Jev writes no reason, so the rationale of an answer holds its numbers, for example "Jev: yes 0.83. Enough information to answer: 0.35 (weak evidence)." The words "weak evidence" are added when the companion is under 0.7. For a `YES_NO` answer the companion only marks the evidence; it does not remove the answer. Decided 2026-11-24, so that a decision reached on weak evidence is clear to a reader later, with no new column.
- **FR-010**: Everything goes through the cleaning of feature 009 before it is stored.
- **FR-011**: Every judgment of a run is written to the Worker log (`carmen_typesafe_judgments`), the unsettled ones included, so that a threshold can be checked later. The material is not logged.
- **FR-012**: The run stores the model version that answered in `model_id_used`. This holds for any adapter that names one.

Failures:

- **FR-013**: A status other than 200 fails the run with the status. The API's message goes to the Worker log only. A failed call is never read as an answer.
- **FR-014**: A reply with no answers is unreadable output, and the run framework asks once more.
- **FR-015**: A run MUST NOT fall back to another model.
- **FR-016**: The adapter refuses research.

The key:

- **FR-017**: The API key is the Secrets Store secret `carmen-typesafe-api-key`, bound as `TYPESAFE_API_KEY` in production and in the preview. It is read when a request is sent, and is never logged, stored, or returned.

Testing:

- **FR-017a**: The Jev model is enabled in both databases by a migration. In production it can run once this feature is deployed.
- **FR-018**: `preview-test.sh` and the pipeline MUST NOT call TypeSafe. They use the stub.
- **FR-019**: `./ai-test.sh run <model id> [website] [--keep] [--research <model id>]` takes the model that researches separately, for a model that only scores.
- **FR-020**: The unit tests give the adapter canned replies and make no network call.

### Endpoints

None are added or changed. Jev is run through the endpoints of feature 009, with their actions: starting a run is `WRITE`, and editing a model is `MANAGE`.

### Not in this feature

- Level descriptions written for each rating criterion. All ratings share six general levels.
- Tuning the thresholds. One run is too little to tune on.
- Trying a request again after status 429.
- The long context header, images, and the `box` question type.

### What is not tested

- A `CHOICE` answer kept from a real run. That case is covered with canned replies. A `RATING` was kept in the third real run, with a companion of 0.73.
- A real error from the API, and a real 429.
- More than three real runs, all on the same company.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors and no network call.
- **SC-002**: `./preview-test.sh` passes and calls no real model.
- **SC-003**: `./ai-test.sh run <the Jev model id> --research <model id>` on the preview ends with both runs `SUCCEEDED`.
- **SC-004**: The check `Preview and smoke test` passes.

## Manual steps

None for the model: it is enabled in the preview and in production by the migration `0005_enable_jev` in `andexor-carmen-main` (issue #24 there), applied to both on 2026-11-24. Decided 2026-11-24: the model comes enabled, with no manual step. Listed in `MANUAL-TASKS.md`: merge that migration's pull request unchanged.
