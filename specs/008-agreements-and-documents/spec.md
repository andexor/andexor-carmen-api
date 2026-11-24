<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Agreements and documents

**Feature Branch**: `27-agreements-and-documents`

**Created**: 2026-11-23

**Status**: Implemented. Changed by `specs/024-renewal-policy/spec.md`: the field `auto_renews` is gone, and an active agreement needs a renewal policy and an end basis.

**Input**: Issue #27. Backlog feature 008: agreements, the documents stored with them, the R2 buckets, and discovery task D7. The rules of the earlier specs for bodies, codes, errors, paging, dates, own records, and the change log (`specs/018-change-log/spec.md`) apply here unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Record what was agreed (Priority: P1)

An account manager records an agreement with a company. Only the company and the type are needed.

**Independent Test**: The smoke test creates a Master Services Agreement (MSA) for its test company, activates it, and removes it again.

**Acceptance Scenarios**:

1. **Given** a company,\
   **When** an agreement is posted with `company_id` and `type`,\
   **Then** the answer is 201 with status `DRAFT`, currency `USD`, and the caller as owner.
2. **Given** an agreement of the company,\
   **When** another is created with it as parent,\
   **Then** the first agreement's detail lists the second among its `children`.
3. **Given** an agreement of another company,\
   **When** it is named as parent, or a deal of another company is named,\
   **Then** the answer is 400.

### User Story 2 - A customer is a company with an agreement in effect (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a company that is not a `CUSTOMER`,\
   **When** one of its agreements becomes `ACTIVE`, on create or on change,\
   **Then** the company moves to `CUSTOMER`, with a `STAGE_CHANGE` entry, in the same transaction.
2. **Given** a `CUSTOMER` with two active agreements,\
   **When** one of them ends,\
   **Then** the company stays a `CUSTOMER`.
3. **Given** a `CUSTOMER` with one active agreement,\
   **When** it leaves `ACTIVE`,\
   **Then** the company moves to `FORMER`, with a `STAGE_CHANGE` entry, in the same transaction.

### User Story 3 - Keep the signed document (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a company,\
   **When** a file is uploaded to its documents,\
   **Then** the file is stored in the private bucket, and the answer is 201 with its name, type, size, and SHA-256 checksum.
2. **Given** a document,\
   **When** its content is requested,\
   **Then** the same bytes come back, with the stored content type and a header that makes a browser save it under its name.
3. **Given** a file larger than the setting `document.max_size_bytes`,\
   **When** it is uploaded,\
   **Then** the answer is 413 and nothing is stored.
4. **Given** a document,\
   **When** it is deleted,\
   **Then** its row and its file are both gone, and the timeline has the record of what it was.

### User Story 4 - What can be removed (Priority: P2)

**Acceptance Scenarios**:

1. **Given** an agreement that is not a `DRAFT`, or that has children,\
   **When** it is deleted,\
   **Then** the answer is 409.
2. **Given** a company with an agreement or a document,\
   **When** the company is deleted,\
   **Then** the answer is 409.
3. **Given** a `MEMBER`,\
   **When** they delete a draft agreement they own, or a document they uploaded,\
   **Then** the answer is 204. For one that is not theirs it is 403.

### Edge Cases

- `terminated_at` is set to now when the status becomes `TERMINATED` and no time is given, and cleared when the status changes to anything else.
- An agreement cannot be its own parent, and cannot be moved to another company.
- A file name is the last part of a path with no control characters, at most 255 characters, and `document` when nothing is given.
- An empty file is refused with 400.
- When the file is stored and its row then fails, the file is removed again.
- When a row is deleted and its file then cannot be, the request still succeeds: no row points to the file.
- Deleting an agreement leaves its documents in place, no longer attached to an agreement.

## Requirements

### Discovery result

- **D7 (answered 2026-11-23)**: A 25 MiB file passes through the Python Worker in both directions. Measured on the preview from one machine, so the times include the transfer: upload 9.0 seconds, download 5.3 seconds; 5 MiB took 2.3 and 2.6 seconds; 1 MiB took 1.2 and 1.8 seconds. Each file came back identical and its checksum matched. The limit of 25 MiB stays.
- **What D7 found on the way**: the first attempt took 4.5 seconds for 1 MiB and failed above that. The Workers Python layer checks every value given to a binding and walks anything iterable one item at a time, so a byte array of 5 MiB exceeded the Worker's limits. The bytes are now given to the bucket as a JavaScript `ArrayBuffer`, which is not walked. `CLAUDE.md` records the rule.
- An upload is held in memory once in Python and once more when it is handed to the bucket. A download is streamed and never held whole.

### Endpoints and their actions

