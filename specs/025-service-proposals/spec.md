<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Service proposals

**Feature Branch**: `54-service-proposals`

**Created**: 2026-11-25

**Status**: Implemented, and live on the preview.

**Input**: Issue #54. Asked for on 2026-11-24, with the renewal policy (`specs/024-renewal-policy/spec.md`): when a one-time agreement is completed, an email proposes one or more other services to offer that customer, shows the research and the scoring behind each one, and puts the best match between a need and a service first. The job is started through the queue. The same list is on the Company screen at any time. Decided 2026-11-25: the job runs fresh research and scoring first, and does not rely on the stored scores. Decided later that day: when a run fails, the job tries the next model that can do the part, and the one email of a job lists every run, failed or not. Where this spec and an earlier document differ, this spec governs.

## The design

```
   the scheduler completes         a person sets a one-time        an administrator, or the
   a one-time agreement            agreement to COMPLETED          smoke test
              |                              |                              |
              |                              |     POST /api/v1/agreements/{id}/proposals
              v                              v                              v
   +------------------------------------------------------------------------------+
   |  the RUNS queue: one message with proposal_agreement_id                      |
   +------------------------------------------------------------------------------+
                                             |
                                             v
   +------------------------------------------------------------------------------+
   |  process_proposals, written once                                             |
   |                                                                              |
   |  1. research     a research run; when it fails, the next model is tried      |
   |  2. score        a scoring run on that research, with the same fallback      |
   |  3. apply        the run's answers and scores become the company's           |
   |  4. list         the other services worth proposing, best match first        |
   |  5. email        one message to digest.recipient, stored as a notification   |
   +------------------------------------------------------------------------------+

   the Company screen, at any time:  GET /api/v1/companies/{id}/proposals  (step 4 alone)
```

The code is the same in the preview and in production. The difference is two variables. `PROPOSAL_MODELS` is `DEFAULT` in production, where the job uses the default models of the settings, and `STUB` in the preview, where it uses the stub model. `EMAIL_PROVIDER` is as in `specs/015-scheduler-and-notifications/spec.md`. So the hourly runs and the pipeline pay for no model and send no email. A person who is testing asks for a real job, one at a time, with `real=true`.

## User Scenarios and Testing

### User Story 1 - An email with proposals when a project is done (Priority: P1)

A one-time agreement is completed. Without anyone asking, Carmen looks at the customer again and sends one email that says what to offer next, and why.

**Independent Test**: `./preview-test.sh` lets the scheduler complete a one-time agreement and reads the stored email and the runs behind it.

**Acceptance Scenarios**:

1. **Given** an active one-time agreement past its end date,\
   **When** the scheduler completes it,\
   **Then** its proposals job is put on the queue.
2. **Given** a one-time agreement,\
   **When** a person sets its status to `COMPLETED`,\
   **Then** its proposals job is put on the queue.
3. **Given** a proposals job,\
   **When** it runs,\
   **Then** a research run and a scoring run are made for the company, the scoring run is applied, and one email is sent to `digest.recipient` and stored as a notification of the kind `SERVICE_PROPOSALS` for the agreement.
4. **Given** the email,\
   **When** it lists services,\
   **Then** the service with the highest Need score is first, and each service shows its Need score, its confidence, the answers to its questions with the model's reason, and the facts behind those answers.
5. **Given** no service is worth proposing,\
   **When** the job runs,\
   **Then** the email is still sent and says "No other service is a good match."

### User Story 2 - The same list on the Company screen (Priority: P1)

**Independent Test**: `./preview-test.sh` reads the list of the company after the job.

**Acceptance Scenarios**:

1. **Given** a company with stored scores,\
   **When** its proposals are read,\
   **Then** the answer holds `scored_at` and the services worth proposing in the order of the email, each with its answers and facts.
2. **Given** a service that an active agreement of the company already covers,\
   **When** the proposals are read,\
   **Then** that service is not in the list.
3. **Given** an id that no company has,\
   **When** its proposals are read,\
   **Then** the answer is 404.

### User Story 3 - A failed run is said, and the next model is tried (Priority: P1)

**Independent Test**: `./unit-test.sh` covers the list of runs in the email. `./preview-test.sh` reads that list in the stored email.

**Acceptance Scenarios**:

1. **Given** a research run fails,\
   **When** the job goes on,\
   **Then** a new research run is made with the next model that can research, until one succeeds or none is left.
2. **Given** a scoring run fails,\
   **When** the job goes on,\
   **Then** a new scoring run is made on the same research with the next model that can score.
3. **Given** a job that made runs,\
   **When** its email is written,\
   **Then** the email lists every run in the order it was made: research or scoring, the model, and "succeeded" or "failed" with the reason. Each run also has a line of its own in the Worker log.
4. **Given** no model could do the research or the scoring, or the scores could not be stored,\
   **When** the job goes on,\
   **Then** the email is still sent, and begins with "Fresh research and scoring failed", the reason, and the date of the scores the list comes from.

### Edge Cases

