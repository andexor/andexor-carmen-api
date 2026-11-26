<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Every route's request and response in the OpenAPI specification

**Feature Branch**: `68-openapi-models`

**Created**: 2026-11-26

**Status**: Implemented. Every route has a described answer and a described body.

**Input**: Issue #68. Asked for on 2026-11-26: the specification of `specs/031-openapi/spec.md` listed the routes but said of most only that they take and answer "an object". The fields are to come from the database schema and from the code, not from models written by hand. This spec replaces the section "Routes whose description is still incomplete" of spec 031 and its FR-007. Where this spec and an earlier document differ, this spec governs.

## Why

A client developer must be able to read the fields of every request and answer from the contract. The database already knows the fields of each record, and the code already knows which fields a body may set. Writing them a third time by hand would let the three drift apart.

## User Scenarios and Testing

### User Story 1 - The contract names the fields (Priority: P1)

**Independent Test**: Signed in, open `/api/docs` and any route. Its answer and its body show their fields and types.

**Acceptance Scenarios**:

1. **Given** a route that answers one stored record, such as `GET /api/v1/tasks`,\
   **When** its answer is read in the specification,\
   **Then** it has the columns of the table, with their types, and says which may be empty.
2. **Given** a route that answers more than one record, such as `GET /api/v1/companies/{company_id}`,\
   **When** its answer is read,\
   **Then** it has the record's fields and the fields that are joined or computed.
3. **Given** a route that takes a JSON body,\
   **When** its body is read,\
   **Then** it has the fields the code accepts.
4. **Given** a route that answers 201, 202, or 204,\
   **When** it is read,\
   **Then** that status is the one listed.

### User Story 2 - The models cannot drift (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a migration in `andexor-carmen-main` adds a column, or a field list in `src/records.py` changes,\
   **When** `./unit-test.sh` runs before the generator was run again,\
   **Then** it fails and names the command to run.
2. **Given** a handler answers a field that its model does not have, or leaves one out,\
   **When** the smoke test runs,\
   **Then** it fails and names the route and the field.
3. **Given** a body that the API accepted in the smoke test,\
   **When** it does not fit the documented body,\
   **Then** the smoke test fails.
4. **Given** a request that the API accepted before this feature,\
   **When** it is sent now,\
   **Then** it is accepted as before: a model describes a body and never refuses one.

### Edge Cases

- The database stores yes-or-no as 0 or 1 and a date as text. The model says so in the field's description; the type is the stored one.
- A column that holds an SRT code names its domain in its description. The codes themselves are data and are read from `GET /api/v1/srt`.
- Columns that no answer carries are left out of the models: `document.r2_key`, `ai_run.dossier` (only a research run's detail carries it), `ai_run_fact.ai_run_id`, and `active` of `srt` and `enrichment_key`.
- `GET /api/v1/facts/keys` also lists keys that only stored facts use. Those have no label, so its model is written by hand.
- A body model has every field optional: which fields a route needs is checked by the code, as before.
- The document download answers the file's bytes and is described as binary.
- The triggers in the migrations need SQLite 3.47. The generator leaves them out, because it needs only the tables.

## Requirements

### Functional Requirements

- **FR-001**: `tools/gen_models.py` writes `src/models.py`: one model for each table, from the migrations in `andexor-carmen-main` (found through `CARMEN_MAIN`, as `run.sh` finds them), and one body model for each field list `*_FIELDS` in `src/records.py`. `src/models.py` MUST NOT be edited by hand.
- **FR-002**: `src/contract.py` holds what is more than one record: a page and a list of any record, records with joined or computed fields, small results, and the bodies that are not one record's fields. Each is built on the generated models where it can be.
- **FR-003**: Every route declares its answer with `response_model` and its real `status_code`. Handlers still answer with their own response, so a model describes an answer and does not change it.
- **FR-004**: Every route with a JSON body declares it with `@takes(<model>)` under `@action`. The handler still reads the body as a free object and checks it itself, so no request is refused because of a model. `describe` puts the model into the specification.
- **FR-005**: A model of an answer allows no field it does not name, and requires each field it names unless the model says otherwise.
- **FR-006**: `./unit-test.sh` fails when `src/models.py` is not what the generator writes, when an operation has a success answer or a JSON body with no model, and when the specification is not valid OpenAPI 3.1.
- **FR-007**: `preview-test.sh` records every call and its answer, and at the end `tools/contract_check.py` compares each successful one with the specification the preview serves: the status, the answer, and the body that was sent.
- **FR-008**: A new route comes with its `response_model`, its `status_code`, and `@takes` when it has a body, and with a smoke test call, so its model is checked against a real answer.

### Endpoints

None are added. Every endpoint keeps its action. These statuses are now listed as the code answers them: 201 on creating routes, 202 on the routes that start a job (`POST /companies/{company_id}/runs`, `.../runs/research-and-score`, `POST /imports`, `POST /agreements/{agreement_id}/proposals`), 200 or 201 on `POST /machine/leads`, and 204 on the deletes.

### Not checked against a real answer

The smoke test reaches 65 of the 79 operations. The other 14 have models from the schema and the code, but no recorded answer:

- The nine deletes. No test calls a delete endpoint (`specs/030-keep-preview-data/spec.md`). Eight answer 204 with no body; `DELETE /imports/{import_id}` answers a count.
- `POST /services`, `PATCH /services/{service_id}`, `POST /rubric`, `PATCH /rubric/{criterion_id}`, and `POST /ai/models`. They change the shared catalog, the rubric, and the list of models, which the smoke test leaves alone.

A list that was empty in every recorded answer has its item model from the code alone: an agreement's `children`, a company's `services` and `active_agreements`, Today's `tasks_due`, `top_leads`, and `runs_in_progress`, and a proposal's `facts`.

### Not in this feature

- Validation of a request by its model. The code's own checks stay the only ones.
- Lists of allowed codes in the specification.
- Generated client code.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: The specification has no success answer and no JSON body without a model.
- **SC-003**: `./preview-test.sh` passes, and its contract check reports that every recorded answer fits the specification.
