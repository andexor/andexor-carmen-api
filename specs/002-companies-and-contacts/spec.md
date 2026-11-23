<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Companies and contacts

**Feature Branch**: `15-companies-and-contacts`

**Created**: 2026-11-22

**Status**: Implemented.

**Input**: Issue #15. Backlog feature 002: companies, contacts, the reference codes, and duplicate detection. Identity and roles come from `specs/001-skeleton-and-pipeline/spec.md`. The tables are those of the common data model, with their names unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Record a company with almost nothing (Priority: P1)

A lead is saved with only a name or a website. Everything else can be filled in later.

**Independent Test**: The smoke test creates a company from a website, reads it back, changes it, and deletes it.

**Acceptance Scenarios**:

1. **Given** a body with only a website,\
   **When** it is posted to `/api/v1/companies`,\
   **Then** the answer is 201 with the company, its derived `domain`, stage `LEAD`, and source `MANUAL`.
2. **Given** a body with neither a name nor a website,\
   **When** it is posted,\
   **Then** the answer is 400.
3. **Given** a company,\
   **When** any writable field is patched,\
   **Then** the answer is the changed company and `updated_at` has moved.

### User Story 2 - No duplicates (Priority: P1)

Two unarchived companies never share a domain.

**Acceptance Scenarios**:

1. **Given** a company with domain `acme.com`,\
   **When** a second one is created with the website `https://www.acme.com/about`,\
   **Then** the answer is 409 `CONFLICT` and carries `company_id`, the id of the first.
2. **Given** an archived company with a domain,\
   **When** a new company is created with that domain,\
   **Then** it is created.
3. **Given** that new company,\
   **When** the archived one is unarchived, or another company's website is changed to that domain,\
   **Then** the answer is 409 with the `company_id` of the company that holds the domain.

### User Story 3 - People at a company (Priority: P2)

A company has contacts, and at most one of them is the primary contact.

**Acceptance Scenarios**:

1. **Given** a company,\
   **When** a contact is posted with only a name or only an email,\
   **Then** the answer is 201.
2. **Given** a company with a primary contact,\
   **When** another contact is created or patched with `is_primary` true,\
   **Then** the earlier one is no longer primary.
3. **Given** a company that is deleted,\
   **Then** its contacts are deleted with it.

### User Story 4 - Labels and pick lists (Priority: P2)

The UI reads every code, its label, and its order from the API.

**Acceptance Scenarios**:

1. **Given** any signed-in user,\
   **When** `GET /api/v1/srt` is requested,\
   **Then** the answer lists every active code, by domain and then by sort order.

### Edge Cases

- A domain is the website's host in lowercase, with no scheme, no `www.`, no port, and no path. A company with no website has no domain and never conflicts.
- Two requests that create the same domain at the same moment: the unique index in the database refuses the second, and it gets the same 409.
- A company that holds a code retired later can still be edited, because only a changed code is checked.
- A patch that would leave a company with neither a name nor a website, or a contact with neither a name nor an email, is refused with 400.
- A patch with no writable key changes nothing and returns the record.
- Deleting a company that has agreements or documents is refused with 409. No endpoint creates those yet, so this is proven by the query only.

## Requirements

### Endpoints and their actions

