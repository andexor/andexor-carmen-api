<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Deals

**Feature Branch**: `25-deals`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #25. Backlog feature 007: deals and the stage changes that follow from them. The rules of the earlier specs for bodies, codes, errors, paging, dates, own records, and recorded changes apply here unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Open a deal with almost nothing (Priority: P1)

A salesperson records that there is a possible sale with a company. Only the company is needed.

**Independent Test**: The smoke test creates a deal for its test company with nothing but the company and one service.

**Acceptance Scenarios**:

1. **Given** a company named Acme,\
   **When** a deal is posted with only `company_id`,\
   **Then** the answer is 201 with the name `Acme deal`, stage `QUALIFYING`, currency `USD`, and the caller as owner.
2. **Given** a company in stage `LEAD`,\
   **When** its first deal is created,\
   **Then** the company moves to `PROSPECT`, and a `STAGE_CHANGE` entry that points to the deal is on its timeline, in the same transaction.
3. **Given** a company in any other stage,\
   **When** a deal is created,\
   **Then** the company's stage does not change.

### User Story 2 - Move a deal through its stages (Priority: P1)

**Acceptance Scenarios**:

1. **Given** an open deal,\
   **When** its stage is changed,\
   **Then** a `STAGE_CHANGE` entry with the deal's name and the old and new stage is on the company's timeline.
2. **Given** an open deal,\
   **When** its stage becomes `WON` or `LOST`,\
   **Then** `closed_at` and `total_deal_time` are set by the database.
3. **Given** a closed deal,\
   **When** its stage becomes an open one again,\
   **Then** `closed_at` and `total_deal_time` are cleared.

### User Story 3 - Say what a deal covers and what it is worth (Priority: P2)

**Acceptance Scenarios**:

1. **Given** a deal,\
   **When** it is patched with `service_ids`,\
   **Then** the services it covers are replaced by that list.
2. **Given** a deal,\
   **When** a value, a date, a note, or its services are changed,\
   **Then** a permanent entry with the old and new values is on the company's timeline, written first in the same transaction.

### User Story 4 - Remove a deal (Priority: P2)

**Acceptance Scenarios**:

1. **Given** a `MEMBER` and a deal they own,\
   **When** they delete it,\
   **Then** the answer is 204, and the timeline has a permanent entry with what the deal held.
2. **Given** a `MEMBER` and a deal owned by someone else or by nobody,\
   **When** they delete it,\
   **Then** the answer is 403.
3. **Given** an `ADMIN`,\
   **When** they delete any deal,\
   **Then** the answer is 204.

### Edge Cases

- A company with no name gives the deal the name `<domain> deal`.
- A deal created already `WON` or `LOST` is closed at once.
- A deal cannot be moved to another company. `company_id` in a patch is ignored.
- A primary contact must be a contact of the deal's company.
- A service repeated in `service_ids` counts once. A service that does not exist refuses the whole request.
- Deleting a deal leaves its timeline entries and tasks in place, no longer pointing to a deal.
- A won deal does not make the company a customer. An active agreement does that, in feature 008.

## Requirements

### Endpoints and their actions

All paths are under `/api/v1`.

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /deals` | `READ` | List and filter deals |
| `POST /deals` | `WRITE` | Create a deal |
| `GET /deals/{id}` | `READ` | One deal |
| `PATCH /deals/{id}` | `WRITE` | Change a deal, its stage, and its services |
| `DELETE /deals/{id}` | `WRITE`, then FR-012 | Delete a deal |

### Functional Requirements

- **FR-001**: Every deal in an answer is the `deal` row plus `company_name` and `service_ids`, a sorted list.
- **FR-002**: `GET /deals` returns `{"items": [...], "next_cursor": ...}`, newest first. Filters: `stage`, `company_id`, and `owner_id` are exact; `open=true` leaves out `WON` and `LOST`.
- **FR-003**: The writable fields are `name`, `stage`, `engagement_model`, `primary_contact_id`, `one_time_value_cents`, `monthly_value_cents`, `currency`, `expected_close_date`, `lost_reason`, `notes`, `owner_id`, and `service_ids`, and on create `company_id`. Only `company_id` is required.
- **FR-004**: `stage` MUST be an active `DEAL_STAGE` code and `engagement_model` an active `ENGAGEMENT_MODEL` code. A `company_id` that names no company, a contact of another company, a service that does not exist, and a negative amount each get 400.
- **FR-005**: A deal created with no name is named `<company name> deal`, or `<domain> deal` when the company has no name. A deal created with no `owner_id` key is owned by the caller.
- **FR-006**: Creating a deal for a company in stage `LEAD` sets the company to `PROSPECT` and adds a `STAGE_CHANGE` entry with the deal's id, in the same transaction as the deal.
- **FR-007**: A change of a deal's stage is written to the Worker log, and then adds a `STAGE_CHANGE` entry on the company's timeline, with the deal's id, its name, and the old and new stage, in the same transaction as the change.
- **FR-008**: `closed_at` and `total_deal_time` are set and cleared by the database, never by the API. The API reads the deal back after a write so that the answer shows them.
- **FR-009**: `service_ids` in a patch replaces the deal's services. Leaving the key out leaves them as they are.
- **FR-010**: A patch sets `updated_at`. A patch that changes nothing writes nothing.
- **FR-011**: An edit of anything but the stage, and a delete, MUST be recorded before they are carried out, as feature 005 does for facts (its FR-013): a permanent `SYSTEM` entry with the old and new values, or with what the deal held, first in the same transaction. A stage change is recorded by FR-007 instead.
- **FR-016**: The order is fixed for every edit and delete of a deal: the Worker log first, then the timeline entry and the change together. The Worker log line is written before anything is sent to the database, so it exists even when the database then refuses the change. Decided 2026-11-23.
- **FR-012**: `DELETE /deals/{id}` declares the action `WRITE`, then allows the delete for a role with the `DELETE` action, or when `owner_id` is the caller (feature 003, FR-017). Otherwise the answer is 403.
- **FR-013**: `GET /companies/{id}` also returns `open_deals`: the company's deals that are neither `WON` nor `LOST`, newest first.
- **FR-014**: `expected_close_date` is put in ISO 8601 form by the common date rule.
- **FR-015**: The unit tests cover the id list rule. `preview-test.sh` creates a deal, sees the company move to `PROSPECT`, closes the deal as won, finds the stage entry and the record of the edit, sees that no open deal is left, deletes the deal, and finds the record of what it held.

### Not in this feature

- Moving a deal to another company.
- Clearing `lost_reason` when a deal leaves `LOST`.
- A total of deal values. The UI adds them up.

### What is not tested

- A real `MEMBER` or `READER` through Cloudflare Access. In a local run, a `READER` was refused every write; a `MEMBER` could change a deal, deleted their own, and was refused another user's deal and an unowned one.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company and no test deal behind.
- **SC-003**: The check `Preview and smoke test` passes.