All paths are under `/api/v1`.

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /agreements` | `READ` | List and filter agreements |
| `POST /agreements` | `WRITE` | Create an agreement |
| `GET /agreements/{id}` | `READ` | The agreement with its children and documents |
| `PATCH /agreements/{id}` | `WRITE` | Change an agreement, its status, and its services |
| `DELETE /agreements/{id}` | `WRITE`, then FR-011 | Delete a draft |
| `GET /companies/{id}/documents` | `READ` | The company's documents |
| `POST /companies/{id}/documents` | `WRITE` | Upload a file |
| `GET /documents/{id}` | `READ` | A document's details |
| `GET /documents/{id}/content` | `READ` | The file |
| `DELETE /documents/{id}` | `WRITE`, then FR-021 | Delete a document |

### Functional Requirements

Agreements:

- **FR-001**: Every agreement in an answer is the `agreement` row plus `company_name` and `service_ids`, a sorted list.
- **FR-002**: `GET /agreements` returns `{"items": [...], "next_cursor": ...}`, soonest end date first. Filters: `company_id`, `type`, `status`, and `engagement_model` are exact. `renewing_within_days=N` lists active agreements whose end date is no later than N days from today, where today is the date in the `digest.time_zone` setting.
- **FR-003**: The writable fields are `deal_id`, `parent_agreement_id`, `type`, `title`, `status`, `engagement_model`, `amount_cents`, `amount_period`, `setup_fee_cents`, `performance_terms`, `currency`, `signed_date`, `effective_date`, `end_date`, `auto_renews`, `renewal_notice_days`, `terminated_at`, `notes`, `owner_id`, and `service_ids`, and on create `company_id`. `company_id` and `type` are required.
- **FR-004**: `type`, `status`, `engagement_model`, and `amount_period` MUST be active codes of `AGREEMENT_TYPE`, `AGREEMENT_STATUS`, `ENGAGEMENT_MODEL`, and `AMOUNT_PERIOD`. A company, parent, deal, or service that does not exist, a parent or deal of another company, and a negative amount each get 400.
- **FR-005**: An agreement created with no `owner_id` key is owned by the caller.
- **FR-006**: `GET /agreements/{id}` also returns `children`, the agreements whose parent it is, and `documents`, the details of the documents attached to it.
- **FR-007**: When an agreement becomes `ACTIVE` and its company is not a `CUSTOMER`, the company moves to `CUSTOMER`.
- **FR-008**: When an agreement leaves `ACTIVE`, the company is a `CUSTOMER`, and no other agreement of the company is `ACTIVE`, the company moves to `FORMER`.
- **FR-009**: The moves of FR-007 and FR-008 go to the Worker log, and then add a `STAGE_CHANGE` entry in the same transaction as the agreement's change. A stage can still be set by hand.
- **FR-010**: `service_ids` in a patch replaces the agreement's services. The dates are put in ISO 8601 form by the common date rule. A patch sets `updated_at`.
- **FR-011**: `DELETE /agreements/{id}` declares the action `WRITE`, then allows the delete for a role with the `DELETE` action, or when `owner_id` is the caller. Otherwise the answer is 403. Then only a `DRAFT` with no children is deleted; any other gets 409.
- **FR-012**: Every edit and delete of an agreement is recorded as the change log requires. A status change is recorded as an edit.
- **FR-013**: `GET /companies/{id}` also returns `active_agreements`, soonest end date first.

Documents:

- **FR-014**: A document is a row with `id`, `company_id`, `agreement_id`, `file_name`, `content_type`, `size_bytes`, `sha256`, `uploaded_by`, and `uploaded_at`, and a file in the private R2 bucket bound as `DOCS`, at the key `<company_id>/<document_id>`. The key is not returned.
- **FR-015**: `POST /companies/{id}/documents` takes the file as the request body, not as a multipart form. The file name is the query parameter `file_name`, the type is the `Content-Type` header (`application/octet-stream` when absent), and `agreement_id` is an optional query parameter that MUST name an agreement of the same company. Decided 2026-11-23: a raw body needs no form-parsing package and no second copy of the file in memory, and a browser sends it with one `fetch` call.
- **FR-016**: The size limit is the setting `document.max_size_bytes`, 26214400 (25 MiB) when missing. A larger body gets 413. An empty body gets 400.
- **FR-017**: The API computes the SHA-256 checksum and gives it to the bucket with the file. The bucket refuses a body that does not match it.
- **FR-018**: The file is written first, then the row. A row that fails removes the file again.
- **FR-019**: `GET /documents/{id}/content` streams the file with the stored `Content-Type` exactly, and `Content-Disposition: attachment` with the file name, in plain form and in UTF-8 form. There are no public links and no pre-signed links: a file is reached only through the API, by a signed-in user.
- **FR-020**: `DELETE /documents/{id}` deletes the row first, then the file, and answers 204.
- **FR-021**: `DELETE /documents/{id}` declares the action `WRITE`, then allows the delete for a role with the `DELETE` action, or when `uploaded_by` is the caller. The delete is recorded as the change log requires, with the name, type, size, and checksum.

Environments:

- **FR-022**: `wrangler.jsonc` binds `DOCS` to the bucket `andexor-carmen-api-prod` for production and to `andexor-carmen-api-prev` under `env.preview`. A unit test fails when the two environments share a bucket or a database.
- **FR-023**: `preview-create.sh` creates the preview bucket when it is missing. `preview-delete.sh` deletes it when it is empty, and refuses to act when the preview names the production bucket.

Tests:

- **FR-024**: The unit tests cover the file name rule, the download header, and the environment rule of FR-022.
- **FR-025**: `preview-test.sh` creates and activates an agreement, uploads a file to it and checks its checksum, downloads it and checks its content, name, and type, is refused deleting the company and the active agreement, deletes the document, ends the agreement and sees the company become `FORMER`, and deletes the agreement. Before it starts, it removes the documents and agreements of a test company left by an earlier failed run.

### Not in this feature

- Multipart form uploads.
- Changing a document: a new upload replaces it.
- Resumable uploads, and files larger than one request.
- Checking that a parent chain has no loop longer than one.
- Renewal alerts and the task they create. Feature 015 sends them.

### What is not tested

- A 25 MiB upload in the smoke test. It was measured once for D7 and is not repeated on every run.
- A real `MEMBER` or `READER` through Cloudflare Access.
- Memory use at 25 MiB under concurrent uploads.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company, agreement, or document behind, and no object in the preview bucket.
- **SC-003**: The check `Preview and smoke test` passes, which proves the pipeline's token can deploy a Worker bound to R2.