All paths are under `/api/v1`. The action is the one enforced by the role rule: a `READER` may `READ`; a `MEMBER` may also `WRITE`; only an `ADMIN` may `DELETE`.

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /srt` | `READ` | Every active reference code |
| `GET /companies` | `READ` | List, filter, search, sort, and page |
| `POST /companies` | `WRITE` | Create |
| `GET /companies/{id}` | `READ` | The company with its `contacts` |
| `PATCH /companies/{id}` | `WRITE` | Change writable fields |
| `DELETE /companies/{id}` | `DELETE` | Delete with everything under it |
| `POST /companies/{id}/archive` | `WRITE` | Hide from normal lists |
| `POST /companies/{id}/unarchive` | `WRITE` | Return to normal lists |
| `GET /companies/{id}/contacts` | `READ` | The company's contacts |
| `POST /companies/{id}/contacts` | `WRITE` | Create a contact |
| `PATCH /contacts/{id}` | `WRITE` | Change a contact |
| `DELETE /contacts/{id}` | `DELETE` | Delete a contact |

### Functional Requirements

Reference codes:

- **FR-001**: `GET /srt` returns `{"items": [...]}` with `domain`, `code`, `label`, `description`, and `sort_order` of every active row, ordered by domain, sort order, and code.

Reading companies:

- **FR-002**: `GET /companies` returns `{"items": [...], "next_cursor": ...}`. Each item is the whole `company` row, with JSON keys equal to the column names.
- **FR-003**: Filters are exact matches on `stage`, `source`, `recommendation`, and `owner_id`. `archived=true` lists only archived companies; otherwise only unarchived ones are listed.
- **FR-004**: `q` finds companies whose name or domain contains the text, or that have a contact whose name or email contains it. Case is ignored.
- **FR-005**: `sort` is `name` (A to Z, the default), `created` (newest first), `fit`, or `need` (highest first, unscored last). The direction is fixed.
- **FR-006**: `limit` is 1 to 200, default 50. `next_cursor` is null on the last page; otherwise it is passed back as `cursor` for the next page. A `limit`, `cursor`, or `sort` that is not valid gets 400. The cursor is a row offset.
- **FR-007**: `GET /companies/{id}` returns the company row plus `contacts`, primary first, then by name. The per-service Need, open deals, active agreements, and open tasks are added to this answer by the features that own those tables (004, 007, 008, 003).

Writing companies:

- **FR-008**: The writable fields are `name`, `website`, `stage`, `source`, `source_ref`, `phone`, `email`, `street`, `city`, `region`, `postal_code`, `country`, `industry`, `naics_code`, `employee_count`, `meeting_mode`, `summary`, `disqualified_reason`, and `owner_id`. Every other key in a body is ignored, including `id`, `domain`, the scores, `recommendation`, and the timestamps: the API, the scoring engine, or the database sets those.
- **FR-009**: Text fields take a string or null, and `employee_count` takes a whole number or null. A blank string is stored as NULL. A value of another type gets 400. A body that is not a JSON object gets 400.
- **FR-010**: Creating requires `name` or `website`. Nothing else is required. A key left out or null on create takes the column's default.
- **FR-011**: `domain` is derived from `website` on create and whenever `website` is patched. It cannot be set directly.
- **FR-012**: `stage`, `source`, and `meeting_mode` MUST be active codes of `COMPANY_STAGE`, `LEAD_SOURCE`, and `MEETING_MODE`. The API checks a new or changed code before it writes, and answers 400. The database triggers remain as the last line of defense.
- **FR-013**: A write that the database refuses for a duplicate domain gets 409 with `company_id` (User Story 2). A write it refuses for any other constraint, such as an `owner_id` that is not a user, gets 400.
- **FR-014**: A patch sets `updated_at`.
- **FR-015**: A patch that changes `stage` also inserts an `activity` row of kind `STAGE_CHANGE` for the company, with the old and new stage in `body` and the caller in `created_by` (NULL for a machine caller). The two writes are one transaction.
- **FR-016**: `archive` sets `archived_at` to now, and keeps the earlier time if the company is already archived. `unarchive` clears it. Both return the company.
- **FR-017**: `DELETE /companies/{id}` answers 409 when the company has an agreement or a document. Otherwise it deletes the company, and the database deletes everything under it. The answer is 204.

Contacts:

- **FR-018**: The writable fields are `name`, `title`, `email`, `phone`, `role`, `notes`, and `is_primary`. `is_primary` takes true, false, 1, or 0, and is returned as 1 or 0. FR-009 applies to the rest.
- **FR-019**: Creating a contact requires `name` or `email`. `role` MUST be an active `CONTACT_ROLE` code, checked as in FR-012.
- **FR-020**: When a contact is created or patched with `is_primary` true, the company's other primary contact is cleared in the same transaction.
- **FR-021**: `GET /companies/{id}/contacts` returns `{"items": [...]}`. `DELETE /contacts/{id}` answers 204.

Common:

- **FR-022**: An id that names nothing gets 404, on every path above.
- **FR-023**: A query parameter or body that fails validation gets 400 in the standard error shape, never the framework's own 422.
- **FR-024**: Foreign keys are enforced by D1 itself. The API issues no `PRAGMA foreign_keys`. The smoke test proves the enforcement on the preview with an `owner_id` that is not a user.

Tests:

- **FR-025**: The unit tests cover the domain rule, the body rule, the mapping of database errors to statuses, and, as before, that every route declares an action.
- **FR-026**: `preview-test.sh` lists the codes, creates a company from a website, is refused a duplicate, changes the stage, is refused an unknown stage and an unknown owner, adds a primary contact, reads the company back, finds it by its contact's name, and deletes it. It first deletes a test company left by an earlier failed run.

### Not in this feature

- Cross-Origin Resource Sharing (CORS). It is specified in `specs/017-cors/spec.md` and delivered with this feature.
- Choosing the sort direction.
- Clearing `disqualified_reason` when a company leaves `DISQUALIFIED`.
- Reading the timeline. Feature 003 adds it; until then the `STAGE_CHANGE` rows are written but not served.

### What is not tested

- A real `MEMBER` or `READER` through Cloudflare Access. In a local run, a `READER` was refused every write and delete, and a `MEMBER` was refused both deletes and allowed the rest.
- The 409 on deleting a company with agreements or documents, until feature 008 can create them.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes, and leaves no test company behind.
- **SC-003**: The check `Preview and smoke test` passes.