- A service is worth proposing when its Need score is at or above the setting `score.skip.max_need`, the score below which a company is skipped (30 when the setting is missing). Only a service that is offered and active is proposed.
- The email leaves out the services of the agreement that was just completed, because it proposes other services. The Company screen leaves out only the services of active agreements.
- Two services with the same Need score: the higher confidence is first, then the catalog order.
- The facts a research run proposes stay proposed. The scoring run reads them from the research run, as every scoring run with an input does; a person still decides which facts the company keeps.
- The order of the models is: the default model of the settings first, then every other enabled model that can do the part, in catalog order. The stub model is never used for a real job.
- A fallback is a run of its own with its own model. The failed run stays failed, so runs are still compared per model.
- A job tries at most as many models for research as the setting `proposal.max_tries` says (3 at first, and when the setting is missing), and as many again for scoring, so that it ends inside one queue invocation.
- The runs of a job have no requester, and the answers it applies have no author. The record of the change in the Worker log names the job, "proposals", where a request has its id.
- With no `digest.recipient` the job does nothing and says so in the Worker log: no run is made, because nobody would read the result.
- An agreement that is deleted before its job runs: the job does nothing.
- A message that the provider refuses is stored with the status `FAILED`, as every notification is.
- A job that cannot be put on the queue is logged. The agreement stays completed, and the edit of a person still succeeds.
- A job started by hand works for any agreement, whatever its status and policy.

## Requirements

### Functional Requirements

Starting the job:

- **FR-001**: When the scheduler ends a one-time agreement (`COMPLETED`), it puts the agreement's proposals job on the `RUNS` queue. An agreement that expires gets none.
- **FR-002**: When an edit sets the status of an agreement whose renewal policy is `ONE_TIME` to `COMPLETED`, the same job is put on the queue after the edit is stored.
- **FR-003**: `POST /api/v1/agreements/{id}/proposals` puts the job on the queue and answers 202 with `queued` true. `real=true` asks for the default models and real email, whatever the environment says. The smoke test and the pipeline never ask for it.
- **FR-004**: A queue message for the job holds `proposal_agreement_id` and `real`. `src/worker.py` tells it from a run and from an import.

The job:

- **FR-005**: The job is written once, with no request, and never raises.
- **FR-006**: Where the variable `PROPOSAL_MODELS` is `DEFAULT`, or with `real`, the research models are the setting `ai.default_research_model` and then every other enabled model that can research, in catalog order; the scoring models are `ai.default_scoring_model` and then every other enabled model that can score. A model whose provider has no adapter, and the stub, are left out. Any other value of the variable, or none, is the stub model alone.
- **FR-007**: The job makes a research run, then a scoring run on that research. When a run fails it makes a new run with the next model, at most `proposal.max_tries` models for each part. All of it is inside its own queue invocation, with the code every run uses. It applies the scoring run with the code a person's apply uses: the answers are replaced, the scores are stored, and the change is recorded.
- **FR-008**: When no model succeeds, or the apply fails, the job goes on. The email says so first, with a reason that is safe to show, and the list then comes from the stored scores.
- **FR-014**: Every run of a job writes a line to the Worker log, and the email lists every run with its model and its result. An email is sent for every job that has a recipient, whatever its runs did.
- **FR-009**: The email goes to the setting `digest.recipient` through the provider of `EMAIL_PROVIDER`, and is stored as a `notification` row of the kind `SERVICE_PROPOSALS` with the agreement's id. Its template is `src/templates/email/proposals.txt`.

The list:

- **FR-010**: The list holds every offered, active service of the company's stored scores whose Need score is at or above `score.skip.max_need` and that no active agreement of the company covers.
- **FR-011**: The order is the Need score, highest first; then the confidence, highest first; then the catalog order.
- **FR-012**: Each service holds `service_id`, `name`, `category`, `need_score`, `confidence`, `answers` (the company's answers to the service's Need questions: `criterion_id`, `prompt`, `value`, `points`, `source`, and `rationale` when a run gave one), and `facts` (the company's latest fact for each key those questions name).
- **FR-013**: `GET /api/v1/companies/{id}/proposals` answers `scored_at` and `items`. It reads the stored scores and starts nothing.

### Endpoints

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /api/v1/companies/{id}/proposals` | `READ` | The services worth proposing to the company, best match first |
| `POST /api/v1/agreements/{id}/proposals` | `MANAGE` | Puts the agreement's proposals job on the queue. `real=true`: the default models and real email |

### Configuration

In `wrangler.jsonc`: the variable `PROPOSAL_MODELS`, `DEFAULT` at the top level and `STUB` under `env.preview`.

### Data model

Migration `0013_service_proposals_kind` in `andexor-carmen-main` adds the code `SERVICE_PROPOSALS` to the domain `NOTIFICATION_KIND`. Migration `0014_proposal_max_tries` adds the setting `proposal.max_tries`, a whole number of at least 1, with the value 3. No table changes.

### Not in this feature

- The dossier of the research run in the email. The email shows the answers, their reasons, and the facts; the dossier is on the run.
- A setting of its own for the lowest Need score that is proposed. It follows `score.skip.max_need`.
- A proposal for an agreement that expires or is terminated.
- A task, or a deal, made from a proposal.
- Trying a failed job again.
- Email in the HyperText Markup Language (HTML). It is parked in `NOTES.md`.
- A script for a real job. It is one call: `POST /api/v1/agreements/{id}/proposals?real=true`.

### What is not tested

- A job with the default models and real email. It costs money, and no adapter, prompt, or model changed. The preview ran the job with the stub.
- A job whose run fails, and so the fallback to a next model, against a deployed Worker: the preview has the stub alone, which does not fail. The wording of the email is covered by a unit test.
- The job started by a person's edit to `COMPLETED`. The smoke test starts it through the scheduler and by hand; the edit calls the same function.
- A job on a company that is deleted while it runs.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: the scheduler completes a one-time agreement, the proposals email is stored with stub runs behind it and no failure in it, a second job started by hand stores a second email, the company's list is read, and an unknown company and an unknown agreement are refused.

## Manual steps

- [ ] After the first one-time agreement is completed in production, confirm that the proposals email reached the inbox and that its research is from that day.
