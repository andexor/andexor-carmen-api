<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Enrichment facts

**Feature Branch**: `21-enrichment-facts`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #21. Backlog feature 005: facts about a company, each a key and a value with its source and date. The rules for bodies, errors, paging, and dates of the earlier specs apply here unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Record what is learned (Priority: P1)

A salesperson records a fact about a company with a key and a value. Where it came from, a note, and when it was observed are optional.

**Independent Test**: The smoke test adds one fact and then two more to its test company and reads them back.

**Acceptance Scenarios**:

1. **Given** a company,\
   **When** a body with a key and a value is posted to its facts,\
   **Then** the answer is 201 with the stored fact, source `MANUAL`, observed now.
2. **Given** a company,\
   **When** several facts are posted under `facts`,\
   **Then** all are stored and returned, in the order given.
3. **Given** several facts in one request,\
   **When** one of them has no key or no value,\
   **Then** the answer is 400 and none is stored.

### User Story 2 - The newest fact wins (Priority: P1)

A newer fact with the same key replaces the older one in effect, and the older one stays on record.

**Acceptance Scenarios**:

1. **Given** a company with two facts of the same key,\
   **When** its facts are requested,\
   **Then** both are listed, newest first.
2. **Given** the same company,\
   **When** its facts are requested with `latest=true`,\
   **Then** only the newest fact of each key is listed, by key.

### User Story 4 - Nothing is lost when a fact is changed or removed (Priority: P1)

A fact can be corrected or removed, and what it said before is kept.

**Acceptance Scenarios**:

1. **Given** a fact,\
   **When** an `ADMIN` or a `MEMBER` changes it,\
   **Then** the company's timeline has a permanent entry with each changed field's old and new value, and the caller.
2. **Given** a fact,\
   **When** an `ADMIN` or a `MEMBER` removes it,\
   **Then** the company's timeline has a permanent entry with every value the fact held.
3. **Given** a change that the database refuses,\
   **When** the request ends,\
   **Then** neither the change nor its record is stored.
4. **Given** a `READER`,\
   **When** they change or remove a fact,\
   **Then** the answer is 403.

### User Story 3 - Keys are suggested, not enforced (Priority: P2)

Any key is allowed, so a tool can add a fact without a change to the database. The well-known keys are offered for auto-complete, and each says what kind of value it holds.

**Acceptance Scenarios**:

1. **Given** any signed-in user,\
   **When** `GET /api/v1/facts/keys` is requested,\
   **Then** the active well-known keys are listed in their order, followed by every other key already used in a fact.
2. **Given** a well-known yes-or-no key,\
   **When** a fact is posted with the value `yes`,\
   **Then** it is stored as `YES`.
3. **Given** a well-known date key,\
   **When** a fact is posted with the value `Dec 1, 2027`,\
   **Then** it is stored as `2027-12-01`.

### Edge Cases

- A key is put in lowercase snake case: `Has Website` and `has-website` both become `has_website`. A key with no letter or digit is refused.
- A number given as a value is stored as text, as every value is.
- A value for a well-known key is not checked against the key's type, except that a date that is not understood is refused. The key list is a hint, not a constraint.
- When a company is deleted, its timeline goes with it, including these records.
- When two facts of one key were observed at the same moment, the one created later is the newest.
- Deleting a company deletes its facts.

## Requirements

### Endpoints and their actions

All paths are under `/api/v1`.

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /companies/{id}/facts` | `READ` | The company's facts |
| `POST /companies/{id}/facts` | `WRITE` | Add one fact or many |
| `PATCH /facts/{id}` | `WRITE` | Change a fact |
| `DELETE /facts/{id}` | `WRITE` | Remove a fact |
| `GET /facts/keys` | `READ` | Keys for auto-complete |

An `ADMIN` and a `MEMBER` may change and remove any fact. A fact has no owner column, so there is no own-record rule here. Every change and removal is recorded first (FR-013). Decided 2026-11-23.

### Functional Requirements

- **FR-001**: `GET /companies/{id}/facts` returns `{"items": [...], "next_cursor": ...}`, ordered by `observed_at`, newest first. `limit` and `cursor` work as for companies.
- **FR-002**: With `latest=true`, the list holds the newest fact of each key, ordered by key.
- **FR-003**: `POST /companies/{id}/facts` takes either one fact as the body, or `{"facts": [...]}` with at least one fact. The answer is 201 with `{"items": [...]}`.
- **FR-004**: The writable fields are `key`, `value`, `source_detail`, `notes`, and `observed_at`. `key` and `value` are required. `observed_at` defaults to now and is put in ISO 8601 form like every date.
- **FR-005**: `source` is always `MANUAL` and `ai_run_id` is always empty for a fact added here. Other sources are written by the features that own them: accepted research facts (009), the import (013), and the machine methods (014).
- **FR-006**: The key is stored in lowercase snake case: letters are lowered and every run of other characters becomes one underscore.
- **FR-007**: When the key is a well-known key of type `YES_NO`, a value of yes or no in any letter case is stored in capitals. When it is of type `DATE`, the value is put in the form `YYYY-MM-DD`, and a date that is not understood gets 400. No other value is checked or changed.
- **FR-008**: All facts of one request are stored in one transaction. One fact that is not valid refuses them all.
- **FR-009**: `DELETE /facts/{id}` answers 204.
- **FR-013**: An edit and a delete MUST be recorded before they are carried out. The record is a timeline entry of kind `SYSTEM` on the fact's company, with the caller as `created_by`. For an edit its body names the fact and each changed field with its old and new value. For a delete it names the fact and every value it held. The record is the first statement of the same transaction as the change, so a change cannot be stored without its record, and a record cannot be stored for a change that failed. The same text is written to the Worker log first, with the company, the caller, and the request id, before the transaction is sent. So the order is: the Worker log, then the timeline entry and the change together. Decided 2026-11-23.
- **FR-014**: A `SYSTEM` entry is automatic, so nobody can change or delete the record (feature 003, FR-006).
- **FR-015**: `PATCH /facts/{id}` changes `key`, `value`, `source_detail`, `notes`, and `observed_at`, under the rules of FR-004, FR-006, and FR-007. `key` and `value` cannot be blanked. `source` and `ai_run_id` do not change, so a fact from a research run that is corrected by hand still shows where it came from. A patch that changes nothing writes no record.
- **FR-010**: `GET /facts/keys` returns `{"items": [...]}` with `key`, `label`, `value_type`, `description`, and `sort_order`: the active rows of `enrichment_key` in their order, then each key used in `enrichment_fact` that is not in that table, with type `TEXT` and no label.
- **FR-011**: An id that names nothing gets 404.
- **FR-012**: The unit tests cover the key rule, the value rule, and the text of a record. `preview-test.sh` adds one fact and then two, is refused a request with one bad fact, reads all facts and the newest per key, finds its key among the suggestions, changes a fact and finds the record of the old value, and removes it and finds the record of what it held. Deleting the test company removes the rest.

### Not in this feature

- Answering a rubric question from a fact. Feature 009 does that for scoring runs.
- Facts in the company detail. They are read from their own endpoint.
- A filter by key or by source.

### What is not tested

- A real `MEMBER` or `READER` through Cloudflare Access. In a local run, a `READER` could read and was refused adding, changing, and removing; a `MEMBER` could add, change, and remove, and could not delete the record of a change.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company and no test fact behind.
- **SC-003**: The check `Preview and smoke test` passes.
