<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A change of the primary contact is one plain sentence

**Feature Branch**: `100-primary-contact-entry`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #100. Finding 15 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: one entry that names both contacts, in words written for a person, in the form "The primary contact for XYZ has changed from Ann Margaret to Bob Smithers." This spec changes `specs/002-companies-contacts-and-facts/spec.md` and `specs/018-change-log/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

A company has at most one primary contact. Making a contact primary takes the mark from the contact that held it, in the same transaction. That second change was not recorded anywhere, and when a new contact was created as primary, nothing was recorded at all. The one entry that was written read "Contact Bob edited: is_primary 0 to 1", which names a column and is not written for a person. With a sales team, "who replaced Ann as the primary contact, and when?" needs an answer on the timeline.

## User Scenarios and Testing

### User Story 1 - The timeline says who replaced whom (Priority: P2)

**Independent Test**: The unit tests make the sentence for a change, a first primary contact, and a removed one. `./preview-test.sh` creates a second contact as primary, makes the first one primary again, and reads both sentences on the timeline.

**Acceptance Scenarios**:

1. **Given** the company XYZ, whose primary contact is Ann Margaret,\
   **When** Bob Smithers is edited to be the primary contact,\
   **Then** the timeline says "The primary contact for XYZ has changed from Ann Margaret to Bob Smithers."
2. **Given** the company XYZ, whose primary contact is Ann Margaret,\
   **When** Carl Jones is created as the primary contact,\
   **Then** the timeline says "The primary contact for XYZ has changed from Ann Margaret to Carl Jones."
3. **Given** the company XYZ with no primary contact,\
   **When** Bob Smithers is edited to be the primary contact,\
   **Then** the timeline says "The primary contact for XYZ is now Bob Smithers."
4. **Given** the company XYZ, whose primary contact is Ann Margaret,\
   **When** Ann Margaret is edited to be no longer primary,\
   **Then** the timeline says "Ann Margaret is no longer the primary contact for XYZ."
5. **Given** an edit that changes a contact's title and makes the contact primary,\
   **When** it is stored,\
   **Then** the timeline has the sentence for the primary contact and the entry for the title, as before, and neither names `is_primary`.

### Edge Cases

- A contact with no name is named by its email address. A company with no name is "this company".
- A contact whose name changes in the same edit is named by its new name.
- A new contact that is created as primary when the company has none gets no entry: a new record is not recorded, and no other contact changed.
- A lead and a CSV import make a contact primary only when the company has none, so they replace nobody and write no entry.
- A contact that is deleted is recorded by its delete entry, as before.
- When two people make two contacts primary at the same moment, one sentence may name a contact that the other call had just replaced. The mark itself ends on exactly one contact.

## Requirements

### Functional Requirements

- **FR-001**: `records.primary_change(company, old, new)` makes the sentence. With both contacts: "The primary contact for <company> has changed from <old> to <new>." With only a new one: "The primary contact for <company> is now <new>." With only an old one: "<old> is no longer the primary contact for <company>."
- **FR-002**: `primary_entry` in `src/app.py` writes the sentence to the Worker log first and returns the statement for a permanent `SYSTEM` entry on the company's timeline. The statement goes in the same `batch` as the change, before it.
- **FR-003**: `PATCH /contacts/{contact_id}` writes the sentence when `is_primary` changes. `is_primary` is left out of the entry for the other changed fields, and that entry is not written when nothing else changed.
- **FR-004**: `POST /companies/{company_id}/contacts` writes the sentence when the new contact is primary and takes the mark from another contact.
- **FR-005**: The company's name and the contact that held the mark are read in one query before the write.

### Endpoints

None are added, and no action changes: both endpoints are `WRITE`.

### Not in this feature

- The wording of every other entry of the change log, such as "monthly_value_cents None to 50000". It is the same kind of text and is a change of its own.
- Entries in a language other than English.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: both sentences are on the timeline, and the timeline does not name `is_primary`.
